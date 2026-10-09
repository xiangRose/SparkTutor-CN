"""Reproducible, isolated teaching demonstrations of the real learning pipeline.

Recorded mode simulates only process outcomes. Neither mode represents a human
learner: the implementations and their sequence are predefined demonstration
inputs. No user settings or persistent learning database is opened here.
"""

from __future__ import annotations

import ast
import asyncio
from collections import deque
from datetime import datetime, timezone
import inspect
import os
from pathlib import Path
import re
import shutil
import sys
from tempfile import TemporaryDirectory
from typing import Callable

from sparktutor.config.settings import ExecutionMode, Settings
from sparktutor.courses.registry import CourseRegistry
from sparktutor.engine.executor import ExecMode, ExecResult, Executor
from sparktutor.engine.exercise_catalog import build_exercise_catalog
from sparktutor.engine.exercise_validation import VALIDATION_MARKER
from sparktutor.engine.learning_history import LearningHistory
from sparktutor.server.handler import ServerHandler


_COURSE = "learning_spark"
_A = ("01_getting_started", "15")
_B = ("11_diagnosis_practice", "sensor_transfer")
_TITLES = {
    "transfer_retry": "修复后再迁移：重试通过不会改写首次迁移结果",
    "transfer_first_pass": "修复后首次迁移通过",
}
_DISCLAIMER = (
    "这是隔离的教学演示，代码来自预设参考实现与预设错误变体，并非真人自主作答，"
    "分数不能用于评价真实学习者。所有事件仅写入运行期间的临时数据库。"
    "课程副本限制为 local[2] 和 2 个 shuffle 分区，测试数据使用临时目录，课程断言保持不变。"
    "提示覆盖是描述性比例；首次迁移失败后的重试不会重写首次迁移证据。"
)


class DemoScenarioError(RuntimeError):
    """The requested demonstration could not be verified; never fake success."""


def list_demo_scenarios() -> list[dict]:
    return [{"id": key, "title": title} for key, title in _TITLES.items()]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _check(identifier: str, label: str, passed: bool) -> dict:
    return {"id": identifier, "label": label, "passed": bool(passed)}


def _require(passed: bool, message: str) -> None:
    if not passed:
        raise DemoScenarioError(message)


class _RecordedExecutor(Executor):
    """Per-instance synthetic outcomes, explicitly labelled in the report."""

    def __init__(self, settings: Settings, retry: bool):
        super().__init__(settings=settings)
        self._outcomes = deque([
            (False, False), (False, False), (True, True), (True, not retry),
            *([(True, True)] if retry else []),
        ])

    async def execute(self, code: str, on_output=None) -> ExecResult:
        _require(bool(self._outcomes), "录制演示执行次数超出预设轨迹。")
        harness, passed = self._outcomes.popleft()
        _require((VALIDATION_MARKER in code) == harness,
                 "录制演示的运行/课程提交顺序不匹配。")
        return ExecResult(
            mode=ExecMode.LOCAL, exit_code=0 if passed else 1,
            stdout=VALIDATION_MARKER + "\n" if passed else "",
            stderr="" if passed else "AssertionError: 预设演示结果与课程断言不符",
        )


