"""Exercise browser parity contracts using isolated local storage.

The gated execution double below verifies transport ordering and notification
delivery only. It is deliberately not a substitute for real Spark validation.
"""

import asyncio
import base64
import io
import json
from pathlib import Path
import sqlite3
import zipfile

import pytest

from sparktutor.config.settings import ExecutionMode, Settings
from sparktutor.courses.registry import CourseRegistry
from sparktutor.engine.executor import ExecMode, ExecResult
from sparktutor.engine.lesson_loader import load_lesson
from sparktutor.state.progress import ProgressStore
from sparktutor.web.runtime import SessionResetError, WebRuntime
from sparktutor.web import runtime as runtime_module
from sparktutor.state import data_management


A, B = "a" * 32, "b" * 32


@pytest.fixture
def runtimes(tmp_path):
    settings = Settings(data_dir=tmp_path / "learning", execution_mode=ExecutionMode.DRY_RUN)
    opened = []

    def create(*, mode=ExecutionMode.DRY_RUN):
        configured = settings.model_copy(deep=True)
        configured.execution_mode = mode
        instance = WebRuntime(configured)
        opened.append(instance)
        return instance

    yield create
    for instance in reversed(opened):
        instance.close()


def pending(runtime, method, params=None, session=A):
    return asyncio.run_coroutine_threadsafe(runtime.dispatch(session, method, params or {}), runtime.loop)


def call(runtime, method, params=None, session=A):
    return pending(runtime, method, params, session).result(timeout=10)


def on_loop(runtime, coroutine):
    return asyncio.run_coroutine_threadsafe(coroutine, runtime.loop).result(timeout=10)


def open_code_step(runtime, step_id="8", *, course_id="learning_spark",
                   lesson_id="01_getting_started", session=A):
    registry = CourseRegistry()
    course = registry.get_course(course_id)
    lesson = load_lesson(registry.courses_dir / course_id / "lessons" / lesson_id)
    steps = [step for step in lesson.steps_for_depth("beginner") if step.cls != "meta"]
    index = next(index for index, step in enumerate(steps) if step.id == step_id)
    ProgressStore(runtime.settings.data_dir / "progress.db").save(
        course_id, lesson_id, index, len(steps), depth="beginner", current_step_id=step_id,
    )
    return call(runtime, "loadLesson", {"courseId": course_id,
                                       "lessonIdx": course.lessons.index(lesson_id), "depth": "beginner"}, session)


def test_each_task_draft_survives_back_navigation_and_server_restart(runtimes):
    runtime = runtimes()
    loaded = open_code_step(runtime)
    answer = loaded["step"]["correctAnswer"] + "\n# my persistent answer\n"
    submitted = call(runtime, "webSubmit", {"code": answer, "contextToken": loaded["contextToken"]})
    assert submitted["passed"]
    advanced = call(runtime, "advance", {"code": answer, "contextToken": loaded["contextToken"]})
    assert advanced["step"]["id"] != "8"
    previous = call(runtime, "goBack", {"contextToken": advanced["contextToken"]})
    assert previous["step"]["id"] == "8"
    assert previous["draftFound"] and previous["restoredCode"] == answer
    runtime.close()

    reopened = runtimes()
    restored = call(reopened, "resumeWebLesson")
    assert restored["lesson"]["step"]["id"] == "8"
    assert restored["lesson"]["restoredCode"] == answer
    assert restored["lesson"]["draftFound"]


def test_intentionally_empty_draft_does_not_resurrect_starter_or_old_code(runtimes):
    runtime = runtimes()
    loaded = open_code_step(runtime)
    first = call(runtime, "saveDraft", {"stepId": "8", "code": "old code", "contextToken": loaded["contextToken"]})
    call(runtime, "saveDraft", {"stepId": "8", "code": "", "draftRevision": first["draftRevision"],
                                 "contextToken": loaded["contextToken"]})
    runtime.close()
    reopened = runtimes()
    result = call(reopened, "resumeWebLesson")["lesson"]
    assert result["draftFound"] is True and result["restoredCode"] == ""
    assert call(reopened, "getDraft")["code"] == ""


