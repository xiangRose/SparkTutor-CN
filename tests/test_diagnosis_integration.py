"""Issue #5 evidence production, persistence and recommendation navigation."""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from sparktutor.config.settings import Settings
from sparktutor.courses.registry import CourseRegistry
from sparktutor.engine.executor import ExecMode, ExecResult
from sparktutor.engine.exercise_catalog import build_exercise_catalog
from sparktutor.engine.exercise_validation import VALIDATION_MARKER
from sparktutor.server.handler import ServerHandler
from sparktutor.state.learning_events import LearningEventStore


@pytest.fixture
def handler(sample_course_dir, tmp_path):
    instance = ServerHandler(Settings(data_dir=tmp_path / "data"))
    instance.registry = CourseRegistry(sample_course_dir.parent)
    return instance


async def call(handler, method, **params):
    return await handler.dispatch({"method": method, "params": params})


def scores(report):
    return {item["key"]: item["score"] for item in report["dimensions"]}


async def choice(handler):
    await call(handler, "loadLesson", courseId="test_course", lessonIdx=0)
    await call(handler, "advance")


@pytest.mark.asyncio
async def test_choice_evidence_survives_restart_and_repeated_refresh(handler):
    await choice(handler)
    await call(handler, "submit", code="4")
    report = await call(handler, "getDiagnosis")
    assert scores(report)["knowledge"] == 100
    assert report["eligibleAssessmentCount"] == 1
    assert await call(handler, "getDiagnosis") == report
    event = next(e for e in handler.events.list_events() if e.event_type == "code_submit")
    assert event.data["assessmentEligible"] is True
    assert event.data["assessmentSource"] == "choice"
    assert event.data["stepId"] == handler._runner.current_step().id
    restarted = ServerHandler(handler.settings)
    restarted.registry = handler.registry
    assert scores(await call(restarted, "getDiagnosis")) == scores(report)


@pytest.mark.asyncio
async def test_failure_then_pass_updates_one_task_without_revealing_answer(handler):
    await choice(handler)
    wrong = await call(handler, "submit", code="3")
    assert "正确答案是" not in wrong["feedback"][0]["message"]
    assert scores(await call(handler, "getDiagnosis"))["knowledge"] == 0
    await call(handler, "submit", code="4")
    report = await call(handler, "getDiagnosis")
    k = next(d for d in report["dimensions"] if d["key"] == "knowledge")
    assert k["score"] == 100
    assert k["evidenceCount"] == 1


@pytest.mark.asyncio
async def test_unavailable_hint_does_not_create_dependency(handler):
    await choice(handler)
    handler._runner.current_step().hint = None
    await call(handler, "getHint")
    await call(handler, "submit", code="4")
    assert scores(await call(handler, "getDiagnosis"))["hint_dependency"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", ['{"passed": true}', '{"passed": false}', '{"passed": }'])
async def test_ai_only_evidence_is_not_promoted_to_verified_scores(handler, raw):
    await choice(handler)
    await call(handler, "submit", code="4")
    await call(handler, "advance")
    review = await call(handler, "buildReviewPrompt", code="x = int('42')")
    await call(handler, "parseReviewResponse", reviewId=review["reviewId"], rawText=raw)
    report = await call(handler, "getDiagnosis")
    assert report["eligibleAssessmentCount"] == 1
    submits = [e for e in handler.events.list_events() if e.event_type == "code_submit"]
    assert submits[-1].data["assessmentEligible"] is False


@pytest.mark.asyncio
async def test_answer_view_keeps_later_copy_out_of_knowledge(handler):
    await choice(handler)
    step = handler._runner.current_step()
    step.solution_code = "answer.py"
    (handler._runner.state.lesson.base_path / step.solution_code).write_text("4", encoding="utf-8")
    await call(handler, "getSolution")
    await call(handler, "submit", code="4")
    assert scores(await call(handler, "getDiagnosis"))["knowledge"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,exit_code,stderr,expected", [
    (ExecMode.LOCAL, 1, "AssertionError: wrong total", True),
    (ExecMode.LOCAL, 1, "ModuleNotFoundError: No module named pyspark", False),
    (ExecMode.LOCAL, 124, "Execution timed out", False),
    (ExecMode.DRY_RUN, 0, "", False),
    (ExecMode.LOCAL, 1, "unknown launcher failure", False),
    (ExecMode.LOCAL, 0, "", True),
])
async def test_course_result_has_conservative_evidence_qualification(handler, mode, exit_code, stderr, expected):
    await choice(handler)
    step = handler._runner.current_step()
    step.cls = "script"
    step.requires_execution = True
    step.correct_answer = None
    step.starter_code = "starter.py"
    template = "def answer():\n    pass\nif __name__ == '__main__':\n    assert answer() == 42\n"
    (handler._runner.state.lesson.base_path / step.starter_code).write_text(template, encoding="utf-8")
    handler.executor.execute = AsyncMock(return_value=ExecResult(mode, exit_code, VALIDATION_MARKER if exit_code == 0 else "", stderr))
    await call(handler, "submit", code="def answer():\n    return 42")
    event = [e for e in handler.events.list_events() if e.event_type == "code_submit"][-1]
    assert event.data["assessmentEligible"] is expected
    assert (await call(handler, "getDiagnosis"))["eligibleAssessmentCount"] == int(expected)


