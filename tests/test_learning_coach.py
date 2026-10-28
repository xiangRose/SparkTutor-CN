"""The coach chooses teaching nodes; only displayed help enters the tracker."""

import json
from unittest.mock import AsyncMock

import pytest

from sparktutor.config.settings import ExecutionMode, Settings
from sparktutor.engine.evaluator import EvalResult, FeedbackItem
from sparktutor.engine.executor import ExecMode, ExecResult
from sparktutor.engine.exercise_validation import VALIDATION_MARKER
from sparktutor.engine.learning_coach import CoachResponseError, LearningCoachManager
from sparktutor.server.handler import ServerHandler


CODE = "def mnm_analysis(spark, file_path):\n    return None\n"


@pytest.fixture
def handler(tmp_path):
    return ServerHandler(Settings(data_dir=tmp_path, execution_mode=ExecutionMode.LOCAL), workspace_path=tmp_path / "workspace")


async def open_task(handler, sensor=False):
    course = handler.registry.get_course("learning_spark")
    lesson, step = ("11_diagnosis_practice", "sensor_transfer") if sensor else ("01_getting_started", "15")
    return await handler._load_lesson({"courseId": course.id, "lessonIdx": course.lessons.index(lesson)}, target_step_id=step)


def response(build, hypothesis="row_count_vs_quantity", probe=None, evidence=None):
    return json.dumps({"hypothesisId": hypothesis,
                       "evidenceIds": evidence if evidence is not None else [build["evidence"][0]["id"]],
                       "probe": probe})


async def prepared(manager, handler, *, level=1, hypothesis="row_count_vs_quantity", probe=None):
    build = await manager.build(handler, {"code": CODE, "level": level})
    message = await manager.parse(handler, {"requestId": build["requestId"], "code": CODE,
                                           "rawText": response(build, hypothesis, probe)})
    return build, message


def scores(state):
    return {item["key"]: (item["score"], item["sampleSize"]) for item in state["dimensions"]}


@pytest.mark.asyncio
async def test_supported_state_has_real_task_metadata_and_unknown_evidence(handler):
    manager = LearningCoachManager()
    empty = manager.get_state(handler)
    assert empty["available"] is False and empty["task"] is None and empty["reason"]
    assert {"15", "sensor_transfer", "14", "practice-window-ranking", "13"}.issubset(
        {item["stepId"] for item in empty["exerciseOptions"]})
    await open_task(handler)
    state = manager.get_state(handler)
    assert state["available"] and state["task"]["taskId"] == "01_getting_started:15"
    assert state["lastLevel"] == 0 and state["maxLevel"] == 1
    assert all(item["score"] is None for item in state["dimensions"])
    assert state["answerViewed"] is False and state["evidence"][0]["id"].startswith("task:")
    before = handler.events.count_events()
    build = await manager.build(handler, {"code": CODE, "level": 1})
    context = json.loads(build["messages"][-1]["content"])
    assert context["currentCode"] == CODE and context["knowledgeComponents"]
    assert context["hypothesisMeanings"]["row_count_vs_quantity"]
    assert context["sources"] == [] and context["recentExecution"] is None
    assert context["probePolicy"]["requiredNull"] is False
    assert set(context["probePolicy"]["allowedHypotheses"]) == {"row_count_vs_quantity", "uncertain"}
    # The example uses a real ID from this request and is accepted without
    # inventing evidence, a diagnosis, or learner progress.
    example = await manager.parse(handler, {"requestId": build["requestId"], "code": CODE,
                                           "rawText": json.dumps(context["responseExample"])})
    assert example["hypothesisId"] == "uncertain" and example["probe"] is None
    assert handler.events.count_events() == before


