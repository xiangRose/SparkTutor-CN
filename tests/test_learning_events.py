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


async def _call(handler, method, **params):
    return await handler.dispatch({"method": method, "params": params})


async def _open_code_task(handler):
    await _call(handler, "loadLesson", courseId="test_course", lessonIdx=0)
    await _call(handler, "advance")
    await _call(handler, "submit", code="4")
    await _call(handler, "advance")


def _edit_payload(handler):
    return {
        "eventType": "code_edit", "courseId": "test_course",
        "lessonId": "01_test_lesson", "taskId": handler._task_id(),
        "data": {"changeCount": 2, "addedChars": 8, "removedChars": 1,
                 "documentVersion": 3, "burstDurationMs": 500},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("task_type", ["cmd_question", "script"])
async def test_edit_is_bound_to_current_code_task_and_does_not_change_scores(handler, task_type):
    await _open_code_task(handler)
    handler._runner.current_step().cls = task_type
    before = await _call(handler, "getDiagnosis")
    response = await _call(handler, "recordLearningEvent", **_edit_payload(handler))
    event = response["event"]
    assert response["recorded"] is True
    assert event["courseId"] == "test_course"
    assert event["lessonId"] == "01_test_lesson"
    assert event["taskId"] == "01_test_lesson:3"
    assert event["taskType"] == task_type
    assert event["sessionId"] == handler._session_id
    assert event["attemptNumber"] == 0
    assert event["data"]["addedChars"] == 8
    assert event["data"]["stepId"] == "3"
    assert "assessmentEligible" not in event["data"]
    after = await _call(handler, "getDiagnosis")
    assert after["dimensions"] == before["dimensions"]
    assert after["eligibleEventCount"] == before["eligibleEventCount"]
    assert after["eventCount"] == before["eventCount"] + 1


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["courseId", "lessonId", "taskId"])
async def test_wrong_edit_context_is_rejected_without_recording(handler, field):
    await _open_code_task(handler)
    payload = _edit_payload(handler)
    payload[field] = "another_task"
    count = handler.events.count_events()
    assert await _call(handler, "recordLearningEvent", **payload) == {
        "recorded": False, "reason": "stale_task",
    }
    assert handler.events.count_events() == count


@pytest.mark.asyncio
async def test_edit_that_arrives_after_navigation_is_not_assigned_to_new_task(handler):
    await _open_code_task(handler)
    old = _edit_payload(handler)
    await _call(handler, "submit", code="x = 42")
    await _call(handler, "advance")
    assert handler._task_id() != old["taskId"]
    assert await _call(handler, "recordLearningEvent", **old) == {
        "recorded": False, "reason": "stale_task",
    }
    assert not any(e.event_type == "code_edit" for e in handler.events.list_events())


@pytest.mark.asyncio
@pytest.mark.parametrize("position", ["unloaded", "reading", "choice"])
async def test_edits_require_an_active_code_question(handler, position):
    if position != "unloaded":
        await _call(handler, "loadLesson", courseId="test_course", lessonIdx=0)
    if position == "choice":
        await _call(handler, "advance")
    payload = _edit_payload(handler)
    payload["taskId"] = payload["taskId"] or "01_test_lesson:3"
    count = handler.events.count_events()
    assert await _call(handler, "recordLearningEvent", **payload) == {
        "recorded": False, "reason": "no_code_task",
    }
    assert handler.events.count_events() == count


@pytest.mark.asyncio
@pytest.mark.parametrize("event_type", [
    "code_submit", "code_run", "hint_request", "solution_view", "task_complete",
    "task_start", "session_start", "error", "chat_request", "made_up", None, [],
])
async def test_client_cannot_forge_trusted_learning_events(handler, event_type):
    await _open_code_task(handler)
    count = handler.events.count_events()
    with pytest.raises(ValueError):
        await _call(handler, "recordLearningEvent", eventType=event_type, data={
            "passed": True, "assessmentEligible": True, "assessmentSource": "course_tests",
        })
    assert handler.events.count_events() == count


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_data", [
    {}, None, [],
    {"changeCount": 1, "addedChars": 1},
    {"changeCount": True, "addedChars": 1, "removedChars": 0},
    {"changeCount": 1, "addedChars": -1, "removedChars": 0},
    {"changeCount": 1, "addedChars": 1.5, "removedChars": 0},
    {"changeCount": 1, "addedChars": "12", "removedChars": 0},
    {"changeCount": 100001, "addedChars": 1, "removedChars": 0},
    {"changeCount": 1, "addedChars": 10000001, "removedChars": 0},
    {"changeCount": 1, "addedChars": 1, "removedChars": 10000001},
    {"changeCount": 1, "addedChars": 1, "removedChars": 0, "documentVersion": 2147483648},
    {"changeCount": 1, "addedChars": 1, "removedChars": 0, "burstDurationMs": 86400001},
    {"changeCount": 1, "addedChars": 1, "removedChars": 0, "code": "private source"},
    {"changeCount": 1, "addedChars": 1, "removedChars": 0, "path": "private/path"},
    {"changeCount": 1, "addedChars": 1, "removedChars": 0, "assessmentEligible": True},
])
async def test_edit_data_is_bounded_numeric_metadata_only(handler, bad_data):
    await _open_code_task(handler)
    payload = _edit_payload(handler)
    payload["data"] = bad_data
    count = handler.events.count_events()
    with pytest.raises(ValueError):
        await _call(handler, "recordLearningEvent", **payload)
    assert handler.events.count_events() == count


