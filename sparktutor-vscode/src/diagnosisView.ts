/** Pure HTML rendering kept separate from VS Code so feedback states can be tested. */
import { escapeHtml } from "./lessonHelpers";
import { DiagnosisDimension, DiagnosisResult } from "./types";

const DIRECTIONS: Record<string, string> = {
  knowledge: "数值越高，任务最近一次有效评估的通过率越高。",
  debugging: "数值越高，经有效评估确认修复的过程占比越高；提示辅助后的修复也计入。",
  hint_dependency: "这是提示使用比例，不参与能力强弱排序。主动求助本身不代表能力低。",
  transfer: "数值越高，具备来源知识的新情境任务首次有效评估通过率越高；提示辅助后的通过也计入。",
};

const SAMPLE_UNITS: Record<string, string> = {
  knowledge: "个已评估任务", debugging: "个失败修复过程",
  hint_dependency: "个已评估任务", transfer: "个有效迁移任务",
};

const EVIDENCE: Record<string, string> = {
  none: "证据不足", limited: "少量证据", available: "证据较多", sufficient: "证据较多",
};

function percentage(value: unknown): string {
  return typeof value === "number" && Number.isFinite(value) ? `${value.toFixed(1)}%` : "证据不足";
}

function dimensionCard(item: DiagnosisDimension): string {
  const measured = typeof item.score === "number" && Number.isFinite(item.score);
  const score = measured
    ? `${item.score!.toFixed(1)}<span>${item.direction === "descriptive" ? "%" : " / 100"}</span>`
    : "证据不足";
  const progress = measured
    ? `<progress max="100" value="${Math.max(0, Math.min(100, item.score!))}" aria-label="${escapeHtml(item.name)}"></progress>`
    : `<div class="empty-meter" aria-hidden="true"></div>`;
  const hintMetrics = item.key === "hint_dependency" && item.metrics
    ? `<p class="metrics">提示后出现有效通过的请求比例：${percentage(item.metrics.hintProductivity)}（时间关联，不代表因果）</p>`
    : "";
  return `<article class="dimension">
    <div class="dimension-heading"><h2>${escapeHtml(item.name)}</h2><strong class="score${measured ? "" : " missing"}">${score}</strong></div>
    ${progress}
    <p class="direction">${escapeHtml(DIRECTIONS[item.key] || "分数反映当前可观测的学习表现。")}</p>
    <p>${escapeHtml(item.summary)}</p>
    ${hintMetrics}
    <p class="evidence">证据量：${EVIDENCE[item.evidenceLevel || item.confidence || "none"] || "证据不足"} · ${item.sampleSize ?? item.evidenceCount} ${SAMPLE_UNITS[item.key] || "个样本"}</p>
  </article>`;
}

export function diagnosisContent(result: DiagnosisResult): string {
  const exercise = result.recommendedExercise;
  return `<section class="summary" aria-labelledby="summary-title">
    <div class="eyebrow">本次学习建议</div><h2 id="summary-title">${escapeHtml(result.diagnosis)}</h2>
    <p>已记录 ${result.eventCount} 条学习事件，其中 ${result.eligibleEventCount} 条可用于本次诊断。完成新的练习后，点击「刷新诊断」查看变化。</p>
  </section>
  ${result.eventCount === 0 ? `<div class="notice">还没有学习记录。请先从课程列表打开一课，运行并提交练习；证据不足时不会给出能力分数。</div>` : ""}
  <div class="dimensions">${result.dimensions.map(dimensionCard).join("")}</div>
  <section class="recommendation" aria-labelledby="recommendation-title">
    <div class="eyebrow">下一步</div><h2 id="recommendation-title">${exercise ? escapeHtml(exercise.title) : "积累更多练习证据"}</h2>
    <p>${escapeHtml(exercise?.reason || result.recommendationReason || "先完成一次独立尝试和提交，系统再根据有效证据推荐具体练习。")}</p>
    ${exercise ? `<button id="open-exercise" class="primary">打开这道推荐练习</button><p class="muted">直接定位到目标题目，保留已有练习文件。</p>` : ""}
    <p id="exercise-status" role="status" aria-live="polite"></p>
  </section>
  <p class="disclaimer">${escapeHtml(result.disclaimer)}</p>`;
}

export function diagnosisLoading(): string {
  return `<section class="notice" role="status" aria-live="polite"><h2>正在整理学习记录……</h2><p>诊断将显示各维度的证据与下一步练习建议。</p></section>`;
}

export function diagnosisError(message: string): string {
  return `<section class="notice error" role="alert"><h2>暂时无法生成诊断</h2><p>${escapeHtml(message)}</p><p>请点击「刷新诊断」重试。</p></section>`;
}
