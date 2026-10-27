"""The official source entry point starts the Web app without TUI packages."""

from click.testing import CliRunner
import pytest

from sparktutor.cli import main


@pytest.mark.parametrize("arguments,dry_run", [([], False), (["--dry-run"], True)])
def test_default_command_starts_the_web_server(monkeypatch, arguments, dry_run):
    calls = []
    monkeypatch.setattr("sparktutor.web.server.serve", lambda **kwargs: calls.append(kwargs))
    result = CliRunner().invoke(main, arguments)
    assert result.exit_code == 0, result.output
    assert calls == [{"port": 8888, "data_dir": None, "dry_run": dry_run, "host": "0.0.0.0"}]


def test_explicit_web_options_keep_an_isolated_data_directory(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr("sparktutor.web.server.serve", lambda **kwargs: calls.append(kwargs))
    directory = tmp_path / "trial learner"
    result = CliRunner().invoke(main, ["--dry-run", "web", "--port", "8899", "--host", "127.0.0.1",
                                      "--data-dir", str(directory)])
    assert result.exit_code == 0, result.output
    assert calls == [{"port": 8899, "data_dir": directory, "dry_run": True, "host": "127.0.0.1"}]