async def _check_spark_environment(executor: Executor) -> None:
    """Check the current interpreter and Java without loading user settings."""
    try:
        process = await executor._start_process(
            sys.executable, "-c", "import pyspark; print(pyspark.__version__)",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await executor._communicate(process, timeout=20)
        version = stdout.decode("utf-8", errors="replace").strip().splitlines()
        _require(process.returncode == 0 and bool(version) and version[-1] == "4.1.1",
                 "Spark 演示需要当前 Python 环境安装项目基线 PySpark 4.1.1；请安装 spark 可选依赖。")
        java_home = os.environ.get("JAVA_HOME")
        java = str(Path(java_home) / "bin" / ("java.exe" if os.name == "nt" else "java")) if java_home else "java"
        process = await executor._start_process(
            java, "-version", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await executor._communicate(process, timeout=10)
        version = re.search(r'version\s+"(\d+)', (stdout + stderr).decode("utf-8", errors="replace"))
        _require(process.returncode == 0 and version is not None and int(version[1]) >= 17,
                 "Spark 演示需要可用的 Java 17 或更高版本，请检查 JAVA_HOME 和 Java 安装。")
        launcher = executor._spark_submit()
        _require(Path(launcher).is_file() or shutil.which(launcher) is not None,
                 "当前 Python 环境缺少可用的 spark-submit，请安装项目的 spark 可选依赖。")
    except (OSError, asyncio.TimeoutError) as error:
        raise DemoScenarioError("Spark 环境检查未完成：请检查当前 Python 的 PySpark 4.1.1、Java 17+ 和 spark-submit；演示未降级。") from error


def _limit_resources(source: str, *, temporary_root: Path | None = None) -> str:
    # Only the copied template changes. All assertions, fixtures and imports
    # remain course-owned; no global environment/class patches are needed.
    result = source.replace('.master("local[*]")', '.master("local[2]")').replace(
        ".getOrCreate()", '.config("spark.sql.shuffle.partitions", "2").getOrCreate()'
    )
    if temporary_root is not None:
        # The original M&M harness creates CSV fixtures with mkdtemp without
        # removing them. Keep those fixtures inside this demo's managed root.
        result = result.replace("tempfile.mkdtemp()", f"tempfile.mkdtemp(dir={str(temporary_root)!r})")
    return result


def _prepare_registry(destination: Path) -> CourseRegistry:
    original = CourseRegistry()
    # Keep the full catalog so the actual recommender, including prerequisites,
    # chooses the next exercise. Copy only this course's local teaching assets.
    for course in original.list_courses():
        shutil.copytree(original.courses_dir / course.id, destination / course.id,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for lesson, filename in ((_A[0], "starter.py"), (_B[0], "sensor_transfer_starter.py")):
        path = destination / _COURSE / "lessons" / lesson / filename
        original_source = path.read_text(encoding="utf-8")
        limited = _limit_resources(original_source, temporary_root=destination.parent)
        original_asserts = [ast.dump(node) for node in ast.walk(ast.parse(original_source)) if isinstance(node, ast.Assert)]
        copied_asserts = [ast.dump(node) for node in ast.walk(ast.parse(limited)) if isinstance(node, ast.Assert)]
        _require(bool(original_asserts) and copied_asserts == original_asserts,
                 "演示课程副本未保留原课程断言。")
        path.write_text(limited, encoding="utf-8")
    return CourseRegistry(courses_dir=destination)


def _replace_function(template: str, name: str, implementation: str) -> str:
    function = next((node for node in ast.parse(template).body
                     if isinstance(node, ast.FunctionDef) and node.name == name), None)
    _require(function is not None, "演示练习的课程函数入口已变化。")
    lines = template.splitlines(keepends=True)
    result = "".join(lines[:function.lineno - 1]) + implementation.strip() + "\n" + "".join(lines[function.end_lineno:])
    ast.parse(result)
    return result


def _a_code(template: str, variant: int) -> str:
    grouping = '"State"' if variant == 0 else '"State", "Color"'
    aggregation = 'f.sum("Count")' if variant == 2 else 'f.count("*")'
    return _replace_function(template, "mnm_analysis", f'''
def mnm_analysis(spark, file_path):
    records = spark.read.option("header", "true").option("inferSchema", "true").csv(file_path)
    totals = records.groupBy({grouping}).agg({aggregation}.alias("Total"))
    return totals.filter(f.col("State") == "CA").orderBy(f.col("Total").desc())
''')


def _b_code(template: str, passed: bool) -> str:
    aggregation = 'f.sum("reading_count")' if passed else 'f.count("*")'
    return _replace_function(template, "transfer_sensor_aggregation", f'''
def transfer_sensor_aggregation(spark, readings, target_site):
    schema = StructType([
        StructField("site", StringType(), False),
        StructField("sensor_type", StringType(), False),
        StructField("reading_count", IntegerType(), False),
    ])
    records = spark.createDataFrame(readings, schema)
    totals = records.groupBy("site", "sensor_type").agg({aggregation}.alias("total_readings"))
    return (totals.filter(f.col("site") == target_site)
            .orderBy(f.col("total_readings").desc(), f.col("sensor_type").asc()))
''')


def _task(catalog: list[dict], reference: tuple[str, str]) -> dict:
    entry = next((item for item in catalog if item["courseId"] == _COURSE
                  and (item["lessonId"], item["stepId"]) == reference), None)
    _require(entry is not None, "演示引用的课程练习已不可用。")
    return {key: entry[key] for key in ("courseId", "lessonId", "stepId", "taskId", "title", "context")}


def _dimensions(report: dict) -> dict:
    return {item["key"]: item for item in report["dimensions"]}


def _scores(report: dict) -> dict:
    return {key: item["score"] for key, item in _dimensions(report).items()}


async def run_demo_scenario(
    *, scenario_id: str = "transfer_retry", mode: str = "recorded",
    on_progress: Callable[[dict], object] | None = None,
) -> dict:
    """Exercise tracker → assessment → diagnosis → recommendation → review.

    ``on_progress`` may be synchronous or asynchronous; its payload contains
    only ``id``, ``title``, ``index`` (1-based) and ``total``. Failure never
    switches a Spark demonstration to synthetic execution.
    """
    if not isinstance(scenario_id, str) or scenario_id not in _TITLES:
        raise ValueError("未知演示场景。")
    if not isinstance(mode, str) or mode not in {"recorded", "spark"}:
        raise ValueError("演示模式必须是 recorded 或 spark。")
    retry = scenario_id == "transfer_retry"
    started = _now()
    stages: list[dict] = []
    stage_total = 9 if retry else 7
    execution_log: list[tuple[str, bool]] = []
    verified_submissions: list[bool] = []
    expected_operations = [("run", False), ("run", False), ("submit", True),
                           ("submit", not retry), *([("submit", True)] if retry else [])]
    with TemporaryDirectory(prefix="sparktutor_demo_") as directory:
        root = Path(directory)
        settings = Settings(data_dir=root / "data", execution_mode=ExecutionMode.LOCAL)
        handler = ServerHandler(settings=settings)
        handler.registry = _prepare_registry(root / "courses")
        if mode == "recorded":
            handler.executor = _RecordedExecutor(settings, retry)
        else:
            await _check_spark_environment(handler.executor)
        catalog = build_exercise_catalog(handler.registry)
        task_a, task_b = _task(catalog, _A), _task(catalog, _B)

        async def dispatch(method: str, **params) -> dict:
            return await handler.dispatch({"method": method, "params": params})

        async def stage(identifier: str, title: str, explanation: str, *, expected: dict,
                        checks: list[dict] | None = None) -> dict:
            diagnosis = await dispatch("getDiagnosis", courseId=_COURSE)
            history = LearningHistory(handler.registry, handler.events)
            candidates = [event for event in history.events if event.task_id and event.event_type
                          in {"code_run", "code_edit", "hint_request", "code_submit"}]
            last = candidates[-1] if candidates else None
            review = await dispatch(
                "getLearningReview", courseId=last.course_id, lessonId=last.lesson_id,
                taskId=last.task_id, eventId=last.event_id, scopeCourseId=_COURSE,
                snapshotEventId=history.snapshot_event_id,
            ) if last else None
            actual = _scores(diagnosis)
            validations = [_check(f"{identifier}_{key}", f"{key} = {value if value is not None else '证据不足'}",
                                  actual[key] == value) for key, value in expected.items()]
            validations.extend(checks or [])
            if review is not None:
                validations.append(_check(f"{identifier}_review", "复盘 after 与当前画像相同",
                                          _scores(review["after"]) == actual))
            displayed_title = f"合成演示 · {title}" if mode == "recorded" else title
            value = {"id": identifier, "title": displayed_title, "explanation": explanation,
                     "diagnosis": diagnosis, "review": review,
                     "events": [history.describe_event(event) for event in history.events],
                     "checks": validations}
            stages.append(value)
            if on_progress:
                pending = on_progress({"id": identifier, "title": displayed_title, "index": len(stages), "total": stage_total})
                if inspect.isawaitable(pending):
                    await pending
            return value

        async def edit(task: dict, old: str, new: str, version: int) -> None:
            result = await dispatch(
                "recordLearningEvent", eventType="code_edit", courseId=task["courseId"],
                lessonId=task["lessonId"], taskId=task["taskId"], data={
                    "changeCount": 1, "removedChars": len(old), "addedChars": len(new),
                    "documentVersion": version, "burstDurationMs": 0,
                },
            )
            _require(result["recorded"] is True, "演示编辑事件未归属到当前题目。")

        async def execute(method: str, code: str, expected_pass: bool) -> dict:
            try:
                response = await dispatch(method, code=code)
            except (OSError, asyncio.TimeoutError) as error:
                raise DemoScenarioError("演示执行未完成，请检查本机 Spark/Java 环境；未生成替代成绩。") from error
            event = next(event for event in reversed(handler.events.list_events(limit=None))
                         if event.event_type == ("code_run" if method == "run" else "code_submit"))
            execution = handler._runner.state.last_exec if handler._runner and handler._runner.state else None
            if execution is not None and not execution.success and "Python worker exited unexpectedly" in execution.stdout + execution.stderr:
                raise DemoScenarioError(
                    "本机 Spark 的 Python worker 意外退出，课程测试未完成；请检查 Python/PySpark/Java 的运行兼容性。"
                    "这不是学习者错误，演示未降级为合成执行。"
                )
            _require(event.data.get("assessmentEligible") is True,
                     "演示未产生有效课程证据；可能是 Spark 环境、执行超时或课程验证失败，未降级。")
            if method == "run":
                _require(response["mode"] == "local" and response["exitCode"] != 0
                         and "AssertionError" in response["stderr"] + response["stdout"],
                         "演示预期由完整课程测试识别聚合错误，但执行结果不符。")
            else:
                _require(response["passed"] is expected_pass and event.data.get("assessmentSource") == "course_tests",
                         "演示提交结果不符合当前课程测试，请检查练习或运行环境；未伪造通过。")
                if response["passed"]:
                    verified_submissions.append(event.data.get("validationPassed") is True)
            execution_log.append((method, response["exitCode"] == 0 if method == "run" else response["passed"]))
            return response

        try:
            no_evidence = dict(knowledge=None, debugging=None, hint_dependency=None, transfer=None)
            await stage("empty", "隔离的新会话", "尚无题目评估，四维均保留证据不足。", expected=no_evidence)
            course = handler.registry.get_course(_COURSE)
            loaded = await handler._load_lesson(
                {"courseId": _COURSE, "lessonIdx": course.lessons.index(_A[0]), "depth": "beginner"},
                target_step_id=_A[1],
            )
            a0, a1, a2 = [_a_code(loaded["starterCode"], variant) for variant in range(3)]
            await execute("run", a0, False)
            failure = dict(knowledge=None, debugging=0, hint_dependency=None, transfer=None)
            await stage("a_failure", "A：分组遗漏颜色，运行失败",
                        ("合成失败结果模拟课程断言未通过：" if mode == "recorded" else "完整课程测试发现：")
                        + "按州分组无法得到两组 CA 颜色；开启调试过程。", expected=failure)
            await edit(task_a, a0, a1, 1)
            await stage("a_edit", "A：补上颜色分组", "只记录编辑；尚未验证求和语义，编辑本身不产生分数。", expected=failure)
            await execute("run", a1, False)
            await stage("a_second_failure", "A：count 仍未累计糖果数量",
                        "分组正确，但 count 统计行数；" + ("再次记录合成失败结果" if mode == "recorded" else "原课程断言再次失败")
                        + "，仍是同一调试过程。", expected=failure)
            await dispatch("getHint")
            await stage("a_hint", "A：读取课程提示", "提示不会扣分；尚无首次有效提交，提示覆盖暂无分母。", expected=failure)
            await edit(task_a, a1, a2, 2)
            await execute("submit", a2, True)
            a_report = await dispatch("getDiagnosis", courseId=_COURSE)
            recommendation = a_report.get("recommendedExercise")
            recommended_b = recommendation is not None and all(recommendation.get(key) == task_b[key]
                                                               for key in ("courseId", "lessonId", "stepId"))
            await stage("a_pass", "A：合成通过结果进入课程判定链" if mode == "recorded" else "A：sum 通过真实可信测试",
                        "演示的知识和调试有通过证据；首次提交前使用过提示。真实推荐器选出新情境传感器练习 B。",
                        expected=dict(knowledge=100, debugging=100, hint_dependency=100, transfer=None),
                        checks=[_check("recommend_b", "推荐指向有来源依据的传感器迁移题 B", recommended_b)])
            _require(recommended_b, "当前课程推荐未指向传感器迁移练习，演示停止；不会改写推荐结果。")
            loaded = await dispatch("openRecommendedExercise", **{
                key: task_b[key] for key in ("courseId", "lessonId", "stepId")}, scopeCourseId=_COURSE)
            b_first = _b_code(loaded["starterCode"], not retry)
            await execute("submit", b_first, not retry)
            if retry:
                failed_transfer = dict(knowledge=50, debugging=50, hint_dependency=50, transfer=0)
                await stage("b_first", "B：首次迁移仍使用 count，未通过", "来源 A 已通过，但在新传感器情境首评失败；首次迁移证据为 0/1。", expected=failed_transfer)
                b_fixed = _b_code(loaded["starterCode"], True)
                await edit(task_b, b_first, b_fixed, 3)
                await stage("b_edit", "B：改为累计 reading_count", "修改尚未评估，四维保持不变。", expected=failed_transfer)
                await execute("submit", b_fixed, True)
                await stage("b_repair", "B：重试通过，保留首次迁移结果", "最近有效知识评估和调试修复更新；提示覆盖为 1/2，首次迁移仍为 0/1。",
                            expected=dict(knowledge=100, debugging=100, hint_dependency=50, transfer=0))
            else:
                await stage("b_first", "B：新传感器情境首次通过", "B 无提示且首次通过，首次迁移证据为 1/1；A 的调试过程已经确认修复。",
                            expected=dict(knowledge=100, debugging=100, hint_dependency=50, transfer=100))
            all_events = handler.events.list_events(limit=None)
            checks = [
                _check("stage_checks", "各节点的实际画像与复盘均满足演示预期", all(check["passed"] for item in stages for check in item["checks"])),
                _check("no_answer_event", "预设代码未伪装成真人查看答案事件", not any(event.event_type == "solution_view" for event in all_events)),
                _check("no_normal_progress", "内部练习导航不写课程进度", not handler.progress.get_course_summary(_COURSE, course.lessons)["started"]),
                _check("execution_sequence", "实际执行结果符合完整场景顺序", execution_log == expected_operations),
                _check("trusted_submissions", "两次合成通过结果均经课程判定链处理" if mode == "recorded" else "两次成功提交均经真实课程 harness 确认",
                       len(verified_submissions) == 2 and all(verified_submissions)),
            ]
            return {"schemaVersion": 1, "isDemo": True, "mode": mode, "scenarioId": scenario_id,
                    "title": _TITLES[scenario_id], "isolated": True, "taskA": task_a, "taskB": task_b,
                    "provenanceLabel": "合成执行结果 · 真实事件与评分链" if mode == "recorded" else "真实 Spark 课程测试 · 预设演示代码",
                    "disclaimer": _DISCLAIMER + ("recorded 模式使用预设 ExecResult，未运行 Spark。" if mode == "recorded" else "spark 模式实际执行原课程测试逻辑；通过结果来自可信 harness。"),
                    "stages": stages, "checks": checks, "eventCount": len(all_events),
                    "startedAt": started, "finishedAt": _now(),
                    "sparkVerified": mode == "spark" and execution_log == expected_operations
                    and len(verified_submissions) == 2 and all(verified_submissions)}
        finally:
            handler.end_session()
