"""Smoke-test an installed wheel with python -I, without npm, Spark or AI.

Run after installing the wheel into a clean environment. The package must be
loaded from site-packages, not an editable checkout or inherited PYTHONPATH.
"""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import threading
from urllib.request import Request, urlopen

import sparktutor
from sparktutor.config.settings import ExecutionMode, Settings
from sparktutor.web.server import WebServer


def main() -> None:
    package = Path(sparktutor.__file__).resolve().parent
    assert {"site-packages", "dist-packages"}.intersection(package.parts), "Use a normal wheel installation, not an editable source checkout"
    with tempfile.TemporaryDirectory(prefix="sparktutor-wheel-smoke-") as temporary:
        server = WebServer(("127.0.0.1", 0), Settings(data_dir=Path(temporary), execution_mode=ExecutionMode.DRY_RUN))
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            def get(path):
                with urlopen(base + path, timeout=10) as response:
                    assert response.status == 200
                    return response.read()

            assert b"__API_TOKEN__" not in get("/")
            assets = (
                "/app.js", "/style.css", "/features.js", "/features.css", "/settings.js",
                "/settings.css", "/vendor/editor.bundle.js", "/vendor/EDITOR-LICENSES.txt",
            )
            for path in assets:
                assert get(path), f"Empty or missing installed browser resource: {path}"

            def call(method, params=None):
                request = Request(base + "/api/rpc", data=json.dumps({"method": method, "params": params or {}}).encode(), headers={
                    "Content-Type": "application/json", "X-SparkTutor-Token": server.runtime.token,
                    "X-SparkTutor-Session": "installed-wheel-smoke-session",
                })
                with urlopen(request, timeout=10) as response:
                    return json.load(response)["result"]

            courses = call("listCourses")["courses"]
            assert len(courses) == 2, "The installed wheel must contain both course manifests"
            lesson_count = sum(len(course["lessons"]) for course in courses)
            assert lesson_count == 19, "The competition release contains 19 lessons"
            lesson = call("loadLesson", {"courseId": "learning_spark", "lessonIdx": 0})
            assert lesson["step"], "The first installed lesson is empty"
            result = call("run", {"code": "print('WHEEL_SMOKE_NOT_EXECUTED')"})
            assert result["mode"] == "dry_run" and result["exitCode"] == 0
            assert "Syntax OK" in result["stdout"] and "WHEEL_SMOKE_NOT_EXECUTED" not in result["stdout"]
            print(json.dumps({"installedWeb": "passed", "courses": len(courses), "lessons": lesson_count,
                              "browserAssets": len(assets), "modelCalls": 0, "sparkJobs": 0}))
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=5)


if __name__ == "__main__":
    main()
