"""Evidence eligibility, temporal ordering and real-catalog recommendations."""

from dataclasses import replace

import pytest

from sparktutor.engine.diagnosis import build_diagnosis
from sparktutor.state.learning_events import LearningEvent, LearningEventStore


def event(kind="code_submit", *, task="1", second=1, course="course", lesson="lesson",
          session="session", task_type="cmd_question", event_id=None, **data):
    payload = {"assessmentEligible": True, "assessmentSource": "exact", "passed": True}
    if kind != "code_submit":
        payload = {}
    payload.update(data)
    if payload.get("passed") is False:
        payload.setdefault("failureKind", "learner")
    return LearningEvent(
        event_type=kind, course_id=course, lesson_id=lesson, task_id=f"{lesson}:{task}",
        task_type=task_type, session_id=session, data=payload,
        timestamp=f"2026-01-01T00:00:{second:02d}+00:00",
        event_id=event_id or f"{kind}-{course}-{lesson}-{task}-{second}-{session}",
    )


def spec(step="1", *, course="course", lesson="lesson", context="base", **kwargs):
    result = {
        "courseId": course, "lessonId": lesson, "stepId": step, "taskId": f"{lesson}:{step}",
        "lessonIndex": 0, "title": f"真实练习{step}", "taskType": "cmd_question",
        "knowledgeComponents": ["filter"], "targetDimensions": ["knowledge"],
        "prerequisites": [], "depth": "all", "context": context, "transfer": {},
    }
    result.update(kwargs)
    return result


def ref(step="1", course="course", lesson="lesson"):
    return {"courseId": course, "lessonId": lesson, "stepId": step}


def dimension(result, key):
    return next(item for item in result["dimensions"] if item["key"] == key)


def score(events, key="knowledge", catalog=()):
    return dimension(build_diagnosis(events, catalog), key)


def test_empty_history_is_insufficient_but_can_recommend_a_real_initial_exercise():
    result = build_diagnosis([], [spec()])
    assert {item["key"] for item in result["dimensions"]} == {
        "knowledge", "debugging", "hint_dependency", "transfer",
    }
    assert all(item["score"] is None and item["evidenceCount"] == 0 for item in result["dimensions"])
    assert all(item["confidence"] == "none" for item in result["dimensions"])
    assert "证据不足" in result["diagnosis"]
    assert result["recommendedExercise"]["stepId"] == "1"
    assert build_diagnosis([])["recommendedExercise"] is None


@pytest.mark.parametrize("replacement", [
    {"assessmentEligible": None}, {"assessmentEligible": False}, {"assessmentEligible": 1},
    {"assessmentSource": "ai_review"}, {"assessmentSource": "syntax"},
    {"mode": "dry_run"}, {"executionMode": "dry_run"},
    {"failureKind": "infrastructure"}, {"failureKind": "ai_failure"},
    {"failureKind": "dry_run"}, {"passed": "true"}, {"passed": 1},
])
def test_non_assessment_or_unreliable_results_never_produce_scores(replacement):
    result = build_diagnosis([event(**replacement)])
    assert all(item["score"] is None for item in result["dimensions"])
    assert result["eligibleEventCount"] == 0


def test_legacy_logs_reading_and_successful_runs_do_not_claim_knowledge():
    legacy = event()
    legacy.data.pop("assessmentEligible")
    results = [legacy, event(task_type="text"), event("task_complete", passed=True),
               event("code_run", exitCode=0, mode="local", assessmentEligible=True)]
    assert score(results)["score"] is None


def test_raw_event_count_includes_session_events_without_treating_them_as_assessments():
    session = LearningEvent(event_type="session_start", timestamp="2026-01-01T00:00:00Z", event_id="session")
    result = build_diagnosis([session])
    assert result["eventCount"] == 1
    assert result["eligibleEventCount"] == 0
    assert all(item["score"] is None for item in result["dimensions"])


