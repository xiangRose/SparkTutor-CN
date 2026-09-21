from pathlib import Path

from sparktutor.engine.diagnosis import build_diagnosis
from sparktutor.state.learning_events import LearningEventStore


def test_event_store_round_trip(tmp_path: Path):
    store = LearningEventStore(tmp_path / "events.db")
    store.record(
        "code_submit", course_id="course", lesson_id="lesson",
        task_id="lesson:1", attempt_number=1,
        knowledge_components=["dataframe.filter"],
        data={"passed": True, "hintUsed": False},
    )
    events = store.list_events(course_id="course")
    assert len(events) == 1
    assert events[0].knowledge_components == ["dataframe.filter"]
    assert events[0].data["passed"] is True


def test_diagnosis_reports_scores_and_confidence(tmp_path: Path):
    store = LearningEventStore(tmp_path / "events.db")
    store.record("code_run", task_id="t1", data={"exitCode": 1, "errorType": "TypeError"})
    store.record("code_run", task_id="t1", data={"exitCode": 0, "errorType": ""})
    store.record(
        "code_submit", task_id="t1", attempt_number=1,
        knowledge_components=["filter"],
        data={"passed": True, "hintUsed": False, "isTransfer": False},
    )

    result = build_diagnosis(store.list_events())
    dimensions = {item["key"]: item for item in result["dimensions"]}
    assert dimensions["knowledge"]["score"] == 100.0
    assert dimensions["debugging"]["score"] == 100.0
    assert dimensions["transfer"]["score"] is None
    assert dimensions["transfer"]["confidence"] == "low"
    assert result["knowledgeComponents"]["filter"] == {"attempts": 1, "passed": 1}


def test_help_seeking_distinguishes_premature_hint(tmp_path: Path):
    store = LearningEventStore(tmp_path / "events.db")
    store.record("hint_request", task_id="t1", attempt_number=0)
    store.record("code_submit", task_id="t1", attempt_number=1, data={"passed": True})
    result = build_diagnosis(store.list_events())
    item = next(d for d in result["dimensions"] if d["key"] == "helpSeeking")
    assert item["metrics"]["productiveHints"] == 1
    assert item["metrics"]["prematureHints"] == 1
    assert item["score"] == 50.0
