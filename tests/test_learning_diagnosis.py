from pathlib import Path

from sparktutor.engine.diagnosis import build_diagnosis
from sparktutor.state.learning_events import LearningEventStore


def test_diagnosis_returns_four_dimensions_sentence_and_exercise(tmp_path: Path):
    store = LearningEventStore(tmp_path / "events.db")
    store.record("code_run", task_id="t1", data={"exitCode": 1, "errorType": "TypeError"})
    store.record("code_run", task_id="t1", data={"exitCode": 1, "errorType": "TypeError"})
    store.record("code_run", task_id="t1", data={"exitCode": 0})
    store.record(
        "code_submit",
        task_id="t1",
        attempt_number=1,
        data={"passed": True, "hintUsed": False, "knowledgeComponents": ["filter"]},
    )

    result = build_diagnosis(store.list_events())
    dimensions = {item["key"]: item for item in result["dimensions"]}
    assert set(dimensions) == {"knowledge", "debugging", "hint_independence", "transfer"}
    assert dimensions["knowledge"]["score"] == 100.0
    assert dimensions["debugging"]["metrics"]["failureEpisodes"] == 1
    assert dimensions["debugging"]["score"] == 100.0
    assert result["diagnosis"]
    assert result["recommendedExercise"]["courseId"] == "learning_spark"
    assert result["knowledgeComponents"]["filter"] == {"attempts": 1, "passed": 1}


def test_hint_risk_and_productivity_have_unambiguous_semantics(tmp_path: Path):
    store = LearningEventStore(tmp_path / "events.db")
    store.record("code_run", task_id="t1", attempt_number=1, data={"exitCode": 1})
    store.record("hint_request", task_id="t1", attempt_number=1)
    store.record("code_submit", task_id="t1", attempt_number=2, data={"passed": True})
    store.record("code_submit", task_id="t2", attempt_number=1, data={"passed": True})

    result = build_diagnosis(store.list_events())
    item = next(d for d in result["dimensions"] if d["key"] == "hint_independence")
    assert item["score"] == 50.0
    assert item["metrics"]["hintDependencyRisk"] == 50.0
    assert item["metrics"]["hintProductivity"] == 100.0


def test_debugging_tracks_multiple_failures_until_repair_and_assistance(tmp_path: Path):
    store = LearningEventStore(tmp_path / "events.db")
    store.record("code_run", task_id="independent", data={"exitCode": 1})
    store.record("code_run", task_id="independent", data={"exitCode": 1})
    store.record("code_submit", task_id="independent", data={"passed": True})
    store.record("code_run", task_id="assisted", data={"exitCode": 1})
    store.record("hint_request", task_id="assisted")
    store.record("code_run", task_id="assisted", data={"exitCode": 0})

    result = build_diagnosis(store.list_events())
    item = next(d for d in result["dimensions"] if d["key"] == "debugging")
    assert item["metrics"]["failureEpisodes"] == 2
    assert item["metrics"]["independentRepairs"] == 1
    assert item["metrics"]["assistedRepairs"] == 1
    assert item["score"] == 50.0


def test_empty_history_is_honest_about_missing_evidence():
    result = build_diagnosis([])
    assert all(item["score"] is None for item in result["dimensions"])
    assert result["recommendedExercise"] is None
    assert "证据不足" in result["diagnosis"]


def test_store_migrates_legacy_event_schema(tmp_path: Path):
    import sqlite3

    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE learning_events (event_id TEXT PRIMARY KEY, event_type TEXT NOT NULL, "
            "timestamp TEXT NOT NULL, course_id TEXT NOT NULL DEFAULT '', lesson_id TEXT NOT NULL DEFAULT '', "
            "task_id TEXT NOT NULL DEFAULT '', attempt_number INTEGER NOT NULL DEFAULT 0, "
            "knowledge_components TEXT NOT NULL DEFAULT '[]', data TEXT NOT NULL DEFAULT '{}')"
        )
        conn.execute(
            "INSERT INTO learning_events VALUES ('old', 'code_submit', '2026-01-01', '', '', 't', 1, "
            "'[\"dataframe.filter\"]', '{\"passed\": true}')"
        )
    store = LearningEventStore(path)
    event = store.record("session_start", session_id="session")
    assert event.session_id == "session"
    events = store.list_events()
    assert events[0].data["knowledgeComponents"] == ["dataframe.filter"]
    assert events[1].session_id == "session"
