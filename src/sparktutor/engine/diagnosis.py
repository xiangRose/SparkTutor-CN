"""Explainable, evidence-aware learner diagnosis from observable events.

The scores are engineering heuristics for formative feedback.  They are not
psychometric measurements and deliberately become ``None`` when the event log
does not contain suitable evidence.
"""

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
            "key": self.key,
            "name": self.name,
            "score": self.score,
            "confidence": self.confidence,
            "evidenceCount": self.evidence_count,
            "summary": self.summary,
            "recommendation": self.recommendation,
            "metrics": self.metrics,
        }


def _confidence(count: int) -> str:
    if count >= 12:
        return "high"
    if count >= 5:
        return "medium"
    return "low"


def _pct(numerator: float, denominator: int) -> float | None:
    return round(100 * numerator / denominator, 1) if denominator else None


def _knowledge(events: list[LearningEvent]) -> DimensionResult:
    submits = [event for event in events if event.event_type == "code_submit"]
    passed = sum(bool(event.data.get("passed")) for event in submits)
    first = [event for event in submits if event.attempt_number <= 1]
    first_passed = sum(bool(event.data.get("passed")) for event in first)
    independent = [event for event in submits if not event.data.get("hintUsed")]
    independent_passed = sum(bool(event.data.get("passed")) for event in independent)
    score = None
    if submits:
        # Multiple indicators make the estimate less sensitive to repeatedly
        # submitting until a solution passes.  These weights are transparent
        # product heuristics, not fitted psychometric parameters.
        overall = passed / len(submits)
        first_rate = first_passed / len(first) if first else overall
        independent_rate = independent_passed / len(independent) if independent else 0
        score = round(100 * (0.5 * overall + 0.3 * first_rate + 0.2 * independent_rate), 1)
    return DimensionResult(
        "knowledge",
        "知识掌握度",
        score,
        _confidence(len(submits)),
        len(submits),
        "综合提交正确率、首次正确率与独立完成率。" if submits else "尚无提交证据。",
        "完成同一知识点的变式练习，并在提交前解释代码为什么成立。",
        {
            "submissions": len(submits),
            "passRate": _pct(passed, len(submits)),
            "firstAttemptPassRate": _pct(first_passed, len(first)),
            "independentPassRate": _pct(independent_passed, len(independent)),
        },
    )


def _debugging(events: list[LearningEvent]) -> DimensionResult:
    """Measure task-level repair episodes, not merely adjacent run pairs."""
    by_task: dict[str, list[LearningEvent]] = defaultdict(list)
    for event in events:
        if event.task_id:
            by_task[event.task_id].append(event)

    episodes = 0
    repaired = 0
    assisted_repairs = 0
    error_types: Counter[str] = Counter()
    for task_events in by_task.values():
        active_failure = False
        assisted = False
        for event in task_events:
            if event.event_type in {"hint_request", "solution_view"} and active_failure:
                assisted = True
                continue
            failed_run = event.event_type == "code_run" and event.data.get("exitCode", 0) != 0
            failed_submit = event.event_type == "code_submit" and not event.data.get("passed")
            if failed_run or failed_submit:
                if not active_failure:
                    episodes += 1
                    active_failure = True
                    assisted = False
                raw_types = event.data.get("errorTypes") or [event.data.get("errorType")]
                for error_type in raw_types:
                    if error_type:
                        error_types[str(error_type)] += 1
                continue
            successful_run = event.event_type == "code_run" and event.data.get("exitCode", 1) == 0
            successful_submit = event.event_type == "code_submit" and bool(event.data.get("passed"))
            if active_failure and (successful_run or successful_submit):
                if assisted:
                    assisted_repairs += 1
                else:
                    repaired += 1
                active_failure = False

    score = _pct(repaired, episodes)
    common = "、".join(name for name, _ in error_types.most_common(2))
    summary = "尚无错误修复轨迹。"
    if episodes:
        summary = f"记录到 {episodes} 个失败修复过程，其中 {repaired} 个未借助提示完成。"
        if common:
            summary += f" 常见错误：{common}。"
    return DimensionResult(
        "debugging",
        "调试自修复能力",
        score,
        _confidence(episodes),
        episodes,
        summary,
        "失败后先定位错误类型、提出一个修复假设，再运行验证。",
        {
            "failureEpisodes": episodes,
            "independentRepairs": repaired,
            "assistedRepairs": assisted_repairs,
            "selfRepairRate": score,
            "errorTypes": dict(error_types),
        },
    )


