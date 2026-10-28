"""Export independent human review packets and summarize reviewed model runs.

This tool never calls a model or fills in human ratings. Reviewer declarations
are explicit attestations, not proof that two real people performed the work.
"""

from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import statistics
import unicodedata

try:
    from tools.prepare_coach_evaluation import REVIEW_FIELDS
except ModuleNotFoundError:
    from prepare_coach_evaluation import REVIEW_FIELDS


DIMENSIONS = ("evidenceTruthfulness", "hypothesisAppropriateness", "answerBoundary", "actionability",
              "assessmentAndTransferBoundary", "presentationProvenance")
RATINGS = {"pass", "fail", "unassessable"}


def _hash(content):
    return hashlib.sha256(content).hexdigest()


def _json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _review_facts(review_id, record):
    case = record["case"]
    return {"reviewItemId": review_id, "task": case["task"],
            "question": case["input"]["question"], "code": case["input"].get("code"),
            "events": case["input"]["events"], "humanReviewCriteria": case["expected"],
            "scenarioSource": "author-written synthetic facts, not real Spark execution",
            "visibleOutput": record["visibleOutput"],
            "outputStatus": "available" if record["visibleOutput"] is not None else "unavailable"}


def read_run(directory):
    """Verify frozen run artifacts without consulting today's product sources."""
    directory = Path(directory)
    manifest_path = directory / "manifest.json"
    manifest = _json(manifest_path)
    if (manifest.get("schemaVersion") != "coach-evaluation-run-v1" or manifest.get("mode") != "execute"
            or manifest.get("evaluationStatus") != "model_calls_finished_manual_review_pending"
            or not manifest.get("finishedAt")):
        raise ValueError("Human review requires a finished execute run, never build-only inputs.")
    rows = manifest.get("results", [])
    if not rows or len(rows) != manifest.get("completedRequests") or len(rows) != manifest.get("selectedRequests"):
        raise ValueError("Run is incomplete or has inconsistent request counts.")
    cases_path = directory / "cases.frozen.json"
    if _hash(cases_path.read_bytes()) != manifest.get("frozenCasesSha256"):
        raise ValueError("Frozen case facts changed after execution.")
    cases = {case["id"]: case for case in _json(cases_path)["cases"]}
    records = {}
    requests = set()
    for row in rows:
        request_id, review_id = row.get("requestId", ""), row.get("reviewItemId", "")
        if (not request_id.startswith("planned-") or not request_id[8:].isdigit()
                or not review_id.startswith("review-") or not review_id[7:].isdigit()
                or review_id in records or request_id in requests or row.get("caseId") not in cases):
            raise ValueError("Run contains invalid or duplicate request/review identifiers.")
        requests.add(request_id)
        result_path = directory / request_id / "result.json"
        if _hash(result_path.read_bytes()) != row.get("resultSha256"):
            raise ValueError(f"Run result changed: {request_id}")
        result = _json(result_path)
        if any(row.get(field) != result.get(field) for field in ("requestId", "reviewItemId", "caseId", "condition", "status")):
            raise ValueError("Run result does not match its manifest.")
        input_path = directory / request_id / "input.json"
        if _hash(input_path.read_bytes()) != result.get("actualInputSha256"):
            raise ValueError(f"Actual model input changed: {request_id}")
        visible = result.get("visibleOutput")
        if visible is not None and (not isinstance(visible, str) or not visible.strip()):
            raise ValueError("Visible output must be a nonempty string or null.")
        digest = _hash(visible.encode("utf-8")) if visible is not None else None
        if row.get("visibleOutputSha256") != digest:
            raise ValueError("Visible output fingerprint is invalid.")
        records[review_id] = {**result, "case": cases[row["caseId"]]}
    return manifest, records, _hash(manifest_path.read_bytes())


