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
  knowledge: "个已评估任务", debugging: "个可观察失败过程",
  hint_dependency: "个已评估任务", transfer: "个有效迁移任务",
};

const EVIDENCE: Record<string, string> = {
  none: "证据不足", limited: "少量证据", available: "证据较多", sufficient: "证据较多",
};

function percentage(value: unknown): string {
  return typeof value === "number" && Number.isFinite(value) ? `${value.toFixed(1)}%` : "证据不足";
}

const FORMULAS: Record<string, { expression: string; numerator: string; denominator: string; sample: string }> = {
  knowledge: { expression: "最近一次有效评估通过的任务数 ÷ 已评估任务数 × 100",
    numerator: "latestPassedTasks", denominator: "evaluatedTasks",
    sample: "每个任务取最近一次有效评估。查看参考答案后受影响的评估不计入；使用提示后的通过仍计入。" },
  debugging: { expression: "经有效评估确认修复的过程数 ÷ 可观察失败过程数 × 100",
    numerator: "confirmedRepairs", denominator: "failureEpisodes",
    sample: "连续失败合并为一个过程，尚未修复的过程也在分母中。提示辅助后的修复计入，受答案查看影响的过程排除。" },
  hint_dependency: { expression: "首次有效评估前使用提示的任务数 ÷ 已评估任务数 × 100",
    numerator: "hintedBeforeFirstAssessment", denominator: "evaluatedTasks",
    sample: "每个任务只计一次提示覆盖；首评之后的提示不会改写该任务的覆盖状态。这是求助行为描述，不是能力评分。" },
  transfer: { expression: "新情境任务首次有效评估通过数 ÷ 有效迁移任务数 × 100",
    numerator: "firstPassedTasks", denominator: "eligibleTransferTasks",
    sample: "只纳入首评前来源知识已通过且情境不同的迁移任务。提示辅助后的通过计入；受答案查看影响的评估排除。" },
};

function evidenceDetails(item: DiagnosisDimension): string {
  const formula = FORMULAS[item.key];
  if (!formula) { return ""; }
  const numerator = item.metrics?.[formula.numerator];
  const denominator = item.metrics?.[formula.denominator];
  const counts = typeof numerator === "number" && typeof denominator === "number"
    ? `<p>本次计数：${numerator} / ${denominator}${denominator === 0 ? "（无有效分母，不显示分数）" : ""}</p>` : "";
  const outcomes: Record<string, string> = { passed: "通过", failed: "未通过", repaired: "已确认修复",
    unresolved: "尚未确认修复", hinted: "首评前使用提示", not_hinted: "首评前未记录提示" };
  const evidence = item.evidence;
  const tasks = evidence ? `<div class="task-evidence"><p>计入本维度的具体样本：共 ${evidence.totalCount} 条，当前展示 ${evidence.items.length} 条${evidence.totalCount > evidence.items.length ? `（最多 ${evidence.limit} 条）` : ""}。</p>
    ${evidence.items.length ? `<ul>${evidence.items.map((entry) => `<li><strong>${escapeHtml(entry.title)}</strong>
      <span>${escapeHtml(outcomes[entry.outcome] || "有效评估样本")}${entry.assisted ? " · 使用过提示" : ""}</span>
      <small>${escapeHtml(entry.courseId)} / ${escapeHtml(entry.lessonId)} / ${escapeHtml(entry.taskId)}${entry.episodeIndex !== undefined ? ` · 过程 ${entry.episodeIndex}` : ""}</small></li>`).join("")}</ul>` : `<p>暂无计入样本，不据此判断未学会。</p>`}</div>` : "";
  return `<details class="evidence-details"><summary>查看计算方式与样本说明</summary>
    <p class="formula">${formula.expression}</p>${counts}<p>${formula.sample}</p>
    <p>证据量按样本数标记：0 为证据不足，1–4 为少量证据，5 及以上为证据较多；不表示统计置信度。平台聊天及外部帮助未被这些指标完整观测。</p>${tasks}</details>`;
}

export function dimensionCard(item: DiagnosisDimension, showEvidence = false): string {
  const dashboardNames: Record<string, string> = { knowledge: "知识任务表现", debugging: "失败过程修复率",
    transfer: "新情境首评通过率", hint_dependency: "提示使用率（Hint Dependency）" };
  const name = showEvidence ? dashboardNames[item.key] || item.name : item.name;
  const measured = typeof item.score === "number" && Number.isFinite(item.score);
  const score = measured
    ? `${item.score!.toFixed(1)}<span>${showEvidence || item.direction === "descriptive" ? "%" : " / 100"}</span>`
    : "证据不足";
  const progress = measured
    ? `<progress max="100" value="${Math.max(0, Math.min(100, item.score!))}" aria-label="${escapeHtml(name)}"></progress>`
    : `<div class="empty-meter" aria-hidden="true"></div>`;
  const hintMetrics = item.key === "hint_dependency" && item.metrics
    ? `<p class="metrics">提示后出现有效通过的请求比例：${percentage(item.metrics.hintProductivity)}（时间关联，不代表因果）</p>`
    : "";
  return `<article class="dimension${showEvidence && item.direction === "descriptive" ? " descriptive" : ""}">
    <div class="dimension-heading"><${showEvidence ? "h3" : "h2"}>${escapeHtml(name)}</${showEvidence ? "h3" : "h2"}><strong class="score${measured ? "" : " missing"}">${score}</strong></div>
    ${progress}
    <p class="direction">${escapeHtml(DIRECTIONS[item.key] || "分数反映当前可观测的学习表现。")}</p>
    <p>${escapeHtml(item.summary)}</p>
    ${hintMetrics}
    <p class="evidence">证据量：${EVIDENCE[item.evidenceLevel || item.confidence || "none"] || "证据不足"} · ${item.sampleSize ?? item.evidenceCount} ${SAMPLE_UNITS[item.key] || "个样本"}</p>
    ${showEvidence ? evidenceDetails(item) : ""}
  </article>`;
}

export function diagnosisContent(result: DiagnosisResult): string {
  const exercise = result.recommendedExercise;
  return `<section class="summary" aria-labelledby="summary-title">
    <div class="eyebrow">本次学习建议</div><h2 id="summary-title">${escapeHtml(result.diagnosis)}</h2>
    <p>已记录 ${result.eventCount} 条学习事件，其中 ${result.eligibleEventCount} 条可用于本次诊断。完成新的练习后，点击「刷新诊断」查看变化。</p>
  </section>
  ${result.eventCount === 0 ? `<div class="notice">还没有学习记录。请先从课程列表打开一课，运行并提交练习；证据不足时不会给出能力分数。</div>` : ""}
  <div class="dimensions">${result.dimensions.map((item) => dimensionCard(item)).join("")}</div>
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
