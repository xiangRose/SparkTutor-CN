"""Opt-in real execution through the web adapter, not a mocked executor."""

import asyncio
import os
from pathlib import Path

import pytest

from sparktutor.config.settings import ExecutionMode, Settings
from sparktutor.engine.lesson_loader import load_lesson
from sparktutor.web.runtime import WebRuntime


pytestmark = [pytest.mark.spark, pytest.mark.skipif(
    os.environ.get("SPARKTUTOR_TEST_SPARK") != "1", reason="Requires explicit real execution")]
SESSION = "real-web-execution-session"


@pytest.fixture
def runtime(tmp_path):
    pytest.importorskip("pyspark")
    instance = WebRuntime(Settings(data_dir=tmp_path, execution_mode=ExecutionMode.LOCAL, timeout_seconds=90))
    yield instance
    instance.close()


def call(runtime, method, params=None):
    return asyncio.run_coroutine_threadsafe(
        runtime.dispatch(SESSION, method, params or {}), runtime.loop
    ).result(timeout=150)


def test_web_run_delivers_real_stdout_in_its_notification_stream(runtime):
    loaded = call(runtime, "loadLesson", {"courseId": "learning_spark", "lessonIdx": 0})
    result = call(runtime, "run", {"code": 'print("WEB_REAL_EXECUTION_OUTPUT")',
                                  "contextToken": loaded["contextToken"], "operationId": "real-output"})
    assert result["exitCode"] == 0, result
    assert result["mode"] == "local" and "WEB_REAL_EXECUTION_OUTPUT" in result["stdout"]
    events = call(runtime, "pollWebNotifications", {"after": 0})["notifications"]
    assert any(item["method"] == "output" and item["params"]["operationId"] == "real-output"
               and "WEB_REAL_EXECUTION_OUTPUT" in item["params"]["line"] for item in events)


def test_web_submit_uses_course_tests_and_persists_the_accepted_code(runtime):
    call(runtime, "listCourses")
    handler = runtime.sessions[SESSION]["handler"]
    directory = handler.registry.courses_dir / "learning_spark/lessons/01_getting_started"
    lesson = load_lesson(directory)
    steps = [step for step in lesson.steps_for_depth("beginner") if step.cls != "meta"]
    index = next(index for index, step in enumerate(steps) if step.id == "practice-filter")
    handler.progress.save("learning_spark", lesson.id, current_step=index, total_steps=len(steps),
                          depth="beginner", current_step_id="practice-filter", last_code="")
    loaded = call(runtime, "loadLesson", {"courseId": "learning_spark", "lessonIdx": 0})
    reference = (directory / steps[index].solution_code).read_text(encoding="utf-8")
    wrong = reference.replace('return df.filter(f.col("State") == "CA")',
                              'return df.filter(f.col("State") == "CA").dropDuplicates()')
    assert wrong != reference
    rejected = call(runtime, "webSubmit", {"code": wrong, "contextToken": loaded["contextToken"]})
    assert not rejected["passed"]
    execution = call(runtime, "webLastExecution")
    # Native Windows launchers can route the Python traceback to stdout.
    errors = execution["stdout"] + "\n" + execution["stderr"]
    assert "AssertionError" in errors
    assert "只保留 CA 行，不聚合或丢失重复行" in errors
    accepted = call(runtime, "webSubmit", {"code": reference, "contextToken": loaded["contextToken"]})
    assert accepted["passed"], accepted
    assert runtime.workspace.load("learning_spark", lesson.id, "practice-filter") == reference
