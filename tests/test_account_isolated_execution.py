"""Real login/RPC boundary with an explicitly simulated Docker subprocess."""

import asyncio
from http.client import HTTPConnection
import json
from pathlib import Path
import re
import threading
import time

import pytest

from sparktutor.config.settings import IsolatedExecutionConfig, Settings
from sparktutor.engine.exercise_validation import VALIDATION_MARKER
from sparktutor.engine.isolated_executor import IsolatedExecutor, IsolatedJobPool
from sparktutor.engine.lesson_loader import load_lesson
from sparktutor.state.progress import ProgressStore
from sparktutor.web.server import WebServer


class FakeContainerProcess:
    def __init__(self, code, delay=0):
        self.stdout, self.stderr = asyncio.StreamReader(), asyncio.StreamReader()
        failed = "# wrong-for-isolation-test" in code
        self.stdout.feed_data(("private-output-" + ("failed" if failed else "passed") + "\n" +
                               ("" if failed else VALIDATION_MARKER + "\n")).encode())
        self.stderr.feed_data(b"AssertionError: wrong rows" if failed else b"")
        self.stdout.feed_eof()
        self.stderr.feed_eof()
        self.returncode = None
        self.code, self.delay = (1 if failed else 0), delay

    async def wait(self):
        await asyncio.sleep(self.delay)
        if self.returncode is None:
            self.returncode = self.code
        return self.returncode


@pytest.fixture
def server(tmp_path, monkeypatch):
    configuration = IsolatedExecutionConfig(enabled=True)
    instance = WebServer(("127.0.0.1", 0), Settings(data_dir=tmp_path,
                         isolated_execution=configuration), accounts=True)
    commands, removed = [], []
    instance.test_commands = commands
    instance.test_removed = removed
    instance.test_delay = 0
    instance.test_started = threading.Event()
    instance.test_ready = True

    async def control(pool, *arguments, **kwargs):
        if arguments[0] == "rm":
            removed.append(arguments[-1])
            return 0, "", ""
        return (0, "linux\n", "") if instance.test_ready else (125, "", "not ready")

    async def start(*arguments, **kwargs):
        commands.append(arguments)
        mount = arguments[arguments.index("--mount") + 1]
        directory = Path(mount.split("source=", 1)[1].split(",target=", 1)[0])
        code = (directory / "exercise.py").read_text(encoding="utf-8")
        instance.test_started.set()
        return FakeContainerProcess(code, instance.test_delay)

    async def stop(process):
        process.returncode = -9

    monkeypatch.setattr(IsolatedJobPool, "_control", control)
    monkeypatch.setattr(IsolatedExecutor, "_start_process", staticmethod(start))
    monkeypatch.setattr(IsolatedExecutor, "_stop_process", staticmethod(stop))
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    try:
        yield instance
    finally:
        instance.shutdown()
        instance.server_close()
        thread.join(timeout=5)


def request(server, path, payload=None, client=None, session="same-browser-tab-id"):
    connection = HTTPConnection("127.0.0.1", server.server_port, timeout=20)
    headers = {"Content-Type": "application/json", "X-SparkTutor-Auth": "1",
               "X-SparkTutor-Session": session}
    if client:
        headers.update({"Cookie": client["cookie"], "X-SparkTutor-Token": client["token"]})
    connection.request("POST" if payload is not None else "GET", path,
                       json.dumps(payload) if payload is not None else None, headers)
    response = connection.getresponse()
    body = response.read().decode()
    data = json.loads(body) if "application/json" in response.getheader("Content-Type", "") else body
    result = response.status, data, response.getheader("Set-Cookie")
    connection.close()
    return result


def register(server, username):
    password = "test-password-123"
    status, _, _ = request(server, "/api/auth/register", {
        "username": username, "displayName": username, "password": password})
    assert status == 201
    status, data, cookie = request(server, "/api/auth/login", {"username": username, "password": password})
    assert status == 200
    client = {"cookie": cookie.split(";", 1)[0], "user": data["user"], "token": ""}
    status, page, _ = request(server, "/", client=client)
    assert status == 200
    client["token"] = re.search('name="sparktutor-token" content="([^\"]+)"', page)[1]
    return client


def rpc(server, client, method, params=None, session="same-browser-tab-id"):
    return request(server, "/api/rpc", {"method": method, "params": params or {}}, client, session)[:2]


