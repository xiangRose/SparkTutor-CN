import * as vscode from "vscode";
import { CoachAck, CoachMessage, CoachProbe, CoachState, CoachTask, sameCoachTask } from "./coachTypes";
import { coachContent } from "./coachView";

export interface CoachActions {
  load(): Promise<CoachState>;
  request(level: number, current: () => boolean): Promise<CoachMessage>;
  acknowledge(message: CoachMessage, current: () => boolean): Promise<CoachAck>;
  probe(message: CoachMessage, current: () => boolean): Promise<CoachProbe>;
  discard(requestId: string): Promise<unknown>;
  openExercise(task: CoachTask): Promise<void>;
  configure(): Promise<void>;
}

/** Requests remain locked across close/reopen. UI versions never identify a backend task. */
export class CoachPanel {
  private panel: vscode.WebviewPanel | null = null;
  private epoch = 0;
  private version = 0;
  private busy = false;
  private loading = false;
  private state: CoachState | null = null;
  private message: CoachMessage | null = null;
  private acknowledged = false;
  private probeResult: CoachProbe | null = null;
  private reflection = false;
  private error = "";
  private notice = "";

  constructor(private readonly extensionUri: vscode.Uri, private readonly actions: CoachActions) {}

  async show(): Promise<void> {
    if (!this.panel) {
      const panel = vscode.window.createWebviewPanel("sparktutorCoach", "SparkTutor AI 调试教练", vscode.ViewColumn.Two,
        { enableScripts: true, retainContextWhenHidden: true,
          localResourceRoots: [vscode.Uri.joinPath(this.extensionUri, "media")] });
      this.panel = panel;
      panel.onDidDispose(() => {
        if (this.panel !== panel) { return; }
        this.panel = null;
        this.invalidate();
      });
      panel.webview.onDidReceiveMessage(async (value: Record<string, unknown>) => {
        if (this.panel !== panel || !value || value.version !== this.version || this.loading) { return; }
        if (value.type === "displayed") {
          if (!this.busy && this.message && !this.acknowledged && value.messageId === this.message.messageId) {
            await this.acknowledge();
          }
          return;
        }
        if (this.busy || (this.message && !this.acknowledged)) { return; }
        if (value.type === "refresh") { await this.refreshIfOpen(); }
        else if (value.type === "configure") { await this.configure(); }
        else if (value.type === "request" && typeof value.level === "number" && Number.isInteger(value.level) &&
          value.level >= 1 && value.level <= 3 && this.state?.available && this.state.task && value.level <= this.state.maxLevel) {
          await this.request(value.level);
        } else if (value.type === "open" && typeof value.index === "number" && Number.isInteger(value.index) && value.index >= 0) {
          const task = this.state?.exerciseOptions[value.index];
          if (task && !this.state?.available) { await this.openExercise(task); }
        } else if (value.type === "reflection" && this.message && this.acknowledged) {
          this.reflection = !this.reflection; this.render();
        } else if (value.type === "probe" && this.message?.probe && this.acknowledged) { await this.probe(); }
      });
    } else { this.panel.reveal(vscode.ViewColumn.Two); }
    if (!this.busy) { await this.refreshIfOpen(); }
    else { this.render(); }
  }

  private discardMessage(): void {
    if (this.message && !this.acknowledged) { void this.actions.discard(this.message.requestId).catch(() => {}); }
    this.message = null; this.acknowledged = false; this.probeResult = null; this.reflection = false;
  }

  /** Called for every current-file edit and before task/execution/help mutations. */
  invalidate(notice = "当前练习或代码已变化，请刷新证据后重新请求辅导。"): void {
    this.epoch++; this.loading = false; this.state = null; this.discardMessage();
    this.error = ""; this.notice = notice; this.render();
  }

  async refreshIfOpen(): Promise<void> {
    if (!this.panel) { return; }
    const panel = this.panel, epoch = ++this.epoch;
    this.discardMessage(); this.state = null; this.loading = true; this.error = ""; this.notice = ""; this.render();
    try {
      const state = await this.actions.load();
      if (this.panel === panel && epoch === this.epoch) { this.state = state; }
    } catch {
      if (this.panel === panel && epoch === this.epoch) { this.error = "暂时无法读取教练状态。请确认后端可用后刷新重试；没有生成辅导结果。"; }
    } finally {
      if (this.panel === panel && epoch === this.epoch) { this.loading = false; this.render(); }
    }
  }

