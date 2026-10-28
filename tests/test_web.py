"""Exercise the HTTP boundary and browser session isolation using real lesson rules."""

import asyncio
import json
from pathlib import Path
import threading
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from sparktutor.config.settings import ExecutionMode, Settings
from sparktutor.engine.lesson_loader import load_course, load_lesson
from sparktutor.state.progress import ProgressStore
from sparktutor.web.server import WebRequestHandler, WebRuntime, WebServer


@pytest.fixture
def runtime(tmp_path):
    instance = WebRuntime(Settings(data_dir=tmp_path, execution_mode=ExecutionMode.DRY_RUN))
    yield instance
    instance.close()


def call(runtime, method, params=None, session="a" * 32):
    return asyncio.run_coroutine_threadsafe(
        runtime.dispatch(session, method, params or {}), runtime.loop
    ).result(timeout=30)


def test_tabs_keep_independent_lesson_positions(runtime):
    first = call(runtime, "loadLesson", {"courseId": "learning_spark", "lessonIdx": 0})
    second = call(runtime, "loadLesson", {"courseId": "learning_spark", "lessonIdx": 1}, session="b" * 32)
    call(runtime, "advance")
    assert call(runtime, "getStep")["currentIndex"] == first["currentIndex"] + 1
    assert call(runtime, "getStep", session="b" * 32)["currentIndex"] == second["currentIndex"]


def test_draft_is_restored_and_stale_save_cannot_overwrite_next_step(runtime):
    loaded = call(runtime, "loadLesson", {"courseId": "learning_spark", "lessonIdx": 0})
    step_id = loaded["step"]["id"]
    assert call(runtime, "saveDraft", {"stepId": step_id, "code": "# saved draft"})["saved"]
    restored = call(runtime, "loadLesson", {"courseId": "learning_spark", "lessonIdx": 0})
    assert restored["restoredCode"] == "# saved draft"
    call(runtime, "advance")
    assert not call(runtime, "saveDraft", {"stepId": step_id, "code": "# stale"})["saved"]


def test_choice_gate_and_diagnosis_use_existing_assessment_rules(runtime):
    loaded = call(runtime, "loadLesson", {"courseId": "learning_spark", "lessonIdx": 0})
    while loaded["step"]["cls"] == "text":
        loaded = call(runtime, "advance")
    assert loaded["step"]["cls"] == "mult_question"
    with pytest.raises(ValueError, match="请先提交"):
        call(runtime, "advance")
    assert not call(runtime, "webSubmit", {"code": "an incorrect answer"})["passed"]
    assert call(runtime, "webSubmit", {"code": loaded["step"]["correctAnswer"]})["passed"]
    report = call(runtime, "getDiagnosis")
    knowledge = next(d for d in report["dimensions"] if d["key"] == "knowledge")
    assert knowledge["score"] == 100
    assert call(runtime, "advance")["currentIndex"] == loaded["currentIndex"] + 1


@pytest.mark.parametrize("depth", ["beginner", "intermediate", "advanced"])
def test_new_learner_can_reach_guided_practice_and_dry_run_cannot_pass_it(runtime, depth):
    loaded = call(runtime, "loadLesson", {"courseId": "learning_spark", "lessonIdx": 0, "depth": depth})
    visited = []
    while loaded["step"]["id"] != "practice-filter":
        step = loaded["step"]
        visited.append(step["id"])
        assert step["cls"] in {"text", "mult_question", "cmd_question"}
        if step["cls"] != "text":
            result = call(runtime, "webSubmit", {"code": step["correctAnswer"]})
            assert result["passed"], result
        loaded = call(runtime, "advance")
    assert {"learning-route", "first-dataframe", "lazy-actions", "filter-rows"} <= set(visited)
    assert "def filter_ca(df):" in loaded["starterCode"]
    assert "CANDY_QUERY" in loaded["starterCode"]
    with pytest.raises(ValueError, match="请先提交"):
        call(runtime, "advance")
    # A syntactically valid starter is not evidence that a Spark exercise passed.
    assert not call(runtime, "webSubmit", {"code": loaded["starterCode"]})["passed"]
    with pytest.raises(ValueError, match="请先提交"):
        call(runtime, "advance")


