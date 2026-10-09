"""Lesson state machine: load → present → evaluate → advance."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
import uuid
from typing import Optional

from sparktutor.engine.adaptive import Depth, LearnerProfile
from sparktutor.engine.evaluator import EvalResult, Evaluator
from sparktutor.engine.executor import ExecMode, ExecResult, Executor
from sparktutor.engine.exercise_validation import VALIDATION_MARKER, build_validation_script
from sparktutor.engine.feedback import parse_stderr
from sparktutor.engine.lesson_loader import Lesson, Step, load_lesson
from sparktutor.state.progress import ProgressStore


class StepState(str, Enum):
    PRESENTING = "presenting"  # Showing content/question to user
    AWAITING_INPUT = "awaiting_input"  # Waiting for user code/answer
    EVALUATING = "evaluating"  # Running evaluation
    FEEDBACK = "feedback"  # Showing feedback
    COMPLETE = "complete"  # Step passed, ready for next


@dataclass
class RunnerState:
    lesson: Lesson
    filtered_steps: list[Step]
    current_index: int = 0
    step_state: StepState = StepState.PRESENTING
    attempts: int = 0
    last_result: Optional[EvalResult] = None
    last_exec: Optional[ExecResult] = None

    @property
    def current_step(self) -> Optional[Step]:
        if 0 <= self.current_index < len(self.filtered_steps):
            return self.filtered_steps[self.current_index]
        return None

    @property
    def is_finished(self) -> bool:
        return self.current_index >= len(self.filtered_steps)

    @property
    def progress_fraction(self) -> float:
        total = len(self.filtered_steps)
        if total == 0:
            return 1.0
        return self.current_index / total


class LessonRunner:
    """Drives lesson progression, evaluation, and state persistence."""

    def __init__(
        self,
        course_id: str,
        executor: Executor,
        evaluator: Evaluator,
        progress: Optional[ProgressStore] = None,
        profile: Optional[LearnerProfile] = None,
    ):
        self.course_id = course_id
        self.executor = executor
        self.evaluator = evaluator
        self.progress = progress or ProgressStore()
        self.profile = profile or LearnerProfile()
        self.state: Optional[RunnerState] = None
        self._pending_code: str = ""
        self._hint_used: bool = False
        self.pending_review_id: Optional[str] = None
        self.practice_mode = False

    def load_lesson(self, lesson_dir: Path) -> RunnerState:
        """Load a lesson and filter steps by depth."""
        self.pending_review_id = None
        self.practice_mode = False
        self._hint_used = False
        self._pending_code = ""
        lesson = load_lesson(lesson_dir)
        depth = self.profile.depth.value
        filtered = lesson.steps_for_depth(depth)

        # Skip meta steps in filtered list (they're metadata)
        filtered = [s for s in filtered if s.cls != "meta"]

        self.state = RunnerState(lesson=lesson, filtered_steps=filtered)

        # Resume from saved progress (only if depth matches)
        saved = self.progress.get(self.course_id, lesson.id)
        self._restored_code = ""
        self._legacy_code = ""
        if saved and not saved.completed and saved.depth == depth:
            matches = [i for i, step in enumerate(filtered) if step.id == saved.current_step_id]
            self.state.current_index = matches[0] if matches else max(0, min(saved.current_step, len(filtered) - 1))
            if saved.current_step_id and matches:
                self._restored_code = saved.last_code
            else:
                self._legacy_code = saved.last_code

        return self.state

    def open_practice_step(self, step_id: str) -> None:
        """Open one recommended exercise without rewriting sequential progress."""
        if self.state is None:
            raise ValueError("No lesson loaded")
        matches = [i for i, step in enumerate(self.state.filtered_steps) if step.id == step_id]
        if not matches:
            raise ValueError("推荐练习不适用于当前难度，请刷新学习画像。")
        if self.state.current_index != matches[0]:
            self._restored_code = ""
        self.state.current_index = matches[0]
        self.practice_mode = True

    def get_restored_code(self) -> str:
        """Return saved code from previous session (empty if none)."""
        code = getattr(self, "_restored_code", "")
        self._restored_code = ""  # Only return once
        return code

    def get_legacy_code(self) -> str:
        """Unbound legacy source is backed up by the UI, never inserted into a task."""
        code = getattr(self, "_legacy_code", "")
        self._legacy_code = ""
        return code

    def current_step(self) -> Optional[Step]:
        if self.state is None:
            return None
        return self.state.current_step

    async def submit(self, user_input: str) -> EvalResult:
        """Use the same execution gates for Anthropic and external providers."""
        result, needs_ai, kwargs = await self.submit_local(user_input)
        if needs_ai:
            result = await self.evaluator.claude_review(**kwargs)
            self.complete_review(result)
        return result or EvalResult(passed=False)

    async def submit_local(self, user_input: str) -> tuple[Optional[EvalResult], bool, dict]:
        """Submit and run local checks only. Returns (result, needs_ai, review_kwargs).

        Handles execution and state tracking like submit(), but uses
        evaluate_local() to stop before calling the AI review.
        """
        if self.state is None or self.state.current_step is None:
            return EvalResult(passed=False, feedback=[]), False, {}

        step = self.state.current_step
        self.state.step_state = StepState.EVALUATING
        self.state.attempts += 1
        self.state.last_result = None
        self.state.last_exec = None
        self.pending_review_id = None
        self._pending_code = user_input
        self._save_progress(user_input)

        # Execute if needed
        exec_result = None
        if step.requires_execution and step.cls in ("script", "cmd_question"):
            code_to_run = user_input
            if step.cls == "script":
                try:
                    if not step.starter_code:
                        raise ValueError("本练习缺少起始代码和课程测试。")
                    template = (self.state.lesson.base_path / step.starter_code).read_text(encoding="utf-8")
                    code_to_run = build_validation_script(user_input, template)
                except (SyntaxError, ValueError, OSError) as error:
                    exec_result = ExecResult(ExecMode.LOCAL, 1, "", str(error))
            if exec_result is None:
                exec_result = await self.executor.execute(code_to_run)
            if step.cls == "script":
                exec_result.validation_passed = (
                    exec_result.mode != ExecMode.DRY_RUN
                    and exec_result.success
                    and VALIDATION_MARKER in exec_result.stdout.splitlines()
                )
            self.state.last_exec = exec_result

        # Evaluate locally
        result, needs_ai, review_kwargs = await self.evaluator.evaluate_local(
            code=user_input,
            step=step,
            depth=self.profile.depth.value,
            exec_result=exec_result,
            lesson_title=self.state.lesson.title,
        )

        if needs_ai:
            self.pending_review_id = uuid.uuid4().hex
        if result is not None:
            self.complete_review(result)

        return result, needs_ai, review_kwargs

    def complete_review(self, result: EvalResult) -> None:
        """Finalize local or remote feedback without losing submitted code."""
        if self.state is None:
            return
        self.pending_review_id = None
        if self.state.last_exec and not self.state.last_exec.success:
            result.feedback.extend(parse_stderr(self.state.last_exec.stderr))
        self.state.last_result = result
        self.state.step_state = StepState.FEEDBACK
        self.profile.record_attempt(
            passed=result.passed, used_hint=self._hint_used,
            first_attempt=self.state.attempts == 1, signals=result.skill_signals,
        )
        self._hint_used = False
        self._save_progress(self._pending_code)

    def require_can_advance(self) -> None:
        if self.state and self.state.current_step:
            if self.state.current_step.cls in ("mult_question", "cmd_question", "script"):
                if not self.state.last_result or not self.state.last_result.passed:
                    raise ValueError("请先提交并通过当前练习，再进入下一步。")

    def advance(self, current_code: str = "") -> Optional[Step]:
        """Move to the next step (only if current step passed)."""
        if self.state is None:
            return None

        self.require_can_advance()

        self.state.current_index = (len(self.state.filtered_steps) if self.practice_mode
                                    else self.state.current_index + 1)
        self.state.attempts = 0
        self.state.step_state = StepState.PRESENTING
        self.state.last_result = None
        self.state.last_exec = None
        self._hint_used = False
        self.pending_review_id = None

        # The next step may use a different file. Never restore the previous
        # exercise's source into its newly selected step.
        self._save_progress("")

        return self.state.current_step

    def go_back(self, current_code: str = "") -> Optional[Step]:
        """Move to the previous step."""
        if self.state is None or self.practice_mode:
            return None
        if self.state.current_index <= 0:
            return None

        self.state.current_index -= 1
        self.state.attempts = 0
        self.state.step_state = StepState.PRESENTING
        self.state.last_result = None
        self.state.last_exec = None
        self._hint_used = False
        self.pending_review_id = None

        self._save_progress("")

        return self.state.current_step

    def _save_progress(self, last_code: str) -> None:
        if self.state is None or self.practice_mode:
            return
        saved = self.progress.get(self.course_id, self.state.lesson.id)
        self.progress.save(
            course_id=self.course_id,
            lesson_id=self.state.lesson.id,
            current_step=self.state.current_index,
            total_steps=len(self.state.filtered_steps),
            # Reviewing a completed lesson must not erase its completion fact.
            # An explicit reset deletes this row and starts a fresh record.
            completed=self.state.is_finished or bool(saved and saved.completed),
            depth=self.profile.depth.value,
            last_code=last_code,
            current_step_id=self.state.current_step.id if self.state.current_step else "finished",
        )

    def get_hint(self) -> Optional[str]:
        """Return the hint for the current step, if available."""
        step = self.current_step()
        if step and step.hint:
            self._hint_used = True
            return step.hint
        return None

    def get_starter_code(self) -> str:
        """Return starter code for the current step.

        If a step has an explicit starter file, use that. Otherwise, generate
        depth-appropriate scaffolding so the student knows where to start.
        """
        step = self.current_step()
        if step is None:
            return ""

        if step.starter_code and self.state:
            return (self.state.lesson.base_path / step.starter_code).read_text(encoding="utf-8")

        from sparktutor.engine.scaffolding import generate_scaffold
        return generate_scaffold(
            step_output=step.output, step_cls=step.cls, depth=self.profile.depth.value,
            correct_answer=step.correct_answer or "", hint=step.hint or "",
            lesson_title=self.state.lesson.title if self.state else "",
        )

    def get_first_starter_code(self) -> str:
        """Scan ahead for the first code step's starter code.

        Used to pre-populate exercise.py when a lesson starts on a text step,
        so the editor isn't empty.
        """
        if self.state is None:
            return ""
        for step in self.state.filtered_steps:
            if step.cls not in ("cmd_question", "script"):
                continue
            if step.starter_code:
                starter_path = self.state.lesson.base_path / step.starter_code
                if starter_path.exists():
                    return starter_path.read_text(encoding="utf-8")
            from sparktutor.engine.scaffolding import generate_scaffold
            return generate_scaffold(
                step_output=step.output,
                step_cls=step.cls,
                depth=self.profile.depth.value,
                correct_answer=step.correct_answer or "",
                hint=step.hint or "",
                lesson_title=self.state.lesson.title,
            )
        return ""
