"""Declarative coaching routes tasks and evidence; it does not score model semantics."""

from copy import deepcopy
import json
from pathlib import Path

import pytest
import yaml

from sparktutor.config.settings import ExecutionMode, Settings
from sparktutor.courses.registry import CourseRegistry
from sparktutor.engine.coach_catalog import MAX_FILE_BYTES, load_coach_catalog
from sparktutor.engine.learning_coach import CoachResponseError, LearningCoachManager
from sparktutor.server.handler import ServerHandler


CASES = json.loads((Path(__file__).parent / "fixtures/coach_catalog_cases.json").read_text(encoding="utf-8"))


def configured_task(step_id="task-a", hypothesis="contract_check"):
    def node(label, kinds):
        return {"summary": label, "evidenceRequirements": {"anyOf": kinds},
                "levels": [{"question": f"Level {n}: what differs?", "guidance": f"Check one input at level {n}.",
                            "reflection": "Compare the observation with the contract."} for n in range(1, 4)]}
    return {"stepId": step_id, "topic": "independent_topic", "contract": "Preserve the supplied values.",
            "knowledgeComponents": ["test.contract"],
            "validation": {"kind": "course_tests", "guidance": "Submit to the original course tests."},
            "hypotheses": {hypothesis: node("A contract may need checking.", ["task", "event"]),
                           "uncertain": node("Collect evidence before choosing a cause.", ["task"])}}


def save_config(directory, tasks):
    (directory / "coach.yaml").write_text(
        yaml.safe_dump({"schemaVersion": "coach-catalog-v1", "tasks": tasks}, sort_keys=False), encoding="utf-8")


@pytest.fixture
def configured_registry(tmp_path):
    courses = tmp_path / "courses"
    course = courses / "config_course"
    course.mkdir(parents=True)
    (course / "course.yaml").write_text(yaml.safe_dump({"course": {
        "id": "config_course", "title": "Configured course", "description": "Test course",
        "version": "1.0.0", "lessons": ["lesson_one", "lesson_two"],
    }}), encoding="utf-8")
    for name in ("lesson_one", "lesson_two"):
        directory = course / "lessons" / name
        directory.mkdir(parents=True)
        steps = [{"Class": "meta", "Id": "meta", "Lesson": name}]
        for step_id in ("task-a", "task-b"):
            steps.append({"Class": "script", "Id": step_id, "Depth": "all",
                          "RequiresExecution": True, "KnowledgeComponents": ["test.contract"],
                          "Output": "Preserve every input value, including duplicates.",
                          "StarterCode": "starter.py", "SolutionCode": "solution.py"})
        (directory / "lesson.yaml").write_text(yaml.safe_dump(steps), encoding="utf-8")
        (directory / "starter.py").write_text("def solve(data):\n    return None\n", encoding="utf-8")
        (directory / "solution.py").write_text("def solve(data):\n    return data\n", encoding="utf-8")
        save_config(directory, [configured_task()])
    return CourseRegistry(courses)


@pytest.fixture
def configured_handler(configured_registry, tmp_path):
    handler = ServerHandler(Settings(data_dir=tmp_path / "state", execution_mode=ExecutionMode.DRY_RUN),
                            workspace_path=tmp_path / "workspace")
    handler.registry = configured_registry
    return handler


async def open_configured(handler, step="task-a"):
    await handler._load_lesson({"courseId": "config_course", "lessonIdx": 0}, target_step_id=step)


def reply(build, hypothesis="contract_check", *, evidence=None, probe=None):
    return json.dumps({"hypothesisId": hypothesis, "evidenceIds": evidence or [build["evidence"][0]["id"]],
                       "probe": probe})


def test_registered_course_configs_cover_four_topics_and_preserve_legacy_choices():
    catalog = load_coach_catalog(CourseRegistry())
    assert {"aggregation", "schema", "join", "window"}.issubset({task.topic for task in catalog.tasks.values()})
    mnm = catalog.tasks[("learning_spark", "01_getting_started", "15")]
    sensor = catalog.tasks[("learning_spark", "11_diagnosis_practice", "sensor_transfer")]
    assert set(mnm.hypotheses) == {"missing_group_key", "row_count_vs_quantity", "uncertain"}
    assert set(sensor.hypotheses) == set(mnm.hypotheses) | {"target_site_parameter", "tie_break_order"}
    assert mnm.probe_kind == sensor.probe_kind == "row_count_vs_quantity"
    assert set(mnm.probe_hypotheses) == {"row_count_vs_quantity", "uncertain"}
    for task in catalog.tasks.values():
        assert len(task.hypotheses["uncertain"].levels) == 3
        assert task.validation_kind == "course_tests" and task.fingerprint