@pytest.mark.parametrize("relative,step_id", [
    ("learning_spark/lessons/04_external_sources_hof", "practice-window-ranking"),
    ("learning_spark/lessons/05_spark_sql_deep_dive", "practice-sql-aggregation"),
    ("spark_declarative_pipelines/lessons/04_pipeline_framework", "practice-function-registry"),
    ("spark_declarative_pipelines/lessons/04_pipeline_framework", "practice-dependency-order"),
])
@pytest.mark.parametrize("depth", ["beginner", "intermediate", "advanced"])
def test_revised_guided_tasks_restore_complete_code_and_require_execution(runtime, relative, step_id, depth):
    courses = Path(__file__).resolve().parents[1] / "src/sparktutor/courses"
    directory = courses / relative
    lesson = load_lesson(directory)
    course_id = Path(relative).parts[0]
    course = load_course(courses / course_id)
    steps = lesson.steps_for_depth(depth)
    index = next(index for index, step in enumerate(steps) if step.id == step_id)
    step = steps[index]
    store = ProgressStore(runtime.settings.data_dir / "progress.db")
    store.save(course_id, lesson.id, current_step=index, total_steps=len(steps),
               depth=depth, current_step_id=step_id, last_code="")

    loaded = call(runtime, "loadLesson", {"courseId": course_id,
                                         "lessonIdx": course.lessons.index(lesson.id), "depth": depth})
    assert loaded["step"]["id"] == step_id
    assert loaded["step"]["requiresExecution"] is True
    assert loaded["starterCode"] == (directory / step.starter_code).read_text(encoding="utf-8")
    # Even a correct reference is only syntax-checked in dry-run mode. In
    # particular, the two plain-Python tasks must not silently bypass grading.
    reference = (directory / step.solution_code).read_text(encoding="utf-8")
    assert not call(runtime, "webSubmit", {"code": reference})["passed"]
    with pytest.raises(ValueError, match="请先提交"):
        call(runtime, "advance")
    assert call(runtime, "getStep")["step"]["id"] == step_id


def test_settings_never_return_the_secret_and_are_tab_local(runtime):
    settings = call(runtime, "configureWebSettings", {"baseUrl": "http://localhost:11434/v1", "model": "test", "apiKey": "private-secret"})
    assert settings["hasApiKey"]
    assert "private-secret" not in json.dumps(settings)
    assert call(runtime, "webSettings", session="b" * 32)["baseUrl"] == ""
    with pytest.raises(ValueError):
        call(runtime, "configureWebSettings", {"baseUrl": "file:///etc/passwd"})


@pytest.fixture
def http_server(tmp_path):
    server = WebServer(("127.0.0.1", 0), Settings(data_dir=tmp_path, execution_mode=ExecutionMode.DRY_RUN))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def http(server, path="/", body=None, headers=None):
    return urlopen(Request(f"http://127.0.0.1:{server.server_port}{path}",
                           data=body, headers=headers or {}), timeout=10)


def test_assets_health_and_token_are_served(http_server):
    with http(http_server) as response:
        html = response.read().decode()
        assert 'lang="zh-CN"' in html
        assert http_server.runtime.token in html
        assert "__API_TOKEN__" not in html
        assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    for path in ("/app.js", "/style.css", "/appearance.js", "/appearance.css", "/favicon.svg", "/api/health"):
        with http(http_server, path) as response:
            assert response.status == 200
    with pytest.raises(HTTPError) as error:
        http(http_server, "/../server.py")
    assert error.value.code == 404


def test_occupied_port_fails_instead_of_starting_a_second_server(http_server, tmp_path):
    with pytest.raises(OSError):
        WebServer(("127.0.0.1", http_server.server_port), Settings(data_dir=tmp_path))


def test_rpc_rejects_cross_origin_missing_token_and_invalid_messages(http_server):
    payload = json.dumps({"method": "listCourses"}).encode()
    headers = {"X-SparkTutor-Token": http_server.runtime.token, "X-SparkTutor-Session": "a" * 32}
    for invalid_headers in ({}, {**headers, "Origin": "https://other.example"}, {**headers, "Host": "other.example"}):
        with pytest.raises(HTTPError) as error:
            http(http_server, "/api/rpc", payload, invalid_headers)
        assert error.value.code == 403
    with http(http_server, "/api/rpc", payload, headers) as response:
        assert len(json.load(response)["result"]["courses"]) == 2
    for bad in (b"[]", b"{", b'{"method":"listCourses","params":[]}'):
        with pytest.raises(HTTPError) as error:
            http(http_server, "/api/rpc", bad, headers)
        assert error.value.code == 400


@pytest.mark.parametrize("host, expected", [
    ("192.168.1.20:8888", True), ("localhost:8888", True),
    ("127.0.0.1:8888", True), ("192.168.1.21:8888", False),
    ("192.168.1.20:9999", False), ("other.example:8888", False),
])
def test_host_accepts_actual_lan_interface_without_accepting_arbitrary_hosts(host, expected):
    request = SimpleNamespace(headers={"Host": host}, server=SimpleNamespace(server_port=8888),
                              connection=SimpleNamespace(getsockname=lambda: ("192.168.1.20", 8888)))
    assert WebRequestHandler.valid_host(request) is expected


def test_rpc_accepts_same_origin_and_rejects_another_allowed_host_origin(http_server):
    payload = json.dumps({"method": "listCourses"}).encode()
    headers = {"X-SparkTutor-Token": http_server.runtime.token, "X-SparkTutor-Session": "a" * 32,
               "Origin": f"http://127.0.0.1:{http_server.server_port}"}
    with http(http_server, "/api/rpc", payload, headers) as response:
        assert len(json.load(response)["result"]["courses"]) == 2
    headers["Origin"] = f"http://localhost:{http_server.server_port}"
    with pytest.raises(HTTPError) as error:
        http(http_server, "/api/rpc", payload, headers)
    assert error.value.code == 403
