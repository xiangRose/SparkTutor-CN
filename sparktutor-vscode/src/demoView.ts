import { dimensionCard } from "./diagnosisView";
import { reviewContent } from "./learningHistoryView";
import { escapeHtml } from "./lessonHelpers";
import { DemoCheck, DemoProgress, DemoReport, DemoSelection } from "./demoTypes";
import { HistoryEvent } from "./learningHistoryTypes";

export interface DemoViewState {
  selection: DemoSelection;
  report: DemoReport | null;
  stageIndex: number;
  running: boolean;
  detachedRun: boolean;
  progress: DemoProgress | null;
  error: string;
  notice: string;
}

const EVENTS: Record<string, string> = { code_submit: "提交答案", code_run: "运行代码", error: "代码错误",
  hint_request: "请求提示", solution_view: "查看参考答案", task_open: "打开题目", task_complete: "完成题目",
  code_edit: "编辑计数", chat_request: "导师提问", session_start: "会话开始", session_end: "会话结束" };

function checks(items: DemoCheck[], heading: string): string {
  const passed = items.filter((item) => item.passed === true).length;
  return `<section class="demo-checks"><h3>${heading}</h3><p class="muted">断言核查：${passed} / ${items.length} 项符合预期。核查结果不等于学习成绩或实验效果。</p>
    <ul>${items.map((item) => `<li><span class="check-result ${item.passed === true ? "check-pass" : "check-fail"}">${item.passed === true ? "符合预期" : "不符合预期"}</span>
      <span>${escapeHtml(item.label)}</span></li>`).join("")}</ul>${items.length ? "" : `<p>本阶段没有附带断言，不能据此声称验证通过。</p>`}</section>`;
}

function eventRow(event: HistoryEvent): string {
  const result = event.passed === true ? "通过" : event.passed === false ? "未通过" : "未评估";
  return `<li><div class="event-heading"><strong>${escapeHtml(EVENTS[event.eventType] || "学习事件")} · ${result}</strong>
    <span class="muted">记录 ${escapeHtml(event.eventId)}</span></div><p>${escapeHtml(event.reason)}</p>
    ${event.answerInfluenced ? `<p class="muted">受答案查看影响，不能作为未受答案影响的通过证据。</p>` : ""}
    ${event.mode === "dry_run" ? `<p class="muted">仅语法检查，不代表执行过 Spark 课程测试。</p>` : ""}</li>`;
}

function stageContent(report: DemoReport, index: number): string {
  const stage = report.stages[index];
  if (!stage) { return ""; }
  const diagnosis = stage.diagnosis;
  const recommendation = diagnosis.recommendedExercise;
  return `<section id="demo-stage" class="demo-stage" tabindex="-1" aria-labelledby="stage-heading"><div class="section-heading"><div><div class="eyebrow">阶段 ${index + 1} / ${report.stages.length} · 报告中的阶段快照</div>
    <h2 id="stage-heading">${escapeHtml(stage.title)}</h2></div></div><p>${escapeHtml(stage.explanation)}</p>
    <div class="diagnosis-summary"><p>${escapeHtml(diagnosis.diagnosis)}</p><span class="muted">本阶段快照含 ${stage.events.length} 条演示事件；当前课程记录数为 ${diagnosis.eventCount}，其中 ${diagnosis.eligibleEventCount} 条可用于此诊断。</span></div>
    <div class="dimensions abilities">${diagnosis.dimensions.filter((item) => item.key !== "hint_dependency").map((item) => dimensionCard(item, true)).join("")}</div>
    ${diagnosis.dimensions.filter((item) => item.key === "hint_dependency").map((item) => `<div class="hint-section"><div class="eyebrow">描述性求助指标</div>${dimensionCard(item, true)}</div>`).join("")}
    <section class="recommendation demo-recommendation"><div><div class="eyebrow">本阶段的单条推荐 · 仅供核查</div><h3>${recommendation ? escapeHtml(recommendation.title) : "本阶段暂无具体推荐"}</h3>
      <p>${escapeHtml(recommendation?.reason || diagnosis.recommendationReason || "当前证据不足以生成推荐。")}</p>
      ${recommendation ? `<p class="muted">${escapeHtml(recommendation.courseId)} / ${escapeHtml(recommendation.lessonId)} / 步骤 ${escapeHtml(recommendation.stepId)}</p>` : ""}
      <p class="muted">推荐来自本次隔离演示，仅展示核查信息，不打开正常课程或写入学习进度。</p></div></section>
    <details class="demo-events" open><summary>本阶段的事件记录（${stage.events.length} 条）</summary>
      <p class="muted">${report.mode === "recorded" ? "以下是合成轨迹中的事件，不能当成实际执行结果。" : "以下事件来自预设脚本测试过程，不代表学习者自主完成。"}</p>
      <ol>${stage.events.map(eventRow).join("")}</ol>${stage.events.length ? "" : `<p>该阶段没有新增事件。</p>`}</details>
    ${checks(stage.checks, "本阶段核查")}
    ${stage.review ? reviewContent(stage.review) : `<p class="notice">本阶段暂无可展示的记录前后复盘。</p>`}
    <p class="disclaimer">${escapeHtml(diagnosis.disclaimer)}</p></section>`;
}

