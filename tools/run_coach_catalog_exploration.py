"""Run six fresh, contract-aligned Coach examples separately from the main 72.

The protocol fixture was consulted while authoring these corrected examples.
This is exploratory coverage, not a pristine statistical holdout or a teaching
experiment. Default mode only freezes inputs. --execute explicitly calls the
chosen model with the API key already present in the process environment.
"""

from __future__ import annotations

import argparse
import ast
import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import time

try:
    from tools.prepare_coach_evaluation import ROOT, fingerprint_source_paths
    from tools.run_coach_evaluation import _call_model, _visible_coach, build_product_input
except ModuleNotFoundError:
    from prepare_coach_evaluation import ROOT, fingerprint_source_paths
    from run_coach_evaluation import _call_model, _visible_coach, build_product_input


LESSONS = ROOT / "src/sparktutor/courses/learning_spark/lessons"


def _hash(content):
    return hashlib.sha256(content).hexdigest()


def _write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _authored_code(relative, function, arguments, body):
    starter = (LESSONS / relative).read_text(encoding="utf-8")
    node = next(node for node in ast.parse(starter).body if isinstance(node, ast.FunctionDef) and node.name == function)
    if [arg.arg for arg in node.args.args] != arguments:
        raise ValueError("The exploration must be updated for changed task signatures.")
    # Reuse only starter imports, supplied DATA/generators/schema declarations;
    # write a new function body and never import the starter's test assertions.
    prefix = "\n".join(starter.splitlines()[:node.lineno - 1])
    code = prefix + "\n\ndef " + function + "(" + ", ".join(arguments) + "):\n" + body.strip("\n") + "\n"
    compile(code, relative + ":exploration", "exec")
    return code


def fresh_cases():
    specifications = [
        ("schema-retain-input-v1", "02_dataframes_schemas", "14", "schema", "starter.py", "build_blogs_df", ["spark"],
         '''    schema = StructType([
        StructField("Id", IntegerType(), True), StructField("First", StringType(), True),
        StructField("Last", StringType(), True), StructField("Url", StringType(), True),
        StructField("Published", StringType(), True), StructField("Hits", IntegerType(), True),
        StructField("Campaigns", ArrayType(StringType()), True)])
    frame = spark.createDataFrame(DATA, schema)
    flagged = frame.withColumn("Big_Hitter", f.col("Hits") > 10000)
    return flagged.filter(f.col("Big_Hitter"))''', "computed_column_preserves_rows"),
        ("window-tied-ranks-v1", "04_external_sources_hof", "practice-window-ranking", "window", "window_practice_starter.py", "rank_orders", ["df"],
         '''    order = Window.partitionBy("region").orderBy(f.col("total").desc())
    return df.withColumn("region_rank", f.dense_rank().over(order))''', "window_tie_semantics"),
        ("join-distinct-plan-strings-v1", "07_join_strategies", "13", "join", "starter.py", "join_comparison", ["spark"],
         '''    users = spark.createDataFrame(generate_users(), USER_SCHEMA)
    orders = spark.createDataFrame(generate_orders(), ORDER_SCHEMA)
    first = orders.join(f.broadcast(users), "user_id", "inner")
    second = orders.hint("merge").join(users.hint("merge"), "user_id", "inner")
    first_plan = first._jdf.queryExecution().simpleString()
    return first, second, first_plan, first_plan''', "join_plan_mismatch"),
        ("orders-unmatched-preservation-v1", "07_join_strategies", "join-cardinality-lab", "join", "join_cardinality_starter.py", "enrich_orders", ["orders", "customer_history"],
         '''    ordering = Window.partitionBy("customer_id").orderBy(f.col("updated_at").desc(), f.col("version").desc())
    latest = (customer_history.withColumn("chosen", f.row_number().over(ordering))
              .filter(f.col("chosen") == 1).select("customer_id", "segment"))
    return orders.join(latest, "customer_id", "inner").select("order_id", "customer_id", "amount", "segment")''', "left_rows_lost"),
        ("join-two-actual-results-v1", "07_join_strategies", "join-plan-lab", "join", "join_plan_starter.py", "compare_join_plans", ["orders", "profiles"],
         '''    first = orders.join(f.broadcast(profiles), "customer_id", "left").select("order_id", "customer_id", "amount", "segment")
    return {"broadcast": first, "merge": first}''', "join_plan_mismatch"),
        ("visits-legitimate-multiplicity-v1", "07_join_strategies", "join-cardinality-transfer", "join", "join_transfer_starter.py", "enrich_visits", ["visits", "page_versions"],
         '''    ordering = Window.partitionBy("page_id").orderBy(f.col("updated_at").desc(), f.col("version").desc())
    latest = (page_versions.withColumn("chosen", f.row_number().over(ordering))
              .filter(f.col("chosen") == 1).select("page_id", "category"))
    result = visits.join(latest, "page_id", "left").select("visit_id", "page_id", "dwell_seconds", "category")
    return result.dropDuplicates()''', "output_deduplication"),
    ]
    cases = []
    for case_id, lesson, step, topic, starter, function, arguments, body, hypothesis in specifications:
        code = _authored_code(lesson + "/" + starter, function, arguments, body)
        cases.append({"id": case_id, "task": {"courseId": "learning_spark", "lessonId": lesson,
                       "stepId": step, "taskId": lesson + ":" + step}, "topic": topic,
                      "input": {"code": code, "question": "请根据当前题目和代码提供一个下一步提示，区分事实与待验证假设。", "events": []},
                      "humanReviewOnly": {"intendedHypothesis": hypothesis,
                                          "noClaimOfSparkExecution": True, "noClaimOfPriorMastery": True},
                      "starterSource": ("src/sparktutor/courses/learning_spark/lessons/" + lesson + "/" + starter)})
    return cases