def test_normal_tabs_restore_their_own_positions_without_rewriting_shared_progress(runtimes):
    runtime = runtimes()
    first = call(runtime, "loadLesson", {"courseId": "learning_spark", "lessonIdx": 0})
    first = call(runtime, "advance", {"contextToken": first["contextToken"]})
    second = call(runtime, "loadLesson", {"courseId": "learning_spark", "lessonIdx": 0}, session=B)
    second = call(runtime, "advance", {"contextToken": second["contextToken"]}, session=B)
    assert first["step"]["id"] != second["step"]["id"]
    store = ProgressStore(runtime.settings.data_dir / "progress.db")
    before = store.get("learning_spark", "01_getting_started")
    runtime.close()

    reopened = runtimes()
    one = call(reopened, "resumeWebLesson")["lesson"]
    two = call(reopened, "resumeWebLesson", session=B)["lesson"]
    assert one["step"]["id"] == first["step"]["id"] and one["practiceMode"] is False
    assert two["step"]["id"] == second["step"]["id"] and two["practiceMode"] is False
    assert store.get("learning_spark", "01_getting_started") == before


def test_single_exercise_navigation_survives_restart_without_overwriting_sequence(runtimes):
    runtime = runtimes()
    loaded = call(runtime, "loadLesson", {"courseId": "learning_spark", "lessonIdx": 0})
    call(runtime, "advance", {"contextToken": loaded["contextToken"]})
    store = ProgressStore(runtime.settings.data_dir / "progress.db")
    sequential = store.get("learning_spark", "01_getting_started")
    practice = call(runtime, "openCoachExercise", {
        "courseId": "learning_spark", "lessonId": "01_getting_started", "stepId": "15",
    })
    assert practice["practiceMode"] and practice["step"]["id"] == "15"
    call(runtime, "saveDraft", {"stepId": "15", "code": "# focused exercise", "contextToken": practice["contextToken"]})
    assert store.get("learning_spark", "01_getting_started") == sequential
    runtime.close()

    reopened = runtimes()
    result = call(reopened, "resumeWebLesson")["lesson"]
    assert result["practiceMode"] and result["step"]["id"] == "15"
    assert result["restoredCode"] == "# focused exercise"
    assert store.get("learning_spark", "01_getting_started") == sequential


def test_changed_context_rejects_late_saves_before_and_after_lesson_reset(runtimes):
    runtime = runtimes()
    loaded = call(runtime, "loadLesson", {"courseId": "learning_spark", "lessonIdx": 0})
    current = call(runtime, "advance", {"contextToken": loaded["contextToken"]})
    with pytest.raises(ValueError, match="题目已变化"):
        call(runtime, "saveDraft", {"code": "late old step", "stepId": loaded["step"]["id"],
                                     "contextToken": loaded["contextToken"]})
    call(runtime, "resetWebLesson", {"courseId": "learning_spark", "lessonId": "01_getting_started",
                                     "confirmation": "reset-lesson"})
    with pytest.raises(ValueError, match="题目已变化"):
        call(runtime, "saveDraft", {"code": "late reset buffer", "stepId": current["step"]["id"],
                                     "contextToken": current["contextToken"]})
    assert runtime.workspace.iter_files() == []


async def gated_execution(runtime, session=A):
    entry = runtime.sessions[session]
    started, release = asyncio.Event(), asyncio.Event()

    async def execute(code, on_output=None):
        if on_output:
            on_output("first output")
        started.set()
        await release.wait()
        if on_output:
            on_output("last output")
        return ExecResult(ExecMode.LOCAL, 0, "first output\nlast output\n", "")

    entry["handler"].executor.execute = execute
    return started, release


async def release_execution(event):
    event.set()


