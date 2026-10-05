"""Curriculum contracts that must hold before lessons can be published."""

import ast
from pathlib import Path
import re
import runpy

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
            for key in ("StarterFile", "SolutionFile"):
                source = path.with_name(step[key]).read_text(encoding="utf-8")
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