def test_unique_task_latest_assessment_is_not_overweighted_by_repeated_submissions():
    events = [event(passed=False), event(second=2), event(task="2", second=3, passed=False)]
    baseline = score(events)
    repeated = score(events + [event(second=n) for n in range(4, 40)])
    assert baseline["score"] == repeated["score"] == 50
    assert baseline["evidenceCount"] == repeated["evidenceCount"] == 2
    assert baseline["metrics"]["firstAttemptPassRate"] == 0


def test_last_failed_assessment_is_not_hidden_by_an_earlier_pass():
    assert score([event(), event(second=2, passed=False)])["score"] == 0


def test_hint_assistance_does_not_reduce_knowledge_score():
    result = score([event("hint_request"), event(second=2)])
    assert result["score"] == 100
    assert result["metrics"]["assistedTasks"] == 1
    assert result["metrics"]["independentPassRate"] is None


def test_viewing_solution_cannot_manufacture_knowledge_and_keeps_prior_evidence():
    assert score([event("solution_view"), event(second=2)])["score"] is None
    result = score([event(passed=False), event("solution_view", second=2), event(second=3)])
    assert result["score"] == 0
    assert result["metrics"]["answerSupportedTasks"] == 1
    result = score([event(), event("solution_view", second=2)])
    assert result["score"] == 100
    assert result["metrics"]["independentPassRate"] == 100


def test_task_identity_includes_course_and_lesson_but_not_session():
    results = [event(course="a", passed=False), event(course="a", second=2, session="new"),
               event(course="b", second=3, passed=False),
               event(course="a", lesson="other", second=4, passed=False)]
    result = score(results)
    assert result["evidenceCount"] == 3
    assert result["score"] == 33.3


def test_events_are_chronological_deduplicated_and_timezone_aware():
    first = event(passed=False)
    latest = replace(event(second=2), timestamp="2026-01-01T08:00:02+08:00")
    result = build_diagnosis([latest, first, first])
    assert dimension(result, "knowledge")["score"] == 100
    assert result["eventCount"] == 2
    assert result["dataQuality"]["duplicateEvents"] == 1
    assert result == build_diagnosis([first, latest, first])


def test_conflicting_event_ids_and_invalid_timestamps_are_not_evidence():
    first = event()
    conflicting = replace(first, data={**first.data, "passed": False})
    invalid = replace(event(second=2), timestamp="not-a-date")
    result = build_diagnosis([first, conflicting, invalid])
    assert dimension(result, "knowledge")["score"] is None
    assert result["dataQuality"]["conflictingEventIds"] == 1
    assert result["dataQuality"]["invalidTimestamps"] == 1


def test_equal_timestamps_preserve_store_order_instead_of_sorting_random_ids():
    earlier = event(event_id="z-earlier", passed=False)
    later = event(event_id="a-later")
    result = score([earlier, later])
    assert result["score"] == 100
    assert result["metrics"]["firstAttemptPassRate"] == 0


def test_same_timestamp_help_is_conservatively_assisted_regardless_of_input_order():
    submit = event(event_id="a-submit")
    hint = event("hint_request", event_id="z-hint")
    for history in ([submit, hint], [hint, submit]):
        result = build_diagnosis(history)
        assert dimension(result, "knowledge")["metrics"]["independentTasks"] == 0
        assert dimension(result, "hint_dependency")["score"] == 100
    answer = event("solution_view", event_id="z-answer")
    assert score([submit, answer])["score"] is None


def test_hint_coverage_has_one_denominator_and_ignores_unassessed_tasks():
    events = [event(), event("hint_request", task="unassessed", second=2),
              event("hint_request", task="unassessed", second=3)]
    result = score(events, "hint_dependency")
    assert result["score"] == 0
    assert result["metrics"]["evaluatedTasks"] == 1
    assert result["metrics"]["hintRequests"] == 0


