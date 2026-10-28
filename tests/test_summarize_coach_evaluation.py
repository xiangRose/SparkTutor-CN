"""Synthetic test ratings verify imports; no human/model evaluation is claimed."""

import asyncio
import csv
from datetime import datetime, timezone
import json

import pytest

from tools import prepare_coach_evaluation as preparation
from tools import run_coach_evaluation as runner
from tools import summarize_coach_evaluation as review


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def executed_run(tmp_path):
    batch = tmp_path / "batch"
    preparation.prepare(batch, model="test-model", repeats=1)
    async def fake_call(messages, settings, *, model, base_url, coach):
        answer = json.dumps(json.loads(messages[-1]["content"])["responseExample"]) if coach else "先比较输入和预期输出。"
        return {"rawModelOutput": answer, "failureReason": None, "modelLatencyMs": 10,
                "attempts": [{"httpStatus": 200, "transportError": None, "usage": {"total_tokens": 12}}]}
    output = tmp_path / "run"
    asyncio.run(runner.run_batch(batch, output, execute=True, model="test-model",
                                base_url="https://example.invalid/v1", limit=2, call_model=fake_call))
    return output


def fill_synthetic_test_ratings(packet, reviewer_id, *, override=None):
    declaration = read_json(packet / "declaration.json")
    declaration.update(reviewerId=reviewer_id, reviewerType="human", completedIndependently=True,
                       reviewedAllItems=True, completedAt=datetime.now(timezone.utc).isoformat())
    (packet / "declaration.json").write_text(json.dumps(declaration), encoding="utf-8")
    with (packet / "ratings.csv").open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    for row in rows:
        facts = read_json(packet / row["visibleOutputReference"])
        row.update(reviewerId=reviewer_id, availability=facts["outputStatus"], reason="Synthetic test rating, not a real review")
        row.update({key: "pass" if facts["outputStatus"] == "available" else "unassessable" for key in review.DIMENSIONS})
        if facts["outputStatus"] == "unavailable":
            row["unassessableReason"] = "Synthetic unavailable output"
        if override:
            override(row)
    with (packet / "ratings.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=preparation.REVIEW_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return rows


def test_export_hides_condition_mapping_and_keeps_both_human_forms_blank(executed_run, tmp_path):
    output = tmp_path / "reviews"
    assert review.export_reviews(executed_run, output)["ratings"] == "blank"
    for number in (1, 2):
        packet = output / f"reviewer-{number}"
        declaration = read_json(packet / "declaration.json")
        assert declaration["reviewerId"] == declaration["reviewerType"] == ""
        assert declaration["completedIndependently"] is None
        with (packet / "ratings.csv").open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        assert len(rows) == 2
        for row in rows:
            assert all(row[key] == "" for key in (*review.DIMENSIONS, "reviewerId", "availability", "reason"))
            facts = read_json(packet / row["visibleOutputReference"])
            assert facts["visibleOutput"] and facts["humanReviewCriteria"]
            assert "condition" not in facts and "model" not in facts and "caseId" not in facts
    with pytest.raises(FileExistsError):
        review.export_reviews(executed_run, output)


def test_summary_keeps_reviewers_separate_denominators_unknown_cost_and_disagreements(executed_run, tmp_path):
    packets = tmp_path / "packets"
    review.export_reviews(executed_run, packets)
    first, second = packets / "reviewer-1", packets / "reviewer-2"
    fill_synthetic_test_ratings(first, "human-A")
    fill_synthetic_test_ratings(second, "human-B", override=lambda row: row.update(actionability="fail"))
    output = tmp_path / "summary"
    result = review.summarize(executed_run, first, second, output)
    assert result["cost"] == "unknown" and result["teachingEffectEvaluated"] is False
    assert result["sourceTransport"] == "injected-test-client" and result["partialRun"] is True
    assert len(result["disagreements"]) == 2
    for condition in ("Tutor", "Coach"):
        metrics = result["conditions"][condition]
        assert metrics["requestCount"] == metrics["availableOutputCount"] == 1
        action = metrics["dimensions"]["actionability"]
        assert action["perReviewer"]["human-A"]["pass"] == 1
        assert action["perReviewer"]["human-B"]["fail"] == 1
        assert action["agreement"]["cohenKappa"] is None
        assert metrics["usage"]["actualReturnedTotals"]["total_tokens"] == 12
        assert metrics["usage"]["actualReturnedTotals"]["prompt_tokens"] is None
        assert metrics["modelLatencyMs"]["median"] == 10
    assert (output / "reviewer-1-ratings.csv").read_bytes() == (first / "ratings.csv").read_bytes()
    assert (output / "summary.md").is_file()


def test_same_identity_unfilled_and_nonhuman_declarations_are_refused(executed_run, tmp_path):
    packets = tmp_path / "packets"
    review.export_reviews(executed_run, packets)
    first, second = packets / "reviewer-1", packets / "reviewer-2"
    with pytest.raises(ValueError, match="attest"):
        review.summarize(executed_run, first, second, tmp_path / "unfilled")
    fill_synthetic_test_ratings(first, "reviewer-a")
    fill_synthetic_test_ratings(second, "ＲＥＶＩＥＷＥＲ-Ａ")
    with pytest.raises(ValueError, match="distinct"):
        review.summarize(executed_run, first, second, tmp_path / "same-human")
    declaration = read_json(second / "declaration.json")
    declaration.update(reviewerId="reviewer-b", reviewerType="ai")
    (second / "declaration.json").write_text(json.dumps(declaration), encoding="utf-8")
    with pytest.raises(ValueError, match="attest"):
        review.summarize(executed_run, first, second, tmp_path / "ai-declared")


def test_changed_review_facts_and_incomplete_rows_are_refused(executed_run, tmp_path):
    packets = tmp_path / "packets"
    review.export_reviews(executed_run, packets)
    first, second = packets / "reviewer-1", packets / "reviewer-2"
    rows = fill_synthetic_test_ratings(first, "reviewer-a")
    fill_synthetic_test_ratings(second, "reviewer-b")
    item = first / rows[0]["visibleOutputReference"]
    original = item.read_bytes()
    facts = read_json(item)
    facts["visibleOutput"] = "A different prompt that was not produced"
    item.write_text(json.dumps(facts), encoding="utf-8")
    with pytest.raises(ValueError, match="facts changed"):
        review.summarize(executed_run, first, second, tmp_path / "changed")
    item.write_bytes(original)
    with (first / "ratings.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=preparation.REVIEW_FIELDS)
        writer.writeheader()
        writer.writerows(rows[:-1])
    with pytest.raises(ValueError, match="every selected"):
        review.summarize(executed_run, first, second, tmp_path / "missing-row")


def test_unavailable_outputs_are_not_counted_as_semantic_failure_or_pass(tmp_path):
    batch, output = tmp_path / "batch", tmp_path / "run"
    preparation.prepare(batch, repeats=1)
    async def failed_call(messages, settings, **kwargs):
        return {"rawModelOutput": None, "failureReason": "timeout", "modelLatencyMs": 90,
                "attempts": [{"httpStatus": None, "transportError": "TimeoutError", "usage": None}]}
    asyncio.run(runner.run_batch(batch, output, execute=True, model="test-model",
                                base_url="https://example.invalid/v1", limit=2, call_model=failed_call))
    packets = tmp_path / "packets"
    review.export_reviews(output, packets)
    first, second = packets / "reviewer-1", packets / "reviewer-2"
    fill_synthetic_test_ratings(first, "reviewer-a")
    fill_synthetic_test_ratings(second, "reviewer-b")
    result = review.summarize(output, first, second, tmp_path / "summary")
    for item in result["conditions"].values():
        assert item["availableOutputCount"] == 0 and item["firstHttpAttemptFailed"] == 1
        assert item["visibleOutputLatencyMs"]["count"] == 0
        assert item["usage"]["actualReturnedTotals"]["total_tokens"] is None
        for metric in item["dimensions"].values():
            assert all(values["assessableDenominator"] == 0 and values["unassessable"] == 1
                       and values["passRate"] is None for values in metric["perReviewer"].values())
    fill_synthetic_test_ratings(first, "reviewer-a", override=lambda row: row.update(availability="available"))
    with pytest.raises(ValueError, match="Availability"):
        review.summarize(output, first, second, tmp_path / "false-available")


def test_build_only_and_changed_execution_records_cannot_be_reviewed(tmp_path, executed_run):
    batch, offline = tmp_path / "offline-batch", tmp_path / "offline"
    preparation.prepare(batch, repeats=1)
    asyncio.run(runner.run_batch(batch, offline, limit=1))
    with pytest.raises(ValueError, match="finished execute"):
        review.export_reviews(offline, tmp_path / "offline-review")
    manifest = read_json(executed_run / "manifest.json")
    result = executed_run / manifest["results"][0]["requestId"] / "result.json"
    result.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="result changed"):
        review.export_reviews(executed_run, tmp_path / "tampered-review")


def test_agreement_reports_valid_and_degenerate_denominators():
    perfect = review.agreement(["pass", "fail", "pass", "fail"], ["pass", "fail", "pass", "fail"])
    assert perfect["cohenKappa"] == perfect["agreementRate"] == 1
    opposed = review.agreement(["pass", "fail"], ["fail", "pass"])
    assert opposed["cohenKappa"] == -1 and opposed["agreementRate"] == 0
    assert review.agreement(["pass", "pass"], ["pass", "pass"])["cohenKappa"] is None
    assert review.agreement(["unassessable"], ["pass"])["jointlyAssessableCount"] == 0
