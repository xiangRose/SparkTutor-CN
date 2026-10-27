"""Check the ML harness observes training without leaving patched APIs behind.

These unit tests do not execute Spark. Real API behavior and misconception
rejection are verified separately by the opt-in course execution suite.
"""

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest


ML_LESSON = Path(__file__).resolve().parents[1] / "src/sparktutor/courses/learning_spark/lessons/10_mllib"


def execute_ml_harness(filename, implementation):
    module = ast.parse((ML_LESSON / filename).read_text(encoding="utf-8"))
    main = next(node for node in module.body if isinstance(node, ast.If)
                and ast.unparse(node.test) == "__name__ == '__main__'")

    class Builder:
        def appName(self, value):
            return self

        def master(self, value):
            return self

        def getOrCreate(self):
            return object()

    namespace = {
        "SparkSession": SimpleNamespace(builder=Builder()),
        "Pipeline": implementation.pipeline_type,
        "SCHEMA": SimpleNamespace(fieldNames=lambda: ["example_id"]),
        "generate_housing_data": lambda: [(index,) for index in range(500)],
        "build_pipeline": implementation.build,
    }
    exec(compile(ast.Module(body=main.body, type_ignores=[]), str(ML_LESSON / filename), "exec"), namespace)


class ExampleRows:
    def __init__(self, rows):
        self.rows = rows

    def select(self, *columns):
        return self

    def collect(self):
        return self.rows


@pytest.mark.parametrize("filename", ["starter.py", "solution.py"])
def test_full_dataset_is_rejected_before_fit_and_original_method_is_restored(filename):
    fits = []

    class Pipeline:
        def fit(self, dataset):
            fits.append(dataset)
            return SimpleNamespace(uid="example-model")

    original_fit = Pipeline.fit
    implementation = SimpleNamespace(
        pipeline_type=Pipeline,
        build=lambda spark: Pipeline().fit(ExampleRows([(index,) for index in range(500)])),
    )
    with pytest.raises(AssertionError, match="只能在训练子集上拟合"):
        execute_ml_harness(filename, implementation)
    assert not fits, "Leaking the whole dataset must be rejected before expensive model training"
    assert Pipeline.fit is original_fit


@pytest.mark.parametrize("filename", ["starter.py", "solution.py"])
def test_student_failure_after_valid_fit_also_restores_original_method(filename):
    fits = []

    class Pipeline:
        def fit(self, dataset):
            fits.append(dataset)
            return SimpleNamespace(uid="example-model")

    original_fit = Pipeline.fit

    def build(spark):
        Pipeline().fit(ExampleRows([(index,) for index in range(400)]))
        raise RuntimeError("failure after training")

    with pytest.raises(RuntimeError, match="failure after training"):
        execute_ml_harness(filename, SimpleNamespace(pipeline_type=Pipeline, build=build))
    assert len(fits) == 1
    assert Pipeline.fit is original_fit
