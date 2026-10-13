"""Export the demonstration report without code, raw logs or database files."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile


_NUMERATORS = {"knowledge": "latestPassedTasks", "debugging": "confirmedRepairs",
               "hint_dependency": "hintedBeforeFirstAssessment", "transfer": "firstPassedTasks"}


def demo_summary(report: dict) -> str:
    """A readable companion to the complete, labelled JSON evidence."""
    lines = [f"# {report['title']}", "", "**脚本化演示数据，不是真实学生成绩或教学效果实验。**", "",
             report["provenanceLabel"], "", report["disclaimer"], "",
             f"- 场景：`{report['scenarioId']}`", f"- 模式：`{report['mode']}`",
             f"- 真实 Spark 验证：{'已完成' if report['sparkVerified'] else '未执行'}",
             f"- 演示记录数：{report['eventCount']}", "",
             "开始与结束时间是脚本运行时间，不代表学生学习时长。", ""]
    for stage in report["stages"]:
        lines.extend([f"## {stage['title']}", "", stage["explanation"], "", stage["diagnosis"]["diagnosis"], ""])
        for dimension in stage["diagnosis"]["dimensions"]:
            score = dimension["score"]
            proportion = "证据不足" if score is None else f"{score:g}%"
            numerator = dimension["metrics"][_NUMERATORS[dimension["key"]]]
            lines.append(f"- {dimension['name']}：{proportion}，分子 / 分母 {numerator} / {dimension['sampleSize']}")
        recommendation = stage["diagnosis"].get("recommendedExercise")
        lines.extend(["", f"推荐：{recommendation['title']}。{recommendation['reason']}" if recommendation else
                      f"推荐：暂无。{stage['diagnosis'].get('recommendationReason', '')}", ""])
    lines.extend(["## 可核查条件", ""])
    for check in report["checks"]:
        lines.append(f"- {'通过' if check['passed'] else '未通过'}：{check['label']}")
    lines.extend(["", "完整阶段指标、元数据和前后复盘见同目录的 report.json。", ""])
    return "\n".join(lines)


def export_demo_report(report: dict, output_dir: Path) -> Path:
    """Make a new directory per run; never replace an earlier report."""
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    run_dir = Path(tempfile.mkdtemp(prefix=f"{report['scenarioId']}-", dir=output_dir))
    (run_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (run_dir / "summary.md").write_text(demo_summary(report), encoding="utf-8")
    return run_dir
