import * as vscode from "vscode";
import { HistoryViewState, learningHistoryContent } from "./learningHistoryView";
import { HistoryQuery, HistoryTask, HistoryTaskRef, HistoryWindow, LearningHistoryResult, LearningReviewQuery,
  LearningReviewResult, LearningTrendQuery, LearningTrendResult, TaskHistoryQuery, TaskHistoryResult } from "./learningHistoryTypes";

export interface LearningHistoryApi {
  history(query: HistoryQuery): Promise<LearningHistoryResult>;
  task(query: TaskHistoryQuery): Promise<TaskHistoryResult>;
  review(query: LearningReviewQuery): Promise<LearningReviewResult>;
  trend(query: LearningTrendQuery): Promise<LearningTrendResult>;
}

const taskRef = (task: HistoryTaskRef): HistoryTaskRef => ({ courseId: task.courseId, lessonId: task.lessonId, taskId: task.taskId });
const messageError = (error: unknown) => error instanceof Error ? error.message : String(error);
const itemAt = <T>(items: T[] | undefined, value: unknown): T | undefined =>
  typeof value === "number" && Number.isInteger(value) && value >= 0 ? items?.[value] : undefined;

export class LearningHistoryPanel {
  private panel: vscode.WebviewPanel | null = null;
  private renderVersion = 0;
  private listSequence = 0;
  private detailSequence = 0;
  private reviewSequence = 0;
  private trendSequence = 0;
  private snapshotEventId: string | undefined;
  private windowEnd: string | undefined;
  private detailOffset = 0;
  private reviewTarget: LearningReviewQuery | null = null;
  private state: HistoryViewState = { courses: [], courseId: "", window: "all", report: null, loading: false, error: "",
    task: null, detail: null, detailLoading: false, detailError: "", selectedEvent: null,
    selectedTrendIndex: null, review: null, reviewLoading: false, reviewError: "",
    trend: null, trendLoading: false, trendError: "" };

  constructor(private readonly extensionUri: vscode.Uri, private readonly api: LearningHistoryApi) {}

  async show(): Promise<void> {
    if (!this.panel) {
      const panel = vscode.window.createWebviewPanel("sparktutorLearningHistory", "SparkTutor 学习历史与复盘", vscode.ViewColumn.One,
        { enableScripts: true, retainContextWhenHidden: true,
          localResourceRoots: [vscode.Uri.joinPath(this.extensionUri, "media")] });
      this.panel = panel;
      panel.onDidDispose(() => {
        if (this.panel !== panel) { return; }
        this.panel = null; this.listSequence++; this.invalidateDetails(); this.state.report = null;
      });
      panel.webview.onDidReceiveMessage(async (message: Record<string, unknown>) => {
        if (this.panel !== panel || !message || message.version !== this.renderVersion) { return; }
        await this.receive(message);
      });
    } else { this.panel.reveal(vscode.ViewColumn.One); }
    await this.loadHistory();
  }

  private async receive(message: Record<string, unknown>): Promise<void> {
    const report = this.state.report, detail = this.state.detail;
    if (message.type === "refresh") { await this.loadHistory(); }
    else if (message.type === "filter") {
      const courseId = message.courseId, window = message.window;
      if (typeof courseId !== "string" || (courseId && !this.state.courses.some((course) => course.id === courseId)) ||
        !["all", "7d", "30d"].includes(String(window))) { return; }
      this.state.courseId = courseId; this.state.window = window as HistoryWindow;
      await this.loadHistory();
    } else if (message.type === "tasksNext" && report?.hasMore) {
      await this.loadHistory(report.offset + report.limit, true);
    } else if (message.type === "tasksPrevious" && report && report.offset > 0) {
      await this.loadHistory(Math.max(0, report.offset - report.limit), true);
    } else if (message.type === "selectTask") {
      const task = itemAt(report?.tasks, message.taskIndex);
      if (task) { await this.loadTask(task); }
    } else if (message.type === "eventsNext" && detail?.hasMore && this.state.task) {
      await this.loadTask(this.state.task, detail.offset + detail.limit);
    } else if (message.type === "eventsPrevious" && detail && detail.offset > 0 && this.state.task) {
      await this.loadTask(this.state.task, Math.max(0, detail.offset - detail.limit));
    } else if (message.type === "retryTask" && this.state.task) {
      await this.loadTask(this.state.task, this.detailOffset);
    } else if (message.type === "closeTask") {
      this.detailSequence++; this.clearReview(); this.state.task = null; this.state.detail = null;
      this.state.detailLoading = false; this.state.detailError = ""; this.render();
    } else if (message.type === "reviewEvent" && detail) {
      const event = itemAt(detail.events, message.eventIndex);
      if (!event?.reviewable) { return; }
      this.state.selectedEvent = event; this.state.selectedTrendIndex = null;
      await this.loadReview({ ...taskRef(detail.task), eventId: event.eventId,
        scopeCourseId: this.state.courseId, snapshotEventId: this.snapshotEventId });
    } else if (message.type === "retryReview" && this.reviewTarget) {
      await this.loadReview(this.reviewTarget);
    } else if (message.type === "loadTrend" && report) {
      await this.loadTrend();
    } else if (message.type === "selectTrend") {
      const point = itemAt(this.state.trend?.points, message.pointIndex);
      if (!point) { return; }
      this.state.selectedTrendIndex = message.pointIndex as number; this.state.selectedEvent = null;
      await this.loadReview({ ...taskRef(point), eventId: point.eventId,
        scopeCourseId: this.state.courseId, snapshotEventId: this.snapshotEventId });
    }
  }

