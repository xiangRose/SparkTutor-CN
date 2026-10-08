"""The learning workspace reads real saved progress over the JSON-lines protocol."""

from __future__ import annotations

import json
import subprocess
import sys

from sparktutor.courses.registry import CourseRegistry
from sparktutor.engine.lesson_loader import load_lesson
from sparktutor.state.progress import ProgressStore


def test_dashboard_protocol_reads_nonsequential_progress_and_resumes_saved_work(tmp_path):
    registry = CourseRegistry()
    course = registry.get_course("learning_spark")
    lesson_id = course.lessons[1]
    lesson = load_lesson(registry.courses_dir / course.id / "lessons" / lesson_id)
    steps = [step for step in lesson.steps_for_depth("intermediate") if step.cls != "meta"]
    store = ProgressStore(tmp_path / "progress.db")
    store.save(course.id, course.lessons[3], 999, 999, completed=True)
    store.save(course.id, "removed_lesson", 0, 1, completed=True)
    store.save(course.id, lesson_id, 2, len(steps), depth="intermediate",
               current_step_id=steps[2].id, last_code="# learner code must survive\nx = 42")
    original = store.get_course_progress(course.id)
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
        {"id": 2, "method": "getLearningDashboard", "params": {}},
        {"id": 3, "method": "getLearningDashboard", "params": {"courseId": course.id}},
        {"id": 4, "method": "getCourseProgress", "params": {"courseId": course.id}},
        {"id": 5, "method": "getLearningDashboard", "params": {"courseId": "missing"}},
        {"id": 6, "method": "getLearningEvents", "params": {}},
        {"id": 7, "method": "loadLesson", "params": {
            "courseId": course.id, "lessonIdx": 1, "depth": "intermediate",
        }},
    ]
    completed = subprocess.run(
        [sys.executable, "-X", "utf8", "-c", script, str(tmp_path)],
        input="".join(json.dumps(request) + "\n" for request in requests),
        capture_output=True, encoding="utf-8", timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    responses = {item["id"]: item for item in map(json.loads, completed.stdout.splitlines()) if "id" in item}
    dashboard = responses[2]["result"]
    scoped = responses[3]["result"]
    assert len(dashboard["courses"]) == len(registry.list_courses())
    assert scoped["courses"] == dashboard["courses"], "Filtering must retain the full course catalogue"
    assert scoped["selectedCourseId"] == course.id
    assert all(dimension["score"] is None for dimension in dashboard["diagnosis"]["dimensions"])
    assert dashboard["resume"] == scoped["resume"]
    assert dashboard["resume"]["lessonIdx"] == 1
    assert dashboard["resume"]["depth"] == "intermediate"
    assert dashboard["resume"]["stepId"] == steps[2].id
    progress = responses[4]["result"]
    assert progress["lessonsCompleted"] == 1, "Deleted lessons must not inflate completion"
    assert progress["totalLessons"] == len(course.lessons)
    assert [item["status"] for item in progress["lessons"][:4]] == [
        "not_started", "in_progress", "not_started", "completed",
    ]
    assert responses[5].get("error"), "An invalid course must not silently select all history"
    assert responses[1]["result"] == responses[6]["result"], "Dashboard refresh must not record learning events"
    serialized = json.dumps(dashboard)
    assert "learner code must survive" not in serialized
    assert "last_code" not in serialized
    assert "updated_at" not in serialized
    loaded = responses[7]["result"]
    assert loaded["lessonId"] == lesson_id
    assert loaded["currentIndex"] == 2
    assert loaded["step"]["id"] == steps[2].id
    assert loaded["restoredCode"] == "# learner code must survive\nx = 42"
    assert store.get_course_progress(course.id) == original, "Read and resume must preserve the saved work"
