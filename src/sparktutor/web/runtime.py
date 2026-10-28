"""Browser transport adapters around the shared teaching and assessment engine."""

from __future__ import annotations

import asyncio
import base64
from collections import deque
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import secrets
import tempfile
import threading
import time
import uuid
import zipfile

from sparktutor.config.settings import ExecutionMode, Settings
from sparktutor.engine.exercise_catalog import build_exercise_catalog
from sparktutor.engine.isolated_executor import ExecutionIdentity, IsolatedExecutor, execution_scope
from sparktutor.server.handler import ServerHandler
from sparktutor.state.data_management import backup_learning_database
from sparktutor.state.checkins import CheckInStore

from .ai import WebAiManager
from .secret_store import atomic_write
from .workspace import WebWorkspace


SESSION_ID = re.compile(r"[A-Za-z0-9_-]{16,100}\Z")
OPERATION_ID = re.compile(r"[A-Za-z0-9_-]{1,100}\Z")
CONTEXT_METHODS = {
    "saveDraft", "getDraft", "run", "submit", "webSubmit", "advance", "goBack",
    "getHint", "getSolution", "chat", "webChat", "webCoach", "getLearningCoach",
    "buildCoachPrompt", "parseCoachResponse", "ackCoachMessage", "runCoachProbe",
    "buildReviewPrompt", "parseReviewResponse", "completeReviewFailure", "recordLearningEvent",
}
EXECUTION_METHODS = {"run", "submit", "webSubmit", "buildReviewPrompt", "runDemoScenario", "webCoach", "webChat", "chat"}


class SessionResetError(ValueError):
    def __init__(self, replacement: str):
        super().__init__("学习记录已更新，请重新开始当前学习页面。")
        self.session_id = replacement