def export_reviews(run, output):
    manifest, records, run_hash = read_run(run)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    for reviewer in (1, 2):
        packet = output / f"reviewer-{reviewer}"
        (packet / "items").mkdir(parents=True)
        with (packet / "ratings.csv").open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=REVIEW_FIELDS)
            writer.writeheader()
            for review_id, record in sorted(records.items()):
                # The same author scenario facts and human-only rubric are
                # supplied for all conditions. Never disclose model labels or
                # condition mappings in reviewer packets.
                facts = _review_facts(review_id, record)
                _write(packet / "items" / (review_id + ".json"), facts)
                writer.writerow({"reviewItemId": review_id,
                                 "visibleOutputReference": "items/" + review_id + ".json"})
        _write(packet / "declaration.json", {
            "schemaVersion": "coach-reviewer-declaration-v1", "runManifestSha256": run_hash,
            "reviewerId": "", "reviewerType": "", "completedIndependently": None,
            "reviewedAllItems": None, "completedAt": "", "itemCount": len(records),
        })
        (packet / "INSTRUCTIONS.txt").write_text(
            "请由一名真实评审者独立完成此包，勿查看另一评审者的评分或条件映射。\n"
            "请使用固定匿名 reviewerId，并填写 declaration.json：reviewerType=human、"
            "completedIndependently=true、reviewedAllItems=true、completedAt=带时区ISO时间。\n"
            "ratings.csv 的评分保持空白，需逐项人工填写：availability=available 或 unavailable；"
            "六个维度分别填 pass、fail、unassessable。\n"
            "没有实际可见正文时，availability 必须为 unavailable，六维全部 unassessable；"
            "有正文但建议错误仍属 available，应在相关维度填 fail。\n"
            "reason 需说明判断理由；任一不可评维度还需填写 unassessableReason。\n"
            "填写评分前阅读同编号 items 文件。它含共同病例事实与仅供人工使用的判据；"
            "禁止把该文件再次交给待评模型。结构和固定引用可能暴露条件，因此仅遮蔽标签，非完全盲评。\n"
            "两名评审完成后分别导回，不合并或改写彼此评分。工具只核验声明与数据完整性，不能证明独立性。\n",
            encoding="utf-8")
    _write(output / "export-manifest.json", {"schemaVersion": "coach-review-export-v1",
           "runManifestSha256": run_hash, "itemCountPerReviewer": len(records),
           "ratingStatus": "blank_not_reviewed", "conditionLabelsHidden": True,
           "fullyBlind": False, "sourceTransport": manifest.get("transport", "unknown")})
    return {"reviewers": 2, "itemsPerReviewer": len(records), "ratings": "blank"}


def _identity(value):
    return unicodedata.normalize("NFKC", value).strip().casefold()


def read_review(packet, records, run_hash, *, completed_after=None):
    packet = Path(packet)
    declaration = _json(packet / "declaration.json")
    reviewer_id = declaration.get("reviewerId")
    if (declaration.get("schemaVersion") != "coach-reviewer-declaration-v1"
            or declaration.get("runManifestSha256") != run_hash
            or not isinstance(reviewer_id, str) or not reviewer_id.strip()
            or declaration.get("reviewerType") != "human"
            or declaration.get("completedIndependently") is not True
            or declaration.get("reviewedAllItems") is not True
            or declaration.get("itemCount") != len(records)):
        raise ValueError("Each reviewer must independently complete and attest a human review packet.")
    try:
        value = declaration["completedAt"]
        if not isinstance(value, str):
            raise ValueError()
        when = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if when.tzinfo is None:
            raise ValueError()
        if completed_after and when < datetime.fromisoformat(completed_after.replace("Z", "+00:00")):
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise ValueError("Reviewer completion time must be an ISO timestamp with timezone.") from None
    with (packet / "ratings.csv").open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != list(REVIEW_FIELDS):
            raise ValueError("Reviewer CSV headers do not match the independent review schema.")
        rows = list(reader)
    by_id = {}
    for row in rows:
        if set(row) != set(REVIEW_FIELDS) or any(not isinstance(value, str) for value in row.values()):
            raise ValueError("Reviewer CSV contains truncated or extra fields.")
        review_id = row.get("reviewItemId")
        if review_id not in records or review_id in by_id:
            raise ValueError("Reviewer CSV contains missing, duplicate, or unknown items.")
        if _identity(row.get("reviewerId", "")) != _identity(reviewer_id):
            raise ValueError("Reviewer identity must match the declaration on every item.")
        if row.get("visibleOutputReference") != "items/" + review_id + ".json":
            raise ValueError("Reviewer output reference was changed or is missing.")
        if _json(packet / "items" / (review_id + ".json")) != _review_facts(review_id, records[review_id]):
            raise ValueError("The reviewer packet's output or common case facts changed.")
        availability = row.get("availability")
        expected_availability = "available" if records[review_id]["visibleOutput"] is not None else "unavailable"
        if availability != expected_availability:
            raise ValueError("Availability must distinguish absent outputs from incorrect available advice.")
        ratings = [row.get(key) for key in DIMENSIONS]
        if any(value not in RATINGS for value in ratings) or not row.get("reason", "").strip():
            raise ValueError("Every dimension needs an explicit rating and each item needs a reason.")
        if availability == "unavailable" and set(ratings) != {"unassessable"}:
            raise ValueError("Absent outputs cannot be graded as semantic passes or failures.")
        if "unassessable" in ratings and not row.get("unassessableReason", "").strip():
            raise ValueError("Unassessable ratings require a separate explanation.")
        by_id[review_id] = row
    if set(by_id) != set(records):
        raise ValueError("Both reviewers must review every selected request, including failures.")
    return reviewer_id.strip(), by_id, declaration


