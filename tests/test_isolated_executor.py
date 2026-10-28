"""Account-job lifecycle and quotas, using an explicit fake Docker boundary."""

import asyncio
from pathlib import Path

import pytest

from sparktutor.config.settings import IsolatedExecutionConfig, Settings
from sparktutor.engine.assessment_evidence import execution_failure_kind
from sparktutor.engine.executor import ExecMode
from sparktutor.engine.isolated_executor import ExecutionIdentity, IsolatedExecutor, IsolatedJobPool, execution_scope


class FakeProcess:
    def __init__(self, stdout=b"", stderr=b"", code=0, delay=0):
        self.stdout = asyncio.StreamReader()
        self.stderr = asyncio.StreamReader()
        self.stdout.feed_data(stdout)
        self.stderr.feed_data(stderr)
        self.stdout.feed_eof()
        self.stderr.feed_eof()
        self.returncode = None
        self.code = code
        self.delay = delay

    async def wait(self):
        await asyncio.sleep(self.delay)
        if self.returncode is None:
            self.returncode = self.code
        return self.returncode


def identity(owner="alice", operation="operation-a"):
    return ExecutionIdentity(owner, "same-tab-session", "learning_spark", "01_getting_started",
                             "practice-filter", operation, "server-context")


def executor(tmp_path, **configuration):
    config = IsolatedExecutionConfig(enabled=True, **configuration)
    pool = IsolatedJobPool(config, tmp_path / "jobs")
    return IsolatedExecutor(Settings(isolated_execution=config), pool, "alice")


def fake_service(monkeypatch, subject, process=None, *, cleanup=True, ready=True):
    commands = []
    removed = []

    async def control(*arguments, **kwargs):
        if arguments[0] == "rm":
            removed.append(arguments[-1])
            return (0, "", "") if cleanup else (125, "", "daemon unavailable")
        return (0, "linux\n", "") if ready else (125, "", "daemon unavailable")

    async def start(*arguments, **kwargs):
        commands.append(arguments)
        return process or FakeProcess(b"hello from this job\n")

    async def stop(process):
        process.returncode = -9

    monkeypatch.setattr(subject.pool, "_control", control)
    monkeypatch.setattr(subject, "_start_process", start)
    monkeypatch.setattr(subject, "_stop_process", stop)
    return commands, removed


def test_capacity_is_shared_across_accounts_and_never_exceeds_per_user_limits(tmp_path):
    pool = IsolatedJobPool(IsolatedExecutionConfig(enabled=True), tmp_path)
    first = pool.reserve("alice")
    assert first and pool.reserve("alice") is None
    second = pool.reserve("bob")
    assert second and pool.reserve("charlie") is None
    pool.release(first)
    assert pool.reserve("charlie")


def test_job_mounts_only_its_directory_and_pins_nonroot_resource_and_network_limits(tmp_path):
    subject = executor(tmp_path)
    directory = tmp_path / "job-123"
    command = subject.pool.command("sparktutor-job-test", directory, "alice")
    for flag, value in [("--network", "none"), ("--user", "10001:10001"),
                        ("--cpus", "2.0"), ("--memory", "3g"), ("--pids-limit", "512"),
                        ("--cap-drop", "ALL"), ("--security-opt", "no-new-privileges")]:
        assert command[command.index(flag) + 1] == value
    assert "--read-only" in command and "--pull=never" in command
    assert command.count("--mount") == 1
    assert command[command.index("--mount") + 1] == f"type=bind,source={directory},target=/job,readonly"
    assert command[-2:] == ["sparktutor-executor:4.1.1", "/job/exercise.py"]
    assert "alice" not in " ".join(command)


@pytest.mark.asyncio
async def test_execution_binds_server_identity_and_cleans_only_its_job(tmp_path, monkeypatch):
    subject = executor(tmp_path)
    commands, removed = fake_service(monkeypatch, subject)
    observed = []
    with execution_scope(identity()):
        result = await subject.execute("print('hello')", observed.append)
    assert result.mode == ExecMode.ISOLATED and result.success
    assert result.execution["userId"] == "alice" and result.execution["operationId"] == "operation-a"
    assert result.execution["taskId"] == "01_getting_started:practice-filter"
    assert result.execution["cleanupVerified"] is True
    assert removed == [result.execution["jobId"]]
    assert observed == ["hello from this job"]
    assert subject.pool.capacity()["activeJobs"] == 0
    assert not list(subject.pool.root.iterdir())


