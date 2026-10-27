"""Course-owned code files for the local browser application.

Only validated course/lesson/step identifiers can address a draft. No API takes
an arbitrary filesystem path. Saves are atomic and a workspace snapshot never
follows a symbolic link or Windows junction outside the managed directory.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import stat
import tempfile
import threading


_ID = re.compile(r"[A-Za-z0-9_-]{1,160}\Z")
_DRAFT = re.compile(r"exercise_([A-Za-z0-9_-]{1,160})\.py\Z")
_LEGACY = re.compile(r"legacy_restored_[0-9a-f]{64}(?:_[0-9]+)?\.py\Z")
MAX_CODE_BYTES = 2 * 1024 * 1024
MAX_WORKSPACE_BYTES = 64 * 1024 * 1024


class WebWorkspace:
    """Persist one UTF-8 code file for each stable exercise identity."""

    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir).absolute()
        self._lock = threading.RLock()
        self._check_path(self.base_dir)
        if self.base_dir.exists() and not self.base_dir.is_dir():
            raise ValueError("练习工作目录不是目录。")

    @staticmethod
    def _identifier(value: str) -> str:
        if not isinstance(value, str) or not _ID.fullmatch(value):
            raise ValueError("课程、课节或题目标识无效。")
        return value

    @staticmethod
    def _is_link(path: Path) -> bool:
        try:
            info = path.lstat()
        except FileNotFoundError:
            return False
        # Windows junctions are reparse points and are not covered by
        # Path.is_symlink() on every supported Python version.
        return stat.S_ISLNK(info.st_mode) or bool(
            getattr(info, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        )

    def _check_path(self, path: Path) -> None:
        if path != self.base_dir and self.base_dir not in path.parents:
            raise ValueError("练习文件必须位于受管工作目录内。")
        for component in [*reversed(path.parents), path]:
            if self._is_link(component):
                raise ValueError("练习目录不能包含符号链接或目录联接。")
            if component != path and component.exists() and not component.is_dir():
                raise ValueError("练习文件的上级路径不是目录。")
        if path.exists() and not (path.is_file() or path.is_dir()):
            raise ValueError("练习路径不是普通文件或目录。")

    def _lesson_dir(self, course_id: str, lesson_id: str) -> Path:
        path = self.base_dir / self._identifier(course_id) / self._identifier(lesson_id)
        self._check_path(path)
        return path

    def get_path(self, course_id: str, lesson_id: str, step_id: str) -> Path:
        """Return a checked managed filename without creating any storage."""
        path = self._lesson_dir(course_id, lesson_id) / f"exercise_{self._identifier(step_id)}.py"
        self._check_path(path)
        if path.exists() and not path.is_file():
            raise ValueError("练习路径已存在且不是普通文件。")
        return path

    def load(self, course_id: str, lesson_id: str, step_id: str) -> str | None:
        with self._lock:
            path = self.get_path(course_id, lesson_id, step_id)
            if not path.exists():
                return None
            if path.stat().st_size > MAX_CODE_BYTES:
                raise ValueError("练习文件超过允许的大小。")
            try:
                return path.read_text(encoding="utf-8")
            except UnicodeError:
                raise ValueError("练习文件必须使用 UTF-8 编码。") from None

    def save(self, course_id: str, lesson_id: str, step_id: str, code: str,
             *, expected_revision: str | None = None) -> dict:
        """Atomically replace a draft; optionally reject an outdated editor.

        A missing file has revision ``""``. Existing empty drafts have the
        SHA256 of empty bytes, so an intentional empty answer stays distinct
        from a file that has never been saved.
        """
        if not isinstance(code, str):
            raise ValueError("练习代码必须是文本。")
        payload = code.encode("utf-8")
        if len(payload) > MAX_CODE_BYTES:
            raise ValueError("练习代码超过允许的大小。")
        with self._lock:
            path = self.get_path(course_id, lesson_id, step_id)
            if path.exists() and path.stat().st_size > MAX_CODE_BYTES:
                raise ValueError("练习文件超过允许的大小。")
            current = path.read_bytes() if path.exists() else None
            revision = hashlib.sha256(current).hexdigest() if current is not None else ""
            if expected_revision is not None and expected_revision != revision:
                raise ValueError("练习代码已在其他页面更新，请重新加载后再保存。")
            if current == payload:
                return {"saved": True, "path": str(path), "bytes": len(payload), "revision": revision}
            path.parent.mkdir(parents=True, exist_ok=True)
            self._check_path(path)
            temporary = None
            try:
                descriptor, name = tempfile.mkstemp(prefix=".draft-", suffix=".tmp", dir=path.parent)
                temporary = Path(name)
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                self._check_path(path)
                os.replace(temporary, path)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
            return {"saved": True, "path": str(path), "bytes": len(payload),
                    "revision": hashlib.sha256(payload).hexdigest()}

    def restore_legacy(self, course_id: str, lesson_id: str, step_id: str, code: str) -> dict:
        """Preserve an old progress buffer only when no per-task draft exists."""
        if not isinstance(code, str):
            raise ValueError("练习代码必须是文本。")
        with self._lock:
            if self.load(course_id, lesson_id, step_id) is not None:
                return {"saved": False, "reason": "already_saved"}
            if not code.strip():
                return {"saved": False, "reason": "empty"}
            return self.save(course_id, lesson_id, step_id, code, expected_revision="")

    def backup_legacy(self, course_id: str, lesson_id: str, code: str) -> dict:
        """Keep an unbound old buffer without replacing existing backups."""
        if not isinstance(code, str):
            raise ValueError("练习代码必须是文本。")
        if not code.strip():
            return {"saved": False, "reason": "empty"}
        payload = code.encode("utf-8")
        if len(payload) > MAX_CODE_BYTES:
            raise ValueError("练习代码超过允许的大小。")
        with self._lock:
            directory = self._lesson_dir(course_id, lesson_id)
            directory.mkdir(parents=True, exist_ok=True)
            stem = "legacy_restored_" + hashlib.sha256(payload).hexdigest()
            suffix = 0
            while True:
                path = directory / (stem + (f"_{suffix}" if suffix else "") + ".py")
                self._check_path(path)
                if path.exists():
                    if not path.is_file():
                        raise ValueError("旧代码备份路径不是普通文件。")
                    if path.stat().st_size <= MAX_CODE_BYTES and path.read_bytes() == payload:
                        return {"saved": False, "reason": "already_backed_up", "path": str(path)}
                    suffix += 1
                    continue
                try:
                    with path.open("xb") as stream:
                        try:
                            stream.write(payload)
                            stream.flush()
                            os.fsync(stream.fileno())
                        except BaseException:
                            stream.close()
                            path.unlink(missing_ok=True)
                            raise
                except FileExistsError:
                    continue
                return {"saved": True, "path": str(path), "bytes": len(payload)}

    def reset_lesson(self, course_id: str, lesson_id: str) -> dict:
        """Delete only managed exercise files, without recursively deleting."""
        with self._lock:
            directory = self._lesson_dir(course_id, lesson_id)
            if not directory.exists():
                return {"deletedFiles": 0}
            if not directory.is_dir():
                raise ValueError("课节练习路径不是目录。")
            managed = [path for path in directory.iterdir() if _DRAFT.fullmatch(path.name)]
            for path in managed:
                self._check_path(path)
                if not path.is_file():
                    raise ValueError("受管练习路径不是普通文件，已取消重置。")
            for path in managed:
                self._check_path(path)
                path.unlink()
            return {"deletedFiles": len(managed)}

    def iter_files(self) -> list[tuple[str, bytes]]:
        """Snapshot exercise and recognized legacy backup files for a ZIP."""
        with self._lock:
            self._check_path(self.base_dir)
            if not self.base_dir.exists():
                return []
            if not self.base_dir.is_dir():
                raise ValueError("练习工作目录不是目录。")
            files = []
            total_bytes = 0
            for course in sorted(self.base_dir.iterdir()):
                if not _ID.fullmatch(course.name):
                    continue
                self._check_path(course)
                if not course.is_dir():
                    continue
                for lesson in sorted(course.iterdir()):
                    if not _ID.fullmatch(lesson.name):
                        continue
                    self._check_path(lesson)
                    if not lesson.is_dir():
                        continue
                    for path in sorted(lesson.iterdir()):
                        if not (_DRAFT.fullmatch(path.name) or _LEGACY.fullmatch(path.name)):
                            continue
                        self._check_path(path)
                        if not path.is_file():
                            raise ValueError("受管练习路径不是普通文件。")
                        size = path.stat().st_size
                        if size > MAX_CODE_BYTES:
                            raise ValueError("练习文件超过允许的大小。")
                        total_bytes += size
                        if total_bytes > MAX_WORKSPACE_BYTES:
                            raise ValueError("练习代码总量超过单次备份上限，请分开保存代码文件。")
                        files.append((path.relative_to(self.base_dir).as_posix(), path.read_bytes()))
            return files
