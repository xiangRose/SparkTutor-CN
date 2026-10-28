"""Evaluation adapters use real builders and parsers, without real model calls."""

import asyncio
from copy import deepcopy
import json
from pathlib import Path
import socket

import pytest

from tools import prepare_coach_evaluation as preparation
from tools import run_coach_evaluation as runner


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_build_only_uses_actual_product_inputs_without_network_or_evaluator_labels(tmp_path, monkeypatch):
    batch = tmp_path / "batch"
    preparation.prepare(batch, repeats=1)
    def forbidden(*args, **kwargs):
        raise AssertionError("Offline evaluation must not contact models")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(runner, "_call_model", forbidden)
    output = tmp_path / "offline"
    summary = asyncio.run(runner.run_batch(batch, output, limit=4))
    assert summary["mode"] == "build-only" and summary["evaluationStatus"] == "not_run"
    assert summary["networkRequestsMade"] == 0 and summary["plannedRequests"] == 24
    assert summary["builtInputCount"] == summary["selectedRequests"] == 4
    assert summary["completedRequests"] == 0 and not (output / "review-output").exists()
    assert not (output / "reviewer-1.csv").exists()
    assert summary["realLearnerDataIncluded"] is False and summary["cost"] == "unknown"
    for row in summary["results"]:
        result = read_json(output / row["requestId"] / "request-status.json")
        actual = read_json(output / row["requestId"] / "input.json")
        assert result["rawModelOutput"] is result["visibleOutput"] is None
        assert result["status"] == "not_run" and result["attempts"] == []
        assert result["actualInputSha256"]
        assert len(actual["messages"]) == 2
        if row["condition"] == "Coach":
            context = json.loads(actual["messages"][-1]["content"])
            assert context["level"] == 1 and context["currentCode"] is not None
            assert "diagnosisBefore" not in context and "expected" not in context


def test_each_prompt_uses_only_its_own_fixture_events_and_hides_label_sentinels(tmp_path):
    cases = read_json(preparation.CASES_PATH)["cases"]
    case = deepcopy(cases[0])
    case["title"] = "SECRET_CASE_LABEL"
    case["input"]["description"] = "SECRET_DESCRIPTION_LABEL"
    case["expected"] = {"requiredObservations": ["SECRET_EXPECTED_LABEL"]}
    for condition in ("Tutor", "Coach"):
        handler, built, code = asyncio.run(runner.build_product_input(case, condition, tmp_path / condition))
        sent = json.dumps(built["messages"], ensure_ascii=False)
        assert not any(label in sent for label in ("SECRET_CASE_LABEL", "SECRET_DESCRIPTION_LABEL", "SECRET_EXPECTED_LABEL"))
        assert code == case["input"]["code"]
        observed = {event.event_id for event in handler.events.list_events(limit=None)
                    if event.event_id.startswith("fixture-")}
        assert observed == {event["eventId"] for event in case["input"]["events"]}
        assert handler.settings.data_dir == tmp_path / condition
        if condition == "Coach":
            context = json.loads(built["messages"][-1]["content"])
            assert context["dimensions"]  # Computed by the product, not copied from expected.
            assert any(e["id"] == "event:" + case["input"]["events"][0]["eventId"] for e in context["evidence"])


def test_null_case_code_remains_empty_without_reference_solution(tmp_path):
    case = next(c for c in read_json(preparation.CASES_PATH)["cases"] if c["input"]["code"] is None)
    _, built, code = asyncio.run(runner.build_product_input(case, "Coach", tmp_path / "state"))
    assert code == "" and json.loads(built["messages"][-1]["content"])["currentCode"] == ""


def test_actual_coach_parser_accepts_legal_choice_and_preserves_failed_attempts(tmp_path):
    batch = tmp_path / "batch"
    preparation.prepare(batch, model="test-model", repeats=1)
    async def fake_call(messages, settings, *, model, base_url, coach):
        raw = json.dumps(json.loads(messages[-1]["content"])["responseExample"]) if coach else "先核对输入和预期结果。"
        return {"rawModelOutput": raw, "failureReason": None, "modelLatencyMs": 12,
                "attempts": [{"httpStatus": 400, "usage": None},
                             {"httpStatus": 200, "usage": {"total_tokens": 30}}]}
    output = tmp_path / "run"
    summary = asyncio.run(runner.run_batch(batch, output, execute=True, model="test-model",
                                            base_url="https://model.example/v1", limit=2, call_model=fake_call))
    assert summary["networkRequestsMade"] == 4
    assert summary["requestStatusCounts"] == {"completed": 2}
    assert summary["evaluationStatus"] == "model_calls_finished_manual_review_pending"
    for row in summary["results"]:
        result = read_json(output / row["requestId"] / "result.json")
        assert result["rawModelOutput"] and result["visibleOutput"]
        assert result["attempts"][0]["httpStatus"] == 400
        assert result["semanticReview"] == "pending" and result["usage"] == [{"total_tokens": 30}]
        if row["condition"] == "Coach":
            assert result["parseStatus"] == "contract_valid_semantics_unreviewed"
            assert result["coachMessage"]["hypothesisId"] == "uncertain"
        review = (output / "review-output" / (row["reviewItemId"] + ".txt")).read_text(encoding="utf-8")
        assert review == result["visibleOutput"] and "test-model" not in review
    assert read_json(batch / "manifest.json")["evaluationStatus"] == "not_run"


