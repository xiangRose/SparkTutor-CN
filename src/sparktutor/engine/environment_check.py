"""Read-only prerequisites inspection; never starts Spark or contacts a model.

Installed-package metadata and launcher presence are useful setup evidence, not
proof that a Spark job can run. Remote endpoints and Docker daemons are not
contacted, and credential values never become part of this report.
"""

from __future__ import annotations

import asyncio
from importlib import metadata
import os
from pathlib import Path
import re
import shutil
import sys

from sparktutor.config.settings import Settings
from sparktutor.engine.executor import Executor


JAVA_TIMEOUT_SECONDS = 5
SPARK_BASELINE = "4.1.1"


def _check(identifier: str, title: str, status: str, summary: str,
           details: list[str] | None = None, next_steps: list[str] | None = None) -> dict:
    return {"id": identifier, "title": title, "status": status, "summary": summary,
            "details": details or [], "nextSteps": next_steps or []}


async def _java_check() -> dict:
    java_home = os.environ.get("JAVA_HOME", "")
    filename = "java.exe" if os.name == "nt" else "java"
    executable = str(Path(java_home) / "bin" / filename) if java_home else shutil.which(filename)
    details = [f"JAVA_HOME：{java_home}" if java_home else "JAVA_HOME：未设置；将使用 PATH 中的 Java。"]
    if not executable or not Path(executable).is_file():
        return _check("java", "Java / JAVA_HOME", "missing", "未找到可用的 Java 启动程序。", details,
                      ["安装 Java 17 或更高版本；将 JAVA_HOME 指向 Java 安装目录，然后重载窗口。"])
    details.append(f"Java 路径：{executable}")
    process = None
    try:
        process = await asyncio.create_subprocess_exec(
            executable, "-version", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=JAVA_TIMEOUT_SECONDS)
        # Show only the parsed version, never raw process output or exceptions.
        version = re.search(r'(?:version\s+"|openjdk\s+)(\d+(?:[.\w+-]*))',
                            (stdout + stderr).decode("utf-8", errors="replace"))
        if process.returncode == 0 and version:
            value = version[1]
            major = int(re.match(r"\d+", value)[0])
            if major == 1 and "." in value:
                major = int(value.split(".")[1].split("_")[0])
            details.append(f"Java 版本：{value}")
            if major >= 17:
                return _check("java", "Java / JAVA_HOME", "available", "检测到 Java 17+。", details)
            return _check("java", "Java / JAVA_HOME", "warning", "Java 版本低于课程所需的 17。", details,
                          ["安装 Java 17 或更高版本，并更新 JAVA_HOME 后重载窗口。"])
        return _check("java", "Java / JAVA_HOME", "warning", "Java 启动检查未成功或版本无法确认。", details,
                      ["检查 Java 安装和 JAVA_HOME；在本机终端确认 java -version 后重载窗口。"])
    except asyncio.TimeoutError:
        return _check("java", "Java / JAVA_HOME", "warning", "Java 版本检查超时（5 秒）。", details,
                      ["检查 Java 安装和 JAVA_HOME，修复后刷新检查。"])
    except OSError:
        return _check("java", "Java / JAVA_HOME", "warning", "Java 启动程序无法执行。", details,
                      ["检查 Java 安装和执行权限，修复后重载窗口。"])
    finally:
        if process is not None and process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.wait()


def _pyspark_check() -> dict:
    try:
        version = metadata.version("pyspark")
    except metadata.PackageNotFoundError:
        return _check("pyspark", "PySpark", "missing", "当前后端 Python 未发现 PySpark 安装元数据。", [],
                      ['在下方显示的 Python 环境安装项目 Spark 依赖：python -m pip install -e ".[spark]"，然后重载窗口。'])
    if version != SPARK_BASELINE:
        return _check("pyspark", "PySpark", "warning", f"发现 PySpark {version}；课程基线为 {SPARK_BASELINE}。",
                      ["仅检查安装元数据，尚未导入 PySpark 或运行课程测试。"],
                      ['使用下方显示的 Python 安装项目锁定的 Spark 依赖：python -m pip install -e ".[spark]"。'])
    return _check("pyspark", "PySpark", "available", f"发现 PySpark {version} 安装元数据。",
                  ["尚未导入 PySpark；真实执行仍需课程运行验证。"])


