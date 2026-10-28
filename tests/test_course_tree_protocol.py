"""Course catalogue titles survive the backend protocol and sidebar rendering."""

from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest

from sparktutor.courses.registry import CourseRegistry
from sparktutor.engine.lesson_loader import load_lesson
from sparktutor.state.progress import ProgressStore


def _read_catalogue(data_dir: Path, course_ids: list[str]) -> dict:
    script = """
import asyncio
import sys
from pathlib import Path
from sparktutor.config.settings import Settings
Settings.load = classmethod(lambda cls: Settings(data_dir=Path(sys.argv[1])))
from sparktutor.server.__main__ import main
asyncio.run(main())
"""
    requests = [{"id": 0, "method": "listCourses", "params": {}}] + [
        {"id": index + 1, "method": "getCourseProgress", "params": {"courseId": course_id}}
        for index, course_id in enumerate(course_ids)
    ]
    completed = subprocess.run(
        [sys.executable, "-X", "utf8", "-c", script, str(data_dir)],
        input="".join(json.dumps(request) + "\n" for request in requests),
        capture_output=True, encoding="utf-8", timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    responses = {item["id"]: item for item in map(json.loads, completed.stdout.splitlines()) if "id" in item}
    assert all("result" in response for response in responses.values()), responses
    return {"catalogue": responses[0]["result"], "progress": {
        course_id: responses[index + 1]["result"] for index, course_id in enumerate(course_ids)
    }}


@pytest.fixture(scope="module")
def catalogue_snapshots(tmp_path_factory):
    registry = CourseRegistry()
    courses = registry.list_courses()
    course_ids = [course.id for course in courses]
    data_dir = tmp_path_factory.mktemp("course-tree")
    fresh = _read_catalogue(data_dir, course_ids)
    store = ProgressStore(data_dir / "progress.db")
    expected = {}
    for course in courses:
        labels = []
        for index, lesson_id in enumerate(course.lessons):
            lesson = load_lesson(registry.courses_dir / course.id / "lessons" / lesson_id)
            labels.append(f"{index + 1}. {lesson.title}")
        expected[course.id] = {"title": course.title, "labels": labels}
        store.save(course.id, course.lessons[0], 1, 10)
        store.save(course.id, course.lessons[-1], 10, 10, completed=True)
    saved_progress = {course.id: store.get_course_progress(course.id) for course in courses}
    saved = _read_catalogue(data_dir, course_ids)
    assert saved_progress == {course.id: store.get_course_progress(course.id) for course in courses}
    return {"fresh": fresh, "saved": saved, "expected": expected}


def test_catalogue_protocol_returns_all_19_chinese_lesson_titles_without_saved_progress(catalogue_snapshots):
    fresh = catalogue_snapshots["fresh"]
    saved = catalogue_snapshots["saved"]
    assert fresh["catalogue"] == saved["catalogue"], "Display titles must not depend on saved progress"
    courses = fresh["catalogue"]["courses"]
    assert len(courses) == 2
    assert sum(course["lessonCount"] for course in courses) == 19
    for course in courses:
        assert re.search(r"[\u4e00-\u9fff]", course["title"])
        expected = catalogue_snapshots["expected"][course["id"]]["labels"]
        assert [f"{index + 1}. {course['lessonTitles'][lesson_id]}"
                for index, lesson_id in enumerate(course["lessons"])] == expected
        assert all(re.search(r"[\u4e00-\u9fff]", title) for title in course["lessonTitles"].values())
        assert all(lesson["status"] == "not_started" for lesson in fresh["progress"][course["id"]]["lessons"])
        assert saved["progress"][course["id"]]["lessons"][0]["status"] == "in_progress"
        assert saved["progress"][course["id"]]["lessons"][-1]["status"] == "completed"


def test_real_backend_catalogue_renders_chinese_tree_labels_across_progress_and_refresh(catalogue_snapshots):
    extension_dir = Path(__file__).resolve().parents[1] / "sparktutor-vscode"
    extension_test = extension_dir / "tests" / "courseTree.test.cjs"
    node = shutil.which("node")
    if not node or not extension_test.is_file() or not (extension_dir / "node_modules" / "esbuild").exists():
        pytest.skip("Sidebar integration requires the archived extension source, Node and its development dependencies")
    completed = subprocess.run(
        [node, str(extension_test), "--protocol-fixture"],
        input=json.dumps(catalogue_snapshots, ensure_ascii=False),
        capture_output=True, encoding="utf-8", timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "all 19 lesson labels" in completed.stdout
