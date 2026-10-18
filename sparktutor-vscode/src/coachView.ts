import { escapeHtml } from "./lessonHelpers";
import { CoachMessage, CoachProbe, CoachState } from "./coachTypes";

export const COACH_LEVELS = ["概念提问", "检查方法", "局部示例"];
const LEVEL_HELP = ["先厘清概念和思路", "找到可以动手检查的地方", "借助局部示例理解差异"];
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

function coachDetails(state: CoachState | null): string {
  const history = state?.available ? `<details class="coach-history"><summary>查看课程历史学习画像 · 四个维度</summary>
    <p class="muted">以下汇总当前课程历史，不代表当前这道题已通过，也不是本题得分。分别描述已有记录，不合成总分；缺少样本不等于零分或未学会。</p>
    <div class="coach-dimensions">${state.dimensions.map((item) => `<article class="coach-dimension${item.key === "hint_dependency" ? " descriptive" : ""}">
      <h3>${escapeHtml(NAMES[item.key] || item.name)}</h3><strong>${item.score === null ? "证据不足" : `${item.score.toFixed(1)}%`}</strong>
      <p>计数 ${item.numerator} / ${item.denominator} · 样本 ${item.sampleSize} ${escapeHtml(UNITS[item.key] || "条")}</p>
      ${item.key === "hint_dependency" ? "<p>描述求助方式，不用于判断能力强弱。</p>" : ""}</article>`).join("")}</div></details>` : "";
  return `${history}<details class="coach-about"><summary>教练如何给提示？模型怎么配置？</summary>
    <p>教练结合当前代码、已有运行反馈和课程学习记录，从预设的教学方向中选择一个待验证的解释，并引用相关记录。教学措辞来自审核节点；具体问题仍需要你检查代码，再通过课程测试确认。</p>
    <p>点击“配置教练模型”，填写你自己的 OpenAI 兼容接口地址、模型名称和密钥。接口与模型名称保存在本地设置中，密钥可保存在安全存储中；未配置时不会自动改用其他提供方。</p>
    <p>求助记录用于描述学习过程，不用于直接判断能力强弱。小样例只计算行数与数量总和，不执行你的代码，也不代替课程判题。</p>
    ${state?.task ? `<p class="muted">任务标识：${escapeHtml(state.task.courseId)} · ${escapeHtml(state.task.taskId)}</p>` : ""}</details>`;
}

