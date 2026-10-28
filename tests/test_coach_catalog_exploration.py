"""Only synthetic clients validate the separate six-example exploration tool."""

import ast
import asyncio
import json

import pytest

from tools import run_coach_catalog_exploration as exploration


def test_fresh_examples_use_real_task_signatures_and_no_fixture_execution_events():
    cases = exploration.fresh_cases()
    assert len(cases) == 6 and len({case["id"] for case in cases}) == 6
    assert {case["topic"] for case in cases} == {"schema", "window", "join"}
    for case in cases:
        starter = (exploration.ROOT / case["starterSource"]).read_text(encoding="utf-8")
        student = ast.parse(case["input"]["code"])
        starter_tree = ast.parse(starter)
        function = [node for node in student.body if isinstance(node, ast.FunctionDef)][-1]
        original = next(node for node in starter_tree.body if isinstance(node, ast.FunctionDef) and node.name == function.name)
        assert ast.dump(function.args) == ast.dump(original.args)
        assert not [node for node in student.body if isinstance(node, ast.If)]
        assert case["input"]["events"] == [] and case["task"]["taskId"].endswith(":" + case["task"]["stepId"])
        compile(case["input"]["code"], case["id"], "exec")


def test_six_actual_builders_parse_fake_answers_without_synthetic_scores_or_label_leak(tmp_path):
    seen = []
    async def fake_call(messages, settings, **kwargs):
        context = json.loads(messages[-1]["content"])
        assert all(item["score"] is None for item in context["dimensions"])
        assert all(not entry["id"].startswith("event:") for entry in context["evidence"])
        assert "humanReviewOnly" not in messages[-1]["content"]
        seen.append(context["task"]["stepId"])
        return {"rawModelOutput": json.dumps(context["responseExample"]), "failureReason": None,
                "modelLatencyMs": 1, "attempts": [{"httpStatus": 200, "usage": None}]}
    output = tmp_path / "six"
    result = asyncio.run(exploration.run_exploration(output, execute=True, caller=fake_call))
    assert len(seen) == result["caseCount"] == result["completedRequests"] == result["httpAttempts"] == 6
    assert result["humanSemanticReview"] == "not_reviewed" and result["transport"] == "injected-test-client"
    assert all(row["parseStatus"] == "contract_valid_semantics_unreviewed" for row in result["results"])
    frozen = json.loads((output / "cases.frozen.json").read_text(encoding="utf-8"))
    assert frozen["provenance"]["notPristineStatisticalHoldout"] is True
    assert frozen["provenance"]["sparkExecutionEvidenceProvided"] is False


def test_default_does_not_call_models_and_refuses_to_overwrite(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Freezing examples must not call a model")
    monkeypatch.setattr(exploration, "_call_model", forbidden)
    output = tmp_path / "six"
    result = asyncio.run(exploration.run_exploration(output))
    assert result["completedRequests"] == result["httpAttempts"] == 0 and result["status"] == "not_run"
    original = (output / "manifest.json").read_bytes()
    with pytest.raises(FileExistsError):
        asyncio.run(exploration.run_exploration(output))
    assert (output / "manifest.json").read_bytes() == original
