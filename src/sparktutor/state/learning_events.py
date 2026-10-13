"""Privacy-conscious SQLite storage for observable learning events."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


EVENT_TYPES = {
    "session_start",
    "session_end",
    "lesson_loaded",
    "task_open",
    "task_start",
    "task_complete",
    "code_edit",
    "code_run",
    "code_submit",
    "error",
    "hint_request",
    "chat_request",
    "solution_view",
}


@dataclass(frozen=True)
class LearningEvent:
    event_type: str
    course_id: str = ""
    lesson_id: str = ""
    task_id: str = ""
    session_id: str = ""
    task_type: str = ""
    attempt_number: int = 0
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: str = ""
    event_id: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "eventId": self.event_id,
            "eventType": self.event_type,
            "timestamp": self.timestamp,
            "courseId": self.course_id,
            "lessonId": self.lesson_id,
            "taskId": self.task_id,
            "sessionId": self.session_id,
            "taskType": self.task_type,
            "attemptNumber": self.attempt_number,
            "data": self.data,
        }


class LearningEventStore:
    """Append-only event log stored beside the existing progress database."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or (Path.home() / ".sparktutor" / "progress.db")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS learning_events (
                    event_id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    course_id TEXT NOT NULL DEFAULT '',
                    lesson_id TEXT NOT NULL DEFAULT '',
                    task_id TEXT NOT NULL DEFAULT '',
                    session_id TEXT NOT NULL DEFAULT '',
                    task_type TEXT NOT NULL DEFAULT '',
                    attempt_number INTEGER NOT NULL DEFAULT 0,
                    data TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            columns = {row[1] for row in conn.execute("PRAGMA table_info(learning_events)")}
            for column in ("session_id", "task_type"):
                if column not in columns:
                    conn.execute(f"ALTER TABLE learning_events ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_learning_events_context
                ON learning_events(course_id, lesson_id, task_id, timestamp)
                """
            )

    @contextmanager
    def _conn(self):
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def record(
        self,
        event_type: str,
        *,
        course_id: str = "",
        lesson_id: str = "",
        task_id: str = "",
        session_id: str = "",
        task_type: str = "",
        attempt_number: int = 0,
        data: Optional[dict[str, Any]] = None,
        timestamp: Optional[str] = None,
    ) -> LearningEvent:
        if event_type not in EVENT_TYPES:
            raise ValueError(f"Unknown learning event type: {event_type}")

        event = LearningEvent(
            event_id=uuid.uuid4().hex,
            event_type=event_type,
            timestamp=timestamp or datetime.now(timezone.utc).isoformat(),
            course_id=course_id,
            lesson_id=lesson_id,
            task_id=task_id,
            session_id=session_id,
            task_type=task_type,
            attempt_number=max(0, int(attempt_number)),
            data=dict(data or {}),
        )
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO learning_events
                (event_id, event_type, timestamp, course_id, lesson_id,
                 task_id, session_id, task_type, attempt_number, data)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.event_type,
                    event.timestamp,
                    event.course_id,
                    event.lesson_id,
                    event.task_id,
                    event.session_id,
                    event.task_type,
                    event.attempt_number,
                    json.dumps(event.data, ensure_ascii=False),
                ),
            )
        return event

    def list_events(
        self,
        *,
        course_id: str = "",
        lesson_id: str = "",
        limit: Optional[int] = 2000,
        order: str = "oldest",
    ) -> list[LearningEvent]:
        """Return events chronologically, optionally selecting the latest page."""
        if not isinstance(order, str) or order not in {"oldest", "latest", "insertion"}:
            raise ValueError("Event order must be oldest, latest or insertion")
        clauses: list[str] = []
        values: list[Any] = []
        if course_id:
            clauses.append("course_id = ?")
            values.append(course_id)
        if lesson_id:
            clauses.append("lesson_id = ?")
            values.append(lesson_id)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        if limit is not None:
            values.append(max(1, min(int(limit), 10000)))
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM learning_events"
                + where
                + (" ORDER BY rowid ASC" if order == "insertion" else
                   " ORDER BY timestamp DESC, rowid DESC" if order == "latest"
                   else " ORDER BY timestamp ASC, rowid ASC")
                + (" LIMIT ?" if limit is not None else ""),
                values,
            ).fetchall()
        if order == "latest":
            rows.reverse()
        return [
            LearningEvent(
                event_id=row["event_id"],
                event_type=row["event_type"],
                timestamp=row["timestamp"],
                course_id=row["course_id"],
                lesson_id=row["lesson_id"],
                task_id=row["task_id"],
                session_id=row["session_id"],
                task_type=row["task_type"],
                attempt_number=row["attempt_number"],
                data=self._event_data(row),
            )
            for row in rows
        ]

    def count_events(self, *, course_id: str = "", lesson_id: str = "") -> int:
        clauses: list[str] = []
        values: list[str] = []
        if course_id:
            clauses.append("course_id = ?")
            values.append(course_id)
        if lesson_id:
            clauses.append("lesson_id = ?")
            values.append(lesson_id)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._conn() as conn:
            return conn.execute("SELECT COUNT(*) FROM learning_events" + where, values).fetchone()[0]

    @staticmethod
    def _event_data(row) -> dict:
        data = json.loads(row["data"])
        if "knowledge_components" in row.keys() and "knowledgeComponents" not in data:
            data["knowledgeComponents"] = json.loads(row["knowledge_components"] or "[]")
        return data

    def clear(self, *, course_id: str = "", lesson_id: str = "") -> None:
        clauses: list[str] = []
        values: list[Any] = []
        if course_id:
            clauses.append("course_id = ?")
            values.append(course_id)
        if lesson_id:
            clauses.append("lesson_id = ?")
            values.append(lesson_id)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._conn() as conn:
            conn.execute("DELETE FROM learning_events" + where, values)