@pytest.mark.asyncio
async def test_executor_rejects_a_different_owner_even_with_a_well_formed_task(tmp_path, monkeypatch):
    subject = executor(tmp_path)
    commands, _ = fake_service(monkeypatch, subject)
    with execution_scope(identity(owner="bob")):
        result = await subject.execute("print('not run')")
    assert not result.success and execution_failure_kind(result) == "infrastructure"
    assert commands == [] and subject.pool.capacity()["activeJobs"] == 0


@pytest.mark.asyncio
async def test_missing_docker_never_degrades_to_syntax_or_host_execution(tmp_path, monkeypatch):
    subject = executor(tmp_path)
    commands, removed = fake_service(monkeypatch, subject, ready=False)
    with execution_scope(identity()):
        result = await subject.execute("print('syntax is valid')")
    assert result.exit_code == 125 and result.mode == ExecMode.ISOLATED
    assert execution_failure_kind(result) == "infrastructure"
    assert not commands and not removed and not subject.pool.root.exists()
    assert subject.pool.capacity()["activeJobs"] == 0


@pytest.mark.asyncio
async def test_timeout_removes_the_exact_container_and_releases_capacity(tmp_path, monkeypatch):
    subject = executor(tmp_path, timeout_seconds=1)
    commands, removed = fake_service(monkeypatch, subject, FakeProcess(delay=5))
    with execution_scope(identity()):
        result = await subject.execute("while True: pass")
    assert result.exit_code == 124 and execution_failure_kind(result) == "infrastructure"
    assert removed == [result.execution["jobId"]]
    assert subject.pool.capacity()["activeJobs"] == 0


@pytest.mark.asyncio
async def test_output_limit_stops_the_job_without_creating_pass_evidence(tmp_path, monkeypatch):
    subject = executor(tmp_path, output_limit_bytes=1024)
    _, removed = fake_service(monkeypatch, subject, FakeProcess(stdout=b"x" * 2048))
    with execution_scope(identity()):
        result = await subject.execute("print('too much')")
    assert result.exit_code == 126 and len(result.stdout.encode()) <= 1024
    assert execution_failure_kind(result) == "infrastructure" and removed


@pytest.mark.asyncio
async def test_cancelled_request_also_removes_its_container(tmp_path, monkeypatch):
    subject = executor(tmp_path)
    commands, removed = fake_service(monkeypatch, subject, FakeProcess(delay=5))

    async def run():
        with execution_scope(identity()):
            await subject.execute("while True: pass")

    running = asyncio.create_task(run())
    while not commands:
        await asyncio.sleep(0)
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    assert len(removed) == 1 and subject.pool.capacity()["activeJobs"] == 0


@pytest.mark.asyncio
async def test_repeated_cancellation_waits_for_full_cleanup_before_releasing_request(tmp_path, monkeypatch):
    subject = executor(tmp_path)
    commands, removed = fake_service(monkeypatch, subject, FakeProcess(delay=5))
    removal_started, release_removal = asyncio.Event(), asyncio.Event()
    original_remove = subject.pool.remove

    async def delayed_remove(job_id):
        removal_started.set()
        await release_removal.wait()
        return await original_remove(job_id)

    monkeypatch.setattr(subject.pool, "remove", delayed_remove)

    async def run():
        with execution_scope(identity()):
            await subject.execute("while True: pass")

    running = asyncio.create_task(run())
    while not commands:
        await asyncio.sleep(0)
    running.cancel()
    await removal_started.wait()
    running.cancel()
    await asyncio.sleep(0)
    assert not running.done()
    assert subject.pool.capacity()["activeJobs"] == 1
    release_removal.set()
    with pytest.raises(asyncio.CancelledError):
        await running
    assert len(removed) == 1
    assert subject.pool.capacity()["activeJobs"] == 0
    assert subject.execution_metadata == {}
    assert not list(subject.pool.root.iterdir())
    assert subject.pool.reserve("alice") is not None


@pytest.mark.asyncio
async def test_unverified_container_cleanup_blocks_success_and_holds_its_quota(tmp_path, monkeypatch):
    subject = executor(tmp_path)
    fake_service(monkeypatch, subject, cleanup=False)
    with execution_scope(identity()):
        result = await subject.execute("print('complete')")
    assert result.exit_code == 125 and not result.execution["cleanupVerified"]
    assert execution_failure_kind(result) == "infrastructure"
    assert subject.pool.capacity()["activeJobs"] == 1