def _hint_independence(events: list[LearningEvent]) -> DimensionResult:
    hints = [event for event in events if event.event_type == "hint_request"]
    attempted_tasks = {
        event.task_id
        for event in events
        if event.task_id and event.event_type in {"code_run", "code_submit"}
    }
    hinted_tasks = {event.task_id for event in hints if event.task_id}
    premature = sum(event.attempt_number == 0 for event in hints)
    productive = 0
    for hint in hints:
        later = [
            event
            for event in events
            if event.task_id == hint.task_id and event.timestamp > hint.timestamp
        ]
        if any(event.event_type == "code_submit" and event.data.get("passed") for event in later):
            productive += 1

    dependency_risk = _pct(len(hinted_tasks), len(attempted_tasks))
    productivity = _pct(productive, len(hints))
    independence = None if dependency_risk is None else round(100 - dependency_risk, 1)
    if hints:
        summary = (
            f"{len(hinted_tasks)}/{len(attempted_tasks)} 个已尝试任务使用了提示；"
            f"{productive}/{len(hints)} 次提示后最终完成，{premature} 次在首次尝试前请求。"
        )
    elif attempted_tasks:
        summary = "已有独立尝试记录，尚未请求提示；目前只能说明使用频率较低。"
    else:
        summary = "尚无可用于判断提示使用方式的任务记录。"
    return DimensionResult(
        "hint_independence",
        "提示使用自主性",
        independence,
        _confidence(len(attempted_tasks)),
        len(attempted_tasks),
        summary,
        "先独立尝试并记录卡点，再把提示用于检验思路而非直接替代解题。",
        {
            "attemptedTasks": len(attempted_tasks),
            "hintedTasks": len(hinted_tasks),
            "hintRequests": len(hints),
            "prematureHints": premature,
            "hintDependencyRisk": dependency_risk,
            "hintProductivity": productivity,
        },
    )


def _transfer(events: list[LearningEvent]) -> DimensionResult:
    transfers = [
        event
        for event in events
        if event.event_type == "code_submit" and event.data.get("isTransfer")
    ]
    passed = sum(bool(event.data.get("passed")) for event in transfers)
    independent = sum(
        bool(event.data.get("passed")) and not event.data.get("hintUsed")
        for event in transfers
    )
    score = _pct(independent, len(transfers))
    return DimensionResult(
        "transfer",
        "知识迁移能力",
        score,
        _confidence(len(transfers)),
        len(transfers),
        (
            f"完成 {len(transfers)} 次带来源关系的迁移任务，{independent} 次独立通过。"
            if transfers
            else "尚未产生带知识来源标签的迁移任务证据。"
        ),
        "在新数据或新业务情境中重做已学操作，并说明可复用的原则。",
        {
            "transferSubmissions": len(transfers),
            "passed": passed,
            "independentPassed": independent,
            "independentTransferRate": score,
        },
    )


EXERCISE_CATALOG = {
    "knowledge": {
        "courseId": "learning_spark",
        "lessonId": "02_dataframes_schemas",
        "lessonIndex": 1,
        "stepIndex": 4,
        "title": "DataFrame 与 Schema 变式练习",
    },
    "debugging": {
        "courseId": "learning_spark",
        "lessonId": "06_optimization_tuning",
        "lessonIndex": 5,
        "stepIndex": 4,
        "title": "执行计划与性能诊断练习",
    },
    "hint_independence": {
        "courseId": "learning_spark",
        "lessonId": "03_data_sources",
        "lessonIndex": 2,
        "stepIndex": 4,
        "title": "数据读写独立练习",
    },
    "transfer": {
        "courseId": "learning_spark",
        "lessonId": "03_data_sources",
        "lessonIndex": 2,
        "stepIndex": 4,
        "title": "Schema 到数据读写的迁移练习",
    },
}


def _overall_diagnosis(dimensions: list[DimensionResult]) -> tuple[str, DimensionResult | None]:
    measured = [dimension for dimension in dimensions if dimension.score is not None]
    if not measured:
        return "目前证据不足；先完成一次运行和提交，我会从真实学习轨迹生成诊断。", None
    weakest = min(measured, key=lambda dimension: dimension.score or 0)
    strongest = max(measured, key=lambda dimension: dimension.score or 0)
    if weakest.evidence_count < 5:
        return (
            f"目前初步表现中，{strongest.name}相对较好，{weakest.name}需要更多练习；"
            "由于样本较少，这只是低置信度提示。",
            weakest,
        )
    return (
        f"你目前的优势是{strongest.name}，最值得优先提升的是{weakest.name}；"
        f"建议先完成针对性练习，再观察该维度是否改善。",
        weakest,
    )


def build_diagnosis(events: Iterable[LearningEvent]) -> dict:
    ordered = sorted(events, key=lambda event: (event.timestamp, event.event_id))
    dimensions = [
        _knowledge(ordered),
        _debugging(ordered),
        _hint_independence(ordered),
        _transfer(ordered),
    ]
    diagnosis, weakest = _overall_diagnosis(dimensions)

    by_component = defaultdict(lambda: {"attempts": 0, "passed": 0})
    for event in ordered:
        if event.event_type != "code_submit":
            continue
        for component in event.data.get("knowledgeComponents", []):
            by_component[str(component)]["attempts"] += 1
            by_component[str(component)]["passed"] += int(bool(event.data.get("passed")))

    recommendation = None
    if weakest:
        recommendation = {
            **EXERCISE_CATALOG[weakest.key],
            "dimension": weakest.key,
            "reason": f"{weakest.name}当前估计为 {weakest.score:.1f} 分；{weakest.recommendation}",
        }
    return {
        "model": "explainable-rules-v2",
        "disclaimer": "这是基于可观测行为的形成性学习提示，不是成绩、心理测量或能力定论。权重未经本项目人群效度验证。",
        "eventCount": len(ordered),
        "diagnosis": diagnosis,
        "dimensions": [dimension.as_dict() for dimension in dimensions],
        "knowledgeComponents": dict(by_component),
        "recommendedExercise": recommendation,
    }
