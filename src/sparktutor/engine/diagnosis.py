"""Descriptive, task-level learning feedback from explicitly eligible evidence.

The rates describe observed work, not latent ability or psychometric scores.
The research rationale and operational definitions live in the model document.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from typing import Iterable

from sparktutor.state.learning_events import LearningEvent


_ASSESSMENTS = {"mult_question", "cmd_question", "script"}
_CODE_TASKS = {"cmd_question", "script"}
_SOURCES = {"choice", "exact", "course_tests", "structural"}
_NAMES = {"knowledge": "知识掌握", "debugging": "调试修复", "hint_dependency": "提示使用覆盖", "transfer": "新情境迁移"}


def _pct(numerator: int, denominator: int) -> float | None:
    return round(100 * numerator / denominator, 1) if denominator else None


def _timestamp(event: LearningEvent) -> datetime:
    value = datetime.fromisoformat(event.timestamp.replace("Z", "+00:00"))
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _order(event: LearningEvent) -> datetime:
    # UUIDs identify events but do not encode chronology. Python's stable sort
    # keeps store insertion order when two timestamps have equal precision.
    return _timestamp(event)


def _key(event: LearningEvent) -> tuple[str, str, str]:
    return event.course_id, event.lesson_id, event.task_id


def _ref(spec: dict) -> tuple[str, str, str] | None:
    values = tuple(spec.get(name) for name in ("courseId", "lessonId", "stepId"))
    return values if all(isinstance(value, str) and value for value in values) else None


def _context(spec: dict) -> str:
    return (spec.get("transfer") or {}).get("context") or spec.get("context") or ""


def _catalog(catalog: Iterable[dict]) -> list[dict]:
    result = []
    seen = set()
    for raw in catalog:
        if not isinstance(raw, dict) or _ref(raw) is None or raw.get("taskType") not in _ASSESSMENTS:
            continue
        spec = dict(raw)
        ref = _ref(spec)
        if ref in seen:
            continue
        seen.add(ref)
        spec["taskId"] = spec.get("taskId") or f"{spec['lessonId']}:{spec['stepId']}"
        result.append(spec)
    return result


def _events(events: Iterable[LearningEvent]) -> tuple[list[LearningEvent], dict]:
    groups = defaultdict(list)
    invalid = 0
    for event in events:
        try:
            _timestamp(event)
        except (ValueError, TypeError, AttributeError):
            invalid += 1
            continue
        # A store-generated id is globally unique. Imported id-less events are
        # deduplicated only if all recorded fields are identical.
        fingerprint = json.dumps(event.as_dict(), sort_keys=True, ensure_ascii=False, default=str)
        groups[event.event_id or fingerprint].append((event, fingerprint))
    unique = []
    conflicts = 0
    duplicates = 0
    for values in groups.values():
        duplicates += len(values) - 1
        if len({fingerprint for _, fingerprint in values}) != 1:
            conflicts += 1
            continue  # Conflicting copies cannot be reliable evidence.
        unique.append(values[0][0])
    return sorted(unique, key=_order), {
        "duplicateEvents": duplicates, "conflictingEventIds": conflicts,
        "invalidTimestamps": invalid,
    }


def _eligible(event: LearningEvent) -> bool:
    data = event.data
    return (
        all(_key(event))
        and event.task_type in _ASSESSMENTS
        and data.get("assessmentEligible") is True
        and data.get("mode") != "dry_run"
        and data.get("executionMode") != "dry_run"
        and data.get("failureKind") not in {"infrastructure", "ai_failure", "dry_run"}
    )


def _assessment(event: LearningEvent) -> bool:
    return (
        event.event_type == "code_submit" and _eligible(event)
        and event.data.get("assessmentSource") in _SOURCES
        and type(event.data.get("passed")) is bool
        and (event.data["passed"] or event.data.get("failureKind") == "learner")
    )


def _debug_failure(event: LearningEvent) -> bool:
    if event.task_type not in _CODE_TASKS or not all(_key(event)):
        return False
    data = event.data
    # A real Python parser failure is evidence about code even when no Spark
    # runtime was available. A successful syntax check never proves a repair.
    if (event.event_type == "code_submit" and data.get("assessmentSource") == "syntax"
            and data.get("assessmentEligible") is True and data.get("failureKind") == "learner"
            and data.get("passed") is False):
        return True
    if not _eligible(event):
        return False
    return (
        (event.event_type == "code_run" and data.get("failureKind") == "learner"
         and type(data.get("exitCode")) is int and data["exitCode"] != 0)
        or (_assessment(event) and not data["passed"])
    )


def _help(event: LearningEvent, kind: str) -> bool:
    return event.event_type == kind and event.data.get("available") is not False


def _before(events: list[LearningEvent], end: LearningEvent, kind: str) -> bool:
    return any(_help(event, kind) and _order(event) <= _order(end) for event in events)


def _hinted(events: list[LearningEvent], end: LearningEvent) -> bool:
    return end.data.get("hintUsed") is True or _before(events, end, "hint_request")


def _independent(events: list[LearningEvent], end: LearningEvent) -> bool:
    return not _hinted(events, end) and not _before(events, end, "solution_view")


def _evidence(event: LearningEvent, outcome: str, **fields) -> dict:
    return {"courseId": event.course_id, "lessonId": event.lesson_id,
            "taskId": event.task_id, "outcome": outcome, **fields}


def _dimension(key: str, score: float | None, count: int, summary: str, metrics: dict,
               evidence: list[dict]) -> dict:
    return {
        "key": key, "name": _NAMES[key], "score": score,
        "direction": "descriptive" if key == "hint_dependency" else "higher_is_better",
        "evidenceCount": count, "sampleSize": count,
        "evidenceLevel": "none" if count == 0 else "limited" if count < 5 else "available",
        "confidence": "none" if count == 0 else "limited" if count < 5 else "sufficient",
        "summary": summary, "metrics": metrics,
        "evidence": {"items": evidence[:100], "totalCount": len(evidence), "limit": 100},
    }


def _knowledge(tasks: dict, submissions: dict) -> tuple[dict, dict]:
    count = len(submissions)
    passed = sum(events[-1].data["passed"] for events in submissions.values())
    first_passed = sum(events[0].data["passed"] for events in submissions.values())
    independent = [events[-1] for key, events in submissions.items() if _independent(tasks[key], events[-1])]
    independent_passed = sum(event.data["passed"] for event in independent)
    score = _pct(passed, count)
    supported = sum(any(_assessment(event) and _before(events, event, "solution_view") for event in events)
                    for events in tasks.values())
    metrics = {
        "evaluatedTasks": count, "latestPassedTasks": passed,
        "firstAttemptPassRate": _pct(first_passed, count),
        "independentTasks": len(independent), "independentPassedTasks": independent_passed,
        "independentPassRate": _pct(independent_passed, len(independent)),
        "assistedTasks": count - len(independent), "answerSupportedTasks": supported,
    }
    summary = (f"{count} 个任务的最近一次有效评估中有 {passed} 个通过；合理使用提示不会扣分。"
               if count else "尚无未受答案查看影响的有效知识评估。")
    evidence = [_evidence(events[-1], "passed" if events[-1].data["passed"] else "failed",
                          passed=events[-1].data["passed"], assisted=not _independent(tasks[key], events[-1]))
                for key, events in submissions.items()]
    return _dimension("knowledge", score, count, summary, metrics, evidence), metrics


def _debugging(tasks: dict) -> dict:
    episodes = repairs = independent = assisted = excluded = 0
    error_types = Counter()
    evidence = []
    for events in tasks.values():
        active = False
        failure_started = None
        failure_event = None
        episode_index = 0
        help_used = False
        answer_times = [_order(event) for event in events if _help(event, "solution_view")]
        answer_time = min(answer_times) if answer_times else None
        for event in events:
            if answer_time is not None and _order(event) >= answer_time:
                if active:
                    excluded += 1
                    active = False
                continue
            if _help(event, "hint_request") and active:
                help_used = True
            if event.task_type not in _CODE_TASKS:
                continue
            if _debug_failure(event):
                if not active:
                    active = True
                    failure_started = _order(event)
                    failure_event = event
                    help_used = _hinted(events, event)
                raw_types = event.data.get("errorTypes") or [event.data.get("errorType")]
                if isinstance(raw_types, str):
                    raw_types = [raw_types]
                error_types.update(str(value) for value in raw_types if value)
            elif active and _assessment(event) and event.data["passed"]:
                episodes += 1
                repairs += 1
                help_used = help_used or event.data.get("hintUsed") is True or any(
                    _help(hint, "hint_request") and failure_started <= _order(hint) <= _order(event)
                    for hint in events
                )
                assisted += int(help_used)
                independent += int(not help_used)
                episode_index += 1
                evidence.append(_evidence(event, "repaired", passed=True, assisted=help_used,
                                          episodeIndex=episode_index))
                active = False
        episodes += int(active)
        if active:
            evidence.append(_evidence(failure_event, "unresolved", passed=False, assisted=help_used,
                                      episodeIndex=episode_index + 1))
    return _dimension(
        "debugging", _pct(repairs, episodes), episodes,
        (f"{episodes} 个可观察失败过程有 {repairs} 个经后续评估确认修复，其中 {assisted} 个使用了提示。"
         if episodes else "尚无可用于评价调试修复的失败过程。"),
        {"failureEpisodes": episodes, "confirmedRepairs": repairs,
         "independentRepairs": independent, "assistedRepairs": assisted,
         "unresolvedEpisodes": episodes - repairs, "excludedAnswerEpisodes": excluded,
         "errorTypes": dict(error_types)}, evidence,
    )


def _hint_dependency(tasks: dict, assessed: dict) -> dict:
    hinted = later_hinted = requests = productive = answers = 0
    evidence = []
    for key, submissions in assessed.items():
        events = tasks[key]
        first = submissions[0]
        hinted += int(_hinted(events, first))
        first_hinted = _hinted(events, first)
        evidence.append(_evidence(first, "hinted" if first_hinted else "not_hinted", hinted=first_hinted))
        answers += int(_before(events, first, "solution_view"))
        hints = [event for event in events if _help(event, "hint_request")]
        later_hinted += int(any(_order(event) > _order(first) for event in hints))
        # Requests on unassessed tasks never enter either side of these rates.
        requests += len(hints)
        productive += sum(any(_order(submit) > _order(hint) and submit.data["passed"]
                              and not _before(events, submit, "solution_view")
                              for submit in submissions) for hint in hints)
    count = len(assessed)
    return _dimension(
        "hint_dependency", _pct(hinted, count), count,
        (f"{count} 个已评估任务中有 {hinted} 个在首次有效评估前使用提示；这描述求助行为，不代表能力高低。"
         if count else "尚无已评估任务，不能计算提示使用覆盖率。"),
        {"evaluatedTasks": count, "hintedBeforeFirstAssessment": hinted,
         "laterHintedTasks": later_hinted, "hintRequests": requests,
         "productiveHints": productive, "hintProductivity": _pct(productive, requests),
         "answerViewedBeforeFirstAssessment": answers}, evidence,
    )


def _transfer_ready(spec: dict, by_ref: dict, knowledge: dict, before: LearningEvent | None = None) -> bool:
    sources = (spec.get("transfer") or {}).get("sources", [])
    context = _context(spec)
    if not sources or not context:
        return False
    for source in sources:
        source_spec = by_ref.get(_ref(source)) if isinstance(source, dict) else None
        if source_spec is None or not _context(source_spec) or _context(source_spec) == context:
            return False
        if not set(spec.get("knowledgeComponents") or []).intersection(source_spec.get("knowledgeComponents") or []):
            return False
        source_key = (source_spec["courseId"], source_spec["lessonId"], source_spec["taskId"])
        prior = [event for event in knowledge.get(source_key, []) if before is None or _order(event) < _order(before)]
        if not prior or not prior[-1].data["passed"]:
            return False
    return True


def _novel_context(spec: dict, key: tuple, assessed: dict, by_key: dict,
                   before: LearningEvent | None = None) -> bool:
    for other_key, other_submits in assessed.items():
        if (other_key != key and _context(by_key.get(other_key, {})) == _context(spec)
                and (before is None or _order(other_submits[0]) <= _order(before))):
            return False
    return True


def _transfer(tasks: dict, assessed: dict, knowledge: dict, specs: list[dict], scope: str) -> dict:
    by_ref = {_ref(spec): spec for spec in specs}
    by_key = {(spec["courseId"], spec["lessonId"], spec["taskId"]): spec for spec in specs}
    count = passed = independent = independent_passed = excluded = 0
    evidence = []
    for key, submits in assessed.items():
        if scope and key[0] != scope:
            continue
        spec = by_key.get(key, {})
        if not spec.get("transfer"):
            continue
        first = submits[0]
        if (not _transfer_ready(spec, by_ref, knowledge, before=first)
                or not _novel_context(spec, key, assessed, by_key, before=first)
                or _before(tasks[key], first, "solution_view")):
            excluded += 1
            continue
        count += 1
        passed += int(first.data["passed"])
        is_independent = _independent(tasks[key], first)
        independent += int(is_independent)
        independent_passed += int(is_independent and first.data["passed"])
        evidence.append(_evidence(first, "passed" if first.data["passed"] else "failed",
                                  passed=first.data["passed"], assisted=not is_independent))
    return _dimension(
        "transfer", _pct(passed, count), count,
        (f"{count} 个具备已通过来源和新情境的任务中，有 {passed} 个首次评估通过。"
         if count else "尚无同时满足来源已通过、新情境和有效首次评估的迁移证据。"),
        {"eligibleTransferTasks": count, "firstPassedTasks": passed,
         "independentTasks": independent, "independentPassedTasks": independent_passed,
         "independentPassRate": _pct(independent_passed, independent),
         "excludedTransferTasks": excluded}, evidence,
    )


def _recommend(specs: list[dict], assessed: dict, knowledge: dict, dimensions: list[dict],
               course_id: str, depth: str) -> tuple[dict | None, str]:
    by_ref = {_ref(spec): spec for spec in specs}
    by_key = {(spec["courseId"], spec["lessonId"], spec["taskId"]): spec for spec in specs}
    mastered = set()
    completed = set()
    weak_components = set()
    for spec in specs:
        key = (spec["courseId"], spec["lessonId"], spec["taskId"])
        if knowledge.get(key) and knowledge[key][-1].data["passed"]:
            mastered.add(_ref(spec))
        elif knowledge.get(key) and (not course_id or spec["courseId"] == course_id):
            weak_components.update(spec.get("knowledgeComponents") or [])
        if any(event.data["passed"] for event in assessed.get(key, [])):
            completed.add(_ref(spec))
    measured = [item for item in dimensions if item["score"] is not None and item["direction"] != "descriptive"]
    candidates = []
    for index, spec in enumerate(specs):
        if (course_id and spec["courseId"] != course_id) or spec.get("depth", "all") not in ("all", depth):
            continue
        if _ref(spec) in completed:
            continue
        prerequisites = list(spec.get("prerequisites") or []) + list((spec.get("transfer") or {}).get("sources") or [])
        refs = [_ref(value) if isinstance(value, dict) else None for value in prerequisites]
        if any(ref not in by_ref or ref not in mastered for ref in refs):
            continue
        key = (spec["courseId"], spec["lessonId"], spec["taskId"])
        targets = set(spec.get("targetDimensions") or ["knowledge"]) - {"hint_dependency"}
        if "transfer" in targets:
            if (key in assessed or not _transfer_ready(spec, by_ref, knowledge)
                    or not _novel_context(spec, key, assessed, by_key)):
                targets.remove("transfer")
                targets.add("knowledge")  # Reattempts consolidate, never prove new transfer.
        if not targets:
            targets.add("knowledge")
        component_match = not weak_components or bool(weak_components.intersection(spec.get("knowledgeComponents") or []))
        candidates.append((spec, targets, (not component_match, key not in assessed, index)))
    if not candidates:
        return None, "当前范围内没有同时满足未通过、难度和前置条件的可推荐练习。"
    available = set().union(*(targets for _, targets, _ in candidates))
    gaps = sorted((item for item in measured if item["score"] < 100), key=lambda item: item["score"])
    focus = next((item["key"] for item in gaps if item["key"] in available), None)
    if focus is None and gaps:
        focus = "knowledge"  # No new eligible transfer task: consolidate the observed gap.
    if focus is None:
        missing = {item["key"] for item in dimensions if item["score"] is None}
        # Source mastery is enforced above, so transfer sampling never skips
        # its prerequisites. With no history at all, start with knowledge.
        priority = ["transfer", "debugging", "knowledge"] if measured or mastered else ["knowledge", "transfer", "debugging"]
        focus = next((key for key in priority if key in missing and key in available), "knowledge")
    spec, targets, _ = min(candidates, key=lambda item: (focus not in item[1], *item[2]))
    target = focus if focus in targets else next((key for key in ("knowledge", "debugging", "transfer") if key in targets), "knowledge")
    chosen_key = (spec["courseId"], spec["lessonId"], spec["taskId"])
    if target == "transfer":
        reason = "来源练习已通过且此题提供尚未评估的新情境，用下一次首次评估检验迁移表现。"
    elif spec.get("transfer") and chosen_key in assessed:
        reason = "此情境已经评估过，重做用于巩固相关知识，不作为新的迁移证据。"
    else:
        reason = (f"此题满足前置条件且尚未通过，可继续观察你的{_NAMES[target]}表现。"
                  if measured else "先完成一道符合当前难度和前置条件的练习，建立可核验的学习证据。")
    return {
        "courseId": spec["courseId"], "lessonId": spec["lessonId"], "stepId": spec["stepId"],
        "taskId": spec["taskId"], "lessonIndex": spec.get("lessonIndex", 0),
        "title": spec.get("title") or "课程练习", "dimension": target,
        "reason": reason,
    }, ""


def _diagnostic_sentence(dimensions: list[dict], recommendation: dict | None) -> str:
    by_key = {item["key"]: item for item in dimensions}
    measured = [item for item in dimensions if item["score"] is not None and item["direction"] != "descriptive"]
    if not measured:
        return "目前有效评估证据不足，先完成符合前置条件的练习，再观察知识、调试和迁移表现。"
    if recommendation is None:
        return "当前记录反映已评估任务的表现，暂时没有满足前置条件的后续练习，缺失维度仍需新证据。"
    target = recommendation["dimension"]
    item = by_key[target]
    if target == "transfer":
        if item["score"] is None:
            return "已有来源题目通过，下一题将检验新情境迁移，首次评估前尚不能给出迁移结论。"
        return f"已有新情境任务的首次通过率为 {item['score']:g}%，下一题将在满足前置条件的新情境中继续检验迁移表现。"
    if target == "debugging" and item["score"] is None:
        return "当前尚缺少可观察的调试修复轨迹，推荐练习将补充这一类证据，不能据此认定调试能力不足。"
    if target == "knowledge" and "不作为新的迁移证据" in recommendation["reason"]:
        return "该新情境练习尚未通过，推荐先巩固相关知识，重做本题不会被计为新的迁移证据。"
    if item["score"] == 100:
        return f"已有{item['name']}评估均通过，推荐练习将扩充观察样本，少量成功记录仍不能代表全面掌握。"
    if item["score"] is None:
        return f"当前尚缺少{item['name']}的有效证据，推荐练习用于建立可核验的观察记录。"
    qualifier = "当前样本较少，" if item["evidenceCount"] < 5 else "依据当前记录，"
    return f"{qualifier}{item['name']}的观察通过率为 {item['score']:g}%，推荐练习将针对这一方面继续巩固和验证。"


def build_diagnosis(events: Iterable[LearningEvent], exercise_catalog: Iterable[dict] = (), *,
                    course_id: str = "", depth: str = "beginner") -> dict:
    """Aggregate eligible events and choose at most one real catalog exercise.

    Pass the full catalog/history even for a course-scoped view so transfer and
    recommendation prerequisites can refer to a different course correctly.
    """
    ordered, quality = _events(events)
    specs = _catalog(exercise_catalog)
    tasks = defaultdict(list)
    for event in ordered:
        if all(_key(event)):
            tasks[_key(event)].append(event)
    assessed = {key: [event for event in values if _assessment(event)] for key, values in tasks.items()}
    assessed = {key: values for key, values in assessed.items() if values}
    knowledge = {key: [event for event in values if not _before(tasks[key], event, "solution_view")]
                 for key, values in assessed.items()}
    knowledge = {key: values for key, values in knowledge.items() if values}
    scoped_tasks = {key: values for key, values in tasks.items() if not course_id or key[0] == course_id}
    scoped_assessed = {key: values for key, values in assessed.items() if key in scoped_tasks}
    scoped_knowledge = {key: values for key, values in knowledge.items() if key in scoped_tasks}
    knowledge_dimension, _ = _knowledge(scoped_tasks, scoped_knowledge)
    dimensions = [knowledge_dimension, _debugging(scoped_tasks),
                  _hint_dependency(scoped_tasks, scoped_assessed),
                  _transfer(tasks, assessed, knowledge, specs, course_id)]
    by_key = {(spec["courseId"], spec["lessonId"], spec["taskId"]): spec for spec in specs}
    for dimension in dimensions:
        for item in dimension["evidence"]["items"]:
            spec = by_key.get((item["courseId"], item["lessonId"], item["taskId"]), {})
            item["stepId"] = spec.get("stepId", "")
            item["title"] = spec.get("title") or item["taskId"]
    recommendation, no_recommendation = _recommend(specs, assessed, knowledge, dimensions, course_id, depth)
    diagnosis = _diagnostic_sentence(dimensions, recommendation)
    components = defaultdict(lambda: {"evaluatedTasks": 0, "latestPassedTasks": 0})
    for spec in specs:
        key = (spec["courseId"], spec["lessonId"], spec["taskId"])
        if key not in scoped_knowledge:
            continue
        for component in set(spec.get("knowledgeComponents") or []):
            components[component]["evaluatedTasks"] += 1
            components[component]["latestPassedTasks"] += int(scoped_knowledge[key][-1].data["passed"])
    return {
        "model": "formative-evidence-v1",
        "disclaimer": "这些比率描述已观察到的学习行为，用于形成性反馈，不是标准化能力测量；低样本或缺少证据时不作能力定论。",
        "eventCount": sum(not course_id or event.course_id == course_id for event in ordered),
        "eligibleAssessmentCount": sum(len(values) for values in scoped_assessed.values()),
        "eligibleEventCount": sum(_assessment(event) or _debug_failure(event)
                                  for values in scoped_tasks.values() for event in values),
        "dataQuality": quality, "diagnosis": diagnosis, "dimensions": dimensions,
        "knowledgeComponents": dict(components), "recommendedExercise": recommendation,
        "recommendationReason": no_recommendation,
    }