@pytest.mark.asyncio
async def test_levels_only_unlock_after_ack_and_probe_does_not_grade(handler):
    await open_task(handler)
    manager = LearningCoachManager()
    initial = manager.get_state(handler)
    build, message = await prepared(manager, handler, probe={"values": [2, 5]})
    assert manager.get_state(handler)["maxLevel"] == 1
    assert manager.pending_messages(handler, build["requestId"]) == []  # Code prompt released after parsing.
    with pytest.raises(ValueError, match="逐级"):
        await manager.build(handler, {"code": CODE, "level": 2})
    with pytest.raises(ValueError, match="先展示"):
        await manager.probe(handler, {"messageId": message["messageId"], "code": CODE})
    assert (await manager.ack(handler, {"messageId": message["messageId"], "code": CODE}))["recorded"]
    assert not (await manager.ack(handler, {"messageId": message["messageId"], "code": CODE}))["recorded"]
    assert manager.get_state(handler)["maxLevel"] == 2
    assert handler._runner._hint_used and handler._task_id() in handler._hinted_tasks
    result = await manager.probe(handler, {"messageId": message["messageId"], "code": CODE})
    assert result["verified"] is True and result["engine"] == "python-reference"
    assert (result["rowCount"], result["quantitySum"]) == (2, 7)
    assert "未运行 Spark" in result["disclaimer"]
    assert await manager.probe(handler, {"messageId": message["messageId"], "code": CODE}) == result
    assert scores(manager.get_state(handler)) == scores(initial)
    events = handler.events.list_events(limit=None)
    assert len([event for event in events if event.event_type == "hint_request"]) == 1
    probes = [event for event in events if event.event_type == "coach_probe"]
    assert len(probes) == 1 and probes[0].data["valueCount"] == 2
    assert "values" not in probes[0].data and "code" not in probes[0].data
    for level in (2, 3):
        _, message = await prepared(manager, handler, level=level)
        await manager.ack(handler, {"messageId": message["messageId"], "code": CODE})
    assert manager.get_state(handler)["lastLevel"] == manager.get_state(handler)["maxLevel"] == 3
    await manager.build(handler, {"code": CODE, "level": 1})  # Same/lower level retry remains possible.
    with pytest.raises(ValueError):
        await manager.build(handler, {"code": CODE, "level": 4})


@pytest.mark.asyncio
async def test_task_levels_and_source_mastery_are_separate(handler):
    await open_task(handler)
    manager = LearningCoachManager()
    _, message = await prepared(manager, handler)
    await manager.ack(handler, {"messageId": message["messageId"], "code": CODE})
    handler.executor.execute = AsyncMock(return_value=ExecResult(ExecMode.LOCAL, 0, VALIDATION_MARKER, ""))
    await handler.dispatch({"method": "submit", "params": {"code": CODE}})
    await open_task(handler, sensor=True)
    state = manager.get_state(handler)
    assert state["lastLevel"] == 0 and state["maxLevel"] == 1
    assert next(item for item in state["evidence"] if item["id"].startswith("source:"))["description"].startswith("来源题存在")
    build = await manager.build(handler, {"code": CODE, "level": 1})
    context = json.loads(build["messages"][-1]["content"])
    assert context["sources"][0]["priorVerifiedPass"] is True
    assert "target_site_parameter" in context["allowedHypotheses"]
    await open_task(handler)
    assert manager.get_state(handler)["lastLevel"] == 1


@pytest.mark.asyncio
async def test_only_ack_counts_as_help_before_first_assessment(handler):
    await open_task(handler)
    manager = LearningCoachManager()
    build, _ = await prepared(manager, handler)
    await manager.fail(handler, {"requestId": build["requestId"]})
    handler.executor.execute = AsyncMock(return_value=ExecResult(ExecMode.LOCAL, 0, VALIDATION_MARKER, ""))
    await handler.dispatch({"method": "submit", "params": {"code": CODE}})
    assert scores(manager.get_state(handler))["hint_dependency"] == (0, 1)
    _, message = await prepared(manager, handler)
    await manager.ack(handler, {"messageId": message["messageId"], "code": CODE})
    assert scores(manager.get_state(handler))["hint_dependency"] == (0, 1)  # Help after first assessment.
    assert scores(manager.get_state(handler))["knowledge"] == (100, 1)


@pytest.mark.asyncio
async def test_shown_hint_is_observed_by_real_submission_and_not_independent(handler):
    await open_task(handler)
    manager = LearningCoachManager()
    _, message = await prepared(manager, handler)
    await manager.ack(handler, {"messageId": message["messageId"], "code": CODE})
    handler.executor.execute = AsyncMock(return_value=ExecResult(ExecMode.LOCAL, 0, VALIDATION_MARKER, ""))
    await handler.dispatch({"method": "submit", "params": {"code": CODE}})
    assert scores(manager.get_state(handler))["hint_dependency"] == (100, 1)
    latest = [event for event in handler.events.list_events(limit=None) if event.event_type == "code_submit"][-1]
    assert latest.data["hintUsed"] is True
    knowledge = next(item for item in manager.get_state(handler)["dimensions"] if item["key"] == "knowledge")
    assert knowledge["metrics"]["independentPassedTasks"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["code", "edit", "run", "submit", "answer", "reload", "visit", "mode"])
