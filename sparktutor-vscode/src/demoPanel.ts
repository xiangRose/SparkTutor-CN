import * as vscode from "vscode";
import { randomUUID } from "crypto";
import { demoContent, DemoViewState } from "./demoView";
import { DemoProgress, DemoReport, DemoRunRequest, DemoSelection } from "./demoTypes";

/** The run lock survives closing the panel; closing a webview never cancels Python. */
export class DemoPanel {
  private panel: vscode.WebviewPanel | null = null;
  private version = 0;
  private runSequence = 0;
  private running = false;
  private runPanel: vscode.WebviewPanel | null = null;
  private activeRunId: string | null = null;
  private selection: DemoSelection = { scenarioId: "transfer_retry", mode: "recorded" };
  private report: DemoReport | null = null;
  private stageIndex = 0;
  private progress: DemoProgress | null = null;
  private error = "";
  private notice = "";

  constructor(private readonly extensionUri: vscode.Uri,
    private readonly runScenario: (selection: DemoRunRequest) => Promise<DemoReport>) {}

  show(): void {
    if (!this.panel) {
      const panel = vscode.window.createWebviewPanel("sparktutorDemo", "SparkTutor 诊断演示", vscode.ViewColumn.One,
        { enableScripts: true, retainContextWhenHidden: true,
          localResourceRoots: [vscode.Uri.joinPath(this.extensionUri, "media")] });
      this.panel = panel;
      this.report = null; this.error = ""; this.notice = ""; this.progress = null;
      panel.onDidDispose(() => {
        if (this.panel !== panel) { return; }
        this.panel = null; this.report = null; this.progress = null; this.version++;
      });
      panel.webview.onDidReceiveMessage(async (message: Record<string, unknown>) => {
        if (this.panel !== panel || !message || message.version !== this.version || this.running) { return; }
        if (message.type === "selection" && (message.mode === "recorded" || message.mode === "spark") &&
          (message.scenarioId === "transfer_retry" || message.scenarioId === "transfer_first_pass")) {
          this.selection = { mode: message.mode, scenarioId: message.scenarioId };
          this.report = null; this.error = ""; this.notice = ""; this.progress = null; this.stageIndex = 0; this.render();
        } else if (message.type === "run") { await this.run(); }
        else if (message.type === "stage" && typeof message.stageIndex === "number" && Number.isInteger(message.stageIndex) &&
          message.stageIndex >= 0 && this.report?.stages[message.stageIndex]) {
          this.stageIndex = message.stageIndex; this.render("demo-stage");
        }
      });
    } else { this.panel.reveal(vscode.ViewColumn.One); }
    this.render();
  }

  /** Progress carries only stage metadata, and belongs only to its original live panel. */
  updateProgress(value: Record<string, unknown>): void {
    if (!this.running || !this.panel || this.panel !== this.runPanel || typeof value.runId !== "string" ||
      value.runId !== this.activeRunId || typeof value.id !== "string" ||
      typeof value.title !== "string" || typeof value.index !== "number" || !Number.isInteger(value.index) ||
      typeof value.total !== "number" || !Number.isInteger(value.total) || value.total < 1 || value.index < 1 || value.index > value.total) { return; }
    this.progress = { runId: value.runId, id: value.id, title: value.title, index: value.index, total: value.total };
    this.render();
  }

  private validReport(report: DemoReport, selection: DemoRunRequest): boolean {
    return Boolean(report && report.schemaVersion === 1 && report.isDemo === true && report.isolated === true &&
      report.scenarioId === selection.scenarioId && report.mode === selection.mode &&
      report.runId === selection.runId &&
      typeof report.sparkVerified === "boolean" && (report.mode !== "recorded" || report.sparkVerified === false) &&
      report.taskA && report.taskB && Array.isArray(report.checks) && Array.isArray(report.stages) && report.stages.length &&
      report.stages.every((stage) => stage && stage.diagnosis && Array.isArray(stage.diagnosis.dimensions) &&
        Array.isArray(stage.events) && Array.isArray(stage.checks)));
  }

  private async run(): Promise<void> {
    if (this.running || !this.panel) { return; }
    const panel = this.panel, sequence = ++this.runSequence, selection = { ...this.selection, runId: randomUUID() };
    this.activeRunId = selection.runId;
    this.running = true; this.runPanel = panel; this.report = null; this.stageIndex = 0;
    this.progress = null; this.error = ""; this.notice = ""; this.render();
    try {
      const report = await this.runScenario(selection);
      if (this.panel !== panel || sequence !== this.runSequence) { return; }
      if (!this.validReport(report, selection)) { throw new Error("Invalid demo report"); }
      this.report = report;
    } catch {
      if (this.panel !== panel || sequence !== this.runSequence) { return; }
      // Do not echo exception text: subprocess errors may contain code, paths or secrets.
      this.error = selection.mode === "spark"
        ? "真实 Spark 演示未完成。请检查本机 PySpark 与 Java 环境。本次未收到可核查的完整报告，不会显示验证通过，也没有改写正常学习记录。若请求超时，后台可能仍在执行，请避免连续重试。"
        : "合成演示请求未完成。请确认 SparkTutor 后端正常后重试。本次未收到可核查的完整报告，不会显示验证通过，也没有改写正常学习记录。";
      this.report = null;
    } finally {
      if (sequence === this.runSequence) {
        this.running = false; this.runPanel = null; this.activeRunId = null; this.progress = null;
        if (this.panel && this.panel !== panel) {
          this.notice = "先前演示请求已结束，旧报告未加载到此窗口。现在可以重新运行所选演示。";
        }
        this.render();
      }
    }
  }

  private render(focus = ""): void {
    if (!this.panel) { return; }
    const webview = this.panel.webview;
    const resource = (file: string) => webview.asWebviewUri(vscode.Uri.joinPath(this.extensionUri, "media", file));
    const state: DemoViewState = { selection: this.selection, report: this.report, stageIndex: this.stageIndex,
      running: this.running, detachedRun: this.running && this.runPanel !== this.panel,
      progress: this.progress, error: this.error, notice: this.notice };
    webview.html = `<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
      <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src ${webview.cspSource}; script-src ${webview.cspSource};">
      <link rel="stylesheet" href="${resource("diagnosis.css")}"><link rel="stylesheet" href="${resource("dashboard.css")}">
      <link rel="stylesheet" href="${resource("learningHistory.css")}"><link rel="stylesheet" href="${resource("demo.css")}">
      <title>SparkTutor 诊断演示</title></head><body data-version="${++this.version}" data-focus="${focus}"><main>${demoContent(state)}</main>
      <script src="${resource("demo.js")}"></script></body></html>`;
  }

  dispose(): void { this.panel?.dispose(); this.panel = null; this.report = null; }
}
