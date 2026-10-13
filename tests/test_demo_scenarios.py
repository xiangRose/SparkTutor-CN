"""Demo inputs exercise the real event/diagnosis/recommendation/review chain."""

import ast
import json
import os
from pathlib import Path

import pytest

from sparktutor.config.settings import Settings
from sparktutor.courses.registry import CourseRegistry
from sparktutor.engine import demo_scenarios as demo
from sparktutor.engine.executor import ExecMode, ExecResult, Executor
from sparktutor.engine.exercise_validation import build_validation_script
from sparktutor.server.handler import ServerHandler


def dimensions(report):
    return {item["key"]: item for item in report["dimensions"]}


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario_id,transfer,stage_count", [
    ("transfer_retry", 0, 9), ("transfer_first_pass", 100, 7),
])
async def test_recorded_real_pipeline_isolated_and_prefix_correct(monkeypatch, scenario_id, transfer, stage_count):
    instances = []
    methods = []
    progress = []
    original_dispatch = ServerHandler.dispatch

    def construct(*args, **kwargs):
        handler = ServerHandler(*args, **kwargs)
        instances.append(handler)
        return handler

    async def dispatch(self, msg):
        methods.append(msg["method"])
        return await original_dispatch(self, msg)

    def forbidden(*args, **kwargs):
        raise AssertionError("Recorded demo must not load user settings, call AI, or start processes")

    monkeypatch.setattr(demo, "ServerHandler", construct)
    monkeypatch.setattr(ServerHandler, "dispatch", dispatch)
    monkeypatch.setattr(Settings, "load", forbidden)
    monkeypatch.setattr(Executor, "_start_process", forbidden)
    report = await demo.run_demo_scenario(scenario_id=scenario_id, on_progress=progress.append)
    assert len(instances) == 1
    assert not instances[0].settings.data_dir.exists()
    assert not instances[0].registry.courses_dir.exists()
    assert report["schemaVersion"] == 1 and report["isDemo"] is True and report["isolated"] is True
    assert report["mode"] == "recorded" and report["sparkVerified"] is False
    assert "合成" in report["provenanceLabel"] and "预设" in report["disclaimer"]
    assert "未运行 Spark" in report["disclaimer"]
    assert len(report["stages"]) == stage_count
    assert all(check["passed"] for check in report["checks"])
    assert all(check["passed"] for stage in report["stages"] for check in stage["checks"])
    assert [item["index"] for item in progress] == list(range(1, stage_count + 1))
    assert all(set(item) == {"id", "title", "index", "total"} and item["total"] == stage_count for item in progress)
    assert methods.count("run") == 2
    assert methods.count("getHint") == 1
    assert methods.count("openRecommendedExercise") == 1
    assert methods.count("submit") == (3 if transfer == 0 else 2)
    assert "getSolution" not in methods
    assert "recordLearningEvent" in methods and "getLearningReview" in methods
    stages = {stage["id"]: stage for stage in report["stages"]}
    assert stages["empty"]["review"] is None
    assert all(item["score"] is None for item in dimensions(stages["empty"]["diagnosis"]).values())
    a_dims = dimensions(stages["a_pass"]["diagnosis"])
    assert [a_dims[key]["score"] for key in ("knowledge", "debugging", "hint_dependency", "transfer")] == [100, 100, 100, None]
    recommendation = stages["a_pass"]["diagnosis"]["recommendedExercise"]
    assert recommendation["stepId"] == report["taskB"]["stepId"] == "sensor_transfer"
    assert a_dims["debugging"]["sampleSize"] == 1  # Two runs are one repair episode.
    assert stages["a_edit"]["review"]["before"] == stages["a_edit"]["review"]["after"]
    final = report["stages"][-1]
    final_dims = dimensions(final["diagnosis"])
    assert [final_dims[key]["score"] for key in ("knowledge", "debugging", "hint_dependency", "transfer")] == [100, 100, 50, transfer]
    assert final_dims["transfer"]["sampleSize"] == 1
    assert report["eventCount"] == len(final["events"])
    previous = []
    for stage in report["stages"]:
        ids = [event["eventId"] for event in stage["events"]]
        assert ids[:len(previous)] == previous
        if stage["review"]:
            assert stage["review"]["event"]["eventId"] in ids
            assert {key: value["score"] for key, value in dimensions(stage["review"]["after"]).items()} == {
                key: value["score"] for key, value in dimensions(stage["diagnosis"]).items()}
        previous = ids
    assert not any(event["eventType"] == "code_submit" for event in stages["a_hint"]["events"])
    if transfer == 0:
        assert dimensions(stages["b_first"]["diagnosis"])["knowledge"]["score"] == 50
        assert dimensions(stages["b_first"]["diagnosis"])["transfer"]["score"] == 0
        assert dimensions(stages["b_repair"]["review"]["before"])["transfer"]["score"] == 0
        assert dimensions(stages["b_repair"]["review"]["after"])["transfer"]["score"] == 0
    encoded = json.dumps(report, ensure_ascii=False)
    for private in (str(instances[0].settings.data_dir), "Traceback (", "def mnm_analysis", "student_source ="):
        assert private not in encoded
    for event in final["events"]:
        assert not ({"data", "code", "stdout", "stderr", "path", "sessionId"} & set(event))
        assert event["eventType"] != "solution_view"


