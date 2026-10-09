import { escapeHtml } from "./lessonHelpers";
import { HistoryCourse, HistoryEvent, HistoryPage, HistoryTask, HistoryWindow, LearningHistoryResult,
  LearningReviewResult, LearningTrendResult, ReviewChange, ReviewDimension, TaskHistoryResult } from "./learningHistoryTypes";

export interface HistoryViewState {
  courses: HistoryCourse[];
  courseId: string;
  window: HistoryWindow;
  report: LearningHistoryResult | null;
  loading: boolean;
  error: string;
  task: HistoryTask | null;
  detail: TaskHistoryResult | null;
  detailLoading: boolean;
  detailError: string;
  selectedEvent: HistoryEvent | null;
  selectedTrendIndex: number | null;
  review: LearningReviewResult | null;
  reviewLoading: boolean;
  reviewError: string;
  trend: LearningTrendResult | null;
  trendLoading: boolean;
  trendError: string;
}

const EVENT_NAMES: Record<string, string> = {
  task_open: "打开题目", task_start: "开始题目", task_complete: "完成题目", lesson_loaded: "加载课节",
  code_edit: "编辑代码", code_run: "运行代码", code_submit: "提交答案", error: "出现错误",
  hint_request: "请求提示", chat_request: "向导师提问", solution_view: "查看参考答案",
};
const DIMENSION_NAMES: Record<string, string> = { knowledge: "知识任务表现", debugging: "失败过程修复率",
  transfer: "新情境首评通过率", hint_dependency: "提示使用率（描述性）" };
const UNITS: Record<string, string> = { knowledge: "个已评估任务", debugging: "个可观察失败过程",
  transfer: "个有效迁移任务", hint_dependency: "个已评估任务" };
const FORMULAS: Record<string, string> = { knowledge: "最近有效评估通过任务 / 已评估任务",
  debugging: "确认修复过程 / 可观察失败过程", transfer: "新情境首评通过任务 / 有效迁移任务",
  hint_dependency: "首评前使用提示的任务 / 已评估任务" };
const number = (value: number) => Number.isFinite(value) ? String(value) : "未知";
const percent = (value: number | null) => typeof value === "number" && Number.isFinite(value) ? `${value.toFixed(1)}%` : "证据不足";
const signed = (value: number) => Number.isFinite(value) ? `${value > 0 ? "+" : ""}${value}` : "未知";
const time = (timestamp: string) => {
  const date = new Date(timestamp);
  return Number.isNaN(date.getTime()) ? "时间未知" : date.toLocaleString("zh-CN", { hour12: false });
};
const resultLabel = (event: HistoryEvent | null) => !event || event.passed === null ? "未评估" : event.passed ? "通过" : "未通过";

function pager(page: HistoryPage, prefix: string): string {
  return `<nav class="history-pagination" aria-label="${prefix === "tasks" ? "任务" : "事件"}分页">
    <button data-action="${prefix}Previous"${page.offset <= 0 ? " disabled" : ""}>上一页</button>
    <span>${page.total ? `${number(page.offset + 1)}–${number(Math.min(page.offset + page.limit, page.total))}` : "0"} / ${number(page.total)} 条</span>
    <button data-action="${prefix}Next"${!page.hasMore ? " disabled" : ""}>下一页</button></nav>`;
}

function loading(title: string): string { return `<div class="notice" role="status" aria-live="polite">${escapeHtml(title)}</div>`; }
function failure(message: string, action: string): string {
  return `<div class="notice error" role="alert"><p>${escapeHtml(message)}</p><button data-action="${action}">重试</button></div>`;
}

function taskCard(task: HistoryTask, index: number, selected: boolean): string {
  const counts = task.counts;
  return `<article class="history-task${selected ? " selected-task" : ""}"><div class="task-heading"><div>
    <p class="eyebrow">${escapeHtml(task.courseTitle)} · ${escapeHtml(task.lessonTitle)}</p><h3>${escapeHtml(task.title)}</h3></div>
    <span class="badge">${task.available ? "学习任务" : "历史题目"}</span></div>
    <p class="latest-result">最近提交：<strong>${resultLabel(task.latestSubmission)}</strong></p>
    ${task.latestSubmission ? `<p class="task-note">${escapeHtml(task.latestSubmission.reason)}</p>` : ""}
    <p class="task-note">最近未受答案查看影响的有效评估：${resultLabel(task.latestAssessment)}</p>
    <dl class="task-counts">${[["提交", counts.submissions], ["有效评估", counts.eligibleAssessments], ["运行", counts.runs],
      ["提示", counts.hints], ["查看答案", counts.answers], ["编辑记录", counts.edits]].map(([name, value]) =>
      `<div><dt>${name}</dt><dd>${number(value as number)}</dd></div>`).join("")}</dl>
    ${task.answerViewed ? `<p class="task-note">完整历史中查看过参考答案；受影响的结果会单独标明。</p>` : ""}
    ${!task.available ? `<p class="task-note">该题目前不在课程目录中，已有记录仍可查阅。</p>` : ""}
    <div class="task-footer"><span class="muted">最近活动 ${escapeHtml(time(task.lastActivityAt))}</span>
      <button data-action="selectTask" data-task-index="${index}"${selected ? ' aria-current="true"' : ""}>查看过程</button></div></article>`;
}