@pytest.mark.parametrize("corruption, expected", [
    ("unknown_field", "invalid_fields"), ("unknown_kc", "unknown_knowledge_component"),
    ("executable_validation", "unsupported_validation_kind"), ("executable_probe", "unsupported_probe_kind"),
    ("missing_uncertain", "uncertain_hypothesis_required"), ("wrong_level_count", "three_levels_required"),
    ("code_only_evidence", "unsupported_evidence_kind"), ("uncertain_without_task", "uncertain_requires_task_evidence"),
    ("wrong_step", "unknown_step_id"), ("bad_hypothesis_id", "invalid_hypothesis_id"),
])
def test_bad_task_is_isolated_from_other_valid_tasks(configured_registry, corruption, expected):
    directory = configured_registry.courses_dir / "config_course/lessons/lesson_one"
    bad = configured_task()
    if corruption == "unknown_field": bad["execute"] = "PRIVATE_COMMAND"
    elif corruption == "unknown_kc": bad["knowledgeComponents"] = ["not.in.this.task"]
    elif corruption == "executable_validation": bad["validation"]["kind"] = "python"
    elif corruption == "executable_probe": bad["probe"] = {"kind": "python", "allowedHypotheses": ["uncertain"]}
    elif corruption == "missing_uncertain": del bad["hypotheses"]["uncertain"]
    elif corruption == "wrong_level_count": bad["hypotheses"]["contract_check"]["levels"].pop()
    elif corruption == "code_only_evidence": bad["hypotheses"]["contract_check"]["evidenceRequirements"]["anyOf"] = ["code"]
    elif corruption == "uncertain_without_task": bad["hypotheses"]["uncertain"]["evidenceRequirements"]["anyOf"] = ["event"]
    elif corruption == "wrong_step": bad["stepId"] = "does-not-exist"
    elif corruption == "bad_hypothesis_id": bad["hypotheses"]["execute-command"] = bad["hypotheses"].pop("contract_check")
    save_config(directory, [bad, configured_task("task-b")])
    catalog = load_coach_catalog(configured_registry)
    assert ("config_course", "lesson_one", "task-a") not in catalog.tasks
    assert ("config_course", "lesson_one", "task-b") in catalog.tasks
    assert ("config_course", "lesson_two", "task-a") in catalog.tasks
    assert expected in {issue.code for issue in catalog.issues}
    assert "PRIVATE_COMMAND" not in str(catalog.issues)


@pytest.mark.parametrize("content", [
    "schemaVersion: coach-catalog-v1\nschemaVersion: duplicate\ntasks: []\n",
    "!!python/object/apply:os.system ['PRIVATE_COMMAND']",
    "schemaVersion: coach-catalog-v1\ntasks: &loop [*loop]\n",
    "[" * 25 + "0" + "]" * 25,
    "#" * (MAX_FILE_BYTES + 1),
], ids=["duplicate-key", "python-tag", "recursive-alias", "deep-document", "oversized-document"])
def test_bad_file_does_not_disable_other_lessons(configured_registry, content):
    path = configured_registry.courses_dir / "config_course/lessons/lesson_one/coach.yaml"
    path.write_text(content, encoding="utf-8")
    catalog = load_coach_catalog(configured_registry)
    assert ("config_course", "lesson_two", "task-a") in catalog.tasks
    assert not any(key[1] == "lesson_one" for key in catalog.tasks)
    assert catalog.issues and "PRIVATE_COMMAND" not in str(catalog.issues)


def test_duplicate_step_config_disables_that_step_without_overriding_an_author_node(configured_registry):
    directory = configured_registry.courses_dir / "config_course/lessons/lesson_one"
    save_config(directory, [configured_task(), configured_task(), configured_task("task-b")])
    catalog = load_coach_catalog(configured_registry)
    assert ("config_course", "lesson_one", "task-a") not in catalog.tasks
    assert ("config_course", "lesson_one", "task-b") in catalog.tasks
    assert "duplicate_step_id" in {issue.code for issue in catalog.issues}


