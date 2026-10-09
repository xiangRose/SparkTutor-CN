"""Bounded visualization must retain complete past evidence and causal limits."""

from datetime import datetime, timedelta, timezone

import pytest
import yaml

from sparktutor.courses.registry import CourseRegistry
from sparktutor.engine.learning_history import LearningHistory
from sparktutor.engine.learning_trend import build_learning_trend
from sparktutor.state.learning_events import LearningEventStore


@pytest.fixture
def history(tmp_path):
    courses = tmp_path / "courses"
    for course_id in ("a", "b"):
        directory = courses / course_id
        lesson = directory / "lessons" / "lesson"
        lesson.mkdir(parents=True)
        (directory / "course.yaml").write_text(yaml.safe_dump({"course": {
            "id": course_id, "title": f"课程{course_id}", "description": "测试课程",
            "version": "1", "lessons": ["lesson"],
        }}, allow_unicode=True), encoding="utf-8")
        task = {"Class": "cmd_question", "Id": "task", "Output": f"真实题目{course_id}",
                "CorrectAnswer": "value = 1", "KnowledgeComponents": ["dataframe.schema"],
                "Context": f"context_{course_id}"}
        if course_id == "b":
            task["Transfer"] = {"context": "context_b", "sources": [
                {"courseId": "a", "lessonId": "lesson", "stepId": "task"},
            ]}
        (lesson / "lesson.yaml").write_text(yaml.safe_dump([
            {"Class": "meta", "Lesson": f"课节{course_id}"}, task,
        ], allow_unicode=True), encoding="utf-8")
    return CourseRegistry(courses), LearningEventStore(tmp_path / "history.db")


def record(store, course, when, *, passed=True, kind="code_submit", task="lesson:task", **data):
    return store.record(kind, course_id=course, lesson_id="lesson", task_id=task,
                        task_type="cmd_question", timestamp=when,
                        data={"passed": passed, "assessmentSource": "exact", "assessmentEligible": True,
                              "failureKind": "" if passed else "learner", "mode": "local_check", **data})


def dimensions(point):
    return {dimension["key"]: dimension for dimension in point["dimensions"]}


def test_time_and_course_filter_retain_old_cross_course_source_and_do_not_write(history):
    registry, store = history
    record(store, "a", "2026-01-01T00:00:00Z")
    target = record(store, "b", "2026-01-10T00:00:00Z")
    before = store.list_events(limit=None, order="insertion")
    result = build_learning_trend(registry, store, course_id="b", window="7d",
                                  now=datetime(2026, 1, 12, tzinfo=timezone.utc))
    assert [point["eventId"] for point in result["points"]] == [target.event_id]
    point = dimensions(result["points"][0])
    assert point["knowledge"]["sampleSize"] == 1
    assert point["transfer"]["score"] == 100
    assert point["transfer"]["numerator"] == point["transfer"]["denominator"] == 1
    assert store.list_events(limit=None, order="insertion") == before
    assert "不直接证明能力变化" in result["disclaimer"]


def test_later_failure_does_not_rewrite_an_earlier_point_or_transfer_first_attempt(history):
    registry, store = history
    record(store, "a", "2026-01-01T00:00:00Z")
    passed = record(store, "b", "2026-01-02T00:00:00Z")
    failed = record(store, "b", "2026-01-03T00:00:00Z", passed=False)
    result = build_learning_trend(registry, store, course_id="b")
    assert [point["eventId"] for point in result["points"]] == [passed.event_id, failed.event_id]
    earlier, later = map(dimensions, result["points"])
    assert earlier["knowledge"]["score"] == 100
    assert later["knowledge"]["score"] == 0
    assert earlier["knowledge"]["sampleSize"] == later["knowledge"]["sampleSize"] == 1
    assert earlier["transfer"]["score"] == later["transfer"]["score"] == 100


def test_equal_utc_times_preserve_insertion_order_and_do_not_fill_lost_evidence_with_zero(history):
    registry, store = history
    record(store, "a", "2026-01-01T00:00:00Z")
    submitted = record(store, "b", "2026-01-02T08:00:00+08:00")
    answer = record(store, "b", "2026-01-02T00:00:00Z", kind="solution_view", available=True)
    result = build_learning_trend(registry, store, course_id="b")
    assert [point["eventId"] for point in result["points"]] == [submitted.event_id, answer.event_id]
    earlier, later = map(dimensions, result["points"])
    assert earlier["knowledge"]["score"] == earlier["transfer"]["score"] == 100
    assert later["knowledge"]["score"] is None
    assert later["transfer"]["score"] is None
    assert later["knowledge"]["sampleSize"] == 0
    assert earlier["hint_dependency"]["score"] == later["hint_dependency"]["score"] == 0


