"""Persistent browser drafts are scoped to stable task IDs and managed files."""

from pathlib import Path
import hashlib

import pytest

from sparktutor.config.settings import ExecutionMode, Settings
from sparktutor.server.handler import ServerHandler
from sparktutor.web import workspace as workspace_module
from sparktutor.web.workspace import WebWorkspace


def test_missing_draft_reads_do_not_create_storage(tmp_path):
    base = tmp_path / "not_created" / "workspace"
    workspace = WebWorkspace(base)
    assert workspace.load("course", "lesson", "step") is None
    assert workspace.iter_files() == []
    assert workspace.reset_lesson("course", "lesson") == {"deletedFiles": 0}
    assert not base.exists()


def test_drafts_persist_per_task_and_preserve_an_intentional_empty_answer(tmp_path):
    workspace = WebWorkspace(tmp_path / "workspace")
    workspace.save("course", "lesson", "first", "print('中文')\n")
    workspace.save("course", "lesson", "second", "")
    reopened = WebWorkspace(workspace.base_dir)
    assert reopened.load("course", "lesson", "first") == "print('中文')\n"
    assert reopened.load("course", "lesson", "second") == ""
    assert reopened.load("course", "other_lesson", "first") is None
    assert reopened.iter_files() == [
        ("course/lesson/exercise_first.py", "print('中文')\n".encode()),
        ("course/lesson/exercise_second.py", b""),
    ]


@pytest.mark.parametrize("identifier", ["../escape", "a/b", "a\\b", "", ".", "..", "x:y", None, 123])
def test_invalid_identifiers_cannot_address_files(tmp_path, identifier):
    workspace = WebWorkspace(tmp_path / "workspace")
    for values in [(identifier, "lesson", "step"), ("course", identifier, "step"),
                   ("course", "lesson", identifier)]:
        with pytest.raises(ValueError, match="标识无效"):
            workspace.save(*values, "code")
    assert not workspace.base_dir.exists()


def test_old_editor_revision_cannot_overwrite_newer_work(tmp_path):
    workspace = WebWorkspace(tmp_path / "workspace")
    first = workspace.save("course", "lesson", "step", "first", expected_revision="")
    second = workspace.save("course", "lesson", "step", "newer", expected_revision=first["revision"])
    with pytest.raises(ValueError, match="其他页面更新"):
        workspace.save("course", "lesson", "step", "stale", expected_revision=first["revision"])
    assert workspace.load("course", "lesson", "step") == "newer"
    assert workspace.save("course", "lesson", "step", "newer")["revision"] == second["revision"]


def test_legacy_restore_never_replaces_an_existing_draft_even_when_empty(tmp_path):
    workspace = WebWorkspace(tmp_path / "workspace")
    assert workspace.restore_legacy("course", "lesson", "step", "legacy")["saved"]
    assert not workspace.restore_legacy("course", "lesson", "step", "older")["saved"]
    workspace.save("course", "lesson", "empty", "")
    assert not workspace.restore_legacy("course", "lesson", "empty", "do not resurrect")["saved"]
    assert workspace.load("course", "lesson", "empty") == ""
    assert not workspace.restore_legacy("course", "lesson", "blank", "  \n")["saved"]
    assert workspace.load("course", "lesson", "blank") is None


def test_failed_atomic_replace_preserves_old_file_and_removes_temporary_file(tmp_path, monkeypatch):
    workspace = WebWorkspace(tmp_path / "workspace")
    workspace.save("course", "lesson", "step", "saved")

    def interrupted(*args):
        raise OSError("simulated replacement failure")

    monkeypatch.setattr(workspace_module.os, "replace", interrupted)
    with pytest.raises(OSError, match="replacement failure"):
        workspace.save("course", "lesson", "step", "unsaved")
    assert workspace.load("course", "lesson", "step") == "saved"
    assert [path.name for path in workspace.get_path("course", "lesson", "step").parent.iterdir()] == ["exercise_step.py"]


def test_code_and_snapshot_limits_do_not_write_or_return_partial_data(tmp_path, monkeypatch):
    workspace = WebWorkspace(tmp_path / "workspace")
    monkeypatch.setattr(workspace_module, "MAX_CODE_BYTES", 8)
    with pytest.raises(ValueError, match="超过允许"):
        workspace.save("course", "lesson", "step", "012345678")
    assert not workspace.base_dir.exists()
    workspace.save("course", "lesson", "one", "1234")
    workspace.save("course", "lesson", "two", "5678")
    monkeypatch.setattr(workspace_module, "MAX_WORKSPACE_BYTES", 7)
    with pytest.raises(ValueError, match="单次备份上限"):
        workspace.iter_files()
    assert workspace.load("course", "lesson", "one") == "1234"
    assert workspace.load("course", "lesson", "two") == "5678"


def test_reset_only_removes_this_lessons_managed_exercises(tmp_path):
    workspace = WebWorkspace(tmp_path / "workspace")
    workspace.save("course", "lesson", "one", "one")
    workspace.save("course", "lesson", "two", "two")
    workspace.save("course", "other", "one", "other lesson")
    supplementary = workspace.get_path("course", "lesson", "one").parent / "notes.py"
    supplementary.write_text("keep me")
    assert workspace.reset_lesson("course", "lesson") == {"deletedFiles": 2}
    assert supplementary.read_text() == "keep me"
    assert workspace.load("course", "other", "one") == "other lesson"
    assert workspace.iter_files() == [("course/other/exercise_one.py", b"other lesson")]