def exercise(server, client, session="same-browser-tab-id"):
    directory = Path(__file__).resolve().parents[1] / "src/sparktutor/courses/learning_spark/lessons/01_getting_started"
    lesson = load_lesson(directory)
    steps = [step for step in lesson.steps_for_depth("beginner") if step.cls != "meta"]
    index = next(i for i, step in enumerate(steps) if step.id == "practice-filter")
    runtime = server.runtime_for(client["user"])
    ProgressStore(runtime.settings.data_dir / "progress.db").save(
        "learning_spark", lesson.id, current_step=index, total_steps=len(steps),
        depth="beginner", current_step_id="practice-filter", last_code="")
    status, loaded = rpc(server, client, "loadLesson", {"courseId": "learning_spark", "lessonIdx": 0}, session)
    assert status == 200 and loaded["result"]["step"]["id"] == "practice-filter"
    return loaded["result"], (directory / steps[index].solution_code).read_text(encoding="utf-8")


def test_logged_in_accounts_submit_through_isolation_and_never_mix_results(server):
    first, second = register(server, "first_learner"), register(server, "second_learner")
    loaded, code = exercise(server, first)
    exercise(server, second)
    status, settings = rpc(server, first, "webSettings")
    assert status == 200 and settings["result"]["mode"] == "isolated"
    assert settings["result"]["accountExecutionManaged"] is True
    assert rpc(server, first, "setExecutionMode", {"mode": "local"})[0] == 400
    assert rpc(server, first, "configureWebSettings", {"mode": "databricks"})[0] == 400
    status, failed = rpc(server, first, "webSubmit", {
        "code": code + "\n# wrong-for-isolation-test", "contextToken": loaded["contextToken"],
        "operationId": "first-failed", "userId": second["user"]["id"]})
    assert status == 200 and failed["result"]["passed"] is False
    status, passed = rpc(server, first, "webSubmit", {
        "code": code, "contextToken": loaded["contextToken"], "operationId": "first-passed"})
    assert status == 200 and passed["result"]["passed"] is True
    execution = passed["result"]["execution"]
    assert execution["userId"] == first["user"]["id"]
    assert execution["operationId"] == "first-passed" and execution["cleanupVerified"]
    assert execution["taskId"] == "01_getting_started:practice-filter"
    assert len(server.test_commands) == len(server.test_removed) == 2
    for client, expected in ((first, 100), (second, None)):
        status, report = rpc(server, client, "getDiagnosis")
        assert status == 200
        assert next(item for item in report["result"]["dimensions"] if item["key"] == "knowledge")["score"] == expected
    assert rpc(server, second, "webLastExecution")[1]["result"] == {}
    first_events = server.runtime_for(first["user"]).sessions["same-browser-tab-id"]["handler"].events.list_events(limit=None)
    submissions = [event for event in first_events if event.event_type == "code_submit"]
    assert len(submissions) == 2
    assert all(event.data["execution"]["userId"] == first["user"]["id"] for event in submissions)
    assert all(event.data["mode"] == "isolated" and event.data["assessmentEligible"] for event in submissions)
    notifications = rpc(server, second, "pollWebNotifications", {"after": 0})[1]["result"]["notifications"]
    assert "private-output" not in json.dumps(notifications)


def test_stale_task_token_cannot_start_a_container(server):
    client = register(server, "stale_learner")
    _, code = exercise(server, client)
    assert rpc(server, client, "webSubmit", {"code": code, "contextToken": "stale-token"})[0] == 400
    assert not server.test_commands


def test_unavailable_execution_service_cannot_grant_course_or_diagnosis_success(server):
    client = register(server, "no_service")
    loaded, code = exercise(server, client)
    server.test_ready = False
    status, result = rpc(server, client, "webSubmit", {"code": code, "contextToken": loaded["contextToken"]})
    assert status == 200 and result["result"]["passed"] is False
    assert result["result"]["failureKind"] == "infrastructure"
    assert result["result"]["assessmentEligible"] is False and not server.test_commands
    status, state = rpc(server, client, "getDiagnosis")
    assert all(item["score"] is None for item in state["result"]["dimensions"])


def test_accounts_share_global_capacity_and_a_busy_submission_creates_no_grade(server):
    first, second = register(server, "running_learner"), register(server, "waiting_learner")
    first_loaded, first_code = exercise(server, first)
    second_loaded, second_code = exercise(server, second)
    server.isolated_pool.configuration.max_concurrent_jobs = 1
    server.test_delay = 0.7
    outcome = []
    running = threading.Thread(target=lambda: outcome.append(rpc(server, first, "webSubmit", {
        "code": first_code, "contextToken": first_loaded["contextToken"], "operationId": "running-job"})))
    running.start()
    assert server.test_started.wait(5)
    status, busy = rpc(server, second, "webSubmit", {
        "code": second_code, "contextToken": second_loaded["contextToken"], "operationId": "busy-job"})
    assert status == 200 and busy["result"]["passed"] is False
    assert busy["result"]["failureKind"] == "infrastructure"
    assert busy["result"]["assessmentEligible"] is False
    running.join(timeout=10)
    assert not running.is_alive() and outcome[0][1]["result"]["passed"] is True
    assert len(server.test_commands) == 1 and server.isolated_pool.capacity()["activeJobs"] == 0


