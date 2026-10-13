"""Read-only dashboard progress, navigation and evidence contracts."""

import json

import pytest
import yaml

from sparktutor.config.settings import Settings
from sparktutor.courses.registry import CourseRegistry
from sparktutor.server.handler import ServerHandler


@pytest.fixture
def handler(tmp_path):
    courses = tmp_path / "courses"
    for course_id, count in (("a", 3), ("b", 2)):
        directory = courses / course_id
        directory.mkdir(parents=True)
        lesson_ids = [f"lesson_{index}" for index in range(1, count + 1)]
        (directory / "course.yaml").write_text(yaml.safe_dump({"course": {
            "id": course_id, "title": f"课程{course_id}", "description": "课程简介",
            "version": "1", "lessons": lesson_ids,
        }}, allow_unicode=True), encoding="utf-8")
        for lesson_id in lesson_ids:
            lesson = directory / "lessons" / lesson_id
            lesson.mkdir(parents=True)
            (lesson / "lesson.yaml").write_text(yaml.safe_dump([
                {"Class": "meta", "Lesson": f"真实标题{course_id}{lesson_id}", "EstimatedMinutes": 9},
                {"Class": "text", "Id": "intro", "Output": "教学内容"},
                {"Class": "cmd_question", "Id": "task", "Output": "任务",
                 "CorrectAnswer": "value = 1", "KnowledgeComponents": ["dataframe.schema"],
                 "Context": f"{course_id}_{lesson_id}"},
                {"Class": "text", "Id": "advanced", "Depth": "advanced", "Output": "进阶内容"},
            ], allow_unicode=True), encoding="utf-8")
    server = ServerHandler(Settings(data_dir=tmp_path / "data"))
    server.registry = CourseRegistry(courses)
    return server


async def call(handler, method="getLearningDashboard", **params):
    return await handler.dispatch({"method": method, "params": params})


def save(handler, course="a", lesson="lesson_1", *, completed=False, depth="beginner",
         updated="2026-01-01T00:00:00+00:00", step_id="task", index=1):
    handler.progress.save(course, lesson, current_step=index, total_steps=2,
                          completed=completed, depth=depth, current_step_id=step_id,
                          last_code="PRIVATE SOURCE MUST NOT APPEAR")
    with handler.progress._conn() as connection:
        connection.execute("UPDATE progress SET updated_at=? WHERE course_id=? AND lesson_id=?",
                           (updated, course, lesson))


def assessment(handler, course, lesson="lesson_1", *, passed=True, timestamp="2026-01-01T00:00:00Z", **data):
    return handler.events.record("code_submit", course_id=course, lesson_id=lesson,
                                 task_id=f"{lesson}:task", task_type="cmd_question", timestamp=timestamp,
                                 data={"passed": passed, "assessmentEligible": True,
                                       "assessmentSource": "exact", "mode": "local_check",
                                       "failureKind": "" if passed else "learner", **data})


def dimensions(report):
    return {entry["key"]: entry for entry in report["diagnosis"]["dimensions"]}


@pytest.mark.asyncio
async def test_empty_dashboard_has_real_titles_start_target_and_no_invented_scores(handler):
    result = await call(handler)
    assert result["selectedCourseId"] == ""
    assert result["catalogScope"] == "all"
    assert [course["id"] for course in result["courses"]] == ["a", "b"]
    first = result["courses"][0]["lessons"][0]
    assert first["title"] == "真实标题alesson_1"
    assert first["estimatedMinutes"] == 9
    assert first["status"] == "not_started"
    assert first["totalSteps"] == 2
    assert result["resume"]["courseId"] == "a"
    assert result["resume"]["lessonIdx"] == 0
    assert result["resume"]["stepId"] == "intro"
    assert result["resume"]["action"] == "start"
    assert all(item["score"] is None for item in dimensions(result).values())
    assert result["knowledgeComponents"] == {}


