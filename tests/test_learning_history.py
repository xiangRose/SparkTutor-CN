"""Historical task views must preserve chronology and evidence boundaries."""

from dataclasses import replace
from datetime import datetime, timezone
import json
import shutil

import pytest
import yaml

from sparktutor.config.settings import Settings
from sparktutor.courses.registry import CourseRegistry
from sparktutor.engine.diagnosis import build_diagnosis
from sparktutor.engine.learning_history import LearningHistory, resolve_window_end, score_snapshot
from sparktutor.server.handler import ServerHandler


COURSE = "test_course"
LESSON = "01_test_lesson"
TASK = f"{LESSON}:3"


@pytest.fixture
def handler(sample_course_dir, tmp_path):
    instance = ServerHandler(Settings(data_dir=tmp_path / "data"))
    instance.registry = CourseRegistry(sample_course_dir.parent)
    return instance


def record(handler, kind="code_submit", *, when="2026-01-01T00:00:00Z", course=COURSE,
           lesson=LESSON, task=TASK, task_type="cmd_question", passed=True, data=None):
    payload = ({"passed": passed, "assessmentEligible": True, "assessmentSource": "exact",
                "failureKind": "" if passed else "learner", "mode": "local_check"}
               if kind == "code_submit" else {})
    payload.update(data or {})
    return handler.events.record(kind, course_id=course, lesson_id=lesson, task_id=task,
                                 task_type=task_type, timestamp=when, data=payload)


async def call(handler, method="getLearningHistory", **params):
    return await handler.dispatch({"method": method, "params": params})


def scores(snapshot):
    return {dimension["key"]: dimension for dimension in snapshot["dimensions"]}


def add_transfer_course(handler):
    directory = handler.registry.courses_dir / "second_course"
    shutil.copytree(handler.registry.courses_dir / COURSE, directory)
    course_file = directory / "course.yaml"
    meta = yaml.safe_load(course_file.read_text(encoding="utf-8"))
    meta["course"]["id"] = "second_course"
    meta["course"]["title"] = "第二课程"
    course_file.write_text(yaml.safe_dump(meta, allow_unicode=True), encoding="utf-8")
    for course_id in (COURSE, "second_course"):
        path = handler.registry.courses_dir / course_id / "lessons" / LESSON / "lesson.yaml"
        steps = yaml.safe_load(path.read_text(encoding="utf-8"))
        steps[3]["KnowledgeComponents"] = ["spark.example"]
        steps[3]["Context"] = course_id
        if course_id == "second_course":
            steps[3]["Transfer"] = {"sources": [{"courseId": COURSE, "lessonId": LESSON, "stepId": "3"}],
                                     "context": "second_course"}
        path.write_text(yaml.safe_dump(steps, allow_unicode=True), encoding="utf-8")


@pytest.mark.asyncio
async def test_empty_history_has_catalog_and_no_invented_tasks(handler):
    result = await call(handler)
    assert result["tasks"] == []
    assert result["total"] == 0
    assert result["hasMore"] is False
    assert result["courses"][0]["title"] == "Test Course"
    assert result["courses"][0]["lessons"][0]["title"] == "Test Lesson"
    detail = await call(handler, "getTaskHistory", courseId=COURSE, lessonId=LESSON, taskId=TASK)
    assert detail["events"] == []
    assert detail["task"]["latestAssessment"] is None


@pytest.mark.asyncio
async def test_reading_finished_and_session_events_are_not_attempted_exercises(handler):
    record(handler, "task_start", task=f"{LESSON}:1", task_type="text")
    record(handler, "task_complete", task=f"{LESSON}:finished", task_type="")
    record(handler, "session_end", task="", task_type="")
    assert (await call(handler))["tasks"] == []
    record(handler, "task_start", task=f"{LESSON}:2", task_type="mult_question")
    result = await call(handler)
    assert result["total"] == 1
    assert result["tasks"][0]["latestSubmission"] is None
    assert result["tasks"][0]["counts"]["eligibleAssessments"] == 0