def agreement(first, second):
    pairs = [(a, b) for a, b in zip(first, second) if a in {"pass", "fail"} and b in {"pass", "fail"}]
    n = len(pairs)
    if not n:
        return {"jointlyAssessableCount": 0, "agreementCount": 0, "agreementRate": None,
                "cohenKappa": None, "kappaUnavailableReason": "no jointly assessable items"}
    equal = sum(a == b for a, b in pairs)
    observed = equal / n
    a_pass = sum(a == "pass" for a, _ in pairs) / n
    b_pass = sum(b == "pass" for _, b in pairs) / n
    expected = a_pass * b_pass + (1 - a_pass) * (1 - b_pass)
    kappa = (observed - expected) / (1 - expected) if n >= 2 and not math.isclose(expected, 1) else None
    return {"jointlyAssessableCount": n, "agreementCount": equal, "agreementRate": observed,
            "cohenKappa": kappa, "kappaUnavailableReason": None if kappa is not None else
            ("fewer than two jointly assessable items" if n < 2 else "degenerate rating marginals")}


def _distribution(values):
    values = sorted(value for value in values if isinstance(value, (int, float)) and not isinstance(value, bool)
                    and math.isfinite(value) and value >= 0)
    if not values:
        return {"count": 0, "median": None, "min": None, "max": None, "p95": None}
    position = (len(values) - 1) * .95
    lo, hi = math.floor(position), math.ceil(position)
    return {"count": len(values), "median": statistics.median(values), "min": values[0], "max": values[-1],
            "p95": values[lo] + (values[hi] - values[lo]) * (position - lo)}