function eventRow(event: HistoryEvent, index: number, selected: boolean): string {
  const category = { assessment: "有效评估记录", debug_failure: "可观察失败记录", answer_view: "答案查看",
    behavior: "行为记录", excluded: "不计分记录" }[event.category] || "历史记录";
  const edits = event.editCounts;
  return `<li class="timeline-event${selected ? " selected-event" : ""}"><div class="event-heading"><div><time>${escapeHtml(time(event.timestamp))}</time>
    <h4>${escapeHtml(EVENT_NAMES[event.eventType] || "其他学习事件")}${event.passed === null ? "" : ` · ${resultLabel(event)}`}</h4></div>
    <span class="badge">${category}</span></div>
    <p>${escapeHtml(event.reason)}</p>
    ${event.answerInfluenced ? `<p class="evidence-note">此结果发生在答案查看之后，不能作为未受答案影响的通过证据。</p>` : ""}
    ${event.mode === "dry_run" ? `<p class="evidence-note">仅 Python 语法检查，未执行 Spark；其资格以本条记录说明为准。</p>` : ""}
    ${edits ? `<p class="event-counts">${number(edits.changeCount)} 次变更 · 新增 ${number(edits.addedChars)} · 删除 ${number(edits.removedChars)} 字符（UTF-16 单位）</p>` : ""}
    <p class="event-meta">${event.eventType === "code_submit" && event.attemptNumber ? `第 ${number(event.attemptNumber)} 次提交 · ` : ""}${event.hintUsed ? "记录了提示使用 · " : ""}
      ${event.exitCode !== undefined ? `退出码 ${number(event.exitCode)} · ` : ""}记录标识 ${escapeHtml(event.eventId)}</p>
    ${event.reviewable ? `<button data-action="reviewEvent" data-event-index="${index}">查看这条记录的前后变化</button>` : ""}</li>`;
}

function snapshot(dimension: ReviewDimension | undefined, label: string): string {
  return `<div class="review-snapshot"><span>${label}</span><strong>${percent(dimension?.score ?? null)}</strong>
    <p>样本 ${dimension ? number(dimension.sampleSize) : "未知"} ${UNITS[dimension?.key || ""] || ""}</p>
    <p>分子 / 分母：${dimension ? `${number(dimension.numerator)} / ${number(dimension.denominator)}` : "未知"}</p></div>`;
}

function reviewCard(before: ReviewDimension | undefined, after: ReviewDimension, change?: ReviewChange): string {
  const descriptive = after.key === "hint_dependency" || after.direction === "descriptive";
  const delta = change?.delta;
  const comparable = before?.score !== null && before?.score !== undefined && after.score !== null && typeof delta === "number" && Number.isFinite(delta);
  return `<article class="review-dimension${descriptive ? " review-descriptive" : ""}"><div class="eyebrow">${descriptive ? "求助行为 · 不表示能力高低" : "行为证据比例"}</div>
    <h4>${escapeHtml(DIMENSION_NAMES[after.key] || after.name)}</h4><p class="muted">${escapeHtml(FORMULAS[after.key] || "以有效记录计算")}</p>
    <div class="snapshot-pair">${snapshot(before, "记录前")}${snapshot(after, "记录后")}</div>
    <p class="review-change">${comparable ? `比例变化 ${signed(Number(delta.toFixed(1)))} 个百分点` :
      (before?.score === null || before === undefined) && after.score !== null ? "新增可用证据，不计算百分点变化" : "证据不足，不计算百分点变化"}</p>
    ${change ? `<p>样本变化 ${signed(change.sampleDelta)} · 分子变化 ${signed(change.numeratorDelta)} · 分母变化 ${signed(change.denominatorDelta)}</p>
    <p class="change-explanation">${escapeHtml(change.explanation)}</p>` : ""}</article>`;
}