  private clearReview(): void {
    this.reviewSequence++; this.reviewTarget = null; this.state.review = null;
    this.state.reviewLoading = false; this.state.reviewError = "";
    this.state.selectedEvent = null; this.state.selectedTrendIndex = null;
  }

  private invalidateDetails(): void {
    this.detailSequence++; this.trendSequence++; this.clearReview();
    this.state.task = null; this.state.detail = null; this.state.detailLoading = false; this.state.detailError = "";
    this.state.trend = null; this.state.trendLoading = false; this.state.trendError = "";
  }

  private async loadHistory(offset = 0, preserveSnapshot = false): Promise<void> {
    if (!this.panel) { return; }
    const panel = this.panel, sequence = ++this.listSequence;
    if (!preserveSnapshot) { this.snapshotEventId = undefined; this.windowEnd = undefined; }
    const query: HistoryQuery = { courseId: this.state.courseId, window: this.state.window, offset, limit: 20,
      ...(this.snapshotEventId !== undefined ? { snapshotEventId: this.snapshotEventId } : {}),
      ...(this.windowEnd !== undefined ? { windowEnd: this.windowEnd } : {}) };
    this.invalidateDetails(); this.state.report = null; this.state.loading = true; this.state.error = ""; this.render();
    try {
      const result = await this.api.history(query);
      if (this.panel !== panel || sequence !== this.listSequence) { return; }
      this.state.report = result; this.state.courses = result.courses; this.snapshotEventId = result.snapshotEventId;
      this.windowEnd = result.windowEnd;
      this.state.courseId = result.courseId; this.state.window = result.window;
    } catch (error) {
      if (this.panel !== panel || sequence !== this.listSequence) { return; }
      this.state.error = `读取学习历史失败：${messageError(error)}`;
    } finally {
      if (this.panel === panel && sequence === this.listSequence) { this.state.loading = false; this.render(); }
    }
  }

  private async loadTask(task: HistoryTask, offset = 0): Promise<void> {
    if (!this.panel) { return; }
    const panel = this.panel, sequence = ++this.detailSequence, listSequence = this.listSequence;
    this.clearReview(); this.detailOffset = offset; this.state.task = task;
    this.state.detail = null; this.state.detailLoading = true; this.state.detailError = ""; this.render("task-detail");
    try {
      const result = await this.api.task({ ...taskRef(task), offset, limit: 50, snapshotEventId: this.snapshotEventId });
      if (this.panel !== panel || listSequence !== this.listSequence || sequence !== this.detailSequence) { return; }
      this.state.detail = result;
    } catch (error) {
      if (this.panel !== panel || listSequence !== this.listSequence || sequence !== this.detailSequence) { return; }
      this.state.detailError = `读取任务过程失败：${messageError(error)}`;
    } finally {
      if (this.panel === panel && listSequence === this.listSequence && sequence === this.detailSequence) {
        this.state.detailLoading = false; this.render("task-detail");
      }
    }
  }

  private async loadReview(target: LearningReviewQuery): Promise<void> {
    if (!this.panel) { return; }
    const panel = this.panel, sequence = ++this.reviewSequence, listSequence = this.listSequence;
    this.reviewTarget = target; this.state.review = null; this.state.reviewLoading = true; this.state.reviewError = ""; this.render("history-review");
    try {
      const result = await this.api.review(target);
      if (this.panel !== panel || listSequence !== this.listSequence || sequence !== this.reviewSequence) { return; }
      this.state.review = result;
    } catch (error) {
      if (this.panel !== panel || listSequence !== this.listSequence || sequence !== this.reviewSequence) { return; }
      this.state.reviewError = `读取记录复盘失败：${messageError(error)}`;
    } finally {
      if (this.panel === panel && listSequence === this.listSequence && sequence === this.reviewSequence) {
        this.state.reviewLoading = false; this.render("history-review");
      }
    }
  }

  private async loadTrend(): Promise<void> {
    if (!this.panel || this.state.trendLoading) { return; }
    const panel = this.panel, sequence = ++this.trendSequence, listSequence = this.listSequence;
    this.clearReview(); this.state.trend = null; this.state.trendLoading = true; this.state.trendError = ""; this.render("history-trend");
    try {
      const result = await this.api.trend({ courseId: this.state.courseId, window: this.state.window,
        snapshotEventId: this.snapshotEventId, windowEnd: this.windowEnd });
      if (this.panel !== panel || listSequence !== this.listSequence || sequence !== this.trendSequence) { return; }
      this.state.trend = result;
    } catch (error) {
      if (this.panel !== panel || listSequence !== this.listSequence || sequence !== this.trendSequence) { return; }
      this.state.trendError = `读取四维趋势失败：${messageError(error)}`;
    } finally {
      if (this.panel === panel && listSequence === this.listSequence && sequence === this.trendSequence) {
        this.state.trendLoading = false; this.render("history-trend");
      }
    }
  }

  private render(focus = ""): void {
    if (!this.panel) { return; }
    const webview = this.panel.webview;
    const resource = (file: string) => webview.asWebviewUri(vscode.Uri.joinPath(this.extensionUri, "media", file));
    webview.html = `<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
      <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src ${webview.cspSource}; script-src ${webview.cspSource};">
      <link rel="stylesheet" href="${resource("diagnosis.css")}"><link rel="stylesheet" href="${resource("dashboard.css")}">
      <link rel="stylesheet" href="${resource("learningHistory.css")}"><title>SparkTutor 学习历史与复盘</title></head>
      <body data-version="${++this.renderVersion}" data-focus="${focus}"><main>${learningHistoryContent(this.state)}</main>
      <script src="${resource("learningHistory.js")}"></script></body></html>`;
  }

  dispose(): void { this.listSequence++; this.invalidateDetails(); this.panel?.dispose(); this.panel = null; }
}
