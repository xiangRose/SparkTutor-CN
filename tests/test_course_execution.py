"""Opt-in Spark 4.1.1 execution of every reference through its course harness."""

import os
from pathlib import Path
import subprocess
import sys

import pytest

from sparktutor.engine.exercise_validation import VALIDATION_MARKER, build_validation_script
from sparktutor.engine.lesson_loader import load_lesson


COURSES = Path(__file__).parents[1] / "src" / "sparktutor" / "courses"
EXERCISES = [
    (lesson, step)
    for file in sorted(COURSES.glob("*/lessons/*/lesson.yaml"))
    for lesson in [load_lesson(file.parent)]
    for step in lesson.steps if step.cls == "script"
]


def run_course_harness(lesson, step, source, tmp_path):
    starter = (lesson.base_path / step.starter_code).read_text(encoding="utf-8")
    # Bound resources without changing the Spark API, ANSI behavior or tests.
    prelude = '''from pyspark.sql import SparkSession
_master = SparkSession.Builder.master
def _test_master(self, value):
    return _master(self, "local[2]" if value == "local[*]" else value)
SparkSession.Builder.master = _test_master
'''
    path = tmp_path / "validate.py"
    path.write_text(prelude + build_validation_script(source, starter), encoding="utf-8")
    env = {**os.environ, "PYSPARK_PYTHON": sys.executable, "PYSPARK_DRIVER_PYTHON": sys.executable,
           "SPARK_LOCAL_IP": "127.0.0.1", "PYTHONIOENCODING": "utf-8"}
    result = subprocess.run([sys.executable, str(path)], cwd=tmp_path, env=env,
                            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    return result


@pytest.mark.spark
@pytest.mark.skipif(os.environ.get("SPARKTUTOR_TEST_SPARK") != "1", reason="Requires an explicit real-Spark test run")
@pytest.mark.parametrize("lesson,step", EXERCISES, ids=[f"{l.base_path.parents[1].name}/{l.id}/{s.id}" for l, s in EXERCISES])
def test_course_reference_solution(lesson, step, tmp_path):
    pytest.importorskip("pyspark")
    solution = (lesson.base_path / step.solution_code).read_text(encoding="utf-8")
    result = run_course_harness(lesson, step, solution, tmp_path)
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-12000:]
    assert VALIDATION_MARKER in result.stdout.splitlines()


@pytest.mark.spark
@pytest.mark.skipif(os.environ.get("SPARKTUTOR_TEST_SPARK") != "1", reason="Requires an explicit real-Spark test run")
@pytest.mark.parametrize("relative,step_id,original,replacement,message", [
    pytest.param("learning_spark/lessons/01_getting_started", "practice-filter",
                 'return df.filter(f.col("State") == "CA")', 'return df',
                 "只保留 CA 行，不聚合或丢失重复行", id="filter-unfiltered"),
    pytest.param("learning_spark/lessons/01_getting_started", "practice-filter",
                 'return df.filter(f.col("State") == "CA")', 'return df.filter(f.col("State") == "CA").dropDuplicates()',
                 "只保留 CA 行，不聚合或丢失重复行", id="filter-wrong-deduplication"),
    pytest.param("learning_spark/lessons/01_getting_started", "practice-aggregate",
                 'f.sum("Count")', 'f.count("*")',
                 "按州和颜色求数量总和，不是记录条数", id="aggregate-count-instead-of-sum"),
    pytest.param("learning_spark/lessons/04_external_sources_hof", "15",
                 'f.rank().over(w)', 'f.dense_rank().over(w)',
                 "region_rank 必须使用 rank：并列第一后应为第三名", id="ranking-dense-rank"),
    pytest.param("learning_spark/lessons/04_external_sources_hof", "15",
                 'f.rank().over(w)', 'f.row_number().over(w)',
                 "region_rank 必须使用 rank：并列第一后应为第三名", id="ranking-row-number"),
    pytest.param("learning_spark/lessons/04_external_sources_hof", "practice-window-ranking",
                 'f.rank().over(window)', 'f.dense_rank().over(window)',
                 "每区独立降序排名", id="guided-window-dense-rank"),
    pytest.param("learning_spark/lessons/05_spark_sql_deep_dive", "practice-sql-aggregation",
                 'WHERE salary > 50000\n        GROUP BY department',
                 'GROUP BY department\n        HAVING AVG(salary) > 50000',
                 "先保留工资严格大于 50000", id="sql-having-instead-of-where"),
    pytest.param("spark_declarative_pipelines/lessons/04_pipeline_framework", "practice-function-registry",
                 'flows[func.__name__] = func', 'flows[func.__name__] = func(None)',
                 "以函数名为键", id="registry-calls-instead-of-registering"),
    pytest.param("spark_declarative_pipelines/lessons/04_pipeline_framework", "practice-dependency-order",
                 'return order', 'return list(graph)',
                 "步骤必须在它依赖的图内节点之后执行", id="dependency-insertion-order"),
    pytest.param("learning_spark/lessons/10_mllib", "15",
                 'pipeline.fit(trainDF)', 'pipeline.fit(housing_df)',
                 "Pipeline 只能在训练子集上拟合，不能使用全部数据或空数据", id="ml-fit-test-data-leakage"),
    pytest.param("learning_spark/lessons/10_mllib", "15",
                 'model.transform(testDF)', 'model.transform(trainDF)',
                 "预测必须来自测试集，行数应与 test_count 一致", id="ml-evaluate-training-data"),
])
def test_course_harness_rejects_common_concept_errors(relative, step_id, original, replacement, message, tmp_path):
    pytest.importorskip("pyspark")
    lesson = load_lesson(COURSES / relative)
    step = next(item for item in lesson.steps if item.id == step_id)
    solution = (lesson.base_path / step.solution_code).read_text(encoding="utf-8")
    assert solution.count(original) == 1, "Update the negative case when the reference implementation changes"
    result = run_course_harness(lesson, step, solution.replace(original, replacement), tmp_path)
    assert result.returncode != 0
    # Environment failures, syntax errors and unrelated assertions do not prove
    # that the intended misconception was rejected by the course.
    assert f"AssertionError: {message}" in result.stderr, result.stdout[-2000:] + result.stderr[-4000:]
    assert VALIDATION_MARKER not in result.stdout.splitlines()
