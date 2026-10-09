"""Server handler: dispatches JSON-lines requests to engine components."""

from __future__ import annotations

import sys
import json
import re
import time
import uuid

from dataclasses import asdict
from pathlib import Path
from typing import Callable, Optional

from sparktutor.config.settings import ExecutionMode, Settings
from sparktutor.courses.registry import CourseRegistry
from sparktutor.engine.adaptive import Depth, LearnerProfile
from sparktutor.engine.evaluator import Evaluator
from sparktutor.engine.executor import Executor
from sparktutor.engine.assessment_evidence import execution_failure_kind
from sparktutor.engine.diagnosis import build_diagnosis
from sparktutor.engine.exercise_catalog import build_exercise_catalog
from sparktutor.engine.lesson_runner import LessonRunner
from sparktutor.engine.learning_dashboard import build_learning_dashboard, get_course_progress
from sparktutor.engine.learning_history import LearningHistory
from sparktutor.engine.learning_trend import build_learning_trend
from sparktutor.state.progress import ProgressStore
from sparktutor.state.learning_events import LearningEvent, LearningEventStore

from .protocol import Notification


_EDIT_FIELD_LIMITS = {
    "changeCount": 100_000,
    "addedChars": 10_000_000,
    "removedChars": 10_000_000,
    "documentVersion": 2_147_483_647,
    "burstDurationMs": 86_400_000,
}


def _step_to_dict(step) -> dict:
    """Serialize a Step dataclass to a JSON-friendly dict."""
    if step is None:
        return {}
    return {
        "id": step.id,
        "cls": step.cls,
        "depth": step.depth,
        "output": step.output,
        "answerChoices": step.answer_choices,
        "correctAnswer": step.correct_answer,
        "hint": step.hint,
        "starterCode": step.starter_code,
        "solutionCode": step.solution_code,
        "requiresExecution": step.requires_execution,
        "lessonTitle": step.lesson_title,
        "estimatedMinutes": step.estimated_minutes,
        "validation": [
            {"type": v.type, "params": v.params} for v in step.validation
        ],
    }


def _feedback_to_dict(item) -> dict:
    return {
        "line": item.line,
        "severity": item.severity,
        "message": item.message,
        "suggestion": item.suggestion,
        "category": getattr(item, "category", None),
    }


