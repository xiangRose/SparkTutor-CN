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


@pytest.mark.spark
@pytest.mark.skipif(os.environ.get("SPARKTUTOR_TEST_SPARK") != "1", reason="Requires an explicit real-Spark test run")
@pytest.mark.parametrize("lesson,step", EXERCISES, ids=[f"{l.base_path.parents[1].name}/{l.id}" for l, _ in EXERCISES])
def test_course_reference_solution(lesson, step, tmp_path):
    pytest.importorskip("pyspark")
    solution = (lesson.base_path / step.solution_code).read_text(encoding="utf-8")
    starter = (lesson.base_path / step.starter_code).read_text(encoding="utf-8")
    # Bound resources without changing the Spark API, ANSI behavior or tests.
    prelude = '''from pyspark.sql import SparkSession
_master = SparkSession.Builder.master
def _test_master(self, value):
    return _master(self, "local[2]" if value == "local[*]" else value)
SparkSession.Builder.master = _test_master
'''
    path = tmp_path / "validate.py"
    path.write_text(prelude + build_validation_script(solution, starter), encoding="utf-8")
    env = {**os.environ, "PYSPARK_PYTHON": sys.executable, "PYSPARK_DRIVER_PYTHON": sys.executable,
           "SPARK_LOCAL_IP": "127.0.0.1", "PYTHONIOENCODING": "utf-8"}
    result = subprocess.run([sys.executable, str(path)], cwd=tmp_path, env=env,
                            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-12000:]
    assert VALIDATION_MARKER in result.stdout.splitlines()
