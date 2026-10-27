"""Environment setup reports must remain read-only and distinguish evidence."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from sparktutor.config.settings import DatabricksConfig, ExecutionMode, Settings
from sparktutor.engine import environment_check as env


class VersionProcess:
    returncode = 0

    async def communicate(self):
        return b"", b'openjdk version "17.0.12" 2024-07-16\nSECRET_PROCESS_OUTPUT'


@pytest.fixture
def setup_environment(monkeypatch, tmp_path):
    java_home = tmp_path / "jdk"
    java_home.joinpath("bin").mkdir(parents=True)
    java_home.joinpath("bin", "java.exe" if env.os.name == "nt" else "java").touch()
    launcher = tmp_path / "spark-submit"
    launcher.touch()
    monkeypatch.setenv("JAVA_HOME", str(java_home))
    monkeypatch.setattr(env.metadata, "version", lambda name: "4.1.1")
    calls = []

    async def create(*args, **kwargs):
        calls.append(args)
        return VersionProcess()

    monkeypatch.setattr(env.asyncio, "create_subprocess_exec", create)
    executor = SimpleNamespace(_spark_submit=lambda: str(launcher))
    return executor, calls


@pytest.mark.asyncio
async def test_prerequisites_are_static_evidence_not_spark_verification(setup_environment):
    executor, calls = setup_environment
    report = await env.build_environment_report(Settings(execution_mode=ExecutionMode.LOCAL), executor)
    assert report["staticOnly"] is True
    assert report["sparkVerified"] is False
    assert report["execution"]["localPrerequisites"] is True
    assert report["capabilities"][1]["availability"] == "unverified"
    assert report["backend"]["pythonPath"] == env.sys.executable
    assert [args[-1] for args in calls] == ["-version"]
    assert "SECRET_PROCESS_OUTPUT" not in json.dumps(report)


@pytest.mark.asyncio
async def test_missing_local_dependencies_provide_next_steps_without_disabling_reading(monkeypatch, tmp_path):
    def missing(name):
        raise env.metadata.PackageNotFoundError(name)
    monkeypatch.setattr(env.metadata, "version", missing)
    monkeypatch.setenv("JAVA_HOME", str(tmp_path / "missing-jdk"))
    monkeypatch.setattr(env.shutil, "which", lambda name: None)
    executor = SimpleNamespace(_spark_submit=lambda: "missing-spark-submit")
    report = await env.build_environment_report(Settings(execution_mode=ExecutionMode.LOCAL), executor)
    checks = {item["id"]: item for item in report["checks"]}
    for key in ("java", "pyspark", "sparkSubmit"):
        assert checks[key]["status"] == "missing"
        assert checks[key]["nextSteps"]
    assert report["execution"]["localPrerequisites"] is False
    assert report["capabilities"][0]["availability"] == "available"
    assert report["capabilities"][1]["availability"] == "needs_setup"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [ExecutionMode.AUTO, ExecutionMode.LAKEHOUSE, ExecutionMode.DATABRICKS])
async def test_remote_or_auto_never_claim_actual_execution_or_leak_configuration(mode, setup_environment, monkeypatch):
    executor, calls = setup_environment
    settings = Settings(execution_mode=mode,
                        databricks=DatabricksConfig(host="private-workspace", token="SECRET_TOKEN", cluster_id="private-cluster"))
    monkeypatch.setenv("SPARK_REMOTE", "sc://SECRET_REMOTE_TOKEN")
    report = await env.build_environment_report(settings, executor)
    serialized = json.dumps(report)
    assert "SECRET" not in serialized
    assert "private-workspace" not in serialized
    assert report["execution"]["configuredMode"] == mode.value
    assert report["capabilities"][1]["availability"] == "unverified"
    assert len(calls) == 1 and calls[0][-1] == "-version"
    assert not hasattr(executor, "_detected_mode")


@pytest.mark.asyncio
async def test_old_java_and_different_pyspark_are_actionable_warnings(setup_environment, monkeypatch):
    executor, _ = setup_environment
    monkeypatch.setattr(env.metadata, "version", lambda name: "3.5.0")

    async def old_java(self):
        return b"", b'java version "1.8.0_412"'
    monkeypatch.setattr(VersionProcess, "communicate", old_java)
    report = await env.build_environment_report(Settings(execution_mode=ExecutionMode.LOCAL), executor)
    checks = {item["id"]: item for item in report["checks"]}
    assert checks["java"]["status"] == checks["pyspark"]["status"] == "warning"
    assert checks["java"]["nextSteps"] and checks["pyspark"]["nextSteps"]
    assert not report["execution"]["localPrerequisites"]


@pytest.mark.asyncio
async def test_java_timeout_kills_the_probe_and_does_not_echo_output(setup_environment, monkeypatch):
    executor, _ = setup_environment

    class HangingProcess:
        returncode = None
        killed = False
        waited = False

        async def communicate(self):
            await asyncio.sleep(60)

        def kill(self):
            self.killed = True
            self.returncode = -9

        async def wait(self):
            self.waited = True
            return self.returncode

    process = HangingProcess()
    async def create(*args, **kwargs):
        return process
    monkeypatch.setattr(env.asyncio, "create_subprocess_exec", create)
    monkeypatch.setattr(env, "JAVA_TIMEOUT_SECONDS", 0.01)
    report = await env.build_environment_report(Settings(), executor)
    assert process.killed and process.waited
    assert report["checks"][1]["status"] == "warning"
    assert "超时" in report["checks"][1]["summary"]
    assert not report["execution"]["localPrerequisites"]


@pytest.mark.asyncio
async def test_java_unparseable_output_or_launch_error_stays_unverified(setup_environment, monkeypatch):
    executor, _ = setup_environment
    async def fail(*args, **kwargs):
        raise OSError("SECRET_TOKEN /private/user.py")
    monkeypatch.setattr(env.asyncio, "create_subprocess_exec", fail)
    report = await env.build_environment_report(Settings(), executor)
    assert report["checks"][1]["status"] == "warning"
    assert "SECRET_TOKEN" not in json.dumps(report)
    assert "/private/user.py" not in json.dumps(report)


@pytest.mark.asyncio
async def test_early_access_java_version_and_dry_run_remain_truthful(setup_environment, monkeypatch):
    executor, _ = setup_environment
    async def early_java(self):
        return b"", b'openjdk version "21-ea"'
    monkeypatch.setattr(VersionProcess, "communicate", early_java)
    report = await env.build_environment_report(Settings(execution_mode=ExecutionMode.DRY_RUN), executor)
    assert report["checks"][1]["status"] == "available"
    assert report["capabilities"][1]["availability"] == "needs_setup"
    assert "不执行 Spark" in report["execution"]["summary"]
