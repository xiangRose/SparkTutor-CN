/** Webview actions resolve only against the latest report owned by the extension. */
import * as vscode from "vscode";
import { dashboardContent, dashboardError, dashboardLoading } from "./dashboardView";
import { DashboardCourse, LearningDashboardResult, RecommendedExercise } from "./types";

export interface DashboardLessonTarget {
  courseId: string;
  lessonId: string;
  lessonIdx: number;
  depth: string;
}

export class DashboardPanel {
  private panel: vscode.WebviewPanel | null = null;
  private report: LearningDashboardResult | null = null;
  private catalog: DashboardCourse[] = [];
  private selectedCourseId = "";
  private version = 0;
  private opening = false;

  constructor(
    private readonly extensionUri: vscode.Uri,
    private readonly load: (courseId?: string) => Promise<LearningDashboardResult>,
    private readonly openLesson: (target: DashboardLessonTarget) => Promise<void>,
    private readonly openRecommendation: (exercise: RecommendedExercise, scopeCourseId?: string) => Promise<void>,
    private readonly showHistory: () => Promise<void>,
    private readonly showLearningHistory?: () => Promise<void>,
    private readonly showDemo?: () => Promise<void>
  ) {}

  async show(): Promise<void> {
    if (!this.panel) {
      const panel = vscode.window.createWebviewPanel("sparktutorDashboard", "SparkTutor 学习工作台", vscode.ViewColumn.One,
        { enableScripts: true, retainContextWhenHidden: true,
          localResourceRoots: [vscode.Uri.joinPath(this.extensionUri, "media")] });
      this.panel = panel;
      panel.onDidDispose(() => {
        if (this.panel !== panel) { return; }
        this.panel = null; this.report = null; this.opening = false; this.version++;
      });
      panel.webview.onDidReceiveMessage(async (message: Record<string, unknown>) => {
        if (this.panel !== panel || this.opening || !message || message.version !== this.version) { return; }
        if (message.type === "refresh") { await this.refresh(); }
        else if (message.type === "filter" && typeof message.courseId === "string" &&
          (!message.courseId || this.catalog.some((course) => course.id === message.courseId))) {
          this.selectedCourseId = message.courseId;
          await this.refresh();
        } else if (message.type === "history") {
          await this.perform(() => this.showHistory(), "学习行为记录已在输出窗口打开。");
        } else if (message.type === "learningHistory" && this.showLearningHistory) {
          await this.perform(() => this.showLearningHistory!(), "学习历史与复盘已打开。");
        } else if (message.type === "demo" && this.showDemo) {
          await this.perform(() => this.showDemo!(), "独立诊断演示已打开，请选择场景并明确运行。");
        } else if (message.type === "resume") {
          const resume = this.report?.resume;
          if (!resume) { return; }
          const target = this.target(resume.courseId, resume.lessonId);
          if (target) { await this.perform(() => this.openLesson({ ...target, depth: resume.depth }), "已打开保存的课节位置，可继续学习。"); }
        } else if (message.type === "openLesson") {
          const target = this.target(message.courseId, message.lessonId);
          if (target) { await this.perform(() => this.openLesson(target), "课节已打开，已有进度与代码已保留。"); }
        } else if (message.type === "openRecommendation") {
          const exercise = this.report?.diagnosis?.recommendedExercise;
          if (exercise) {
            const scope = this.report?.selectedCourseId || undefined;
            await this.perform(() => this.openRecommendation(exercise, scope), "推荐练习已打开。完成后刷新工作台查看新证据。");
          }
        }
      });
    } else { this.panel.reveal(vscode.ViewColumn.One); }
    await this.refresh();
  }

  private target(courseId: unknown, lessonId: unknown): DashboardLessonTarget | null {
    if (typeof courseId !== "string" || typeof lessonId !== "string") { return null; }
    const course = this.report?.courses.find((item) => item.id === courseId);
    const lesson = course?.lessons.find((item) => item.id === lessonId);
    return course && lesson && lesson.available !== false && lesson.status !== "unavailable"
      ? { courseId: course.id, lessonId: lesson.id, lessonIdx: lesson.index, depth: lesson.depth } : null;
  }

  private async refresh(): Promise<void> {
    if (!this.panel || this.opening) { return; }
    const panel = this.panel, version = ++this.version, scope = this.selectedCourseId;
    this.report = null;
    this.render(dashboardLoading(this.catalog, scope));
    try {
      const report = await this.load(scope || undefined);
      if (this.panel !== panel || this.version !== version) { return; }
      this.report = report; this.catalog = report.courses; this.selectedCourseId = report.selectedCourseId;
      this.render(dashboardContent(report));
    } catch (error) {
      if (this.panel !== panel || this.version !== version) { return; }
      this.render(dashboardError(error instanceof Error ? error.message : String(error), this.catalog, scope));
    }
  }

  private async perform(action: () => Promise<void>, success: string): Promise<void> {
    if (this.opening || !this.panel) { return; }
    const panel = this.panel, version = this.version;
    this.opening = true;
    panel.webview.postMessage({ type: "actionStatus", version, busy: true, message: "正在打开……", error: false });
    try {
      await action();
      if (this.panel === panel && this.version === version) {
        panel.webview.postMessage({ type: "actionStatus", version, busy: false, message: success, error: false });
      }
    } catch (error) {
      if (this.panel === panel && this.version === version) {
        panel.webview.postMessage({ type: "actionStatus", version, busy: false, error: true,
          message: `打开失败：${error instanceof Error ? error.message : String(error)}。请重试，或刷新后重新选择。` });
      }
    } finally { if (this.panel === panel && this.version === version) { this.opening = false; } }
  }

  private render(content: string): void {
    if (!this.panel) { return; }
    const webview = this.panel.webview;
    const resource = (file: string) => webview.asWebviewUri(vscode.Uri.joinPath(this.extensionUri, "media", file));
    webview.html = `<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8">
      <meta name="viewport" content="width=device-width, initial-scale=1.0">
      <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src ${webview.cspSource}; script-src ${webview.cspSource};">
      <link rel="stylesheet" href="${resource("diagnosis.css")}"><link rel="stylesheet" href="${resource("dashboard.css")}">
      <title>SparkTutor 学习工作台</title></head><body data-version="${this.version}"><main>${content}</main>
      <script src="${resource("dashboard.js")}"></script></body></html>`;
  }

  dispose(): void { this.version++; this.panel?.dispose(); this.panel = null; this.report = null; }
}
