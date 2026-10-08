"""Spark code execution engine with three-mode graceful degradation."""

from __future__ import annotations

import asyncio
import os
import shutil
import signal
import sys
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

from sparktutor.config.settings import Settings


class ExecMode(str, Enum):
    LAKEHOUSE = "lakehouse"
    LOCAL = "local"
    DRY_RUN = "dry_run"
    DATABRICKS = "databricks"


@dataclass
class ExecResult:
    mode: ExecMode
    exit_code: int
    stdout: str
    stderr: str
    validation_passed: bool = False

    @property
    def success(self) -> bool:
        return self.exit_code == 0


class Executor:
    def __init__(self, settings: Optional[Settings] = None, force_dry_run: bool = False):
        self.settings = settings or Settings.load()
        self._force_dry_run = force_dry_run
        self._detected_mode: Optional[ExecMode] = None

    @staticmethod
    def _spark_submit() -> str:
        # PySpark installs a .cmd launcher on Windows; the extensionless file is
        # a POSIX shell script and cannot be executed there.
        name = "spark-submit.cmd" if os.name == "nt" else "spark-submit"
        # VS Code can select a virtualenv interpreter without activating its
        # shell. Its scripts directory is then absent from the inherited PATH.
        interpreter_launcher = Path(sys.executable).parent / name
        if interpreter_launcher.is_file():
            return str(interpreter_launcher)
        return shutil.which(name) or name

    @staticmethod
    async def _start_process(*args: str, **kwargs) -> asyncio.subprocess.Process:
        if os.name != "nt":
            # Spark launches Java/Python children. Give executions their own
            # process group so a timeout can stop the complete local job.
            kwargs["start_new_session"] = True
        return await asyncio.create_subprocess_exec(*args, **kwargs)

    @staticmethod
    async def _stop_process(proc: asyncio.subprocess.Process) -> None:
        if proc.returncode is None:
            try:
                if os.name == "nt":
                    # Killing only spark-submit.cmd would leave its JVM alive.
                    killer = await asyncio.create_subprocess_exec(
                        "taskkill", "/PID", str(proc.pid), "/T", "/F",
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
                    try:
                        await asyncio.wait_for(killer.wait(), timeout=5)
                    except asyncio.TimeoutError:
                        killer.kill()
                        await killer.wait()
                elif os.getpgid(proc.pid) == proc.pid:
                    os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                pass
            if proc.returncode is None:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
        await proc.wait()

    async def _communicate(
        self, proc: asyncio.subprocess.Process, timeout: float = 5,
    ) -> tuple[bytes, bytes]:
        try:
            return await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except BaseException:
            await self._stop_process(proc)
            raise

    async def detect_mode(self) -> ExecMode:
        """Detect the best available execution mode."""
        if self._force_dry_run:
            return ExecMode.DRY_RUN

        configured = self.settings.execution_mode
        if configured.value != "auto":
            return ExecMode(configured.value)

        # Check for lakehouse-stack docker container
        try:
            proc = await self._start_process(
                "docker", "ps", "--filter",
                f"name={self.settings.docker.container_name}",
                "--format", "{{.Names}}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await self._communicate(proc)
            if proc.returncode == 0 and self.settings.docker.container_name in stdout.decode().splitlines():
                return ExecMode.LAKEHOUSE
        except (OSError, asyncio.TimeoutError):
            pass

        # Check for Databricks Spark Connect
        if os.environ.get("SPARK_REMOTE") or self.settings.databricks.build_spark_remote():
            return ExecMode.DATABRICKS

        # Check for local spark-submit
        try:
            proc = await self._start_process(
                self._spark_submit(), "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await self._communicate(proc)
            if proc.returncode == 0:
                return ExecMode.LOCAL
        except (OSError, asyncio.TimeoutError):
            pass

        return ExecMode.DRY_RUN

    async def execute(
        self,
        code: str,
        on_output: Optional[Callable[[str], None]] = None,
    ) -> ExecResult:
        """Execute PySpark code and return results."""
        mode = self._detected_mode or await self.detect_mode()
        self._detected_mode = mode

        if mode == ExecMode.DRY_RUN:
            return self._dry_run(code)
        elif mode == ExecMode.LAKEHOUSE:
            return await self._lakehouse_exec(code, on_output)
        elif mode == ExecMode.DATABRICKS:
            return await self._databricks_exec(code, on_output)
        else:
            return await self._local_exec(code, on_output)

    def _dry_run(self, code: str) -> ExecResult:
        """Syntax check only — no Spark execution."""
        import ast
        try:
            ast.parse(code)
            return ExecResult(
                mode=ExecMode.DRY_RUN, exit_code=0,
                stdout="[dry-run] Syntax OK", stderr="",
            )
        except SyntaxError as e:
            return ExecResult(
                mode=ExecMode.DRY_RUN, exit_code=1,
                stdout="", stderr=f"SyntaxError: {e.msg} (line {e.lineno})",
            )

    async def _lakehouse_exec(
        self, code: str, on_output: Optional[Callable[[str], None]] = None,
    ) -> ExecResult:
        """Execute via docker exec on spark-master-41."""
        container = self.settings.docker.container_name

        with tempfile.TemporaryDirectory(prefix="sparktutor_") as tmp_dir:
            tmp_path = Path(tmp_dir) / "exercise.py"
            tmp_path.write_text(code, encoding="utf-8")
            remote_path = f"/tmp/{Path(tmp_dir).name}.py"
            try:
                cp_proc = await self._start_process(
                    "docker", "cp", str(tmp_path), f"{container}:{remote_path}",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                copied = await self._stream_process(cp_proc, ExecMode.LAKEHOUSE)
                if not copied.success:
                    return copied

                proc = await self._start_process(
                    "docker", "exec", container,
                    "/opt/spark/bin/spark-submit",
                    "--master", self.settings.docker.spark_master_url,
                    remote_path,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                return await self._stream_process(proc, ExecMode.LAKEHOUSE, on_output)
            finally:
                # The remote name is generated locally and unique to this run.
                try:
                    cleanup = await self._start_process(
                        "docker", "exec", container, "rm", "-f", remote_path,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    await self._communicate(cleanup)
                except (OSError, asyncio.TimeoutError):
                    pass

    async def _local_exec(
        self, code: str, on_output: Optional[Callable[[str], None]] = None,
    ) -> ExecResult:
        """Execute via local spark-submit."""
        with tempfile.TemporaryDirectory(prefix="sparktutor_") as tmp_dir:
            tmp_path = Path(tmp_dir) / "exercise.py"
            tmp_path.write_text(code, encoding="utf-8")
            env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
            env.setdefault("PYSPARK_PYTHON", sys.executable)
            env.setdefault("PYSPARK_DRIVER_PYTHON", sys.executable)
            proc = await self._start_process(
                self._spark_submit(), str(tmp_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            return await self._stream_process(proc, ExecMode.LOCAL, on_output)

    async def _databricks_exec(
        self, code: str, on_output: Optional[Callable[[str], None]] = None,
    ) -> ExecResult:
        """Execute Spark Connect in the backend's configured Python environment."""
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        # Set SPARK_REMOTE if explicit config provides it
        remote_url = self.settings.databricks.build_spark_remote()
        if remote_url:
            env["SPARK_REMOTE"] = remote_url
        # Set profile if configured
        if self.settings.databricks.profile:
            env["DATABRICKS_CONFIG_PROFILE"] = self.settings.databricks.profile

        with tempfile.TemporaryDirectory(prefix="sparktutor_") as tmp_dir:
            tmp_path = Path(tmp_dir) / "exercise.py"
            tmp_path.write_text(code, encoding="utf-8")
            proc = await self._start_process(
                sys.executable, str(tmp_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            return await self._stream_process(proc, ExecMode.DATABRICKS, on_output)

    async def _stream_process(
        self,
        proc: asyncio.subprocess.Process,
        mode: ExecMode,
        on_output: Optional[Callable[[str], None]] = None,
    ) -> ExecResult:
        """Stream stdout/stderr from a subprocess, calling on_output for each line."""
        stdout_lines: list[str] = []
        stderr_lines: list[str] = []

        async def _read_stream(
            stream: Optional[asyncio.StreamReader], lines: list[str], is_stderr: bool = False,
        ):
            if stream is None:
                return
            while True:
                line = await stream.readline()
                if not line:
                    break
                decoded = line.decode("utf-8", errors="replace").rstrip("\r\n")
                lines.append(decoded)
                if on_output:
                    prefix = "[stderr] " if is_stderr else ""
                    on_output(f"{prefix}{decoded}")

        timed_out = False
        tasks = asyncio.gather(
            _read_stream(proc.stdout, stdout_lines),
            _read_stream(proc.stderr, stderr_lines, is_stderr=True),
            proc.wait(),
        )
        try:
            await asyncio.wait_for(
                tasks,
                timeout=self.settings.timeout_seconds,
            )
        except asyncio.TimeoutError:
            timed_out = True
            await self._stop_process(proc)
            stderr_lines.append(f"[sparktutor] Execution timed out after {self.settings.timeout_seconds}s")
        except BaseException:
            tasks.cancel()
            await self._stop_process(proc)
            await asyncio.gather(tasks, return_exceptions=True)
            raise

        return ExecResult(
            mode=mode,
            exit_code=124 if timed_out else (proc.returncode if proc.returncode is not None else 1),
            stdout="\n".join(stdout_lines),
            stderr="\n".join(stderr_lines),
        )