def test_output_can_be_polled_during_run_and_operations_are_distinguished(runtimes):
    runtime = runtimes()
    loaded = open_code_step(runtime)
    started, release = on_loop(runtime, gated_execution(runtime))
    running = pending(runtime, "run", {"code": "print('transport test')", "operationId": "first-run",
                                        "contextToken": loaded["contextToken"]})
    on_loop(runtime, asyncio.wait_for(started.wait(), timeout=2))
    polled = call(runtime, "pollWebNotifications")
    assert not running.done(), "Polling must not wait for the running lesson lock"
    assert any(item["method"] == "output" and item["params"]["line"] == "first output"
               and item["params"]["operationId"] == "first-run" for item in polled["notifications"])
    on_loop(runtime, release_execution(release))
    assert running.result(timeout=10)["operationId"] == "first-run"
    call(runtime, "run", {"code": "print('second')", "operationId": "second-run", "contextToken": loaded["contextToken"]})
    subsequent = call(runtime, "pollWebNotifications", {"after": polled["cursor"]})
    assert all(item["cursor"] > polled["cursor"] for item in subsequent["notifications"])
    assert any(item["method"] == "operationComplete" and item["params"]["operationId"] == "first-run"
               for item in subsequent["notifications"])
    assert any(item["method"] == "output" and item["params"]["operationId"] == "second-run"
               for item in subsequent["notifications"])


def test_backup_download_is_consistent_and_includes_only_database_and_managed_code(runtimes, tmp_path):
    runtime = runtimes()
    loaded = open_code_step(runtime)
    call(runtime, "saveDraft", {"code": "# saved code", "stepId": "8", "contextToken": loaded["contextToken"]})
    runtime.workspace.backup_legacy("learning_spark", "01_getting_started", "# unbound old code")
    (runtime.settings.data_dir / "ai-settings.json").write_text(json.dumps({"apiKey": "PRIVATE_MODEL_SECRET"}))
    (runtime.settings.data_dir / "workspace" / "notes.py").write_text("UNMANAGED_PRIVATE_NOTE")
    database = runtime.settings.data_dir / "progress.db"
    source = sqlite3.connect(database)
    try:
        source.execute("PRAGMA journal_mode=WAL")
        source.execute("UPDATE progress SET last_code='LATEST_STORED_CODE'")
        source.commit()
        plain = call(runtime, "backupLearningDatabase")
        restored = tmp_path / "downloaded.db"
        restored.write_bytes(base64.b64decode(plain["base64"]))
        with sqlite3.connect(restored) as connection:
            assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert connection.execute("SELECT last_code FROM progress").fetchone()[0] == "LATEST_STORED_CODE"
        zipped = call(runtime, "backupLearningDatabase", {"includeWorkspace": True})
    finally:
        source.close()
    with zipfile.ZipFile(io.BytesIO(base64.b64decode(zipped["base64"]))) as archive:
        names = archive.namelist()
        assert "progress.db" in names
        assert "workspace/learning_spark/01_getting_started/exercise_8.py" in names
        assert any("/legacy_restored_" in name for name in names)
        assert len(names) == 3 and all("ai-settings" not in name and "notes.py" not in name for name in names)
        assert all(b"PRIVATE_MODEL_SECRET" not in archive.read(name) for name in names)
    destination = tmp_path / "browser-chosen-server-path.db"
    with pytest.raises(ValueError, match="不接受本机目标路径"):
        call(runtime, "backupLearningDatabase", {"destination": str(destination)})
    assert not destination.exists()


async def wait_for_clear_writer(runtime):
    while runtime._waiting_writers == 0:
        await asyncio.sleep(0)


