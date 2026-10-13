"""Read-only, on-demand reconstruction of a bounded set of evidence points."""

from __future__ import annotations

from datetime import datetime

from sparktutor.engine.learning_history import (
    LearningHistory,
    event_timestamp,
    is_diagnosis_event,
    resolve_window_end,
    score_snapshot,
    window_start,
)


_POINT_LIMIT = 20
_SCORE_FIELDS = ("key", "name", "score", "sampleSize", "direction", "numerator", "denominator")
_LABELS = {
    "code_submit": "提交评估",
    "code_run": "运行中出现代码错误",
    "solution_view": "查看参考答案",
}


def build_learning_trend(registry, event_store, *, course_id: str = "", window: str = "all",
                         snapshot_event_id: str | None = None, depth: str = "beginner",
                         window_end: str | None = None, now: datetime | None = None) -> dict:
    """Keep every prerequisite event while selecting only the displayed points.

    A point describes the prefix ending at that specific stored record. It is
    not a saved historical model state or an estimate of causal learning gain.
    The insertion snapshot also excludes later imports with backdated times.
    """
    context = LearningHistory(registry, event_store, snapshot_event_id=snapshot_event_id)
    context.validate_course(course_id)
    end = resolve_window_end(window_end, now=now)
    start = window_start(window, now=end)
    candidates = []
    for index, event in enumerate(context.events):
        key = (event.course_id, event.lesson_id, event.task_id)
        if not event.event_id or key not in context.tasks or not is_diagnosis_event(event):
            continue
        if course_id and event.course_id != course_id:
            continue
        timestamp = event_timestamp(event)
        if start is not None and not start <= timestamp <= end:
            continue
        candidates.append((index, event))
    specifications = {
        (spec["courseId"], spec["lessonId"], spec["taskId"]): spec
        for spec in context.catalog
    }
    points = []
    for index, event in candidates[-_POINT_LIMIT:]:
        # Scoping belongs inside the model, not before prefix reconstruction.
        snapshot = score_snapshot(context.events[:index + 1], context.catalog,
                                  course_id=course_id, depth=depth)
        specification = specifications.get((event.course_id, event.lesson_id, event.task_id), {})
        points.append({
            "eventId": event.event_id, "timestamp": event_timestamp(event).isoformat(),
            "courseId": event.course_id, "lessonId": event.lesson_id, "taskId": event.task_id,
            "title": specification.get("title") or event.task_id,
            "type": event.event_type, "eventLabel": _LABELS.get(event.event_type, "诊断证据记录"),
            "dimensions": [{field: dimension[field] for field in _SCORE_FIELDS}
                           for dimension in snapshot["dimensions"]],
        })
    return {
        "courseId": course_id, "window": window, "windowEnd": end.isoformat(),
        "snapshotEventId": context.snapshot_event_id,
        "points": points, "total": len(candidates), "limit": _POINT_LIMIT,
        "hasMore": len(candidates) > len(points), "dataQuality": context.data_quality,
        "disclaimer": "仅展示最近 20 个证据记录点，每点按当前规则与课程标注使用完整过去历史重建。"
                      "曲线变化可能来自样本组成、难度、答案影响或日志缺失，不直接证明能力变化或因果效果；"
                      "提示覆盖描述求助方式，缺少证据保留空值。",
    }
