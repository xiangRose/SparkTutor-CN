"""Protect historical evidence and the prerequisites visible at every difficulty.

The legacy manifest was captured before the 2026-10-26 curriculum revision.
It deliberately checks identity and grading contracts, not lesson prose.
"""

import json
from pathlib import Path

import pytest

from sparktutor.engine.lesson_loader import load_course, load_lesson


COURSES = Path(__file__).resolve().parents[1] / "src/sparktutor/courses"
LEGACY = json.loads((Path(__file__).parent / "fixtures/curriculum_legacy_ids.json").read_text(encoding="utf-8"))
DEPTHS = ("beginner", "intermediate", "advanced")


@pytest.mark.parametrize("relative,legacy", LEGACY.items(), ids=LEGACY.keys())
def test_existing_steps_keep_their_identity_and_evidence_contract(relative, legacy):
    lesson = load_lesson(COURSES / relative)
    current = {step.id: step for step in lesson.steps}
    fields = {
        "KnowledgeComponents": "knowledge_components", "Context": "context",
        "Transfer": "transfer", "Prerequisites": "prerequisites",
        "StarterFile": "starter_code", "StarterCode": "starter_code",
        "SolutionFile": "solution_code", "SolutionCode": "solution_code",
    }
    for old in legacy:
        assert old["id"] in current, f"Historical progress references missing step {old['id']}"
        step = current[old["id"]]
        assert step.cls == old["class"]
        for original_field, attribute in fields.items():
            if original_field in old:
                assert getattr(step, attribute) == old[original_field], (relative, step.id, attribute)


# Each pair names a prerequisite before the task that actually needs it.
PREREQUISITES = {
    "learning_spark/lessons/01_getting_started": [
        ("lazy-actions", "9"), ("filter-rows", "practice-filter"),
        ("practice-filter", "practice-aggregate"), ("sum-before-grouping", "practice-aggregate"),
        ("practice-aggregate", "12"), ("12", "15"),
    ],
    "learning_spark/lessons/02_dataframes_schemas": [("array-field-primer", "14"), ("12", "14")],
    "learning_spark/lessons/03_data_sources": [("11", "10"), ("io-roundtrip-example", "16")],
    "learning_spark/lessons/04_external_sources_hof": [
        ("14", "15"), ("window-ranking-example", "practice-window-ranking"),
        ("practice-window-ranking", "15"),
    ],
    "learning_spark/lessons/05_spark_sql_deep_dive": [
        ("8", "sql-first-query"), ("sql-first-query", "sql-grouping-primer"),
        ("sql-grouping-primer", "practice-sql-aggregation"),
        ("practice-sql-aggregation", "2"), ("practice-sql-aggregation", "14"),
    ],
    "learning_spark/lessons/06_optimization_tuning": [("8", "9"), ("11", "14")],
    "learning_spark/lessons/07_join_strategies": [("join-meaning-example", "left-join-practice"), ("left-join-practice", "6")],
    "learning_spark/lessons/08_structured_streaming": [("12", "11"), ("streaming-scope", "16")],
    "learning_spark/lessons/09_data_lakes": [("8", "9"), ("lakehouse-scope", "14")],
    "learning_spark/lessons/10_mllib": [("ml-task-and-split", "7"), ("11", "15")],
    "spark_declarative_pipelines/lessons/02_functions_and_transforms": [("derived-column-primer", "8"), ("10", "9")],
    "spark_declarative_pipelines/lessons/03_reading_writing_data": [("6", "7"), ("11", "12")],
    "spark_declarative_pipelines/lessons/04_pipeline_framework": [
        ("functions-before-decorators", "practice-function-registry"),
        ("practice-function-registry", "3"), ("dependency-graph-example", "8"),
        ("8", "practice-dependency-order"), ("practice-dependency-order", "11"), ("12", "11"),
    ],
    "spark_declarative_pipelines/lessons/05_bronze_layer": [("9", "12")],
    "spark_declarative_pipelines/lessons/06_silver_layer": [("silver-cleaning-example", "13")],
    "spark_declarative_pipelines/lessons/07_gold_layer": [("gold-metrics-example", "6"), ("gold-rank-example", "16")],
    "spark_declarative_pipelines/lessons/08_full_pipeline": [("pipeline-acceptance-example", "14")],
}


@pytest.mark.parametrize("relative,pairs", PREREQUISITES.items(), ids=PREREQUISITES.keys())
@pytest.mark.parametrize("depth", DEPTHS)
def test_required_scaffolding_precedes_its_task_in_each_difficulty(relative, pairs, depth):
    lesson = load_lesson(COURSES / relative)
    ids = [step.id for step in lesson.steps_for_depth(depth)]
    for prerequisite, task in pairs:
        assert prerequisite in ids and task in ids, (relative, depth, prerequisite, task)
        assert ids.index(prerequisite) < ids.index(task), (relative, depth, prerequisite, task)


