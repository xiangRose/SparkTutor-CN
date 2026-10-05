"""Build diagnosis recommendations from the lessons that can actually open."""

from sparktutor.engine.lesson_loader import load_lesson


def build_exercise_catalog(registry) -> list[dict]:
    catalog = []
    for course in registry.list_courses():
        for lesson_index, lesson_id in enumerate(course.lessons):
            lesson = load_lesson(registry.courses_dir / course.id / "lessons" / lesson_id)
            for step in lesson.steps:
                if step.cls not in {"mult_question", "cmd_question", "script"}:
                    continue
                title = step.exercise_title or step.output.strip().split("\n")[0].lstrip("# ")[:100]
                catalog.append({
                    "courseId": course.id, "lessonId": lesson_id,
                    "lessonIndex": lesson_index, "stepId": step.id,
                    "taskId": f"{lesson_id}:{step.id}", "title": title,
                    "taskType": step.cls, "depth": step.depth,
                    "knowledgeComponents": list(step.knowledge_components),
                    "targetDimensions": list(step.diagnosis_targets),
                    "prerequisites": list(step.prerequisites),
                    "context": step.context, "transfer": dict(step.transfer),
                })
    return catalog