def test_yaml_object_tags_never_invoke_author_supplied_commands(configured_registry, monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("Coaching configuration cannot execute commands")
    monkeypatch.setattr("os.system", forbidden)
    path = configured_registry.courses_dir / "config_course/lessons/lesson_one/coach.yaml"
    path.write_text("!!python/object/apply:os.system ['anything']", encoding="utf-8")
    catalog = load_coach_catalog(configured_registry)
    assert not any(key[1] == "lesson_one" for key in catalog.tasks)
    assert ("config_course", "lesson_two", "task-a") in catalog.tasks


def test_coach_file_cannot_follow_a_path_outside_registered_courses(configured_registry, tmp_path):
    path = configured_registry.courses_dir / "config_course/lessons/lesson_one/coach.yaml"
    external = tmp_path / "outside-coach.yaml"
    external.write_bytes(path.read_bytes())
    path.unlink()
    try:
        path.symlink_to(external)
    except OSError as error:
        pytest.skip(f"Symlink creation not available: {error}")
    catalog = load_coach_catalog(configured_registry)
    assert not any(key[1] == "lesson_one" for key in catalog.tasks)
    assert ("config_course", "lesson_two", "task-a") in catalog.tasks
    assert "configuration_path_outside_courses" in {issue.code for issue in catalog.issues}


@pytest.mark.asyncio
async def test_new_configured_task_and_hypothesis_work_without_engine_edits(configured_handler):
    handler = configured_handler
    await open_configured(handler, "task-b")
    manager = LearningCoachManager()
    assert not manager.get_state(handler)["available"]
    directory = handler.registry.courses_dir / "config_course/lessons/lesson_one"
    save_config(directory, [configured_task(), configured_task("task-b", "new_configured_choice")])
    assert manager.get_state(handler)["available"]
    build = await manager.build(handler, {"code": "def solve(values): return values", "level": 1})
    context = json.loads(build["messages"][-1]["content"])
    assert "new_configured_choice" in context["allowedHypotheses"]
    assert context["coachTopic"] == "independent_topic" and context["taskContract"]
    assert context["verification"]["kind"] == "course_tests" and context["coachConfigurationSha256"]
    before = handler.events.count_events()
    message = await manager.parse(handler, {"requestId": build["requestId"], "code": "def solve(values): return values",
                                           "rawText": reply(build, "new_configured_choice")})
    assert message["hypothesisId"] == "new_configured_choice"
    assert handler.events.count_events() == before
    assert manager.get_state(handler)["maxLevel"] == 1
    ack = {"messageId": message["messageId"], "code": "def solve(values): return values"}
    assert (await manager.ack(handler, ack))["recorded"]
    assert not (await manager.ack(handler, ack))["recorded"]
    assert manager.get_state(handler)["maxLevel"] == 2
    assert all(item["score"] is None for item in manager.get_state(handler)["dimensions"])


@pytest.mark.asyncio
async def test_task_specific_evidence_requirements_are_available_and_enforced(configured_handler):
    handler = configured_handler
    directory = handler.registry.courses_dir / "config_course/lessons/lesson_one"
    task = configured_task()
    task["hypotheses"]["contract_check"]["evidenceRequirements"]["anyOf"] = ["event"]
    save_config(directory, [task])
    await open_configured(handler)
    manager = LearningCoachManager()
    build = await manager.build(handler, {"code": "x = 1", "level": 1})
    assert json.loads(build["messages"][-1]["content"])["allowedHypotheses"] == ["uncertain"]
    handler._record_event("code_edit", {"changeCount": 1})
    build = await manager.build(handler, {"code": "x = 1", "level": 1})
    assert "contract_check" in json.loads(build["messages"][-1]["content"])["allowedHypotheses"]
    with pytest.raises(CoachResponseError) as error:
        await manager.parse(handler, {"requestId": build["requestId"], "code": "x = 1", "rawText": reply(build)})
    assert error.value.code == "COACH_RESPONSE_REFERENCES"
    build = await manager.build(handler, {"code": "x = 1", "level": 1})
    event = next(item["id"] for item in build["evidence"] if item["id"].startswith("event:"))
    message = await manager.parse(handler, {"requestId": build["requestId"], "code": "x = 1",
                                           "rawText": reply(build, evidence=[event])})
    assert message["hypothesisId"] == "contract_check"


@pytest.mark.asyncio
async def test_unconfigured_probe_and_changed_configuration_cannot_reuse_a_pending_request(configured_handler):
    handler = configured_handler
    await open_configured(handler)
    manager = LearningCoachManager()
    build = await manager.build(handler, {"code": "x = 1", "level": 1})
    assert json.loads(build["messages"][-1]["content"])["probePolicy"]["requiredNull"]
    with pytest.raises(CoachResponseError) as error:
        await manager.parse(handler, {"requestId": build["requestId"], "code": "x = 1",
                                       "rawText": reply(build, probe={"values": [3, 9]})})
    assert error.value.code == "COACH_RESPONSE_PROBE"
    build = await manager.build(handler, {"code": "x = 1", "level": 1})
    directory = handler.registry.courses_dir / "config_course/lessons/lesson_one"
    changed = configured_task()
    changed["hypotheses"]["contract_check"]["summary"] = "A changed author contract."
    save_config(directory, [changed])
    with pytest.raises(ValueError, match="配置已变化"):
        await manager.parse(handler, {"requestId": build["requestId"], "code": "x = 1", "rawText": reply(build)})
    assert not any(event.event_type == "hint_request" for event in handler.events.list_events(limit=None))


@pytest.mark.asyncio
async def test_unavailable_author_configuration_keeps_other_learning_tasks_usable(configured_handler):
    handler = configured_handler
    directory = handler.registry.courses_dir / "config_course/lessons/lesson_one"
    (directory / "coach.yaml").write_text("broken: [", encoding="utf-8")
    await open_configured(handler)
    manager = LearningCoachManager()
    state = manager.get_state(handler)
    assert not state["available"] and "配置不可用" in state["reason"]
    assert any(option["lessonId"] == "lesson_two" for option in state["exerciseOptions"])
    await handler._load_lesson({"courseId": "config_course", "lessonIdx": 1}, target_step_id="task-a")
    assert manager.get_state(handler)["available"]


@pytest.mark.asyncio
@pytest.mark.parametrize("case", CASES["cases"], ids=lambda case: case["id"])
async def test_fresh_cases_exercise_configured_routing_not_model_semantic_accuracy(tmp_path, case):
    assert CASES["provenance"]["modelCalled"] is False
    assert CASES["provenance"]["usedToDesignAuthorNodes"] is False
    handler = ServerHandler(Settings(data_dir=tmp_path / "state", execution_mode=ExecutionMode.DRY_RUN),
                            workspace_path=tmp_path / "workspace")
    course = handler.registry.get_course(case["courseId"])
    await handler._load_lesson({"courseId": case["courseId"], "lessonIdx": course.lessons.index(case["lessonId"])},
                               target_step_id=case["stepId"])
    manager = LearningCoachManager()
    assert manager.get_state(handler)["available"]
    build = await manager.build(handler, {"code": case["code"], "level": 1})
    context = json.loads(build["messages"][-1]["content"])
    assert context["coachTopic"] == case["topic"]
    assert case["simulatedSelection"] in context["allowedHypotheses"]
    assert case["foreignSelection"] not in context["allowedHypotheses"]
    assert context["probePolicy"]["requiredNull"]
    before = handler.events.count_events()
    with pytest.raises(CoachResponseError) as error:
        await manager.parse(handler, {"requestId": build["requestId"], "code": case["code"],
                                       "rawText": reply(build, case["foreignSelection"])})
    assert error.value.code == "COACH_RESPONSE_HYPOTHESIS"
    build = await manager.build(handler, {"code": case["code"], "level": 1})
    message = await manager.parse(handler, {"requestId": build["requestId"], "code": case["code"],
                                           "rawText": reply(build, case["simulatedSelection"])})
    assert message["hypothesisId"] == case["simulatedSelection"]
    assert handler.events.count_events() == before
    assert all(item["score"] is None for item in manager.get_state(handler)["dimensions"])
    # The selector response is authored to test routing. This is not a model call
    # and therefore does not establish whether a model diagnoses this code well.
