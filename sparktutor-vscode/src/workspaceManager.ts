/** Exercise workspaces: short answers share a lesson file; scripts stay independent. */
import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import * as vscode from "vscode";
import { exerciseContent, stableHash } from "./lessonHelpers";

export class WorkspaceManager {
  private currentFile: vscode.Uri | null = null;
  private currentCourseId: string | null = null;
  private selectedChoice: string | null = null;
  private currentStepCls = "";
  private readonly legacyNotices = new Set<string>();

  constructor(private readonly baseDir = path.join(os.homedir(), ".sparktutor", "workspace")) {}

  private courseDir(courseId: string): string {
    this.validateId(courseId);
    return path.join(this.baseDir, courseId);
  }

  private validateId(id: string): void {
    if (!/^[a-zA-Z0-9_-]+$/.test(id)) {
      throw new Error("课程或练习标识无效。");
    }
  }

  private contains(directory: string, file: string): boolean {
    const relative = path.relative(directory, file);
    return relative !== ".." && !relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative);
  }

  getSupplementaryDir(courseId: string, lessonId: string): string {
    this.validateId(lessonId);
    const directory = path.join(this.courseDir(courseId), lessonId);
    fs.mkdirSync(directory, { recursive: true });
    return directory;
  }

  async switchCourse(newCourseId: string): Promise<void> {
    if (this.currentCourseId === newCourseId) { return; }
    if (this.currentCourseId) {
      const oldDirectory = this.courseDir(this.currentCourseId);
      await this.saveDocuments(oldDirectory);
      if (vscode.workspace.getConfiguration("sparktutor").get<boolean>("autoCloseTabs", true)) {
        await this.closeTabs((file) => this.contains(oldDirectory, file));
      }
    }
    this.currentCourseId = newCourseId;
    this.currentFile = null;
  }

  private async closeTabs(matches: (file: string) => boolean): Promise<void> {
    const tabs: vscode.Tab[] = [];
    for (const group of vscode.window.tabGroups.all) {
      for (const tab of group.tabs) {
        const input = tab.input;
        if ((input instanceof vscode.TabInputText && matches(input.uri.fsPath)) ||
          (input instanceof vscode.TabInputTextDiff &&
            (matches(input.original.fsPath) || matches(input.modified.fsPath)))) {
          tabs.push(tab);
        }
      }
    }
    if (tabs.length && !await vscode.window.tabGroups.close(tabs)) {
      throw new Error("编辑器未关闭，已取消文件重置或课程切换。");
    }
  }

  private async saveDocuments(directory: string): Promise<void> {
    for (const document of vscode.workspace.textDocuments) {
      if (document.isDirty && this.contains(directory, document.uri.fsPath) && !await document.save()) {
        throw new Error("练习代码保存失败，请先保存编辑器中的修改。");
      }
    }
  }

  setStepType(cls: string): void {
    this.currentStepCls = cls;
    this.selectedChoice = null;
  }

  setSelectedChoice(choice: string): void {
    this.selectedChoice = choice;
  }

  /** Unverified legacy progress is preserved for the learner, never executed as a new answer. */
  backupLegacyCode(courseId: string, lessonId: string, code: string): void {
    if (!code.trim()) { return; }
    const directory = this.getSupplementaryDir(courseId, lessonId);
    const stem = `legacy_restored_${stableHash(code)}`;
    let filePath = path.join(directory, `${stem}.py`);
    let suffix = 1;
    // Preserve both inputs even in the unlikely event of a content hash collision.
    while (fs.existsSync(filePath) && fs.readFileSync(filePath, "utf-8") !== code) {
      filePath = path.join(directory, `${stem}_${suffix++}.py`);
    }
    if (!fs.existsSync(filePath)) { fs.writeFileSync(filePath, code, "utf-8"); }
    if (!this.legacyNotices.has(filePath)) {
      this.legacyNotices.add(filePath);
      vscode.window.showInformationMessage(`旧版进度中的代码已备份到 ${filePath}。请按当前题面完成独立练习。`);
    }
  }

  /** File names use an unfiltered step identity so changing depth preserves work. */
  async openExercise(
    courseId: string,
    lessonId: string,
    stepKey: string,
    starterCode: string,
    restoredCode?: string
  ): Promise<vscode.Uri> {
    this.validateId(stepKey);
    const directory = this.getSupplementaryDir(courseId, lessonId);
    const isolated = this.currentStepCls === "script";
    const filePath = path.join(directory, isolated ? `script_${stepKey}.py` : "exercise.py");
    const document = vscode.workspace.textDocuments.find((doc) => doc.uri.fsPath === filePath);
    const existing = document?.getText() ?? (fs.existsSync(filePath) ? fs.readFileSync(filePath, "utf-8") : "");
    // Older sessions stored an entire course in one buffer. Never import that
    // buffer into a standalone script with its own Spark lifecycle and tests.
    const legacyFile = path.join(this.courseDir(courseId), "exercise.py");
    const legacyContents = fs.existsSync(legacyFile) ? fs.readFileSync(legacyFile, "utf-8") : "";
    if (isolated && restoredCode && (/^# --- Step \d+ ---/m.test(restoredCode) || restoredCode === legacyContents)) {
      const backup = path.join(directory, `legacy_restored_${stepKey}.py`);
      if (!fs.existsSync(backup)) { fs.writeFileSync(backup, restoredCode, "utf-8"); }
      restoredCode = undefined;
      vscode.window.showInformationMessage(`旧版累积代码已保留在 ${backup}；本题使用独立练习文件。`);
    }
    const content = exerciseContent(existing, starterCode, restoredCode, stepKey, isolated);
    if (content !== existing && document) {
      const edit = new vscode.WorkspaceEdit();
      edit.replace(document.uri, new vscode.Range(document.positionAt(0), document.positionAt(existing.length)), content);
      if (!await vscode.workspace.applyEdit(edit)) {
        throw new Error("无法更新练习文件，请先保存或关闭该文件。");
      }
      await document.save();
    } else if (!fs.existsSync(filePath) || content !== existing) {
      fs.writeFileSync(filePath, content, "utf-8");
    }
    return this.showFile(filePath, courseId);
  }

  /** Text and quiz steps display lesson notes without injecting a script's test harness. */
  async openExerciseIfExists(courseId: string, lessonId: string, lessonTitle?: string): Promise<void> {
    const directory = this.getSupplementaryDir(courseId, lessonId);
    // Keep the current exercise visible while reading within the same lesson.
    if (this.currentFile && this.contains(directory, this.currentFile.fsPath)) { return; }
    const filePath = path.join(directory, "exercise.py");
    if (!fs.existsSync(filePath)) {
      fs.writeFileSync(filePath,
        `# ${lessonTitle || "SparkTutor 练习"}\n# 在此记录本课短题代码；综合练习会打开独立文件。\n`, "utf-8");
    }
    await this.showFile(filePath, courseId);
  }

  private async showFile(filePath: string, courseId: string): Promise<vscode.Uri> {
    const legacyFile = path.join(this.courseDir(courseId), "exercise.py");
    if (!this.legacyNotices.has(courseId) && fs.existsSync(legacyFile)) {
      this.legacyNotices.add(courseId);
      vscode.window.showInformationMessage(`旧版课程作业保留在 ${legacyFile}。现在按课保存短题，并为综合练习建立独立文件。`);
    }
    const uri = vscode.Uri.file(filePath);
    const document = await vscode.workspace.openTextDocument(uri);
    await vscode.window.showTextDocument(document, {
      viewColumn: vscode.ViewColumn.One, preserveFocus: false, preview: false,
    });
    this.currentFile = uri;
    this.currentCourseId = courseId;
    return uri;
  }

  getCurrentCode(): string {
    if (this.currentStepCls === "mult_question") { return this.selectedChoice || ""; }
    if (!this.currentFile || this.currentStepCls === "text") { return ""; }
    // Include unsaved buffers even when the tab is hidden behind another editor.
    const document = vscode.workspace.textDocuments.find((doc) => doc.uri.fsPath === this.currentFile?.fsPath);
    if (document) { return document.getText(); }
    try { return fs.readFileSync(this.currentFile.fsPath, "utf-8"); }
    catch { return ""; }
  }

  getCurrentUri(): vscode.Uri | null { return this.currentFile; }

  async saveCurrentExercise(): Promise<void> {
    const document = vscode.workspace.textDocuments.find((doc) => doc.uri.fsPath === this.currentFile?.fsPath);
    if (document?.isDirty && !await document.save()) {
      throw new Error("练习代码保存失败，请先保存当前文件再切换步骤。");
    }
  }

  /** Only this lesson's managed exercises are reset. Legacy course files stay untouched. */
  async deleteExerciseFile(courseId: string, lessonId: string): Promise<void> {
    const directory = this.getSupplementaryDir(courseId, lessonId);
    const files = fs.readdirSync(directory)
      .filter((name) => name === "exercise.py" || /^script_[a-zA-Z0-9_-]+\.py$/.test(name))
      .map((name) => path.join(directory, name));
    await this.saveDocuments(directory);
    await this.closeTabs((file) => files.includes(file));
    for (const file of files) {
      // No recursive deletion: supplementary data, solutions and other lessons survive.
      if (this.contains(directory, path.resolve(file)) && fs.lstatSync(file).isFile()) {
        fs.unlinkSync(file);
      }
    }
    if (this.currentFile && files.includes(this.currentFile.fsPath)) { this.currentFile = null; }
  }

  writeSolutionFile(courseId: string, lessonId: string, stepIdx: number, solutionCode: string): vscode.Uri {
    const filePath = path.join(this.getSupplementaryDir(courseId, lessonId), `step_${stepIdx}_solution.py`);
    fs.writeFileSync(filePath, solutionCode, "utf-8");
    return vscode.Uri.file(filePath);
  }
}