def test_hint_after_first_assessment_is_reported_without_rewriting_first_coverage():
    events = [event(passed=False), event("hint_request", second=2), event(second=3)]
    result = score(events, "hint_dependency")
    assert result["score"] == 0
    assert result["direction"] == "descriptive"
    assert result["metrics"]["laterHintedTasks"] == 1
    assert result["metrics"]["hintProductivity"] == 100


def test_hint_flags_and_repeated_hints_stay_within_zero_to_one_hundred():
    events = [event("hint_request"), event("hint_request", second=2), event(second=3),
              event(task="2", second=4, hintUsed=True)]
    result = score(events, "hint_dependency")
    assert result["score"] == 100
    assert result["metrics"]["hintedBeforeFirstAssessment"] == 2
    assert score([event("hint_request", available=False), event(second=2)], "hint_dependency")["score"] == 0


def test_debugging_requires_confirmed_assessment_and_supports_cross_session_repairs():
    events = [event("code_run", assessmentEligible=True, failureKind="learner", exitCode=1,
                    mode="local", errorType="TypeError"),
              event("code_run", second=2, exitCode=0, assessmentEligible=True, mode="local")]
    assert score(events, "debugging")["score"] == 0
    result = score(events + [event(second=3, session="new")], "debugging")
    assert result["score"] == 100
    assert result["metrics"]["failureEpisodes"] == 1
    assert result["metrics"]["independentRepairs"] == 1


def test_debugging_counts_repeated_failures_once_and_accepts_hint_assisted_repairs():
    events = [event(passed=False), event(second=2, passed=False),
              event("hint_request", second=3), event(second=4)]
    result = score(events, "debugging")
    assert result["score"] == 100
    assert result["metrics"]["failureEpisodes"] == 1
    assert result["metrics"]["assistedRepairs"] == 1
    assert result["metrics"]["independentRepairs"] == 0


def test_hints_before_first_failed_run_are_not_misreported_as_independent_repair():
    events = [event("hint_request"), event("code_run", second=2, assessmentEligible=True,
              failureKind="learner", exitCode=1, mode="local"), event(second=3)]
    result = score(events, "debugging")
    assert result["score"] == 100
    assert result["metrics"]["assistedRepairs"] == 1
    assert result["metrics"]["independentRepairs"] == 0


def test_debugging_excludes_infrastructure_dry_runs_ai_and_quiz_failures():
    events = [event(passed=False, failureKind="infrastructure"),
              event(task="2", second=2, passed=False, assessmentSource="ai_review"),
              event("code_run", task="3", second=3, assessmentEligible=True,
                    failureKind="learner", exitCode=1, mode="dry_run"),
              event(task="4", second=4, task_type="mult_question", passed=False)]
    assert score(events, "debugging")["score"] is None


def test_real_syntax_error_is_only_debugging_evidence_until_assessed_repair():
    failed = event(passed=False, assessmentSource="syntax", mode="dry_run", errorTypes=["SyntaxError"])
    result = build_diagnosis([failed])
    assert dimension(result, "knowledge")["score"] is None
    assert dimension(result, "debugging")["score"] == 0
    assert result["eligibleEventCount"] == 1
    result = build_diagnosis([failed, event(second=2, assessmentSource="syntax")])
    assert dimension(result, "debugging")["score"] == 0
    result = build_diagnosis([failed, event(second=3)])
    assert dimension(result, "debugging")["score"] == 100


def test_answer_contaminated_repair_episode_is_excluded_and_reported():
    result = score([event(passed=False), event("solution_view", second=2), event(second=3)], "debugging")
    assert result["score"] is None
    assert result["metrics"]["excludedAnswerEpisodes"] == 1


def test_same_timestamp_help_is_conservative_for_debugging_repairs():
    history = [event(passed=False), event(second=2), event("hint_request", second=2)]
    result = score(history, "debugging")
    assert result["metrics"]["assistedRepairs"] == 1
    assert result["metrics"]["independentRepairs"] == 0
    history[-1] = event("solution_view", second=2)
    result = score(history, "debugging")
    assert result["score"] is None
    assert result["metrics"]["excludedAnswerEpisodes"] == 1