export function reviewContent(review: LearningReviewResult): string {
  const dimensions = review.after.dimensions;
  const card = (dimension: ReviewDimension) => reviewCard(review.before.dimensions.find((item) => item.key === dimension.key), dimension,
    review.changes.find((item) => item.key === dimension.key));
  return `<section class="learning-review" aria-labelledby="review-heading"><div class="section-heading"><div><div class="eyebrow">在当时的证据下重新看一次</div>
    <h3 id="review-heading">记录前后变化</h3><p class="muted">${escapeHtml(review.task.title)}</p></div><span class="muted">${escapeHtml(time(review.event.timestamp))}</span></div>
    <p class="review-reason">${escapeHtml(review.reason)}</p>
    <p class="section-help">使用该记录发生前后的完整历史前缀计算，并保留跨课来源证据。列表时间窗口不截断评分依据；这不是一次干预的因果效果，也不代表能力提升。</p>
    <div class="review-dimensions">${dimensions.filter((item) => item.key !== "hint_dependency").map(card).join("")}</div>
    ${dimensions.filter((item) => item.key === "hint_dependency").map(card).join("")}
    <p class="disclaimer">${escapeHtml(review.disclaimer)}</p></section>`;
}

function taskDetail(state: HistoryViewState): string {
  const task = state.task;
  if (!task) { return ""; }
  const detail = state.detail;
  return `<section id="task-detail" class="task-detail" tabindex="-1" aria-labelledby="task-detail-heading"><div class="section-heading"><div><div class="eyebrow">任务时间线</div>
    <h2 id="task-detail-heading">${escapeHtml(task.title)}</h2><p class="muted">${escapeHtml(task.courseTitle)} · ${escapeHtml(task.lessonTitle)}</p></div>
    <button data-action="closeTask">收起详情</button></div>
    <p class="section-help">显示该题完整时间线，按时间正序分页，不受上方时间窗口限制。时间按本机时区显示；每条记录的计分资格请看具体说明。</p>
    ${state.detailLoading ? loading("正在读取任务过程……") : state.detailError ? failure(state.detailError, "retryTask") : detail ?
      `${detail.events.length ? `<ol class="event-timeline">${detail.events.map((event, index) => eventRow(event, index, state.selectedEvent?.eventId === event.eventId)).join("")}</ol>` : `<p class="notice">该题暂无可展示的事件。</p>`}
      ${pager(detail, "events")}<p class="disclaimer">${escapeHtml(detail.disclaimer)}</p>` : ""}
    </section>`;
}

function trendChart(trend: LearningTrendResult, key: string): string {
  // Record positions are equally spaced, not elapsed time. Nulls break the line.
  const points = trend.points;
  const x = (index: number) => points.length <= 1 ? 160 : 22 + index / (points.length - 1) * 280;
  const y = (score: number) => 106 - Math.max(0, Math.min(100, score)) * .84;
  const segments: string[] = [];
  let current: string[] = [];
  const circles: string[] = [];
  points.forEach((point, index) => {
    const dimension = point.dimensions.find((item) => item.key === key);
    if (dimension?.score === null || dimension?.score === undefined || !Number.isFinite(dimension.score)) {
      if (current.length) { segments.push(current.join(" ")); current = []; }
      return;
    }
    current.push(`${x(index).toFixed(2)},${y(dimension.score).toFixed(2)}`);
    circles.push(`<circle cx="${x(index).toFixed(2)}" cy="${y(dimension.score).toFixed(2)}" r="3"><title>记录 ${index + 1}：${percent(dimension.score)}，样本 ${number(dimension.sampleSize)}，分子/分母 ${number(dimension.numerator)}/${number(dimension.denominator)}</title></circle>`);
  });
  if (current.length) { segments.push(current.join(" ")); }
  return `<article class="trend-chart${key === "hint_dependency" ? " trend-descriptive" : ""}"><h3>${DIMENSION_NAMES[key]}</h3>
    <svg viewBox="0 0 320 130" role="img" aria-label="${DIMENSION_NAMES[key]}最近证据比例，详情见下方记录按钮">
      <text x="0" y="19">100</text><text x="9" y="111">0</text><path class="chart-axis" d="M22 22V106H302"/>
      ${segments.map((points) => `<polyline points="${points}"/>`).join("")}${circles.join("")}
      <text x="22" y="125">较早记录</text><text x="253" y="125">较近记录</text></svg>
    ${key === "hint_dependency" ? `<p class="muted">描述提示覆盖，不解释为越高或越低越好。</p>` : ""}</article>`;
}

