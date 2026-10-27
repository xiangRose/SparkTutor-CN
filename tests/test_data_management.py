"""Learning-data operations use temporary databases only."""
from contextlib import contextmanager
import json
import sqlite3

import pytest

from sparktutor.state import data_management
from sparktutor.state.data_management import (
    backup_learning_database, clear_learning_records, export_learning_summary,
    get_data_overview,
)
from sparktutor.state.learning_events import LearningEventStore
from sparktutor.state.progress import ProgressStore


@pytest.fixture
def learning_db(tmp_path):
    database = tmp_path / "progress.db"
    progress = ProgressStore(database)
    progress.save("course", "lesson_1", 2, 4, last_code="PRIVATE_STUDENT_CODE", completed=True)
    progress.save("course", "lesson_2", 0, 4)
    events = LearningEventStore(database)
    events.record("chat_request", course_id="course", data={
        "question": "PRIVATE_CHAT_TEXT", "apiKey": "PRIVATE_CREDENTIAL",
    })
    events.record("error", data={"rawError": "PRIVATE_ERROR_TEXT"})
    events.record("code_submit", data={"passed": True, "code": "PRIVATE_EVENT_CODE"})
    return database


def test_overview_and_empty_summary_do_not_create_storage(tmp_path):
    database = tmp_path / "not_created" / "progress.db"
    overview = get_data_overview(database, tmp_path / "workspace")
    assert overview["databaseExists"] is False
    assert overview["eventCount"] == 0
    assert overview["databasePath"] == str(database.resolve())
    assert export_learning_summary(database)["totals"]["progressCount"] == 0
    assert clear_learning_records(database)["deletedEvents"] == 0
    assert not database.parent.exists()


def test_summary_excludes_raw_data_code_credentials_and_paths(learning_db):
    # Old, malformed event data is still safe to count: it is never decoded.
    with sqlite3.connect(learning_db) as conn:
        conn.execute("UPDATE learning_events SET data = 'NOT_JSON_PRIVATE_PAYLOAD' WHERE event_type='error'")
        conn.execute("UPDATE progress SET updated_at='PRIVATE_TIMESTAMP', depth='PRIVATE_DEPTH'")
    summary = export_learning_summary(learning_db)
    serialized = json.dumps(summary, ensure_ascii=False)
    assert "PRIVATE_" not in serialized
    assert str(learning_db.parent) not in serialized
    assert summary["totals"] == {"progressCount": 2, "completedLessonCount": 1, "eventCount": 3}
    assert summary["courses"] == [{"courseId": "course", "lessons": [
        {"lessonId": "lesson_1", "completed": True}, {"lessonId": "lesson_2", "completed": False},
    ]}]
    assert summary["eventCounts"] == {"chat_request": 1, "code_submit": 1, "error": 1}
    overview = get_data_overview(learning_db, learning_db.parent / "workspace")
    assert overview["eventCount"] == 3
    assert "last_code" in overview["privacy"]["backup"]
    assert "PRIVATE_" not in json.dumps(overview)


def test_summary_rejects_free_text_identifiers_and_unknown_event_names(learning_db):
    with sqlite3.connect(learning_db) as conn:
        conn.execute("UPDATE progress SET lesson_id='PRIVATE free text code()' WHERE lesson_id='lesson_1'")
        conn.execute("UPDATE learning_events SET event_type='PRIVATE free text' WHERE event_type='error'")
    summary = export_learning_summary(learning_db)
    assert "PRIVATE" not in json.dumps(summary)
    assert summary["totals"]["eventCount"] == 3
    assert summary["courses"][0]["lessons"] == [{"lessonId": "lesson_2", "completed": False}]


def test_backup_is_consistent_in_wal_mode_and_contains_stored_code(learning_db, tmp_path):
    destination = tmp_path / "backup.db"
    source = sqlite3.connect(learning_db)
    try:
        source.execute("PRAGMA journal_mode=WAL")
        source.execute("UPDATE progress SET last_code='LATEST_PRIVATE_CODE' WHERE lesson_id='lesson_1'")
        source.commit()
        result = backup_learning_database(learning_db, destination)
        assert result["includesCode"] is True
        assert result["includesWorkspace"] is False
        with sqlite3.connect(destination) as snapshot:
            assert snapshot.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert snapshot.execute("SELECT last_code FROM progress WHERE lesson_id='lesson_1'").fetchone()[0] == "LATEST_PRIVATE_CODE"
            assert snapshot.execute("SELECT COUNT(*) FROM learning_events").fetchone()[0] == 3
        assert result["bytes"] == destination.stat().st_size
    finally:
        source.close()