@pytest.mark.asyncio
async def test_out_of_order_completion_does_not_mark_a_prefix_completed(handler):
    save(handler, lesson="lesson_3", completed=True)
    result = await call(handler)
    course = result["courses"][0]
    assert [lesson["status"] for lesson in course["lessons"]] == ["not_started", "not_started", "completed"]
    assert course["progress"]["lessonsCompleted"] == 1
    assert course["progress"]["completionPercent"] == 33.3
    assert result["resume"]["lessonId"] == "lesson_1"
    sidebar = await call(handler, "getCourseProgress", courseId="a")
    assert sidebar["lessons"] == course["lessons"]
    assert sidebar["lessonsCompleted"] == 1
    assert sidebar["currentLessonIdx"] == 0
    assert "current_lesson_idx" not in sidebar


@pytest.mark.asyncio
async def test_resume_uses_latest_unfinished_valid_lesson_and_its_saved_depth(handler):
    save(handler, lesson="lesson_1", updated="2026-01-01T00:00:00Z")
    save(handler, lesson="lesson_3", updated="2026-01-03T00:00:00Z", depth="advanced", index=0)
    save(handler, lesson="lesson_2", updated="2026-01-04T00:00:00Z", completed=True)
    save(handler, lesson="deleted_lesson", updated="2026-01-05T00:00:00Z")
    save(handler, course="deleted_course", updated="2026-01-06T00:00:00Z")
    result = await call(handler)
    target = result["resume"]
    assert target["courseId"] == "a"
    assert target["lessonId"] == "lesson_3"
    assert target["lessonIdx"] == 2
    assert target["depth"] == "advanced"
    assert target["currentStep"] == 1  # Stable task id wins over old index zero.
    assert target["action"] == "resume"
    opened = await call(handler, "loadLesson", courseId=target["courseId"],
                        lessonIdx=target["lessonIdx"], depth=target["depth"])
    assert opened["step"]["id"] == target["stepId"]
    assert opened["currentIndex"] == target["currentStep"]


@pytest.mark.asyncio
async def test_scope_keeps_complete_catalog_but_filters_diagnosis_and_resume(handler):
    save(handler, course="a", updated="2026-01-01T00:00:00Z")
    save(handler, course="b", lesson="lesson_2", updated="2026-01-03T00:00:00Z")
    assessment(handler, "a", passed=True)
    assessment(handler, "b", passed=False)
    all_courses = await call(handler)
    assert all_courses["resume"]["courseId"] == "b"
    assert dimensions(all_courses)["knowledge"]["score"] == 50
    selected = await call(handler, courseId="a")
    assert len(selected["courses"]) == 2
    assert selected["resume"]["courseId"] == "a"
    assert selected["selectedCourseId"] == "a"
    assert dimensions(selected)["knowledge"]["score"] == 100
    assert selected["knowledgeComponents"] == {"dataframe.schema": {"evaluatedTasks": 1, "latestPassedTasks": 1}}


@pytest.mark.asyncio
async def test_selected_new_course_starts_there_instead_of_resuming_other_course(handler):
    save(handler, course="b")
    result = await call(handler, courseId="a")
    assert result["resume"]["courseId"] == "a"
    assert result["resume"]["action"] == "start"


