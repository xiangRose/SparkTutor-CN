"""Price arithmetic uses synthetic usage, never claims an actual model bill."""

from copy import deepcopy
import json
from pathlib import Path

import pytest

from tools import estimate_coach_evaluation_cost as cost


QUOTE = Path(__file__).resolve().parents[1] / "tools/pricing/deepseek-flash-20261028.json"


def set_synthetic_usage(monkeypatch, attempts, *, model="deepseek-flash"):
    monkeypatch.setattr(cost, "read_run", lambda _: ({"model": model, "transport": "injected-test-client"},
                        {"review-1": {"attempts": attempts}}, "test-run-hash"))


def test_quote_range_uses_actual_split_without_double_counting_reasoning(monkeypatch):
    usage = {"prompt_tokens": 2000000, "prompt_cache_hit_tokens": 1000000,
             "prompt_cache_miss_tokens": 1000000, "completion_tokens": 1000000,
             "completion_tokens_details": {"reasoning_tokens": 500000}}
    set_synthetic_usage(monkeypatch, [{"usage": usage}])
    result = cost.estimate("unused-test-run", QUOTE)
    assert result["estimatedTotalRange"] == {"lower": "0.753", "upper": "1.506"}
    assert result["currency"] == "USD" and result["actualBilledCost"] == "unknown"
    assert "非账单" in result["label"] and result["sourceTransport"] == "injected-test-client"
    assert result["quote"]["sourceUrl"].startswith("https://api-docs.deepseek.com/")
    assert result["quote"]["retrievedAt"] and result["quoteSha256"]


def test_missing_error_attempt_usage_keeps_whole_run_cost_unknown(monkeypatch):
    usage = {"prompt_cache_hit_tokens": 1000000, "prompt_cache_miss_tokens": 1000000, "completion_tokens": 1000000}
    set_synthetic_usage(monkeypatch, [{"usage": usage}, {"httpStatus": 400, "usage": None}])
    result = cost.estimate("unused-test-run", QUOTE)
    assert result["knownUsageSubtotalRange"] == {"lower": "0.753", "upper": "1.506"}
    assert result["estimatedTotalRange"] is None
    assert result["attemptsWithCompleteUsage"] == result["attemptsWithUnknownCost"] == 1
    set_synthetic_usage(monkeypatch, [{"usage": {"total_tokens": 1000}}])
    result = cost.estimate("unused-test-run", QUOTE)
    assert result["estimatedTotalRange"] is result["knownUsageSubtotalRange"] is None
    assert result["actualUsageIncludedInKnownSubtotal"] is None


def test_inconsistent_split_and_wrong_model_are_not_silently_priced(monkeypatch):
    set_synthetic_usage(monkeypatch, [{"usage": {"prompt_tokens": 3, "prompt_cache_hit_tokens": 2,
                                              "prompt_cache_miss_tokens": 2, "completion_tokens": 10}}])
    result = cost.estimate("unused-test-run", QUOTE)
    assert result["estimatedTotalRange"] is None
    assert result["unknownReasons"] == {"inconsistent_actual_prompt_cache_split": 1}
    set_synthetic_usage(monkeypatch, [], model="different-model")
    with pytest.raises(ValueError, match="does not match"):
        cost.estimate("unused-test-run", QUOTE)


@pytest.mark.parametrize("invalid", ["-1", "NaN", "Infinity", True, None])
def test_quote_requires_complete_finite_rates_and_provenance(tmp_path, invalid):
    quote = json.loads(QUOTE.read_text(encoding="utf-8"))
    quote["rates"]["peak"]["output"] = invalid
    path = tmp_path / "quote.json"
    path.write_text(json.dumps(quote), encoding="utf-8")
    with pytest.raises(ValueError, match="finite"):
        cost.read_quote(path)


def test_existing_estimate_is_not_overwritten(tmp_path, monkeypatch):
    set_synthetic_usage(monkeypatch, [{"usage": {"prompt_cache_hit_tokens": 2,
                                               "prompt_cache_miss_tokens": 2, "completion_tokens": 10}}])
    output = tmp_path / "estimate.json"
    output.write_text("keep existing evidence", encoding="utf-8")
    with pytest.raises(SystemExit):
        cost.main(["--run", "unused", "--quote", str(QUOTE), "--output", str(output)])
    assert output.read_text(encoding="utf-8") == "keep existing evidence"