@pytest.mark.asyncio
async def test_async_progress_and_concurrent_instances_do_not_share_history():
    import asyncio
    seen = []

    async def progress(value):
        await asyncio.sleep(0)
        seen.append(value["id"])

    first, second = await asyncio.gather(
        demo.run_demo_scenario(on_progress=progress),
        demo.run_demo_scenario(scenario_id="transfer_first_pass"),
    )
    first_ids = {event["eventId"] for event in first["stages"][-1]["events"]}
    second_ids = {event["eventId"] for event in second["stages"][-1]["events"]}
    assert first_ids.isdisjoint(second_ids)
    assert seen[0] == "empty" and seen[-1] == "b_repair"


@pytest.mark.asyncio
@pytest.mark.parametrize("parameters", [
    {"scenario_id": "unknown"}, {"scenario_id": []}, {"mode": "dry_run"}, {"mode": None},
])
async def test_invalid_request_does_not_construct_handler(monkeypatch, parameters):
    def forbidden(*args, **kwargs):
        raise AssertionError("Invalid request must not create a database")
    monkeypatch.setattr(demo, "ServerHandler", forbidden)
    with pytest.raises(ValueError):
        await demo.run_demo_scenario(**parameters)


def test_presets_preserve_all_original_course_assertions_and_limit_only_resources(tmp_path):
    original = CourseRegistry()
    copied = demo._prepare_registry(tmp_path / "courses")
    for lesson, filename, variants in [
        ("01_getting_started", "starter.py", lambda source: [demo._a_code(source, value) for value in range(3)]),
        ("11_diagnosis_practice", "sensor_transfer_starter.py", lambda source: [demo._b_code(source, value) for value in (False, True)]),
    ]:
        relative = Path("learning_spark") / "lessons" / lesson / filename
        original_text = (original.courses_dir / relative).read_text(encoding="utf-8")
        copied_text = (copied.courses_dir / relative).read_text(encoding="utf-8")
        assertions = [ast.dump(node) for node in ast.walk(ast.parse(original_text)) if isinstance(node, ast.Assert)]
        assert assertions
        assert 'local[*]' not in copied_text and 'local[2]' in copied_text
        if filename == "starter.py":
            assert f"tempfile.mkdtemp(dir={str(tmp_path)!r})" in copied_text
        for code in variants(copied_text):
            assert [ast.dump(node) for node in ast.walk(ast.parse(code)) if isinstance(node, ast.Assert)] == assertions
            script = build_validation_script(code, copied_text)
            assert "COURSE_TESTS_PASSED" in script and "optimize=0" in script
        assert (original.courses_dir / relative).read_text(encoding="utf-8") == original_text