export function coachContent(view: CoachViewState): string {
  const { state, message } = view;
  const blocked = view.busy || view.loading || Boolean(message && !view.acknowledged);
  const disabled = blocked ? " disabled" : "";
  let body = `<header class="dashboard-header"><div><div class="eyebrow">SparkTutor · 练习帮助</div>
    <h1>AI 调试教练</h1><p class="coach-lead">写 Spark 练习卡住时，帮你一步步排查问题。</p></div>
    <div class="coach-actions"><button data-action="home"${disabled}>返回学习首页</button><button data-action="configure"${disabled}>配置教练模型</button><button data-action="refresh"${disabled}>刷新当前练习</button></div></header>
    <section class="coach-intro" aria-label="教练使用说明"><p>根据当前代码、已有运行反馈和学习记录，给你排查方向。你自己修改代码，再点“提交判题”检查是否通过。</p>
      <ol class="coach-steps"><li><strong>打开支持的练习</strong><span>目前支持 M&amp;M 糖果计数分析、传感器聚合练习。</span></li>
        <li><strong>先试一试，保留反馈</strong><span>建议先运行或提交一次，已有报错和结果有助于排查；也可以先问概念。</span></li>
        <li><strong>从第 1 级开始求助</strong><span>看提示、改代码，再提交判题；仍不清楚时再看更深一层。</span></li></ol>
      <p class="coach-scope">教练提供提示，不会自动修改代码，也不负责判断本题是否通过。</p></section>`;
  if (view.error) { body += `<p class="notice error" role="alert">${escapeHtml(view.error)}</p>`; }
  if (view.notice) { body += `<p class="notice" role="status">${escapeHtml(view.notice)}</p>`; }
  if (view.loading) { body += `<p role="status">正在读取当前练习和学习记录……</p>`; }
  if (view.busy) { body += `<p role="status" aria-live="polite">正在处理，请稍候。关闭面板不会取消已发出的请求；旧回复不会进入新练习。</p>`; }
  if (!state) { return body + coachDetails(state); }
  if (!state.available || !state.task) {
    return body + `<section class="notice"><h2>选择一项支持的练习</h2><p>${escapeHtml(state.reason || "当前练习尚未接入调试教练。")}</p>
      <p>点击下面的练习进入编辑器，尝试作答后回来请求帮助；原课节进度会保留。</p>
      <div class="coach-options">${state.exerciseOptions.map((task, index) => `<button data-action="open" data-index="${index}"${disabled}>${escapeHtml(task.title)}</button>`).join("")}</div></section>` + coachDetails(state);
  }
  body += `<section class="coach-task"><div class="eyebrow">当前任务</div><h2>${escapeHtml(state.task.title)}</h2>
    ${state.answerViewed ? `<p class="notice">本题已记录参考答案查看。教练会保留这一背景，不能将之后的结果解释为未受答案影响的表现。</p>` : ""}</section>
    <section class="coach-levels"><h2>从哪一步开始排查？</h2><p>先点“1. 概念提问”。看过后才开放下一级；当前提示够用时，就回到代码中试一试。</p>
      <div>${COACH_LEVELS.map((label, index) => `<button class="coach-level-button" data-action="request" data-level="${index + 1}"${blocked || index + 1 > state.maxLevel ? " disabled" : ""}><strong>${index + 1}. ${label}${index + 1 > state.maxLevel ? "（待解锁）" : ""}</strong><span>${LEVEL_HELP[index]}</span></button>`).join("")}</div>
      <p class="muted">已看过：${state.lastLevel ? COACH_LEVELS[state.lastLevel - 1] : "尚未请求提示"}。首次使用请先配置教练模型。修改代码后，刷新当前练习再请求新的提示。</p></section>`;
  if (!message) { return body + coachDetails(state); }
  body += `<section id="coach-message" data-message-id="${escapeHtml(message.messageId)}" data-awaiting-ack="${view.acknowledged ? "false" : "true"}">
    ${!view.acknowledged ? `<p role="status">正在确认当前代码并登记提示展示，登记完成前暂不能请求更深提示或核查样例……</p>` : ""}
    <div class="coach-answer">
      <div class="eyebrow">${message.level}. ${COACH_LEVELS[message.level - 1]}</div>
      <h2>可以先排查这个方向 <span class="coach-unverified">未验证</span></h2><p>${escapeHtml(message.hypothesis)}</p>
      <h3>先想一想</h3><p class="coach-text">${escapeHtml(message.question)}</p>
      <h3>尝试这一步</h3><p class="coach-text">${escapeHtml(message.guidance)}</p>
      <details class="coach-evidence"><summary>为什么给这条提示？查看参考记录（${message.evidence.length} 条）</summary>
        <ul>${message.evidence.map((item) => `<li><strong>${escapeHtml(item.title)}</strong><code>${escapeHtml(item.id)}</code><p>${escapeHtml(item.description)}</p></li>`).join("")}</ul>
        <p class="disclaimer">${escapeHtml(message.disclaimer)}</p></details>
      <p class="muted">这是排查方向，还需要你通过代码和课程测试确认。</p>
      <div class="coach-actions"><button data-action="reflection"${disabled}>${view.reflection ? "收起反思问题" : "想一想如何迁移"}</button>
        ${message.probe ? `<button data-action="probe"${disabled}>比较样例的行数与总和</button>` : ""}</div>
      ${view.reflection ? `<section class="notice"><h3>反思与迁移</h3><p class="coach-text">${escapeHtml(message.reflection)}</p><p class="muted">可自行思考或记在练习中。此处不生成反思分数，也不作为新的迁移评估。</p></section>` : ""}
      ${message.probe ? `<p class="muted">供你理解的小样例：${escapeHtml(message.probe.values.join(", "))}。点击上方按钮比较行数与数量总和；这不会运行你的代码。</p>` : ""}
      ${view.probe ? `<section class="notice coach-probe"><h3>样例计算结果</h3><p>未运行 Spark 或学生代码，不影响成绩，也不证明上述假设成立。</p>
        <p>输入：${escapeHtml(view.probe.values.join(", "))}</p><p>行数：${view.probe.rowCount} · 数量总和：${view.probe.quantitySum}</p><p>${escapeHtml(view.probe.disclaimer)}</p></section>` : ""}
    </div></section>`;
  return body + coachDetails(state);
}
