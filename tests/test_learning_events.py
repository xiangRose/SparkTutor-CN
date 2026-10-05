"""Tests for Issue 4 learning event persistence and runtime tracking."""

from __future__ import annotations

from pathlib import Path

import pytest

from sparktutor.config.settings import Settings
from sparktutor.courses.registry import CourseRegistry
from sparktutor.server.handler import ServerHandler
from sparktutor.state.learning_events import LearningEventStore
from sparktutor.state.progress import ProgressStore


@pytest.fixture
def handler(sample_course_dir, tmp_path):
    settings = Settings(data_dir=tmp_path / "data")
    instance = ServerHandler(settings=settings)
    instance.registry = CourseRegistry(courses_dir=sample_course_dir.parent)
    instance.progress = ProgressStore(db_path=tmp_path / "data" / "progress.db")
    return instance


def test_event_store_round_trip(tmp_path: Path):
    store = LearningEventStore(tmp_path / "events.db")
    event = store.record(
        "code_submit",
        course_id="course",
        lesson_id="lesson",
        task_id="lesson:1",
        attempt_number=2,
        data={"passed": False, "hintUsed": True, "durationMs": 1200},
    )

    events = store.list_events(course_id="course", lesson_id="lesson")

    assert len(events) == 1
    assert events[0].event_id == event.event_id
    assert events[0].attempt_number == 2
    assert events[0].data["durationMs"] == 1200


@pytest.mark.asyncio
async def test_server_records_runtime_events(handler):
    handler.executor._force_dry_run = True

    await handler.dispatch({
        "method": "loadLesson",
        "params": {"courseId": "test_course", "lessonIdx": 0},
    })
    await handler.dispatch({"method": "advance", "params": {}})
    await handler.dispatch({
        "method": "run",
        "params": {"code": "def broken("},
    })
    await handler.dispatch({"method": "getHint", "params": {}})
    await handler.dispatch({
        "method": "submit",
        "params": {"code": "anything"},
    })
    await handler.dispatch({
        "method": "getLearningEvents",
        "params": {},
    })

    result = await handler.dispatch({
        "method": "getLearningEvents",
        "params": {},
    })
    event_types = [event["eventType"] for event in result["events"]]

    assert "session_start" in event_types
    assert "lesson_loaded" in event_types
    assert "task_start" in event_types
    assert "code_run" in event_types
    assert "error" in event_types
    assert "hint_request" in event_types
    assert "code_submit" in event_types

    run_event = next(event for event in result["events"] if event["eventType"] == "code_run")
    assert run_event["data"]["exitCode"] == 1
    assert "durationMs" in run_event["data"]
    submit_event = next(event for event in result["events"] if event["eventType"] == "code_submit")
    assert submit_event["data"]["hintUsed"] is True
    assert submit_event["attemptNumber"] == 1


@pytest.mark.asyncio
async def test_reading_is_not_recorded_as_a_passed_assessment(handler):
    await handler.dispatch({"method": "loadLesson", "params": {"courseId": "test_course", "lessonIdx": 0}})
    await handler.dispatch({"method": "advance", "params": {}})
    with pytest.raises(ValueError):
        await handler.dispatch({"method": "advance", "params": {}})
    events = handler.events.list_events()
    completed = [e for e in events if e.event_type == "task_complete"]
    assert len(completed) == 1
    assert completed[0].data["passed"] is None
    await handler.dispatch({"method": "submit", "params": {"code": "4"}})
    await handler.dispatch({"method": "advance", "params": {}})
    completed = [e for e in handler.events.list_events() if e.event_type == "task_complete"]
    assert any(e.task_type == "mult_question" and e.data["passed"] is True for e in completed)