def test_clear_waits_for_run_then_retires_all_old_handlers_and_keeps_saved_code(runtimes):
    runtime = runtimes()
    loaded = open_code_step(runtime)
    call(runtime, "loadLesson", {"courseId": "learning_spark", "lessonIdx": 1}, session=B)
    old_handlers = [entry["handler"] for entry in runtime.sessions.values()]
    call(runtime, "saveDraft", {"stepId": "8", "code": "# keep saved work", "contextToken": loaded["contextToken"]})
    started, release = on_loop(runtime, gated_execution(runtime))
    running = pending(runtime, "run", {"code": "# keep saved work", "contextToken": loaded["contextToken"]})
    on_loop(runtime, asyncio.wait_for(started.wait(), timeout=2))
    clearing = pending(runtime, "clearLearningRecords", {"confirmation": "clear-learning-records"}, session=B)
    on_loop(runtime, asyncio.wait_for(wait_for_clear_writer(runtime), timeout=2))
    assert not clearing.done(), "Clear must wait until in-flight execution has persisted its final events"
    late_save = pending(runtime, "saveDraft", {"stepId": "8", "code": "late stale code", "contextToken": loaded["contextToken"]})
    on_loop(runtime, release_execution(release))
    running.result(timeout=10)
    assert clearing.result(timeout=10)["codePreserved"] is True
    with pytest.raises(SessionResetError):
        late_save.result(timeout=10)
    for handler in old_handlers:
        assert handler.end_session()["reason"] == "learning_data_cleared"
    assert any(item["method"] == "sessionReset" for item in call(runtime, "pollWebNotifications")["notifications"])
    assert runtime.workspace.load("learning_spark", "01_getting_started", "8") == "# keep saved work"
    runtime.close()
    with sqlite3.connect(runtime.settings.data_dir / "progress.db") as connection:
        assert connection.execute("SELECT COUNT(*) FROM progress").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM learning_events").fetchone()[0] == 0


def test_reset_removes_only_the_target_lessons_managed_drafts(runtimes):
    runtime = runtimes()
    open_code_step(runtime)
    runtime.workspace.save("learning_spark", "01_getting_started", "8", "reset this")
    runtime.workspace.save("learning_spark", "02_dataframes_schemas", "other", "keep other lesson")
    legacy = runtime.workspace.backup_legacy("learning_spark", "01_getting_started", "legacy auxiliary code")
    notes = runtime.workspace.get_path("learning_spark", "01_getting_started", "8").parent / "notes.py"
    notes.write_text("keep notes")
    result = call(runtime, "resetWebLesson", {"courseId": "learning_spark", "lessonId": "01_getting_started",
                                             "confirmation": "reset-lesson"})
    assert result["deletedFiles"] == 1
    assert runtime.workspace.load("learning_spark", "01_getting_started", "8") is None
    assert runtime.workspace.load("learning_spark", "02_dataframes_schemas", "other") == "keep other lesson"
    assert Path(legacy["path"]).read_text() == "legacy auxiliary code"
    assert notes.read_text() == "keep notes"


def test_reset_of_another_lesson_preserves_the_active_lesson_state(runtimes):
    runtime = runtimes()
    current = open_code_step(runtime)
    call(runtime, "resetWebLesson", {"courseId": "learning_spark", "lessonId": "02_dataframes_schemas",
                                     "confirmation": "reset-lesson"})
    assert call(runtime, "getStep")["step"]["id"] == current["step"]["id"]


@pytest.mark.parametrize("provider", ["auto", "copilot"])
def test_browser_ping_is_a_read_only_summary_and_never_calls_legacy_model_probe(runtimes, monkeypatch, provider):
    runtime = runtimes()
    call(runtime, "webSettings")
    handler = runtime.sessions[A]["handler"]
    runtime.sessions[A]["ai"]["provider"] = provider
    probes = []

    async def private_model_probe():
        probes.append(True)
        return True, "PRIVATE_PROVIDER_RESPONSE_AND_CREDENTIAL_MARKER"

    monkeypatch.setattr(handler.evaluator, "ping", private_model_probe)
    before = handler.events.count_events()
    result = call(runtime, "ping")
    assert result["ready"] is True and result["aiVerified"] is False
    assert result["configuredMode"] == "dry_run"
    assert type(result["aiAvailable"]) is bool
    if provider == "copilot":
        assert result["aiAvailable"] is False
    assert not probes and "PRIVATE_" not in json.dumps(result)
    assert handler.events.count_events() == before


