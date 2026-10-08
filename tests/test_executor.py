"""Executor regression tests using local Python processes and mocked Spark."""

import asyncio
import os
from pathlib import Path
import sys
from unittest.mock import AsyncMock

import pytest

from sparktutor.config.settings import DatabricksConfig, ExecutionMode, Settings
from sparktutor.engine.executor import ExecMode, ExecResult, Executor


class TestDryRun:
    @pytest.fixture
    def executor(self):
        return Executor(force_dry_run=True)

    def test_dry_run_valid_syntax(self, executor):
        result = executor._dry_run("x = 42\nprint(x)")
        assert result.success
        assert result.mode == ExecMode.DRY_RUN
        assert "Syntax OK" in result.stdout

    def test_dry_run_syntax_error(self, executor):
        result = executor._dry_run("x = ")
        assert not result.success
        assert "SyntaxError" in result.stderr

    def test_dry_run_multiline(self, executor):
        code = """
from pyspark.sql import SparkSession

spark = SparkSession.builder.appName('test').getOrCreate()
df = spark.read.csv('/data/test.csv', header=True)
df.show()
"""
        result = executor._dry_run(code)
        assert result.success


class TestExecResult:
    def test_success(self):
        r = ExecResult(mode=ExecMode.DRY_RUN, exit_code=0, stdout="ok", stderr="")
        assert r.success

    def test_failure(self):
        r = ExecResult(mode=ExecMode.DRY_RUN, exit_code=1, stdout="", stderr="error")
        assert not r.success


@pytest.mark.asyncio
async def test_detect_mode_dry_run():
    executor = Executor(force_dry_run=True)
    mode = await executor.detect_mode()
    assert mode == ExecMode.DRY_RUN


@pytest.mark.asyncio
async def test_execute_dry_run():
    executor = Executor(force_dry_run=True)
    result = await executor.execute("x = 42")
    assert result.success
    assert result.mode == ExecMode.DRY_RUN


class TestDatabricksConfig:
    def test_build_spark_remote_full(self):
        cfg = DatabricksConfig(
            host="adb-123.45.azuredatabricks.net",
            token="dapi_abc123",
            cluster_id="0101-abcdef",
        )
        url = cfg.build_spark_remote()
        assert url == (
            "sc://adb-123.45.azuredatabricks.net:443/"
            ";use_ssl=true;token=dapi_abc123"
            ";x-databricks-cluster-id=0101-abcdef"
        )

    def test_build_spark_remote_strips_trailing_slash(self):
        cfg = DatabricksConfig(
            host="adb-123.45.azuredatabricks.net/",
            token="tok",
            cluster_id="cid",
        )
        assert cfg.build_spark_remote().startswith("sc://adb-123.45.azuredatabricks.net:443/")

    def test_build_spark_remote_missing_fields(self):
        assert DatabricksConfig().build_spark_remote() is None
        assert DatabricksConfig(host="h").build_spark_remote() is None
        assert DatabricksConfig(host="h", token="t").build_spark_remote() is None

    def test_build_spark_remote_profile_only(self):
        cfg = DatabricksConfig(profile="STAGING")
        assert cfg.build_spark_remote() is None


@pytest.mark.asyncio
async def test_detect_mode_databricks():
    settings = Settings(execution_mode=ExecutionMode.DATABRICKS)
    executor = Executor(settings=settings)
    mode = await executor.detect_mode()
    assert mode == ExecMode.DATABRICKS


class TestExecResultDatabricks:
    def test_databricks_mode(self):
        r = ExecResult(mode=ExecMode.DATABRICKS, exit_code=0, stdout="ok", stderr="")
        assert r.success
        assert r.mode == ExecMode.DATABRICKS


