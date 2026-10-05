"""Integration of scaffolding, provider-independent grading and navigation."""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from sparktutor.config.settings import Settings
from sparktutor.engine.evaluator import EvalResult, Evaluator
from sparktutor.engine.executor import ExecMode, ExecResult, Executor
from sparktutor.engine.exercise_validation import VALIDATION_MARKER
from sparktutor.engine.lesson_loader import Step
from sparktutor.engine.lesson_runner import LessonRunner
from sparktutor.state.progress import ProgressStore


@pytest.fixture
def runner(sample_lesson_dir, tmp_path):
    settings = Settings(data_dir=tmp_path)
    instance = LessonRunner(
        "test_course", Executor(settings, force_dry_run=True), Evaluator(settings),
        ProgressStore(tmp_path / "progress.db"),
    )
    instance.load_lesson(sample_lesson_dir)
    return instance


def test_later_code_step_gets_scaffold(runner):
    runner.state.current_index = 2
    scaffold = runner.get_starter_code()
    assert isinstance(scaffold, str)
    assert "TODO" in scaffold


def test_explicit_starter_uses_utf8(runner):
    path = runner.state.lesson.base_path / "starter.py"
    path.write_text("# 中文说明\nvalue = None", encoding="utf-8")
    runner.state.filtered_steps = [Step(cls="script", starter_code="starter.py")]
    assert runner.get_starter_code() == path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_next_requires_correct_answer(runner):
    runner.advance()
    with pytest.raises(ValueError):
        runner.advance()
    await runner.submit("3")
    with pytest.raises(ValueError):
        runner.advance()
    assert (await runner.submit("4")).passed
    runner.advance()
    assert runner.current_step().cls == "cmd_question"


@pytest.mark.asyncio
async def test_external_review_keeps_submitted_code(runner):
    runner.state.current_index = 2
    code = "x = int('42')"
    _, needs_ai, _ = await runner.submit_local(code)
    assert needs_ai
    assert runner.progress.get("test_course", runner.state.lesson.id).last_code == code
    runner.complete_review(EvalResult(passed=True))
    assert runner.progress.get("test_course", runner.state.lesson.id).last_code == code


@pytest.mark.asyncio
async def test_hint_is_not_a_failed_submission(runner):
    runner.advance()
    runner.get_hint()
    assert runner.profile.total_attempts == 0
    await runner.submit("4")
    assert runner.profile.total_attempts == 1
    assert runner.profile.hint_usage == 1
    assert runner.profile.correct_first_try == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,exit_code,stdout,passed", [
    (ExecMode.DRY_RUN, 0, "[dry-run] Syntax OK", False),
    (ExecMode.LOCAL, 1, VALIDATION_MARKER, False),
    (ExecMode.LOCAL, 0, "", False),
    (ExecMode.LOCAL, 0, VALIDATION_MARKER, True),
])
async def test_script_submission_requires_real_completed_tests(runner, mode, exit_code, stdout, passed):
    (runner.state.lesson.base_path / "starter.py").write_text(
        "def double(x):\n    pass\nif __name__ == '__main__':\n    assert double(2) == 4\n",
        encoding="utf-8",
    )
    runner.state.filtered_steps = [Step(cls="script", starter_code="starter.py", requires_execution=True)]
    runner.executor.execute = AsyncMock(return_value=ExecResult(mode, exit_code, stdout, ""))
    runner.evaluator.claude_review = AsyncMock(side_effect=AssertionError("AI cannot decide test results"))
    result = await runner.submit("def double(x):\n    return x * 2")
    assert result.passed is passed
    assert "_run_course_tests" in runner.executor.execute.call_args.args[0]
    runner.evaluator.claude_review.assert_not_called()


@pytest.mark.asyncio
async def test_failed_execution_cannot_pass_exact_answer(runner):
    step = Step(cls="cmd_question", correct_answer="x = 42", requires_execution=True)
    result = await runner.evaluator.evaluate(
        "x = 42", step, exec_result=ExecResult(ExecMode.LOCAL, 1, "", "AssertionError")
    )
    assert not result.passed


def test_legacy_code_is_preserved_without_inserting_it_in_another_step(runner):
    lesson = runner.state.lesson
    runner.progress.save("test_course", lesson.id, 2, len(runner.state.filtered_steps), last_code="old_task = 1")
    runner.load_lesson(lesson.base_path)
    assert runner.get_restored_code() == ""
    assert runner.get_legacy_code() == "old_task = 1"


def test_resume_uses_original_step_id_instead_of_filtered_index(runner):
    lesson = runner.state.lesson
    wanted = runner.state.filtered_steps[2]
    runner.progress.save("test_course", lesson.id, 0, len(runner.state.filtered_steps),
                         last_code="x = 42", current_step_id=wanted.id)
    runner.load_lesson(lesson.base_path)
    assert runner.current_step().id == wanted.id
    assert runner.get_restored_code() == "x = 42"
