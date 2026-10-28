"""One disposable Linux container per account job, with shared bounded capacity."""

from __future__ import annotations

import asyncio
import codecs
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import hashlib
from pathlib import Path
import shutil
import tempfile
import threading
import uuid

from sparktutor.config.settings import IsolatedExecutionConfig, Settings
from .executor import ExecMode, ExecResult, Executor


@dataclass(frozen=True)
class ExecutionIdentity:
    owner_id: str
    session_id: str
    course_id: str
    lesson_id: str
    step_id: str
    operation_id: str
    context_token: str

    def public(self):
        return {"userId": self.owner_id, "sessionId": self.session_id,
                "courseId": self.course_id, "lessonId": self.lesson_id,
                "taskId": f"{self.lesson_id}:{self.step_id}", "stepId": self.step_id,
                "operationId": self.operation_id, "contextToken": self.context_token}


_IDENTITY = ContextVar("sparktutor_isolated_identity", default=None)


@contextmanager
def execution_scope(identity):
    token = _IDENTITY.set(identity)
    try:
        yield
    finally:
        _IDENTITY.reset(token)


class OutputLimitExceeded(Exception):
    pass


class IsolatedJobPool:
    """Thread-safe capacity across all per-account WebRuntime event loops."""

    def __init__(self, configuration: IsolatedExecutionConfig, root: Path):
        self.configuration = configuration.model_copy(deep=True)
        self.root = Path(root).resolve()
        self._lock = threading.Lock()
        self._active = {}

    def reserve(self, owner_id):
        with self._lock:
            if len(self._active) >= self.configuration.max_concurrent_jobs:
                return None
            if sum(item == owner_id for item in self._active.values()) >= self.configuration.max_jobs_per_user:
                return None
            job_id = "sparktutor-job-" + uuid.uuid4().hex
            self._active[job_id] = owner_id
            return job_id

    def release(self, job_id):
        with self._lock:
            self._active.pop(job_id, None)

    def capacity(self):
        with self._lock:
            return {"activeJobs": len(self._active),
                    "maxConcurrentJobs": self.configuration.max_concurrent_jobs,
                    "maxJobsPerUser": self.configuration.max_jobs_per_user}

    async def _control(self, *arguments, timeout=5):
        process = None
        try:
            process = await Executor._start_process(
                self.configuration.docker_executable, *arguments,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
            return process.returncode, stdout.decode("utf-8", "replace"), stderr.decode("utf-8", "replace")
        except (OSError, asyncio.TimeoutError):
            return 125, "", "Docker execution service unavailable"
        finally:
            if process is not None and process.returncode is None:
                await Executor._stop_process(process)

    async def status(self):
        result = {"enabled": self.configuration.enabled, "ready": False,
                  "mode": "isolated", "image": self.configuration.image, **self.capacity()}
        if not self.configuration.enabled:
            return {**result, "message": "管理员尚未启用隔离执行服务。"}
        code, operating_system, _ = await self._control("version", "--format", "{{.Server.Os}}")
        if code != 0 or operating_system.strip() != "linux":
            return {**result, "message": "Docker Linux 执行服务未就绪，请管理员启动服务。"}
        code, image_os, _ = await self._control("image", "inspect", "--format", "{{.Os}}", self.configuration.image)
        if code != 0 or image_os.strip() != "linux":
            return {**result, "message": "隔离执行镜像未就绪，请管理员构建课程镜像。"}
        return {**result, "ready": True, "message": "账号隔离执行服务已就绪。"}

    async def remove(self, job_id):
        code, _, error = await self._control("rm", "--force", job_id)
        return code == 0 or "No such container" in error or "No such object" in error

    def command(self, job_id, directory, owner_id):
        c = self.configuration
        owner_hash = hashlib.sha256(owner_id.encode()).hexdigest()[:20]
        tmpfs = f"/tmp:rw,nosuid,nodev,size={c.tmpfs_mb}m,mode=1777"
        return [c.docker_executable, "run", "--rm", "--pull=never", "--init", "--name", job_id,
                "--label", "sparktutor.job=" + job_id, "--label", "sparktutor.owner=" + owner_hash,
                "--network", "none", "--user", "10001:10001", "--read-only",
                "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                "--cpus", str(c.cpus), "--memory", c.memory, "--memory-swap", c.memory,
                "--pids-limit", str(c.pids_limit), "--ulimit", "nofile=1024:1024",
                "--tmpfs", tmpfs, "--workdir", "/tmp",
                "--mount", f"type=bind,source={directory},target=/job,readonly",
                "--env", "HOME=/tmp", "--env", "SPARK_LOCAL_DIRS=/tmp/spark",
                "--env", "SPARK_LOCAL_IP=127.0.0.1", "--env", "PYSPARK_PYTHON=python3",
                "--env", "PYSPARK_DRIVER_PYTHON=python3", "--env", "PYTHONIOENCODING=utf-8",
                "--env", "OMP_NUM_THREADS=1", "--env", "OPENBLAS_NUM_THREADS=1",
                "--env", "SPARK_SUBMIT_OPTS=-XX:ActiveProcessorCount=" + str(max(1, int(c.cpus))),
                "--entrypoint", "python3", c.image, "/job/exercise.py"]


class IsolatedExecutor(Executor):
    """Bound to an authenticated account; browser inputs never select the owner."""

    def __init__(self, settings: Settings, pool: IsolatedJobPool, owner_id: str):
        super().__init__(settings=settings)
        self.pool = pool
        self.owner_id = owner_id
        self.execution_metadata = {}

    async def detect_mode(self):
        return ExecMode.ISOLATED

    async def _cleanup_job(self, job_id, metadata, process, directory, attempted, result):
        """Complete one job's cleanup independently of request cancellation."""
        process_stopped = process is None or process.returncode is not None
        container_removed = not attempted
        directory_removed = directory is None
        try:
            if not process_stopped:
                try:
                    await self._stop_process(process)
                    process_stopped = True
                except Exception:
                    pass
            if attempted:
                try:
                    container_removed = await self.pool.remove(job_id)
                except Exception:
                    container_removed = False
            if container_removed and directory is not None:
                try:
                    if directory.resolve().parent != self.pool.root:
                        raise RuntimeError("Unexpected isolated job directory")
                    script = directory / "exercise.py"
                    if script.exists():
                        script.chmod(0o644)
                    shutil.rmtree(directory)
                    directory_removed = True
                except Exception:
                    directory_removed = False
        finally:
            verified = process_stopped and container_removed and directory_removed
            try:
                metadata["cleanupVerified"] = verified
                if not verified and result is not None:
                    result.exit_code = 125
                    result.failure_kind = "infrastructure"
                    result.stderr += "\n无法确认隔离作业已清理，当前结果不作为课程通过。"
                if verified:
                    self.pool.release(job_id)
                # Unconfirmed cleanup retains its reservation until restart.
            finally:
                self.execution_metadata = {}

    async def _wait_for_cleanup(self, *arguments):
        cleanup = asyncio.create_task(self._cleanup_job(*arguments))
        cancelled = False
        while True:
            try:
                await asyncio.shield(cleanup)
                break
            except asyncio.CancelledError:
                if cleanup.cancelled():
                    raise
                # A second cancellation must not abandon the rest of cleanup
                # after Docker removal (directory, quota, and metadata).
                cancelled = True
        if cancelled:
            raise asyncio.CancelledError

    async def _capture(self, process, on_output, buffers):
        total = 0

        async def read(stream, key):
            nonlocal total
            decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
            while True:
                chunk = await stream.read(4096)
                if not chunk:
                    return
                remaining = self.pool.configuration.output_limit_bytes - total
                if remaining > 0:
                    kept = chunk[:remaining]
                    total += len(kept)
                    buffers[key].append(kept)
                    if on_output:
                        prefix = "[stderr] " if key == "stderr" else ""
                        for line in decoder.decode(kept).splitlines():
                            on_output(prefix + line)
                if len(chunk) > remaining:
                    raise OutputLimitExceeded

        tasks = [asyncio.create_task(read(process.stdout, "stdout")),
                 asyncio.create_task(read(process.stderr, "stderr")),
                 asyncio.create_task(process.wait())]
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def execute(self, code, on_output=None):
        identity = _IDENTITY.get()
        if (identity is None or identity.owner_id != self.owner_id or not identity.step_id
                or not identity.operation_id):
            return ExecResult(ExecMode.ISOLATED, 125, "", "隔离作业缺少当前账号和题目上下文。",
                              failure_kind="infrastructure")
        metadata = identity.public()
        job_id = self.pool.reserve(self.owner_id)
        if job_id is None:
            return ExecResult(ExecMode.ISOLATED, 75, "", "执行资源正忙，请等待当前作业完成后重试。",
                              execution=metadata, failure_kind="infrastructure")
        metadata["jobId"] = job_id
        metadata["codeSha256"] = hashlib.sha256(code.encode("utf-8")).hexdigest()
        self.execution_metadata = metadata
        process = None
        directory = None
        attempted = False
        result = None
        buffers = {"stdout": [], "stderr": []}
        try:
            readiness = await self.pool.status()
            if not readiness["ready"]:
                result = ExecResult(ExecMode.ISOLATED, 125, "", readiness["message"],
                                    execution=metadata, failure_kind="infrastructure")
                return result
            self.pool.root.mkdir(parents=True, exist_ok=True)
            directory = Path(tempfile.mkdtemp(prefix="job-", dir=self.pool.root))
            directory.chmod(0o755)
            script = directory / "exercise.py"
            script.write_text(code, encoding="utf-8")
            script.chmod(0o444)
            attempted = True
            process = await self._start_process(
                *self.pool.command(job_id, directory, self.owner_id),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            try:
                await asyncio.wait_for(self._capture(process, on_output, buffers),
                                       timeout=self.pool.configuration.timeout_seconds)
                exit_code = process.returncode
                failure = "infrastructure" if exit_code in {125, 126, 127, 137} else ""
            except asyncio.TimeoutError:
                await self._stop_process(process)
                exit_code, failure = 124, "infrastructure"
                buffers["stderr"].append("执行超时，隔离作业已停止，课程测试未完成。".encode())
            except OutputLimitExceeded:
                await self._stop_process(process)
                exit_code, failure = 126, "infrastructure"
                buffers["stderr"].append("执行输出超过上限，隔离作业已停止，课程测试未完成。".encode())
            result = ExecResult(ExecMode.ISOLATED, exit_code,
                                b"".join(buffers["stdout"]).decode("utf-8", "replace"),
                                b"".join(buffers["stderr"]).decode("utf-8", "replace"),
                                execution=metadata, failure_kind=failure)
            return result
        except OSError:
            result = ExecResult(ExecMode.ISOLATED, 125, "", "隔离执行服务启动失败，请管理员检查 Docker 与镜像。",
                                execution=metadata, failure_kind="infrastructure")
            return result
        finally:
            await self._wait_for_cleanup(job_id, metadata, process, directory, attempted, result)
