"""Curriculum contracts that must hold before lessons can be published."""

import ast
from pathlib import Path
import re
import runpy
from types import SimpleNamespace

import pytest
import yaml


COURSES = Path(__file__).resolve().parents[1] / "src" / "sparktutor" / "courses"
LESSONS = sorted(COURSES.rglob("lesson.yaml"))


@pytest.mark.parametrize("path", LESSONS, ids=lambda p: f"{p.parents[2].name}/{p.parent.name}")
def test_lesson_questions_and_executable_contracts(path):
    steps = yaml.safe_load(path.read_text(encoding="utf-8"))
    for step in steps:
        if step["Class"] == "mult_question":
            choices = step["AnswerChoices"].split(";")
            assert len(choices) == len(set(choices))
            assert choices.count(step["CorrectAnswer"]) == 1
        if step["Class"] == "cmd_question":
            ast.parse(step["CorrectAnswer"])
        if step["Class"] == "script":
            assert step["RequiresExecution"] is True
            assert "输入、输出与通过条件" in step["Output"]
            for key, legacy_key in (("StarterCode", "StarterFile"), ("SolutionCode", "SolutionFile")):
                source = path.with_name(step.get(key) or step[legacy_key]).read_text(encoding="utf-8")
                module = ast.parse(source)
                guards = [node for node in module.body if isinstance(node, ast.If)
                          and ast.unparse(node.test) == "__name__ == '__main__'"]
                assert len(guards) == 1
                assert any(isinstance(node, ast.Assert) for node in ast.walk(guards[0])), (
                    path, "Executable exercises need real assertions, not only printed output"
                )


def test_documentation_links_use_supported_course_baseline():
    for path in COURSES.rglob("*.yaml"):
        content = path.read_text(encoding="utf-8")
        versions = re.findall(r"https://spark\.apache\.org/docs/([^/]+)/", content)
        assert all(version == "4.1.1" for version in versions), path


def test_required_pipeline_concepts_reach_all_depths():
    path = COURSES / "spark_declarative_pipelines/lessons/04_pipeline_framework/lesson.yaml"
    steps = yaml.safe_load(path.read_text(encoding="utf-8"))
    for heading in ("## 装饰器模式", "## 依赖检测", "## 拓扑排序", "## run() 方法"):
        matching = [s for s in steps if s.get("Output", "").startswith(heading)]
        assert len(matching) == 1 and matching[0]["Depth"] == "all"


def test_teaching_pipeline_orders_dependencies_and_rejects_cycles():
    path = COURSES / "spark_declarative_pipelines/lessons/04_pipeline_framework/solution.py"
    pipeline = runpy.run_path(str(path))["Pipeline"](None)
    assert pipeline._topo_sort({"gold": ["silver"], "silver": ["bronze", "bronze"], "bronze": []}) == [
        "bronze", "silver", "gold"
    ]
    assert pipeline._topo_sort({"bronze": ["external_table"]}) == ["bronze"]
    with pytest.raises(ValueError):
        pipeline._topo_sort({"gold": ["silver"], "silver": ["gold"]})


def test_teaching_pipeline_runs_in_dependency_order_without_spark():
    path = COURSES / "spark_declarative_pipelines/lessons/04_pipeline_framework/solution.py"
    registered = []

    class Frame:
        def createOrReplaceTempView(self, name):
            registered.append(name)

        def count(self):
            return 1

    class Session:
        def table(self, name):
            assert name in registered
            return Frame()

    pipeline = runpy.run_path(str(path))["Pipeline"](Session())

    @pipeline.materialized_view()
    def gold(spark):
        return spark.table("silver")

    @pipeline.materialized_view()
    def silver(spark):
        return spark.table("bronze")

    @pipeline.materialized_view()
    def bronze(spark):
        return Frame()

    pipeline.run()
    assert registered == ["bronze", "silver", "gold"]


@pytest.mark.parametrize("filename", ["starter.py", "solution.py"])
def test_data_source_harness_finishes_actions_before_overwrite_cleanup(filename, capsys):
    """A lazy file scan becomes invalid when overwrite replaces its source files."""
    path = COURSES / "learning_spark/lessons/03_data_sources" / filename
    module = ast.parse(path.read_text(encoding="utf-8"))
    main = next(node for node in module.body if isinstance(node, ast.If)
                and ast.unparse(node.test) == "__name__ == '__main__'")
    generation = 0
    written_paths = []
    shown_generations = []

    class LazyFrame:
        columns = ["destination", "total_delay"]

        def __init__(self, source, row_count=3):
            self.source = Path(source)
            self.generation = generation
            self.row_count = row_count

        def check_source(self):
            assert self.source.exists(), "Action ran after temporary files were removed"
            assert self.generation == generation, "Action reused a stale plan after overwrite"

        def count(self):
            self.check_source()
            return self.row_count

        def collect(self):
            self.check_source()
            return [SimpleNamespace(destination=destination, total_delay=total_delay)
                    for destination, total_delay in [("ORD", 120), ("JFK", 105), ("DEN", 35)]]

        def show(self, **kwargs):
            self.check_source()
            shown_generations.append(self.generation)
            print("result displayed")

    class Session:
        stopped = False

        def __init__(self):
            self.read = SimpleNamespace(parquet=lambda source: LazyFrame(source, 9))

        def table(self, name):
            assert name == "flights"
            return LazyFrame(written_paths[-1], 9)

        def stop(self):
            self.stopped = True

    session = Session()

    class Builder:
        def appName(self, name):
            return self

        def master(self, name):
            return self

        def getOrCreate(self):
            return session

    def data_pipeline(spark, source):
        nonlocal generation
        generation += 1
        Path(source).mkdir(exist_ok=True)
        written_paths.append(Path(source))
        return LazyFrame(source)

    namespace = {
        "SparkSession": SimpleNamespace(builder=Builder()),
        "data_pipeline": data_pipeline,
        "FLIGHT_DATA": [None] * 9,
    }
    exec(compile(ast.Module(body=main.body, type_ignores=[]), str(path), "exec"), namespace)
    assert shown_generations == [2]
    assert session.stopped
    assert all(not source.exists() for source in written_paths)
    output = capsys.readouterr().out
    assert output.index("result displayed") < output.index("所有测试通过！")
