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
    assert calls == [{"port": 8888, "data_dir": None, "dry_run": dry_run, "host": "0.0.0.0", "trusted_local": False, "isolated_execution": False}]


def test_explicit_web_options_keep_an_isolated_data_directory(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr("sparktutor.web.server.serve", lambda **kwargs: calls.append(kwargs))
    directory = tmp_path / "trial learner"
    result = CliRunner().invoke(main, ["--dry-run", "web", "--port", "8899", "--host", "127.0.0.1",
                                      "--data-dir", str(directory)])
    assert result.exit_code == 0, result.output
    assert calls == [{"port": 8899, "data_dir": directory, "dry_run": True, "host": "127.0.0.1", "trusted_local": False, "isolated_execution": False}]


def test_trusted_local_cli_selects_loopback_and_passes_explicit_opt_in(monkeypatch):
    calls = []
    monkeypatch.setattr("sparktutor.web.server.serve", lambda **kwargs: calls.append(kwargs))
    result = CliRunner().invoke(main, ["web", "--trusted-local"])
    assert result.exit_code == 0, result.output
    assert calls == [{"port": 8888, "data_dir": None, "dry_run": False,
                      "host": "127.0.0.1", "trusted_local": True, "isolated_execution": False}]


def test_account_isolated_execution_cli_keeps_login_and_accepts_explicit_service_opt_in(monkeypatch):
    calls = []
    monkeypatch.setattr("sparktutor.web.server.serve", lambda **kwargs: calls.append(kwargs))
    result = CliRunner().invoke(main, ["web", "--isolated-execution"])
    assert result.exit_code == 0, result.output
    assert calls[0]["isolated_execution"] is True
    assert calls[0]["trusted_local"] is False


@pytest.mark.parametrize("arguments", [
    ["web", "--trusted-local", "--isolated-execution"],
    ["--dry-run", "web", "--isolated-execution"],
])
def test_account_isolated_execution_rejects_other_execution_entry_points(arguments):
    result = CliRunner().invoke(main, arguments)
    assert result.exit_code != 0
    assert "不能" in result.output


@pytest.mark.parametrize("arguments,message", [
    (["--dry-run", "web", "--trusted-local"], "不能同时使用"),
    (["web", "--trusted-local", "--host", "0.0.0.0"], "回环地址"),
])
def test_trusted_local_invalid_options_fail_before_creating_a_server(arguments, message):
    result = CliRunner().invoke(main, arguments)
    assert result.exit_code != 0
    assert message in result.output