@pytest.mark.asyncio
async def test_completed_scope_has_no_resume_and_is_not_treated_as_mastery(handler):
    for lesson in ("lesson_1", "lesson_2", "lesson_3"):
        save(handler, lesson=lesson, completed=True)
    save(handler, course="b")
    result = await call(handler, courseId="a")
    assert result["resume"] is None
    assert result["courses"][0]["progress"]["completionPercent"] == 100
    assert result["courses"][0]["progress"]["allCompleted"] is True
    assert dimensions(result)["knowledge"]["score"] is None
    for lesson in ("lesson_1", "lesson_2"):
        save(handler, course="b", lesson=lesson, completed=True)
    assert (await call(handler))["resume"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("course_id", ["missing", "../a", None, 42])
async def test_invalid_course_is_rejected_without_mutating_state(handler, course_id):
    before = handler.events.list_events()
    with pytest.raises(ValueError):
        await call(handler, courseId=course_id)
    assert handler.events.list_events() == before
    assert handler._runner is None


@pytest.mark.asyncio
async def test_refresh_is_read_only_and_does_not_expose_source_or_private_timestamps(handler):
    save(handler, updated="2026-01-01T11:12:13+00:00")
    assessment(handler, "a")
    progress_before = handler.progress.get_course_progress("a")
    events_before = handler.events.list_events()
    first = await call(handler)
    assert await call(handler) == first
    assert handler.progress.get_course_progress("a") == progress_before
    assert handler.events.list_events() == events_before
    serialized = json.dumps(first, ensure_ascii=False)
    for value in ("PRIVATE SOURCE", "2026-01-01", "updated_at", "updatedAt", "last_code", "lastCode", "timestamp"):
        assert value not in serialized
    assert handler._runner is None


@pytest.mark.asyncio
async def test_invalid_depth_is_not_offered_and_offset_times_are_compared(handler):
    save(handler, lesson="lesson_1", updated="2026-01-01T10:00:00+08:00")
    save(handler, lesson="lesson_2", updated="2026-01-01T03:00:00+00:00")
    save(handler, lesson="lesson_3", depth="unknown", updated="2026-01-02T00:00:00Z")
    result = await call(handler)
    assert result["resume"]["lessonId"] == "lesson_2"
    assert result["courses"][0]["lessons"][2]["status"] == "in_progress"
    assert any(warning["section"] == "progress" for warning in result["warnings"])


@pytest.mark.asyncio
async def test_practice_submission_does_not_replace_sequential_resume(handler):
    save(handler)
    await call(handler, "loadLesson", courseId="b", lessonIdx=0)
    handler._runner.open_practice_step("task")
    await call(handler, "submit", code="value = 1")
    result = await call(handler)
    assert result["resume"]["courseId"] == "a"
    assert handler.progress.get_course_progress("b") == []


@pytest.mark.asyncio
async def test_selected_transfer_uses_cross_course_source_evidence(handler):
    lesson_file = handler.registry.courses_dir / "b" / "lessons" / "lesson_1" / "lesson.yaml"
    steps = yaml.safe_load(lesson_file.read_text(encoding="utf-8"))
    source = {"courseId": "a", "lessonId": "lesson_1", "stepId": "task"}
    steps[2].update({"DiagnosisTargets": ["transfer", "knowledge"],
                     "Prerequisites": [source], "Transfer": {"sources": [source], "context": "b_lesson_1"}})
    lesson_file.write_text(yaml.safe_dump(steps, allow_unicode=True), encoding="utf-8")
    assessment(handler, "a", timestamp="2026-01-01T00:00:00Z")
    assessment(handler, "b", timestamp="2026-01-02T00:00:00Z")
    result = await call(handler, courseId="b")
    assert dimensions(result)["transfer"]["score"] == 100
    assert dimensions(result)["transfer"]["evidenceCount"] == 1
    assert dimensions(result)["knowledge"]["evidenceCount"] == 1
    item = dimensions(result)["transfer"]["evidence"]["items"][0]
    assert item["courseId"] == "b"
    assert item["taskId"] == "lesson_1:task"
    assert item["title"] == "任务"
    assert item["passed"] is True


@pytest.mark.asyncio
async def test_ai_and_dry_run_are_still_not_quantitative_evidence(handler):
    assessment(handler, "a", assessmentEligible=False, assessmentSource="ai_review")
    assessment(handler, "b", mode="dry_run")
    result = await call(handler)
    assert all(item["score"] is None for item in dimensions(result).values())


@pytest.mark.asyncio
async def test_missing_lesson_does_not_offer_a_broken_resume(handler):
    save(handler, lesson="lesson_3")
    (handler.registry.courses_dir / "a" / "lessons" / "lesson_3" / "lesson.yaml").unlink()
    result = await call(handler)
    assert result["resume"]["lessonId"] == "lesson_1"
    assert result["courses"][0]["lessonCount"] == 3
    assert result["courses"][0]["lessons"][2]["status"] == "unavailable"
    assert result["courses"][0]["lessons"][2]["available"] is False
    assert any(item["section"] == "catalog" for item in result["warnings"])


@pytest.mark.asyncio
async def test_diagnosis_failure_keeps_course_navigation_available_without_error_details(handler, monkeypatch):
    from sparktutor.engine import learning_dashboard

    def broken(*args, **kwargs):
        raise RuntimeError("private file path and source")

    monkeypatch.setattr(learning_dashboard, "build_diagnosis", broken)
    result = await call(handler)
    assert result["diagnosis"] is None
    assert result["knowledgeComponents"] == {}
    assert result["resume"] is not None
    assert result["courses"]
    assert "private file path" not in json.dumps(result)


@pytest.mark.asyncio
async def test_legacy_invalid_depth_does_not_erase_completion(handler):
    save(handler, completed=True, depth="old_level")
    result = await call(handler, courseId="a")
    course = result["courses"][0]
    assert course["lessons"][0]["status"] == "completed"
    assert course["lessons"][0]["depth"] == "beginner"
    assert course["progress"]["lessonsCompleted"] == 1
    assert result["resume"]["lessonId"] == "lesson_2"


@pytest.mark.asyncio
async def test_unavailable_lesson_keeps_denominator_and_prevents_false_completion(handler):
    for lesson in ("lesson_1", "lesson_2"):
        save(handler, lesson=lesson, completed=True)
    (handler.registry.courses_dir / "a" / "lessons" / "lesson_3" / "lesson.yaml").unlink()
    result = await call(handler, courseId="a")
    progress = result["courses"][0]["progress"]
    assert progress["totalLessons"] == 3
    assert progress["lessonsCompleted"] == 2
    assert progress["completionPercent"] == 66.7
    assert progress["allCompleted"] is False
    assert progress["unavailableLessons"] == 1
    assert result["resume"] is None


def help_event(handler, kind, course, lesson, day):
    handler.events.record(kind, course_id=course, lesson_id=lesson, task_id=f"{lesson}:task",
                          task_type="cmd_question", timestamp=f"2026-01-0{day}T00:00:00Z",
                          data={"available": True})


def assert_evidence_counts(report):
    metric_numerators = {"knowledge": "latestPassedTasks", "debugging": "confirmedRepairs",
                         "hint_dependency": "hintedBeforeFirstAssessment", "transfer": "firstPassedTasks"}
    allowed = {"courseId", "lessonId", "taskId", "stepId", "title", "outcome", "passed",
               "hinted", "assisted", "episodeIndex"}
    for key, dimension in dimensions(report).items():
        evidence = dimension["evidence"]
        assert evidence["totalCount"] == dimension["evidenceCount"]
        assert len(evidence["items"]) == evidence["totalCount"]
        numerator = sum(item.get("hinted" if key == "hint_dependency" else "passed", False)
                        for item in evidence["items"])
        assert numerator == dimension["metrics"][metric_numerators[key]]
        for item in evidence["items"]:
            assert set(item).issubset(allowed)


@pytest.mark.asyncio
async def test_evidence_matches_real_denominators_repair_episodes_and_answer_cutoffs(handler):
    assessment(handler, "a", passed=False)
    help_event(handler, "hint_request", "a", "lesson_1", 2)
    assessment(handler, "a", timestamp="2026-01-03T00:00:00Z")
    assessment(handler, "a", passed=False, timestamp="2026-01-06T00:00:00Z")
    # Failure before seeing an answer remains K evidence, but the active D
    # episode is excluded and the answer-assisted pass cannot replace it.
    assessment(handler, "a", "lesson_2", passed=False)
    help_event(handler, "solution_view", "a", "lesson_2", 2)
    assessment(handler, "a", "lesson_2", timestamp="2026-01-03T00:00:00Z")
    # A task seen only after its answer is excluded entirely from K and D.
    help_event(handler, "solution_view", "a", "lesson_3", 1)
    assessment(handler, "a", "lesson_3", timestamp="2026-01-03T00:00:00Z")
    report = await call(handler)
    assert_evidence_counts(report)
    d = dimensions(report)
    assert len(d["knowledge"]["evidence"]["items"]) == 2
    assert all(item["passed"] is False for item in d["knowledge"]["evidence"]["items"])
    assert [item["outcome"] for item in d["debugging"]["evidence"]["items"]] == ["repaired", "unresolved"]
    assert [item["episodeIndex"] for item in d["debugging"]["evidence"]["items"]] == [1, 2]
    assert all(item["taskId"] == "lesson_1:task" for item in d["debugging"]["evidence"]["items"])
    assert d["debugging"]["evidence"]["items"][0]["assisted"] is True
    # The hint was after first assessment, so H does not retroactively count it.
    assert all(item["hinted"] is False for item in d["hint_dependency"]["evidence"]["items"])


@pytest.mark.asyncio
async def test_transfer_evidence_keeps_first_result_and_excludes_answer_supported_target(handler):
    source = {"courseId": "a", "lessonId": "lesson_1", "stepId": "task"}
    for lesson_id in ("lesson_1", "lesson_2"):
        path = handler.registry.courses_dir / "b" / "lessons" / lesson_id / "lesson.yaml"
        steps = yaml.safe_load(path.read_text(encoding="utf-8"))
        steps[2]["Transfer"] = {"sources": [source], "context": f"b_{lesson_id}"}
        path.write_text(yaml.safe_dump(steps, allow_unicode=True), encoding="utf-8")
    assessment(handler, "a")
    help_event(handler, "hint_request", "b", "lesson_1", 2)
    assessment(handler, "b", passed=False, timestamp="2026-01-03T00:00:00Z")
    assessment(handler, "b", timestamp="2026-01-04T00:00:00Z")
    help_event(handler, "solution_view", "b", "lesson_2", 2)
    assessment(handler, "b", "lesson_2", timestamp="2026-01-03T00:00:00Z")
    report = await call(handler, courseId="b")
    assert_evidence_counts(report)
    d = dimensions(report)
    assert d["knowledge"]["score"] == 100
    assert d["transfer"]["score"] == 0
    assert d["transfer"]["evidence"]["items"] == [{
        "courseId": "b", "lessonId": "lesson_1", "taskId": "lesson_1:task", "stepId": "task",
        "title": "任务", "outcome": "failed", "passed": False, "assisted": True,
    }]
    assert d["hint_dependency"]["evidence"]["items"][0]["hinted"] is True


@pytest.mark.asyncio
async def test_evidence_display_limit_never_changes_score_or_full_sample_count(handler):
    for index in range(105):
        handler.events.record("code_submit", course_id="a", lesson_id="lesson_1",
                              task_id=f"lesson_1:task_{index}", task_type="cmd_question",
                              data={"passed": True, "assessmentEligible": True, "assessmentSource": "exact"})
    report = await call(handler)
    knowledge = dimensions(report)["knowledge"]
    assert knowledge["score"] == 100
    assert knowledge["evidenceCount"] == knowledge["evidence"]["totalCount"] == 105
    assert len(knowledge["evidence"]["items"]) == knowledge["evidence"]["limit"] == 100


@pytest.mark.asyncio
async def test_review_navigation_and_submission_preserve_completion_until_explicit_reset(handler):
    save(handler, completed=True)
    await call(handler, "loadLesson", courseId="a", lessonIdx=0, depth="advanced")
    await call(handler, "advance")
    assert handler.progress.get("a", "lesson_1").completed is True
    await call(handler, "goBack")
    assert handler.progress.get("a", "lesson_1").completed is True
    await call(handler, "advance")
    await call(handler, "submit", code="value = 1")
    saved = handler.progress.get("a", "lesson_1")
    assert saved.completed is True
    assert saved.last_code == "value = 1"
    assert saved.depth == "advanced"
    assert saved.current_step_id == "task"
    assert (await call(handler))["courses"][0]["progress"]["lessonsCompleted"] == 1

    await call(handler, "resetLesson", courseId="a", lessonId="lesson_1")
    await call(handler, "loadLesson", courseId="a", lessonIdx=0, depth="beginner")
    await call(handler, "advance")
    await call(handler, "submit", code="value = 1")
    assert handler.progress.get("a", "lesson_1").completed is False
    await call(handler, "advance")
    assert handler.progress.get("a", "lesson_1").completed is True


@pytest.mark.asyncio
async def test_opening_and_advancing_an_unfinished_lesson_does_not_mark_it_complete(handler):
    await call(handler, "loadLesson", courseId="a", lessonIdx=0)
    await call(handler, "advance")
    assert handler.progress.get("a", "lesson_1").completed is False
    assert (await call(handler))["courses"][0]["progress"]["lessonsCompleted"] == 0