@pytest.mark.asyncio
@pytest.mark.parametrize("stdout,stderr,error_type", [
    ("", "NameError: bad", "NameError"),
    ("AssertionError: wrong total", "Spark INFO", "AssertionError"),
])
async def test_actual_run_failure_requires_assessment_to_confirm_repair(handler, stdout, stderr, error_type):
    await choice(handler)
    await call(handler, "submit", code="4")
    await call(handler, "advance")
    handler.executor.execute = AsyncMock(return_value=ExecResult(ExecMode.LOCAL, 1, stdout, stderr))
    await call(handler, "run", code="bad")
    event = [event for event in handler.events.list_events() if event.event_type == "code_run"][-1]
    assert event.data["assessmentEligible"] is True
    assert event.data["errorType"] == error_type
    handler.executor.execute.return_value = ExecResult(ExecMode.LOCAL, 0, "", "")
    await call(handler, "run", code="pass")
    assert scores(await call(handler, "getDiagnosis"))["debugging"] == 0
    await call(handler, "submit", code="x = 42")
    assert scores(await call(handler, "getDiagnosis"))["debugging"] == 100


@pytest.mark.asyncio
async def test_recommended_step_opens_exactly_without_overwriting_course_progress(handler):
    await choice(handler)
    saved = handler.progress.get("test_course", "01_test_lesson")
    recommendation = (await call(handler, "getDiagnosis"))["recommendedExercise"]
    assert recommendation is not None
    result = await call(handler, "openRecommendedExercise", **{
        key: recommendation[key] for key in ("courseId", "lessonId", "stepId")
    })
    assert result["step"]["id"] == recommendation["stepId"]
    assert result["practiceMode"] is True
    assert handler.progress.get("test_course", "01_test_lesson") == saved
    await call(handler, "submit", code="4")
    finished = await call(handler, "advance")
    assert finished == {"finished": True, "practiceMode": True}
    assert handler.progress.get("test_course", "01_test_lesson") == saved


@pytest.mark.asyncio
async def test_stale_or_forged_recommendation_rejected_before_navigation(handler):
    await choice(handler)
    rec = (await call(handler, "getDiagnosis"))["recommendedExercise"]
    await call(handler, "submit", code="4")
    current = handler._runner
    with pytest.raises(ValueError, match="推荐"):
        await call(handler, "openRecommendedExercise", **{k: rec[k] for k in ("courseId", "lessonId", "stepId")})
    assert handler._runner is current
    with pytest.raises(ValueError):
        await call(handler, "getDiagnosis", courseId="missing")