@pytest.mark.parametrize("course_dir", [COURSES / "learning_spark", COURSES / "spark_declarative_pipelines"])
def test_every_lesson_has_a_shared_learning_route_and_valid_transfer_sources(course_dir):
    course = load_course(course_dir)
    for lesson_id in course.lessons:
        lesson = load_lesson(course_dir / "lessons" / lesson_id)
        route = next(step for step in lesson.steps if step.id == "learning-route")
        assert route.depth == "all"
        for step in lesson.steps:
            for source in [*step.prerequisites, *step.transfer.get("sources", [])]:
                source_lesson = load_lesson(COURSES / source["courseId"] / "lessons" / source["lessonId"])
                assert any(item.id == source["stepId"] and item.cls == "script" for item in source_lesson.steps)


def test_guided_work_uses_independent_tasks_and_the_original_capstone_keeps_its_evidence():
    lesson = load_lesson(COURSES / "learning_spark/lessons/01_getting_started")
    tasks = {step.id: step for step in lesson.steps if step.cls == "script"}
    guided = [tasks["practice-filter"], tasks["practice-aggregate"]]
    capstone = tasks["15"]
    assert all(step.requires_execution for step in [*guided, capstone])
    assert len({step.context for step in [*guided, capstone]}) == 3
    assert capstone.starter_code == "starter.py"
    assert capstone.context == "candy_counts"


GUIDED_TASKS = [
    ("learning_spark/lessons/04_external_sources_hof", "practice-window-ranking", "15"),
    ("learning_spark/lessons/05_spark_sql_deep_dive", "practice-sql-aggregation", "14"),
    ("spark_declarative_pipelines/lessons/04_pipeline_framework", "practice-function-registry", "11"),
    ("spark_declarative_pipelines/lessons/04_pipeline_framework", "practice-dependency-order", "11"),
]


@pytest.mark.parametrize("relative,task_id,capstone_id", GUIDED_TASKS)
def test_new_guided_tasks_are_independent_assessments_before_their_capstone(relative, task_id, capstone_id):
    lesson = load_lesson(COURSES / relative)
    tasks = {step.id: step for step in lesson.steps}
    guided, capstone = tasks[task_id], tasks[capstone_id]
    assert guided.cls == "script" and guided.requires_execution and guided.depth == "all"
    assert guided.context and guided.context != capstone.context
    assert guided.starter_code != capstone.starter_code
    assert guided.solution_code != capstone.solution_code
    assert not guided.transfer, "Guided work must not masquerade as the capstone's independent transfer evidence"
    assert lesson.steps.index(guided) < lesson.steps.index(capstone)


@pytest.mark.parametrize("stem", ["registry", "dependency"])
def test_python_pipeline_guided_references_pass_the_course_owned_assertions(stem, capsys):
    from sparktutor.engine.exercise_validation import VALIDATION_MARKER, build_validation_script

    directory = COURSES / "spark_declarative_pipelines/lessons/04_pipeline_framework"
    starter = (directory / f"{stem}_practice_starter.py").read_text(encoding="utf-8")
    solution = (directory / f"{stem}_practice_solution.py").read_text(encoding="utf-8")
    exec(compile(build_validation_script(solution, starter), "<guided-course-validation>", "exec"), {})
    assert VALIDATION_MARKER in capsys.readouterr().out.splitlines()


@pytest.mark.parametrize("stem,wrong", [
    ("registry", "def register_flow(flows, func):\n    flows[func.__name__] = func(None)\n    return func\n"),
    ("dependency", "def dependency_order(graph):\n    return list(graph)\n"),
])
def test_python_pipeline_guided_assertions_reject_early_execution_and_registration_order(stem, wrong):
    from sparktutor.engine.exercise_validation import build_validation_script

    directory = COURSES / "spark_declarative_pipelines/lessons/04_pipeline_framework"
    starter = (directory / f"{stem}_practice_starter.py").read_text(encoding="utf-8")
    with pytest.raises(AssertionError):
        exec(compile(build_validation_script(wrong, starter), "<guided-course-validation>", "exec"), {})


@pytest.mark.parametrize("depth", DEPTHS)
def test_in_progress_capstone_resumes_by_identity_after_new_material_is_inserted(depth, tmp_path):
    from sparktutor.engine.adaptive import Depth, LearnerProfile
    from sparktutor.engine.lesson_runner import LessonRunner
    from sparktutor.state.progress import ProgressStore

    lesson_path = COURSES / "learning_spark/lessons/01_getting_started"
    store = ProgressStore(tmp_path / "progress.db")
    # A pre-update saved position refers to step 15, not the new array position.
    store.save("learning_spark", "01_getting_started", current_step=12, total_steps=13, depth=depth,
               current_step_id="15", last_code="def mnm_analysis(spark, file_path):\n    pass\n")
    runner = LessonRunner("learning_spark", executor=None, evaluator=None, progress=store,
                          profile=LearnerProfile(depth=Depth(depth)))
    state = runner.load_lesson(lesson_path)
    assert state.current_step.id == "15"
    assert "mnm_analysis" in runner.get_restored_code()
