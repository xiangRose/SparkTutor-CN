"""Binary UI resources and private-data exclusions are part of release checks."""

import importlib.util
from pathlib import Path
import zipfile

import pytest


SPEC = importlib.util.spec_from_file_location("distribution_asset_checker", Path(__file__).resolve().parents[1] / "tools/check_distribution.py")
CHECKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKER)


@pytest.fixture
def release(tmp_path, monkeypatch):
    package = tmp_path / "src/sparktutor"
    assets = package / "web/static/assets"
    assets.mkdir(parents=True)
    (package / "__init__.py").write_bytes(b'"""Fixture package."""\n')
    (assets / "pet-idle.png").write_bytes(b"\x89PNG\r\n\x1a\nfixture-binary-asset")
    monkeypatch.setattr(CHECKER, "ROOT", tmp_path)
    return tmp_path, package


def wheel(release, *, image=True, changed=False, private=None):
    root, package = release
    archive = root / "test-0.1-py3-none-any.whl"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("sparktutor/__init__.py", (package / "__init__.py").read_bytes())
        if image:
            payload = (package / "web/static/assets/pet-idle.png").read_bytes()
            output.writestr("sparktutor/web/static/assets/pet-idle.png", payload + (b"tampered" if changed else b""))
        if private:
            output.writestr(private, b"private fixture")
    return archive


def test_shipped_png_must_be_present_and_byte_identical(release):
    assert CHECKER.check(wheel(release))["matchedPackageResources"] == 2
    with pytest.raises(AssertionError, match="Missing.*pet-idle.png"):
        CHECKER.check(wheel(release, image=False))
    with pytest.raises(AssertionError, match="Changed resource"):
        CHECKER.check(wheel(release, changed=True))


@pytest.mark.parametrize("private", ["data/accounts.sqlite3", "data/progress.sqlite", "data/private.key",
                                     "coach-evaluation-output/request.json", ".sparktutor-trusted-local/state.json"])
def test_release_rejects_private_database_keys_and_evaluation_output(release, private):
    with pytest.raises(AssertionError):
        CHECKER.check(wheel(release, private=private))
