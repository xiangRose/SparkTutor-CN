"""CLI entry point for SparkTutor."""

import click
from pathlib import Path


@click.group(invoke_without_command=True)
@click.option("--dry-run", is_flag=True, help="Force dry-run mode (no Spark execution)")
@click.pass_context
def main(ctx: click.Context, dry_run: bool) -> None:
    """SparkTutor — 在浏览器中学习 PySpark，默认启动本地网站。"""
    ctx.ensure_object(dict)
    ctx.obj["dry_run"] = dry_run
    if ctx.invoked_subcommand is None:
        ctx.invoke(web)


@main.command()
@click.pass_context
def launch(ctx: click.Context) -> None:
    """Launch the optional legacy terminal interface (requires .[tui])."""
    from sparktutor.app.main_app import SparkTutorApp

    app = SparkTutorApp(force_dry_run=ctx.obj.get("dry_run", False))
    app.run()


@main.command()
@click.option("--port", default=8888, type=click.IntRange(1, 65535), show_default=True)
@click.option("--host", help="监听地址；账号模式默认 0.0.0.0，可信本机实操默认 127.0.0.1。")
@click.option("--data-dir", type=click.Path(file_okay=False, path_type=Path), help="网页端学习数据目录。")
@click.option("--trusted-local", is_flag=True, help="启用可信单人本机实操，真实执行代码，仅允许本机访问。")
@click.option("--isolated-execution", is_flag=True, help="启用登录账号的独立容器执行。")
@click.pass_context
def web(ctx: click.Context, port: int, host: str | None, data_dir: Path | None, trusted_local: bool, isolated_execution: bool) -> None:
    """在浏览器中学习 Spark，默认地址 http://localhost:8888。"""
    from sparktutor.web.server import serve
    host = host or ("127.0.0.1" if trusted_local else "0.0.0.0")
    try:
        serve(port=port, data_dir=data_dir, dry_run=ctx.obj.get("dry_run", False),
              host=host, trusted_local=trusted_local, isolated_execution=isolated_execution)
    except ValueError as error:
        raise click.ClickException(str(error)) from error


@main.command()
def courses() -> None:
    """List available courses."""
    from sparktutor.courses.registry import CourseRegistry

    registry = CourseRegistry()
    for course in registry.list_courses():
        click.echo(f"  {course.id}: {course.title} ({len(course.lessons)} lessons)")


@main.command()
def status() -> None:
    """Show execution environment status."""
    import asyncio

    from sparktutor.engine.executor import Executor

    async def _check():
        executor = Executor()
        mode = await executor.detect_mode()
        click.echo(f"Execution mode: {mode.value}")

    asyncio.run(_check())


@main.command()
@click.option("--scenario", type=click.Choice(["transfer_retry", "transfer_first_pass"]),
              default="transfer_retry", show_default=True, help="选择先失败再修复，或新情境首次通过的独立演示。")
@click.option("--mode", type=click.Choice(["recorded", "spark"]), default="recorded", show_default=True,
              help="recorded 为合成轨迹；spark 使用预设代码执行真实本机课程测试。")
@click.option("--output", type=click.Path(file_okay=False, path_type=Path),
              help="报告保存目录；每次运行创建新子目录，默认 demo-output。")
@click.option("--json", "as_json", is_flag=True, help="标准输出只返回 JSON；进度显示在标准错误。")
@click.pass_context
def demo(ctx: click.Context, scenario: str, mode: str, output: Path | None, as_json: bool) -> None:
    """运行隔离的诊断演示，不修改自己的学习进度或行为记录。"""
    import asyncio
    import json

    from sparktutor.engine.demo_report import export_demo_report
    from sparktutor.engine.demo_scenarios import run_demo_scenario

    if ctx.obj.get("dry_run") and mode == "spark":
        raise click.ClickException("--dry-run 与真实 Spark 演示冲突，请选择 --mode recorded。")
    click.echo("脚本化演示：与真实学习记录隔离，不代表真实学生成绩或教学效果。", err=True)

    def progress(value: dict) -> None:
        click.echo(f"[{value.get('index', '?')}/{value.get('total', '?')}] {value['title']}", err=True)

    try:
        report = asyncio.run(run_demo_scenario(scenario_id=scenario, mode=mode, on_progress=progress))
        run_dir = export_demo_report(report, output or Path.cwd() / "demo-output")
    except (ValueError, RuntimeError, OSError) as error:
        raise click.ClickException(str(error)) from error
    if as_json:
        click.echo(json.dumps(report, ensure_ascii=False))
        click.echo(f"报告已保存：{run_dir}", err=True)
    else:
        click.echo(report["provenanceLabel"])
        click.echo(f"可核查条件：{sum(check['passed'] for check in report['checks'])}/{len(report['checks'])} 通过")
        click.echo(f"报告已保存：{run_dir}")
