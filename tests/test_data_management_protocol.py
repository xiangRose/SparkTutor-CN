"""Exercise explicit data management over the real server using temporary data."""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys

from sparktutor.state.progress import ProgressStore


_SERVER_SCRIPT = """
import asyncio
import sys
from pathlib import Path
from sparktutor.config.settings import Settings, ExecutionMode, ClaudeConfig, DatabricksConfig
Settings.load = classmethod(lambda cls: Settings(
    data_dir=Path(sys.argv[1]), execution_mode=ExecutionMode.DRY_RUN,
    claude=ClaudeConfig(api_key='PRIVATE_MODEL_CREDENTIAL'),
    databricks=DatabricksConfig(token='PRIVATE_REMOTE_CREDENTIAL'),
))
from sparktutor.engine import environment_check
async def no_java_process():
    return environment_check._check('java', 'Java / JAVA_HOME', 'missing', 'Temporary test: Java not launched.')
environment_check._java_check = no_java_process
from sparktutor.server.__main__ import main
asyncio.run(main())
"""


def _run_server(tmp_path, requests):
    completed = subprocess.run(
        [sys.executable, "-X", "utf8", "-c", _SERVER_SCRIPT, str(tmp_path)],
        input="".join(json.dumps(request) + "\n" for request in requests),
        capture_output=True, encoding="utf-8", timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    return {item["id"]: item for item in map(json.loads, completed.stdout.splitlines()) if "id" in item}


def test_data_management_protocol_exports_backs_up_and_prevents_stale_writes_after_clear(tmp_path):
    database = tmp_path / "progress.db"
    ProgressStore(database).save("learning_spark", "01_getting_started", 0, 5, last_code="PRIVATE_STUDENT_CODE")
    destination = tmp_path / "selected-backup.db"
    requests = [
        {"id": 1, "method": "getEnvironmentReport"},
        {"id": 2, "method": "getLearningDataOverview"},
        {"id": 3, "method": "exportLearningSummary"},
        {"id": 4, "method": "backupLearningDatabase", "params": {"destination": "relative.db"}},
        {"id": 5, "method": "backupLearningDatabase", "params": {"destination": str(destination)}},
        {"id": 6, "method": "clearLearningRecords", "params": {"confirmation": "clear-learning-records"}},
        {"id": 7, "method": "exportLearningSummary"},
        {"id": 8, "method": "recordLearningEvent", "params": {
            "eventType": "session_end", "data": {"reason": "extension_deactivated"},
        }},
        {"id": 9, "method": "loadLesson", "params": {
            "courseId": "learning_spark", "lessonIdx": 0, "depth": "beginner",
        }},
        {"id": 10, "method": "advance", "params": {"code": "STALE_BUFFER_CODE"}},
        {"id": 11, "method": "getLearningDashboard"},
        {"id": 12, "method": "getLearningEvents"},
        {"id": 13, "method": "getLearningCoach"},
    ]
    responses = _run_server(tmp_path, requests)
    report = responses[1]["result"]
    assert report["staticOnly"] is True and report["sparkVerified"] is False
    assert report["execution"]["configuredMode"] == "dry_run"
    overview = responses[2]["result"]
    assert overview["databasePath"] == str(database.resolve())
    assert overview["progressCount"] == 1 and overview["eventCount"] == 1
    assert "PRIVATE_" not in json.dumps([report, overview, responses[3]["result"]])
    assert responses[4].get("error")
    assert responses[5]["result"]["destination"] == str(destination.resolve())
    assert responses[6]["result"] == {"cleared": True, "deletedProgress": 1, "deletedEvents": 1}
    assert responses[7]["result"]["totals"] == {"progressCount": 0, "completedLessonCount": 0, "eventCount": 0}
    for identifier in (8, 9, 10, 13):
        assert "重载" in responses[identifier]["error"]
    dashboard = responses[11]["result"]
    assert dashboard["resume"]["action"] == "start"
    assert all(not course["progress"]["started"] for course in dashboard["courses"])
    assert responses[12]["result"]["totalCount"] == 0
    # Normal EOF is itself a lifecycle callback, outside dispatch. It must not
    # resurrect an old session after the user's explicit deletion.
    with sqlite3.connect(database) as conn:
        assert conn.execute("SELECT COUNT(*) FROM progress").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM learning_events").fetchone()[0] == 0
    with sqlite3.connect(destination) as conn:
        assert conn.execute("SELECT last_code FROM progress").fetchone()[0] == "PRIVATE_STUDENT_CODE"
        assert conn.execute("SELECT COUNT(*) FROM learning_events").fetchone()[0] == 1


def test_data_management_protocol_rejects_clear_without_confirmation_and_keeps_session_usable(tmp_path):
    database = tmp_path / "progress.db"
    ProgressStore(database).save("learning_spark", "01_getting_started", 0, 5)
    responses = _run_server(tmp_path, [
        {"id": 1, "method": "clearLearningRecords"},
        {"id": 2, "method": "getLearningDataOverview"},
        {"id": 3, "method": "loadLesson", "params": {
            "courseId": "learning_spark", "lessonIdx": 0, "depth": "beginner",
        }},
    ])
    assert "明确确认" in responses[1]["error"]
    assert responses[2]["result"]["progressCount"] == 1
    assert responses[3].get("result"), "Rejected deletion must not freeze the learning session"