def test_backup_never_overwrites_source_sidecars_or_existing_file(learning_db, tmp_path):
    with pytest.raises(ValueError, match="目标不能"):
        backup_learning_database(learning_db, learning_db)
    with pytest.raises(ValueError, match="目标不能"):
        backup_learning_database(learning_db, str(learning_db) + "-wal")
    existing = tmp_path / "existing.db"
    existing.write_text("preserve me")
    with pytest.raises(FileExistsError):
        backup_learning_database(learning_db, existing)
    assert existing.read_text() == "preserve me"
    assert export_learning_summary(learning_db)["totals"]["eventCount"] == 3


def test_clear_only_deletes_learning_rows_and_preserves_files_and_other_tables(learning_db, tmp_path):
    code = tmp_path / "workspace" / "exercise.py"
    code.parent.mkdir()
    code.write_text("keep_student_work()")
    with sqlite3.connect(learning_db) as conn:
        conn.execute("CREATE TABLE application_settings (name TEXT, value TEXT)")
        conn.execute("INSERT INTO application_settings VALUES ('setting', 'keep')")
    result = clear_learning_records(learning_db)
    assert result == {"cleared": True, "deletedProgress": 2, "deletedEvents": 3}
    assert export_learning_summary(learning_db)["totals"] == {
        "progressCount": 0, "completedLessonCount": 0, "eventCount": 0,
    }
    assert code.read_text() == "keep_student_work()"
    with sqlite3.connect(learning_db) as conn:
        assert conn.execute("SELECT value FROM application_settings").fetchone()[0] == "keep"


def test_clear_rolls_back_first_table_when_second_delete_fails(learning_db, monkeypatch):
    original_connection = data_management._connection

    @contextmanager
    def deny_progress_delete(db_path, *, writable=False):
        with original_connection(db_path, writable=writable) as conn:
            conn.set_authorizer(lambda action, table, *_:
                sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_DELETE and table == "progress"
                else sqlite3.SQLITE_OK)
            yield conn

    monkeypatch.setattr(data_management, "_connection", deny_progress_delete)
    with pytest.raises(sqlite3.DatabaseError):
        clear_learning_records(learning_db)
    monkeypatch.setattr(data_management, "_connection", original_connection)
    assert export_learning_summary(learning_db)["totals"] == {
        "progressCount": 2, "completedLessonCount": 1, "eventCount": 3,
    }


@pytest.mark.parametrize("extra_schema", [
    "CREATE TRIGGER external_change AFTER DELETE ON progress BEGIN DELETE FROM learning_events; END",
    "CREATE TABLE linked_records (course_id TEXT REFERENCES progress(course_id) ON DELETE CASCADE)",
    "CREATE TABLE linked_records (course_id TEXT REFERENCES PROGRESS(course_id) ON DELETE CASCADE)",
])
def test_clear_refuses_schema_with_unbounded_side_effects(learning_db, extra_schema):
    with sqlite3.connect(learning_db) as conn:
        conn.execute(extra_schema)
    with pytest.raises(ValueError, match="无法确认清除范围"):
        clear_learning_records(learning_db)
    assert export_learning_summary(learning_db)["totals"]["eventCount"] == 3


def test_clear_refuses_mismatched_named_table_without_partial_deletion(learning_db):
    with sqlite3.connect(learning_db) as conn:
        conn.execute("DROP TABLE progress")
        conn.execute("CREATE TABLE progress (unrelated TEXT)")
        conn.execute("INSERT INTO progress VALUES ('keep')")
    with pytest.raises(ValueError, match="数据结构不匹配"):
        clear_learning_records(learning_db)
    with sqlite3.connect(learning_db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM learning_events").fetchone()[0] == 3
        assert conn.execute("SELECT unrelated FROM progress").fetchone()[0] == "keep"
