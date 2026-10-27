import { escapeHtml } from "./lessonHelpers";
import { EnvironmentAction, EnvironmentReport } from "./environmentTypes";

export interface EnvironmentViewState { report: EnvironmentReport | null; loading: boolean; busy: boolean; error: string; notice: string }
const STATUS = { available: "已发现", missing: "缺少", warning: "需要检查", unverified: "尚未验证" };
const MODE: Record<string, string> = { auto: "自动选择", local: "本地 Spark", lakehouse: "Docker 湖仓", databricks: "Databricks", dry_run: "仅语法检查" };
function list(items: string[]): string { return items.length ? `<ul>${items.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>` : ""; }
function button(id: EnvironmentAction | "refresh", label: string, disabled: boolean): string {
  return `<button data-action="${id}"${disabled ? " disabled" : ""}>${label}</button>`;
}

export function environmentContent(state: EnvironmentViewState): string {
  const { report, loading, busy } = state;
  const blocked = busy;
  return `<header class="dashboard-header"><div class="brand"><span class="brand-mark" aria-hidden="true">S</span><div><div class="eyebrow">SPARKTUTOR · 首次使用</div><h1>环境检查</h1></div></div><span class="badge">只读检查 · 未运行 Spark</span></header>
    <p>先看目前能做哪些练习，再按缺项完成配置。检查不会安装依赖、执行作业、连接远端或请求模型。</p>
    <nav class="dashboard-actions" aria-label="环境操作">${button("refresh", loading ? "正在检查……" : "刷新检查", blocked || loading)}
      ${button("pythonSettings", "选择 Python", blocked)}${button("modeSettings", "设置执行模式", blocked)}
      ${button("reload", "重载窗口", blocked)}${button("diagnostics", "查看启动诊断", blocked)}</nav>
    ${loading ? `<p class="notice" role="status">正在检查当前 Python 与 Java 前置条件；不会运行 Spark 作业。</p>` : ""}
    ${state.error ? `<section class="notice error" role="alert"><h2>检查未完成</h2><p>${escapeHtml(state.error)}</p></section>` : ""}
    ${report?.backend?.pythonPath ? `<p>当前 Python：<code>${escapeHtml(report.backend.pythonPath)}</code></p>` : ""}
    ${report?.backend?.available === false || (!report && !loading) ? `<section class="notice"><h2>Python 后端尚未就绪</h2><ol><li>选择已安装 SparkTutor 的 Python 解释器；Windows 可填写虚拟环境 python.exe 的完整路径。</li><li>若提示找不到 sparktutor，请在该 Python 环境安装当前项目：python -m pip install -e .</li><li>打开启动诊断查看原因，调整后重载窗口，再刷新检查。</li></ol><p>后端恢复后才能确认课程、进度和代码检查功能是否可用。</p></section>` : ""}
    ${report ? `<section><h2>现在可以做什么</h2>${report.capabilities.map((item) => `<article class="notice"><h3>${escapeHtml(item.title)} · ${item.availability === "available" ? "可开始" : item.availability === "needs_setup" ? "需要配置" : "需要实际运行验证"}</h3><p>${escapeHtml(item.detail)}</p></article>`).join("")}</section>
      <section><h2>执行方式：${escapeHtml(MODE[report.execution.configuredMode] || report.execution.configuredMode)}</h2><p>${escapeHtml(report.execution.summary)}</p>${list(report.execution.nextSteps)}</section>
      <section aria-label="本地前置条件"><h2>本地前置条件</h2>${report.checks.map((item) => `<article class="notice"><h3>${escapeHtml(item.title)} · ${STATUS[item.status]}</h3><p>${escapeHtml(item.summary)}</p>${list(item.details)}${item.nextSteps.length ? `<h4>下一步</h4>${list(item.nextSteps)}` : ""}</article>`).join("")}</section>` : ""}
    <section class="notice"><h2>AI 导师与调试教练</h2><p>${report?.ai ? escapeHtml(report.ai.summary) : "尚未读取 AI 配置。AI 并非阅读课程、选择题和本地检查的必需项。"}</p>
      ${report?.ai ? `<p>提供方：${escapeHtml(report.ai.provider)} · ${report.ai.configured ? "已发现配置，连接与输出质量仍需单独验证。" : "配置尚不完整，可先使用无需 AI 的功能。"}</p>` : ""}
      <div class="dashboard-actions">${button("aiSettings", "AI 设置", blocked)}${button("configureCoach", "配置教练模型", blocked)}${button("checkAi", "检查 AI 连接", blocked)}</div>
      <p class="muted">本页刷新只检查配置；点击「检查 AI 连接」会另行发起请求。配置状态不能证明教练的教学效果。</p></section>
    <p class="muted">找到安装项不等于真实 Spark 执行已通过。请在可信的本地环境运行练习代码；当前执行器不是不可信 Python 的安全沙箱。</p>
    ${state.notice ? `<p class="notice" role="status">${escapeHtml(state.notice)}</p>` : ""}<p id="dashboard-status" role="status" aria-live="polite"></p>`;
}