  private async request(level: number): Promise<void> {
    const panel = this.panel, epoch = this.epoch, task = this.state?.task || null;
    const current = () => this.panel === panel && epoch === this.epoch;
    this.busy = true; this.error = ""; this.notice = ""; this.discardMessage(); this.render();
    try {
      const message = await this.actions.request(level, current);
      if (!current() || !sameCoachTask(task, message.task) || message.level !== level) {
        await this.actions.discard(message.requestId).catch(() => {});
        if (current()) { this.error = "回复与当前练习不匹配，已撤下。请刷新后重试。"; }
        return;
      }
      this.message = message;
    } catch (error) {
      if (current()) { this.error = error instanceof Error ? error.message : "AI 请求未完成，请检查模型配置后重试。"; }
    } finally { this.busy = false; this.render(); }
  }

  private async acknowledge(): Promise<void> {
    const panel = this.panel, epoch = this.epoch, message = this.message!;
    const current = () => this.panel === panel && epoch === this.epoch && this.message === message;
    this.busy = true;
    try {
      const result = await this.actions.acknowledge(message, current);
      if (!current()) { return; }
      if (!result.recorded && result.reason !== "already_acknowledged") { throw new Error("Display not confirmed"); }
      this.acknowledged = true;
      if (this.state) {
        this.state.lastLevel = Math.max(this.state.lastLevel, message.level);
        this.state.maxLevel = Math.min(3, this.state.lastLevel + 1);
      }
    } catch {
      if (current()) {
        this.discardMessage();
        this.error = "未能确认提示展示记录，内容已撤下。请刷新后重试；代码或题目变化时旧提示不会继续显示。";
      }
    } finally { this.busy = false; this.render(); }
  }

  private async probe(): Promise<void> {
    const panel = this.panel, epoch = this.epoch, message = this.message!;
    const current = () => this.panel === panel && epoch === this.epoch && this.message === message;
    this.busy = true; this.probeResult = null; this.error = ""; this.render();
    try {
      const result = await this.actions.probe(message, current);
      if (!current()) { return; }
      if (result.verified !== true || result.engine !== "python-reference") { throw new Error("Invalid probe result"); }
      this.probeResult = result;
    } catch {
      if (current()) {
        this.discardMessage();
        this.error = "小反例未能完成核查，或当前代码与证据已变化。旧内容已撤下，请刷新重试；本次没有课程判题结果。";
      }
    } finally { this.busy = false; this.render(); }
  }

  private async openExercise(task: CoachTask): Promise<void> {
    const panel = this.panel;
    this.busy = true; this.error = ""; this.render();
    try { await this.actions.openExercise(task); }
    catch { if (this.panel === panel) { this.error = "练习打开失败，请刷新后重试。"; } }
    finally { this.busy = false; this.render(); }
  }

  private async configure(): Promise<void> {
    this.busy = true; this.render();
    try { await this.actions.configure(); }
    catch { this.error = "未能打开模型配置，请从命令面板运行「SparkTutor：配置 AI 调试教练模型」。"; }
    finally { this.busy = false; this.render(); }
  }

  private render(): void {
    if (!this.panel) { return; }
    const webview = this.panel.webview;
    const resource = (file: string) => webview.asWebviewUri(vscode.Uri.joinPath(this.extensionUri, "media", file));
    const content = coachContent({ state: this.state, message: this.message, acknowledged: this.acknowledged,
      probe: this.probeResult, reflection: this.reflection, busy: this.busy, loading: this.loading, error: this.error, notice: this.notice });
    webview.html = `<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
      <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src ${webview.cspSource}; script-src ${webview.cspSource};">
      <link rel="stylesheet" href="${resource("diagnosis.css")}"><link rel="stylesheet" href="${resource("dashboard.css")}"><link rel="stylesheet" href="${resource("coach.css")}">
      <title>SparkTutor AI 调试教练</title></head><body data-version="${++this.version}"><main>${content}</main><script src="${resource("coach.js")}"></script></body></html>`;
  }

  dispose(): void { this.panel?.dispose(); this.panel = null; this.invalidate(); }
}
