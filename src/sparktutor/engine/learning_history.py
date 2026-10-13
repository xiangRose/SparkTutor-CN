"""Read-only task history and prefix-based review of formative evidence."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
import re

from sparktutor.engine.diagnosis import (
    _assessment, _before, _debug_failure, _events, _help, _key, _timestamp, build_diagnosis,
)
from sparktutor.engine.lesson_loader import load_lesson


DISCLAIMER = (
    "历史分数按当前评分规则和课程标注、使用该节点以前的完整日志重建，并非当时保存的快照；"
    "教材或规则改版、缺失日志会影响重建。时间筛选和分页只影响展示，不裁剪评分历史。"
    "变化表示观察比例或样本变化，不代表能力的因果增减；提示覆盖是描述性指标。"
    "同一时刻按存储顺序定位节点，答案和提示的保守规则仅作用于该节点已出现的记录。"
)
_TASK_TYPES = {"mult_question", "cmd_question", "script"}
_NUMERATORS = {"knowledge": "latestPassedTasks", "debugging": "confirmedRepairs",
               "hint_dependency": "hintedBeforeFirstAssessment", "transfer": "firstPassedTasks"}
_EDIT_FIELDS = {"changeCount", "addedChars", "removedChars", "documentVersion", "burstDurationMs"}
_SOURCES = {"choice", "exact", "course_tests", "structural", "syntax", "ai_review", "execution"}
_MODES = {"local", "docker", "databricks", "dry_run", "local_check"}


def event_timestamp(event):
    return _timestamp(event)


def resolve_window_end(value=None, now: datetime | None = None) -> datetime:
    if value is None:
        result = now or datetime.now(timezone.utc)
        return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result.astimezone(timezone.utc)
    if not isinstance(value, str) or not value:
        raise ValueError("windowEnd must be an ISO timestamp with a timezone")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            raise ValueError("timezone missing")
        return result.astimezone(timezone.utc)
    except (ValueError, OverflowError) as error:
        raise ValueError("windowEnd must be an ISO timestamp with a timezone") from error


def window_start(window: str, now: datetime | None = None) -> datetime | None:
    if not isinstance(window, str) or window not in {"all", "7d", "30d"}:
        raise ValueError("window must be all, 7d or 30d")
    now = resolve_window_end(now=now)
    return None if window == "all" else now - timedelta(days=7 if window == "7d" else 30)


def _page(offset, limit, maximum):
    if type(offset) is not int or offset < 0:
        raise ValueError("offset must be a non-negative integer")
    if type(limit) is not int or not 1 <= limit <= maximum:
        raise ValueError(f"limit must be an integer between 1 and {maximum}")


def is_diagnosis_event(event) -> bool:
    return _assessment(event) or _debug_failure(event) or _help(event, "solution_view")


def score_snapshot(events, catalog, *, course_id="", depth="beginner") -> dict:
    report = build_diagnosis(events, catalog, course_id=course_id, depth=depth)
    dimensions = []
    for dimension in report["dimensions"]:
        metrics = dimension["metrics"]
        dimensions.append({
            "key": dimension["key"], "name": dimension["name"], "score": dimension["score"],
            "sampleSize": dimension["sampleSize"], "direction": dimension["direction"],
            "numerator": metrics[_NUMERATORS[dimension["key"]]],
            "denominator": dimension["sampleSize"],
            # Error categories are shown separately on events, never raw logs.
            "metrics": {key: value for key, value in metrics.items()
                        if value is None or type(value) in {int, float, bool}},
        })
    return {"dimensions": dimensions}


class LearningHistory:
    """One insertion-bounded snapshot, normalized exactly like diagnosis."""

    def __init__(self, registry, event_store, *, snapshot_event_id=None):
        raw = event_store.list_events(limit=None, order="insertion")
        if snapshot_event_id is not None:
            if not isinstance(snapshot_event_id, str):
                raise ValueError("snapshotEventId must be a string")
            if snapshot_event_id == "":
                raw = []  # A previously returned empty snapshot remains empty.
            else:
                matches = [index for index, event in enumerate(raw) if event.event_id == snapshot_event_id]
                if not matches:
                    raise ValueError("历史快照已不可用，请刷新后重试。")
                raw = raw[:matches[-1] + 1]
        self.snapshot_event_id = raw[-1].event_id if raw else ""
        self.events, self.data_quality = _events(raw)
        self.catalog = []
        self.courses = []
        self._course_names = {}
        self._lesson_names = {}
        self.warnings = []
        for course in registry.list_courses():
            self._course_names[course.id] = course.title
            course_info = {"id": course.id, "title": course.title, "available": True, "lessons": []}
            for index, lesson_id in enumerate(course.lessons):
                try:
                    lesson = load_lesson(registry.courses_dir / course.id / "lessons" / lesson_id)
                except Exception:
                    course_info["lessons"].append({"id": lesson_id, "title": lesson_id, "available": False})
                    self.warnings.append("部分课时暂不可读取，其历史保留但无法从当前课程目录定位。")
                    continue
                self._lesson_names[(course.id, lesson_id)] = lesson.title or lesson_id
                course_info["lessons"].append({"id": lesson_id, "title": lesson.title or lesson_id, "available": True})
                for step in lesson.steps:
                    if step.cls not in _TASK_TYPES:
                        continue
                    self.catalog.append({
                        "courseId": course.id, "lessonId": lesson_id, "stepId": step.id,
                        "taskId": f"{lesson_id}:{step.id}", "lessonIndex": index,
                        "title": step.exercise_title or step.output.strip().split("\n")[0].lstrip("# ")[:100],
                        "taskType": step.cls, "depth": step.depth,
                        "knowledgeComponents": list(step.knowledge_components),
                        "targetDimensions": list(step.diagnosis_targets),
                        "prerequisites": list(step.prerequisites), "context": step.context,
                        "transfer": dict(step.transfer),
                    })
            self.courses.append(course_info)
        self._specs = {(item["courseId"], item["lessonId"], item["taskId"]): item for item in self.catalog}
        exercise_keys = set(self._specs) | {
            _key(event) for event in self.events if all(_key(event)) and not event.task_id.endswith(":finished")
            and (event.task_type in _TASK_TYPES
                 or (not event.task_type and event.event_type in {"code_submit", "code_run"}))
        }
        self.tasks = defaultdict(list)
        for event in self.events:
            if _key(event) in exercise_keys:
                self.tasks[_key(event)].append(event)
        known_courses = {course["id"]: course for course in self.courses}
        for course_id, lesson_id, _ in self.tasks:
            if course_id not in known_courses:
                course = {"id": course_id, "title": f"{course_id}（已不在课程目录）", "available": False, "lessons": []}
                known_courses[course_id] = course
                self.courses.append(course)
            course = known_courses[course_id]
            if not any(lesson["id"] == lesson_id for lesson in course["lessons"]):
                course["lessons"].append({"id": lesson_id, "title": f"{lesson_id}（已不在课程目录）", "available": False})

    def validate_course(self, course_id):
        if not isinstance(course_id, str):
            raise ValueError("courseId must be a string")
        if course_id and not any(course["id"] == course_id for course in self.courses):
            raise ValueError("未找到课程或对应的历史记录。")

    def _task_key(self, course_id, lesson_id, task_id):
        values = (course_id, lesson_id, task_id)
        if any(not isinstance(value, str) or not value for value in values):
            raise ValueError("需要明确的 courseId、lessonId 和 taskId。")
        if values not in self.tasks and values not in self._specs:
            raise ValueError("未找到题目或对应的历史记录。")
        return values

    def describe_event(self, event, *, prefix=None):
        context = self.tasks.get(_key(event), []) if prefix is None else [e for e in prefix if _key(e) == _key(event)]
        data = event.data
        answer_influenced = _before(context, event, "solution_view")
        assessment = _assessment(event)
        debug_failure = _debug_failure(event)
        category = "behavior"
        reason = "行为记录不直接证明知识掌握、调试修复或新情境迁移。"
        if assessment:
            category = "assessment"
            reason = ("本次为可信评估；知识取每题最近有效结果，提示覆盖取首次评估，迁移还需来源与新情境条件。"
                      "是否改变各维度请查看前后复盘。")
            if answer_influenced:
                reason = "已查看参考答案，本次结果不新增知识、调试修复或迁移证据；仍可能进入首次评估前提示覆盖的样本。"
        elif debug_failure:
            category = "debug_failure" if not answer_influenced else "excluded"
            reason = ("可观察的学习者代码错误，仅可参与调试失败过程；不直接计为知识或迁移评估。"
                      if not answer_influenced else "已查看参考答案，此后的错误不新增调试失败过程或独立知识证据。")
        elif _help(event, "solution_view"):
            category = "answer_view"
            reason = "答案查看会截断其后同题的知识、调试和迁移证据；之前的合格表现保留，同刻记录按保守规则处理。"
        elif event.event_type in {"code_submit", "code_run"}:
            category = "excluded"
            if data.get("mode") == "dry_run" or data.get("failureKind") == "dry_run":
                reason = "仅语法或模拟运行未验证课程结果，不作为知识、调试修复或迁移成绩。"
            elif data.get("assessmentSource") == "ai_review" or data.get("aiReview") is True or data.get("failureKind") == "ai_failure":
                reason = "AI 评审或服务失败仅保留行为结果，不作为四维量化评估证据。"
            elif data.get("failureKind") == "infrastructure":
                reason = "环境或基础设施失败不计为学习者知识错误或调试失败。"
            elif event.event_type == "code_run" and data.get("exitCode") == 0:
                category = "behavior"
                reason = "运行退出成功不等于课程评估通过，不能据此确认知识、调试修复或迁移成功。"
            else:
                reason = "缺少可信评估资格或来源，仅保留原行为；旧日志不会被追认为有效成绩。"
        elif _help(event, "hint_request"):
            reason = "提示使用不会扣减知识或调试通过率；提示覆盖只统计首次有效评估前的使用，不能视为能力不足。"
        source = data.get("assessmentSource")
        mode = data.get("mode")
        raw_passed = data.get("passed") if type(data.get("passed")) is bool else None
        raw_types = data.get("errorTypes") or [data.get("errorType")]
        if not isinstance(raw_types, list):
            raw_types = [raw_types]
        errors = [value for value in raw_types if isinstance(value, str)
                  and len(value) <= 80 and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception)|[A-Z][A-Z0-9_]+", value)]
        result = {
            "eventId": event.event_id, "eventType": event.event_type,
            "timestamp": event_timestamp(event).isoformat(), "attemptNumber": event.attempt_number,
            "passed": raw_passed if assessment or debug_failure else None, "rawPassed": raw_passed,
            "assessmentSource": source if isinstance(source, str) and source in _SOURCES else "unverified",
            "mode": mode if isinstance(mode, str) and mode in _MODES else "unknown",
            "category": category, "reason": reason, "answerInfluenced": answer_influenced,
            "reviewable": bool(event.event_id), "hintUsed": data.get("hintUsed") is True,
            "available": data.get("available") if type(data.get("available")) is bool else None,
            "errorTypes": errors,
        }
        if type(data.get("exitCode")) is int:
            result["exitCode"] = data["exitCode"]
        if event.event_type == "code_edit":
            result["editCounts"] = {name: value for name, value in data.items()
                                    if name in _EDIT_FIELDS and type(value) is int and value >= 0}
        return result

    def task_summary(self, key, *, display_events=None, prefix=None):
        events = self.tasks.get(key, []) if prefix is None else [event for event in prefix if _key(event) == key]
        display_events = events if display_events is None else display_events
        spec = self._specs.get(key, {})
        submits = [event for event in events if event.event_type == "code_submit"]
        assessments = [event for event in events if _assessment(event) and not _before(events, event, "solution_view")]
        return {
            "courseId": key[0], "lessonId": key[1], "taskId": key[2], "stepId": spec.get("stepId", ""),
            "title": spec.get("title") or f"{key[2]}（已不在课程目录）",
            "courseTitle": self._course_names.get(key[0], f"{key[0]}（已不在课程目录）"),
            "lessonTitle": self._lesson_names.get(key[:2], f"{key[1]}（已不在课程目录）"),
            "available": bool(spec),
            "taskType": spec.get("taskType") or next((event.task_type for event in events if event.task_type in _TASK_TYPES), ""),
            "lastActivityAt": event_timestamp(display_events[-1]).isoformat() if display_events else None,
            "counts": {
                "submissions": sum(event.event_type == "code_submit" for event in display_events),
                "eligibleAssessments": sum(_assessment(event) for event in display_events),
                "hints": sum(_help(event, "hint_request") for event in display_events),
                "answers": sum(_help(event, "solution_view") for event in display_events),
                "runs": sum(event.event_type == "code_run" for event in display_events),
                "edits": sum(event.event_type == "code_edit" for event in display_events),
            },
            "latestSubmission": self.describe_event(submits[-1], prefix=events) if submits else None,
            "latestAssessment": self.describe_event(assessments[-1], prefix=events) if assessments else None,
            "answerViewed": any(_help(event, "solution_view") for event in events),
        }

    def get_history(self, *, course_id="", window="all", offset=0, limit=20, window_end=None, now=None):
        self.validate_course(course_id)
        _page(offset, limit, 50)
        now = resolve_window_end(window_end, now)
        start = window_start(window, now)
        selected = []
        positions = {id(event): index for index, event in enumerate(self.events)}
        for key, events in self.tasks.items():
            if course_id and key[0] != course_id:
                continue
            visible = [event for event in events if start is None or start <= event_timestamp(event) <= now]
            if visible:
                selected.append((positions[id(visible[-1])], key, visible))
        selected.sort(key=lambda item: item[0], reverse=True)
        return {
            "courses": self.courses, "courseId": course_id, "window": window,
            "windowStart": start.isoformat() if start else None, "windowEnd": now.isoformat(),
            "tasks": [self.task_summary(key, display_events=visible) for _, key, visible in selected[offset:offset + limit]],
            "offset": offset, "limit": limit, "total": len(selected), "hasMore": offset + limit < len(selected),
            "snapshotEventId": self.snapshot_event_id, "dataQuality": self.data_quality,
            "disclaimer": DISCLAIMER, "warnings": self.warnings,
        }

    def get_task_history(self, course_id, lesson_id, task_id, *, offset=0, limit=50):
        _page(offset, limit, 100)
        key = self._task_key(course_id, lesson_id, task_id)
        events = self.tasks.get(key, [])
        return {
            "task": self.task_summary(key), "events": [self.describe_event(event) for event in events[offset:offset + limit]],
            "offset": offset, "limit": limit, "total": len(events), "hasMore": offset + limit < len(events),
            "snapshotEventId": self.snapshot_event_id, "dataQuality": self.data_quality, "disclaimer": DISCLAIMER,
        }

    def get_review(self, course_id, lesson_id, task_id, event_id, *, scope_course_id=None, depth="beginner"):
        key = self._task_key(course_id, lesson_id, task_id)
        if not isinstance(event_id, str) or not event_id:
            raise ValueError("需要有效的 eventId。")
        match = next(((index, event) for index, event in enumerate(self.events)
                      if event.event_id == event_id and _key(event) == key), None)
        if match is None:
            raise ValueError("该记录不属于指定题目，或已不在当前历史快照中。")
        scope = course_id if scope_course_id is None else scope_course_id
        self.validate_course(scope)
        if scope and scope != course_id:
            raise ValueError("复盘范围必须包含当前题目的课程。")
        index, event = match
        before_events, after_events = self.events[:index], self.events[:index + 1]
        before = score_snapshot(before_events, self.catalog, course_id=scope, depth=depth)
        after = score_snapshot(after_events, self.catalog, course_id=scope, depth=depth)
        changes = []
        for old, new in zip(before["dimensions"], after["dimensions"]):
            delta = round(new["score"] - old["score"], 1) if old["score"] is not None and new["score"] is not None else None
            if old["score"] is None and new["score"] is not None:
                explanation = "新增可用证据，此前无可比比例，不计算百分点变化。"
            elif old["score"] is not None and new["score"] is None:
                explanation = "此记录使当前证据不再满足计分条件；不是把学习能力判为零。"
            else:
                explanation = "这是该记录前后观察比例与样本的变化，不是能力增减的因果结论。"
            if new["direction"] == "descriptive":
                explanation += "提示覆盖仅描述求助行为，不用于能力高低排序。"
            changes.append({"key": new["key"], "name": new["name"], "beforeScore": old["score"],
                            "afterScore": new["score"], "delta": delta,
                            "numeratorDelta": new["numerator"] - old["numerator"],
                            "denominatorDelta": new["denominator"] - old["denominator"],
                            "sampleDelta": new["sampleSize"] - old["sampleSize"], "explanation": explanation})
        description = self.describe_event(event, prefix=after_events)
        return {
            "task": self.task_summary(key, prefix=after_events), "event": description,
            "before": before, "after": after, "changes": changes, "reason": description["reason"],
            "scopeCourseId": scope, "snapshotEventId": self.snapshot_event_id,
            "dataQuality": self.data_quality, "disclaimer": DISCLAIMER,
        }
