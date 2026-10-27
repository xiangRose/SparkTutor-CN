"""Explicit local learning-data operations; never read AI configuration or files.

Summary exports contain only aggregate learning metadata. Database backups are
different: they copy every SQLite table, including ``progress.last_code``.
Callers must obtain a deliberate user choice before backup or deletion.
"""

from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from sparktutor.state.learning_events import EVENT_TYPES


_SCHEMAS = {
    "progress": {"course_id", "lesson_id", "current_step", "total_steps", "completed",
                 "depth", "last_code", "updated_at"},
    "learning_events": {"event_id", "event_type", "timestamp", "course_id", "lesson_id",
                        "task_id", "attempt_number", "data"},
}
_BOUNDARY = {
    "included": ["课程和课节标识、完成状态及数量", "按类型统计的学习事件数量"],
    "excluded": ["练习代码及 last_code", "聊天内容", "事件原始 data 和错误正文", "AI 配置及凭据", "本机绝对路径"],
}


@contextmanager
def _connection(db_path: Path, *, writable: bool = False) -> Iterator[sqlite3.Connection]:
    # URI mode prevents an overview/export from silently creating a new database.
    mode = "rw" if writable else "ro"
    conn = sqlite3.connect(Path(db_path).resolve().as_uri() + f"?mode={mode}", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _checked_tables(conn: sqlite3.Connection) -> set[str]:
    objects = {row["name"]: row["type"] for row in conn.execute(
        "SELECT name, type FROM sqlite_master WHERE name IN ('progress', 'learning_events')"
    )}
    for table, object_type in objects.items():
        if object_type != "table":
            raise ValueError(f"学习数据结构不匹配：{table} 不是数据表。")
        columns = {row["name"] for row in conn.execute(f'PRAGMA table_info("{table}")')}
        if not _SCHEMAS[table].issubset(columns):
            raise ValueError(f"学习数据结构不匹配：{table} 缺少必要字段，操作已取消。")
    return set(objects)


def _counts(conn: sqlite3.Connection, tables: set[str]) -> dict:
    result = {"progressCount": 0, "completedLessonCount": 0, "eventCount": 0}
    if "progress" in tables:
        row = conn.execute("SELECT COUNT(*) AS total, SUM(completed = 1) AS completed FROM progress").fetchone()
        result.update(progressCount=row["total"], completedLessonCount=row["completed"] or 0)
    if "learning_events" in tables:
        result["eventCount"] = conn.execute("SELECT COUNT(*) FROM learning_events").fetchone()[0]
    return result


def get_data_overview(db_path: Path, workspace_path: Path) -> dict:
    """Describe local locations and counts without opening code or credentials."""
    db_path = Path(db_path)
    result = {
        "databasePath": str(db_path.resolve()),
        "workspacePath": str(Path(workspace_path).resolve()),
        "databaseExists": db_path.is_file(),
        "progressCount": 0, "completedLessonCount": 0, "eventCount": 0,
        "privacy": {
            "summary": "摘要仅导出课程进度和事件数量，不含代码、聊天、凭据或错误正文。",
            "backup": "完整数据库备份可能包含 progress.last_code 代码及全部学习事件；请妥善保管。练习文件需另行复制。",
            "clear": "清除会删除课程进度和行为记录（含诊断与复盘依据）；保留练习文件、扩展设置和模型密钥。此操作不是磁盘安全擦除。",
            "model": "使用 AI 问答、评审或教练时，相关题面、当前代码、问题/对话或学习证据会发给你选择的模型服务；本地摘要、备份和清除不会调用模型。",
            "restore": "迁移时先关闭所有 SparkTutor 窗口和后端，保留原 progress.db，再用备份替换；练习目录单独复制。模型密钥需在新设备重新配置。",
        },
    }
    if result["databaseExists"]:
        with _connection(db_path) as conn:
            conn.execute("BEGIN")
            result.update(_counts(conn, _checked_tables(conn)))
    return result


def export_learning_summary(db_path: Path) -> dict:
    """Return a JSON-ready allowlisted summary, never raw event payloads."""
    result = {
        "schemaVersion": 1,
        "exportedAt": datetime.now(timezone.utc).isoformat(),
        "totals": {"progressCount": 0, "completedLessonCount": 0, "eventCount": 0},
        "courses": [], "eventCounts": {}, "contentBoundary": _BOUNDARY,
    }
    if not Path(db_path).is_file():
        return result
    with _connection(db_path) as conn:
        conn.execute("BEGIN")
        tables = _checked_tables(conn)
        result["totals"] = _counts(conn, tables)
        if "progress" in tables:
            courses: dict[str, list[dict]] = {}
            for row in conn.execute("SELECT course_id, lesson_id, completed FROM progress ORDER BY course_id, lesson_id"):
                # Course files use identifiers, never learner-supplied prose.
                # Malformed historical identifiers must not smuggle text into an export.
                if not all(isinstance(row[key], str) and re.fullmatch(r"[A-Za-z0-9_-]{1,160}", row[key])
                           for key in ("course_id", "lesson_id")):
                    continue
                courses.setdefault(row["course_id"], []).append({
                    "lessonId": row["lesson_id"], "completed": row["completed"] == 1,
                })
            result["courses"] = [{"courseId": course_id, "lessons": lessons}
                                 for course_id, lessons in courses.items()]
        if "learning_events" in tables:
            result["eventCounts"] = {
                row["event_type"]: row["total"] for row in conn.execute(
                    "SELECT event_type, COUNT(*) AS total FROM learning_events GROUP BY event_type ORDER BY event_type"
                ) if row["event_type"] in EVENT_TYPES
            }
    return result


def backup_learning_database(db_path: Path, destination: Path) -> dict:
    """Create a consistent, exclusive SQLite backup, including any stored code.

    The caller selects an unused local destination. Existing files, source files
    and SQLite sidecars are never overwritten. The workspace is not included.
    """
    db_path, destination = Path(db_path).resolve(), Path(destination).resolve()
    if destination in {db_path, Path(str(db_path) + "-wal"), Path(str(db_path) + "-shm"),
                       Path(str(db_path) + "-journal")}:
        raise ValueError("备份目标不能是当前数据库或其日志文件。")
    if not db_path.is_file():
        raise FileNotFoundError("尚无学习数据库可备份。")
    # Reserve exclusively; a Save dialog's overwrite confirmation is not enough
    # to authorize replacing a source or an unrelated file on the backend.
    with _connection(db_path) as source:
        _checked_tables(source)
        with destination.open("xb"):
            pass
        try:
            target = sqlite3.connect(destination)
            try:
                source.backup(target)
                if target.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise ValueError("备份一致性检查失败。")
            finally:
                target.close()
        except BaseException:
            destination.unlink(missing_ok=True)
            raise
    return {"destination": str(destination), "bytes": destination.stat().st_size,
            "includesCode": True, "includesWorkspace": False}


def _validate_delete_boundary(conn: sqlite3.Connection, tables: set[str]) -> None:
    # Refuse schema customizations that could make DELETE affect other data.
    for row in conn.execute("SELECT tbl_name FROM sqlite_master WHERE type = 'trigger'"):
        if row["tbl_name"].casefold() in tables:
            raise ValueError("学习表存在额外触发器，无法确认清除范围，操作已取消。")
    for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'"):
        name = row["name"].replace('"', '""')
        for foreign_key in conn.execute(f'PRAGMA foreign_key_list("{name}")'):
            if foreign_key["table"].casefold() in tables:
                raise ValueError("其他数据表引用学习记录，无法确认清除范围，操作已取消。")


def clear_learning_records(db_path: Path) -> dict:
    """Delete only the two known learning tables, together or not at all.

    This is logical deletion, not secure erasure. It never deletes workspace
    files, directories, model configuration or credentials. UI confirmation is
    required before the caller invokes this function.
    """
    result = {"cleared": True, "deletedProgress": 0, "deletedEvents": 0}
    if not Path(db_path).is_file():
        return result
    with _connection(db_path, writable=True) as conn:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            tables = _checked_tables(conn)
            _validate_delete_boundary(conn, tables)
            if "learning_events" in tables:
                result["deletedEvents"] = conn.execute("DELETE FROM learning_events").rowcount
            if "progress" in tables:
                result["deletedProgress"] = conn.execute("DELETE FROM progress").rowcount
    return result
