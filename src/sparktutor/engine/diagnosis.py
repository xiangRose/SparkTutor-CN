"""Explainable rule-based learner diagnosis from observable event evidence."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Iterable

from sparktutor.state.learning_events import LearningEvent


@dataclass(frozen=True)
class DimensionResult:
    key: str
    name: str
    score: float | None
    confidence: str
    evidence_count: int
    summary: str
    recommendation: str
    metrics: dict

    def as_dict(self) -> dict:
        return {
            "key": self.key, "name": self.name, "score": self.score,
            "confidence": self.confidence, "evidenceCount": self.evidence_count,
            "summary": self.summary, "recommendation": self.recommendation,
            "metrics": self.metrics,
        }


def _confidence(n: int) -> str:
    if n >= 12:
        return "high"
    if n >= 5:
        return "medium"
    return "low"


def _pct(numerator: int, denominator: int) -> float | None:
    return round(100 * numerator / denominator, 1) if denominator else None


def _knowledge(events: list[LearningEvent]) -> DimensionResult:
    submits = [e for e in events if e.event_type == "code_submit"]
    passed = sum(bool(e.data.get("passed")) for e in submits)
    first = [e for e in submits if e.attempt_number == 1]
    first_passed = sum(bool(e.data.get("passed")) for e in first)
    independent = [e for e in submits if not e.data.get("hintUsed")]
    independent_passed = sum(bool(e.data.get("passed")) for e in independent)
    if not submits:
        score = None
    else:
        overall = passed / len(submits)
        first_rate = first_passed / len(first) if first else overall
        independent_rate = independent_passed / len(independent) if independent else 0
        score = round(100 * (.5 * overall + .3 * first_rate + .2 * independent_rate), 1)
    return DimensionResult(
        "knowledge", "知识掌握度", score, _confidence(len(submits)), len(submits),
        "基于提交正确率、首次正确率和独立完成情况的规则型估计。" if submits else "尚无提交证据。",
        "继续完成同一知识点的不同任务，以提高估计可信度。",
        {"submissions": len(submits), "passRate": _pct(passed, len(submits)),
         "firstAttemptPassRate": _pct(first_passed, len(first)),
         "independentPassRate": _pct(independent_passed, len(independent))},
    )


def _debugging(events: list[LearningEvent]) -> DimensionResult:
    runs = [e for e in events if e.event_type == "code_run"]
    errors = [e for e in runs if e.data.get("exitCode", 0) != 0]
    repaired = 0
    error_types = Counter(str(e.data.get("errorType") or "Unknown") for e in errors)
    for index, event in enumerate(runs[:-1]):
        if event.data.get("exitCode", 0) == 0:
            continue
        following = runs[index + 1]
        between = [x for x in events if event.timestamp < x.timestamp < following.timestamp]
        if (following.task_id == event.task_id and following.data.get("exitCode", 1) == 0
                and not any(x.event_type in {"hint_request", "solution_view"} for x in between)):
            repaired += 1
    score = _pct(repaired, len(errors))
    common = error_types.most_common(2)
    detail = "、".join(name for name, _ in common)
    return DimensionResult(
        "debugging", "调试自修复能力", score, _confidence(len(errors)), len(errors),
        (f"记录到 {len(errors)} 次错误，其中 {repaired} 次在未请求支架时自行修复。"
         + (f" 常见错误：{detail}。" if detail else "")) if errors else "尚无错误修复轨迹。",
        "运行失败后先阅读错误信息并尝试一次修改，再请求提示。",
        {"errorRuns": len(errors), "independentRepairs": repaired,
         "selfRepairRate": score, "errorTypes": dict(error_types)},
    )


def _help_seeking(events: list[LearningEvent]) -> DimensionResult:
    hints = [e for e in events if e.event_type == "hint_request"]
    productive = 0
    premature = sum(e.attempt_number == 0 for e in hints)
    for hint in hints:
        later = [e for e in events if e.task_id == hint.task_id and e.timestamp > hint.timestamp]
        submit = next((e for e in later if e.event_type == "code_submit"), None)
        if submit and submit.data.get("passed"):
            productive += 1
    score = None if not hints else round(100 * max(0, productive - .5 * premature) / len(hints), 1)
    return DimensionResult(
        "helpSeeking", "帮助寻求与支架依赖", score, _confidence(len(hints)), len(hints),
        (f"{productive}/{len(hints)} 次求助后完成任务；{premature} 次发生在首次尝试前。"
         if hints else "尚无求助行为证据；不把“未求助”自动判定为高能力。"),
        "遇到困难时先尝试并阅读反馈；查看提示后用自己的代码完成任务。",
        {"hintRequests": len(hints), "productiveHints": productive,
         "prematureHints": premature, "productiveHelpRate": score},
    )


def _transfer(events: list[LearningEvent]) -> DimensionResult:
    transfers = [e for e in events if e.event_type in {"transfer_task_complete", "code_submit"}
                 and e.data.get("isTransfer")]
    passed = sum(bool(e.data.get("passed")) for e in transfers)
    independent = sum(bool(e.data.get("passed")) and not e.data.get("hintUsed") for e in transfers)
    score = _pct(passed, len(transfers))
    return DimensionResult(
        "transfer", "知识迁移能力", score, _confidence(len(transfers)), len(transfers),
        (f"已完成 {len(transfers)} 个有知识关系标签的迁移任务，其中 {passed} 个通过。"
         if transfers else "课程尚未产生带迁移关系标签的任务证据。"),
        "为课程题目添加 KnowledgeComponents、Transfer 与 SourceTask 标签后再评估迁移。",
        {"transferTasks": len(transfers), "passed": passed,
         "independentPassed": independent, "transferPassRate": score},
    )


def build_diagnosis(events: Iterable[LearningEvent]) -> dict:
    ordered = sorted(events, key=lambda e: e.timestamp)
    dimensions = [_knowledge(ordered), _debugging(ordered),
                  _help_seeking(ordered), _transfer(ordered)]
    by_component = defaultdict(lambda: {"attempts": 0, "passed": 0})
    for event in ordered:
        if event.event_type != "code_submit":
            continue
        for component in event.knowledge_components:
            by_component[component]["attempts"] += 1
            by_component[component]["passed"] += int(bool(event.data.get("passed")))
    return {
        "model": "explainable-rules-v1",
        "disclaimer": "这是基于可观测行为的学习状态估计，不是成绩、心理测量或能力定论。",
        "eventCount": len(ordered),
        "dimensions": [d.as_dict() for d in dimensions],
        "knowledgeComponents": dict(by_component),
    }