@pytest.mark.parametrize("new_mode", ["dry_run", None])
def test_disabling_remember_removes_execution_preference_for_new_tabs_and_restarts(runtimes, monkeypatch, new_mode):
    runtime = runtimes(mode=ExecutionMode.AUTO)
    # Persistence of an execution preference must not depend on a particular
    # operating system's credential vault or save real environment credentials.
    monkeypatch.setattr(runtime.ai_manager.secret_store, "save", lambda _values: False)
    remembered = call(runtime, "configureWebSettings", {
        "provider": "auto", "baseUrl": "", "model": "", "remember": True, "mode": "local",
        "clearKey": True, "clearAnthropicKey": True, "clearExtraHeaders": True,
    })
    preference = runtime.settings.data_dir / "web-preferences.json"
    assert remembered["mode"] == "local"
    assert json.loads(preference.read_text(encoding="utf-8")) == {"executionMode": "local"}
    assert call(runtime, "webSettings", session=B)["mode"] == "local"
    values = {"remember": False}
    if new_mode is not None:
        values["mode"] = new_mode
    changed = call(runtime, "configureWebSettings", values)
    assert changed["mode"] == (new_mode or "local"), "Current tab keeps its chosen execution mode"
    assert not preference.exists()
    assert call(runtime, "webSettings")["mode"] == (new_mode or "local")
    assert call(runtime, "webSettings", session="c" * 32)["mode"] == "auto"
    runtime.close()

    restarted = runtimes(mode=ExecutionMode.AUTO)
    assert call(restarted, "webSettings", session="d" * 32)["mode"] == "auto"
    assert not preference.exists()


def _learning_counts(data_dir):
    with sqlite3.connect(data_dir / "progress.db") as connection:
        return tuple(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                     for table in ("progress", "learning_events"))


def test_clear_persistently_retires_previous_process_tabs_before_old_navigation_can_resume(runtimes):
    first = runtimes()
    loaded = open_code_step(first, session=B)
    call(first, "saveDraft", {"stepId": "8", "code": "# saved before clear",
                               "contextToken": loaded["contextToken"]}, session=B)
    old_browser_navigation = first.sessions[B]["navigation"].copy()
    first.close()

    second = runtimes()
    cleared = call(second, "clearLearningRecords", {"confirmation": "clear-learning-records"})
    assert cleared["cleared"] is True
    assert _learning_counts(second.settings.data_dir) == (0, 0)
    retired = json.loads((second.settings.data_dir / "retired-sessions.json").read_text(encoding="utf-8"))
    assert B in retired and retired[B] != B
    with pytest.raises(SessionResetError) as rejected:
        call(second, "resumeWebLesson", old_browser_navigation, session=B)
    assert rejected.value.session_id == retired[B]
    assert B not in second.sessions, "An old ID must be rejected before creating a handler or session_start record"
    assert _learning_counts(second.settings.data_dir) == (0, 0)
    second.close()

    third = runtimes()
    with pytest.raises(SessionResetError) as after_restart:
        call(third, "resumeWebLesson", old_browser_navigation, session=B)
    assert after_restart.value.session_id == retired[B]
    with pytest.raises(SessionResetError):
        call(third, "saveDraft", {"stepId": "8", "code": "late browser draft",
                                   "contextToken": loaded["contextToken"]}, session=B)
    assert _learning_counts(third.settings.data_dir) == (0, 0)
    assert third.workspace.load("learning_spark", "01_getting_started", "8") == "# saved before clear"
    third.close()
    assert _learning_counts(third.settings.data_dir) == (0, 0)