function trendContent(state: HistoryViewState): string {
  const trend = state.trend;
  return `<section id="history-trend" class="history-trend" tabindex="-1"><div class="section-heading"><div><div class="eyebrow">按需查看</div><h2>最近 20 个证据记录点</h2></div>
    <button data-action="loadTrend"${!state.report || state.trendLoading ? " disabled" : ""}>${trend ? "重新读取趋势" : "查看四维趋势"}</button></div>
    <p class="section-help">只显示所选范围内最近的证据点，每点按当时完整历史计算。横轴为记录顺序，不表示等长时间；证据不足的位置不补零、不连线。</p>
    ${state.trendLoading ? loading("正在按历史时点整理四维证据，请稍候……") : state.trendError ? failure(state.trendError, "loadTrend") : trend ?
      `${trend.points.length ? `<p class="muted">当前展示 ${number(trend.points.length)} / ${number(trend.total)} 个记录点${trend.hasMore ? "，更早证据仍参与每点计算" : ""}。</p>
      <div class="trend-charts">${["knowledge", "debugging", "transfer", "hint_dependency"].map((key) => trendChart(trend, key)).join("")}</div>
      <div class="trend-points" aria-label="选择证据记录点">${trend.points.map((point, index) => `<button data-action="selectTrend" data-point-index="${index}"${state.selectedTrendIndex === index ? ' aria-current="true"' : ""}>${index + 1}. ${escapeHtml(time(point.timestamp))} · ${escapeHtml(point.eventLabel)} · ${escapeHtml(point.title)}</button>`).join("")}</div>
      ${state.selectedTrendIndex !== null && trend.points[state.selectedTrendIndex] ? `<div class="point-evidence"><h3>所选记录点的证据</h3><ul>${trend.points[state.selectedTrendIndex].dimensions.map((dimension) => `<li><strong>${escapeHtml(DIMENSION_NAMES[dimension.key] || dimension.name)}</strong> ${percent(dimension.score)} · 样本 ${number(dimension.sampleSize)} · 分子 / 分母 ${number(dimension.numerator)} / ${number(dimension.denominator)}</li>`).join("")}</ul></div>` : ""}` : `<p class="notice">所选范围暂无可展示的证据点。仅有编辑或求助记录时，不生成评分趋势。</p>`}
      <p class="disclaimer">${escapeHtml(trend.disclaimer)}</p>` : ""}</section>`;
}

export function learningHistoryContent(state: HistoryViewState): string {
  const report = state.report;
  return `<header class="dashboard-header"><div class="brand"><span class="brand-mark" aria-hidden="true">S</span><div>
    <div class="eyebrow">SPARKTUTOR · 看清每一次尝试</div><h1>学习历史与复盘</h1></div></div><button data-action="refresh"${state.loading ? " disabled" : ""}>刷新记录</button></header>
    <div class="history-filters"><label for="history-course">课程范围</label><select id="history-course"><option value="">全部课程</option>
      ${state.courses.map((course) => `<option value="${escapeHtml(course.id)}"${state.courseId === course.id ? " selected" : ""}>${escapeHtml(course.title)}${course.available ? "" : "（历史课程）"}</option>`).join("")}</select>
      <label for="history-window">活动时间</label><select id="history-window">${[["all", "全部时间"], ["7d", "近 7 天"], ["30d", "近 30 天"]].map(([value, title]) =>
        `<option value="${value}"${state.window === value ? " selected" : ""}>${title}</option>`).join("")}</select></div>
    <p class="section-help">卡片计数仅统计所选时间窗口；最近提交、最近有效评估及答案查看状态来自该题完整历史。编辑记录仅用于回看过程，不参与诊断评分。</p>
    ${(report?.warnings || []).map((warning) => `<p class="notice warning" role="status">${escapeHtml(warning)}</p>`).join("")}
    ${state.loading ? loading("正在读取任务历史……") : state.error ? failure(state.error, "refresh") : report ?
      `${report.tasks.length ? `<div class="history-tasks">${report.tasks.map((task, index) => taskCard(task, index,
        state.task?.courseId === task.courseId && state.task.lessonId === task.lessonId && state.task.taskId === task.taskId)).join("")}</div>` :
        `<section class="notice"><h2>这个范围内还没有任务记录</h2><p>试试切换课程或时间范围；也可以先在课程里完成一次尝试。没有记录不表示尚未学会。</p></section>`}
      ${pager(report, "tasks")}<p class="disclaimer">${escapeHtml(report.disclaimer)}</p>` : ""}
    ${trendContent(state)}${taskDetail(state)}
    <div id="history-review" tabindex="-1">${state.reviewLoading ? loading("正在按该记录时点重建前后证据……") : state.reviewError ? failure(state.reviewError, "retryReview") : state.review ? reviewContent(state.review) : ""}</div>
    <p id="history-action-status" role="status" aria-live="polite"></p>`;
}
