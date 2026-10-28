"""Exercise coaching through the real JSON-lines server, without an AI service."""

import json
import queue
import subprocess
import sys
import threading
from contextlib import contextmanager

from sparktutor.state.progress import ProgressStore


@contextmanager
def _server(data_dir):
    script = """
import asyncio
import sys
from pathlib import Path
from sparktutor.config.settings import Settings
Settings.load = classmethod(lambda cls: Settings(data_dir=Path(sys.argv[1])))
from sparktutor.server.__main__ import main
asyncio.run(main())
"""
    process = subprocess.Popen(
        [sys.executable, "-X", "utf8", "-c", script, str(data_dir)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8",
    )
    lines = queue.Queue()

    def read():
        for line in process.stdout:
            lines.put(line)
        lines.put(None)

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    sequence = 0

    def request(method, params=None, *, error=False):
        nonlocal sequence
        sequence += 1
        process.stdin.write(json.dumps({"id": sequence, "method": method, "params": params or {}}) + "\n")
        process.stdin.flush()
        while True:
            line = lines.get(timeout=20)
            assert line is not None, "Server closed before replying"
            reply = json.loads(line)
            if "id" not in reply:
                continue
            assert reply["id"] == sequence
            if error:
                assert reply.get("error"), reply
                return reply["error"]
            assert not reply.get("error"), reply
            return reply["result"]

    try:
        yield request
    finally:
        process.stdin.close()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
        reader.join(timeout=2)
        process.stdout.close()
        process.stderr.close()


def _response(prompt, *, probe=True):
    return json.dumps({
        "hypothesisId": "row_count_vs_quantity" if probe else "uncertain",
        "evidenceIds": [prompt["evidence"][0]["id"]],
        "probe": {"values": [2, 5]} if probe else None,
    })


def test_coach_protocol_ack_probe_and_navigation_preserve_assessment(tmp_path):
    progress = ProgressStore(tmp_path / "progress.db")
    progress.save("learning_spark", "01_getting_started", 1, 12,
                  last_code="# EXISTING PRIVATE WORK")
    original = progress.get_course_progress("learning_spark")
    code = "# PRIVATE COACH INPUT\nresult = batch.groupBy('State', 'Color').count()"
    with _server(tmp_path) as request:
        initial = request("getLearningCoach")
        assert initial["available"] is False
        assert len(initial["exerciseOptions"]) >= 5
        option = next(item for item in initial["exerciseOptions"] if item["stepId"] == "15")
        target = {key: option[key] for key in ("courseId", "lessonId", "stepId")}
        loaded = request("openCoachExercise", target)
        assert loaded["practiceMode"] is True and loaded["step"]["id"] == "15"
        assert progress.get_course_progress("learning_spark") == original
        before = request("getDiagnosis")
        count_before = request("getLearningEvents")["totalCount"]
        state = request("getLearningCoach")
        assert state["available"] and state["maxLevel"] == 1
        assert request("getLearningEvents")["totalCount"] == count_before
        request("buildCoachPrompt", {"code": code, "level": 2}, error=True)
        prompt = request("buildCoachPrompt", {"code": code, "level": 1})
        message = request("parseCoachResponse", {
            "requestId": prompt["requestId"], "rawText": _response(prompt), "code": code,
        })
        assert request("getLearningEvents")["totalCount"] == count_before
        assert request("getLearningCoach")["maxLevel"] == 1
        args = {"messageId": message["messageId"], "code": code}
        request("runCoachProbe", args, error=True)
        assert request("ackCoachMessage", args)["recorded"] is True
        assert request("ackCoachMessage", args)["recorded"] is False
        probe = request("runCoachProbe", args)
        assert probe["engine"] == "python-reference" and probe["verified"] is True
        assert probe["rowCount"] == 2 and probe["quantitySum"] == 7
        assert request("runCoachProbe", args) == probe
        assert request("getDiagnosis")["dimensions"] == before["dimensions"]
        events = request("getLearningEvents")["events"]
        hints = [e for e in events if e["eventType"] == "hint_request"]
        probes = [e for e in events if e["eventType"] == "coach_probe"]
        assert len(hints) == len(probes) == 1
        assert hints[0]["data"]["hintType"] == "ai_coach"
        assert probes[0]["data"]["verified"] is True
        assert "PRIVATE COACH INPUT" not in json.dumps(events)
        history = request("getTaskHistory", {
            "courseId": option["courseId"], "lessonId": option["lessonId"], "taskId": option["taskId"],
        })
        probe_description = next(e for e in history["events"] if e["eventType"] == "coach_probe")
        assert "Python" in probe_description["reason"] and "Spark" in probe_description["reason"]
        assert probe_description["passed"] is None
        request("recordLearningEvent", {"eventType": "coach_probe", "data": {"verified": True}}, error=True)
        second = request("buildCoachPrompt", {"code": code, "level": 2})
        request("openCoachExercise", target)
        request("parseCoachResponse", {
            "requestId": second["requestId"], "rawText": _response(second), "code": code,
        }, error=True)
        assert request("getLearningCoach")["lastLevel"] == 1
        request("openCoachExercise", {**target, "stepId": "not_authored"}, error=True)
        assert progress.get_course_progress("learning_spark") == original


def test_coach_protocol_failed_or_edited_reply_does_not_count_help(tmp_path):
    code = "# original"
    with _server(tmp_path) as request:
        option = request("getLearningCoach")["exerciseOptions"][0]
        target = {key: option[key] for key in ("courseId", "lessonId", "stepId")}
        request("openCoachExercise", target)
        original = request("getLearningEvents")["totalCount"]
        first = request("buildCoachPrompt", {"code": code, "level": 1})
        later = request("buildCoachPrompt", {"code": code, "level": 1})
        assert request("completeCoachFailure", {"requestId": first["requestId"]})["cleared"] is False
        parsed = request("parseCoachResponse", {
            "requestId": later["requestId"], "rawText": "```json\n" + _response(later, probe=False) + "\n```", "code": code,
        })
        request("ackCoachMessage", {"messageId": parsed["messageId"], "code": "# edited"}, error=True)
        forged = request("buildCoachPrompt", {"code": code, "level": 1})
        raw = json.loads(_response(forged, probe=False))
        raw["evidenceIds"] = ["event:invented"]
        reason = request("parseCoachResponse", {
            "requestId": forged["requestId"], "rawText": json.dumps(raw), "code": code,
        }, error=True)
        assert "[COACH_RESPONSE_REFERENCES]" in reason
        assert "event:invented" not in reason
        assert request("getLearningEvents")["totalCount"] == original
        assert request("getLearningCoach")["maxLevel"] == 1
