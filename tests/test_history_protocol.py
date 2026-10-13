"""History, frozen pagination and past-state review through the real server."""

import json
import subprocess
import sys

from sparktutor.courses.registry import CourseRegistry
from sparktutor.engine.lesson_loader import load_lesson
from sparktutor.state.learning_events import LearningEventStore
from sparktutor.state.progress import ProgressStore


def test_history_protocol_paginates_reviews_past_state_and_never_exposes_source(tmp_path):
    registry = CourseRegistry()
    course = registry.get_course("learning_spark")
    lesson_id = course.lessons[0]
    lesson = load_lesson(registry.courses_dir / course.id / "lessons" / lesson_id)
    step = next(step for step in lesson.steps if step.cls == "mult_question")
    target = {"courseId": course.id, "lessonId": lesson_id, "taskId": f"{lesson_id}:{step.id}"}
    store = LearningEventStore(tmp_path / "progress.db")
    progress = ProgressStore(tmp_path / "progress.db")
    progress.save(course.id, lesson_id, 0, 1, last_code="PRIVATE SAVED CODE")
    original_progress = progress.get_course_progress(course.id)

    def record(kind, second, **data):
        return store.record(kind, course_id=course.id, lesson_id=lesson_id, task_id=target["taskId"],
                            task_type=step.cls, timestamp=f"2026-01-01T00:00:0{second}Z", data=data)

    valid = {"assessmentEligible": True, "assessmentSource": "choice", "mode": "local_check"}
    record("code_submit", 0, **valid, passed=False, failureKind="learner",
           code="PRIVATE STUDENT CODE", stderr="PRIVATE ERROR BODY", question="PRIVATE CHAT TEXT")
    record("hint_request", 1, available=True)
    passed = record("code_submit", 2, **valid, passed=True)
    record("code_submit", 3, **valid, passed=False, failureKind="learner")
    edit = record("code_edit", 4, changeCount=2, addedChars=7, removedChars=3)
    legacy = store.record("code_submit", course_id="removed_course", lesson_id="removed_lesson",
                          task_id="removed_lesson:old", timestamp="2025-01-01T00:00:00Z",
                          data={"passed": True, "code": "PRIVATE LEGACY CODE"})
    # A later imported row must not enter an earlier insertion-bounded page,
    # even though its recorded time belongs before the selected assessment.
    record("code_submit", 0, **valid, passed=False, failureKind="learner")
    frozen = {"snapshotEventId": legacy.event_id}
    script = """
import asyncio
import sys
from pathlib import Path
from sparktutor.config.settings import Settings
Settings.load = classmethod(lambda cls: Settings(data_dir=Path(sys.argv[1])))
from sparktutor.server.__main__ import main
asyncio.run(main())
"""
    requests = [
        {"id": 1, "method": "getLearningEvents", "params": {}},
        {"id": 2, "method": "getLearningHistory", "params": {**frozen, "limit": 1}},
        {"id": 3, "method": "getLearningHistory", "params": {**frozen, "offset": 1, "limit": 1}},
        {"id": 4, "method": "getTaskHistory", "params": {**target, **frozen, "limit": 2}},
        {"id": 5, "method": "getTaskHistory", "params": {**target, **frozen, "offset": 2, "limit": 2}},
        {"id": 6, "method": "getLearningReview", "params": {
            **target, **frozen, "eventId": passed.event_id, "scopeCourseId": "",
        }},
        {"id": 7, "method": "getLearningReview", "params": {**target, **frozen, "eventId": legacy.event_id}},
        {"id": 8, "method": "getLearningTrend", "params": {**frozen, "courseId": course.id}},
        {"id": 9, "method": "getTaskHistory", "params": {
            **frozen, "courseId": "removed_course", "lessonId": "removed_lesson", "taskId": "removed_lesson:old",
        }},
        {"id": 10, "method": "getLearningHistory", "params": {"courseId": course.id}},
        {"id": 11, "method": "getLearningEvents", "params": {}},
        {"id": 12, "method": "getLearningReview", "params": {**target, **frozen, "eventId": edit.event_id}},
        {"id": 13, "method": "getLearningTrend", "params": {"window": "invalid"}},
    ]
    completed = subprocess.run(
        [sys.executable, "-X", "utf8", "-c", script, str(tmp_path)],
        input="".join(json.dumps(request) + "\n" for request in requests),
        capture_output=True, encoding="utf-8", timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    responses = {item["id"]: item for item in map(json.loads, completed.stdout.splitlines()) if "id" in item}
    first, second = responses[2]["result"], responses[3]["result"]
    assert first["total"] == second["total"] == 2
    assert first["hasMore"] is True and second["hasMore"] is False
    assert first["snapshotEventId"] == second["snapshotEventId"] == legacy.event_id
    assert first["tasks"][0]["taskId"] == target["taskId"]
    assert first["tasks"][0]["counts"]["submissions"] == 3
    assert second["tasks"][0]["courseId"] == "removed_course"
    assert second["tasks"][0]["latestAssessment"] is None
    first_events, next_events = responses[4]["result"], responses[5]["result"]
    assert first_events["total"] == next_events["total"] == 5
    assert not ({event["eventId"] for event in first_events["events"]}
                & {event["eventId"] for event in next_events["events"]})
    assert next_events["events"][0]["eventId"] == passed.event_id
    review = responses[6]["result"]
    before = {dimension["key"]: dimension for dimension in review["before"]["dimensions"]}
    after = {dimension["key"]: dimension for dimension in review["after"]["dimensions"]}
    assert before["knowledge"]["score"] == 0
    assert after["knowledge"]["score"] == 100, "A later failure must not leak into this past review"
    assert before["knowledge"]["denominator"] == after["knowledge"]["denominator"] == 1
    assert before["hint_dependency"]["score"] == after["hint_dependency"]["score"] == 0
    assert responses[7].get("error"), "The server must verify event-to-task ownership"
    trend = responses[8]["result"]
    assert trend["total"] == len(trend["points"]) == 3
    assert trend["snapshotEventId"] == legacy.event_id
    assert responses[9]["result"]["events"][0]["category"] == "excluded"
    assert responses[10]["result"]["tasks"][0]["counts"]["submissions"] == 4
    assert responses[1]["result"] == responses[11]["result"], "Viewing history must not record learning actions"
    assert responses[12]["result"]["before"] == responses[12]["result"]["after"]
    assert responses[13].get("error")
    serialized = json.dumps({key: response for key, response in responses.items()
                             if key not in (1, 11)}, ensure_ascii=False)
    for marker in ("PRIVATE SAVED CODE", "PRIVATE STUDENT CODE", "PRIVATE ERROR BODY", "PRIVATE CHAT TEXT", "PRIVATE LEGACY CODE"):
        assert marker not in serialized
    assert progress.get_course_progress(course.id) == original_progress
