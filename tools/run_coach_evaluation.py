"""Build or execute a frozen author-case evaluation through product prompt builders.

Default mode never opens a network connection. --execute uses only an explicitly
selected compatible endpoint/model and a key from the process environment. No
learner database, personal configuration, or automatic semantic grades are used.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from contextlib import closing
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time
from urllib.error import HTTPError

try:
    from tools.prepare_coach_evaluation import ROOT, _validate_cases, fingerprint_source_paths
except ModuleNotFoundError:  # Direct ``python tools/run_coach_evaluation.py``.
    from prepare_coach_evaluation import ROOT, _validate_cases, fingerprint_source_paths


def _bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _hash(value):
    return hashlib.sha256(value).hexdigest()


def _write(path, value):
    path.write_bytes(_bytes(value))


def read_batch(batch: Path):
    """Refuse changed cases/plans and changed product builders before any call."""
    manifest = json.loads((batch / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schemaVersion") != "coach-evaluation-preparation-v1":
        raise ValueError("Expected an offline preparation batch.")
    for name in ("cases.frozen.json", "request-plan.json", "reviewer-1.csv", "reviewer-2.csv"):
        if _hash((batch / name).read_bytes()) != manifest["artifactSha256"].get(name):
            raise ValueError(f"Frozen artifact changed: {name}")
    if set(manifest.get("sourceFingerprints", {})) != set(fingerprint_source_paths()):
        raise ValueError("Product source set changed; prepare a new batch.")
    for name, digest in manifest["sourceFingerprints"].items():
        if _hash((ROOT / name).read_bytes()) != digest:
            raise ValueError(f"Product source changed; prepare a new batch: {name}")
    document = json.loads((batch / "cases.frozen.json").read_text(encoding="utf-8"))
    cases = {case["id"]: case for case in _validate_cases(document)}
    plan = json.loads((batch / "request-plan.json").read_text(encoding="utf-8"))
    if not plan or len(plan) != manifest["plan"]["requestCount"]:
        raise ValueError("Frozen plan count is invalid.")
    seen = set()
    reviews = set()
    for item in plan:
        request = item.get("requestId", "")
        review = item.get("reviewItemId", "")
        if (not isinstance(request, str) or not request.startswith("planned-") or not request[8:].isdigit()
                or not isinstance(review, str) or not review.startswith("review-") or not review[7:].isdigit()
                or request in seen or review in reviews or item.get("caseId") not in cases
                or item.get("condition") not in {"Tutor", "Coach", "MatchedTutor"} or item.get("status") != "not_run"
                or item.get("coachLevel") != (1 if item["condition"] == "Coach" else None)):
            raise ValueError("Frozen plan has invalid or duplicate requests.")
        seen.add(request)
        reviews.add(review)
    return manifest, cases, plan


def evidence_context(messages):
    """Extract the actual coach's factual context, excluding selection scaffolds."""
    context = json.loads(messages[-1]["content"])
    selectors = {"responseContract", "probePolicy", "responseExample", "allowedHypotheses", "hypothesisMeanings",
                 "hypothesisEvidenceRequirements", "level"}
    return {key: value for key, value in context.items() if key not in selectors}


def _replay_fixture_events(handler, case):
    """Only this fresh evaluation database receives the authored scenario rows."""
    from sparktutor.state.learning_events import EVENT_TYPES

    with closing(sqlite3.connect(handler.events.db_path)) as conn, conn:
        for event in case["input"]["events"]:
            if event["eventType"] not in EVENT_TYPES or not event["eventId"].startswith("fixture-"):
                raise ValueError("Only authored fixture events can be replayed.")
            conn.execute(
                "INSERT INTO learning_events (event_id,event_type,timestamp,course_id,lesson_id,"
                "task_id,session_id,task_type,attempt_number,data) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (event["eventId"], event["eventType"], event["timestamp"], event.get("courseId", ""),
                 event.get("lessonId", ""), event.get("taskId", ""), event.get("sessionId", ""),
                 event.get("taskType", ""), event.get("attemptNumber", 0),
                 json.dumps(event.get("data", {}), ensure_ascii=False)),
            )


