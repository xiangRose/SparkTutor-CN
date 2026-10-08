/** A small, readable output view of recent events; no dashboard or raw payload dump. */
import * as vscode from "vscode";
import { Bridge } from "./bridge";

export interface LearningEventRecord {
  eventType: string;
  timestamp: string;
  courseId?: string;
  lessonId?: string;
  taskId?: string;
  data?: Record<string, unknown>;
}

const EVENT_NAMES: Record<string, string> = {
  session_start: "会话开始", session_end: "会话结束", lesson_loaded: "加载课节",
  task_open: "打开题目", task_start: "开始题目", task_complete: "完成题目",
  code_edit: "编辑代码", code_run: "运行代码", code_submit: "提交答案",
  error: "出现错误", hint_request: "请求提示", chat_request: "向导师提问", solution_view: "查看参考答案",
};

export function formatLearningEvent(event: LearningEventRecord): string {
  const date = new Date(event.timestamp);
  const time = Number.isNaN(date.getTime()) ? "时间未知" : date.toLocaleString("zh-CN", { hour12: false });
  const label = EVENT_NAMES[event.eventType] || "其他学习事件";
  const task = event.taskId ? `课程 ${event.courseId || "未标记"} · 题目 ${event.taskId}` : "会话记录";
  const data = event.data || {};
  const details: string[] = [];
  if (event.eventType === "code_edit") {
    for (const [field, name] of [["changeCount", "变更"], ["addedChars", "新增字符"], ["removedChars", "删除字符"]]) {
      const value = data[field];
      if (typeof value === "number" && Number.isInteger(value) && value >= 0) { details.push(`${name} ${value}`); }
    }
  }
  if (typeof data.passed === "boolean") { details.push(data.passed ? "结果：通过" : "结果：未通过"); }
  if (typeof data.exitCode === "number") { details.push(`退出码：${data.exitCode}`); }
  if (data.mode === "dry_run" || data.executionMode === "dry_run") { details.push("仅语法检查"); }
  return `[${time}] ${label} | ${task}${details.length ? ` | ${details.join("，")}` : ""}`;
}

export class LearningEventsView {
  private channel: vscode.OutputChannel | undefined;
  private requestVersion = 0;
  private disposed = false;
  constructor(private readonly bridge: Bridge) {}

  async show(): Promise<void> {
    if (this.disposed) { return; }
    const version = ++this.requestVersion;
    this.channel ??= vscode.window.createOutputChannel("SparkTutor 学习行为记录");
    const channel = this.channel;
    const active = () => !this.disposed && version === this.requestVersion;
    channel.show(true);
    channel.clear();
    channel.appendLine("正在读取最近的学习行为记录……");
    try {
      const result = await this.bridge.call<{ events: LearningEventRecord[]; hasMore: boolean }>(
        "getLearningEvents", { latest: true, limit: 200 }, 5000);
      if (!active()) { return; }
      channel.clear();
      channel.appendLine("SparkTutor 学习行为记录（最近 200 条，按时间正序；时间为本机时区）");
      channel.appendLine("编辑仅显示变更次数与字符计数（UTF-16 单位），不显示代码原文或文件路径，也不用于诊断评分。\n");
      if (!result.events.length) {
        channel.appendLine("暂无学习行为记录。打开课程并完成一次编辑、运行或提交后，重新执行本命令查看。");
      } else {
        result.events.forEach((event) => channel.appendLine(formatLearningEvent(event)));
      }
      if (result.hasMore) { channel.appendLine("\n还有更早记录，此处仅展示最近 200 条。"); }
      channel.appendLine("\n需要刷新时，请再次执行「SparkTutor：查看学习行为记录」。");
    } catch {
      if (!active()) { return; }
      channel.appendLine("读取失败。请确认 SparkTutor 后端正在运行，然后重试。");
      const choice = await vscode.window.showWarningMessage("暂时无法读取学习行为记录。", "重试");
      if (choice === "重试" && active()) { await this.show(); }
    }
  }

  dispose(): void { this.disposed = true; this.requestVersion++; this.channel?.dispose(); this.channel = undefined; }
}
