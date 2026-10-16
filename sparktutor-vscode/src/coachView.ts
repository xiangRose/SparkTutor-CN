import { escapeHtml } from "./lessonHelpers";
import { CoachMessage, CoachProbe, CoachState } from "./coachTypes";

export const COACH_LEVELS = ["概念提问", "检查方法", "局部示例"];
export interface CoachViewState {
  state: CoachState | null;
  message: CoachMessage | null;
  acknowledged: boolean;
  probe: CoachProbe | null;
  reflection: boolean;
  busy: boolean;
  loading: boolean;
  error: string;
  notice: string;
}

const NAMES: Record<string, string> = {
  knowledge: "知识任务表现", debugging: "调试修复表现", transfer: "新情境首次表现", hint_dependency: "提示使用率（描述性）",
};
const UNITS: Record<string, string> = {
  knowledge: "个有效评估任务", debugging: "个可观察失败过程", transfer: "个符合来源条件的新情境任务", hint_dependency: "个有效评估任务",
};

export function coachContent(view: CoachViewState): string {
  const { state, message } = view;
  const blocked = view.busy || view.loading || Boolean(message && !view.acknowledged);
  const disabled = blocked ? " disabled" : "";
  let body = `<header class="dashboard-header"><div><div class="eyebrow">SparkTutor · 有依据地调试</div>
    <h1>AI 调试教练</h1><p>先解释你的判断，再用可核查的证据验证。</p></div>
    <div class="coach-actions"><button data-action="configure"${disabled}>配置其他模型</button><button data-action="refresh"${disabled}>刷新当前练习</button></div></header>
    <p class="muted">教练使用你配置的 OpenAI 兼容接口。API 地址、模型名称在本地设置中填写，密钥可安全保存；未配置时不会自动改用 Anthropic。</p>
    <p class="coach-scope">你选择帮助深度，AI 从审核过的教学节点中选择假设与相关证据，并可提出样例数据。假设尚待验证；教学措辞来自审核节点，教练不会修改代码或代替课程判题。</p>`;
  if (view.error) { body += `<p class="notice error" role="alert">${escapeHtml(view.error)}</p>`; }
  if (view.notice) { body += `<p class="notice" role="status">${escapeHtml(view.notice)}</p>`; }
  if (view.loading) { body += `<p role="status">正在读取当前任务和证据……</p>`; }
  if (view.busy) { body += `<p role="status" aria-live="polite">正在处理，请稍候。关闭面板不会取消已发出的请求；旧回复不会进入新练习。</p>`; }
  if (!state) { return body; }
  if (!state.available || !state.task) {
    return body + `<section class="notice"><h2>选择一项支持的练习</h2><p>${escapeHtml(state.reason || "当前练习尚未接入调试教练。")}</p>
      <p>目前先支持下面两道练习。打开后，在编辑器编写代码，再请求分级辅导；原课节进度会保留。</p>
      <div class="coach-options">${state.exerciseOptions.map((task, index) => `<button data-action="open" data-index="${index}"${disabled}>${escapeHtml(task.title)}</button>`).join("")}</div></section>`;
  }
  body += `<section class="coach-task"><div class="eyebrow">当前任务</div><h2>${escapeHtml(state.task.title)}</h2>
    <p class="muted">${escapeHtml(state.task.courseId)} · ${escapeHtml(state.task.taskId)}</p>
    ${state.answerViewed ? `<p class="notice">本题已记录参考答案查看。教练会保留这一背景，不能将之后的结果解释为未受答案影响的表现。</p>` : ""}</section>
    <section aria-label="四维证据摘要"><h2>请求前的证据摘要</h2><p class="muted">分别描述已有记录，不合成总分；缺少样本不等于零分或未学会。</p>
      <div class="coach-dimensions">${state.dimensions.map((item) => `<article class="coach-dimension${item.key === "hint_dependency" ? " descriptive" : ""}">
        <h3>${escapeHtml(NAMES[item.key] || item.name)}</h3><strong>${item.score === null ? "证据不足" : `${item.score.toFixed(1)}%`}</strong>
        <p>计数 ${item.numerator} / ${item.denominator} · 样本 ${item.sampleSize} ${escapeHtml(UNITS[item.key] || "条")}</p>
        ${item.key === "hint_dependency" ? "<p>描述求助方式，不用于判断能力强弱。</p>" : ""}</article>`).join("")}</div></section>
    <section class="coach-levels"><h2>选择帮助深度</h2><p>先从概念提问开始。每一级确认展示后，才开放下一级；可随时留在较浅层。</p>
      <div>${COACH_LEVELS.map((label, index) => `<button data-action="request" data-level="${index + 1}"${blocked || index + 1 > state.maxLevel ? " disabled" : ""}>${index + 1}. ${label}${index + 1 > state.maxLevel ? "（待解锁）" : ""}</button>`).join("")}</div>
      <p class="muted">已展示最高层：${state.lastLevel ? COACH_LEVELS[state.lastLevel - 1] : "尚未展示"}。请求使用当前代码与学习证据，需要已配置的 AI 提供方。</p></section>`;
  if (!message) { return body; }
  body += `<section id="coach-message" data-message-id="${escapeHtml(message.messageId)}" data-awaiting-ack="${view.acknowledged ? "false" : "true"}">
    ${!view.acknowledged ? `<p role="status">正在确认当前代码并登记提示展示，登记完成前暂不能请求更深提示或核查样例……</p>` : ""}
    <div class="coach-answer">
      <div class="eyebrow">${message.level}. ${COACH_LEVELS[message.level - 1]}</div>
      <h2>AI 选择的假设 <span class="coach-unverified">未验证</span></h2><p>${escapeHtml(message.hypothesis)}</p>
      <h3>先想一想</h3><p class="coach-text">${escapeHtml(message.question)}</p>
      <h3>尝试这一步</h3><p class="coach-text">${escapeHtml(message.guidance)}</p>
      <details class="coach-evidence"><summary>查看本次引用的真实证据（${message.evidence.length} 条）</summary>
        <ul>${message.evidence.map((item) => `<li><strong>${escapeHtml(item.title)}</strong><code>${escapeHtml(item.id)}</code><p>${escapeHtml(item.description)}</p></li>`).join("")}</ul></details>
      <p class="disclaimer">${escapeHtml(message.disclaimer)}</p>
      <div class="coach-actions"><button data-action="reflection"${disabled}>${view.reflection ? "收起反思问题" : "想一想如何迁移"}</button>
        ${message.probe ? `<button data-action="probe"${disabled}>核查小反例</button>` : ""}</div>
      ${view.reflection ? `<section class="notice"><h3>反思与迁移</h3><p class="coach-text">${escapeHtml(message.reflection)}</p><p class="muted">可自行思考或记在练习中。此处不生成反思分数，也不作为新的迁移评估。</p></section>` : ""}
      ${message.probe ? `<p class="muted">模型建议的样例数据：${escapeHtml(message.probe.values.join(", "))}。需点击核查后才有模板计算结果。</p>` : ""}
      ${view.probe ? `<section class="notice coach-probe"><h3>Python 参考模板验证</h3><p>未运行 Spark 或学生代码，不影响成绩，也不证明上述假设成立。</p>
        <p>输入：${escapeHtml(view.probe.values.join(", "))}</p><p>行数：${view.probe.rowCount} · 数量总和：${view.probe.quantitySum}</p><p>${escapeHtml(view.probe.disclaimer)}</p></section>` : ""}
    </div></section>`;
  return body;
}