def summarize(run, first_packet, second_packet, output):
    manifest, records, run_hash = read_run(run)
    first_id, first, first_declaration = read_review(first_packet, records, run_hash, completed_after=manifest["finishedAt"])
    second_id, second, second_declaration = read_review(second_packet, records, run_hash, completed_after=manifest["finishedAt"])
    if _identity(first_id) == _identity(second_id):
        raise ValueError("Two distinct declared human reviewer identities are required.")
    conditions = sorted({record["condition"] for record in records.values()})
    report = {"schemaVersion": "coach-reviewed-summary-v1", "runManifestSha256": run_hash,
              "sourceTransport": manifest.get("transport", "unknown"), "model": manifest["model"],
              "selectedRequests": len(records), "plannedRequests": manifest["plannedRequests"],
              "partialRun": len(records) != manifest["plannedRequests"], "reviewStatus": "two_declared_human_reviews_imported",
              "reviewers": [first_declaration, second_declaration], "conditions": {}, "disagreements": [],
              "cost": "unknown", "teachingEffectEvaluated": False,
              "limitations": ["Reviewer identities and independence are declared, not externally verified.",
                              "Ratings are not adjudicated; disagreements are retained rather than averaged away.",
                              "Repeated model calls are clustered within authored cases, not independent students.",
                              "Cohen kappa only uses jointly pass/fail-rated outputs and is undefined for degenerate marginals.",
                              "Token usage is summed only where actually returned; no missing values are imputed.",
                              "MatchedTutor, if present, is a separate evidence-matched control; the primary comparison remains Tutor versus Coach."]}
    for condition in conditions:
        subset = {key: record for key, record in records.items() if record["condition"] == condition}
        attempts = [attempt for record in subset.values() for attempt in record["attempts"]]
        known_usage = [attempt.get("usage") for attempt in attempts if isinstance(attempt.get("usage"), dict)]
        condition_report = {"requestCount": len(subset), "availableOutputCount": sum(record["visibleOutput"] is not None for record in subset.values()),
                            "statusCounts": dict(Counter(record["status"] for record in subset.values())),
                            "failureReasons": dict(Counter(record["failureReason"] for record in subset.values() if record["failureReason"])),
                            "httpAttempts": len(attempts), "requestsWithRetries": sum(len(record["attempts"]) > 1 for record in subset.values()),
                            "firstHttpAttemptFailed": sum(bool(record["attempts"]) and
                                (record["attempts"][0].get("transportError") is not None or
                                 (record["attempts"][0].get("httpStatus") or 0) >= 400) for record in subset.values()),
                            "modelLatencyMs": _distribution([record.get("modelLatencyMs") for record in subset.values()]),
                            "visibleOutputLatencyMs": _distribution([record["modelLatencyMs"] + (record.get("parseLatencyMs") or 0)
                                 for record in subset.values() if record["visibleOutput"] is not None and record.get("modelLatencyMs") is not None]),
                            "usage": {"attemptsWithUsage": len(known_usage), "attemptsWithoutUsage": len(attempts) - len(known_usage),
                                      "actualReturnedTotals": {field: sum(usage[field] for usage in known_usage if type(usage.get(field)) is int)
                                        if any(type(usage.get(field)) is int for usage in known_usage) else None
                                        for field in ("prompt_tokens", "completion_tokens", "total_tokens",
                                                      "prompt_cache_hit_tokens", "prompt_cache_miss_tokens")},
                                      "actualReturnedReasoningTokens": sum(usage["completion_tokens_details"]["reasoning_tokens"]
                                           for usage in known_usage if type(usage.get("completion_tokens_details", {}).get("reasoning_tokens")) is int)
                                           if any(type(usage.get("completion_tokens_details", {}).get("reasoning_tokens")) is int
                                                  for usage in known_usage) else None},
                            "dimensions": {}, "cases": {}}
        for dimension in DIMENSIONS:
            counts = {}
            for reviewer_id, ratings in ((first_id, first), (second_id, second)):
                counter = Counter(ratings[key][dimension] for key in subset)
                denominator = counter["pass"] + counter["fail"]
                counts[reviewer_id] = {"pass": counter["pass"], "fail": counter["fail"], "unassessable": counter["unassessable"],
                                       "assessableDenominator": denominator,
                                       "passRate": counter["pass"] / denominator if denominator else None}
            condition_report["dimensions"][dimension] = {"perReviewer": counts,
                "agreement": agreement([first[key][dimension] for key in subset], [second[key][dimension] for key in subset]),
                "bothPass": sum(first[key][dimension] == second[key][dimension] == "pass" for key in subset),
                "bothFail": sum(first[key][dimension] == second[key][dimension] == "fail" for key in subset),
                "eitherUnassessable": sum("unassessable" in (first[key][dimension], second[key][dimension]) for key in subset)}
            for key, record in subset.items():
                if first[key][dimension] != second[key][dimension]:
                    report["disagreements"].append({"reviewItemId": key, "caseId": record["caseId"], "condition": condition,
                        "dimension": dimension, "firstReviewer": first[key][dimension], "secondReviewer": second[key][dimension],
                        "firstReason": first[key]["reason"], "secondReason": second[key]["reason"]})
        for case_id in sorted({record["caseId"] for record in subset.values()}):
            keys = [key for key, record in subset.items() if record["caseId"] == case_id]
            condition_report["cases"][case_id] = {"requestCount": len(keys), "reviewItemIds": keys,
                "availableOutputCount": sum(subset[key]["visibleOutput"] is not None for key in keys)}
        report["conditions"][condition] = condition_report
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    # Preserve exact independently completed inputs and their declarations.
    for number, packet in enumerate((Path(first_packet), Path(second_packet)), 1):
        for filename in ("ratings.csv", "declaration.json"):
            (output / f"reviewer-{number}-{filename}").write_bytes((packet / filename).read_bytes())
    _write(output / "summary.json", report)
    lines = ["# 教练配对评估：双评审汇总", "", "人工身份与独立性由评审者声明，工具不能外部核实。",
             "未进行真人教学效果实验；费用未知。重复调用不是独立学生样本。", "",
             f"本批次：{len(records)}/{manifest['plannedRequests']} 项请求；"
             + ("部分运行。" if report["partialRun"] else "完整运行。"), ""]
    for condition, item in report["conditions"].items():
        lines += ["## " + condition, "", f"可用输出 {item['availableOutputCount']}/{item['requestCount']}；HTTP 尝试 {item['httpAttempts']}。",
                  f"模型响应中位耗时（毫秒）：{item['modelLatencyMs']['median']}；费用：未知。", ""]
        for dimension, dimension_report in item["dimensions"].items():
            lines += ["- " + dimension + "：" + "；".join(
                reviewer + f" 通过 {values['pass']}/{values['assessableDenominator']}，失败 {values['fail']}，不可评 {values['unassessable']}"
                for reviewer, values in dimension_report["perReviewer"].items())]
        lines.append("")
    lines += [f"维度级分歧共 {len(report['disagreements'])} 条，完整保留于 summary.json；没有自动裁决或任意加权总分。"]
    (output / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export-reviews", help="Export two blank independent human review packets.")
    export.add_argument("--run", type=Path, required=True)
    export.add_argument("--output", type=Path, required=True)
    summary = commands.add_parser("summarize", help="Validate two completed human packets and report separate dimensions.")
    summary.add_argument("--run", type=Path, required=True)
    summary.add_argument("--reviewer-1", type=Path, required=True)
    summary.add_argument("--reviewer-2", type=Path, required=True)
    summary.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "export-reviews":
            result = export_reviews(args.run, args.output)
            print(json.dumps(result, ensure_ascii=False))
        else:
            result = summarize(args.run, args.reviewer_1, args.reviewer_2, args.output)
            print(json.dumps({"reviewStatus": result["reviewStatus"], "requests": result["selectedRequests"],
                              "disagreements": len(result["disagreements"])}, ensure_ascii=False))
    except (ValueError, OSError, KeyError, TypeError) as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