def transfer_catalog():
    return [spec(), spec("2", context="novel", targetDimensions=["transfer"],
                         transfer={"sources": [ref()], "context": "novel"})]


def test_transfer_requires_prior_source_pass_and_first_novel_assessment():
    result = score([event(), event(task="2", second=2, passed=False), event(task="2", second=3)],
                   "transfer", transfer_catalog())
    assert result["score"] == 0
    assert result["evidenceCount"] == 1


def test_transfer_accepts_hints_but_reports_independent_performance():
    events = [event(), event("hint_request", task="2", second=2), event(task="2", second=3)]
    result = score(events, "transfer", transfer_catalog())
    assert result["score"] == 100
    assert result["metrics"]["independentTasks"] == 0
    assert result["metrics"]["independentPassRate"] is None


def test_same_timestamp_source_success_does_not_prove_prior_mastery():
    source = event(event_id="a-source")
    target = event(task="2", event_id="z-target")
    for history in ([source, target], [target, source]):
        assert score(history, "transfer", transfer_catalog())["score"] is None


def test_simultaneous_matching_contexts_do_not_fabricate_two_novel_exposures():
    catalog = transfer_catalog() + [spec("3", context="novel", transfer={"sources": [ref()], "context": "novel"})]
    events = [event(), event(task="2", second=2), event(task="3", second=2)]
    assert score(events, "transfer", catalog)["score"] is None


@pytest.mark.parametrize("events,catalog", [
    ([event(task="2"), event(second=2)], transfer_catalog()),
    ([event(passed=False), event(task="2", second=2)], transfer_catalog()),
    ([event(), event("solution_view", task="2", second=2), event(task="2", second=3)], transfer_catalog()),
    ([event(), event(task="2", second=2, isTransfer=True)], [spec(), spec("2")]),
    ([event(), event(task="2", second=2)], [spec(), spec("2", transfer={"sources": [ref()], "context": "base"})]),
    ([event(), event(task="2", second=2)], [spec(), spec("2", transfer={"sources": [ref("missing")], "context": "new"})]),
])
def test_insufficient_transfer_evidence_stays_null(events, catalog):
    assert score(events, "transfer", catalog)["score"] is None


def test_previously_encountered_context_is_not_new_transfer_evidence():
    catalog = transfer_catalog() + [spec("3", context="novel")]
    events = [event(), event(task="3", second=2), event(task="2", second=3)]
    assert score(events, "transfer", catalog)["score"] is None


def test_transfer_requires_shared_knowledge_components_with_its_sources():
    catalog = transfer_catalog()
    catalog[0]["knowledgeComponents"] = ["unrelated-concept"]
    assert score([event(), event(task="2", second=2)], "transfer", catalog)["score"] is None


def test_recommendation_obeys_scope_depth_prerequisites_and_completion():
    catalog = [spec("1"), spec("2", prerequisites=[ref("1")]),
               spec("3", depth="advanced"), spec("4", course="other")]
    result = build_diagnosis([], catalog, course_id="course")
    assert result["recommendedExercise"]["stepId"] == "1"
    result = build_diagnosis([event()], catalog, course_id="course")
    assert result["recommendedExercise"]["stepId"] == "2"
    result = build_diagnosis([event(), event(task="2", second=2)], catalog, course_id="course")
    assert result["recommendedExercise"] is None
    assert result["recommendationReason"]


def test_cross_course_sources_remain_available_to_a_scoped_report():
    catalog = [spec(course="source"), spec(course="target", context="new", prerequisites=[ref(course="source")],
                                          transfer={"sources": [ref(course="source")], "context": "new"})]
    history = [event(course="source")]
    result = build_diagnosis(history, catalog, course_id="target")
    assert result["recommendedExercise"]["courseId"] == "target"
    assert dimension(result, "knowledge")["score"] is None
    result = build_diagnosis(history + [event(course="target", second=2)], catalog, course_id="target")
    assert dimension(result, "transfer")["score"] == 100