def test_malformed_model_choices_are_failures_without_fabricated_visible_answers(tmp_path):
    batch = tmp_path / "batch"
    preparation.prepare(batch, repeats=1)
    async def fake_call(messages, settings, **kwargs):
        return {"rawModelOutput": "invalid-json", "failureReason": None,
                "attempts": [{"httpStatus": 200, "usage": None}], "modelLatencyMs": 1}
    output = tmp_path / "run"
    summary = asyncio.run(runner.run_batch(batch, output, execute=True, model="test-model",
                                            base_url="https://model.example/v1", limit=2, call_model=fake_call))
    coach_row = next(row for row in summary["results"] if row["condition"] == "Coach")
    result = read_json(output / coach_row["requestId"] / "result.json")
    assert result["status"] == "failed" and result["parseStatus"] == "rejected"
    assert result["rawModelOutput"] == "invalid-json" and result["visibleOutput"] is None
    assert result["usage"] is None and result["failureReason"].startswith("COACH_RESPONSE_")


def test_changed_batch_model_and_existing_outputs_are_rejected_before_calls(tmp_path):
    batch = tmp_path / "batch"
    preparation.prepare(batch, model="frozen-model", repeats=1)
    with pytest.raises(ValueError, match="differs"):
        asyncio.run(runner.run_batch(batch, tmp_path / "wrong-model", execute=True,
                                    model="other-model", base_url="https://model.example/v1"))
    assert not (tmp_path / "wrong-model").exists()
    (tmp_path / "existing").mkdir()
    with pytest.raises(FileExistsError):
        asyncio.run(runner.run_batch(batch, tmp_path / "existing"))
    (batch / "cases.frozen.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="changed"):
        runner.read_batch(batch)


def test_recording_opener_captures_usage_and_body_without_headers():
    from urllib.request import Request
    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self, limit):
            return json.dumps({"model":"returned-model", "usage":{"total_tokens":10,"secret":"hidden"}}).encode()
    trace = runner.RecordingOpener(lambda request, timeout: Response())
    request = Request("https://example.invalid/chat/completions", data=b'{"model":"test","messages":[]}',
                      headers={"Authorization":"Bearer DO_NOT_EXPORT"})
    with trace(request, timeout=90) as response:
        response.read(200)
    assert trace.attempts[0]["usage"] == {"total_tokens":10}
    assert trace.attempts[0]["returnedModel"] == "returned-model"
    assert "DO_NOT_EXPORT" not in json.dumps(trace.attempts)
    assert "example.invalid" not in json.dumps(trace.attempts)


def test_optional_matched_control_preserves_primary_plan_and_uses_same_evidence(tmp_path):
    primary = tmp_path / "primary"
    additional = tmp_path / "additional"
    first = preparation.prepare(primary, seed=42)
    matched = preparation.prepare(additional, seed=42, include_matched_evidence=True)
    assert first["plan"]["requestCount"] == 72
    assert matched["plan"]["requestCount"] == 108
    assert matched["plan"]["conditions"] == ["Tutor", "Coach", "MatchedTutor"]
    case = deepcopy(read_json(preparation.CASES_PATH)["cases"][0])
    case["expected"]["hiddenSentinel"] = "SECRET_EXPECTED_MATCHED"
    case["input"]["description"] = "SECRET_DESCRIPTION_MATCHED"
    _, coach, _ = asyncio.run(runner.build_product_input(case, "Coach", tmp_path / "coach"))
    _, free, _ = asyncio.run(runner.build_product_input(case, "MatchedTutor", tmp_path / "free"))
    assert runner.evidence_context(coach["messages"]) == free["matchedEvidence"]
    sent = json.dumps(free["messages"], ensure_ascii=False)
    assert "SECRET_EXPECTED_MATCHED" not in sent and "SECRET_DESCRIPTION_MATCHED" not in sent
    assert "responseExample" not in free["matchedEvidence"]
    assert "allowedHypotheses" not in free["matchedEvidence"]
    assert case["input"]["question"] in free["messages"][-1]["content"]


def test_response_trace_keeps_cache_and_reasoning_counts_but_not_private_reasoning():
    class Response:
        def read(self, limit):
            return json.dumps({"model": "deepseek-flash", "system_fingerprint": "fp_test",
                "usage": {"prompt_tokens": 30, "completion_tokens": 9, "total_tokens": 39,
                          "prompt_cache_hit_tokens": 20, "prompt_cache_miss_tokens": 10,
                          "completion_tokens_details": {"reasoning_tokens": 4, "private": "DO_NOT_KEEP"},
                          "prompt_tokens_details": {"cached_tokens": 20, "private": "DO_NOT_KEEP"}},
                "choices": [{"finish_reason": "stop", "message": {
                    "reasoning_content": "PRIVATE_REASONING",
                    "content": [{"type": "text", "text": "visible", "private": "DO_NOT_KEEP"},
                                {"type": "thinking", "text": "PRIVATE_REASONING"}]}}]}).encode()
    record = {}
    runner._ResponseTrace(Response(), record).read(65536)
    assert record["usage"]["prompt_cache_hit_tokens"] == 20
    assert record["usage"]["prompt_cache_miss_tokens"] == 10
    assert record["usage"]["completion_tokens_details"] == {"reasoning_tokens": 4}
    assert record["systemFingerprint"] == "fp_test"
    assert record["returnedContent"] == [{"type": "text", "text": "visible"}]
    assert "DO_NOT_KEEP" not in json.dumps(record) and "PRIVATE_REASONING" not in json.dumps(record)