async def build_product_input(case, condition, state_dir: Path):
    from sparktutor.config.settings import ExecutionMode, Settings
    from sparktutor.server.handler import ServerHandler

    settings = Settings(data_dir=state_dir, execution_mode=ExecutionMode.DRY_RUN)
    handler = ServerHandler(settings, workspace_path=state_dir / "workspace")
    task = case["task"]
    course = handler.registry.get_course(task["courseId"])
    if course is None or task["taskId"] != task["lessonId"] + ":" + task["stepId"]:
        raise ValueError("Case task does not match the real course.")
    await handler._load_lesson({"courseId": task["courseId"],
                                "lessonIdx": course.lessons.index(task["lessonId"]),
                                "depth": "beginner"}, target_step_id=task["stepId"])
    _replay_fixture_events(handler, case)
    code = case["input"].get("code") or ""
    if condition == "Coach":
        built = await handler._build_coach_prompt({"code": code, "level": 1})
    elif condition == "Tutor":
        built = await handler._build_chat_prompt({"code": code, "question": case["input"]["question"]})
    elif condition == "MatchedTutor":
        coach = await handler._build_coach_prompt({"code": code, "level": 1})
        built = await handler._build_chat_prompt({"code": code, "question": case["input"]["question"]})
        matched = evidence_context(coach["messages"])
        built["messages"][-1]["content"] += ("\n\n以下为本题的可核查学习证据，与证据教练使用相同事实：\n"
                                              + json.dumps(matched, ensure_ascii=False)
                                              + "\n请据此给一个下一步提示，区分事实与待验证假设，不提供可直接提交的整解。")
        built["matchedEvidence"] = matched
    else:
        raise ValueError("Unknown condition.")
    # No expected values, scenario labels, descriptions, or source answers are
    # forwarded here: all messages come from the actual product builders.
    return handler, built, code


class _ResponseTrace:
    def __init__(self, response, record):
        self.response, self.record = response, record

    def __enter__(self):
        self.response.__enter__()
        return self

    def __exit__(self, *args):
        return self.response.__exit__(*args)

    def read(self, limit):
        content = self.response.read(limit)
        try:
            result = json.loads(content)
            usage = result.get("usage") if isinstance(result, dict) else None
            if isinstance(usage, dict):
                self.record["usage"] = {key: value for key, value in usage.items()
                                        if key in {"prompt_tokens", "completion_tokens", "total_tokens",
                                                   "prompt_cache_hit_tokens", "prompt_cache_miss_tokens"}
                                        and type(value) is int and value >= 0} or None
                for detail_name, detail_field in (("completion_tokens_details", "reasoning_tokens"),
                                                   ("prompt_tokens_details", "cached_tokens")):
                    details = usage.get(detail_name)
                    if isinstance(details, dict) and type(details.get(detail_field)) is int and details[detail_field] >= 0:
                        if self.record["usage"] is None:
                            self.record["usage"] = {}
                        self.record["usage"][detail_name] = {detail_field: details[detail_field]}
            if isinstance(result, dict) and isinstance(result.get("model"), str):
                self.record["returnedModel"] = result["model"][:256]
            if isinstance(result, dict) and isinstance(result.get("system_fingerprint"), str):
                self.record["systemFingerprint"] = result["system_fingerprint"][:256]
            choices = result.get("choices") if isinstance(result, dict) else None
            if isinstance(choices, list) and choices and isinstance(choices[0], dict):
                choice = choices[0]
                message = choice.get("message")
                content_value = message.get("content") if isinstance(message, dict) else None
                if isinstance(content_value, str):
                    # Preserve even rejected/truncated answer content, but never
                    # export private reasoning or other provider response fields.
                    self.record["returnedContent"] = content_value
                elif isinstance(content_value, list):
                    self.record["returnedContent"] = [{"type": part.get("type", "text"), "text": part["text"]}
                        for part in content_value if isinstance(part, dict)
                        and part.get("type") in {None, "text", "output_text"} and isinstance(part.get("text"), str)]
                self.record["finishReason"] = choice.get("finish_reason")
        except (ValueError, TypeError, UnicodeError):
            pass
        return content


class RecordingOpener:
    """Capture each product-client attempt without recording URLs or headers."""
    def __init__(self, opener):
        self.opener = opener
        self.attempts = []

    def __call__(self, request, timeout):
        payload = json.loads(request.data)
        record = {"attempt": len(self.attempts) + 1, "startedAt": datetime.now(timezone.utc).isoformat(),
                  "actualSentBody": payload, "timeoutSeconds": timeout,
                  "httpStatus": None, "transportError": None, "usage": None, "returnedModel": None}
        self.attempts.append(record)
        started = time.monotonic()
        try:
            response = self.opener(request, timeout=timeout)
            record["httpStatus"] = getattr(response, "status", 200)
            return _ResponseTrace(response, record)
        except HTTPError as error:
            record["httpStatus"] = error.code
            record["transportError"] = "HTTPError"
            raise
        except Exception as error:
            record["transportError"] = type(error).__name__
            raise
        finally:
            record["connectionLatencyMs"] = round((time.monotonic() - started) * 1000, 2)


