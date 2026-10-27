import * as vscode from "vscode";
import { environmentContent } from "./environmentView";
import { ENVIRONMENT_ACTIONS, EnvironmentAction, EnvironmentReport, isEnvironmentReport } from "./environmentTypes";

/** Setup stays reachable even if the Python bridge cannot start. */
export class EnvironmentPanel {
  private panel: vscode.WebviewPanel | null = null;
  private report: EnvironmentReport | null = null;
  private version = 0;
  private sequence = 0;
  private loading = false;
  private busy = false;
  private error = "";
  private notice = "";

  constructor(private readonly extensionUri: vscode.Uri,
    private readonly load: () => Promise<EnvironmentReport>,
    private readonly action: (id: EnvironmentAction) => Promise<void>) {}

  async show(): Promise<void> {
    if (!this.panel) {
      const panel = vscode.window.createWebviewPanel("sparktutorEnvironment", "SparkTutor 环境检查", vscode.ViewColumn.One,
        { enableScripts: true, retainContextWhenHidden: true, localResourceRoots: [vscode.Uri.joinPath(this.extensionUri, "media")] });
      this.panel = panel;
      panel.onDidDispose(() => {
        if (this.panel !== panel) { return; }
        this.panel = null; this.report = null; this.loading = false; this.busy = false; this.sequence++; this.version++;
      });
      panel.webview.onDidReceiveMessage(async (message: Record<string, unknown>) => {
        if (this.panel !== panel || !message || message.version !== this.version || this.busy) { return; }
        if (message.type === "refresh") { await this.refresh(); }
        else if (typeof message.type === "string" && ENVIRONMENT_ACTIONS.includes(message.type as EnvironmentAction)) {
          await this.perform(message.type as EnvironmentAction);
        }
      });
    } else { this.panel.reveal(vscode.ViewColumn.One); }
    await this.refresh();
  }

  async refresh(): Promise<void> {
    if (!this.panel || this.busy) { return; }
    const panel = this.panel, sequence = ++this.sequence;
    this.report = null; this.error = ""; this.notice = ""; this.loading = true; this.render();
    try {
      const report = await this.load();
      if (this.panel !== panel || this.sequence !== sequence) { return; }
      if (!isEnvironmentReport(report)) { throw new Error("Invalid environment report"); }
      this.report = report;
    } catch {
      if (this.panel !== panel || this.sequence !== sequence) { return; }
      // Subprocess and model exceptions may include credentials; keep this fixed.
      this.error = "未收到可用的环境报告。请检查 Python 路径，并查看启动诊断；修改后重载窗口或刷新检查。";
    } finally {
      if (this.panel === panel && this.sequence === sequence) { this.loading = false; this.render(); }
    }
  }

  private async perform(id: EnvironmentAction): Promise<void> {
    if (!this.panel || this.busy) { return; }
    const panel = this.panel, sequence = ++this.sequence;
    this.busy = true; this.loading = false; this.error = ""; this.notice = ""; this.render();
    try {
      await this.action(id);
      if (this.panel !== panel || this.sequence !== sequence) { return; }
      this.notice = "操作已完成。修改配置后，请刷新检查。";
    } catch {
      if (this.panel !== panel || this.sequence !== sequence) { return; }
      this.error = "操作未完成。请查看启动诊断，并检查相应配置后重试。";
    } finally {
      if (this.panel === panel && this.sequence === sequence) { this.busy = false; this.render(); }
    }
  }

  private render(): void {
    if (!this.panel) { return; }
    const webview = this.panel.webview;
    const resource = (file: string) => webview.asWebviewUri(vscode.Uri.joinPath(this.extensionUri, "media", file));
    webview.html = `<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
      <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src ${webview.cspSource}; script-src ${webview.cspSource};">
      <link rel="stylesheet" href="${resource("diagnosis.css")}"><link rel="stylesheet" href="${resource("dashboard.css")}"><title>SparkTutor 环境检查</title></head>
      <body data-version="${++this.version}"><main>${environmentContent({ report: this.report, loading: this.loading, busy: this.busy, error: this.error, notice: this.notice })}</main>
      <script src="${resource("dashboard.js")}"></script></body></html>`;
  }

  dispose(): void { this.panel?.dispose(); this.panel = null; this.report = null; this.sequence++; }
}