def test_fixed_insertion_snapshot_excludes_new_backdated_imports(history):
    registry, store = history
    record(store, "a", "2026-01-01T00:00:00Z")
    record(store, "b", "2026-01-02T00:00:00Z")
    last_inserted = record(store, "a", "2025-12-01T00:00:00Z", kind="hint_request", available=True)
    first = build_learning_trend(registry, store)
    assert first["snapshotEventId"] == last_inserted.event_id
    added = record(store, "b", "2026-01-01T12:00:00Z", passed=False)
    frozen = build_learning_trend(registry, store, snapshot_event_id=first["snapshotEventId"],
                                  window_end=first["windowEnd"])
    assert frozen == first
    refreshed = build_learning_trend(registry, store)
    assert added.event_id in [point["eventId"] for point in refreshed["points"]]
    assert refreshed["snapshotEventId"] == added.event_id


def test_displaying_last_twenty_points_does_not_reset_denominators_at_the_cutoff(history):
    registry, store = history
    anchor = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for index in range(28):
        record(store, "a", (anchor + timedelta(seconds=index)).isoformat(),
               passed=index % 2 == 0, task=f"lesson:task_{index}")
    record(store, "a", "2026-01-02T00:00:00Z", kind="code_edit",
           assessmentEligible=False, changeCount=2, addedChars=3, removedChars=1)
    result = build_learning_trend(registry, store, course_id="a")
    assert result["total"] == 28
    assert len(result["points"]) == result["limit"] == 20
    assert result["hasMore"] is True
    assert dimensions(result["points"][0])["knowledge"]["sampleSize"] == 9
    latest = dimensions(result["points"][-1])["knowledge"]
    assert latest["sampleSize"] == latest["denominator"] == 28
    assert latest["numerator"] == 14
    assert latest["score"] == 50


def test_unverified_results_successful_runs_and_edits_do_not_create_trend_points(history):
    registry, store = history
    record(store, "a", "2026-01-01T00:00:00Z", assessmentSource="ai_review", assessmentEligible=False)
    record(store, "a", "2026-01-02T00:00:00Z", mode="dry_run")
    record(store, "a", "2026-01-03T00:00:00Z", passed=False, failureKind="infrastructure")
    record(store, "a", "2026-01-04T00:00:00Z", kind="code_run", exitCode=0)
    record(store, "a", "2026-01-05T00:00:00Z", kind="code_edit", changeCount=1)
    result = build_learning_trend(registry, store)
    assert result["points"] == []
    assert result["total"] == 0
    assert result["hasMore"] is False


def test_a_review_from_a_global_trend_point_uses_the_same_scope_and_snapshot(history):
    registry, store = history
    record(store, "a", "2026-01-01T00:00:00Z")
    record(store, "b", "2026-01-02T00:00:00Z", passed=False)
    trend = build_learning_trend(registry, store)
    point = trend["points"][-1]
    context = LearningHistory(registry, store, snapshot_event_id=trend["snapshotEventId"])
    review = context.get_review(point["courseId"], point["lessonId"], point["taskId"], point["eventId"],
                                 scope_course_id="")
    expected = {dimension["key"]: dimension for dimension in review["after"]["dimensions"]}
    for actual in point["dimensions"]:
        for field in ("score", "sampleSize", "numerator", "denominator", "direction"):
            assert actual[field] == expected[actual["key"]][field]
    assert dimensions(point)["knowledge"]["score"] == 50


def test_window_end_keeps_boundary_records_in_list_and_trend_when_the_clock_moves(history):
    registry, store = history
    anchor = datetime(2026, 1, 8, tzinfo=timezone.utc)
    boundary = record(store, "a", "2026-01-01T00:00:00Z", task="lesson:boundary")
    record(store, "a", "2026-01-02T00:00:00Z")
    context = LearningHistory(registry, store)
    first = context.get_history(course_id="a", window="7d", limit=1, now=anchor)
    later = anchor + timedelta(seconds=2)
    second = context.get_history(course_id="a", window="7d", offset=1, limit=1,
                                  window_end=first["windowEnd"], now=later)
    assert first["total"] == second["total"] == 2
    assert second["tasks"][0]["taskId"] == "lesson:boundary"
    trend = build_learning_trend(registry, store, course_id="a", window="7d", now=later,
                                  snapshot_event_id=first["snapshotEventId"], window_end=first["windowEnd"])
    assert trend["total"] == 2
    assert trend["points"][0]["eventId"] == boundary.event_id
    refreshed = build_learning_trend(registry, store, course_id="a", window="7d", now=later)
    assert refreshed["total"] == 1


@pytest.mark.parametrize("kwargs", [{"window": "weekly"}, {"course_id": None},
                                    {"course_id": "absent"}, {"snapshot_event_id": "absent"},
                                    {"window_end": "not-a-date"}])
def test_invalid_filters_do_not_mutate_history(history, kwargs):
    registry, store = history
    record(store, "a", "2026-01-01T00:00:00Z")
    before = store.list_events(limit=None, order="insertion")
    with pytest.raises(ValueError):
        build_learning_trend(registry, store, **kwargs)
    assert store.list_events(limit=None, order="insertion") == before