class ServerHandler:
    """Routes incoming requests to engine methods and returns result dicts."""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        write_notification: Optional[Callable[[Notification], None]] = None,
    ):
        self.settings = settings or Settings.load()
        self._write_notification = write_notification or (lambda n: None)

        self.registry = CourseRegistry()
        self.progress = ProgressStore(
            db_path=self.settings.data_dir / "progress.db"
        )
        self.events = LearningEventStore(
            db_path=self.settings.data_dir / "progress.db"
        )
        self.executor = Executor(settings=self.settings)
        self.evaluator = Evaluator(settings=self.settings)

        self._runner: Optional[LessonRunner] = None
        self._profile = LearnerProfile()
        self._current_course_id: Optional[str] = None
        self._session_id = uuid.uuid4().hex
        self._session_end_event: Optional[LearningEvent] = None
        self._task_started_at: dict[str, float] = {}
        self._hinted_tasks: set[str] = set()
        self._demo_running = False
        self.events.record("session_start", session_id=self._session_id)

    def _task_id(self) -> str:
        if self._runner is None or self._runner.state is None:
            return ""
        step = self._runner.state.current_step
        step_id = step.id if step else "finished"
        return f"{self._runner.state.lesson.id}:{step_id}"

    def _record_event(self, event_type: str, data: Optional[dict] = None) -> Optional[LearningEvent]:
        """Record behavior metadata without storing source code or chat text."""
        if self._runner is None or self._runner.state is None:
            return
        state = self._runner.state
        task_id = self._task_id()
        payload = dict(data or {})
        step = state.current_step
        if step:
            payload.update({
                "eventVersion": 2,
                "stepId": step.id,
                "knowledgeComponents": list(step.knowledge_components),
                "context": step.context,
                "transfer": dict(step.transfer),
            })
        started = self._task_started_at.get(task_id)
        if started is not None and event_type in {"code_run", "code_submit", "task_complete"}:
            payload.setdefault("durationMs", round((time.monotonic() - started) * 1000))
        return self.events.record(
            event_type,
            course_id=self._current_course_id or self._runner.course_id,
            lesson_id=state.lesson.id,
            task_id=task_id,
            session_id=self._session_id,
            task_type=state.current_step.cls if state.current_step else "",
            attempt_number=state.attempts,
            data=payload,
        )

    @staticmethod
    def _error_type(stderr: str) -> str:
        match = re.search(r"([A-Za-z]+Error|AnalysisException|ClassNotFoundException)", stderr)
        return match.group(1) if match else ("ExecutionError" if stderr else "")

    async def dispatch(self, msg: dict) -> dict:
        """Route a request message to the appropriate handler method."""
        method = msg.get("method", "")
        params = msg.get("params", {})
        print(f"sparktutor-server: dispatch {method}", file=sys.stderr)

        handler_map = {
            "listCourses": self._list_courses,
            "getCourseProgress": self._get_course_progress,
            "getLearningDashboard": self._get_learning_dashboard,
            "getLearningHistory": self._get_learning_history,
            "getTaskHistory": self._get_task_history,
            "getLearningReview": self._get_learning_review,
            "getLearningTrend": self._get_learning_trend,
            "runDemoScenario": self._run_demo_scenario,
            "loadLesson": self._load_lesson,
            "getStep": self._get_step,
            "run": self._run,
            "submit": self._submit,
            "advance": self._advance,
            "goBack": self._go_back,
            "getHint": self._get_hint,
            "chat": self._chat,
            "detectMode": self._detect_mode,
            "setExecutionMode": self._set_execution_mode,
            "resetLesson": self._reset_lesson,
            "getSolution": self._get_solution,
            "buildReviewPrompt": self._build_review_prompt,
            "buildChatPrompt": self._build_chat_prompt,
            "parseReviewResponse": self._parse_review_response,
            "completeReviewFailure": self._complete_review_failure,
            "getLearningEvents": self._get_learning_events,
            "recordLearningEvent": self._record_learning_event,
            "getDiagnosis": self._get_diagnosis,
            "openRecommendedExercise": self._open_recommended_exercise,
            "ping": self._ping,
        }

        handler = handler_map.get(method)
        if handler is None:
            raise ValueError(f"Unknown method: {method}")

        return await handler(params)

    async def _list_courses(self, params: dict) -> dict:
        courses = self.registry.list_courses()
        return {
            "courses": [
                {
                    "id": c.id,
                    "title": c.title,
                    "description": c.description,
                    "lessonCount": len(c.lessons),
                    "requiresLakehouse": c.requires_lakehouse,
                    "lessons": c.lessons,
                    "prerequisites": c.prerequisites,
                }
                for c in courses
            ]
        }

    async def _get_course_progress(self, params: dict) -> dict:
        return get_course_progress(self.registry, self.progress, params["courseId"],
                                   depth=self._profile.depth.value)

    async def _get_learning_dashboard(self, params: dict) -> dict:
        return build_learning_dashboard(self.registry, self.progress, self.events,
                                        course_id=params.get("courseId", ""),
                                        depth=self._profile.depth.value)

    def _history_snapshot(self, params: dict) -> LearningHistory:
        return LearningHistory(self.registry, self.events, snapshot_event_id=params.get("snapshotEventId"))

    async def _run_demo_scenario(self, params: dict) -> dict:
        from sparktutor.engine.demo_scenarios import run_demo_scenario

        run_id = params.get("runId", uuid.uuid4().hex)
        if not isinstance(run_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", run_id):
            raise ValueError("演示 runId 必须是 1–100 位字母、数字、下划线或短横线。")
        if self._demo_running:
            raise ValueError("诊断演示正在运行，请等待完成后再试。")
        self._demo_running = True
        try:
            report = await run_demo_scenario(
                scenario_id=params.get("scenarioId", "transfer_retry"),
                mode=params.get("mode", "recorded"),
                on_progress=lambda progress: self._write_notification(Notification("demoProgress", {**progress, "runId": run_id})),
            )
            return {**report, "runId": run_id}
        finally:
            self._demo_running = False

    async def _get_learning_history(self, params: dict) -> dict:
        return self._history_snapshot(params).get_history(
            course_id=params.get("courseId", ""), window=params.get("window", "all"),
            offset=params.get("offset", 0), limit=params.get("limit", 20),
            window_end=params.get("windowEnd"),
        )

    async def _get_learning_trend(self, params: dict) -> dict:
        return build_learning_trend(
            self.registry, self.events, course_id=params.get("courseId", ""),
            window=params.get("window", "all"), snapshot_event_id=params.get("snapshotEventId"),
            window_end=params.get("windowEnd"), depth=self._profile.depth.value,
        )

    async def _get_task_history(self, params: dict) -> dict:
        return self._history_snapshot(params).get_task_history(
            params.get("courseId"), params.get("lessonId"), params.get("taskId"),
            offset=params.get("offset", 0), limit=params.get("limit", 50),
        )

    async def _get_learning_review(self, params: dict) -> dict:
        return self._history_snapshot(params).get_review(
            params.get("courseId"), params.get("lessonId"), params.get("taskId"), params.get("eventId"),
            scope_course_id=params.get("scopeCourseId"), depth=self._profile.depth.value,
        )

    async def _load_lesson(self, params: dict, *, target_step_id: Optional[str] = None) -> dict:
        course_id = params["courseId"]
        lesson_idx = params["lessonIdx"]
        depth = params.get("depth", self._profile.depth.value)

        course = self.registry.get_course(course_id)
        if course is None:
            raise ValueError(f"Unknown course: {course_id}")
        if lesson_idx < 0 or lesson_idx >= len(course.lessons):
            raise ValueError(f"Lesson index {lesson_idx} out of range")

        lesson_id = course.lessons[lesson_idx]
        lesson_dir = self.registry.courses_dir / course_id / "lessons" / lesson_id

        self._profile.depth = Depth(depth)
        self._current_course_id = course_id
        self._runner = LessonRunner(
            course_id=course_id,
            executor=self.executor,
            evaluator=self.evaluator,
            progress=self.progress,
            profile=self._profile,
        )
        state = self._runner.load_lesson(lesson_dir)
        if target_step_id is not None:
            self._runner.open_practice_step(target_step_id)
        self._task_started_at.clear()
        self._hinted_tasks.clear()
        self._task_started_at[self._task_id()] = time.monotonic()
        self._record_event(
            "lesson_loaded",
            {
                "currentStep": state.current_index,
                "totalSteps": len(state.filtered_steps),
                "depth": depth,
                "resumed": bool(restored_code := self._runner.get_restored_code()),
            },
        )
        self._record_event(
            "task_start",
            {"currentStep": state.current_index, "totalSteps": len(state.filtered_steps)},
        )

        step = state.current_step
        starter_code = self._runner.get_starter_code()

        result = {
            "step": _step_to_dict(step),
            "currentIndex": state.current_index,
            "totalSteps": len(state.filtered_steps),
            "lessonTitle": state.lesson.title,
            "lessonId": state.lesson.id,
            "restoredCode": restored_code,
            "legacyCode": self._runner.get_legacy_code(),
            "starterCode": starter_code,
            "firstStarterCode": self._runner.get_first_starter_code(),
            "practiceMode": self._runner.practice_mode,
        }
        if lesson_idx == 0 and course.prerequisites:
            result["coursePrerequisites"] = course.prerequisites
        return result

    async def _get_step(self, params: dict) -> dict:
        if self._runner is None or self._runner.state is None:
            raise ValueError("No lesson loaded")

        state = self._runner.state
        step = state.current_step
        starter_code = self._runner.get_starter_code()

        return {
            "step": _step_to_dict(step),
            "currentIndex": state.current_index,
            "totalSteps": len(state.filtered_steps),
            "starterCode": starter_code,
        }

    async def _run(self, params: dict) -> dict:
        code = params["code"]

        def on_output(line: str):
            self._write_notification(
                Notification("output", {"line": line})
            )

        result = await self.executor.execute(code, on_output=on_output)
        if self._runner and self._runner.state:
            self._runner.state.last_exec = result
        self._record_event(
            "code_run",
            {
                "exitCode": result.exit_code,
                "mode": result.mode.value,
                "errorType": self._error_type(result.stdout + "\n" + result.stderr),
                "assessmentSource": "execution",
                "assessmentEligible": execution_failure_kind(result) == "learner",
                "failureKind": execution_failure_kind(result),
            },
        )
        if result.exit_code != 0:
            self._record_event(
                "error",
                {"errorType": self._error_type(result.stdout + "\n" + result.stderr), "source": "code_run"},
            )
        return {
            "exitCode": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "mode": result.mode.value,
            "validationPassed": result.validation_passed,
        }

    async def _submit(self, params: dict) -> dict:
        if self._runner is None:
            raise ValueError("No lesson loaded")

        code = params["code"]
        result = await self._runner.submit(code)
        self._record_submission(result)
        if not result.passed:
            self._record_event(
                "error",
                {"errorType": "EvaluationError", "source": "code_submit"},
            )
        return {
            "passed": result.passed,
            "feedback": [_feedback_to_dict(f) for f in result.feedback],
            "encouragement": result.encouragement,
            "skillSignals": result.skill_signals,
        }

    async def _advance(self, params: dict) -> dict:
        if self._runner is None:
            raise ValueError("No lesson loaded")

        self._runner.require_can_advance()
        previous_task_id = self._task_id()
        previous_started = self._task_started_at.get(previous_task_id)
        previous_state = self._runner.state
        if previous_state and previous_state.current_step:
            duration_ms = None
            if previous_started is not None:
                duration_ms = round((time.monotonic() - previous_started) * 1000)
            self._record_event(
                "task_complete",
                {
                    "passed": (
                        previous_state.last_result.passed
                        if previous_state.last_result else None
                    ),
                    "currentStep": previous_state.current_index,
                    "totalSteps": len(previous_state.filtered_steps),
                    "durationMs": duration_ms,
                },
            )

        step = self._runner.advance(current_code=params.get("code", ""))
        if self._runner.state and not self._runner.state.is_finished:
            self._task_started_at[self._task_id()] = time.monotonic()
            self._record_event("task_start", {"currentStep": self._runner.state.current_index})
        if self._runner.state and self._runner.state.is_finished:
            return {"finished": True, "practiceMode": self._runner.practice_mode}

        starter_code = self._runner.get_starter_code()
        return {
            "finished": False,
            "step": _step_to_dict(step),
            "currentIndex": self._runner.state.current_index if self._runner.state else 0,
            "totalSteps": len(self._runner.state.filtered_steps) if self._runner.state else 0,
            "starterCode": starter_code,
        }

    async def _go_back(self, params: dict) -> dict:
        if self._runner is None:
            raise ValueError("No lesson loaded")

        step = self._runner.go_back(current_code=params.get("code", ""))
        if step is None:
            return {"atStart": True}

        starter_code = self._runner.get_starter_code()
        return {
            "atStart": False,
            "step": _step_to_dict(step),
            "currentIndex": self._runner.state.current_index if self._runner.state else 0,
            "totalSteps": len(self._runner.state.filtered_steps) if self._runner.state else 0,
            "starterCode": starter_code,
        }

    async def _get_hint(self, params: dict) -> dict:
        if self._runner is None:
            raise ValueError("No lesson loaded")

        hint = self._runner.get_hint()
        if hint:
            self._hinted_tasks.add(self._task_id())
        self._record_event(
            "hint_request",
            {"available": bool(hint), "hintType": "step"},
        )
        return {"hint": hint or "本步骤暂无提示。"}

    async def _chat(self, params: dict) -> dict:
        question = params["question"]

        step_context = ""
        code_context = params.get("code", "")
        lesson_title = ""
        extra_context = ""

        if self._runner and self._runner.state:
            lesson_title = self._runner.state.lesson.title
            step = self._runner.state.current_step
            if step:
                step_context = step.output

            # Include last execution output so the tutor can reference it
            last_exec = self._runner.state.last_exec
            if last_exec:
                parts = []
                if last_exec.stdout:
                    parts.append(f"Last execution stdout:\n{last_exec.stdout[:2000]}")
                if last_exec.stderr:
                    parts.append(f"Last execution stderr:\n{last_exec.stderr[:2000]}")
                if parts:
                    extra_context += "\n".join(parts)

            # Include last feedback so the tutor knows what was already said
            last_result = self._runner.state.last_result
            if last_result and last_result.feedback:
                fb_lines = []
                for fb in last_result.feedback[:10]:
                    cat = f"[{fb.category}] " if getattr(fb, "category", None) else ""
                    fb_lines.append(f"  {cat}{fb.message}")
                extra_context += "\nPrior feedback:\n" + "\n".join(fb_lines)

        answer = await self.evaluator.chat(
            question=question,
            lesson_title=lesson_title,
            step_context=step_context,
            code_context=code_context,
            depth=self._profile.depth.value,
            extra_context=extra_context,
        )
        self._record_event("chat_request", {"providerHandled": True})
        return {"answer": answer}

    async def _get_solution(self, params: dict) -> dict:
        if self._runner is None or self._runner.state is None:
            raise ValueError("No lesson loaded")

        step = self._runner.state.current_step
        if step is None or not step.solution_code:
            return {"solution": ""}

        solution_path = self._runner.state.lesson.base_path / step.solution_code
        if solution_path.exists():
            self._record_event("solution_view", {"available": True})
            return {"solution": solution_path.read_text(encoding="utf-8")}
        self._record_event("solution_view", {"available": False})
        return {"solution": ""}

    async def _reset_lesson(self, params: dict) -> dict:
        course_id = params["courseId"]
        lesson_id = params["lessonId"]
        self.progress.reset_lesson(course_id, lesson_id)
        # Clear the runner so the lesson reloads fresh
        self._runner = None
        return {"ok": True}

    async def _build_review_prompt(self, params: dict) -> dict:
        """Run local checks, then build review prompt if AI review is needed."""
        if self._runner is None:
            raise ValueError("No lesson loaded")

        code = params["code"]
        result, needs_ai, review_kwargs = await self._runner.submit_local(code)
        response: dict = {"needsAiReview": needs_ai}

        if result is not None:
            self._record_submission(result)
            if not result.passed:
                self._record_event(
                    "error",
                    {"errorType": "EvaluationError", "source": "local_submit"},
                )
            response["localResult"] = {
                "passed": result.passed,
                "feedback": [_feedback_to_dict(f) for f in result.feedback],
                "encouragement": result.encouragement,
                "skillSignals": result.skill_signals,
            }

        if needs_ai and review_kwargs:
            messages = self.evaluator.build_review_prompt(**review_kwargs)
            response["messages"] = messages
            response["reviewId"] = self._runner.pending_review_id

        return response

    async def _build_chat_prompt(self, params: dict) -> dict:
        """Build chat messages for external AI provider."""
        question = params["question"]

        step_context = ""
        code_context = params.get("code", "")
        lesson_title = ""
        extra_context = ""

        if self._runner and self._runner.state:
            lesson_title = self._runner.state.lesson.title
            step = self._runner.state.current_step
            if step:
                step_context = step.output

            last_exec = self._runner.state.last_exec
            if last_exec:
                parts = []
                if last_exec.stdout:
                    parts.append(f"Last execution stdout:\n{last_exec.stdout[:2000]}")
                if last_exec.stderr:
                    parts.append(f"Last execution stderr:\n{last_exec.stderr[:2000]}")
                if parts:
                    extra_context += "\n".join(parts)

            last_result = self._runner.state.last_result
            if last_result and last_result.feedback:
                fb_lines = []
                for fb in last_result.feedback[:10]:
                    cat = f"[{fb.category}] " if getattr(fb, "category", None) else ""
                    fb_lines.append(f"  {cat}{fb.message}")
                extra_context += "\nPrior feedback:\n" + "\n".join(fb_lines)

        messages = self.evaluator.build_chat_messages(
            question=question,
            lesson_title=lesson_title,
            step_context=step_context,
            code_context=code_context,
            depth=self._profile.depth.value,
            extra_context=extra_context,
        )
        self._record_event("chat_request", {"providerHandled": False})
        return {"messages": messages}

    async def _parse_review_response(self, params: dict) -> dict:
        """Parse raw AI response text into EvalResult."""
        if (self._runner is None or not self._runner.pending_review_id
                or params.get("reviewId") != self._runner.pending_review_id):
            raise ValueError("这次 AI 评审对应的提交已失效，请在当前练习重新提交。")
        raw_text = params["rawText"]
        result = self.evaluator.parse_review_response(raw_text)

        # Complete the state tracking that submit_local() left pending
        if self._runner and self._runner.state:
            self._runner.complete_review(result)
            self._record_submission(result, ai_review=True)
            if not result.passed:
                self._record_event(
                    "error",
                    {"errorType": "EvaluationError", "source": "ai_review"},
                )

        return {
            "passed": result.passed,
            "feedback": [_feedback_to_dict(f) for f in result.feedback],
            "encouragement": result.encouragement,
            "skillSignals": result.skill_signals,
        }

    async def _complete_review_failure(self, params: dict) -> dict:
        """End an unavailable external review with the same tracking as Claude."""
        return await self._parse_review_response({
            "reviewId": params.get("reviewId"),
            "rawText": json.dumps({
                "passed": False,
                "feedback": [{"severity": "warning", "category": None,
                              "message": str(params.get("message", "AI 评审暂不可用，请稍后重试。"))[:1500]}],
            }, ensure_ascii=False),
        })

    async def _get_learning_events(self, params: dict) -> dict:
        latest = params.get("latest", False)
        if type(latest) is not bool:
            raise ValueError("latest must be a boolean")
        order = params.get("order", "latest" if latest else "oldest")
        if latest and order != "latest":
            raise ValueError("latest and order disagree")
        course_id = params.get("courseId", "")
        lesson_id = params.get("lessonId", "")
        events = self.events.list_events(
            course_id=course_id,
            lesson_id=lesson_id,
            limit=params.get("limit", 2000),
            order=order,
        )
        total = self.events.count_events(course_id=course_id, lesson_id=lesson_id)
        return {"events": [event.as_dict() for event in events],
                "hasMore": total > len(events), "totalCount": total, "order": order}

    def end_session(self, reason: str = "server_shutdown") -> dict:
        """Record an observed orderly end once; never infer one after a crash."""
        if reason not in {"extension_deactivated", "server_shutdown"}:
            raise ValueError("Unsupported session end reason")
        if self._session_end_event is not None:
            return {"recorded": False, "reason": "already_ended",
                    "event": self._session_end_event.as_dict()}
        self._session_end_event = self.events.record(
            "session_end", course_id=self._current_course_id or "",
            session_id=self._session_id, data={"reason": reason},
        )
        return {"recorded": True, "event": self._session_end_event.as_dict()}

    async def _record_learning_event(self, params: dict) -> dict:
        """Accept bounded client telemetry, never client-authored assessments."""
        if not isinstance(params, dict):
            raise ValueError("Learning event parameters must be an object")
        event_type = params.get("eventType")
        if not isinstance(event_type, str) or event_type not in {"code_edit", "session_end"}:
            raise ValueError("Clients may only record code_edit or session_end")
        allowed = {"eventType", "data"}
        if event_type == "code_edit":
            allowed.update({"courseId", "lessonId", "taskId"})
        if set(params) - allowed:
            raise ValueError("Unsupported learning event fields")
        data = params.get("data")
        if not isinstance(data, dict):
            raise ValueError("Learning event data must be an object")
        if event_type == "session_end":
            if (set(data) != {"reason"} or not isinstance(data["reason"], str)
                    or data["reason"] not in {"extension_deactivated", "server_shutdown"}):
                raise ValueError("Session end requires an allowed reason only")
            return self.end_session(data["reason"])

        required_counts = {"changeCount", "addedChars", "removedChars"}
        if not required_counts.issubset(data) or set(data) - set(_EDIT_FIELD_LIMITS):
            raise ValueError("Code edits require change counts and allow only numeric edit metadata")
        if any(type(value) is not int or not 0 <= value <= _EDIT_FIELD_LIMITS[name]
               for name, value in data.items()):
            raise ValueError("Code edit metadata must contain bounded non-negative integers")
        if any(not isinstance(params.get(name), str) or not params[name]
               for name in ("courseId", "lessonId", "taskId")):
            raise ValueError("Code edits require explicit courseId, lessonId and taskId")
        if self._session_end_event is not None:
            return {"recorded": False, "reason": "session_ended"}
        state = self._runner.state if self._runner else None
        if state is None or state.current_step is None or state.current_step.cls not in {"cmd_question", "script"}:
            return {"recorded": False, "reason": "no_code_task"}
        expected = (self._runner.course_id, state.lesson.id, self._task_id())
        supplied = tuple(params[name] for name in ("courseId", "lessonId", "taskId"))
        if supplied != expected:
            return {"recorded": False, "reason": "stale_task"}
        event = self._record_event("code_edit", data)
        return {"recorded": True, "event": event.as_dict()}

    def _record_submission(self, result, *, ai_review: bool = False) -> None:
        hint_used = self._task_id() in self._hinted_tasks
        self._hinted_tasks.discard(self._task_id())
        execution = self._runner.state.last_exec if self._runner and self._runner.state else None
        self._record_event("code_submit", {
            "passed": result.passed,
            "hintUsed": hint_used,
            "aiReview": ai_review or result.assessment_source == "ai_review",
            "assessmentSource": result.assessment_source,
            "assessmentEligible": result.assessment_eligible,
            "failureKind": result.failure_kind,
            "mode": execution.mode.value if execution else "local_check",
            "validationPassed": execution.validation_passed if execution else False,
            "errorTypes": [self._error_type(item.message) for item in result.feedback
                           if item.severity == "error"],
        })

    async def _get_diagnosis(self, params: dict) -> dict:
        course_id = params.get("courseId", "")
        if course_id and self.registry.get_course(course_id) is None:
            raise ValueError("未找到指定课程。")
        return build_diagnosis(
            self.events.list_events(limit=None), build_exercise_catalog(self.registry),
            course_id=course_id, depth=self._profile.depth.value,
        )

    async def _open_recommended_exercise(self, params: dict) -> dict:
        """Revalidate the recommendation; never trust a stale index from the UI."""
        report = await self._get_diagnosis({"courseId": params.get("scopeCourseId", "")})
        exercise = report.get("recommendedExercise")
        if exercise is None or any(params.get(key) != exercise.get(key)
                                   for key in ("courseId", "lessonId", "stepId")):
            raise ValueError("推荐已变化或练习不可用，请刷新学习画像后再试。")
        result = await self._load_lesson({
            "courseId": exercise["courseId"], "lessonIdx": exercise["lessonIndex"],
        }, target_step_id=exercise["stepId"])
        return {**result, "courseId": exercise["courseId"],
                "lessonIdx": exercise["lessonIndex"], "depth": self._profile.depth.value}

    async def _detect_mode(self, params: dict) -> dict:
        mode = await self.executor.detect_mode()
        return {"mode": mode.value}

    async def _set_execution_mode(self, params: dict) -> dict:
        self.settings.execution_mode = ExecutionMode(params["mode"])
        self.executor._detected_mode = None
        return await self._detect_mode({})

    async def _ping(self, params: dict) -> dict:
        """Health check for the configured AI provider (Anthropic client)."""
        ok, message = await self.evaluator.ping()
        return {"ok": ok, "message": message}