def test_unbound_legacy_backup_is_idempotent_exported_and_preserved_by_reset(tmp_path):
    workspace = WebWorkspace(tmp_path / "workspace")
    code = "print('旧代码')\n"
    digest = hashlib.sha256(code.encode()).hexdigest()
    first = workspace.backup_legacy("course", "lesson", code)
    path = Path(first["path"])
    assert path.name == f"legacy_restored_{digest}.py"
    assert path.read_text(encoding="utf-8") == code
    assert not workspace.backup_legacy("course", "lesson", code)["saved"]
    workspace.save("course", "lesson", "step", "current")
    assert workspace.reset_lesson("course", "lesson") == {"deletedFiles": 1}
    assert workspace.iter_files() == [(f"course/lesson/legacy_restored_{digest}.py", code.encode())]
    assert not workspace.backup_legacy("course", "lesson", "  \n")["saved"]


def test_changed_legacy_backup_is_not_overwritten(tmp_path):
    workspace = WebWorkspace(tmp_path / "workspace")
    code = "original code\n"
    first = Path(workspace.backup_legacy("course", "lesson", code)["path"])
    first.write_text("learner edits\n", encoding="utf-8")
    second = Path(workspace.backup_legacy("course", "lesson", code)["path"])
    assert second != first and second.stem == first.stem + "_1"
    assert first.read_text() == "learner edits\n"
    assert second.read_text() == code
    assert len(workspace.iter_files()) == 2
    assert not workspace.backup_legacy("course", "lesson", code)["saved"]


def test_legacy_backup_does_not_follow_a_controlled_filename_link(tmp_path):
    workspace = WebWorkspace(tmp_path / "workspace")
    code = "legacy code"
    outside = tmp_path / "outside.py"
    outside.write_text("preserve")
    directory = workspace.base_dir / "course" / "lesson"
    directory.mkdir(parents=True)
    backup = directory / f"legacy_restored_{hashlib.sha256(code.encode()).hexdigest()}.py"
    try:
        backup.symlink_to(outside)
    except OSError:
        pytest.skip("Symbolic links are unavailable for this Windows account")
    with pytest.raises(ValueError, match="符号链接"):
        workspace.backup_legacy("course", "lesson", code)
    with pytest.raises(ValueError, match="符号链接"):
        workspace.iter_files()
    assert outside.read_text() == "preserve"


@pytest.mark.parametrize("link_kind", ["course", "draft", "root"])
def test_links_cannot_escape_managed_workspace(tmp_path, link_kind):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "preserved.py").write_text("preserve")
    base = tmp_path / "workspace"
    base.mkdir()
    try:
        if link_kind == "root":
            linked_base = tmp_path / "linked-root"
            linked_base.symlink_to(outside, target_is_directory=True)
            with pytest.raises(ValueError, match="符号链接"):
                WebWorkspace(linked_base)
            return
        if link_kind == "course":
            (base / "course").symlink_to(outside, target_is_directory=True)
        else:
            directory = base / "course" / "lesson"
            directory.mkdir(parents=True)
            (directory / "exercise_step.py").symlink_to(outside / "preserved.py")
    except OSError:
        pytest.skip("Symbolic links are unavailable for this Windows account")
    workspace = WebWorkspace(base)
    for operation in [lambda: workspace.save("course", "lesson", "step", "overwrite"),
                      lambda: workspace.load("course", "lesson", "step"),
                      lambda: workspace.reset_lesson("course", "lesson"), workspace.iter_files]:
        with pytest.raises(ValueError, match="符号链接"):
            operation()
    assert (outside / "preserved.py").read_text() == "preserve"


@pytest.mark.asyncio
async def test_per_tab_normal_resume_keeps_its_position_without_importing_another_steps_code(tmp_path):
    handler = ServerHandler(Settings(data_dir=tmp_path, execution_mode=ExecutionMode.DRY_RUN),
                            workspace_path=tmp_path / "workspace")
    loaded = await handler.dispatch({"method": "loadLesson", "params": {
        "courseId": "learning_spark", "lessonIdx": 0,
    }})
    lesson = handler._runner.state.lesson
    current = handler._runner.state.current_step.id
    handler.progress.save("learning_spark", lesson.id, current_step=0, total_steps=100,
                          current_step_id=current, last_code="different-step-code", depth="beginner")
    restored = await handler._load_lesson({"courseId": "learning_spark", "lessonIdx": 0},
                                          resume_step_id="practice-filter")
    assert restored["step"]["id"] == "practice-filter"
    assert restored["practiceMode"] is False
    assert restored["restoredCode"] == ""
    report = await handler.dispatch({"method": "getLearningDataOverview"})
    assert report["workspacePath"] == str((tmp_path / "workspace").resolve())


@pytest.mark.asyncio
async def test_practice_target_takes_priority_over_normal_resume_and_invalidation_blocks_callbacks(tmp_path):
    handler = ServerHandler(Settings(data_dir=tmp_path, execution_mode=ExecutionMode.DRY_RUN))
    restored = await handler._load_lesson({"courseId": "learning_spark", "lessonIdx": 0},
                                          target_step_id="practice-aggregate", resume_step_id="practice-filter")
    assert restored["step"]["id"] == "practice-aggregate"
    assert restored["practiceMode"] is True
    before = handler.events.count_events()
    handler.invalidate_learning_session()
    assert handler.end_session()["reason"] == "learning_data_cleared"
    with pytest.raises(ValueError, match="重载学习页面"):
        await handler.dispatch({"method": "recordLearningEvent", "params": {
            "eventType": "session_end", "data": {"reason": "extension_deactivated"},
        }})
    assert handler.events.count_events() == before