def test_diagnosis_reads_history_beyond_public_list_limit(tmp_path):
    store = LearningEventStore(tmp_path / "events.db")
    for _ in range(2001):
        store.record("session_start")
    assert len(store.list_events()) == 2000
    assert len(store.list_events(limit=None)) == 2001


def test_migrate_legacy_tracker_columns_without_losing_metadata(tmp_path):
    import sqlite3
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE learning_events (event_id TEXT PRIMARY KEY, event_type TEXT NOT NULL, "
            "timestamp TEXT NOT NULL, course_id TEXT NOT NULL DEFAULT '', lesson_id TEXT NOT NULL DEFAULT '', "
            "task_id TEXT NOT NULL DEFAULT '', attempt_number INTEGER NOT NULL DEFAULT 0, "
            "knowledge_components TEXT NOT NULL DEFAULT '[]', data TEXT NOT NULL DEFAULT '{}')"
        )
        connection.execute("INSERT INTO learning_events VALUES ('old','code_submit','2026-01-01','c','l','t',1,'[\"schema\"]','{\"passed\": true}')")
    store = LearningEventStore(path)
    store.record("session_start", session_id="new")
    old, new = store.list_events()
    assert old.data["knowledgeComponents"] == ["schema"]
    assert old.data.get("assessmentEligible") is None
    assert new.session_id == "new"


def test_real_catalog_has_explicit_knowledge_and_resolvable_transfer_sources():
    catalog = build_exercise_catalog(CourseRegistry())
    lookup = {(s["courseId"], s["lessonId"], s["stepId"]): s for s in catalog}
    assert len(lookup) == len(catalog)
    for spec in catalog:
        assert spec["knowledgeComponents"], spec
        assert spec["context"], spec
        for ref in spec["prerequisites"]:
            assert (ref["courseId"], ref["lessonId"], ref["stepId"]) in lookup
        transfer = spec.get("transfer")
        if not transfer:
            continue
        assert transfer["sources"]
        for ref in transfer["sources"]:
            source = lookup[(ref["courseId"], ref["lessonId"], ref["stepId"])]
            assert source["context"] != transfer["context"]
            assert set(source["knowledgeComponents"]) & set(spec["knowledgeComponents"])


@pytest.mark.asyncio
async def test_real_catalog_recommends_and_measures_new_sensor_context(tmp_path):
    handler = ServerHandler(Settings(data_dir=tmp_path))
    handler.events.record(
        "code_submit", course_id="learning_spark", lesson_id="01_getting_started",
        task_id="01_getting_started:15", task_type="script", timestamp="2026-01-01T00:00:00Z",
        data={"passed": True, "assessmentEligible": True, "assessmentSource": "course_tests",
              "mode": "local", "validationPassed": True},
    )
    report = await call(handler, "getDiagnosis")
    rec = report["recommendedExercise"]
    assert rec["lessonId"] == "11_diagnosis_practice"
    assert rec["stepId"] == "sensor_transfer"
    assert rec["dimension"] == "transfer"
    opened = await call(handler, "openRecommendedExercise", **{
        name: rec[name] for name in ("courseId", "lessonId", "stepId")
    })
    assert opened["step"]["id"] == "sensor_transfer"
    assert opened["practiceMode"] is True
    state = handler._runner.state
    source = (state.lesson.base_path / state.current_step.solution_code).read_text(encoding="utf-8")
    handler.executor.execute = AsyncMock(return_value=ExecResult(ExecMode.LOCAL, 0, VALIDATION_MARKER, ""))
    await call(handler, "submit", code=source)
    result = await call(handler, "getDiagnosis")
    assert scores(result)["knowledge"] == 100
    assert scores(result)["transfer"] == 100
    transfer = next(d for d in result["dimensions"] if d["key"] == "transfer")
    assert transfer["evidenceCount"] == 1
    assert handler.progress.get("learning_spark", "11_diagnosis_practice") is None
