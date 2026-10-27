/**
 * Sidebar TreeDataProvider: shows courses and their lessons with progress icons.
 */

import * as vscode from "vscode";
import { Bridge } from "./bridge";
import { SparkOutputChannel } from "./outputChannel";
import { CourseMeta, CourseProgress } from "./types";

type TreeNode = HomeNode | CourseNode | LessonNode;

class HomeNode extends vscode.TreeItem {
  constructor() {
    super("学习首页", vscode.TreeItemCollapsibleState.None);
    this.id = "sparktutor.learningHome";
    this.iconPath = new vscode.ThemeIcon("home");
    this.description = "从这里开始";
    this.tooltip = "继续学习、选择课程，了解提示、导师问答与 AI 调试教练。";
    this.contextValue = "learningHome";
    this.command = { command: "sparktutor.openLearningDashboard", title: "打开学习首页" };
  }
}

class CourseNode extends vscode.TreeItem {
  constructor(
    public readonly course: CourseMeta,
    public progress: CourseProgress
  ) {
    super(course.title, vscode.TreeItemCollapsibleState.Expanded);
    this.description = `${course.lessonCount} 节课`;
    if (course.prerequisites?.length) {
      this.tooltip = course.description + "\n\n前置要求：\n" +
        course.prerequisites.map(p => `• ${p}`).join("\n");
    } else {
      this.tooltip = course.description;
    }
    this.contextValue = "course";
  }
}

class LessonNode extends vscode.TreeItem {
  constructor(
    public readonly courseId: string,
    public readonly lessonId: string,
    public readonly lessonIdx: number,
    label: string,
    public readonly status: "completed" | "in-progress" | "not-started" | "unavailable"
  ) {
    super(label, vscode.TreeItemCollapsibleState.None);
    this.contextValue = "lesson";

    switch (status) {
      case "completed":
        this.iconPath = new vscode.ThemeIcon(
          "pass-filled",
          new vscode.ThemeColor("testing.iconPassed")
        );
        break;
      case "in-progress":
        this.iconPath = new vscode.ThemeIcon(
          "circle-filled",
          new vscode.ThemeColor("charts.orange")
        );
        break;
      case "unavailable":
        this.iconPath = new vscode.ThemeIcon("warning");
        this.description = "暂不可用";
        break;
      default:
        this.iconPath = new vscode.ThemeIcon("circle-outline");
    }

    if (status !== "unavailable") { this.command = {
      command: "sparktutor.openLesson",
      title: "打开课程",
      arguments: [courseId, lessonIdx],
    }; }
  }
}

export class CourseTreeProvider
  implements vscode.TreeDataProvider<TreeNode>
{
  private _onDidChangeTreeData = new vscode.EventEmitter<
    TreeNode | undefined
  >();
  readonly onDidChangeTreeData = this._onDidChangeTreeData.event;

  private courses: CourseMeta[] = [];
  private progressMap = new Map<string, CourseProgress>();
  private retryCount = 0;

  constructor(private bridge: Bridge) {}

  refresh(): void {
    this._onDidChangeTreeData.fire(undefined);
  }

  async getChildren(element?: TreeNode): Promise<TreeNode[]> {
    if (!element) {
      // Keep the home entry available even while the course catalog is loading.
      const home = new HomeNode();
      try {
        const result = await this.bridge.call<{ courses: CourseMeta[] }>(
          "listCourses"
        );
        this.retryCount = 0;
        this.courses = result.courses;

        // Fetch progress for each course
        const nodes: CourseNode[] = [];
        for (const course of this.courses) {
          try {
            const progress = await this.bridge.call<CourseProgress>(
              "getCourseProgress",
              { courseId: course.id }
            );
            this.progressMap.set(course.id, progress);
            nodes.push(new CourseNode(course, progress));
          } catch {
            this.progressMap.delete(course.id);
            const node = new CourseNode(course, { started: false });
            node.description = "进度读取失败，请刷新重试";
            nodes.push(node);
          }
        }
        return [home, ...nodes];
      } catch (err) {
        // Log the failure so it is visible in the SparkTutor output channel,
        // then retry a few times: the tree view can render before the Python
        // server is fully ready, and without a refresh the tree would stay
        // empty forever.
        const message = err instanceof Error ? err.message : String(err);
        const channel = new SparkOutputChannel();
        channel.appendLine(`[tree] listCourses failed: ${message}`);
        if (this.retryCount < 3) {
          this.retryCount++;
          setTimeout(() => this.refresh(), 2000);
        }
        return [home];
      }
    }

    if (element instanceof CourseNode) {
      const course = element.course;
      const progress = element.progress;

      return course.lessons.map((lessonId, idx) => {
        const detail = progress.lessons?.find((lesson) => lesson.id === lessonId);
        let status: "completed" | "in-progress" | "not-started" | "unavailable" = "not-started";
        if (detail?.available === false || detail?.status === "unavailable") { status = "unavailable"; }
        else if (detail?.status === "completed") { status = "completed"; }
        else if (detail?.status === "in_progress" || (!detail && progress.started &&
          (progress.currentLessonId === lessonId || progress.currentLessonIdx === idx))) { status = "in-progress"; }

        // Prefer catalogue metadata so a missing progress response cannot
        // change the language or hide newly added lessons' display titles.
        const title = course.lessonTitles?.[lessonId]?.trim() || detail?.title?.trim() || "课节标题暂不可用";
        const label = `${idx + 1}. ${title}`;
        return new LessonNode(course.id, lessonId, idx, label, status);
      });
    }

    return [];
  }

  getTreeItem(element: TreeNode): vscode.TreeItem {
    return element;
  }
}
