"""Explicit trusted-local startup and HTTP integration (no real Spark calls)."""

from http.client import HTTPConnection
import json
from pathlib import Path
import re
import threading

import pytest

from sparktutor.config.settings import ExecutionMode, Settings
from sparktutor.engine.executor import ExecMode, ExecResult, Executor
from sparktutor.engine.exercise_validation import VALIDATION_MARKER
from sparktutor.engine.lesson_loader import load_lesson
from sparktutor.web.server import WebServer, serve, trusted_local_settings


@pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.2", "localhost"])
def test_trusted_local_accepts_only_explicit_ipv4_loopback(host, tmp_path):
    assert trusted_local_settings(host=host, data_dir=tmp_path) == tmp_path.resolve()


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.20", "example.com", "::", ""])
def test_trusted_local_rejects_remote_addresses_before_opening_any_files(host, tmp_path):
    target = tmp_path / "not-created"
    with pytest.raises(ValueError, match="回环地址"):
        trusted_local_settings(host=host, data_dir=target)
    assert not target.exists()


def test_trusted_local_uses_a_separate_default_directory(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    assert trusted_local_settings(host="127.0.0.1") == tmp_path / ".sparktutor-trusted-local"
    assert not (tmp_path / ".sparktutor-trusted-local").exists()


def test_trusted_local_cannot_reuse_account_root_or_a_nested_user_directory(tmp_path):
    root = tmp_path / "classroom"
    accounts = root / "accounts"
    accounts.mkdir(parents=True)
    (accounts / "accounts.sqlite3").touch()
    user_workspace = root / "users" / "some-user"
    user_workspace.mkdir(parents=True)
    for target in (root, user_workspace, user_workspace / "new-child"):
        with pytest.raises(ValueError, match="独立数据目录"):
            trusted_local_settings(host="127.0.0.1", data_dir=target)


def test_trusted_local_cannot_combine_accounts_or_syntax_only_mode(tmp_path):
    with pytest.raises(ValueError, match="同时使用"):
        trusted_local_settings(host="127.0.0.1", data_dir=tmp_path, dry_run=True)
    with pytest.raises(ValueError, match="多用户账号"):
        WebServer(("127.0.0.1", 0), Settings(data_dir=tmp_path), accounts=True, trusted_local=True)


def test_serve_enables_real_local_mode_without_opening_accounts(monkeypatch, tmp_path, capsys):
    instances = []

    class StubServer:
        def __init__(self, address, settings, **options):
            self.server_port = address[1]
            self.settings = settings
            self.options = options
            self.closed = False
            instances.append(self)

        def serve_forever(self):
            raise KeyboardInterrupt

        def server_close(self):
            self.closed = True

    monkeypatch.setattr("sparktutor.web.server.WebServer", StubServer)
    monkeypatch.setattr(Settings, "load", lambda: Settings(execution_mode=ExecutionMode.DRY_RUN))
    serve(port=8889, host="127.0.0.1", data_dir=tmp_path, trusted_local=True)
    assert instances[0].settings.execution_mode == ExecutionMode.LOCAL
    assert instances[0].options == {"accounts": False, "trusted_local": True}
    assert instances[0].closed
    output = capsys.readouterr().out
    assert "可信本机实操" in output and "首次管理员密码" not in output


@pytest.fixture
def trusted_server(tmp_path):
    server = WebServer(("127.0.0.1", 0),
                       Settings(data_dir=tmp_path, execution_mode=ExecutionMode.LOCAL),
                       trusted_local=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def request(server, path, payload=None, headers=None):
    connection = HTTPConnection("127.0.0.1", server.server_port, timeout=15)
    connection.request("POST" if payload is not None else "GET", path,
                       json.dumps(payload) if payload is not None else None, headers or {})
    response = connection.getresponse()
    body = response.read().decode()
    data = json.loads(body) if "application/json" in response.getheader("Content-Type", "") else body
    connection.close()
    return response.status, data


def client_headers(server):
    status, page = request(server, "/")
    assert status == 200 and "login-form" not in page
    assert "本机实操" in page
    token = re.search('name="sparktutor-token" content="([^\"]+)"', page)[1]
    return {"Content-Type": "application/json", "X-SparkTutor-Token": token,
            "X-SparkTutor-Session": "trusted-local-http-test"}


def rpc(server, headers, method, params=None):
    status, data = request(server, "/api/rpc", {"method": method, "params": params or {}}, headers)
    assert status == 200, data
    return data["result"]


def test_trusted_http_reports_local_mode_and_retains_token_and_origin_checks(trusted_server):
    status, health = request(trusted_server, "/api/health")
    assert status == 200 and health["accessMode"] == "trusted_local"
    assert health["executionMode"] == "local"
    headers = client_headers(trusted_server)
    payload = {"method": "listCourses"}
    assert request(trusted_server, "/api/rpc", payload)[0] == 403
    assert request(trusted_server, "/api/rpc", payload,
                   {**headers, "Origin": "https://outside.example"})[0] == 403
    assert len(rpc(trusted_server, headers, "listCourses")["courses"]) == 2
    assert rpc(trusted_server, headers, "webSettings")["mode"] == "local"
    assert not (trusted_server.settings.data_dir / "accounts").exists()


def test_trusted_http_submission_runs_the_shared_engine_and_records_evidence(trusted_server, monkeypatch):
    # Stub only the external Spark process. This proves transport/engine wiring,
    # while opt-in Spark tests separately validate actual course execution.
    observed = []

    async def execute(self, code, **kwargs):
        observed.append(code)
        if "# intentionally-wrong" in code:
            return ExecResult(ExecMode.LOCAL, 1, "", "AssertionError: expected rows")
        return ExecResult(ExecMode.LOCAL, 0, VALIDATION_MARKER + "\n", "", validation_passed=True)

    monkeypatch.setattr(Executor, "execute", execute)
    headers = client_headers(trusted_server)
    directory = Path(__file__).resolve().parents[1] / "src/sparktutor/courses/learning_spark/lessons/01_getting_started"
    lesson = load_lesson(directory)
    steps = [step for step in lesson.steps_for_depth("beginner") if step.cls != "meta"]
    index = next(i for i, step in enumerate(steps) if step.id == "practice-filter")
    # Existing progress is ordinary persisted state, not a fabricated grade.
    from sparktutor.state.progress import ProgressStore
    ProgressStore(trusted_server.settings.data_dir / "progress.db").save(
        "learning_spark", lesson.id, current_step=index, total_steps=len(steps),
        depth="beginner", current_step_id="practice-filter", last_code="")
    loaded = rpc(trusted_server, headers, "loadLesson", {"courseId": "learning_spark", "lessonIdx": 0})
    assert loaded["step"]["id"] == "practice-filter"
    code = (directory / steps[index].solution_code).read_text(encoding="utf-8")
    failed = rpc(trusted_server, headers, "webSubmit",
                 {"code": code + "\n# intentionally-wrong", "contextToken": loaded["contextToken"]})
    assert failed["passed"] is False
    accepted = rpc(trusted_server, headers, "webSubmit",
                   {"code": code, "contextToken": loaded["contextToken"]})
    assert accepted["passed"] is True
    assert len(observed) == 2
    assert trusted_server.runtime.workspace.load("learning_spark", lesson.id, "practice-filter") == code
    diagnosis = rpc(trusted_server, headers, "getDiagnosis")
    assert next(item for item in diagnosis["dimensions"] if item["key"] == "knowledge")["score"] == 100
    assert next(item for item in diagnosis["dimensions"] if item["key"] == "debugging")["score"] == 100
