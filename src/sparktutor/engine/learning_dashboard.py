"""Read-only course progress and formative evidence for the learning workspace."""

from __future__ import annotations

from datetime import datetime, timezone

from sparktutor.engine.adaptive import Depth
from sparktutor.engine.diagnosis import build_diagnosis
from sparktutor.engine.exercise_catalog import build_exercise_catalog
from sparktutor.engine.lesson_loader import load_lesson


_DEPTHS = {depth.value for depth in Depth}


def _updated(value: str) -> datetime:
    """ProgressStore writes local ISO times; compare imported offsets fairly."""
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except (ValueError, TypeError, AttributeError, OverflowError):
        return datetime.min.replace(tzinfo=timezone.utc)


def _course_snapshot(course, registry, progress, depth: str, warnings: list) -> tuple:
    saved = {entry.lesson_id: entry for entry in progress.get_course_progress(course.id)}
    lessons = []
    resumable = []
    unstarted = []
    for index, lesson_id in enumerate(course.lessons):
        try:
            lesson = load_lesson(registry.courses_dir / course.id / "lessons" / lesson_id)
        except Exception:
            warnings.append({"section": "catalog", "message": f"课程「{course.title}」有课时暂时无法读取，该课时目前无法打开。"})
            lessons.append({
                "id": lesson_id, "title": lesson_id, "index": index,
                "estimatedMinutes": 0, "status": "unavailable", "available": False,
                "currentStep": 0, "currentStepId": "", "totalSteps": 0, "depth": depth,
            })
            continue
        entry = saved.get(lesson_id)
        valid_depth = entry is None or entry.depth in _DEPTHS
        if not valid_depth:
            warnings.append({"section": "progress", "message": f"「{lesson.title}」的旧难度记录无效，按当前难度展示。"})
        lesson_depth = entry.depth if entry and valid_depth else depth
        steps = [step for step in lesson.steps_for_depth(lesson_depth) if step.cls != "meta"]
        status = "not_started" if entry is None else "completed" if entry.completed else "in_progress"
        current_index = 0
        if entry is not None and entry.completed:
            current_index = len(steps)
        elif entry is not None and valid_depth and steps:
            matches = [i for i, step in enumerate(steps) if step.id == entry.current_step_id]
            # Match LessonRunner's stable-ID resume and legacy-index fallback.
            current_index = matches[0] if matches else max(0, min(entry.current_step, len(steps) - 1))
        step_id = steps[current_index].id if current_index < len(steps) else "finished"
        lesson_info = {
            "id": lesson_id, "title": lesson.title or lesson_id, "index": index,
            "estimatedMinutes": lesson.estimated_minutes, "status": status,
            "available": True,
            "currentStep": current_index, "currentStepId": step_id,
            "totalSteps": len(steps), "depth": lesson_depth,
        }
        lessons.append(lesson_info)
        if status != "completed" and steps:
            target = {
                "courseId": course.id, "courseTitle": course.title,
                "lessonId": lesson_id, "lessonTitle": lesson_info["title"],
                "lessonIdx": index, "depth": lesson_depth, "stepId": step_id,
                "currentStep": current_index, "action": "resume" if entry and valid_depth else "start",
            }
            if entry is not None and valid_depth:
                resumable.append((_updated(entry.updated_at), target))
            else:
                unstarted.append(target)
    resume = max(resumable, key=lambda item: item[0])[1] if resumable else next(iter(unstarted), None)
    completed = sum(lesson["status"] == "completed" for lesson in lessons)
    in_progress = sum(lesson["status"] == "in_progress" for lesson in lessons)
    summary = {
        "started": bool(completed or in_progress),
        "lessonsCompleted": completed, "totalLessons": len(lessons),
        "inProgressLessons": in_progress,
        "unavailableLessons": sum(lesson["status"] == "unavailable" for lesson in lessons),
        "completionPercent": round(100 * completed / len(lessons), 1) if lessons else 0,
        "allCompleted": bool(lessons) and completed == len(lessons),
        "currentLessonId": resume["lessonId"] if resume else None,
        "currentLessonIdx": resume["lessonIdx"] if resume else None,
        "depth": resume["depth"] if resume else depth,
    }
    return {
        "id": course.id, "title": course.title, "description": course.description,
        "lessonCount": len(lessons), "requiresLakehouse": course.requires_lakehouse,
        "prerequisites": list(course.prerequisites), "lessons": lessons, "progress": summary,
    }, resumable, unstarted


def get_course_progress(registry, progress, course_id: str, *, depth: str = "beginner") -> dict:
    """Expose per-lesson facts in the camelCase contract used by the sidebar."""
    course = registry.get_course(course_id)
    if course is None:
        raise ValueError(f"Unknown course: {course_id}")
    warnings = []
    snapshot, _, _ = _course_snapshot(course, registry, progress, depth, warnings)
    return {**snapshot["progress"], "lessons": snapshot["lessons"], "warnings": warnings}


def build_learning_dashboard(registry, progress, events, *, course_id: str = "",
                             depth: str = "beginner") -> dict:
    """Summarize saved progress without advancing lessons or recording events."""
    if not isinstance(course_id, str):
        raise ValueError("courseId must be a string")
    course_list = registry.list_courses()
    if course_id and not any(course.id == course_id for course in course_list):
        raise ValueError("未找到指定课程。")
    depth = depth if depth in _DEPTHS else "beginner"
    warnings = []
    courses = []
    resumable = []
    unstarted = []
    for course in course_list:
        snapshot, course_resumable, course_unstarted = _course_snapshot(course, registry, progress, depth, warnings)
        courses.append(snapshot)
        if not course_id or course.id == course_id:
            resumable.extend(course_resumable)
            unstarted.extend(course_unstarted)
    resume = max(resumable, key=lambda item: item[0])[1] if resumable else next(iter(unstarted), None)
    try:
        # Filtering must happen inside diagnosis: cross-course prerequisites
        # still need the complete catalog and event history.
        diagnosis = build_diagnosis(events.list_events(limit=None), build_exercise_catalog(registry),
                                    course_id=course_id, depth=depth)
    except Exception:
        diagnosis = None
        warnings.append({"section": "diagnosis", "message": "学习诊断暂时无法读取，课程与已保存进度仍可使用。"})
    return {
        "courses": courses, "selectedCourseId": course_id, "catalogScope": "all",
        "resume": resume, "diagnosis": diagnosis,
        "knowledgeComponents": diagnosis["knowledgeComponents"] if diagnosis else {},
        "progressMeaning": "课程完成率表示已有完成记录的课时比例；回看会保留完成记录，不代表所有难度均已完成或知识掌握程度。",
        "warnings": warnings,
    }