def test_failed_clear_rolls_back_retirement_and_leaves_old_sessions_usable_after_restart(runtimes, monkeypatch):
    runtime = runtimes()
    loaded = open_code_step(runtime)
    before = _learning_counts(runtime.settings.data_dir)
    previous = runtime._retired_sessions.copy()
    retirement_path = runtime.settings.data_dir / "retired-sessions.json"
    prepared = []

    def failing_clear(_database):
        pending_retirement = json.loads(retirement_path.read_text(encoding="utf-8"))
        assert A in pending_retirement, "Retirement must be durable before database deletion starts"
        prepared.append(pending_retirement)
        raise sqlite3.OperationalError("simulated clear failure")

    with monkeypatch.context() as patch:
        patch.setattr(data_management, "clear_learning_records", failing_clear)
        with pytest.raises(sqlite3.OperationalError, match="simulated clear failure"):
            call(runtime, "clearLearningRecords", {"confirmation": "clear-learning-records"})
    assert prepared
    assert runtime._retired_sessions == previous
    assert json.loads(retirement_path.read_text(encoding="utf-8")) == previous
    assert runtime.sessions[A]["retired"] is False
    assert _learning_counts(runtime.settings.data_dir) == before
    assert call(runtime, "getStep")["step"]["id"] == loaded["step"]["id"]
    assert call(runtime, "saveDraft", {"stepId": "8", "code": "# still editable",
                                       "contextToken": loaded["contextToken"]})["saved"]
    runtime.close()

    reopened = runtimes()
    restored = call(reopened, "resumeWebLesson")["lesson"]
    assert restored["step"]["id"] == "8" and restored["restoredCode"] == "# still editable"


def test_first_spark_session_question_is_graded_locally_without_a_model(runtimes, monkeypatch):
    runtime = runtimes()
    loaded = open_code_step(runtime)

    async def forbid_model(*_args, **_kwargs):
        raise AssertionError("The introductory SparkSession contract must not call AI")

    monkeypatch.setattr(runtime, "complete", forbid_model)
    reported = 'SparkSession.builder.appName("MnCount").master("local[*]").getOrCreate()'
    rejected = call(runtime, "webSubmit", {"code": reported, "contextToken": loaded["contextToken"]})
    assert not rejected["passed"] and rejected["failureKind"] == "learner"
    assert rejected["assessmentSource"] == "structural"
    assert any("赋给变量 spark" in item["message"] for item in rejected["feedback"])

    reordered = "from pyspark.sql import SparkSession\nspark = SparkSession.builder.master('local[*]').appName('MnMCount').getOrCreate()"
    wrong_name = call(runtime, "webSubmit", {"code": reordered.replace("MnMCount", "MnCount")})
    assert not wrong_name["passed"]
    assert any("MnMCount" in item["message"] for item in wrong_name["feedback"])
    accepted = call(runtime, "webSubmit", {"code": reordered})
    assert accepted["passed"] and accepted["assessmentSource"] == "structural"
    assert "不代表 Spark 已实际运行" in accepted["encouragement"]
    assert runtime.workspace.load("learning_spark", "01_getting_started", "8") == reordered
    assert call(runtime, "advance")["step"]["id"] != "8"


def test_retirement_file_failure_aborts_clear_before_deleting_any_learning_records(runtimes, monkeypatch):
    runtime = runtimes()
    loaded = open_code_step(runtime)
    before = _learning_counts(runtime.settings.data_dir)
    original_write = runtime_module.atomic_write

    def refuse_retirement(path, content):
        if Path(path).name == "retired-sessions.json":
            raise OSError("simulated retirement persistence failure")
        return original_write(path, content)

    with monkeypatch.context() as patch:
        patch.setattr(runtime_module, "atomic_write", refuse_retirement)
        with pytest.raises(OSError, match="retirement persistence failure"):
            call(runtime, "clearLearningRecords", {"confirmation": "clear-learning-records"})
    assert _learning_counts(runtime.settings.data_dir) == before
    assert runtime._retired_sessions == {}
    assert runtime.sessions[A]["retired"] is False
    assert call(runtime, "getStep")["step"]["id"] == loaded["step"]["id"]