@pytest.mark.asyncio
@pytest.mark.parametrize("exit_code", [0, 7])
async def test_stream_preserves_real_exit_code(exit_code):
    executor = Executor(settings=Settings())
    output = []
    proc = await executor._start_process(
        sys.executable, "-c",
        f"import sys; print('result'); print('diagnostic', file=sys.stderr); sys.exit({exit_code})",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    result = await executor._stream_process(proc, ExecMode.LOCAL, output.append)
    assert result.exit_code == exit_code
    assert result.success is (exit_code == 0)
    assert result.stdout == "result"
    assert result.stderr == "diagnostic"
    assert set(output) == {"result", "[stderr] diagnostic"}


@pytest.mark.asyncio
async def test_timeout_waits_for_process_exit_and_preserves_output():
    executor = Executor(settings=Settings(timeout_seconds=1))
    proc = await executor._start_process(
        sys.executable, "-c", "import time; print('started', flush=True); time.sleep(30)",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    result = await asyncio.wait_for(executor._stream_process(proc, ExecMode.LOCAL), timeout=10)
    assert result.exit_code == 124
    assert not result.success
    assert "started" in result.stdout
    assert "timed out" in result.stderr
    assert proc.returncode is not None


@pytest.mark.asyncio
async def test_timeout_includes_process_wait_after_output_is_closed():
    executor = Executor(settings=Settings(timeout_seconds=1))
    proc = await executor._start_process(
        sys.executable, "-c", "import time; time.sleep(30)",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    result = await asyncio.wait_for(executor._stream_process(proc, ExecMode.LOCAL), timeout=10)
    assert result.exit_code == 124
    assert proc.returncode is not None


@pytest.mark.asyncio
async def test_cancellation_stops_running_process():
    executor = Executor(settings=Settings())
    proc = await executor._start_process(
        sys.executable, "-c", "import time; time.sleep(30)",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    running = asyncio.create_task(executor._stream_process(proc, ExecMode.LOCAL))
    await asyncio.sleep(0)
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    assert proc.returncode is not None


@pytest.mark.asyncio
async def test_failed_probe_stops_process():
    executor = Executor(settings=Settings())
    proc = await executor._start_process(
        sys.executable, "-c", "import time; time.sleep(30)",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    with pytest.raises(asyncio.TimeoutError):
        await executor._communicate(proc, timeout=0.1)
    assert proc.returncode is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["_local_exec", "_databricks_exec"])
async def test_launch_failure_cleans_utf8_script(monkeypatch, method):
    executor = Executor(settings=Settings())
    paths = []
    code = "print('中文题面')\n"

    async def fail_to_start(*args, **kwargs):
        path = Path(args[1])
        paths.append(path)
        assert path.read_text(encoding="utf-8") == code
        if method == "_databricks_exec":
            assert args[0] == sys.executable
        raise FileNotFoundError("launcher unavailable")

    monkeypatch.setattr(executor, "_start_process", fail_to_start)
    with pytest.raises(FileNotFoundError):
        await getattr(executor, method)(code)
    assert paths
    assert all(not path.exists() and not path.parent.exists() for path in paths)


@pytest.mark.asyncio
async def test_databricks_uses_current_interpreter_and_utf8(monkeypatch):
    executor = Executor(settings=Settings())
    paths = []
    start_process = executor._start_process

    async def record_start(*args, **kwargs):
        assert args[0] == sys.executable
        paths.append(Path(args[1]))
        return await start_process(*args, **kwargs)

    monkeypatch.setattr(executor, "_start_process", record_start)
    result = await executor._databricks_exec("print('中文输出')")
    assert result.success
    assert result.stdout == "中文输出"
    assert all(not path.exists() for path in paths)


@pytest.mark.asyncio
async def test_docker_copy_failure_does_not_launch_spark_and_cleans_files(monkeypatch):
    executor = Executor(settings=Settings())
    calls = []
    paths = []

    async def start_process(*args, **kwargs):
        calls.append(args)
        if args[1] == "cp":
            paths.append(Path(args[2]))
            assert paths[0].read_text(encoding="utf-8") == "print('中文')"
        return AsyncMock()

    monkeypatch.setattr(executor, "_start_process", start_process)
    monkeypatch.setattr(executor, "_stream_process", AsyncMock(return_value=ExecResult(
        mode=ExecMode.LAKEHOUSE, exit_code=1, stdout="", stderr="copy failed",
    )))
    monkeypatch.setattr(executor, "_communicate", AsyncMock(return_value=(b"", b"")))
    result = await executor._lakehouse_exec("print('中文')")
    assert not result.success
    assert result.stderr == "copy failed"
    assert not any("/opt/spark/bin/spark-submit" in args for args in calls)
    assert calls[-1][3:5] == ("rm", "-f")
    assert all(not path.exists() for path in paths)


@pytest.mark.skipif(os.name != "nt", reason="Windows Spark launcher regression")
@pytest.mark.asyncio
async def test_windows_cmd_launcher_executes_with_spaces(monkeypatch, tmp_path):
    launcher_dir = tmp_path / "Spark installation with spaces"
    launcher_dir.mkdir()
    launcher = launcher_dir / "spark-submit.cmd"
    launcher.write_text(
        f'@echo off\r\n"{sys.executable}" "%~1"\r\nexit /b %errorlevel%\r\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(sys, "executable", str(launcher_dir / "python.exe"))
    monkeypatch.setenv("PATH", str(launcher_dir) + os.pathsep + os.environ.get("PATH", ""))
    executor = Executor(settings=Settings())
    assert executor._spark_submit() == str(launcher)
    result = await executor._local_exec("print('中文练习')")
    assert result.success
    assert result.stdout == "中文练习"


def test_spark_launcher_prefers_configured_interpreter_directory(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "executable", str(tmp_path / "python.exe"))
    name = "spark-submit.cmd" if os.name == "nt" else "spark-submit"
    launcher = tmp_path / name
    launcher.write_text("test launcher", encoding="utf-8")
    monkeypatch.setattr("sparktutor.engine.executor.shutil.which", lambda _: "other-environment-spark")
    assert Executor._spark_submit() == str(launcher)


def test_spark_launcher_falls_back_to_path(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "executable", str(tmp_path / "python.exe"))
    monkeypatch.setattr("sparktutor.engine.executor.shutil.which", lambda _: "spark-on-path")
    assert Executor._spark_submit() == "spark-on-path"


@pytest.mark.asyncio
@pytest.mark.parametrize("custom_python", [None, "explicit-pyspark-python"])
async def test_local_spark_python_defaults_preserve_user_configuration(monkeypatch, custom_python):
    executor = Executor(settings=Settings())
    for name in ("PYSPARK_PYTHON", "PYSPARK_DRIVER_PYTHON"):
        monkeypatch.delenv(name, raising=False)
    if custom_python:
        monkeypatch.setenv("PYSPARK_PYTHON", custom_python)
        monkeypatch.setenv("PYSPARK_DRIVER_PYTHON", "explicit-driver-python")
    start = AsyncMock(return_value=object())
    monkeypatch.setattr(executor, "_start_process", start)
    monkeypatch.setattr(executor, "_stream_process", AsyncMock(return_value=ExecResult(
        ExecMode.LOCAL, 0, "", "",
    )))
    assert (await executor._local_exec("print('exercise')")).success
    env = start.call_args.kwargs["env"]
    assert env["PYSPARK_PYTHON"] == (custom_python or sys.executable)
    assert env["PYSPARK_DRIVER_PYTHON"] == (
        "explicit-driver-python" if custom_python else sys.executable
    )
