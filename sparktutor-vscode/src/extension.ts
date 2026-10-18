/**
 * SparkTutor VS Code extension entry point.
 */

import * as vscode from "vscode";
import { AiRouter } from "./aiRouter";
import { Bridge } from "./bridge";
import { clearSavedSession, getSavedSession, registerCommands } from "./commands";
import { CourseTreeProvider } from "./courseTree";
import { DiagnosticsManager } from "./diagnostics";
import { LessonPanel } from "./lessonPanel";
import { SparkOutputChannel } from "./outputChannel";
import { StatusBarManager } from "./statusBar";
import { WorkspaceManager } from "./workspaceManager";
import { createTrackerShutdown, LearningEventTracker } from "./learningEventTracker";

let bridge: Bridge;
let shutdownExtension: (() => Promise<void>) | undefined;
const LEARNING_HOME_OPENED = "sparktutor.learningHomeOpened";

export async function activate(
  context: vscode.ExtensionContext
): Promise<void> {
  const outputChannel = new SparkOutputChannel();

  // Start the Python JSON-lines server
  bridge = new Bridge(context.extensionPath);
  shutdownExtension = async () => { bridge.dispose(); };
  bridge.on("log", (text: string) => outputChannel.appendLine(`[server] ${text}`));

  try {
    await bridge.start();
    outputChannel.appendLine("SparkTutor 后端已启动");
  } catch (err) {
    const msg =
      err instanceof Error ? err.message : "启动后端时发生未知错误";
    vscode.window.showErrorMessage(`SparkTutor: ${msg}`);
    return;
  }

  // Initialize managers
  const diagnostics = new DiagnosticsManager();
  const workspace = new WorkspaceManager();
  const statusBar = new StatusBarManager();
  const lessonPanel = new LessonPanel(context.extensionUri);
  const tracker = new LearningEventTracker(bridge, (message) => outputChannel.appendLine(`[学习记录] ${message}`));
  const editSubscription = vscode.workspace.onDidChangeTextDocument((event) => tracker.observe(event));

  // Register course tree view
  const treeProvider = new CourseTreeProvider(bridge);
  vscode.window.registerTreeDataProvider("sparktutorCourses", treeProvider);

  // Create AI router (resolves provider after startup)
  const aiRouter = new AiRouter(bridge, context.secrets);

  // Register all commands
  registerCommands(
    context,
    bridge,
    treeProvider,
    lessonPanel,
    workspace,
    diagnostics,
    outputChannel,
    statusBar,
    aiRouter,
    tracker
  );

  // Stream output notifications to the output channel
  bridge.onNotification("output", (params) => {
    outputChannel.appendLine(params.line as string);
  });

  // Detect execution mode on startup
  bridge
    .call<{ mode: string }>("detectMode")
    .then((result) => {
      statusBar.setMode(result.mode);
    })
    .catch(() => {
      statusBar.setMode("unknown");
    });

  // Resolve AI provider on startup and show in status bar
  aiRouter
    .resolveProvider()
    .then((provider) => {
      statusBar.setAiProvider(provider);
    })
    .catch(() => {
      statusBar.setAiProvider("none");
    });

  // Startup connectivity probe (non-blocking): cache the outcome so a failed
  // provider is skipped for a short while instead of failing every submit.
  aiRouter.runStartupHealthCheck().catch(() => {});

  shutdownExtension = createTrackerShutdown(tracker, () => {
    editSubscription.dispose();
    bridge.dispose();
    diagnostics.dispose();
    outputChannel.dispose();
    statusBar.dispose();
    lessonPanel.dispose();
  });
  context.subscriptions.push({ dispose: () => { void shutdownExtension?.(); } });

  // Show the output channel so the user knows it exists
  outputChannel.show();
  outputChannel.appendLine("SparkTutor 扩展已启用");

  // Auto-focus the SparkTutor sidebar so courses are visible on startup
  vscode.commands.executeCommand("sparktutorCourses.focus").then(
    () => {},
    () => {} // Silently ignore if view isn't ready yet
  );

  // Show the home once; an explicitly resumed lesson takes priority so that
  // onboarding never covers the lesson the learner just chose to continue.
  const showLearningHomeOnce = async (): Promise<void> => {
    if (context.globalState.get<boolean>(LEARNING_HOME_OPENED)) {
      return;
    }
    try {
      await vscode.commands.executeCommand("sparktutor.openLearningDashboard");
      if (!context.globalState.get<boolean>(LEARNING_HOME_OPENED)) {
        await context.globalState.update(LEARNING_HOME_OPENED, true);
      }
    } catch {
      outputChannel.appendLine("学习首页暂未打开，可点击侧栏顶部的‘学习首页’重试。");
    }
  };

  // Check for a saved session and offer to resume.
  const saved = getSavedSession();
  if (saved) {
    vscode.window
      .showInformationMessage(
        `继续「${saved.lessonTitle}」（${saved.depth}）？`,
        "继续",
        "重新开始"
      )
      .then(async (resume) => {
        if (resume === "继续") {
          vscode.commands.executeCommand(
            "sparktutor.openLesson",
            saved.courseId,
            saved.lessonIdx,
            saved.depth,
            true // skipResumePrompt — user already confirmed
          );
        } else if (resume === "重新开始") {
          clearSavedSession();
          await showLearningHomeOnce();
        } else {
          await showLearningHomeOnce();
        }
      });
  } else {
    await showLearningHomeOnce();
  }
}

export function deactivate(): Promise<void> {
  return shutdownExtension?.() || Promise.resolve();
}
