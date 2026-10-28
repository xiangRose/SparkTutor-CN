"""Offline checks for evidence validation; no successful Docker run is simulated."""

from copy import deepcopy
import importlib.util
import json
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "tools/verify_account_execution.py"
spec = importlib.util.spec_from_file_location("verify_account_execution", SCRIPT)
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


def observed():
    metadata = {"userId": "actor-a", "operationId": "operation-one", "sessionId": "same-tab-session",
                "contextToken": "task-context", "courseId": "learning_spark", "lessonId": "lesson",
                "stepId": "task", "taskId": "lesson:task", "jobId": "sparktutor-job-" + "a" * 32,
                "cleanupVerified": True}
    result = {"passed": True, "assessmentSource": "course_tests", "assessmentEligible": True,
              "execution": metadata}
    last = {"mode": "isolated", "execution": deepcopy(metadata)}
    return result, last


def validate(result, last):
    return tool.validate_assessment(result, last, expected=True, owner="actor-a", operation="operation-one",
                                    session="same-tab-session", context="task-context", task=("lesson", "task"))


@pytest.mark.parametrize("corruption", ["local", "dry_run", "ineligible", "model_review", "wrong_owner",
    "wrong_operation", "wrong_session", "stale_context", "wrong_task", "missing_job", "unclean", "different_last"])
def test_evidence_validator_refuses_non_isolated_or_misattributed_results(corruption):
    result, last = observed()
    if corruption in {"local", "dry_run"}: last["mode"] = corruption
    elif corruption == "ineligible": result["assessmentEligible"] = False
    elif corruption == "model_review": result["assessmentSource"] = "ai_review"
    elif corruption == "different_last": last["execution"]["jobId"] = "sparktutor-job-" + "b" * 32
    elif corruption == "missing_job": result["execution"].pop("jobId")
    elif corruption == "unclean": result["execution"]["cleanupVerified"] = False
    else:
        field = {"wrong_owner": "userId", "wrong_operation": "operationId", "wrong_session": "sessionId",
                 "stale_context": "contextToken", "wrong_task": "taskId"}[corruption]
        result["execution"][field] = "different"
    with pytest.raises(tool.VerificationFailure):
        validate(result, last)


def test_observation_export_has_hashes_but_no_codes_credentials_paths_or_logs():
    result, last = observed()
    last.update(stdout="PRIVATE_PASSWORD and /private/local-path", stderr="PRIVATE_LOG")
    exported = tool._safe_operation("authored-check", "A", "PRIVATE_CODE", result, last, 1.5)
    rendered = json.dumps(exported)
    for private in ("PRIVATE_PASSWORD", "PRIVATE_CODE", "PRIVATE_LOG", "/private/local-path", "task-context", "actor-a"):
        assert private not in rendered
    assert exported["ownerSha256"] == tool._hash("actor-a")
    assert exported["contextTokenSha256"] == tool._hash("task-context")
    assert exported["verified"] is False


def test_child_environment_uses_new_home_and_does_not_forward_model_or_docker_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "PRIVATE_KEY")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "PRIVATE_KEY")
    monkeypatch.setenv("SPARKTUTOR_OPENAI_API_KEY", "PRIVATE_KEY")
    monkeypatch.setenv("DOCKER_CONFIG", "/personal/docker")
    monkeypatch.setenv("DOCKER_CERT_PATH", "/personal/certs")
    monkeypatch.setenv("DOCKER_HOST", "unix:///local-test-socket")
    env = tool._environment(tmp_path, isolated=True)
    assert env["HOME"] == env["USERPROFILE"] == str(tmp_path)
    assert env["DOCKER_CONFIG"] == str(tmp_path / "docker-config")
    assert env["DOCKER_HOST"] == "unix:///local-test-socket"
    assert not any("PRIVATE_KEY" in value or "/personal/" in value for value in env.values())
    assert "DOCKER_CERT_PATH" not in env
    assert tool._environment(tmp_path, isolated=False)["SPARKTUTOR_ISOLATED_ENABLED"] == "false"


def test_output_refuses_existing_evidence_without_launching_a_server(tmp_path):
    output = tmp_path / "existing"
    output.mkdir()
    (output / "keep.txt").write_text("original", encoding="utf-8")
    with pytest.raises(ValueError, match="new directory"):
        tool.verify(output)
    assert (output / "keep.txt").read_text(encoding="utf-8") == "original"
    assert not (output / "report.json").exists()


def test_reference_error_changes_real_aggregation_and_both_versions_compile():
    reference, wrong = tool.reference_variants()
    assert reference != wrong
    assert 'f.sum("Count")' in reference and 'f.sum("Count")' not in wrong
    assert 'f.count("*")' in wrong
    compile(reference, "reference", "exec")
    compile(wrong, "deliberate-error", "exec")


def test_real_cli_unavailable_service_is_refused_without_model_or_fake_spark_results(tmp_path):
    output = tmp_path / "refusal-check"
    report = tool.verify(output, request_timeout=30, expect_unavailable=True)
    assert report["status"] == "passed", report.get("failureCode", report.get("failureType"))
    assert report["mode"] == "unavailable_service_refusal"
    assert report["sparkVerified"] is False and report["realIsolatedAssessments"] == 0
    assert report["provenance"]["modelCalls"] == 0 and report["provenance"]["realLearner"] is False
    assert report["operations"][0]["mode"] == "isolated"
    assert report["operations"][0]["passed"] is False
    assert report["operations"][0]["assessmentEligible"] is False
    assert all(check["passed"] for check in report["checks"])
    assert json.loads((output / "report.json").read_text(encoding="utf-8")) == report
