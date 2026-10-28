"""Estimate a token-cost range from actual usage and an explicit dated quote.

This is not an invoice or a balance deduction. Unknown usage stays unknown;
known subtotals exclude missing attempts, discounts, grants, and billing rounding.
No network, account, key, or balance access is performed.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path

try:
    from tools.summarize_coach_evaluation import read_run
except ModuleNotFoundError:
    from summarize_coach_evaluation import read_run


def read_quote(path):
    quote = json.loads(Path(path).read_text(encoding="utf-8"))
    if (quote.get("schemaVersion") != "model-token-price-quote-v1"
            or not isinstance(quote.get("model"), str) or not quote["model"]
            or not isinstance(quote.get("currency"), str) or not quote["currency"]
            or quote.get("unitTokens") != 1000000
            or not isinstance(quote.get("sourceUrl"), str) or not quote["sourceUrl"].startswith("https://")):
        raise ValueError("An explicit model, currency, unit, and authoritative quote URL are required.")
    try:
        when = datetime.fromisoformat(quote["retrievedAt"].replace("Z", "+00:00"))
        if when.tzinfo is None:
            raise ValueError()
        rates = {}
        for regime in ("off_peak", "peak"):
            rates[regime] = {}
            for field in ("cacheHitInput", "cacheMissInput", "output"):
                value = quote["rates"][regime][field]
                if isinstance(value, bool):
                    raise ValueError()
                rate = Decimal(str(value))
                if not rate.is_finite() or rate < 0:
                    raise ValueError()
                rates[regime][field] = rate
    except (KeyError, TypeError, ValueError, InvalidOperation, AttributeError):
        raise ValueError("Quote needs a timezone-aware date and finite nonnegative rates for every token category.") from None
    return quote, rates


def estimate(run, quote_path):
    manifest, records, run_hash = read_run(run)
    quote, rates = read_quote(quote_path)
    if manifest["model"] != quote["model"]:
        raise ValueError("The quote's model does not match the executed model.")
    totals = {regime: Decimal(0) for regime in rates}
    reasons = Counter()
    attempt_count, known_count = 0, 0
    usage_totals = {"prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 0, "completion_tokens": 0}
    for record in records.values():
        for attempt in record["attempts"]:
            attempt_count += 1
            usage = attempt.get("usage")
            fields = tuple(usage_totals)
            if not isinstance(usage, dict) or any(type(usage.get(field)) is not int or usage[field] < 0 for field in fields):
                reasons["missing_actual_cache_split_or_output_usage"] += 1
                continue
            hit, miss, completion = (usage[field] for field in fields)
            if type(usage.get("prompt_tokens")) is int and usage["prompt_tokens"] != hit + miss:
                reasons["inconsistent_actual_prompt_cache_split"] += 1
                continue
            known_count += 1
            for field in fields:
                usage_totals[field] += usage[field]
            for regime, price in rates.items():
                # completion_tokens already includes its reasoning-token
                # breakdown: never add reasoning_tokens a second time.
                totals[regime] += (Decimal(hit) * price["cacheHitInput"] + Decimal(miss) * price["cacheMissInput"]
                                  + Decimal(completion) * price["output"]) / Decimal(quote["unitTokens"])
    lower, upper = min(totals.values()), max(totals.values())
    covered = attempt_count > 0 and known_count == attempt_count
    return {"schemaVersion": "coach-cost-estimate-v1", "label": "按官方报价与实际返回usage估算，非账单费用",
            "actualBilledCost": "unknown", "currency": quote["currency"], "model": manifest["model"],
            "sourceRunManifestSha256": run_hash, "sourceTransport": manifest.get("transport", "unknown"),
            "quote": quote, "quoteSha256": hashlib.sha256(Path(quote_path).read_bytes()).hexdigest(),
            "attemptCount": attempt_count, "attemptsWithCompleteUsage": known_count,
            "attemptsWithUnknownCost": attempt_count - known_count, "unknownReasons": dict(reasons),
            "actualUsageIncludedInKnownSubtotal": usage_totals if known_count else None,
            "knownUsageSubtotalRange": {"lower": str(lower), "upper": str(upper)} if known_count else None,
            "estimatedTotalRange": {"lower": str(lower), "upper": str(upper)} if covered else None,
            "assumptions": ["Quoted official-provider prices apply to this explicitly selected model; actual endpoint/billing is not verified.",
                            "Bounds use all off-peak versus all peak prices; actual request tariff periods and public holidays are not inferred.",
                            "Missing usage on any HTTP attempt leaves the whole-run total unknown, including error attempts.",
                            "Promotional balances, negotiated discounts, rounding, and invoice adjustments are excluded.",
                            "This script does not read the API key, query a balance, or call any network service."]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--quote", type=Path, required=True, help="Explicit dated price snapshot with source and currency.")
    parser.add_argument("--output", type=Path, required=True, help="New JSON file; existing estimates are never overwritten.")
    args = parser.parse_args(argv)
    try:
        report = estimate(args.run, args.quote)
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    except (ValueError, OSError, KeyError, TypeError) as error:
        parser.error(str(error))
    print(json.dumps({"label": report["label"], "currency": report["currency"],
                      "estimatedTotalRange": report["estimatedTotalRange"], "actualBilledCost": "unknown"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