async def build_environment_report(settings: Settings, executor: Executor | None = None) -> dict:
    """Inspect the active backend interpreter without jobs, writes, or networking.

    ``executor`` is optional and is consulted only for its launcher resolver.
    ``detect_mode`` must not be called here: auto-detection can contact Docker
    and launch Spark, and belongs to explicit code execution instead.
    """
    configured = settings.execution_mode.value
    python_check = _check("python", "Python 后端", "available", "当前 Python 后端已启动。",
                          [f"实际解释器：{sys.executable}", f"Python 版本：{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
                           f"SparkTutor 模块位置：{Path(__file__).resolve().parents[1]}"])
    pyspark = _pyspark_check()
    launcher = (executor or Executor(settings=settings))._spark_submit()
    found_launcher = str(Path(launcher)) if Path(launcher).is_file() else shutil.which(launcher)
    spark_submit = _check("sparkSubmit", "spark-submit", "available" if found_launcher else "missing",
                          "已找到启动程序；尚未执行。" if found_launcher else "未找到 spark-submit 启动程序。",
                          [f"启动程序：{found_launcher}"] if found_launcher else [],
                          [] if found_launcher else ['在当前后端 Python 环境安装项目 Spark 依赖：python -m pip install -e ".[spark]"，然后重载窗口。'])
    java = await _java_check()
    local_ready = all(item["status"] == "available" for item in (pyspark, spark_submit, java))
    mode_next: list[str] = []
    if configured == "local":
        summary = ("本地 Spark 的静态前置条件已找到；尚未验证 Spark 启动或课程运行。" if local_ready else
                   "已选择本地 Spark；请先补齐下列缺项。代码运行可能失败，综合练习尚不能确认可用。")
        if not local_ready:
            mode_next.append("先完成 Java、PySpark 和 spark-submit 的缺项操作，再运行一题验证真实执行。")
    elif configured == "lakehouse":
        summary = "已选择 Docker 湖仓模式；本次未连接 Docker 或检查容器。"
        mode_next.append("准备并启动课程湖仓容器，再显式运行课程验证；本机 PySpark 缺失不直接说明容器不可用。")
    elif configured == "databricks":
        summary = "已选择 Databricks 模式；本次未连接远端，也未验证权限或集群。"
        configured_remote = bool(os.environ.get("SPARK_REMOTE") or settings.databricks.profile or
                                 (settings.databricks.host and settings.databricks.token and settings.databricks.cluster_id))
        mode_next.append("已发现远端配置；请通过显式运行验证连接。" if configured_remote else
                         "在执行模式设置中配置 Databricks 连接信息或配置档，再显式运行验证连接。")
    elif configured == "dry_run":
        summary = "当前仅检查 Python 语法，不执行 Spark，不能通过综合练习的真实课程测试。"
        mode_next.append("需要真实执行时，配置本地 Spark 或其他支持的执行模式。")
    else:
        summary = "自动模式会在运行时选择环境；本次未探测 Docker、远端或 Spark 启动，未确定实际执行模式。"
        mode_next.append("可以先运行一题查看实际执行模式；仅语法检查不代表综合练习通过。")
        if not local_ready:
            mode_next.append("若计划使用本地 Spark，请先补齐下列本地前置条件。")
    if sys.platform == "win32" and configured in {"local", "auto"}:
        mode_next.append("原生 Windows 仍需验证 Spark/Java/Hadoop 组合；若出现 Python worker 或 Hadoop 兼容错误，可在 WSL2、Linux 或项目 Dev Container 中重试真实运行。")
    spark_available = "needs_setup" if configured == "dry_run" or (configured == "local" and not local_ready) else "unverified"
    return {
        "schemaVersion": 1, "staticOnly": True, "sparkVerified": False,
        "backend": {"available": True, "pythonPath": sys.executable,
                    "summary": f"Python 后端已响应；SparkTutor 模块位置：{Path(__file__).resolve().parents[1]}"},
        "checks": [python_check, java, pyspark, spark_submit],
        "execution": {"configuredMode": configured, "localPrerequisites": local_ready,
                      "summary": summary, "nextSteps": mode_next},
        "capabilities": [
            {"id": "lessons", "title": "课程讲解、选择题与本地短代码检查", "availability": "available",
             "detail": "可开始学习；需要 AI 的代码评审取决于单独的 AI 配置。"},
            {"id": "spark", "title": "真实 Spark 运行与综合题判题", "availability": spark_available,
             "detail": summary},
        ],
    }
