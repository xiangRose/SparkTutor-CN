"""Exercise the Tracker contract through the real JSON-lines process.

All events live in a temporary database; the learner's saved history is untouched.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from sparktutor.state.learning_events import LearningEventStore


@pytest.mark.parametrize("explicit_end", [False, True])
def test_tracker_protocol_records_metadata_and_closes_session_once(tmp_path, explicit_end):
    script = """
import asyncio
import sys
from pathlib import Path
from sparktutor.config.settings import Settings
Settings.load = classmethod(lambda cls: Settings(data_dir=Path(sys.argv[1])))
from sparktutor.server.__main__ import main
asyncio.run(main())
"""
    requests = [
        {"id": 1, "method": "loadLesson", "params": {"courseId": "learning_spark", "lessonIdx": 10}},
        {"id": 2, "method": "advance", "params": {}},
        {"id": 3, "method": "recordLearningEvent", "params": {
            "eventType": "code_edit", "courseId": "learning_spark",
            "lessonId": "11_diagnosis_practice", "taskId": "11_diagnosis_practice:debug_orders",
            "data": {"changeCount": 2, "addedChars": 7, "removedChars": 3,
                     "documentVersion": 4, "burstDurationMs": 120},
        }},
        {"id": 4, "method": "recordLearningEvent", "params": {
            "eventType": "code_submit", "data": {
                "assessmentEligible": True, "assessmentSource": "course_tests", "passed": True,
            },
        }},
        {"id": 5, "method": "getDiagnosis", "params": {}},
        {"id": 6, "method": "getLearningEvents", "params": {"order": "latest", "limit": 200}},
    ]
    if explicit_end:
        end_params = {"eventType": "session_end", "data": {"reason": "extension_deactivated"}}
        requests.extend([
            {"id": 7, "method": "recordLearningEvent", "params": end_params},
            {"id": 8, "method": "recordLearningEvent", "params": end_params},
        ])

    completed = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)],
        input="".join(json.dumps(request, ensure_ascii=False) + "\n" for request in requests),
        capture_output=True, encoding="utf-8", timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    responses = {item["id"]: item for item in map(json.loads, completed.stdout.splitlines()) if "id" in item}
    assert responses[1]["result"]["lessonTitle"] == "调试与新场景迁移练习"
    assert responses[3]["result"]["recorded"] is True
    assert responses[4].get("error"), "The client must not manufacture assessment events"
    assert all(item["score"] is None for item in responses[5]["result"]["dimensions"])
    assert responses[6]["result"]["hasMore"] is False

    events = LearningEventStore(tmp_path / "progress.db").list_events(limit=None)
    edits = [event for event in events if event.event_type == "code_edit"]
    assert len(edits) == 1
    assert edits[0].task_id == "11_diagnosis_practice:debug_orders"
    assert edits[0].data["changeCount"] == 2
    assert edits[0].data["addedChars"] == 7
    assert not any(event.event_type == "code_submit" for event in events)
    starts = [event for event in events if event.event_type == "session_start"]
    ends = [event for event in events if event.event_type == "session_end"]
    assert len(starts) == len(ends) == 1
    assert starts[0].session_id == ends[0].session_id == edits[0].session_id
    assert ends[0].data["reason"] == ("extension_deactivated" if explicit_end else "server_shutdown")
    if explicit_end:
        assert responses[7]["result"]["event"]["eventId"] == responses[8]["result"]["event"]["eventId"]
