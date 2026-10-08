/** Learning diagnosis panel. Recommendations stay in the extension, never in webview messages. */
import * as vscode from "vscode";
import { diagnosisContent, diagnosisError, diagnosisLoading } from "./diagnosisView";
import { DiagnosisResult, RecommendedExercise } from "./types";

export class DiagnosisPanel {
  private panel: vscode.WebviewPanel | null = null;
  private result: DiagnosisResult | null = null;
  private requestVersion = 0;
  private opening = false;

  constructor(
    private readonly extensionUri: vscode.Uri,
    private readonly load: () => Promise<DiagnosisResult>,
    private readonly openExercise: (exercise: RecommendedExercise) => Promise<void>
  ) {}

  async show(): Promise<void> {
    if (!this.panel) {
      this.panel = vscode.window.createWebviewPanel(
        "sparktutorDiagnosis", "SparkTutor 学习诊断", vscode.ViewColumn.One,
        { enableScripts: true, retainContextWhenHidden: true,
          localResourceRoots: [vscode.Uri.joinPath(this.extensionUri, "media")] }
      );
      this.panel.onDidDispose(() => {
        this.panel = null;
        this.result = null;
        this.opening = false;
        this.requestVersion++;
      });
      this.panel.webview.onDidReceiveMessage(async (message: { type?: string }) => {
        if (message.type === "refresh") { await this.refresh(); }
        if (message.type === "openExercise") { await this.openRecommendation(); }
      });
    } else {
      this.panel.reveal(vscode.ViewColumn.One);
    }
    await this.refresh();
  }

  private async refresh(): Promise<void> {
    if (!this.panel || this.opening) { return; }
    const panel = this.panel;
    const version = ++this.requestVersion;
    this.result = null;
    this.setContent(diagnosisLoading(), true);
    try {
      const result = await this.load();
      if (this.panel !== panel || version !== this.requestVersion) { return; }
      this.result = result;
      this.setContent(diagnosisContent(result));
    } catch (error) {
      if (this.panel !== panel || version !== this.requestVersion) { return; }
      this.setContent(diagnosisError(error instanceof Error ? error.message : String(error)));
    }
  }

  private async openRecommendation(): Promise<void> {
    const exercise = this.result?.recommendedExercise;
    if (!exercise || this.opening || !this.panel) { return; }
    const panel = this.panel;
    this.opening = true;
    try {
      await this.openExercise(exercise);
      if (this.panel === panel) {
        panel.webview.postMessage({ type: "exerciseStatus", message: "已打开推荐练习，请在题面旁完成作答。", error: false });
      }
    } catch (error) {
      if (this.panel === panel) {
        panel.webview.postMessage({ type: "exerciseStatus",
          message: `打开失败：${error instanceof Error ? error.message : String(error)}。可重试，或刷新诊断获取最新推荐。`, error: true });
      }
    } finally {
      if (this.panel === panel) { this.opening = false; }
    }
  }

  private setContent(content: string, loading = false): void {
    if (!this.panel) { return; }
    const webview = this.panel.webview;
    const css = webview.asWebviewUri(vscode.Uri.joinPath(this.extensionUri, "media", "diagnosis.css"));
    const js = webview.asWebviewUri(vscode.Uri.joinPath(this.extensionUri, "media", "diagnosis.js"));
    webview.html = `<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8">
      <meta name="viewport" content="width=device-width, initial-scale=1.0">
      <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src ${webview.cspSource}; script-src ${webview.cspSource};">
      <link rel="stylesheet" href="${css}"><title>SparkTutor 学习诊断</title></head>
      <body><header><div><h1>学习诊断</h1><p class="scope">全部课程学习记录 · 形成性反馈</p></div>
      <button id="refresh"${loading ? " disabled" : ""}>刷新诊断</button></header><main>${content}</main>
      <script src="${js}"></script></body></html>`;
  }

  dispose(): void {
    this.requestVersion++;
    this.panel?.dispose();
    this.panel = null;
  }
}
