"""Windows launchers route the ordinary account site to its configured runtime."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which("powershell.exe")
pytestmark = pytest.mark.skipif(os.name != "nt" or POWERSHELL is None,
                                reason="Windows PowerShell launcher integration")


@pytest.fixture
def launcher(tmp_path):
    directory = tmp_path / "project with spaces"
    directory.mkdir()
    for name in ("start-web.ps1", "start-web-wsl.ps1"):
        shutil.copyfile(ROOT / name, directory / name)
    log = tmp_path / "calls.jsonl"
    helper = tmp_path / "fake_runtime.py"
    helper.write_text('''import json,os,sys
from pathlib import PureWindowsPath
args=sys.argv[1:]
if os.environ.get("SPARKTUTOR_TEST_STDERR_WARNING"):
    print("wsl: benign startup notice",file=sys.stderr)
with open(os.environ["SPARKTUTOR_TEST_RUNTIME_LOG"],"a",encoding="utf-8") as stream:
    stream.write(json.dumps(args)+"\\n")
if "wslpath" in args:
    path=PureWindowsPath(args[-1])
    print("/mnt/"+path.drive[0].lower()+"/"+"/".join(path.parts[1:]))
elif "image" in args and "inspect" in args:
    if os.environ.get("SPARKTUTOR_TEST_IMAGE_MISSING"): sys.exit(1)
    print("linux")
elif "version" in args and "--format" in args:
    print("linux")
''', encoding="utf-8")
    fake = tmp_path / "fake-runtime.cmd"
    fake.write_text(f'@echo off\r\n"{sys.executable}" "{helper}" %*\r\n', encoding="utf-8")
    configuration = {"runtime": "wsl", "pythonPath": "Z:\\missing-native-python.exe",
                     "wslDistribution": "Ubuntu", "wslPythonPath": "/opt/test-python",
                     "wslProjectPath": "/fake/project with spaces", "wslExecutable": str(fake),
                     "wslDockerHost": "unix:///run/sparktutor-docker.sock",
                     "wslDockerService": "sparktutor-docker.service"}
    data = directory / ".sparktutor-web"
    data.mkdir()
    (data / "runtime.json").write_text(json.dumps(configuration), encoding="utf-8")
    return directory, log, fake


def run(launcher, *arguments, **environment):
    directory, log, fake = launcher
    result = subprocess.run([POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                             str(directory / "start-web.ps1"), *arguments],
                            cwd=directory, env={**os.environ, "SPARKTUTOR_TEST_RUNTIME_LOG": str(log), **environment},
                            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=45)
    calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []
    return result, calls


def web_call(calls):
    return next(args for args in reversed(calls)
                if "-m" in args and args[args.index("-m") + 1] == "sparktutor")


def test_normal_launcher_uses_saved_wsl_account_runtime_and_keeps_the_data_directory(launcher):
    result, calls = run(launcher, "-Port", "8890")
    assert result.returncode == 0, result.stdout + result.stderr
    final = web_call(calls)
    assert "--isolated-execution" in final and "--trusted-local" not in final
    assert final[final.index("--port") + 1] == "8890"
    assert final[final.index("--cd") + 1] == "/fake/project with spaces"
    assert final[final.index("--data-dir") + 1].endswith("project with spaces/.sparktutor-web")
    assert "DOCKER_HOST=unix:///run/sparktutor-docker.sock" in final
    assert "SPARKTUTOR_ISOLATED_DOCKER_EXECUTABLE=/usr/bin/docker" in final
    assert any("systemctl" in args and "sparktutor-docker.service" in args for args in calls)


def test_explicit_native_interpreter_keeps_windows_startup_available(launcher):
    result, calls = run(launcher, "-Runtime", "Windows", "-PythonPath", str(launcher[2]))
    assert result.returncode == 0, result.stdout + result.stderr
    final = web_call(calls)
    assert "--distribution" not in final and "--isolated-execution" not in final
    assert not any("systemctl" in args for args in calls)


def test_explicit_syntax_mode_does_not_start_or_require_the_docker_service(launcher):
    result, calls = run(launcher, "-DryRun")
    assert result.returncode == 0, result.stdout + result.stderr
    final = web_call(calls)
    assert "--dry-run" in final and "--isolated-execution" not in final
    assert not any("systemctl" in args or "inspect" in args for args in calls)


def test_missing_executor_image_stops_instead_of_silently_launching_syntax_only(launcher):
    result, calls = run(launcher, SPARKTUTOR_TEST_IMAGE_MISSING="1")
    assert result.returncode != 0
    assert "executor image is not ready" in result.stdout + result.stderr
    assert not any("-m" in args for args in calls)


def test_new_computer_wsl_defaults_use_the_project_venv_and_standard_socket(launcher):
    directory = launcher[0]
    configuration = {"runtime": "wsl", "wslExecutable": str(launcher[2])}
    (directory / ".sparktutor-web/runtime.json").write_text(json.dumps(configuration), encoding="utf-8")
    result, calls = run(launcher)
    assert result.returncode == 0, result.stdout + result.stderr
    final = web_call(calls)
    assert any(value.endswith("project with spaces/.venv/bin/python") for value in final)
    assert "DOCKER_HOST=unix:///var/run/docker.sock" in final
    assert "--isolated-execution" in final
    assert not any("systemctl" in args for args in calls)


def test_multiple_matching_wsl_applications_use_one_executable_source(launcher):
    directory, _, original = launcher
    entries = []
    for name in ("first-runtime", "second-runtime"):
        folder = directory.parent / name
        folder.mkdir()
        shutil.copyfile(original, folder / "duplicate-runtime.cmd")
        entries.append(str(folder))
    path = directory / ".sparktutor-web/runtime.json"
    configuration = json.loads(path.read_text(encoding="utf-8"))
    configuration["wslExecutable"] = "duplicate-runtime.cmd"
    path.write_text(json.dumps(configuration), encoding="utf-8")
    result, calls = run(launcher, PATH=os.pathsep.join([*entries, os.environ["PATH"]]))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "--isolated-execution" in web_call(calls)


def test_benign_wsl_stderr_with_success_status_does_not_abort_startup(launcher):
    result, calls = run(launcher, SPARKTUTOR_TEST_STDERR_WARNING="1")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "--isolated-execution" in web_call(calls)