@pytest.mark.asyncio
async def test_callback_failure_cleans_the_isolated_database(monkeypatch):
    paths = []
    def construct(*args, **kwargs):
        handler = ServerHandler(*args, **kwargs)
        paths.append(handler.settings.data_dir)
        return handler
    def progress(value):
        raise RuntimeError("stop demo")
    monkeypatch.setattr(demo, "ServerHandler", construct)
    with pytest.raises(RuntimeError, match="stop demo"):
        await demo.run_demo_scenario(on_progress=progress)
    assert paths and not paths[0].parent.exists()


@pytest.mark.asyncio
async def test_spark_preflight_failure_never_falls_back(monkeypatch):
    async def unavailable(executor):
        assert type(executor) is Executor
        raise demo.DemoScenarioError("缺少 Java")
    def synthetic(*args, **kwargs):
        raise AssertionError("No fallback allowed")
    monkeypatch.setattr(demo, "_check_spark_environment", unavailable)
    monkeypatch.setattr(demo, "_RecordedExecutor", synthetic)
    with pytest.raises(demo.DemoScenarioError, match="Java"):
        await demo.run_demo_scenario(mode="spark")


@pytest.mark.asyncio
async def test_spark_infrastructure_failure_is_not_a_learner_episode(monkeypatch):
    async def preflight(executor):
        pass
    async def failed(self, code, on_output=None):
        return ExecResult(ExecMode.LOCAL, 1, "", "JAVA_GATEWAY_EXITED: private environment diagnostics")
    monkeypatch.setattr(demo, "_check_spark_environment", preflight)
    monkeypatch.setattr(Executor, "execute", failed)
    with pytest.raises(demo.DemoScenarioError, match="Spark 环境") as error:
        await demo.run_demo_scenario(mode="spark")
    assert "private environment diagnostics" not in str(error.value)


@pytest.mark.asyncio
async def test_spark_worker_crash_has_a_clear_safe_error(monkeypatch):
    async def preflight(executor):
        pass
    async def failed(self, code, on_output=None):
        return ExecResult(ExecMode.LOCAL, 1, "Python worker exited unexpectedly (crashed)", "private worker path")
    monkeypatch.setattr(demo, "_check_spark_environment", preflight)
    monkeypatch.setattr(Executor, "execute", failed)
    with pytest.raises(demo.DemoScenarioError, match="Python worker") as error:
        await demo.run_demo_scenario(mode="spark")
    assert "不是学习者错误" in str(error.value) and "private worker path" not in str(error.value)


@pytest.mark.asyncio
async def test_changed_recommendation_stops_instead_of_overriding_it(monkeypatch):
    original = ServerHandler._get_diagnosis
    async def no_recommendation(self, params):
        report = await original(self, params)
        report["recommendedExercise"] = None
        return report
    monkeypatch.setattr(ServerHandler, "_get_diagnosis", no_recommendation)
    with pytest.raises(demo.DemoScenarioError, match="推荐未指向"):
        await demo.run_demo_scenario()


@pytest.mark.spark
@pytest.mark.skipif(os.environ.get("SPARKTUTOR_TEST_SPARK") != "1", reason="Opt in to real Spark execution")
@pytest.mark.asyncio
@pytest.mark.parametrize("scenario_id,transfer", [("transfer_retry", 0), ("transfer_first_pass", 100)])
async def test_real_spark_uses_course_harness(scenario_id, transfer):
    report = await demo.run_demo_scenario(mode="spark", scenario_id=scenario_id)
    assert report["sparkVerified"] is True
    assert all(check["passed"] for check in report["checks"])
    assert dimensions(report["stages"][-1]["diagnosis"])["transfer"]["score"] == transfer