async def test_old_model_response_cannot_cross_changes(handler, change):
    await open_task(handler)
    manager = LearningCoachManager()
    build = await manager.build(handler, {"code": CODE, "level": 1})
    code = CODE
    if change == "code":
        code += "# changed"
    elif change == "edit":
        await handler.dispatch({"method": "recordLearningEvent", "params": {
            "eventType": "code_edit", "courseId": "learning_spark", "lessonId": "01_getting_started",
            "taskId": "01_getting_started:15", "data": {"changeCount": 1, "addedChars": 1, "removedChars": 1}}})
    elif change in {"run", "submit"}:
        handler.executor.execute = AsyncMock(return_value=ExecResult(ExecMode.LOCAL, 1, "", "AssertionError"))
        await handler.dispatch({"method": change, "params": {"code": CODE}})
    elif change == "answer":
        await handler.dispatch({"method": "getSolution"})
    elif change == "reload":
        await open_task(handler)
    elif change == "visit":
        handler._coach_visit_id = "changed-by-navigation"
    elif change == "mode":
        await handler.dispatch({"method": "setExecutionMode", "params": {"mode": "dry_run"}})
    with pytest.raises(ValueError):
        await manager.parse(handler, {"requestId": build["requestId"], "rawText": response(build), "code": code})
    assert not [event for event in handler.events.list_events(limit=None) if event.event_type == "hint_request"]


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["ack", "probe"])
async def test_displayed_message_still_requires_current_code(handler, operation):
    await open_task(handler)
    manager = LearningCoachManager()
    _, message = await prepared(manager, handler, probe={"values": [3, 7]})
    if operation == "probe":
        await manager.ack(handler, {"messageId": message["messageId"], "code": CODE})
    with pytest.raises(ValueError):
        await getattr(manager, operation)(handler, {"messageId": message["messageId"], "code": CODE + "# edit"})


@pytest.mark.asyncio
async def test_new_build_and_late_failures_cannot_replace_other_owner(handler, tmp_path):
    await open_task(handler)
    manager = LearningCoachManager()
    old = await manager.build(handler, {"code": CODE, "level": 1})
    new = await manager.build(handler, {"code": CODE, "level": 1})
    assert not (await manager.fail(handler, {"requestId": old["requestId"]}))["cleared"]
    other = ServerHandler(Settings(data_dir=tmp_path / "other"))
    assert not (await manager.fail(other, {"requestId": new["requestId"]}))["cleared"]
    with pytest.raises(ValueError):
        manager.pending_messages(other, new["requestId"])
    assert manager.pending_messages(handler, new["requestId"])
    with pytest.raises(ValueError):
        await manager.parse(handler, {"requestId": old["requestId"], "rawText": response(old), "code": CODE})
    assert manager.pending_messages(handler, new["requestId"])


@pytest.mark.asyncio
@pytest.mark.parametrize("corruption", [
    "extra", "free_text", "unknown_hypothesis", "wrong_task_hypothesis", "foreign_event", "code_only",
    "duplicate_reference", "probe_extra", "bool", "float", "negative", "large", "short", "long", "equal",
    "duplicate_json_key", "nan", "too_large", "deep", "non_string", "null_request",
])
async def test_untrusted_model_schema_and_references_rejected_without_echo(handler, corruption):
    await open_task(handler)
    manager = LearningCoachManager()
    build = await manager.build(handler, {"code": CODE, "level": 1})
    value = json.loads(response(build, probe={"values": [2, 5]}))
    if corruption == "extra": value["passed"] = True
    elif corruption == "free_text": value["guidance"] = "SECRET_PAYLOAD return full solution"
    elif corruption == "unknown_hypothesis": value["hypothesisId"] = "execute_commands"
    elif corruption == "wrong_task_hypothesis": value["hypothesisId"] = "tie_break_order"
    elif corruption == "foreign_event": value["evidenceIds"] = ["event:unrelated"]
    elif corruption == "code_only": value["evidenceIds"] = [build["evidence"][-1]["id"]]
    elif corruption == "duplicate_reference": value["evidenceIds"] *= 2
    elif corruption == "probe_extra": value["probe"]["code"] = "SECRET_PAYLOAD"
    elif corruption in {"bool", "float", "negative", "large", "short", "long", "equal"}:
        value["probe"]["values"] = {"bool": [True, 3], "float": [2.0, 5], "negative": [-1, 3],
                                     "large": [101, 3], "short": [3], "long": [3]*6, "equal": [0, 2]}[corruption]
    raw = json.dumps(value)
    if corruption == "duplicate_json_key": raw = raw[:-1] + ',"hypothesisId":"uncertain"}'
    elif corruption == "nan": raw = raw.replace("[2, 5]", "[NaN, 5]")
    elif corruption == "too_large": raw = "SECRET_PAYLOAD" * 1000
    elif corruption == "deep": raw = "[" * 1100 + "0" + "]" * 1100
    elif corruption == "non_string": raw = value
    request_id = None if corruption == "null_request" else build["requestId"]
    with pytest.raises(ValueError) as error:
        await manager.parse(handler, {"requestId": request_id, "code": CODE, "rawText": raw})
    assert "SECRET_PAYLOAD" not in str(error.value)
    assert not any(event.event_type == "hint_request" for event in handler.events.list_events(limit=None))