export function demoContent(state: DemoViewState): string {
  const { selection, report, running } = state;
  const recorded = selection.mode === "recorded";
  return `<header class="dashboard-header"><div class="brand"><span class="brand-mark" aria-hidden="true">S</span><div>
    <div class="eyebrow">SPARKTUTOR · 独立演示空间</div><h1>诊断演示</h1></div></div><span class="badge">不计入正常学习记录</span></header>
    <section class="demo-provenance" role="note"><strong>${recorded ? "合成轨迹演示 · 无需 Spark" : "真实 Spark 测试 · 预设脚本"}</strong>
      <p>${recorded ? "这不是实际学习成绩、实际运行结果或实验效果。演示只用于核查诊断规则如何处理一段合成轨迹。" :
        "本机执行预设脚本和课程测试，需要 PySpark 与 Java，可能耗时数分钟（最多等待 10 分钟）。这不是学习者自主学习过程，也不是教学效果实验。真实验证期间，普通学习请求会排队，请等待演示结束后继续学习。"}</p>
      <p>每次运行使用独立演示数据，保留正常课程、学习数据库和已有作业。</p></section>
    <section class="demo-controls" aria-label="选择演示"><div><label for="demo-mode">验证模式</label><select id="demo-mode"${running ? " disabled" : ""}>
      <option value="recorded"${recorded ? " selected" : ""}>合成轨迹（无需 Spark）</option><option value="spark"${recorded ? "" : " selected"}>真实 Spark 课程测试</option></select></div>
      <div><label for="demo-scenario">演示场景</label><select id="demo-scenario"${running ? " disabled" : ""}>
      <option value="transfer_retry"${selection.scenarioId === "transfer_retry" ? " selected" : ""}>迁移题先错后修：首次未通过</option>
      <option value="transfer_first_pass"${selection.scenarioId === "transfer_first_pass" ? " selected" : ""}>另一条独立轨迹：迁移题首次通过</option></select></div>
      <button class="primary" data-action="run"${running ? " disabled" : ""}>${running ? "演示运行中……" : report ? "重新运行此演示" : "运行所选演示"}</button></section>
    <p class="demo-scenario-note">场景预期（以运行后的核查为准）：${selection.scenarioId === "transfer_retry" ?
      "来源题 A 先通过，迁移题 B 第一次失败后再修复。修复不会改写 B 的首次评估，因此迁移首评通过率仍为 0%。" :
      "这是一条全新的独立演示轨迹：来源题 A 通过，迁移题 B 首次即通过，因此迁移首评通过率为 100%。它不把上一条轨迹的重试伪装成新迁移。"}</p>
    ${running ? `<section class="notice" role="status" aria-live="polite"><h2>${state.detachedRun ? "先前演示请求仍在执行" : "正在运行所选演示"}</h2>
      <p>${state.detachedRun ? "关闭面板不会取消服务器执行。等待先前请求结束后，可以重新运行；旧请求的通知与报告不会显示在这个新窗口。" :
        "完成后才显示可核查的阶段报告。运行期间不可重复调用；关闭面板不会取消服务器执行。"}</p>
      ${state.progress ? `<p>当前阶段：${escapeHtml(state.progress.title)}</p>` : ""}</section>` : ""}
    ${state.error ? `<section class="notice error" role="alert"><h2>演示未完成</h2><p>${escapeHtml(state.error)}</p></section>` : ""}
    ${state.notice ? `<p class="notice" role="status">${escapeHtml(state.notice)}</p>` : ""}
    ${report ? `<section class="demo-report"><div class="section-heading"><div><div class="eyebrow">${escapeHtml(report.provenanceLabel)}</div><h2>${escapeHtml(report.title)}</h2></div>
      <span class="badge">${report.mode === "recorded" ? "合成事件 · 未执行 Spark" : report.sparkVerified ? "已完成预设 Spark 测试验证" : "未确认 Spark 测试验证"}</span></div>
      <p class="disclaimer">${escapeHtml(report.disclaimer)}</p>
      <div class="demo-task-contexts">${[["来源题 A", report.taskA], ["迁移题 B", report.taskB]].map(([label, task]) => {
        const item = task as DemoReport["taskA"];
        return `<article><div class="eyebrow">${label}</div><h3>${escapeHtml(item.title)}</h3><p>${escapeHtml(item.context)}</p></article>`;
      }).join("")}</div>
      <nav class="demo-stage-tabs" aria-label="演示阶段">${report.stages.map((stage, index) => `<button data-action="stage" data-stage-index="${index}"${state.stageIndex === index ? ' aria-current="step"' : ""}>${index + 1}. ${escapeHtml(stage.title)}</button>`).join("")}</nav>
      ${stageContent(report, state.stageIndex)}${checks(report.checks, "整段轨迹核查")}</section>` :
      !running && !state.error ? `<section class="notice"><h2>选择场景后，点击「运行所选演示」</h2><p>尚未运行演示，没有预填成绩或验证结果。切换场景和模式不会自动执行。</p></section>` : ""}
    <p id="demo-status" role="status" aria-live="polite"></p>`;
}
