"""Verify real account login -> HTTP RPC -> disposable Docker course assessment.

This is a scripted functional check with generated accounts, not learner data or
a teaching experiment. No Docker subprocess or course result is replaced. The
optional unavailable-service mode explicitly configures an absent executable
and verifies refusal; it never counts as a real Spark assessment.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
from http.cookiejar import CookieJar
import json
import os
from pathlib import Path
import re
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPCookieProcessor, ProxyHandler, Request, build_opener
import uuid


ROOT = Path(__file__).resolve().parents[1]
COURSES = ROOT / "src/sparktutor/courses/learning_spark/lessons"
JOB_ID = re.compile(r"sparktutor-job-[0-9a-f]{32}\Z")


class VerificationFailure(RuntimeError):
    """A fixed public code, without credentials, logs, code, or local paths."""


def _hash(value):
    return hashlib.sha256(value if isinstance(value, bytes) else value.encode("utf-8")).hexdigest()


def _source_fingerprints():
    files = {ROOT / "tools/verify_account_execution.py"}
    for relative in ("src/sparktutor/engine", "src/sparktutor/courses", "src/sparktutor/web",
                     "src/sparktutor/server", "src/sparktutor/config"):
        for base, directories, names in os.walk(ROOT / relative, followlinks=False):
            directories[:] = [name for name in directories if name not in {"__pycache__", "node_modules"}
                              and not (Path(base) / name).is_symlink()]
            for name in names:
                path = Path(base) / name
                if not path.is_symlink() and path.suffix in {".py", ".yaml", ".json"}:
                    files.add(path)
    files.add(ROOT / "src/sparktutor/cli.py")
    return {path.relative_to(ROOT).as_posix(): _hash(path.read_bytes()) for path in sorted(files)}


def _reference(relative):
    content = (COURSES / relative).read_text(encoding="utf-8")
    compile(content, relative, "exec")
    return content


def reference_variants():
    reference = _reference("01_getting_started/solution.py")
    expression = 'f.sum("Count")'
    if reference.count(expression) != 1:
        raise VerificationFailure("reference_variant_needs_review")
    wrong = reference.replace(expression, 'f.count("*")')
    compile(wrong, "mnm-deliberate-count-error", "exec")
    return reference, wrong


def _environment(home: Path, *, isolated: bool, unavailable=False):
    # A minimal environment prevents Settings.load and Docker from reading the
    # operator's real home, model configuration, registry login, or TLS keys.
    keep = {"PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "COMSPEC",
            "TEMP", "TMP", "LANG", "TZ", "LD_LIBRARY_PATH", "DOCKER_HOST"}
    env = {key: value for key, value in os.environ.items()
           if key.upper() in keep or key.startswith("LC_") or key.startswith("SPARKTUTOR_ISOLATED_")}
    env.update(HOME=str(home), USERPROFILE=str(home), XDG_CONFIG_HOME=str(home / "xdg"),
               DOCKER_CONFIG=str(home / "docker-config"), PYTHONPATH=str(ROOT / "src"),
               PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1",
               SPARKTUTOR_ISOLATED_ENABLED="true" if isolated else "false",
               SPARKTUTOR_EXECUTION_MODE="auto")
    if unavailable:
        env["SPARKTUTOR_ISOLATED_DOCKER_EXECUTABLE"] = str(home / "intentionally-unavailable-docker")
    return env


class _Client:
    def __init__(self, base, session, *, timeout=240):
        self.base, self.session, self.timeout = base, session, timeout
        self.opener = build_opener(ProxyHandler({}), HTTPCookieProcessor(CookieJar()))
        self.token = ""
        self.context = ""
        self.user_id = ""

    def request(self, path, values=None, *, token=None):
        headers = {"Content-Type": "application/json", "X-SparkTutor-Auth": "1",
                   "X-SparkTutor-Session": self.session,
                   "X-SparkTutor-Token": self.token if token is None else token}
        request = Request(self.base + path, data=json.dumps(values).encode("utf-8") if values is not None else None,
                          headers=headers)
        try:
            response = self.opener.open(request, timeout=self.timeout)
        except HTTPError as error:
            response = error
        with response:
            body = response.read().decode("utf-8")
            content = json.loads(body) if "application/json" in response.headers.get("Content-Type", "") else body
            return response.status, content

    def register_and_login(self):
        username = "verify_" + secrets.token_hex(8)
        password = secrets.token_urlsafe(32)
        status, body = self.request("/api/auth/register", {
            "username": username, "displayName": "Scripted acceptance", "password": password})
        if status != 201 or body.get("user", {}).get("role") != "student":
            raise VerificationFailure("account_registration_failed")
        status, body = self.request("/api/auth/login", {"username": username, "password": password})
        if status != 200 or not body.get("user", {}).get("id"):
            raise VerificationFailure("account_login_failed")
        self.user_id = body["user"]["id"]
        status, page = self.request("/")
        token = re.search(r'<meta\s+name="sparktutor-token"\s+content="([^"]+)"', page) if isinstance(page, str) else None
        if status != 200 or token is None:
            raise VerificationFailure("authenticated_entry_token_missing")
        self.token = token.group(1)

    def rpc_response(self, method, params=None, *, token=None):
        values = dict(params or {})
        if self.context:
            values.setdefault("contextToken", self.context)
        status, body = self.request("/api/rpc", {"method": method, "params": values}, token=token)
        if status == 200 and isinstance(body, dict):
            result = body.get("result")
            if isinstance(result, dict) and result.get("contextToken"):
                self.context = result["contextToken"]
        return status, body

    def call(self, method, params=None):
        status, body = self.rpc_response(method, params)
        if status != 200 or not isinstance(body, dict) or "result" not in body:
            raise VerificationFailure("rpc_request_rejected")
        return body["result"]


@contextmanager
def _server(temporary, *, isolated, request_timeout, unavailable=False):
    home = Path(temporary) / ("isolated-home" if isolated else "default-home")
    home.mkdir()
    data = home / "account-data"
    env = _environment(home, isolated=isolated, unavailable=unavailable)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    command = [sys.executable, "-m", "sparktutor", "web", "--host", "127.0.0.1",
               "--port", str(port), "--data-dir", str(data)]
    if isolated:
        command.append("--isolated-execution")
    with tempfile.TemporaryFile() as log:
        child = subprocess.Popen(command, cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
        try:
            client = _Client(base, uuid.uuid4().hex, timeout=2)
            deadline = time.monotonic() + 45
            while True:
                if child.poll() is not None:
                    raise VerificationFailure("account_cli_exited_before_ready")
                try:
                    status, health = client.request("/api/health")
                    if status == 200:
                        break
                except (OSError, URLError):
                    pass
                if time.monotonic() >= deadline:
                    raise VerificationFailure("account_cli_start_timeout")
                time.sleep(0.1)
            yield base, health, env
        finally:
            if child.poll() is None:
                if os.name == "posix":
                    child.send_signal(signal.SIGINT)
                else:
                    child.terminate()
                try:
                    child.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=10)


def _dimensions(client):
    result = client.call("getDiagnosis", {"courseId": "learning_spark"})
    return {item["key"]: {"score": item["score"], "sampleSize": item["sampleSize"]}
            for item in result["dimensions"]}


def _container_absent(job_id, env):
    if not isinstance(job_id, str) or not JOB_ID.fullmatch(job_id):
        return False
    executable = env.get("SPARKTUTOR_ISOLATED_DOCKER_EXECUTABLE", "docker")
    try:
        completed = subprocess.run([executable, "container", "inspect", "--format", "{{.State.Status}}", job_id],
                                   env=env, capture_output=True, text=True, timeout=15)
        return completed.returncode != 0 and any(message in completed.stderr for message in (
            "No such container", "No such object"))
    except (OSError, subprocess.TimeoutExpired):
        return False


def validate_assessment(result, last, *, expected, owner, operation, session, context, task):
    """Reject unavailable/unclean/misattributed results without a success fallback."""
    metadata = result.get("execution", {})
    if (result.get("passed") is not expected or result.get("assessmentSource") != "course_tests"
            or result.get("assessmentEligible") is not True or last.get("mode") != "isolated"):
        raise VerificationFailure("original_isolated_course_assessment_not_completed")
    expected_metadata = {"userId": owner, "operationId": operation, "sessionId": session,
                         "contextToken": context, "courseId": "learning_spark", "lessonId": task[0],
                         "stepId": task[1], "taskId": task[0] + ":" + task[1]}
    if any(metadata.get(key) != value for key, value in expected_metadata.items()):
        raise VerificationFailure("execution_identity_mismatch")
    if (metadata.get("cleanupVerified") is not True or not JOB_ID.fullmatch(str(metadata.get("jobId", "")))
            or last.get("execution") != metadata):
        raise VerificationFailure("execution_cleanup_or_result_identity_unverified")
    return metadata


def _safe_operation(name, alias, code, result, last, elapsed):
    metadata = result.get("execution", {})
    return {"name": name, "actor": alias, "inputSha256": _hash(code), "passed": result.get("passed"),
            "mode": last.get("mode"), "assessmentSource": result.get("assessmentSource"),
            "assessmentEligible": result.get("assessmentEligible"), "failureKind": result.get("failureKind"),
            "durationSeconds": round(elapsed, 3), "jobId": metadata.get("jobId"),
            "ownerSha256": _hash(str(metadata.get("userId", ""))),
            "operationId": metadata.get("operationId"), "taskId": metadata.get("taskId"),
            "sessionIdSha256": _hash(str(metadata.get("sessionId", ""))),
            "contextTokenSha256": _hash(str(metadata.get("contextToken", ""))),
            "cleanupVerified": metadata.get("cleanupVerified"), "verified": False}


def verify(output: Path, *, request_timeout=240, include_join=False, include_plan=False, expect_unavailable=False):
    output = Path(output)
    if output.exists() or output.is_symlink():
        raise ValueError("Output must be a new directory; previous evidence is never overwritten.")
    if include_plan and not include_join:
        raise ValueError("--include-plan also requires --include-join.")
    output.mkdir(parents=True)
    report = {"schemaVersion": "account-isolated-acceptance-v1", "status": "running",
              "mode": "unavailable_service_refusal" if expect_unavailable else "real_isolated_execution",
              "startedAt": datetime.now(timezone.utc).isoformat(),
              "provenance": {"isScripted": True, "realLearner": False, "modelCalls": 0,
                             "inputSource": "repository references and deliberate count error",
                             "accountsGeneratedForThisRun": True, "realPersonalConfigurationRead": False},
              "checks": [], "operations": [], "rejectedClientRequests": [],
              "sparkVerified": False, "realIsolatedAssessments": 0,
              "joinSourceAndTransferIncluded": bool(include_join), "joinPlanIncluded": bool(include_plan)}

    def check(name, passed):
        report["checks"].append({"name": name, "passed": bool(passed)})
        print(json.dumps({"check": name, "passed": bool(passed)}), file=sys.stderr, flush=True)
        if not passed:
            raise VerificationFailure(name)

    try:
        report["sourceSha256"] = _source_fingerprints()
        reference, wrong = reference_variants()
        with tempfile.TemporaryDirectory(prefix="sparktutor-account-acceptance-") as temporary:
            with _server(temporary, isolated=False, request_timeout=request_timeout) as (base, health, _):
                check("default_accounts_remain_syntax_only", health.get("accessMode") == "accounts"
                      and health.get("executionMode") == "dry_run")
                client = _Client(base, uuid.uuid4().hex, timeout=request_timeout)
                client.register_and_login()
                check("default_account_cannot_enable_host_execution",
                      client.rpc_response("setExecutionMode", {"mode": "local"})[0] == 400
                      and client.rpc_response("configureWebSettings", {"mode": "local"})[0] == 400
                      and client.call("webSettings")["mode"] == "dry_run")
            with _server(temporary, isolated=True, request_timeout=request_timeout,
                         unavailable=expect_unavailable) as (base, health, env):
                check("real_cli_starts_account_isolation", health.get("accessMode") == "accounts"
                      and health.get("executionMode") == "isolated")
                session = uuid.uuid4().hex  # Deliberately shared by both authenticated accounts.
                first, second = (_Client(base, session, timeout=request_timeout) for _ in range(2))
                first.register_and_login()
                second.register_and_login()
                check("two_accounts_have_independent_entry_tokens", first.user_id != second.user_id
                      and first.token != second.token and first.context == second.context == "")
                report["actors"] = {"A": {"ownerSha256": _hash(first.user_id)},
                                    "B": {"ownerSha256": _hash(second.user_id)}}
                for client in (first, second):
                    loaded = client.call("openCoachExercise", {"courseId": "learning_spark",
                                         "lessonId": "01_getting_started", "stepId": "15"})
                    check("authenticated_task_opened", loaded.get("step", {}).get("id") == "15")
                check("account_tokens_cannot_be_cross_used", second.rpc_response("getDiagnosis", token=first.token)[0] == 403)
                check("isolated_account_cannot_switch_to_host", first.rpc_response("setExecutionMode", {"mode": "local"})[0] == 400)
                service = first.call("getExecutionServiceStatus")
                report["executionService"] = {key: service.get(key) for key in (
                    "enabled", "ready", "mode", "image", "maxConcurrentJobs", "maxJobsPerUser")}
                initial_second = _dimensions(second)
                check("new_account_has_no_assessment", all(item["score"] is None for item in initial_second.values()))
                if expect_unavailable:
                    check("absent_execution_service_is_reported", service.get("ready") is False)
                    started = time.monotonic()
                    result = first.call("webSubmit", {"code": reference, "operationId": uuid.uuid4().hex})
                    last = first.call("webLastExecution")
                    report["operations"].append(_safe_operation("unavailable_service_refusal", "A", reference,
                                                                result, last, time.monotonic() - started))
                    check("unavailable_service_cannot_grant_course_success", result.get("passed") is False
                          and result.get("assessmentEligible") is False and result.get("failureKind") == "infrastructure"
                          and last.get("mode") == "isolated")
                    check("unavailable_service_cannot_change_knowledge", all(
                        item["score"] is None for item in _dimensions(first).values()))
                    report["operations"][-1]["verified"] = True
                else:
                    check("actual_linux_docker_service_and_image_ready", service.get("ready") is True)
                    observed_jobs = set()

                    def submit(client, alias, name, code, expected, task, supplied_owner=None):
                        started = time.monotonic()
                        operation = uuid.uuid4().hex
                        values = {"code": code, "operationId": operation}
                        if supplied_owner is not None:
                            values["userId"] = supplied_owner
                        status, body = client.rpc_response("webSubmit", values)
                        if supplied_owner is not None and status in {400, 403}:
                            report["rejectedClientRequests"].append({"kind": "spoofed_owner", "httpStatus": status})
                            check("spoofed_owner_rejected_before_job", not client.call("webLastExecution"))
                            operation = uuid.uuid4().hex
                            result = client.call("webSubmit", {"code": code, "operationId": operation})
                        else:
                            if status != 200 or not isinstance(body, dict) or "result" not in body:
                                raise VerificationFailure("real_submission_http_failure")
                            result = body["result"]
                        last = client.call("webLastExecution")
                        row = _safe_operation(name, alias, code, result, last, time.monotonic() - started)
                        report["operations"].append(row)
                        metadata = validate_assessment(result, last, expected=expected, owner=client.user_id,
                            operation=operation, session=session, context=client.context, task=task)
                        check(name + "_identity_and_original_course_tests", True)
                        check(name + "_new_job_identity", metadata["jobId"] not in observed_jobs)
                        observed_jobs.add(metadata["jobId"])
                        check(name + "_container_removed_independently", _container_absent(metadata["jobId"], env))
                        row["verified"] = True
                        report["realIsolatedAssessments"] += 1
                        return metadata

                    draft_before = second.call("getDraft")
                    task = ("01_getting_started", "15")
                    submit(first, "A", "account_a_wrong_count_rejected", wrong, False, task, supplied_owner=second.user_id)
                    first_pass = submit(first, "A", "account_a_corrected_course_passes", reference, True, task)
                    first_dimensions = _dimensions(first)
                    check("only_account_a_gains_repair_evidence", first_dimensions["knowledge"]["score"] == 100
                          and first_dimensions["debugging"]["score"] == 100 and _dimensions(second) == initial_second)
                    check("account_b_has_no_last_execution_from_a", second.call("webLastExecution") == {})
                    check("account_b_draft_is_not_overwritten_by_a", second.call("getDraft") == draft_before)
                    before_notifications = second.call("pollWebNotifications", {"after": 0})["notifications"]
                    check("account_b_notifications_have_no_a_jobs", all(
                        item["params"].get("userId", second.user_id) == second.user_id
                        and item["params"].get("jobId") not in observed_jobs for item in before_notifications))
                    second_pass = submit(second, "B", "account_b_own_course_passes", reference, True, task)
                    check("shared_tab_id_does_not_mix_account_jobs", first_pass["sessionId"] == second_pass["sessionId"]
                          and first_pass["userId"] != second_pass["userId"]
                          and first_pass["jobId"] != second_pass["jobId"]
                          and first_pass["operationId"] != second_pass["operationId"])
                    check("account_b_submission_does_not_replace_a_result", first.call("webLastExecution")["execution"] == first_pass
                          and _dimensions(first) == first_dimensions)
                    check("account_b_does_not_inherit_a_debugging_failure", _dimensions(second)["debugging"]["sampleSize"] == 0)
                    if include_join:
                        targets = [("join-cardinality-lab", "join_cardinality_solution.py")]
                        if include_plan:
                            targets.append(("join-plan-lab", "join_plan_solution.py"))
                        targets.append(("join-cardinality-transfer", "join_transfer_solution.py"))
                        for step, filename in targets:
                            first.call("openCoachExercise", {"courseId": "learning_spark",
                                       "lessonId": "07_join_strategies", "stepId": step})
                            state = first.call("getLearningCoach")
                            check(step + "_configured_coach_available", state.get("available") is True
                                  and state.get("task", {}).get("stepId") == step)
                            submit(first, "A", step + "_real_course_passes", _reference("07_join_strategies/" + filename),
                                   True, ("07_join_strategies", step))
                        transfer = _dimensions(first)["transfer"]
                        check("new_join_transfer_uses_prior_source_pass", transfer["score"] == 100 and transfer["sampleSize"] == 1)
                    for client in (first, second):
                        events = client.call("getLearningEvents", {"limit": 2000})["events"]
                        executions = [item["data"]["execution"] for item in events if item.get("data", {}).get("execution")]
                        check("stored_execution_events_are_owned_by_authenticated_account", bool(executions)
                              and all(item.get("userId") == client.user_id for item in executions))
                    check("execution_capacity_released", first.call("getExecutionServiceStatus").get("activeJobs") == 0)
                    report["finalDimensions"] = {"A": _dimensions(first), "B": _dimensions(second)}
                    report["sparkVerified"] = True
        changed = [name for name, digest in report["sourceSha256"].items()
                   if not (ROOT / name).is_file() or _hash((ROOT / name).read_bytes()) != digest]
        check("verified_product_sources_unchanged", not changed)
        report["status"] = "passed"
    except Exception as error:
        report["status"] = "failed"
        report["failureType"] = type(error).__name__
        if isinstance(error, VerificationFailure):
            report["failureCode"] = str(error)
    finally:
        report["finishedAt"] = datetime.now(timezone.utc).isoformat()
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        summary = ["# Account execution functional acceptance", "", "Status: " + report["status"],
                   "Mode: " + report["mode"], "Real isolated course assessments: " + str(report["realIsolatedAssessments"]),
                   "", "Scripted accounts and repository code; no real learner, personal configuration or model calls.",
                   "No raw code, passwords, session cookies, page tokens, service logs or local data paths are exported.",
                   "", *[f"- {'PASS' if item['passed'] else 'FAIL'}: {item['name']}" for item in report["checks"]]]
        if report.get("failureCode"):
            summary.extend(["", "Failure: " + report["failureCode"]])
        (output / "summary.md").write_text("\n".join(summary) + "\n", encoding="utf-8")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--request-timeout", type=int, default=240)
    parser.add_argument("--include-join", action="store_true", help="Also execute the new Join source and transfer tasks.")
    parser.add_argument("--include-plan", action="store_true", help="With --include-join, also execute real-plan course tests.")
    parser.add_argument("--expect-unavailable", action="store_true", help="Test real refusal with an explicitly absent Docker executable.")
    args = parser.parse_args(argv)
    if not 10 <= args.request_timeout <= 900:
        parser.error("--request-timeout must be 10..900 seconds")
    try:
        report = verify(args.output, request_timeout=args.request_timeout, include_join=args.include_join,
                        include_plan=args.include_plan, expect_unavailable=args.expect_unavailable)
    except ValueError as error:
        parser.error(str(error))
    print(json.dumps({"status": report["status"], "mode": report["mode"], "checks": len(report["checks"]),
                      "realIsolatedAssessments": report["realIsolatedAssessments"], "sparkVerified": report["sparkVerified"],
                      "modelCalls": 0}))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
