"""The demo must leave an active, real learning session untouched."""

import json
import subprocess
import sys

import pytest

from sparktutor.config.settings import Settings
from sparktutor.server.handler import ServerHandler
from sparktutor.state.progress import ProgressStore


def test_demo_json_lines_preserves_normal_records_and_loaded_lesson(tmp_path):
    progress = ProgressStore(tmp_path / "progress.db")
    progress.save("learning_spark", "01_getting_started", 0, 12,
                  last_code="PRIVATE REAL STUDENT CODE")
    saved = progress.get_course_progress("learning_spark")
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
        {"id": 1, "method": "loadLesson", "params": {"courseId": "learning_spark", "lessonIdx": 0}},
        {"id": 2, "method": "getStep", "params": {}},
        {"id": 3, "method": "getLearningEvents", "params": {}},
        {"id": 4, "method": "runDemoScenario", "params": {"scenarioId": "transfer_retry", "mode": "recorded", "runId": "retry_run"}},
        {"id": 5, "method": "getStep", "params": {}},
        {"id": 6, "method": "getLearningEvents", "params": {}},
        {"id": 7, "method": "runDemoScenario", "params": {"scenarioId": "unknown"}},
        {"id": 8, "method": "runDemoScenario", "params": {"scenarioId": "transfer_first_pass", "mode": "recorded", "runId": "first_run"}},
        {"id": 9, "method": "getLearningEvents", "params": {}},
    ]
    completed = subprocess.run([sys.executable, "-X", "utf8", "-c", script, str(tmp_path)],
                               input="".join(json.dumps(request) + "\n" for request in requests),
                               capture_output=True, encoding="utf-8", timeout=60)
    assert completed.returncode == 0, completed.stderr
    lines = [json.loads(line) for line in completed.stdout.splitlines()]
    responses = {line["id"]: line for line in lines if "id" in line}
    assert responses[2]["result"] == responses[5]["result"]
    assert responses[3]["result"] == responses[6]["result"] == responses[9]["result"]
    for request_id, expected in [(4, 0), (8, 100)]:
        report = responses[request_id]["result"]
        assert report["runId"] == ("retry_run" if request_id == 4 else "first_run")
        assert report["isDemo"] is True and report["isolated"] is True
        assert report["sparkVerified"] is False
        final = {item["key"]: item for item in report["stages"][-1]["diagnosis"]["dimensions"]}
        assert final["transfer"]["score"] == expected
        assert "PRIVATE REAL STUDENT CODE" not in json.dumps(report)
        assert str(tmp_path) not in json.dumps(report)
    assert responses[7].get("error")
    notifications = [line for line in lines if line.get("method") == "demoProgress"]
    assert notifications
    assert all(set(line["params"]) <= {"id", "title", "index", "total", "runId"} for line in notifications)
    assert {line["params"]["runId"] for line in notifications} == {"retry_run", "first_run"}
    assert progress.get_course_progress("learning_spark") == saved


@pytest.mark.asyncio
async def test_demo_lock_rejects_overlap_and_releases_after_cancellation(tmp_path, monkeypatch):
    import asyncio
    import sparktutor.engine.demo_scenarios as scenarios

    entered = asyncio.Event()
    async def blocked(**kwargs):
        entered.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(scenarios, "run_demo_scenario", blocked)
    handler = ServerHandler(Settings(data_dir=tmp_path))
    first = asyncio.create_task(handler.dispatch({"method": "runDemoScenario", "params": {}}))
    await entered.wait()
    with pytest.raises(ValueError, match="正在运行"):
        await handler.dispatch({"method": "runDemoScenario", "params": {}})
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    assert handler._demo_running is False
