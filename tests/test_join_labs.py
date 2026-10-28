"""Join invariants, independent transfer metadata, and real Spark counterexamples."""

import ast
import os
from pathlib import Path

import pytest

from sparktutor.engine.lesson_loader import load_lesson
from tests.test_course_execution import run_course_harness


DIRECTORY = Path(__file__).resolve().parents[1] / "src/sparktutor/courses/learning_spark/lessons/07_join_strategies"


def test_join_transfer_is_a_new_context_with_a_real_source_and_stable_steps():
    lesson = load_lesson(DIRECTORY)
    steps = {step.id: step for step in lesson.steps}
    source = steps["join-cardinality-lab"]
    target = steps["join-cardinality-transfer"]
    assert source.context != target.context
    assert source.knowledge_components == target.knowledge_components
    assert target.transfer["sources"] == [{"courseId": "learning_spark", "lessonId": lesson.id,
                                            "stepId": source.id}]
    assert "13" in steps and "14" in steps
    for identifier in (source.id, "join-plan-lab", target.id):
        step = steps[identifier]
        assert step.requires_execution
        for filename in (step.starter_code, step.solution_code):
            ast.parse((DIRECTORY / filename).read_text(encoding="utf-8"))


NEGATIVES = [
    ("join-cardinality-lab", "return orders.join(latest,", "return orders.join(customer_history.select(\"customer_id\", \"segment\"),",
     "关联结果必须保留每条左表事实行"),
    ("join-cardinality-lab", '.select("order_id", "customer_id", "amount", "segment")',
     '.select("order_id", "customer_id", "amount", "segment").dropDuplicates(["order_id"])',
     "关联结果必须保留每条左表事实行"),
    ("join-cardinality-lab", 'f.col("version").desc()', 'f.col("version").asc()',
     "先按更新时间和版本选最新维表"),
    ("join-plan-lab", '"merge": merge_result', '"merge": broadcast_result',
     "合并结果的真实执行计划必须包含 SortMergeJoin"),
    ("join-cardinality-transfer", "return visits.join(latest,", "return visits.join(page_versions.select(\"page_id\", \"category\"),",
     "迁移任务仍须保留每条访问事实"),
]


@pytest.mark.spark
@pytest.mark.skipif(os.environ.get("SPARKTUTOR_TEST_SPARK") != "1", reason="Requires explicit real Spark execution")
@pytest.mark.parametrize("identifier,original,replacement,message", NEGATIVES,
                         ids=["right-multiplicity", "output-deduplication", "latest-version-tie", "fake-plan", "transfer-multiplicity"])
def test_join_course_assertions_reject_misconceptions(identifier, original, replacement, message, tmp_path):
    pytest.importorskip("pyspark")
    lesson = load_lesson(DIRECTORY)
    step = next(step for step in lesson.steps if step.id == identifier)
    reference = (DIRECTORY / step.solution_code).read_text(encoding="utf-8")
    assert reference.count(original) == 1, "Update the counterexample when the reference changes"
    result = run_course_harness(lesson, step, reference.replace(original, replacement), tmp_path)
    assert result.returncode != 0
    assert "AssertionError: " + message in result.stderr, result.stdout[-1500:] + result.stderr[-3500:]