@pytest.mark.asyncio
@pytest.mark.parametrize("wrapper", ["```json\n{}\n```", "```\n{}\n```", " \r\n```JSON\r\n{}\r\n```\r\n"])
async def test_whole_response_json_fence_is_compatible_without_bypassing_validation(handler, wrapper):
    await open_task(handler)
    manager = LearningCoachManager()
    build = await manager.build(handler, {"code": CODE, "level": 1})
    message = await manager.parse(handler, {"requestId": build["requestId"], "code": CODE,
                                           "rawText": wrapper.format(response(build, probe={"values": [3, 7]}))})
    assert message["hypothesisId"] == "row_count_vs_quantity"
    assert message["probe"] == {"values": [3, 7]}
    assert not any(event.event_type == "hint_request" for event in handler.events.list_events(limit=None))
    build = await manager.build(handler, {"code": CODE, "level": 1})
    with pytest.raises(CoachResponseError) as error:
        await manager.parse(handler, {"requestId": build["requestId"], "code": CODE,
                                      "rawText": wrapper.format(response(build, evidence=["event:SECRET_PAYLOAD"]))})
    assert error.value.code == "COACH_RESPONSE_REFERENCES"
    assert "SECRET_PAYLOAD" not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("wrapper", [
    "Here is the answer:\n```json\n{}\n```", "```json\n{}\n```\nUse this answer.",
    "```json\n{}\n```\n```json\n{{}}\n```", "```python\n{}\n```", "{}\n{{}}",
])
async def test_json_is_not_extracted_from_prose_multiple_blocks_or_other_languages(handler, wrapper):
    await open_task(handler)
    manager = LearningCoachManager()
    build = await manager.build(handler, {"code": CODE, "level": 1})
    with pytest.raises(CoachResponseError) as error:
        await manager.parse(handler, {"requestId": build["requestId"], "code": CODE,
                                      "rawText": wrapper.format(response(build))})
    assert error.value.code == "COACH_RESPONSE_FORMAT"
    with pytest.raises(ValueError, match="失效"):
        manager.pending_messages(handler, build["requestId"])