@pytest.mark.asyncio
@pytest.mark.parametrize("extra", ["sessionId", "timestamp", "eventId", "code", "path"])
async def test_client_cannot_replace_server_identity_or_attach_source(handler, extra):
    await _open_code_task(handler)
    payload = _edit_payload(handler)
    payload[extra] = "untrusted"
    count = handler.events.count_events()
    with pytest.raises(ValueError):
        await _call(handler, "recordLearningEvent", **payload)
    assert handler.events.count_events() == count


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["courseId", "lessonId", "taskId"])
async def test_edit_context_must_be_explicit(handler, field):
    await _open_code_task(handler)
    payload = _edit_payload(handler)
    del payload[field]
    with pytest.raises(ValueError):
        await _call(handler, "recordLearningEvent", **payload)


@pytest.mark.asyncio
async def test_zero_counts_and_optional_metadata_are_accepted(handler):
    await _open_code_task(handler)
    payload = _edit_payload(handler)
    payload["data"] = {"changeCount": 0, "addedChars": 0, "removedChars": 0}
    result = await _call(handler, "recordLearningEvent", **payload)
    assert result["recorded"] is True
    assert "documentVersion" not in result["event"]["data"]


@pytest.mark.asyncio
async def test_session_end_is_server_owned_idempotent_and_blocks_late_edits(handler):
    await _open_code_task(handler)
    before = await _call(handler, "getDiagnosis")
    first = await _call(handler, "recordLearningEvent", eventType="session_end",
                        data={"reason": "extension_deactivated"})
    second = await _call(handler, "recordLearningEvent", eventType="session_end",
                         data={"reason": "extension_deactivated"})
    eof = handler.end_session("server_shutdown")
    assert first["recorded"] is True
    assert second["recorded"] is False
    assert second["reason"] == "already_ended"
    assert first["event"] == second["event"] == eof["event"]
    ends = [e for e in handler.events.list_events() if e.event_type == "session_end"]
    assert len(ends) == 1
    assert ends[0].session_id == handler._session_id
    assert ends[0].task_id == ends[0].lesson_id == ""
    assert ends[0].data == {"reason": "extension_deactivated"}
    assert await _call(handler, "recordLearningEvent", **_edit_payload(handler)) == {
        "recorded": False, "reason": "session_ended",
    }
    after = await _call(handler, "getDiagnosis")
    assert after["dimensions"] == before["dimensions"]
    assert after["eligibleEventCount"] == before["eligibleEventCount"]


def test_each_server_session_has_its_own_orderly_end(handler):
    handler.end_session()
    restarted = ServerHandler(settings=handler.settings)
    restarted.end_session()
    events = handler.events.list_events()
    starts = [e.session_id for e in events if e.event_type == "session_start"]
    ends = [e.session_id for e in events if e.event_type == "session_end"]
    assert len(starts) == len(ends) == 2
    assert len(set(starts)) == 2
    assert set(starts) == set(ends)


@pytest.mark.asyncio
@pytest.mark.parametrize("data", [
    {}, {"reason": "crashed"}, {"reason": []},
    {"reason": "extension_deactivated", "sessionId": "fake"},
])
async def test_session_end_accepts_only_observed_shutdown_reason(handler, data):
    with pytest.raises(ValueError):
        await _call(handler, "recordLearningEvent", eventType="session_end", data=data)
    assert [e.event_type for e in handler.events.list_events()] == ["session_start"]


def test_latest_page_selects_last_events_but_returns_chronological_order(tmp_path):
    store = LearningEventStore(tmp_path / "events.db")
    recorded = [store.record("code_edit", course_id="c", lesson_id="l",
                             timestamp="2026-01-01T00:00:00Z", data={"index": i})
                for i in range(5)]
    store.record("code_edit", course_id="other", timestamp="2026-02-01T00:00:00Z")
    assert store.list_events(course_id="c", limit=2) == recorded[:2]
    assert store.list_events(course_id="c", lesson_id="l", limit=2, order="latest") == recorded[-2:]
    assert store.list_events(course_id="c", limit=None, order="latest") == recorded
    assert store.count_events(course_id="c", lesson_id="l") == 5
    assert store.count_events(course_id="other", lesson_id="l") == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("query", [{"latest": True}, {"order": "latest"}])
async def test_recent_events_rpc_has_more_refers_to_matching_history(handler, query):
    events = [handler.events.record("code_edit", course_id="c", lesson_id="l",
                                    timestamp=f"2026-01-0{i + 1}T00:00:00Z") for i in range(3)]
    response = await _call(handler, "getLearningEvents", courseId="c", lessonId="l", limit=2, **query)
    assert response["events"] == [e.as_dict() for e in events[-2:]]
    assert response["hasMore"] is True
    assert response["totalCount"] == 3
    assert response["order"] == "latest"
    response = await _call(handler, "getLearningEvents", courseId="c", lessonId="l", limit=3, **query)
    assert response["hasMore"] is False
    legacy = await _call(handler, "getLearningEvents", courseId="c", lessonId="l", limit=2)
    assert legacy["events"] == [e.as_dict() for e in events[:2]]


@pytest.mark.asyncio
@pytest.mark.parametrize("params", [
    {"order": "invalid"}, {"latest": "true"}, {"latest": True, "order": "oldest"},
])
async def test_recent_events_rejects_ambiguous_order(handler, params):
    with pytest.raises(ValueError):
        await _call(handler, "getLearningEvents", **params)


@pytest.mark.asyncio
async def test_failed_input_stream_does_not_claim_orderly_session_end(handler, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from sparktutor.server import __main__ as server

    class BrokenInput:
        def __iter__(self):
            raise OSError("input stream interrupted")

    monkeypatch.setattr(server, "ServerHandler", lambda **kwargs: handler)
    monkeypatch.setattr(server.sys, "stdin", SimpleNamespace(buffer=BrokenInput()))
    await asyncio.wait_for(server.main(), timeout=3)
    assert [e.event_type for e in handler.events.list_events()] == ["session_start"]