def test_hint_dependency_is_never_selected_as_a_low_ability_dimension():
    result = build_diagnosis([event()], [spec(), spec("2")])
    assert result["recommendedExercise"]["dimension"] != "hint_dependency"
    assert "最弱" not in result["diagnosis"] and "优势" not in result["diagnosis"]


def test_recommendation_targets_an_observed_gap_instead_of_an_unrelated_first_task():
    catalog = [spec("unrelated", knowledgeComponents=["join"]), spec("1", knowledgeComponents=["filter"])]
    result = build_diagnosis([event(passed=False)], catalog)
    assert result["recommendedExercise"]["stepId"] == "1"


def test_perfect_measured_results_prioritize_missing_eligible_transfer_evidence():
    catalog = [spec(), spec("knowledge-next")] + transfer_catalog()[1:]
    result = build_diagnosis([event()], catalog)
    assert result["recommendedExercise"]["stepId"] == "2"
    assert result["recommendedExercise"]["dimension"] == "transfer"
    assert "已有来源题目通过" in result["diagnosis"]
    assert "新情境迁移" in result["diagnosis"]


def test_missing_debugging_evidence_is_sampled_after_no_eligible_transfer():
    catalog = [spec(), spec("knowledge-next"), spec("debug-next", targetDimensions=["debugging"])]
    result = build_diagnosis([event()], catalog)
    assert result["recommendedExercise"]["stepId"] == "debug-next"
    assert result["recommendedExercise"]["dimension"] == "debugging"
    assert "补充" in result["diagnosis"]


def test_known_knowledge_failure_is_consolidated_before_sampling_transfer():
    catalog = transfer_catalog() + [spec("failed")]
    result = build_diagnosis([event(), event(task="failed", second=2, passed=False)], catalog)
    assert result["recommendedExercise"]["stepId"] == "failed"
    assert result["recommendedExercise"]["dimension"] == "knowledge"


@pytest.mark.parametrize("invalidity", ["same-context", "different-components", "encountered-context"])
def test_transfer_recommendation_requires_same_evidence_design_as_transfer_scoring(invalidity):
    catalog = transfer_catalog()
    history = [event()]
    if invalidity == "same-context":
        catalog[1]["transfer"]["context"] = "base"
    elif invalidity == "different-components":
        catalog[1]["knowledgeComponents"] = ["unrelated"]
    else:
        catalog.append(spec("previous", context="novel"))
        history.append(event(task="previous", second=2))
    result = build_diagnosis(history, catalog)
    assert result["recommendedExercise"]["dimension"] == "knowledge"
    assert "检验新情境迁移" not in result["diagnosis"]


def test_transfer_reattempt_is_only_knowledge_consolidation():
    history = [event(), event(task="2", second=2, passed=False)]
    result = build_diagnosis(history, transfer_catalog())
    assert result["recommendedExercise"]["stepId"] == "2"
    assert result["recommendedExercise"]["dimension"] == "knowledge"
    assert "不作为新的迁移证据" in result["recommendedExercise"]["reason"]
    assert "重做" in result["diagnosis"]


def test_engine_consumes_persisted_tracker_events_without_mutating_them(tmp_path):
    store = LearningEventStore(tmp_path / "events.db")
    source = event()
    store.record(source.event_type, course_id=source.course_id, lesson_id=source.lesson_id,
                 task_id=source.task_id, task_type=source.task_type, session_id=source.session_id,
                 data=source.data, timestamp=source.timestamp)
    before = [item.as_dict() for item in store.list_events()]
    result = build_diagnosis(store.list_events(), [spec()])
    assert dimension(result, "knowledge")["score"] == 100
    assert result["knowledgeComponents"]["filter"] == {"evaluatedTasks": 1, "latestPassedTasks": 1}
    assert before == [item.as_dict() for item in store.list_events()]
