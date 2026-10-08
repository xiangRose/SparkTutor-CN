/**
 * Status bar items: execution mode, current step, depth level.
 */

import * as vscode from "vscode";

export class StatusBarManager {
  private modeItem: vscode.StatusBarItem;
  private stepItem: vscode.StatusBarItem;
  private depthItem: vscode.StatusBarItem;
  private aiProviderItem: vscode.StatusBarItem;

  constructor() {
    this.modeItem = vscode.window.createStatusBarItem(
      vscode.StatusBarAlignment.Left,
      100
    );
    this.modeItem.name = "SparkTutor 执行模式";
    this.modeItem.command = "sparktutor.changeMode";
    this.modeItem.tooltip = "点击切换执行模式";

    this.stepItem = vscode.window.createStatusBarItem(
      vscode.StatusBarAlignment.Left,
      99
    );
    this.stepItem.name = "SparkTutor 学习步骤";

    this.depthItem = vscode.window.createStatusBarItem(
      vscode.StatusBarAlignment.Left,
      98
    );
    this.depthItem.name = "SparkTutor 难度";
    this.depthItem.command = "sparktutor.changeDepth";
    this.depthItem.tooltip = "点击切换难度级别";

    this.aiProviderItem = vscode.window.createStatusBarItem(
      vscode.StatusBarAlignment.Left,
      97
    );
    this.aiProviderItem.name = "SparkTutor AI 提供方";
    this.aiProviderItem.tooltip = "用于代码评审和对话的 AI 提供方";
  }

  setMode(mode: string): void {
    const icons: Record<string, string> = {
      lakehouse: "$(flame)",
      local: "$(terminal)",
      dry_run: "$(beaker)",
      databricks: "$(cloud)",
      unknown: "$(question)",
    };
    const labels: Record<string, string> = {
      lakehouse: "湖仓",
      local: "本地 Spark",
      dry_run: "仅语法检查",
      databricks: "Databricks",
      unknown: "未知模式",
    };
    this.modeItem.text = `${icons[mode] || "$(question)"} ${labels[mode] || mode}`;
    this.modeItem.show();
  }

  setStep(currentIndex: number, totalSteps: number): void {
    this.stepItem.text = `第 ${currentIndex + 1}/${totalSteps} 步`;
    this.stepItem.show();
  }

  setDepth(depth: string): void {
    const labels: Record<string, string> = {
      beginner: "入门",
      intermediate: "中级",
      advanced: "高级",
    };
    this.depthItem.text = labels[depth] || depth;
    this.depthItem.show();
  }

  setAiProvider(provider: string): void {
    const labels: Record<string, string> = {
      anthropic: "$(sparkle) AI: Claude",
      copilot: "$(copilot) AI: Copilot",
      "openai-compatible": "$(cloud) AI：兼容接口",
      none: "$(circle-slash) AI：未配置",
    };
    this.aiProviderItem.text = labels[provider] || `AI: ${provider}`;
    this.aiProviderItem.show();
  }

  dispose(): void {
    this.modeItem.dispose();
    this.stepItem.dispose();
    this.depthItem.dispose();
    this.aiProviderItem.dispose();
  }
}
