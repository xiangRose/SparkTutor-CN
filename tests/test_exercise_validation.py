"""Behavioral tests for the unedited course harness; no Spark needed."""

import subprocess
import sys

import pytest

from sparktutor.engine.exercise_validation import VALIDATION_MARKER, build_validation_script


TEMPLATE = '''
def double(value):
    return None

if __name__ == "__main__":
    assert double(4) == 8, "wrong result"
'''


def run_validation(code, template=TEMPLATE):
    return subprocess.run(
        [sys.executable, "-c", build_validation_script(code, template)],
        capture_output=True, text=True, encoding="utf-8", timeout=10,
    )


def test_valid_implementation_passes_without_student_main():
    result = run_validation("def double(value):\n    return value * 2")
    assert result.returncode == 0, result.stderr
    assert VALIDATION_MARKER in result.stdout.splitlines()


def test_deleting_student_assertions_cannot_pass_wrong_implementation():
    result = run_validation('''
def double(value):
    return 0
if __name__ == "__main__":
    print("I passed!")
''')
    assert result.returncode != 0
    assert "wrong result" in result.stderr
    assert VALIDATION_MARKER not in result.stdout


def test_student_main_is_not_run_twice():
    result = run_validation('''
def double(value):
    return value * 2
if __name__ == "__main__":
    raise RuntimeError("editable harness must not run")
''')
    assert result.returncode == 0, result.stderr


def test_source_inspection_works_for_student_and_course_test_functions():
    template = '''
import inspect
def inspect_function(func):
    pass
if __name__ == "__main__":
    def bronze(spark):
        return spark.table("source")
    assert 'spark.table' in inspect_function(bronze)
    assert 'source' in inspect_function(bronze)
    assert "getsource" in inspect.getsource(inspect_function)
'''
    result = run_validation(
        "import inspect\ndef inspect_function(func):\n    return inspect.getsource(func)",
        template,
    )
    assert result.returncode == 0, result.stderr


def test_early_successful_exit_has_no_validation_marker():
    result = run_validation("raise SystemExit(0)")
    assert result.returncode == 0
    assert VALIDATION_MARKER not in result.stdout


def test_missing_course_harness_is_reported():
    with pytest.raises(ValueError):
        build_validation_script("x = 1", "x = None")


def test_optimized_python_still_runs_course_assertions():
    result = subprocess.run(
        [sys.executable, "-O", "-c", build_validation_script("def double(x):\n    return 0", TEMPLATE)],
        capture_output=True, text=True, encoding="utf-8", timeout=10,
    )
    assert result.returncode != 0
    assert VALIDATION_MARKER not in result.stdout
