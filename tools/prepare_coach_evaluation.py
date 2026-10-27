"""Prepare an offline evaluation batch; never call a model or load learner data.

The frozen author cases and evaluation labels are review material, not model
messages. Actual prompt construction, isolated case replay, calls, and scoring
remain separate work. This script uses only the Python standard library.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import random


ROOT = Path(__file__).resolve().parents[1]
CASES_PATH = ROOT / "tests" / "fixtures" / "coach_cases.json"
SOURCE_PATHS = (
    "src/sparktutor/engine/learning_coach.py",
    "src/sparktutor/engine/evaluator.py",
    "src/sparktutor/engine/spark_knowledge.py",
    "src/sparktutor/server/handler.py",
)
REVIEW_FIELDS = (
    "reviewItemId", "reviewerId", "visibleOutputReference", "availability",
    "evidenceTruthfulness", "hypothesisAppropriateness", "answerBoundary",
    "actionability", "assessmentAndTransferBoundary", "presentationProvenance",
    "reason", "unassessableReason",
)


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _validate_cases(document: dict) -> list[dict]:
    provenance = document.get("provenance", {})
    if (document.get("schemaVersion") != "coach-cases-v1"
            or document.get("status") != "authored_not_executed"
            or provenance.get("caseSource") != "team_authored_synthetic_cases"
            or provenance.get("realLearnerData") is not False
            or provenance.get("modelInvokedForTheseCases") is not False):
        raise ValueError("Only the unexecuted, author-written synthetic case set is supported.")
    cases = document.get("cases", [])
    if not cases or len({case.get("id") for case in cases}) != len(cases):
        raise ValueError("Case IDs must be present and unique.")
    for case in cases:
        if (not isinstance(case.get("id"), str) or not case["id"]
                or case.get("evaluationStatus") != "not_run" or case.get("results") != []
                or not isinstance(case.get("task"), dict)
                or not isinstance(case.get("input"), dict)
                or not isinstance(case.get("expected"), dict)):
            raise ValueError("Cases must have task/input/expected fields and no execution results.")
    return cases


def prepare(output: Path, *, model: str = "unknown", seed: int = 0, repeats: int = 3) -> dict:
    """Write a new preparation directory, refusing any existing destination."""
    if type(repeats) is not int or not 1 <= repeats <= 1000:
        raise ValueError("repeats must be an integer from 1 to 1000.")
    if type(seed) is not int:
        raise ValueError("seed must be an integer for plan ordering only.")
    model = model.strip()
    if not model:
        raise ValueError("model must be a nonempty label or 'unknown'.")
    raw_cases = CASES_PATH.read_bytes()
    document = json.loads(raw_cases)
    cases = _validate_cases(document)
    rng = random.Random(seed)
    pairs = [(case["id"], repeat) for repeat in range(1, repeats + 1) for case in cases]
    rng.shuffle(pairs)
    review_ids = [f"review-{index:04d}" for index in range(1, len(pairs) * 2 + 1)]
    rng.shuffle(review_ids)
    plan = []
    for case_id, repeat in pairs:
        conditions = ["Tutor", "Coach"]
        rng.shuffle(conditions)
        for condition in conditions:
            index = len(plan) + 1
            plan.append({
                "requestId": f"planned-{index:04d}", "order": index,
                "caseId": case_id, "condition": condition, "repeat": repeat,
                "reviewItemId": review_ids[index - 1], "status": "not_run",
                "coachLevel": 1 if condition == "Coach" else None,
                "modelConfigurationRef": "manifest.json#/model",
                "commonParametersRef": "manifest.json#/commonParameters",
                "actualInputSha256": None, "rawModelOutput": None,
                "visibleOutput": None, "parseStatus": None, "failureReason": None,
            })
    files = {
        "cases.frozen.json": raw_cases,
        "request-plan.json": _json_bytes(plan),
    }
    manifest = {
        "schemaVersion": "coach-evaluation-preparation-v1",
        "evaluationStatus": "not_run", "preparationOnly": True,
        "networkRequestsMade": 0, "modelInvocationsMade": 0,
        "realLearnerDataIncluded": False, "evaluationRunDate": "unknown",
        "caseSet": {"source": CASES_PATH.relative_to(ROOT).as_posix(),
                    "schemaVersion": document["schemaVersion"], "sha256": _sha256(raw_cases),
                    "caseCount": len(cases)},
        "sourceFingerprints": {name: _sha256((ROOT / name).read_bytes()) for name in SOURCE_PATHS},
        "plan": {"requestCount": len(plan), "conditions": ["Tutor", "Coach"],
                 "repeatsPerCaseAndCondition": repeats, "orderingSeed": seed,
                 "orderPolicy": "shuffle case/repeat pairs; randomize adjacent Tutor/Coach order",
                 "orderingSeedIsModelSeed": False, "coachStartingLevel": 1},
        "model": {"requestedId": model, "resolvedId": "unknown", "provider": "unknown",
                  "fixedSnapshot": "unknown", "selectionVerified": False},
        "commonParameters": {"temperature": "provider-default", "top_p": "provider-default",
                             "seed": "provider-default", "max_output_tokens": "unknown",
                             "timeout_seconds": "unknown", "retry_policy": "unknown",
                             "language": "zh-CN", "difficulty": "unknown",
                             "actualSentParameters": None, "providerSupportVerified": False},
        "boundaries": [
            "This directory prepares a plan; no model or teaching-quality evaluation has run.",
            "A model label and plan seed do not fix the actual model snapshot or sampling parameters.",
            "Frozen cases include evaluator-only expected answers, titles, and descriptions: never send the whole file to a model.",
            "Build inputs through each product's real prompt builders, using separate temporary state for each case/condition/repeat.",
            "Replay only that case's synthetic events; do not import a real learner database or mark synthetic events as Spark execution.",
            "Coach starts at level 1. Case stage is an evaluation focus, not an unlocked help level.",
            "Store actual sent inputs, parameters, all failures, raw model output, and final visible output before reviewing.",
            "Review templates hide condition labels only; output structure may still reveal the condition.",
            "Keep plan-to-review ID mappings away from reviewers; preserve independent ratings and disagreements.",
        ],
        "artifactSha256": {name: _sha256(data) for name, data in files.items()},
    }
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    for name, data in files.items():
        (output / name).write_bytes(data)
    rng.shuffle(review_ids)
    for reviewer in (1, 2):
        filename = f"reviewer-{reviewer}.csv"
        with (output / filename).open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=REVIEW_FIELDS)
            writer.writeheader()
            writer.writerows({"reviewItemId": item_id} for item_id in review_ids)
        manifest["artifactSha256"][filename] = _sha256((output / filename).read_bytes())
    (output / "manifest.json").write_bytes(_json_bytes(manifest))
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare author-case paired evaluation materials offline; no model calls.")
    parser.add_argument("--output", type=Path, required=True, help="New output directory; existing paths are refused.")
    parser.add_argument("--model", default="unknown", help="Unverified model label; default: unknown.")
    parser.add_argument("--seed", type=int, default=0, help="Randomize plan order only; does not set a model seed.")
    parser.add_argument("--repeats", type=int, default=3, help="Repetitions per case per condition; default: 3.")
    args = parser.parse_args(argv)
    try:
        manifest = prepare(args.output, model=args.model, seed=args.seed, repeats=args.repeats)
    except (ValueError, OSError) as error:
        parser.error(str(error))
    print(f"Prepared {manifest['plan']['requestCount']} planned requests in {args.output}; evaluation not run; no model calls.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