@pytest.mark.asyncio
async def test_tasks_have_real_titles_counts_and_separate_latest_submission_from_assessment(handler):
    failed = record(handler, passed=False)
    record(handler, "hint_request", when="2026-01-02T00:00:00Z", data={"available": True})
    record(handler, "hint_request", when="2026-01-02T01:00:00Z", data={"available": False})
    record(handler, "code_edit", when="2026-01-02T02:00:00Z", data={"changeCount": 2})
    record(handler, "code_run", when="2026-01-02T03:00:00Z", data={"exitCode": 0})
    record(handler, when="2026-01-03T00:00:00Z", data={"assessmentSource": "ai_review", "assessmentEligible": False})
    task = (await call(handler))["tasks"][0]
    assert task["title"] == "Create a variable x = 42"
    assert task["available"] is True
    assert task["counts"] == {"submissions": 2, "eligibleAssessments": 1, "hints": 1, "answers": 0, "runs": 1, "edits": 1}
    assert task["latestSubmission"]["rawPassed"] is True
    assert task["latestSubmission"]["passed"] is None
    assert task["latestSubmission"]["category"] == "excluded"
    assert task["latestAssessment"]["eventId"] == failed.event_id
    assert task["latestAssessment"]["passed"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("data,word", [
    ({"assessmentSource": "ai_review", "assessmentEligible": False}, "AI"),
    ({"mode": "dry_run"}, "语法"),
    ({"failureKind": "infrastructure", "assessmentEligible": False}, "环境"),
    ({"assessmentEligible": None, "assessmentSource": None}, "旧日志"),
])
async def test_unqualified_submit_keeps_original_behavior_without_valid_grade(handler, data, word):
    event = record(handler, passed=False, data=data)
    review = await call(handler, "getLearningReview", courseId=COURSE, lessonId=LESSON, taskId=TASK, eventId=event.event_id)
    assert review["event"]["rawPassed"] is False
    assert review["event"]["passed"] is None
    assert word in review["reason"]
    assert all(dimension["score"] is None for dimension in review["after"]["dimensions"])


@pytest.mark.asyncio
async def test_syntax_failure_is_debug_evidence_only_and_run_success_does_not_repair(handler):
    failure = record(handler, passed=False, data={"assessmentSource": "syntax", "mode": "dry_run"})
    first = await call(handler, "getLearningReview", courseId=COURSE, lessonId=LESSON, taskId=TASK, eventId=failure.event_id)
    assert first["event"]["category"] == "debug_failure"
    assert scores(first["after"])["debugging"]["score"] == 0
    assert scores(first["after"])["knowledge"]["score"] is None
    success = record(handler, "code_run", when="2026-01-02T00:00:00Z", data={"exitCode": 0, "mode": "local"})
    second = await call(handler, "getLearningReview", courseId=COURSE, lessonId=LESSON, taskId=TASK, eventId=success.event_id)
    assert second["before"] == second["after"]
    assert "不等于" in second["reason"]


@pytest.mark.asyncio
async def test_answer_supported_success_does_not_replace_prior_knowledge_failure(handler):
    failed = record(handler, passed=False)
    answer = record(handler, "solution_view", when="2026-01-02T00:00:00Z", data={"available": True})
    passed = record(handler, when="2026-01-03T00:00:00Z")
    task = (await call(handler))["tasks"][0]
    assert task["answerViewed"] is True
    assert task["latestSubmission"]["passed"] is True
    assert task["latestSubmission"]["answerInfluenced"] is True
    assert task["latestAssessment"]["eventId"] == failed.event_id
    review = await call(handler, "getLearningReview", courseId=COURSE, lessonId=LESSON, taskId=TASK, eventId=passed.event_id)
    assert scores(review["after"])["knowledge"]["score"] == 0
    assert scores(review["after"])["debugging"]["score"] is None
    assert "提示覆盖" in review["reason"]
    answer_review = await call(handler, "getLearningReview", courseId=COURSE, lessonId=LESSON, taskId=TASK, eventId=answer.event_id)
    assert scores(answer_review["before"])["debugging"]["denominator"] == 1
    assert scores(answer_review["after"])["debugging"]["denominator"] == 0


@pytest.mark.asyncio
async def test_review_uses_complete_prefix_not_future_hint_solution_or_pass(handler):
    failed = record(handler, passed=False)
    record(handler, "hint_request", when="2026-01-02T00:00:00Z", data={"available": True})
    record(handler, when="2026-01-03T00:00:00Z")
    record(handler, "solution_view", when="2026-01-04T00:00:00Z", data={"available": True})
    review = await call(handler, "getLearningReview", courseId=COURSE, lessonId=LESSON, taskId=TASK, eventId=failed.event_id)
    assert scores(review["before"])["knowledge"]["score"] is None
    assert scores(review["after"])["knowledge"]["score"] == 0
    assert scores(review["after"])["hint_dependency"]["score"] == 0
    assert scores(review["after"])["knowledge"]["metrics"]["assistedTasks"] == 0
    assert review["task"]["answerViewed"] is False
    assert review["task"]["latestSubmission"]["eventId"] == failed.event_id
    assert review["changes"][0]["delta"] is None
    assert "新增" in review["changes"][0]["explanation"]


@pytest.mark.asyncio
async def test_utc_sort_and_equal_instant_insertion_order_are_preserved(handler):
    submitted = record(handler, when="2026-01-01T08:00:00+08:00")
    answer = record(handler, "solution_view", when="2026-01-01T00:00:00Z", data={"available": True})
    # TEXT ordering would place Z before +08:00; insertion fixes equal instants.
    detail = await call(handler, "getTaskHistory", courseId=COURSE, lessonId=LESSON, taskId=TASK)
    assert [event["eventId"] for event in detail["events"]] == [submitted.event_id, answer.event_id]
    before_answer = await call(handler, "getLearningReview", courseId=COURSE, lessonId=LESSON, taskId=TASK, eventId=submitted.event_id)
    assert scores(before_answer["after"])["knowledge"]["score"] == 100
    after_answer = await call(handler, "getLearningReview", courseId=COURSE, lessonId=LESSON, taskId=TASK, eventId=answer.event_id)
    assert scores(after_answer["after"])["knowledge"]["score"] is None
    assert after_answer["changes"][0]["delta"] is None


def test_window_uses_utc_boundaries_but_retains_full_task_assessment_history(handler):
    old = record(handler, when="2026-09-01T00:00:00Z")
    record(handler, "code_edit", when="2026-10-02T20:00:00+08:00", data={"changeCount": 1})
    record(handler, "code_edit", when="2026-10-10T00:00:00Z", data={"changeCount": 1})
    now = datetime(2026, 10, 9, 12, tzinfo=timezone.utc)
    result = LearningHistory(handler.registry, handler.events).get_history(window="7d", now=now)
    assert result["windowStart"] == "2026-10-02T12:00:00+00:00"
    assert result["total"] == 1
    task = result["tasks"][0]
    assert task["counts"]["edits"] == 1
    assert task["counts"]["submissions"] == 0
    assert task["lastActivityAt"] == "2026-10-02T12:00:00+00:00"
    assert task["latestAssessment"]["eventId"] == old.event_id
    assert task["latestAssessment"]["passed"] is True


@pytest.mark.asyncio
async def test_snapshot_freezes_pages_details_and_review_even_for_backdated_new_events(handler):
    one = record(handler, task=f"{LESSON}:2", task_type="mult_question", when="2026-01-01T00:00:00Z")
    two = record(handler, when="2026-01-02T00:00:00Z")
    first_page = await call(handler, limit=1)
    snapshot = first_page["snapshotEventId"]
    assert snapshot == two.event_id
    assert first_page["total"] == 2
    assert first_page["tasks"][0]["taskId"] == TASK
    baseline = await call(handler, "getLearningReview", courseId=COURSE, lessonId=LESSON, taskId=TASK,
                          eventId=two.event_id, snapshotEventId=snapshot)
    record(handler, "solution_view", when="2025-12-31T00:00:00Z", data={"available": True})
    record(handler, task=f"{LESSON}:4", when="2026-01-03T00:00:00Z")
    second_page = await call(handler, offset=1, limit=1, snapshotEventId=snapshot)
    assert second_page["tasks"][0]["taskId"] == one.task_id
    assert second_page["total"] == 2
    assert second_page["hasMore"] is False
    detail = await call(handler, "getTaskHistory", courseId=COURSE, lessonId=LESSON, taskId=TASK, snapshotEventId=snapshot)
    assert detail["total"] == 1
    again = await call(handler, "getLearningReview", courseId=COURSE, lessonId=LESSON, taskId=TASK,
                       eventId=two.event_id, snapshotEventId=snapshot)
    assert again == baseline
    assert (await call(handler))["total"] == 3


@pytest.mark.asyncio
async def test_detail_pagination_does_not_limit_full_history_scores(handler):
    failed = record(handler, passed=False)
    for day in range(2, 7):
        record(handler, "code_edit", when=f"2026-01-0{day}T00:00:00Z")
    passed = record(handler, when="2026-01-07T00:00:00Z")
    detail = await call(handler, "getTaskHistory", courseId=COURSE, lessonId=LESSON, taskId=TASK, offset=5, limit=2)
    assert detail["total"] == 7
    assert len(detail["events"]) == 2
    review = await call(handler, "getLearningReview", courseId=COURSE, lessonId=LESSON, taskId=TASK, eventId=passed.event_id)
    assert scores(review["before"])["knowledge"]["score"] == 0
    assert scores(review["after"])["knowledge"]["score"] == 100
    assert scores(review["after"])["debugging"]["numerator"] == 1
    assert review["changes"][0]["delta"] == 100
    assert failed.event_id not in {event["eventId"] for event in detail["events"]}


@pytest.mark.asyncio
async def test_cross_course_transfer_and_all_course_review_keep_sources(handler):
    add_transfer_course(handler)
    record(handler, course=COURSE, when="2026-01-01T00:00:00Z")
    target = record(handler, course="second_course", passed=False, when="2026-01-02T00:00:00Z")
    retry = record(handler, course="second_course", when="2026-01-03T00:00:00Z")
    report = await call(handler, "getLearningReview", courseId="second_course", lessonId=LESSON, taskId=TASK, eventId=target.event_id)
    assert scores(report["after"])["transfer"]["score"] == 0
    assert scores(report["after"])["transfer"]["denominator"] == 1
    repeated = await call(handler, "getLearningReview", courseId="second_course", lessonId=LESSON, taskId=TASK, eventId=retry.event_id)
    assert scores(repeated["after"])["transfer"]["score"] == 0
    all_courses = await call(handler, "getLearningReview", courseId="second_course", lessonId=LESSON, taskId=TASK,
                             eventId=target.event_id, scopeCourseId="")
    assert scores(all_courses["after"])["knowledge"]["denominator"] == 2
    assert scores(report["after"])["knowledge"]["denominator"] == 1


@pytest.mark.asyncio
async def test_same_time_source_pass_cannot_create_transfer_evidence(handler):
    add_transfer_course(handler)
    record(handler)
    target = record(handler, course="second_course")
    review = await call(handler, "getLearningReview", courseId="second_course", lessonId=LESSON, taskId=TASK, eventId=target.event_id)
    assert scores(review["after"])["transfer"]["score"] is None


@pytest.mark.asyncio
async def test_deleted_course_and_task_history_remain_filterable_but_unavailable(handler):
    event = record(handler, course="removed", lesson="old", task="old:task")
    record(handler, task="deleted:task")
    result = await call(handler, courseId="removed")
    assert result["total"] == 1
    assert result["tasks"][0]["available"] is False
    assert "已不在课程目录" in result["tasks"][0]["title"]
    assert any(course["id"] == "removed" and not course["available"] for course in result["courses"])
    detail = await call(handler, "getTaskHistory", courseId="removed", lessonId="old", taskId="old:task")
    assert detail["total"] == 1
    review = await call(handler, "getLearningReview", courseId="removed", lessonId="old", taskId="old:task", eventId=event.event_id)
    assert scores(review["after"])["knowledge"]["denominator"] == 1


@pytest.mark.asyncio
async def test_legacy_submission_without_type_remains_visible_but_not_assessed(handler):
    record(handler, task="unknown:task", task_type="", data={"assessmentEligible": None})
    result = await call(handler)
    assert result["total"] == 1
    assert result["tasks"][0]["taskType"] == ""
    assert result["tasks"][0]["latestAssessment"] is None
    assert result["tasks"][0]["latestSubmission"]["passed"] is None


@pytest.mark.asyncio
async def test_normalization_reports_duplicates_conflicts_and_invalid_timestamps(handler, monkeypatch):
    first = record(handler)
    conflict = record(handler, task=f"{LESSON}:4")
    bad = replace(first, event_id="invalid", timestamp="invalid")
    raw = [first, first, conflict, replace(conflict, data={"passed": False}), bad]
    monkeypatch.setattr(handler.events, "list_events", lambda **kwargs: raw)
    result = await call(handler)
    assert result["dataQuality"] == {"duplicateEvents": 2, "conflictingEventIds": 1, "invalidTimestamps": 1}
    assert result["total"] == 1
    assert result["tasks"][0]["counts"]["submissions"] == 1


@pytest.mark.asyncio
async def test_history_is_read_only_and_data_fields_are_whitelisted(handler):
    event = record(handler, passed=False, data={
        "code": "SECRET_SOURCE", "stdout": "PRIVATE_OUTPUT", "stderr": "PRIVATE_ERROR",
        "chat": "PRIVATE_CHAT", "path": "PRIVATE_PATH", "errorTypes": ["NameError", "PRIVATE_ERROR content"],
    })
    before = handler.events.list_events()
    for method, params in (
        ("getLearningHistory", {}),
        ("getTaskHistory", {"courseId": COURSE, "lessonId": LESSON, "taskId": TASK}),
        ("getLearningReview", {"courseId": COURSE, "lessonId": LESSON, "taskId": TASK, "eventId": event.event_id}),
    ):
        serialized = json.dumps(await call(handler, method, **params))
        for secret in ("SECRET_SOURCE", "PRIVATE_OUTPUT", "PRIVATE_ERROR", "PRIVATE_CHAT", "PRIVATE_PATH"):
            assert secret not in serialized
    assert handler.events.list_events() == before
    assert handler.progress.get_course_progress(COURSE) == []
    assert handler._runner is None


@pytest.mark.asyncio
@pytest.mark.parametrize("params", [
    {"offset": -1}, {"offset": True}, {"offset": 1.5}, {"limit": 0}, {"limit": 51},
    {"limit": True}, {"window": "yesterday"}, {"window": []}, {"courseId": None},
    {"courseId": "never_seen"}, {"snapshotEventId": "fake"}, {"snapshotEventId": True},
])
async def test_invalid_history_queries_are_rejected(handler, params):
    with pytest.raises(ValueError):
        await call(handler, **params)


@pytest.mark.asyncio
async def test_task_and_review_identifiers_must_match_real_history(handler):
    event = record(handler)
    with pytest.raises(ValueError):
        await call(handler, "getTaskHistory", courseId=COURSE, lessonId=LESSON, taskId="fake")
    with pytest.raises(ValueError):
        await call(handler, "getTaskHistory", courseId=COURSE, lessonId=LESSON, taskId=TASK, limit=101)
    with pytest.raises(ValueError):
        await call(handler, "getLearningReview", courseId=COURSE, lessonId=LESSON, taskId=f"{LESSON}:2", eventId=event.event_id)
    with pytest.raises(ValueError):
        await call(handler, "getLearningReview", courseId=COURSE, lessonId=LESSON, taskId=TASK, eventId=event.event_id,
                   scopeCourseId="fake")


def test_empty_snapshot_remains_empty_after_new_insertions(handler):
    history = LearningHistory(handler.registry, handler.events, snapshot_event_id="")
    assert history.events == []
    record(handler)
    again = LearningHistory(handler.registry, handler.events, snapshot_event_id=history.snapshot_event_id)
    assert again.events == []


def test_compact_snapshot_matches_actual_diagnosis_counts(handler):
    record(handler, passed=False)
    record(handler, "hint_request", when="2026-01-02T00:00:00Z", data={"available": True})
    record(handler, when="2026-01-03T00:00:00Z")
    history = LearningHistory(handler.registry, handler.events)
    compact = score_snapshot(history.events, history.catalog, course_id=COURSE)
    full = build_diagnosis(history.events, history.catalog, course_id=COURSE)
    for left, right in zip(compact["dimensions"], full["dimensions"]):
        assert left["score"] == right["score"]
        assert left["sampleSize"] == left["denominator"] == right["sampleSize"]


def test_frozen_window_boundary_keeps_offset_pagination_stable(handler):
    record(handler, task=f"{LESSON}:2", task_type="mult_question", when="2026-01-01T00:00:01Z")
    record(handler, when="2026-01-07T00:00:00Z")
    snapshot = LearningHistory(handler.registry, handler.events)
    first = snapshot.get_history(window="7d", limit=1, now=datetime(2026, 1, 8, tzinfo=timezone.utc))
    assert first["total"] == 2
    second = snapshot.get_history(window="7d", limit=1, offset=1, window_end=first["windowEnd"],
                                  now=datetime(2026, 1, 8, 0, 0, 2, tzinfo=timezone.utc))
    assert second["total"] == 2
    assert second["tasks"][0]["taskId"] == f"{LESSON}:2"
    assert second["windowEnd"] == first["windowEnd"]


@pytest.mark.parametrize("value", ["", "invalid", "2026-01-01", "2026-01-01T00:00:00", True, 42, []])
def test_window_end_rejects_ambiguous_or_invalid_timestamps(value):
    with pytest.raises(ValueError):
        resolve_window_end(value)


def test_window_end_normalizes_offsets_to_utc():
    assert resolve_window_end("2026-01-01T08:00:00+08:00").isoformat() == "2026-01-01T00:00:00+00:00"