async def _call_model(messages, settings, *, model, base_url, coach):
    from sparktutor.web.ai import WebAiManager, WebAiError

    manager = WebAiManager(settings)
    trace = RecordingOpener(manager.opener)
    manager.opener = trace
    config = {"provider": "openai-compatible", "model": model, "baseUrl": base_url,
              "apiKey": os.environ.get("SPARKTUTOR_OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY", ""),
              "extraHeaders": {}, "_health": {}}
    started = time.monotonic()
    try:
        answer = await manager.complete(config, messages, provider="openai-compatible", coach=coach)
        return {"rawModelOutput": answer, "failureReason": None, "attempts": trace.attempts,
                "modelLatencyMs": round((time.monotonic() - started) * 1000, 2)}
    except WebAiError as error:
        return {"rawModelOutput": None, "failureReason": error.kind, "attempts": trace.attempts,
                "modelLatencyMs": round((time.monotonic() - started) * 1000, 2)}
    except Exception as error:
        return {"rawModelOutput": None, "failureReason": type(error).__name__, "attempts": trace.attempts,
                "modelLatencyMs": round((time.monotonic() - started) * 1000, 2)}


def _visible_coach(message):
    parts = [message[key] for key in ("hypothesis", "question", "guidance", "reflection", "disclaimer")]
    parts += ["引用：" + item["title"] + "；" + item["description"] for item in message["evidence"]]
    if message.get("probe"):
        parts.append("可选教学探针：" + json.dumps(message["probe"], ensure_ascii=False)
                     + "（仅 Python 参考模板，未运行学生代码或 Spark）")
    return "\n\n".join(parts)


