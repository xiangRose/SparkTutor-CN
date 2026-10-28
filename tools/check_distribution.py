"""Check built source/wheel artifacts before publishing a Web release.

This compares the actual packaged teaching and browser resources with the
checkout. It also rejects local runtimes, learner data and credential files.
It reads archives in place and never extracts them.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import tarfile
import zipfile


ROOT = Path(__file__).resolve().parents[1]
RESOURCE_SUFFIXES = {".py", ".yaml", ".json", ".js", ".css", ".html", ".svg", ".txt", ".tcss"}
PRIVATE_PARTS = {".git", ".sparktutor-web", ".sparktutor", ".web-runtime", ".venv", "node_modules", "__pycache__", ".pytest_cache"}
SOURCE_FILES = (
    "pyproject.toml", "README.md", "LICENSE", ".gitignore", "start-web.ps1", "start-web-wsl.ps1",
    "containers/executor/Dockerfile", "containers/executor/.dockerignore",
    "启动网站.cmd", "使用说明.txt", ".github/workflows/ci.yml",
    "tools/check_distribution.py", "tools/smoke_installed_web.py",
)


def resource_files() -> dict[str, bytes]:
    package = ROOT / "src/sparktutor"
    return {
        path.relative_to(package).as_posix(): path.read_bytes()
        for path in package.rglob("*")
        if path.is_file() and path.suffix in RESOURCE_SUFFIXES
        and not set(path.relative_to(package).parts).intersection(PRIVATE_PARTS)
    }


def archive_files(path: Path) -> tuple[dict[str, bytes], bool]:
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            return {name: archive.read(name) for name in archive.namelist() if not name.endswith("/")}, True
    with tarfile.open(path, "r:gz") as archive:
        result = {}
        for member in archive.getmembers():
            if not member.isfile():
                continue
            stream = archive.extractfile(member)
            assert stream is not None, member.name
            result[member.name.partition("/")[2]] = stream.read()
        return result, False


def check(path: Path) -> dict:
    files, wheel = archive_files(path)
    for name in files:
        parts = PurePosixPath(name).parts
        assert not set(parts).intersection(PRIVATE_PARTS), f"Private/local directory in {path.name}: {name}"
        filename = parts[-1]
        assert filename not in {".env", "ai-settings.json", "ai-secrets.dpapi", "web-preferences.json", "runtime.json"}, name
        assert not filename.startswith(".env."), name
        assert not filename.endswith((".pyc", ".pyo", ".dpapi", ".log", ".db", ".db-wal", ".db-shm")), name
    resources = resource_files()
    prefix = "sparktutor/" if wheel else "src/sparktutor/"
    for name, content in resources.items():
        packaged = prefix + name
        assert packaged in files, f"Missing {packaged} in {path.name}"
        assert hashlib.sha256(files[packaged]).digest() == hashlib.sha256(content).digest(), f"Changed resource: {packaged}"
    if not wheel:
        for name in SOURCE_FILES:
            assert name in files, f"Missing release source file: {name}"
    return {
        "artifact": path.name, "files": len(files),
        "courseManifests": sum(name.endswith("/course.yaml") for name in resources),
        "lessons": sum(name.endswith("/lesson.yaml") for name in resources),
        "matchedPackageResources": len(resources), "privateFiles": 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifacts", nargs="+", type=Path)
    args = parser.parse_args()
    for path in args.artifacts:
        print(json.dumps(check(path), ensure_ascii=False))


if __name__ == "__main__":
    main()