@pytest.mark.asyncio
@pytest.mark.parametrize("failure, code", [
    ("invalid_json", "COACH_RESPONSE_FORMAT"), ("extra_field", "COACH_RESPONSE_FIELDS"),
    ("wrong_hypothesis", "COACH_RESPONSE_HYPOTHESIS"), ("bad_reference", "COACH_RESPONSE_REFERENCES"),
    ("irrelevant_probe", "COACH_RESPONSE_PROBE"), ("too_large", "COACH_RESPONSE_TOO_LARGE"),
])
async def test_model_rejection_reports_only_safe_fixed_reason_and_clears_request(handler, failure, code):
    await open_task(handler)
    manager = LearningCoachManager()
    build = await manager.build(handler, {"code": CODE, "level": 1})
    value = json.loads(response(build))
    if failure == "extra_field": value["SECRET_PAYLOAD"] = "SECRET_PAYLOAD"
    elif failure == "wrong_hypothesis": value["hypothesisId"] = "SECRET_PAYLOAD"
    elif failure == "bad_reference": value["evidenceIds"] = ["event:SECRET_PAYLOAD"]
    elif failure == "irrelevant_probe": value.update(hypothesisId="missing_group_key", probe={"values": [3, 7]})
    raw = json.dumps(value)
    if failure == "invalid_json": raw = "SECRET_PAYLOAD"
    elif failure == "too_large": raw = "SECRET_PAYLOAD" * 1000
    with pytest.raises(CoachResponseError) as error:
        await manager.parse(handler, {"requestId": build["requestId"], "code": CODE, "rawText": raw})
    assert error.value.code == code and error.value.retryable is True
    assert code in str(error.value) and "SECRET_PAYLOAD" not in str(error.value)
    with pytest.raises(ValueError, match="失效"):
        manager.pending_messages(handler, build["requestId"])
    assert not any(event.event_type == "hint_request" for event in handler.events.list_events(limit=None))


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["infrastructure", "dry_run", "ai_failure", "worker"])
async def test_environment_and_unverified_checks_never_become_knowledge_claims(handler, kind):
    await open_task(handler)
    manager = LearningCoachManager()
    if kind == "worker":
        handler._runner.state.last_exec = ExecResult(ExecMode.LOCAL, 1, "Python worker exited unexpectedly", "private path")
    else:
        handler._record_event("code_submit", {"passed": False, "assessmentEligible": False,
                                              "assessmentSource": "course_tests", "failureKind": kind,
                                              "mode": "dry_run" if kind == "dry_run" else "local"})
    build = await manager.build(handler, {"code": CODE, "level": 1})
    context = json.loads(build["messages"][-1]["content"])
    assert context["allowedHypotheses"] == ["uncertain"]
    assert context["probePolicy"]["requiredNull"] is True
    assert context["probePolicy"]["allowedHypotheses"] == []
    assert context["responseExample"]["probe"] is None
    message = await manager.parse(handler, {"requestId": build["requestId"], "code": CODE,
                                           "rawText": json.dumps(context["responseExample"])})
    assert "环境" in message["question"] and "不能" in message["reflection"]
    assert "private path" not in json.dumps(message)
    assert all(item["score"] is None for item in manager.get_state(handler)["dimensions"])
    build = await manager.build(handler, {"code": CODE, "level": 1})
    # This otherwise well-formed response is exactly what the old, incomplete
    # prompt permitted. The advertised policy now matches the strict parser.
    with pytest.raises(CoachResponseError) as error:
        await manager.parse(handler, {"requestId": build["requestId"], "code": CODE,
                                      "rawText": response(build, "uncertain", {"values": [3, 7]})})
    assert error.value.code == "COACH_RESPONSE_PROBE"


@pytest.mark.asyncio
async def test_raw_runtime_feedback_only_sent_as_bounded_unverified_context(handler):
    await open_task(handler)
    manager = LearningCoachManager()
    handler._runner.state.last_exec = ExecResult(ExecMode.LOCAL, 1, "x"*5000, "PRIVATE_RUNTIME" + "y"*5000)
    handler._runner.state.last_result = EvalResult(False, feedback=[FeedbackItem(None, "error", "PRIVATE_FEEDBACK" + "z"*2000)])
    build = await manager.build(handler, {"code": CODE, "level": 1})
    context = json.loads(build["messages"][-1]["content"])
    assert context["recentExecution"]["relationshipToCurrentCode"] == "unknown"
    assert len(context["recentExecution"]["stdout"]) == len(context["recentExecution"]["stderr"]) == 2000
    assert len(context["recentFeedback"][0]["message"]) == 800
    message = await manager.parse(handler, {"requestId": build["requestId"], "code": CODE, "rawText": response(build)})
    assert "PRIVATE_" not in json.dumps(message)
    await manager.ack(handler, {"messageId": message["messageId"], "code": CODE})
    assert "PRIVATE_" not in json.dumps([event.as_dict() for event in handler.events.list_events(limit=None)])


@pytest.mark.asyncio
async def test_answer_supported_learning_stays_marked_and_source_not_invented(handler):
    await open_task(handler, sensor=True)
    manager = LearningCoachManager()
    state = manager.get_state(handler)
    assert "未观察到" in next(item for item in state["evidence"] if item["id"].startswith("source:"))["description"]
    await handler.dispatch({"method": "getSolution"})
    _, message = await prepared(manager, handler, hypothesis="target_site_parameter")
    assert message["answerViewed"] is True and "受答案支持" in message["disclaimer"]
    assert "def transfer_sensor_aggregation" not in message["guidance"]


@pytest.mark.asyncio
async def test_rejected_large_code_and_non_integer_levels_are_read_only(handler):
    await open_task(handler)
    manager = LearningCoachManager()
    count = handler.events.count_events()
    for params in ({"code": CODE, "level": True}, {"code": CODE, "level": 1.0},
                   {"code": "x"*128001, "level": 1}, {"code": CODE, "level": 1, "passed": True}):
        with pytest.raises(ValueError):
            await manager.build(handler, params)
    assert handler.events.count_events() == count
