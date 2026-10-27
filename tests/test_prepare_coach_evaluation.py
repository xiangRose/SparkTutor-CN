"""The preparation tool freezes authored cases without running or scoring them."""

from collections import Counter
import csv
import hashlib
import importlib.util
import json
from pathlib import Path
import socket

import pytest


TOOL_PATH = Path(__file__).resolve().parents[1] / "tools" / "prepare_coach_evaluation.py"
spec = importlib.util.spec_from_file_location("prepare_coach_evaluation", TOOL_PATH)
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_freezes_all_cases_and_balances_the_72_request_plan_without_fabricated_results(tmp_path):
    output = tmp_path / "batch"
    manifest = tool.prepare(output, model="example-model", seed=73)
    frozen = (output / "cases.frozen.json").read_bytes()
    assert frozen == tool.CASES_PATH.read_bytes()
    assert manifest["caseSet"]["sha256"] == hashlib.sha256(frozen).hexdigest()
    assert manifest["caseSet"]["caseCount"] == 12
    cases = json.loads(frozen)["cases"]
    plan = read_json(output / "request-plan.json")
    assert len(plan) == manifest["plan"]["requestCount"] == 72
    assert Counter((row["caseId"], row["condition"]) for row in plan) == Counter({
        (case["id"], condition): 3 for case in cases for condition in ("Tutor", "Coach")
    })
    assert len({row["requestId"] for row in plan}) == len({row["reviewItemId"] for row in plan}) == 72
    for first, second in zip(plan[::2], plan[1::2]):
        assert (first["caseId"], first["repeat"]) == (second["caseId"], second["repeat"])
        assert {first["condition"], second["condition"]} == {"Tutor", "Coach"}
    assert {row["condition"] for row in plan[::2]} == {"Tutor", "Coach"}
    for row in plan:
        assert row["status"] == "not_run"
        assert row["rawModelOutput"] is row["visibleOutput"] is row["actualInputSha256"] is None
        assert row["parseStatus"] is row["failureReason"] is None
        assert "expected" not in row and "messages" not in row and "title" not in row
        assert row["coachLevel"] == (1 if row["condition"] == "Coach" else None)
    assert all(case["results"] == [] and case["evaluationStatus"] == "not_run" for case in cases)
    assert manifest["evaluationStatus"] == "not_run" and manifest["preparationOnly"]
    assert manifest["model"]["requestedId"] == "example-model"
    assert manifest["model"]["resolvedId"] == manifest["model"]["fixedSnapshot"] == "unknown"
    assert manifest["model"]["selectionVerified"] is False
    for name, digest in manifest["artifactSha256"].items():
        assert hashlib.sha256((output / name).read_bytes()).hexdigest() == digest


def test_independent_review_templates_are_blank_and_do_not_identify_conditions(tmp_path):
    output = tmp_path / "batch"
    manifest = tool.prepare(output)
    plan = read_json(output / "request-plan.json")
    for reviewer in (1, 2):
        with (output / f"reviewer-{reviewer}.csv").open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        assert len(rows) == 72
        assert {row["reviewItemId"] for row in rows} == {row["reviewItemId"] for row in plan}
        assert all(value == "" for row in rows for key, value in row.items() if key != "reviewItemId")
        assert not any(key in rows[0] for key in ("condition", "caseId", "model"))
    assert manifest["model"]["requestedId"] == "unknown"
    assert manifest["commonParameters"]["temperature"] == manifest["commonParameters"]["top_p"] == "provider-default"
    assert manifest["commonParameters"]["seed"] == "provider-default"
    assert manifest["commonParameters"]["max_output_tokens"] == "unknown"
    assert manifest["commonParameters"]["actualSentParameters"] is None
    assert manifest["plan"]["orderingSeedIsModelSeed"] is False


def test_plan_is_reproducible_changes_with_seed_and_refuses_overwrites(tmp_path):
    tool.prepare(tmp_path / "first", seed=11)
    tool.prepare(tmp_path / "same", seed=11)
    tool.prepare(tmp_path / "different", seed=12)
    for name in ("request-plan.json", "reviewer-1.csv", "reviewer-2.csv"):
        assert (tmp_path / "first" / name).read_bytes() == (tmp_path / "same" / name).read_bytes()
    assert (tmp_path / "first" / "request-plan.json").read_bytes() != (tmp_path / "different" / "request-plan.json").read_bytes()
    before = (tmp_path / "first" / "manifest.json").read_bytes()
    with pytest.raises(FileExistsError):
        tool.prepare(tmp_path / "first")
    assert (tmp_path / "first" / "manifest.json").read_bytes() == before


def test_cli_only_prepares_offline_and_does_not_read_personal_configuration(tmp_path, monkeypatch, capsys):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("Preparation must not open sockets or locate personal data")
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(Path, "home", forbidden)
    output = tmp_path / "offline"
    assert tool.main(["--output", str(output), "--repeats", "1", "--seed", "9"]) == 0
    manifest = read_json(output / "manifest.json")
    assert manifest["networkRequestsMade"] == manifest["modelInvocationsMade"] == 0
    assert manifest["realLearnerDataIncluded"] is False
    assert manifest["plan"]["requestCount"] == 24
    assert "evaluation not run" in capsys.readouterr().out


@pytest.mark.parametrize("repeats", [0, -1, 1001, True, 1.5])
def test_invalid_repetitions_do_not_create_a_directory(tmp_path, repeats):
    with pytest.raises(ValueError):
        tool.prepare(tmp_path / "batch", repeats=repeats)
    assert not (tmp_path / "batch").exists()


def test_executed_or_personal_cases_are_rejected_before_writing(tmp_path, monkeypatch):
    document = read_json(tool.CASES_PATH)
    document["provenance"]["realLearnerData"] = True
    changed = tmp_path / "invalid-cases.json"
    changed.write_text(json.dumps(document), encoding="utf-8")
    monkeypatch.setattr(tool, "CASES_PATH", changed)
    with pytest.raises(ValueError, match="synthetic"):
        tool.prepare(tmp_path / "batch")
    assert not (tmp_path / "batch").exists()