def test_server_configured_timeout_is_not_a_student_failure(server):
    client = register(server, "timed_learner")
    loaded, code = exercise(server, client)
    server.isolated_pool.configuration.timeout_seconds = 1
    server.test_delay = 5
    status, result = rpc(server, client, "webSubmit", {"code": code, "contextToken": loaded["contextToken"]})
    assert status == 200 and result["result"]["passed"] is False
    assert result["result"]["failureKind"] == "infrastructure"
    assert result["result"]["assessmentEligible"] is False
    assert len(server.test_removed) == 1 and server.isolated_pool.capacity()["activeJobs"] == 0


def test_http_cancellation_only_controls_the_requesting_accounts_operation(server):
    first, second = register(server, "cancel_owner"), register(server, "other_owner")
    loaded, code = exercise(server, first)
    exercise(server, second)
    server.test_delay = 5
    outcome = []
    running = threading.Thread(target=lambda: outcome.append(rpc(server, first, "webSubmit", {
        "code": code, "contextToken": loaded["contextToken"], "operationId": "cancel-this-job"})))
    running.start()
    assert server.test_started.wait(5)
    assert rpc(server, second, "cancelExecution", {"operationId": "cancel-this-job"})[1]["result"]["cancelRequested"] is False
    assert rpc(server, first, "cancelExecution", {"operationId": "cancel-this-job"})[1]["result"]["cancelRequested"] is True
    running.join(timeout=10)
    assert not running.is_alive() and outcome[0][0] == 409
    assert outcome[0][1]["cancelled"] is True
    assert len(server.test_removed) == 1 and server.isolated_pool.capacity()["activeJobs"] == 0
    status, report = rpc(server, first, "getDiagnosis")
    assert status == 200
    assert all(item["score"] is None for item in report["result"]["dimensions"])


def test_repeated_cancel_then_runtime_close_waits_for_container_directory_and_quota_cleanup(server, monkeypatch):
    client = register(server, "cancel_then_close")
    loaded, code = exercise(server, client)
    runtime = server.runtime_for(client["user"])
    server.test_delay = 5
    removal_started, release_removal, close_iterating = threading.Event(), threading.Event(), threading.Event()
    original_control = IsolatedJobPool._control

    async def delayed_control(pool, *arguments, **kwargs):
        if arguments[0] == "rm":
            removal_started.set()
            while not release_removal.is_set():
                await asyncio.sleep(0.01)
        return await original_control(pool, *arguments, **kwargs)

    monkeypatch.setattr(IsolatedJobPool, "_control", delayed_control)
    outcome, close_errors = [], []
    running = threading.Thread(target=lambda: outcome.append(rpc(server, client, "webSubmit", {
        "code": code, "contextToken": loaded["contextToken"], "operationId": "cancel-and-close"})))
    close_thread = None
    running.start()
    try:
        assert server.test_started.wait(5)
        assert rpc(server, client, "cancelExecution", {"operationId": "cancel-and-close"})[1]["result"]["cancelRequested"]
        assert removal_started.wait(5)
        # The HTTP endpoint is idempotent while the same cleanup is pending.
        assert rpc(server, client, "cancelExecution", {"operationId": "cancel-and-close"})[1]["result"]["cancelRequested"]

        class ObservedTasks(set):
            def __iter__(self):
                close_iterating.set()
                return super().__iter__()

        runtime._tasks = ObservedTasks(runtime._tasks)

        def close_runtime():
            try:
                runtime.close()
            except BaseException as error:
                close_errors.append(error)

        close_thread = threading.Thread(target=close_runtime)
        close_thread.start()
        assert close_iterating.wait(5)

        async def loop_barrier():
            await asyncio.sleep(0)

        asyncio.run_coroutine_threadsafe(loop_barrier(), runtime.loop).result(timeout=5)
        assert running.is_alive() and close_thread.is_alive()
        assert server.isolated_pool.capacity()["activeJobs"] == 1
    finally:
        release_removal.set()
        running.join(timeout=10)
        if close_thread is not None:
            close_thread.join(timeout=10)
    assert not running.is_alive() and close_thread is not None and not close_thread.is_alive()
    assert not close_errors and outcome[0][0] == 409 and outcome[0][1]["cancelled"] is True
    assert len(server.test_removed) == 1
    assert server.isolated_pool.capacity()["activeJobs"] == 0
    assert not list(server.isolated_pool.root.iterdir())
    handler = runtime.sessions["same-browser-tab-id"]["handler"]
    assert handler.executor.execution_metadata == {}
    assert not any(event.event_type == "code_submit" for event in handler.events.list_events(limit=None))
