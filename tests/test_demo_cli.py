"""Labelled, repeatable exports must not overwrite earlier demonstration runs."""

import json

from click.testing import CliRunner

from sparktutor.cli import main


def test_demo_cli_exports_separate_labelled_reports_without_database(tmp_path):
    output = tmp_path / "reports"
    runner = CliRunner()
    first = runner.invoke(main, ["demo", "--json", "--output", str(output)])
    assert first.exit_code == 0, first.output
    report = json.loads(first.stdout)
    assert report["isDemo"] is True and report["sparkVerified"] is False
    paths = list(output.iterdir())
    assert len(paths) == 1
    original = (paths[0] / "report.json").read_text(encoding="utf-8")
    summary = (paths[0] / "summary.md").read_text(encoding="utf-8")
    assert "脚本化演示" in summary and "真实 Spark 验证：未执行" in summary
    assert {file.name for file in paths[0].iterdir()} == {"report.json", "summary.md"}
    second = runner.invoke(main, ["demo", "--output", str(output), "--scenario", "transfer_first_pass"])
    assert second.exit_code == 0, second.output
    assert len(list(output.iterdir())) == 2
    assert (paths[0] / "report.json").read_text(encoding="utf-8") == original


def test_demo_cli_rejects_conflicting_modes_without_creating_reports(tmp_path):
    output = tmp_path / "reports"
    result = CliRunner().invoke(main, ["--dry-run", "demo", "--mode", "spark", "--output", str(output)])
    assert result.exit_code != 0
    assert "冲突" in result.output
    assert not output.exists()


def test_failed_demo_does_not_export_an_unverified_success(tmp_path, monkeypatch):
    import sparktutor.engine.demo_scenarios as scenarios

    async def failure(**kwargs):
        raise RuntimeError("环境不可用，未完成真实验证。")
    monkeypatch.setattr(scenarios, "run_demo_scenario", failure)
    output = tmp_path / "reports"
    result = CliRunner().invoke(main, ["demo", "--mode", "spark", "--output", str(output)])
    assert result.exit_code != 0
    assert "未完成真实验证" in result.output
    assert not output.exists()