async def run_exploration(output, *, execute=False, model="deepseek-flash", base_url="https://api.deepseek.com", caller=None):
    output = Path(output)
    cases = fresh_cases()
    output.mkdir(parents=True, exist_ok=False)
    frozen = {"schemaVersion": "coach-catalog-exploration-cases-v1", "frozenAt": datetime.now(timezone.utc).isoformat(),
              "provenance": {"authorConstructed": True, "protocolFixtureConsulted": True,
                             "notPristineStatisticalHoldout": True, "usedToChangeAuthorNodes": False,
                             "realLearnerData": False, "sparkExecutionEvidenceProvided": False}, "cases": cases}
    _write(output / "cases.frozen.json", frozen)
    manifest = {"schemaVersion": "coach-catalog-exploration-run-v1", "status": "running" if execute else "not_run",
                "mode": "execute" if execute else "freeze-only", "model": model, "condition": "Coach",
                "caseCount": len(cases), "completedRequests": 0, "httpAttempts": 0,
                "caseSha256": _hash((output / "cases.frozen.json").read_bytes()),
                "protocolFixtureSha256": _hash((ROOT / "tests/fixtures/coach_catalog_cases.json").read_bytes()),
                "sourceFingerprints": {name: _hash((ROOT / name).read_bytes()) for name in fingerprint_source_paths()},
                "toolSha256": _hash(Path(__file__).read_bytes()),
                "humanSemanticReview": "not_reviewed", "teachingEffectEvaluated": False,
                "cost": "unknown", "startedAt": datetime.now(timezone.utc).isoformat(), "finishedAt": None,
                "transport": ("injected-test-client" if caller else "product-compatible-http") if execute else "none",
                "boundaries": ["Six author-written corrected examples; protocol fixture consulted, not a pristine holdout.",
                               "Task and current code only; no synthetic or real Spark assessment results.",
                               "Structural validity does not establish semantic correctness or teaching effectiveness.",
                               "Separate exploration: do not combine these six calls with the original 72-case comparison."], "results": []}
    _write(output / "manifest.json", manifest)
    with tempfile.TemporaryDirectory(prefix="coach-catalog-exploration-") as scratch:
        for index, case in enumerate(cases, 1):
            item_dir = output / f"case-{index:02d}"
            item_dir.mkdir()
            handler, built, code = await build_product_input(case, "Coach", Path(scratch) / f"case-{index:02d}")
            context = json.loads(built["messages"][-1]["content"])
            if any(dimension["score"] is not None for dimension in context["dimensions"]):
                raise ValueError("A fresh exploration must not invent assessment scores.")
            if any(event.data.get("assessmentEligible") for event in handler.events.list_events(limit=None)):
                raise ValueError("A fresh exploration must not contain fabricated execution evidence.")
            _write(item_dir / "input.json", {"messages": built["messages"]})
            result = {"caseId": case["id"], "task": case["task"], "status": "not_run",
                      "inputSha256": _hash((item_dir / "input.json").read_bytes()),
                      "coachConfigurationSha256": context.get("coachConfigurationSha256"),
                      "initialScores": {item["key"]: item["score"] for item in context["dimensions"]},
                      "rawModelOutput": None, "visibleOutput": None, "failureReason": None,
                      "parseStatus": "not_run", "modelLatencyMs": None, "attempts": [], "humanReview": "not_reviewed"}
            if execute:
                result.update(await (caller or _call_model)(built["messages"], handler.settings, model=model,
                                                           base_url=base_url, coach=True))
                result["status"] = "failed" if result["failureReason"] else "completed"
                result["parseStatus"] = "not_available" if result["failureReason"] else "not_run"
                if result["rawModelOutput"] is not None:
                    try:
                        message = await handler._parse_coach_response({"requestId": built["requestId"], "code": code,
                                                                      "rawText": result["rawModelOutput"]})
                        result["coachMessage"] = message
                        result["visibleOutput"] = _visible_coach(message)
                        result["parseStatus"] = "contract_valid_semantics_unreviewed"
                    except ValueError as error:
                        result.update(status="failed", parseStatus="rejected", failureReason=getattr(error, "code", "parse_error"))
                manifest["completedRequests"] += 1
                manifest["httpAttempts"] += len(result["attempts"])
            _write(item_dir / "result.json", result)
            manifest["results"].append({"caseId": case["id"], "directory": item_dir.name, "status": result["status"],
                                        "parseStatus": result["parseStatus"], "failureReason": result["failureReason"],
                                        "resultSha256": _hash((item_dir / "result.json").read_bytes())})
            _write(output / "manifest.json", manifest)
    manifest["status"] = "model_calls_finished_manual_review_pending" if execute else "not_run"
    manifest["finishedAt"] = datetime.now(timezone.utc).isoformat()
    _write(output / "manifest.json", manifest)
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--model", default="deepseek-flash")
    parser.add_argument("--base-url", default="https://api.deepseek.com")
    args = parser.parse_args(argv)
    result = asyncio.run(run_exploration(args.output, execute=args.execute, model=args.model, base_url=args.base_url))
    print(json.dumps({"status": result["status"], "caseCount": result["caseCount"],
                      "completedRequests": result["completedRequests"], "httpAttempts": result["httpAttempts"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