class WebRuntime:
    """Keep independent tab state while sharing one local learning workspace.

    Notifications bypass lesson locks. A writer barrier prevents an in-flight
    run, another tab, or an orderly shutdown from undoing a requested reset.
    """

    def __init__(self, settings: Settings, *, multi_user=False, isolated_pool=None, account_id=""):
        self.settings = settings
        self.checkins = CheckInStore(settings.data_dir / "progress.db")
        self.multi_user = multi_user
        self.isolated_pool = isolated_pool
        self.account_id = account_id
        if isolated_pool is not None and (not multi_user or not account_id):
            raise ValueError("隔离执行必须绑定已认证的账号。")
        self.token = secrets.token_urlsafe(32)
        self.sessions: dict = {}
        self.workspace = WebWorkspace(settings.data_dir / "workspace")
        self.ai_manager = WebAiManager(settings)
        self.ai_manager.use_environment = not multi_user
        self._remembered_mode = self._read_execution_preference()
        self._retired_sessions = self._read_retired_sessions()
        self._pending_sessions: dict = {}
        self._tasks: set = set()
        self._closing = False
        self._condition = asyncio.Condition()
        self._readers = 0
        self._writer = False
        self._waiting_writers = 0
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, name="sparktutor-web", daemon=True)
        self.thread.start()

    def _read_execution_preference(self):
        path = self.settings.data_dir / "web-preferences.json"
        try:
            if not path.is_file() or path.is_symlink() or path.stat().st_size > 1024:
                return None
            return ExecutionMode(json.loads(path.read_text(encoding="utf-8"))["executionMode"])
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def _read_retired_sessions(self):
        path = self.settings.data_dir / "retired-sessions.json"
        if not path.exists():
            return {}
        try:
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
                raise ValueError()
            values = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(values, dict) or any(
                    not isinstance(key, str) or not SESSION_ID.fullmatch(key)
                    or not isinstance(value, str) or not SESSION_ID.fullmatch(value)
                    for key, value in values.items()):
                raise ValueError()
            return values
        except (OSError, ValueError, UnicodeError):
            raise ValueError("学习会话恢复文件无法读取，请保留学习数据并检查服务端文件。") from None

    @asynccontextmanager
    async def _storage_scope(self, exclusive=False):
        async with self._condition:
            if exclusive:
                self._waiting_writers += 1
                try:
                    await self._condition.wait_for(lambda: not self._writer and self._readers == 0)
                    self._writer = True
                finally:
                    self._waiting_writers -= 1
                    self._condition.notify_all()
            else:
                await self._condition.wait_for(lambda: not self._writer and not self._waiting_writers)
                self._readers += 1
        try:
            yield
        finally:
            async with self._condition:
                if exclusive:
                    self._writer = False
                else:
                    self._readers -= 1
                self._condition.notify_all()

    def _navigation_file(self, session_id):
        path = self.settings.data_dir.absolute() / "navigation" / f"{session_id}.json"
        for component in [*reversed(path.parents), path]:
            if WebWorkspace._is_link(component):
                raise ValueError("学习导航目录不能包含符号链接或目录联接。")
        return path

    def _read_navigation(self, session_id):
        path = self._navigation_file(session_id)
        if not path.is_file():
            return None
        if path.stat().st_size > 4096:
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, UnicodeError):
            return None
        return value if isinstance(value, dict) else None

    def _write_navigation(self, session_id, value):
        path = self._navigation_file(session_id)
        if value is None:
            path.unlink(missing_ok=True)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".navigation-", dir=path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(value, stream, ensure_ascii=False)
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def _entry(self, session_id):
        if not isinstance(session_id, str) or not SESSION_ID.fullmatch(session_id):
            raise ValueError("缺少有效的学习会话。")
        if session_id in self.sessions:
            return self.sessions[session_id]
        if session_id in self._retired_sessions:
            raise SessionResetError(self._retired_sessions[session_id])
        for key, old in list(self.sessions.items()):
            if time.monotonic() - old["seen"] > 24 * 3600 and not old["lock"].locked():
                old["handler"].end_session()
                del self.sessions[key]
        if sum(not item["retired"] for item in self.sessions.values()) >= 64:
            raise ValueError("打开的学习标签页过多，请关闭闲置页面或重启服务。")
        inherited = self._pending_sessions.pop(session_id, None)
        entry = {
            "lock": asyncio.Lock(), "seen": time.monotonic(), "retired": False,
            "contextToken": uuid.uuid4().hex, "navigation": self._read_navigation(session_id),
            "notifications": deque(maxlen=1000), "cursor": 0,
            "notificationLock": threading.Lock(), "operationId": None,
            "cancelRequested": False,
            "ai": inherited if inherited is not None else self.ai_manager.new_session(),
        }
        settings = self.settings.model_copy(deep=True)
        if settings.execution_mode == ExecutionMode.AUTO and self._remembered_mode is not None:
            settings.execution_mode = self._remembered_mode
        entry["handler"] = ServerHandler(
            settings=settings, workspace_path=self.workspace.base_dir,
            write_notification=lambda item: self._notify(entry, item.method, item.params),
        )
        if self.isolated_pool is not None:
            entry["handler"].executor = IsolatedExecutor(settings, self.isolated_pool, self.account_id)
        self.ai_manager.apply_to_handler(entry["ai"], entry["handler"])
        self.sessions[session_id] = entry
        return entry

    def _notify(self, entry, method, params):
        values = dict(params)
        if entry["operationId"]:
            values.setdefault("operationId", entry["operationId"])
        if self.multi_user:
            values.setdefault("userId", self.account_id)
            identity = self._identity(entry)
            if identity:
                values.setdefault("courseId", identity["courseId"])
                values.setdefault("lessonId", identity["lessonId"])
                values.setdefault("taskId", identity["lessonId"] + ":" + identity["stepId"])
            execution = getattr(entry["handler"].executor, "execution_metadata", {})
            if execution.get("jobId"):
                values.setdefault("jobId", execution["jobId"])
        with entry["notificationLock"]:
            entry["cursor"] += 1
            entry["notifications"].append({"cursor": entry["cursor"], "method": method, "params": values})

    @staticmethod
    def _identity(entry):
        handler = entry["handler"]
        runner = handler._runner
        if not runner or not runner.state or not runner.state.current_step:
            return None
        return {
            "courseId": handler._current_course_id or runner.course_id,
            "lessonId": runner.state.lesson.id, "stepId": runner.state.current_step.id,
            "depth": handler._profile.depth.value,
        }

    def _check_identity(self, entry, params):
        identity = self._identity(entry)
        if not identity:
            return None
        if any(key in params and params[key] != value for key, value in identity.items()):
            return None
        return identity

    def _draft(self, identity):
        code = self.workspace.load(identity["courseId"], identity["lessonId"], identity["stepId"])
        revision = hashlib.sha256(code.encode("utf-8")).hexdigest() if code is not None else ""
        return {"code": code, "draftRevision": revision, "draftFound": code is not None}

    def _save_draft(self, entry, params):
        identity = self._check_identity(entry, params)
        if not identity:
            return {"saved": False, "reason": "stale_step", "code": None}
        code = params.get("code", "")
        expected = params.get("draftRevision", params.get("revision"))
        result = self.workspace.save(identity["courseId"], identity["lessonId"], identity["stepId"],
                                     code, expected_revision=expected)
        entry["handler"]._runner._save_progress(code)
        return {"saved": True, "code": code, "draftRevision": result["revision"], "draftFound": True}

    def _persist_answer(self, entry, params):
        identity = self._identity(entry)
        runner = entry["handler"]._runner
        if identity and "code" in params and runner.state.current_step.cls in {"cmd_question", "script"}:
            return self._save_draft(entry, {**params, **identity})
        return None

    def _decorate_lesson(self, session_id, entry, result):
        if not result.get("step"):
            return result
        identity = self._identity(entry)
        if not identity:
            return result
        if result.get("legacyCode") and hasattr(self.workspace, "backup_legacy"):
            result["legacyBackup"] = self.workspace.backup_legacy(
                identity["courseId"], identity["lessonId"], result["legacyCode"])
        draft = self._draft(identity)
        if not draft["draftFound"] and result.get("restoredCode"):
            self.workspace.restore_legacy(identity["courseId"], identity["lessonId"], identity["stepId"],
                                          result["restoredCode"])
            draft = self._draft(identity)
        result.update(draftFound=draft["draftFound"], draftRevision=draft["draftRevision"],
                      restoredCode=draft["code"] if draft["code"] is not None else result.get("restoredCode", ""),
                      contextToken=entry["contextToken"])
        handler = entry["handler"]
        course = handler.registry.get_course(identity["courseId"])
        entry["navigation"] = {
            **identity, "lessonIdx": course.lessons.index(identity["lessonId"]),
            "practiceMode": handler._runner.practice_mode,
        }
        self._write_navigation(session_id, entry["navigation"])
        return result

    def _validated_navigation(self, handler, value):
        if not isinstance(value, dict):
            return None
        course = handler.registry.get_course(value.get("courseId", ""))
        index = value.get("lessonIdx")
        if not course or isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(course.lessons):
            return None
        depth = value.get("depth", "beginner")
        if depth not in {"beginner", "intermediate", "advanced"}:
            return None
        step = value.get("stepId")
        if step is not None and (not isinstance(step, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", step)):
            return None
        return {"courseId": course.id, "lessonIdx": index, "depth": depth,
                "stepId": step, "practiceMode": value.get("practiceMode") is True}

    def _prepare_retirement(self):
        identifiers = {key for key, entry in self.sessions.items() if not entry["retired"]}
        directory = self.settings.data_dir / "navigation"
        if directory.is_dir():
            for path in directory.iterdir():
                if path.suffix == ".json" and SESSION_ID.fullmatch(path.stem):
                    self._navigation_file(path.stem)
                    identifiers.add(path.stem)
        replacements = {key: secrets.token_hex(16) for key in identifiers}
        remembered = {**self._retired_sessions, **replacements}
        atomic_write(self.settings.data_dir / "retired-sessions.json",
                     json.dumps(remembered).encode("utf-8"))
        self._retired_sessions = remembered
        return replacements

    def _retire_sessions(self, replacements):
        for session_id, entry in self.sessions.items():
            if entry["retired"]:
                continue
            replacement = replacements[session_id]
            self._pending_sessions[replacement] = deepcopy(entry["ai"])
            entry["retired"] = True
            entry["replacement"] = replacement
            entry["handler"].invalidate_learning_session()
            self._write_navigation(session_id, None)
            self._notify(entry, "sessionReset", {"sessionId": replacement, "reason": "learning_data_cleared"})
        # An inactive tab from an older process must not restore pre-clear navigation either.
        directory = self.settings.data_dir / "navigation"
        if directory.is_dir():
            for path in directory.iterdir():
                if path.suffix == ".json" and SESSION_ID.fullmatch(path.stem):
                    self._navigation_file(path.stem).unlink(missing_ok=True)
        return replacements

    def _backup(self, params):
        if set(params) - {"includeWorkspace"}:
            raise ValueError("网页备份只接受是否包含练习代码，不接受本机目标路径。")
        include_workspace = params.get("includeWorkspace", False)
        if not isinstance(include_workspace, bool):
            raise ValueError("请选择是否包含练习代码。")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ")
        with tempfile.TemporaryDirectory(prefix="sparktutor-backup-") as temporary:
            path = Path(temporary) / "progress.db"
            backup_learning_database(self.settings.data_dir / "progress.db", path)
            if include_workspace:
                output = io.BytesIO()
                with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                    archive.writestr("progress.db", path.read_bytes())
                    for name, content in self.workspace.iter_files():
                        archive.writestr("workspace/" + name, content)
                payload, suffix, mime = output.getvalue(), "zip", "application/zip"
            else:
                payload, suffix, mime = path.read_bytes(), "db", "application/vnd.sqlite3"
        return {"filename": f"sparktutor-backup-{stamp}.{suffix}", "contentType": mime,
                "base64": base64.b64encode(payload).decode("ascii"),
                "includesCode": True, "includesWorkspace": include_workspace, "databaseMayIncludeCode": True}

    async def dispatch(self, session_id: str, method: str, params: dict) -> dict:
        if self._closing:
            raise ValueError("学习服务正在关闭，请稍后重新打开。")
        entry = self._entry(session_id)
        entry["seen"] = time.monotonic()
        if method == "cancelExecution":
            operation = params.get("operationId")
            if set(params) - {"operationId"} or not isinstance(operation, str) or not OPERATION_ID.fullmatch(operation):
                raise ValueError("请提供当前作业的有效运行标识。")
            running = entry.get("executionTask")
            if operation != entry.get("operationId") or running is None or running.done():
                return {"cancelRequested": False, "operationId": operation}
            if not entry["cancelRequested"]:
                entry["cancelRequested"] = True
                running.cancel()
            return {"cancelRequested": True, "operationId": operation}
        if method == "pollWebNotifications":
            after = params.get("after", 0)
            if isinstance(after, bool) or not isinstance(after, int) or after < 0:
                raise ValueError("通知游标无效。")
            with entry["notificationLock"]:
                return {"cursor": entry["cursor"],
                        "notifications": [item for item in entry["notifications"] if item["cursor"] > after]}
        task = asyncio.current_task()
        self._tasks.add(task)
        try:
            async with self._storage_scope(method in {"clearLearningRecords", "resetWebLesson", "backupLearningDatabase"}):
                async with entry["lock"]:
                    if entry["retired"]:
                        raise SessionResetError(entry["replacement"])
                    token = params.get("contextToken")
                    if method in CONTEXT_METHODS and token is not None and token != entry["contextToken"]:
                        raise ValueError("题目已变化，本次操作未应用；请回到当前题重试。")
                    operation = None
                    if method in EXECUTION_METHODS:
                        operation = params.get("operationId") or uuid.uuid4().hex
                        if not isinstance(operation, str) or not OPERATION_ID.fullmatch(operation):
                            raise ValueError("运行标识无效。")
                        entry["operationId"] = operation
                        entry["cancelRequested"] = False
                        entry["executionTask"] = task
                        self._notify(entry, "operationStarted", {})
                    try:
                        execution_identity = None
                        if self.isolated_pool is not None and method in {"run", "submit", "webSubmit", "buildReviewPrompt"}:
                            target = self._identity(entry)
                            if not target:
                                raise ValueError("请先打开当前练习，再运行或提交代码。")
                            execution_identity = ExecutionIdentity(
                                self.account_id, session_id, target["courseId"], target["lessonId"],
                                target["stepId"], operation, entry["contextToken"])
                        with execution_scope(execution_identity):
                            result = await self._dispatch(session_id, entry, method, params)
                        if operation and isinstance(result, dict):
                            result["operationId"] = operation
                            runner = entry["handler"]._runner
                            execution = runner.state.last_exec if runner and runner.state else None
                            if execution and execution.execution.get("operationId") == operation:
                                result["execution"] = dict(execution.execution)
                                result.setdefault("mode", execution.mode.value)
                        return result
                    finally:
                        if operation:
                            self._notify(entry, "operationComplete", {})
                            entry["operationId"] = None
                            entry["cancelRequested"] = False
                            entry["executionTask"] = None
        finally:
            self._tasks.discard(task)

    async def _dispatch(self, session_id, entry, method, params):
        handler = entry["handler"]
        values = {key: value for key, value in params.items() if key not in {"contextToken", "operationId"}}
        if self.multi_user and method in {'configureWebSettings', 'setExecutionMode'} and "mode" in values:
            allowed_mode = "isolated" if self.isolated_pool is not None else "dry_run"
            if values["mode"] != allowed_mode:
                raise ValueError('账号执行方式由管理员配置，不能切换为共享宿主执行。')
        if self.multi_user and method == "runDemoScenario" and values.get("mode") == "spark":
            raise ValueError("账号实操请通过当前练习运行；预设诊断示例仅提供合成轨迹。")

        async def call(name, arguments=None):
            return await handler.dispatch({"method": name, "params": values if arguments is None else arguments})

        if method == "webSettings":
            return self.public_settings(entry)
        if method == "getExecutionServiceStatus":
            if self.isolated_pool is not None:
                return await self.isolated_pool.status()
            return {"enabled": False, "ready": False, "mode": handler.settings.execution_mode.value,
                    "message": "管理员尚未启用账号隔离执行服务。"}
        if method == "getCheckInStatus":
            if set(values) - {"month"}:
                raise ValueError("签到查询仅支持选择月份。")
            return self.checkins.status(values.get("month"))
        if method == "checkIn":
            if values:
                raise ValueError("签到日期由服务器确定，不支持补签或指定日期。")
            return self.checkins.check_in()
        if method == "ping":
            provider = self.public_settings(entry).get("resolvedProvider", "none")
            return {"ready": True, "aiAvailable": provider not in {None, "none"},
                    "configuredMode": handler.settings.execution_mode.value, "aiVerified": False}
        if method == "configureWebSettings":
            if "mode" in values:
                try:
                    ExecutionMode(values["mode"])
                except (ValueError, TypeError):
                    raise ValueError("执行方式无效。") from None
            self.ai_manager.configure(entry["ai"], {key: value for key, value in values.items() if key != "mode"})
            self.ai_manager.apply_to_handler(entry["ai"], handler)
            if "mode" in values:
                await call("setExecutionMode", {"mode": values["mode"]})
                if entry["ai"].get("remember"):
                    atomic_write(self.settings.data_dir / "web-preferences.json",
                                 json.dumps({"executionMode": values["mode"]}).encode("utf-8"))
                    self._remembered_mode = ExecutionMode(values["mode"])
            if values.get("remember") is False:
                preference = self.settings.data_dir / "web-preferences.json"
                if preference.is_symlink():
                    raise ValueError("执行设置文件不能是符号链接。")
                preference.unlink(missing_ok=True)
                self._remembered_mode = None
            return self.public_settings(entry)
        if method == "checkWebAiConnection":
            return await self.ai_manager.check_connection(entry["ai"], coach=values.get("coach") is True)
        if method == "getEnvironmentReport":
            result = await call(method)
            result["ai"] = self.public_settings(entry)
            if self.isolated_pool is not None:
                result["executionService"] = await self.isolated_pool.status()
                result["execution"].update(
                    configuredMode="isolated", localPrerequisites=None,
                    summary=result["executionService"]["message"],
                    nextSteps=["在当前练习运行和提交；每次作业在独立容器中验证。"])
            return result
        if method == "getLearningDashboard":
            result = await call(method)
            diagnosis = result.get("diagnosis") or {}
            dimension = next((item for item in diagnosis.get("dimensions", []) if item["key"] == "knowledge"), {})
            components = {}
            if dimension:
                catalog = {(item["courseId"], item["lessonId"], item["taskId"]): item
                           for item in build_exercise_catalog(handler.registry)}
                for item in dimension.get("evidence", {}).get("items", []):
                    specification = catalog.get((item["courseId"], item["lessonId"], item["taskId"]), {})
                    evidence = {key: item[key] for key in ("courseId", "lessonId", "taskId", "title", "passed", "assisted")
                                if key in item}
                    evidence.setdefault("title", specification.get("title") or item["taskId"])
                    for component in specification.get("knowledgeComponents", []):
                        components.setdefault(component, []).append(evidence)
            result["knowledgeComponentEvidence"] = components
            result["knowledgeEvidenceLimit"] = dimension.get("evidence", {}).get("limit", 100)
            return result
        if method == "webLastExecution":
            runner = handler._runner
            result = runner.state.last_exec if runner and runner.state else None
            return {"stdout": result.stdout, "stderr": result.stderr, "mode": result.mode.value,
                    **({"execution": dict(result.execution)} if result.execution else {})} if result else {}
        if method == "getDraft":
            identity = self._check_identity(entry, values)
            return self._draft(identity) if identity else {"code": None, "draftFound": False, "draftRevision": ""}
        if method == "saveDraft":
            return self._save_draft(entry, values)
        if method == "resumeWebLesson":
            navigation = self._validated_navigation(handler, entry["navigation"] or values)
            if not navigation:
                return {"lesson": None, "courseId": "", "lessonIdx": 0, "depth": "beginner"}
            entry["contextToken"] = uuid.uuid4().hex
            arguments = {key: navigation[key] for key in ("courseId", "lessonIdx", "depth")}
            result = await handler._load_lesson(
                arguments, target_step_id=navigation["stepId"] if navigation["practiceMode"] else None,
                resume_step_id=navigation["stepId"] if not navigation["practiceMode"] else None,
            )
            result = self._decorate_lesson(session_id, entry, result)
            return {**navigation, "lesson": result, "contextToken": entry["contextToken"]}
        if method == "backupLearningDatabase":
            return self._backup(values)
        if method == "getLearningDataOverview":
            result = await call(method)
            files = self.workspace.iter_files()
            result.update(workspaceFileCount=len(files), workspaceBytes=sum(len(content) for _, content in files))
            result["privacy"]["backup"] = "数据库可能包含历史代码及学习事件；可选择同时备份练习代码。备份不含模型配置或密钥。"
            result["privacy"]["clear"] = "清除课程进度和行为记录，保留已保存练习文件及模型配置；其他标签页的旧请求将失效。"
            return result
        if method == "clearLearningRecords":
            if values != {"confirmation": "clear-learning-records"}:
                raise ValueError("需要在学习数据管理中明确确认清除。")
            previous_retired = self._retired_sessions.copy()
            replacements = self._prepare_retirement()
            try:
                result = await call(method)
            except BaseException:
                atomic_write(self.settings.data_dir / "retired-sessions.json",
                             json.dumps(previous_retired).encode("utf-8"))
                self._retired_sessions = previous_retired
                raise
            self._retire_sessions(replacements)
            return {**result, "sessionReset": True, "newSessionId": replacements[session_id], "codePreserved": True}
        if method == "resetWebLesson":
            if values.get("confirmation") != "reset-lesson":
                raise ValueError("请明确确认重置当前课节。")
            course = handler.registry.get_course(values.get("courseId", ""))
            lesson_id = values.get("lessonId")
            if not course or lesson_id not in course.lessons:
                raise ValueError("课程或课节无效。")
            depth = values.get("depth", handler._profile.depth.value)
            if depth not in {"beginner", "intermediate", "advanced"}:
                raise ValueError("课程难度无效。")
            current = self._identity(entry)
            resetting_current = bool(current and current["courseId"] == course.id and current["lessonId"] == lesson_id)
            result = self.workspace.reset_lesson(course.id, lesson_id)
            if resetting_current:
                await call("resetLesson", {"courseId": course.id, "lessonId": lesson_id})
            else:
                handler.progress.reset_lesson(course.id, lesson_id)
            for key, other in self.sessions.items():
                nav = other.get("navigation")
                if nav and nav.get("courseId") == course.id and nav.get("lessonId") == lesson_id:
                    other["handler"]._runner = None
                    other["handler"]._coach_visit_id = uuid.uuid4().hex
                    other["contextToken"] = uuid.uuid4().hex
                    other["navigation"] = None
                    self._write_navigation(key, None)
                    if key != session_id:
                        self._notify(other, "lessonReset", {"courseId": course.id, "lessonId": lesson_id})
            if not resetting_current:
                return {"ok": True, **result, "courseId": course.id, "lessonId": lesson_id, "lesson": None}
            entry["contextToken"] = uuid.uuid4().hex
            loaded = await handler._load_lesson({"courseId": course.id,
                                                "lessonIdx": course.lessons.index(lesson_id), "depth": depth})
            return {"ok": True, **result, "courseId": course.id, "lessonId": lesson_id,
                    "lesson": self._decorate_lesson(session_id, entry, loaded)}
        if method == "resetLesson":
            raise ValueError("请从网页的课节重置入口确认操作。")
        if method in {"run", "submit", "webSubmit", "advance", "goBack", "webChat", "chat", "webCoach"}:
            self._persist_answer(entry, values)
        if method in {"loadLesson", "openRecommendedExercise", "openCoachExercise", "advance", "goBack"}:
            result = await call(method)
            entry["contextToken"] = uuid.uuid4().hex
            if result.get("finished"):
                entry["navigation"] = None
                self._write_navigation(session_id, None)
                return {**result, "contextToken": entry["contextToken"]}
            return self._decorate_lesson(session_id, entry, result)
        if method in {"webChat", "chat"}:
            prompt = await call("buildChatPrompt")
            return {"answer": await self.complete(entry["ai"], prompt["messages"])}
        if method in {"webSubmit", "submit"}:
            review = await call("buildReviewPrompt")
            if not review["needsAiReview"]:
                return review["localResult"]
            try:
                raw = await self.complete(entry["ai"], review["messages"])
            except ValueError as error:
                return await call("completeReviewFailure", {"reviewId": review["reviewId"], "message": str(error)})
            return await call("parseReviewResponse", {"reviewId": review["reviewId"], "rawText": raw})
        if method == "webCoach":
            if self.ai_manager.resolve(entry["ai"], coach=True) == "none":
                raise ValueError("请先在设置中配置调试教练的兼容接口与模型。")
            prompt = await call("buildCoachPrompt")
            try:
                raw = await self.ai_manager.complete(entry["ai"], prompt["messages"], coach=True)
                return await call("parseCoachResponse", {"requestId": prompt["requestId"], "rawText": raw, "code": values["code"]})
            except Exception:
                await call("completeCoachFailure", {"requestId": prompt["requestId"]})
                raise
        return await call(method)

    def public_settings(self, entry):
        return {**self.ai_manager.public(entry["ai"]), "mode": entry["handler"].settings.execution_mode.value,
                "accountExecutionManaged": self.multi_user,
                "isolatedExecutionEnabled": self.isolated_pool is not None}

    async def complete(self, ai, messages):
        return await self.ai_manager.complete(ai, messages)

    def close(self):
        if self._closing:
            return
        self._closing = True

        async def finish():
            tasks = list(self._tasks)
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            for entry in self.sessions.values():
                entry["handler"].end_session()

        try:
            asyncio.run_coroutine_threadsafe(finish(), self.loop).result(timeout=15)
        finally:
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join(timeout=10)
            self.loop.close()