async def run_batch(batch: Path, output: Path, *, execute=False, model="", base_url="", limit=None,
                    call_model=None):
    prepared, cases, full_plan = read_batch(batch)
    if limit is not None and (type(limit) is not int or limit < 1):
        raise ValueError("Limit must be a positive integer.")
    if execute:
        from sparktutor.web.ai import _base_url
        base_url = _base_url(base_url)
        if not model.strip() or not base_url:
            raise ValueError("Execution requires an explicit model and compatible base URL.")
        requested = prepared["model"]["requestedId"]
        if requested != "unknown" and requested != model:
            raise ValueError("Selected model differs from the prepared plan; prepare a new batch.")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    plan = full_plan[:limit] if limit else full_plan
    summary = {"schemaVersion": "coach-evaluation-run-v1", "mode": "execute" if execute else "build-only",
               "evaluationStatus": "running" if execute else "not_run", "realLearnerDataIncluded": False,
               "scenarioProvenance": "team_authored_synthetic_events_not_real_spark_results",
               "preparedManifestSha256": _hash((batch / "manifest.json").read_bytes()),
               "transport": ("injected-test-client" if call_model else "product-compatible-http") if execute else "none",
               "sourceFingerprints": prepared["sourceFingerprints"], "caseSet": prepared["caseSet"],
               "plannedRequests": len(full_plan), "selectedRequests": len(plan), "builtInputCount": 0,
               "completedRequests": 0,
               "networkRequestsMade": 0, "model": model if execute else prepared["model"]["requestedId"],
               "fixedModelSnapshot": "unknown", "provider": "openai-compatible" if execute else "unknown",
               "parameters": {"temperature": "provider-default", "top_p": "provider-default", "seed": "provider-default",
                              "retryPolicy": "product compatible client; every HTTP attempt retained; no outer retry",
                              "requestDeadlineSeconds": 90, "language": "zh-CN", "difficulty": "beginner"},
               "startedAt": datetime.now(timezone.utc).isoformat(), "finishedAt": None,
               "semanticReviewStatus": "not_reviewed", "teachingEffectEvaluated": False,
               "cost": "unknown", "results": [],
               "boundaries": ["Product prompt builders and response parsing are used; Spark and student code are not executed.",
                              "Legal hypothesis/evidence IDs do not establish semantic correctness.",
                              "Repeated calls are not independent students; manual review and teaching studies remain separate.",
                              "Reviewer outputs hide condition/model labels only; output format can reveal the condition."]}
    (output / "cases.frozen.json").write_bytes((batch / "cases.frozen.json").read_bytes())
    summary["frozenCasesSha256"] = _hash((output / "cases.frozen.json").read_bytes())
    _write(output / "manifest.json", summary)
    if execute:
        (output / "review-output").mkdir()
    # Databases, author inputs, and provider credentials never come from a live
    # account. Every condition/repeat owns a new, disposable state directory.
    with tempfile.TemporaryDirectory(prefix="coach-evaluation-") as temporary:
        for item in plan:
            request_dir = output / item["requestId"]
            request_dir.mkdir()
            handler, built, code = await build_product_input(cases[item["caseId"]], item["condition"],
                                                           Path(temporary) / item["requestId"])
            actual = {"messages": built["messages"], "task": cases[item["caseId"]]["task"],
                      "condition": item["condition"], "difficulty": "beginner"}
            if item["condition"] in {"Coach", "MatchedTutor"}:
                matched = (evidence_context(built["messages"]) if item["condition"] == "Coach"
                           else built["matchedEvidence"])
                actual["matchingEvidenceSha256"] = _hash(_bytes(matched))
            _write(request_dir / "input.json", actual)
            result = {**item, "actualInputSha256": _hash(_bytes(actual)), "status": "not_run",
                      "modelRequestAttempted": False, "rawModelOutput": None, "visibleOutput": None,
                      "parseStatus": "not_run", "failureReason": None, "modelLatencyMs": None,
                      "parseLatencyMs": None, "usage": None, "attempts": [],
                      "semanticReview": "pending", "syntheticScenario": True}
            if execute:
                caller = call_model or _call_model
                called = await caller(built["messages"], handler.settings, model=model, base_url=base_url,
                                      coach=item["condition"] == "Coach")
                result.update(called, modelRequestAttempted=bool(called.get("attempts")))
                result["status"] = "failed" if result["failureReason"] else "completed"
                result["parseStatus"] = "not_available" if result["failureReason"] else "not_applicable"
                if result["rawModelOutput"] is not None:
                    started = time.monotonic()
                    if item["condition"] == "Coach":
                        try:
                            message = await handler._parse_coach_response({"requestId": built["requestId"],
                                              "rawText": result["rawModelOutput"], "code": code})
                            result["coachMessage"] = message
                            result["visibleOutput"] = _visible_coach(message)
                            result["parseStatus"] = "contract_valid_semantics_unreviewed"
                        except ValueError as error:
                            result.update(status="failed", parseStatus="rejected", failureReason=getattr(error, "code", "coach_parse_error"))
                    else:
                        result["visibleOutput"] = result["rawModelOutput"]
                    result["parseLatencyMs"] = round((time.monotonic() - started) * 1000, 2)
                usages = [a["usage"] for a in result["attempts"] if a.get("usage") is not None]
                result["usage"] = usages or None
                summary["networkRequestsMade"] += len(result["attempts"])
            if execute:
                visible = result["visibleOutput"] or "本项没有可用提示输出。"
                (output / "review-output" / (item["reviewItemId"] + ".txt")).write_text(visible, encoding="utf-8")
            _write(request_dir / ("result.json" if execute else "request-status.json"), result)
            result_file = request_dir / ("result.json" if execute else "request-status.json")
            summary["results"].append({"requestId": item["requestId"], "caseId": item["caseId"],
                                       "condition": item["condition"], "reviewItemId": item["reviewItemId"],
                                       "status": result["status"], "parseStatus": result["parseStatus"],
                                       "failureReason": result["failureReason"],
                                       "resultSha256": _hash(result_file.read_bytes()),
                                       "visibleOutputSha256": _hash(result["visibleOutput"].encode("utf-8"))
                                       if result["visibleOutput"] is not None else None})
            summary["builtInputCount"] += 1
            summary["completedRequests"] += int(execute)
            _write(output / "manifest.json", summary)
    for reviewer in ((1, 2) if execute else ()):
        # Preserve blank independent ratings, only for requests selected in this
        # run. Partial smoke runs must not leave missing-output review items.
        selected_reviews = {item["reviewItemId"] for item in plan}
        with (batch / f"reviewer-{reviewer}.csv").open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            fields = reader.fieldnames
            rows = [row for row in reader if row["reviewItemId"] in selected_reviews]
        with (output / f"reviewer-{reviewer}.csv").open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
    summary["finishedAt"] = datetime.now(timezone.utc).isoformat()
    summary["evaluationStatus"] = ("model_calls_finished_manual_review_pending" if execute else "not_run")
    summary["requestStatusCounts"] = dict(Counter(row["status"] for row in summary["results"]))
    _write(output / "manifest.json", summary)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", type=Path, required=True, help="Existing frozen preparation directory.")
    parser.add_argument("--output", type=Path, required=True, help="New output directory; never overwrite a run.")
    parser.add_argument("--execute", action="store_true", help="Actually call the model; may incur API charges.")
    parser.add_argument("--model", default="", help="Exact model for both conditions; required with --execute.")
    parser.add_argument("--base-url", default="", help="Compatible API base URL without credentials.")
    parser.add_argument("--limit", type=int, help="Run only the first N frozen requests, clearly recorded as partial.")
    args = parser.parse_args(argv)
    try:
        result = asyncio.run(run_batch(args.batch, args.output, execute=args.execute, model=args.model,
                                       base_url=args.base_url, limit=args.limit))
    except (ValueError, OSError, KeyError) as error:
        parser.error(str(error))
    print(f"{result['mode']}: {result['builtInputCount']} inputs built; "
          f"{result['completedRequests']}/{result['plannedRequests']} model requests processed; "
          f"{result['networkRequestsMade']} HTTP attempts; manual semantic review not performed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
