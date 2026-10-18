"""A slow or failed submission must stream diagnostics before grading ends."""

import asyncio

import pytest

from sparktutor.config.settings import Settings
from sparktutor.courses.registry import CourseRegistry
from sparktutor.engine.executor import ExecMode, ExecResult
from sparktutor.engine.lesson_loader import Step
from sparktutor.server.handler import ServerHandler


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["submit", "buildReviewPrompt"])
async def test_course_submission_streams_before_timeout_without_creating_grades(
    method, sample_course_dir, tmp_path,
):
    notifications = []
    handler = ServerHandler(Settings(data_dir=tmp_path / "state"), write_notification=notifications.append)
    handler.registry = CourseRegistry(courses_dir=sample_course_dir.parent)
    await handler.dispatch({"method": "loadLesson", "params": {"courseId": "test_course", "lessonIdx": 0}})
    state = handler._runner.state
    (state.lesson.base_path / "stream_starter.py").write_text(
        "def double(x):\n    pass\nif __name__ == '__main__':\n    assert double(2) == 4\n",
        encoding="utf-8",
    )
    state.filtered_steps = [Step(id="stream_test", cls="script", requires_execution=True,
                                 starter_code="stream_starter.py")]
    state.current_index = 0

    async def execute(code, *, on_output=None):
        assert "_run_course_tests" in code
        assert on_output is not None
        on_output("PRIVATE_RUNTIME_PHASE: starting Spark")
        await asyncio.sleep(0)
        assert notifications[-1].method == "output"
        assert notifications[-1].params == {"line": "PRIVATE_RUNTIME_PHASE: starting Spark"}
        assert not any(event.event_type == "code_submit" for event in handler.events.list_events(limit=None))
        return ExecResult(ExecMode.LOCAL, 124, "PRIVATE_RUNTIME_PHASE: starting Spark",
                          "[sparktutor] Execution timed out after 120s")

    handler.executor.execute = execute
    result = await handler.dispatch({"method": method, "params": {"code": "def double(x):\n    return x * 2"}})
    grade = result["localResult"] if method == "buildReviewPrompt" else result
    assert grade["passed"] is False
    assert any("超时" in item["message"] for item in grade["feedback"])
    events = handler.events.list_events(limit=None)
    submission = next(event for event in events if event.event_type == "code_submit")
    assert submission.data["failureKind"] == "infrastructure"
    assert submission.data["assessmentEligible"] is False
    assert "PRIVATE_RUNTIME_PHASE" not in str([event.as_dict() for event in events])
