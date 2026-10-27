/** Explicit local learning-data actions. No AI service or configuration access. */
import * as fs from "fs/promises";
import * as vscode from "vscode";

export interface LearningDataOverview {
  databasePath: string;
  workspacePath: string;
  databaseExists: boolean;
  progressCount: number;
  completedLessonCount: number;
  eventCount: number;
  privacy: { summary: string; backup: string; clear: string; model: string; restore: string };
}

export interface DataManagementCallbacks {
  getOverview(): Promise<LearningDataOverview>;
  exportSummary(): Promise<unknown>;
  backupDatabase(destination: string): Promise<{ destination: string }>;
  clearRecords(): Promise<{ cleared: boolean; deletedProgress: number; deletedEvents: number }>;
  /** Called only after the database deletion succeeds. Reset live sessions here. */
  afterClear(): Promise<void>;
}

type DataAction = vscode.QuickPickItem & { action: "overview" | "summary" | "backup" | "clear" };

function localDestination(uri: vscode.Uri): string {
  if (uri.scheme !== "file") { throw new Error("请选择本机文件作为导出目标。"); }
  return uri.fsPath;
}

async function showOverview(overview: LearningDataOverview): Promise<void> {
  const content = [
    "SparkTutor 学习数据", "",
    `学习数据库：${overview.databasePath}${overview.databaseExists ? "" : "（尚未创建）"}`,
    `练习文件目录：${overview.workspacePath}`,
    `有进度的课节：${overview.progressCount}；已完成：${overview.completedLessonCount}；学习事件：${overview.eventCount}`,
    "", "导出学习摘要", overview.privacy.summary,
    "", "备份与迁移", overview.privacy.backup, overview.privacy.restore,
    "", "清除范围", overview.privacy.clear,
    "", "哪些内容会发送到模型", overview.privacy.model,
  ].join("\n");
  const document = await vscode.workspace.openTextDocument({ content, language: "plaintext" });
  await vscode.window.showTextDocument(document, { preview: true });
}

/** Open one deliberate data action. Closing any chooser or modal cancels it. */
export async function openDataManagement(callbacks: DataManagementCallbacks): Promise<void> {
  try {
    const items: DataAction[] = [
      { label: "查看数据位置与使用说明", description: "本地保存、模型发送范围、备份迁移", action: "overview" },
      { label: "导出学习摘要", description: "仅进度和事件数量；不含代码、聊天、凭据", action: "summary" },
      { label: "备份完整学习数据库", description: "可能包含历史代码；练习文件需另行复制", action: "backup" },
      { label: "清除学习记录…", description: "删除进度、事件及诊断依据；保留练习文件和模型配置", action: "clear" },
    ];
    const choice = await vscode.window.showQuickPick(items, { title: "SparkTutor：学习数据管理", placeHolder: "选择要执行的操作" });
    if (!choice) { return; }
    if (choice.action === "overview") { await showOverview(await callbacks.getOverview()); return; }

    if (choice.action === "summary") {
      const destination = await vscode.window.showSaveDialog({
        title: "导出学习摘要（请选择新文件）", saveLabel: "导出摘要", filters: { JSON: ["json"] },
      });
      if (!destination) { return; }
      const filename = localDestination(destination);
      const summary = await callbacks.exportSummary();
      // Exclusive writes cannot replace a database, code file or existing export.
      await fs.writeFile(filename, JSON.stringify(summary, null, 2) + "\n", { encoding: "utf8", flag: "wx" });
      await vscode.window.showInformationMessage(`学习摘要已导出到 ${filename}。`);
      return;
    }

    const overview = await callbacks.getOverview();
    const cancel: vscode.MessageItem = { title: "取消", isCloseAffordance: true };
    if (choice.action === "backup") {
      const confirm: vscode.MessageItem = { title: "选择备份位置" };
      const answer = await vscode.window.showWarningMessage("备份完整学习数据库？", {
        modal: true, detail: `${overview.privacy.backup}\n${overview.privacy.restore}\n仅在本机写出，不会上传到模型。`,
      }, cancel, confirm);
      if (answer !== confirm) { return; }
      const destination = await vscode.window.showSaveDialog({
        title: "备份学习数据库（请选择新文件）", saveLabel: "创建备份", filters: { "SQLite 数据库": ["db"] },
      });
      if (!destination) { return; }
      const result = await callbacks.backupDatabase(localDestination(destination));
      await vscode.window.showInformationMessage(`数据库已备份到 ${result.destination}。练习文件请另行复制。`);
      return;
    }

    const confirm: vscode.MessageItem = { title: "清除学习记录" };
    const answer = await vscode.window.showWarningMessage("清除全部课程的学习记录？", {
      modal: true,
      detail: `${overview.progressCount} 条课节进度，${overview.eventCount} 条学习事件将被删除。\n${overview.privacy.clear}\n清除后无法在扩展内撤销；需要保留时请先取消并备份。`,
    }, cancel, confirm);
    if (answer !== confirm) { return; }
    const result = await callbacks.clearRecords();
    if (!result.cleared) { throw new Error("后端未确认清除成功，当前学习状态已保留。"); }
    try { await callbacks.afterClear(); }
    catch {
      await vscode.window.showErrorMessage("学习记录已清除，但界面刷新失败。请重载窗口后继续学习。");
      return;
    }
    await vscode.window.showInformationMessage(`已清除 ${result.deletedProgress} 条进度和 ${result.deletedEvents} 条学习事件；练习文件和模型配置已保留。`);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    const reason = (error as NodeJS.ErrnoException)?.code === "EEXIST"
      ? "目标文件已存在，请选择新的文件名。" : message;
    await vscode.window.showErrorMessage(`学习数据操作未完成：${reason}`);
  }
}
