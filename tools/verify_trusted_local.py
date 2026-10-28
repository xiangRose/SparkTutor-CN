"""Verify a real CLI -> HTTP -> Spark -> assessment -> diagnosis demonstration.

Only repository-authored reference implementations and deliberate error variants
are used. No learner database or AI configuration is opened. The report is a
functional acceptance record, never a human learning experiment.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import URLError
from urllib.request import Request, urlopen
import uuid

ROOT = Path(__file__).resolve().parents[1]
COURSE = ROOT / "src/sparktutor/courses/learning_spark/lessons"


def _hash(content):
    return hashlib.sha256(content).hexdigest()


def reference_variants():
    """Keep deliberate errors in the actual task functions, never the tests."""
    definitions = (("01_getting_started/solution.py", 'f.sum("Count")'),
                   ("11_diagnosis_practice/sensor_transfer_solution.py", 'f.sum("reading_count")'))
    variants = []
    for relative, expression in definitions:
        reference = (COURSE / relative).read_text(encoding="utf-8")
        if reference.count(expression) != 1:
            raise ValueError("The authored demonstration must be updated for the current references.")
        wrong = reference.replace(expression, 'f.count("*")')
        compile(reference, relative, "exec")
        compile(wrong, relative + ":count-error", "exec")
        variants.append((reference, wrong))
    return variants


def verify(output: Path, *, request_timeout=180):
    if output.exists():
        raise ValueError("Output must be a new directory; previous evidence is never overwritten.")
    output.mkdir(parents=True)
    report = {
        "schemaVersion": "trusted-local-acceptance-v1", "status": "running",
        "startedAt": datetime.now(timezone.utc).isoformat(),
        "provenance": {"scriptedDemonstration": True, "realLearner": False,
                       "inputSource": "repository references and deliberate error variants",
                       "originalCourseTests": True, "modelCalls": 0},
        "checks": [], "operations": [], "sparkVerified": False,
    }
    sources = ["src/sparktutor/web/server.py", "src/sparktutor/web/runtime.py",
               "src/sparktutor/server/handler.py", "src/sparktutor/engine/evaluator.py",
               "src/sparktutor/engine/diagnosis.py", "src/sparktutor/engine/exercise_validation.py",
               "src/sparktutor/courses/learning_spark/lessons/01_getting_started/starter.py",
               "src/sparktutor/courses/learning_spark/lessons/11_diagnosis_practice/sensor_transfer_starter.py",
               "src/sparktutor/courses/learning_spark/lessons/01_getting_started/solution.py",
               "src/sparktutor/courses/learning_spark/lessons/11_diagnosis_practice/sensor_transfer_solution.py",
               "src/sparktutor/cli.py", "tools/verify_trusted_local.py"]
    report["sourceSha256"] = {name: _hash((ROOT / name).read_bytes()) for name in sources}
    (a, wrong_a), (b, wrong_b) = reference_variants()

    def check(name, passed):
        report["checks"].append({"name": name, "passed": bool(passed)})
        if not passed:
            raise AssertionError(name)

    with tempfile.TemporaryDirectory(prefix="sparktutor-http-acceptance-") as temporary:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        env = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONIOENCODING": "utf-8",
               "SPARK_LOCAL_IP": "127.0.0.1", "PYSPARK_PYTHON": sys.executable,
               "PYSPARK_DRIVER_PYTHON": sys.executable}
        # Isolate model credentials even if the calling shell has a provider set.
        for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "SPARKTUTOR_OPENAI_API_KEY",
                    "SPARKTUTOR_OPENAI_BASE_URL", "SPARKTUTOR_OPENAI_MODEL"):
            env.pop(key, None)
        with tempfile.TemporaryFile() as log:
            child = subprocess.Popen(
                [sys.executable, "-m", "sparktutor", "web", "--trusted-local", "--port", str(port),
                 "--data-dir", str(Path(temporary) / "workspace")], cwd=ROOT, env=env,
                stdin=subprocess.DEVNULL, stdout=log, stderr=log,
            )
            try:
                ready_until = time.monotonic() + 45
                while True:
                    if child.poll() is not None:
                        raise RuntimeError("The trusted-local CLI exited before becoming ready.")
                    try:
                        with urlopen(base + "/api/health", timeout=2) as response:
                            health = json.load(response)
                        break
                    except (OSError, URLError):
                        if time.monotonic() > ready_until:
                            raise RuntimeError("The trusted-local CLI did not become ready in 45 seconds.")
                        time.sleep(0.15)
                check("CLI starts loopback trusted-local execution", health.get("accessMode") == "trusted_local"
                      and health.get("executionMode") == "local")
                with urlopen(base, timeout=10) as response:
                    page = response.read().decode("utf-8")
                token_match = re.search(r'<meta\s+name="sparktutor-token"\s+content="([^"]+)"', page)
                check("Actual browser entry supplies a session token", token_match is not None)
                token = token_match.group(1)
                session = uuid.uuid4().hex
                context = None

                def call(method, params=None):
                    nonlocal context
                    values = dict(params or {})
                    if context:
                        values["contextToken"] = context
                    request = Request(base + "/api/rpc", data=json.dumps({
                        "method": method, "params": values}).encode("utf-8"), headers={
                            "Content-Type": "application/json", "X-SparkTutor-Token": token,
                            "X-SparkTutor-Session": session,
                        })
                    with urlopen(request, timeout=request_timeout) as response:
                        result = json.load(response)["result"]
                    if isinstance(result, dict) and result.get("contextToken"):
                        context = result["contextToken"]
                    return result

                def submit(name, code, expected):
                    started = time.monotonic()
                    result = call("webSubmit", {"code": code, "operationId": uuid.uuid4().hex})
                    execution = call("webLastExecution")
                    report["operations"].append({
                        "name": name, "inputSha256": _hash(code.encode("utf-8")),
                        "passed": result.get("passed"), "mode": execution.get("mode"),
                        "assessmentSource": result.get("assessmentSource"),
                        "assessmentEligible": result.get("assessmentEligible"),
                        "failureKind": result.get("failureKind"),
                        "durationSeconds": round(time.monotonic() - started, 3),
                    })
                    check(name + ": original course assessment completed", result.get("assessmentSource") == "course_tests"
                          and result.get("assessmentEligible") is True and execution.get("mode") == "local")
                    check(name + ": expected result", result.get("passed") is expected)

                def dimensions():
                    diagnosis = call("getDiagnosis", {"courseId": "learning_spark"})
                    return diagnosis, {item["key"]: item for item in diagnosis["dimensions"]}

                call("openCoachExercise", {"courseId": "learning_spark", "lessonId": "01_getting_started", "stepId": "15"})
                submit("M&M count instead of quantity is rejected", wrong_a, False)
                call("getHint")
                submit("M&M corrected quantity passes", a, True)
                diagnosis, dims = dimensions()
                check("Actual repair updates knowledge and debugging evidence", dims["knowledge"]["score"] == 100
                      and dims["debugging"]["score"] == 100)
                recommendation = diagnosis.get("recommendedExercise")
                check("A fresh transfer task is recommended", recommendation and recommendation.get("stepId") == "sensor_transfer")
                call("openRecommendedExercise", {key: recommendation[key] for key in ("courseId", "lessonId", "stepId")})
                submit("Sensor first transfer error is rejected", wrong_b, False)
                submit("Sensor retry passes course tests", b, True)
                _, dims = dimensions()
                check("Retry cannot rewrite first transfer evidence", dims["transfer"]["score"] == 0
                      and dims["transfer"]["sampleSize"] == 1)
                check("Repair restores latest knowledge and debugging evidence", dims["knowledge"]["score"] == 100
                      and dims["debugging"]["score"] == 100)
                report["finalDimensions"] = [{"key": key, "score": item["score"], "sampleSize": item["sampleSize"]}
                                             for key, item in dims.items()]
                report.update(status="passed", sparkVerified=True)
            except Exception as error:
                report.update(status="failed", failureType=type(error).__name__)
                raise
            finally:
                if child.poll() is None:
                    child.terminate()
                    try:
                        child.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        child.wait(timeout=10)
                report["finishedAt"] = datetime.now(timezone.utc).isoformat()
                (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                summary = ["# Trusted-local HTTP acceptance", "", "Status: " + report["status"],
                           "", "Repository-authored scripted demonstration; no human learner and no model calls.",
                           "", *[f"- {'PASS' if item['passed'] else 'FAIL'}: {item['name']}" for item in report["checks"]]]
                (output / "summary.md").write_text("\n".join(summary) + "\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = verify(args.output)
    print(json.dumps({"status": report["status"], "checks": len(report["checks"]),
                      "sparkOperations": len(report["operations"]), "modelCalls": 0}))


if __name__ == "__main__":
    main()
