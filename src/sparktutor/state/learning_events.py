"""Privacy-conscious learning event storage for explainable diagnosis.

The event log stores behavioral evidence, not claims about ability.  Free-form
chat text and full source code are deliberately excluded from this database.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


EVENT_TYPES = {
    "session_start", "session_end",
    "lesson_loaded", "lesson_unloaded",
    "task_open", "task_start", "task_complete", "task_exit",
    "code_edit", "code_run", "code_submit", "error",
    "hint_request", "hint_view", "hint_accept", "chat_request", "chat_response",
    "solution_view", "transfer_task_complete",
}


@dataclass(frozen=True)
class LearningEvent:
    event_type: str
    course_id: str = ""
    lesson_id: str = ""
    task_id: str = ""
    session_id: str = ""
    source_task_id: str = ""
    task_type: str = ""
    attempt_number: int = 0
    knowledge_components: list[str] = field(default_factory=list)
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
            "sourceTaskId": self.source_task_id,
            "taskType": self.task_type,
            "attemptNumber": self.attempt_number,
            "knowledgeComponents": self.knowledge_components,
            "data": self.data,
        }


class LearningEventStore:
    """Append-only SQLite event log with JSON metadata."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or (Path.home() / ".sparktutor" / "progress.db")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS learning_events (
                    event_id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    course_id TEXT NOT NULL DEFAULT '',
                    lesson_id TEXT NOT NULL DEFAULT '',
                    task_id TEXT NOT NULL DEFAULT '',
                    session_id TEXT NOT NULL DEFAULT '',
                    source_task_id TEXT NOT NULL DEFAULT '',
                    task_type TEXT NOT NULL DEFAULT '',
                    attempt_number INTEGER NOT NULL DEFAULT 0,
                    knowledge_components TEXT NOT NULL DEFAULT '[]',
                    data TEXT NOT NULL DEFAULT '{}'
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_learning_events_context
                ON learning_events(course_id, lesson_id, task_id, timestamp)
            """)

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def record(
        self,
        event_type: str,
        *,
        course_id: str = "",
        lesson_id: str = "",
        task_id: str = "",
        session_id: str = "",
        source_task_id: str = "",
        task_type: str = "",
        attempt_number: int = 0,
        knowledge_components: Optional[list[str]] = None,
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
            source_task_id=source_task_id,
            task_type=task_type,
            attempt_number=max(0, int(attempt_number)),
            knowledge_components=list(knowledge_components or []),
            data=dict(data or {}),
        )
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO learning_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event.event_id, event.event_type, event.timestamp,
                    event.course_id, event.lesson_id, event.task_id,
                    event.session_id, event.source_task_id, event.task_type,
                    event.attempt_number,
                    json.dumps(event.knowledge_components, ensure_ascii=False),
                    json.dumps(event.data, ensure_ascii=False),
                ),
            )
        return event

    def list_events(
        self, *, course_id: str = "", lesson_id: str = "", limit: int = 2000
    ) -> list[LearningEvent]:
        clauses, values = [], []
        if course_id:
            clauses.append("course_id = ?")
            values.append(course_id)
        if lesson_id:
            clauses.append("lesson_id = ?")
            values.append(lesson_id)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        values.append(max(1, min(int(limit), 10000)))
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM learning_events" + where +
                " ORDER BY timestamp ASC LIMIT ?", values,
            ).fetchall()
        return [LearningEvent(
            event_id=r[0], event_type=r[1], timestamp=r[2],
            course_id=r[3], lesson_id=r[4], task_id=r[5],
            session_id=r[6], source_task_id=r[7], task_type=r[8],
            attempt_number=r[9], knowledge_components=json.loads(r[10]),
            data=json.loads(r[11]),
        ) for r in rows]

    def clear(self, *, course_id: str = "", lesson_id: str = "") -> None:
        clauses, values = [], []
        if course_id:
            clauses.append("course_id = ?")
            values.append(course_id)
        if lesson_id:
            clauses.append("lesson_id = ?")
            values.append(lesson_id)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._conn() as conn:
            conn.execute("DELETE FROM learning_events" + where, values)
