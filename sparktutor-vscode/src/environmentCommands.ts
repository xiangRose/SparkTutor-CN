/** Register setup before starting Python so a broken interpreter remains repairable. */
import * as vscode from "vscode";
import { Bridge } from "./bridge";
import { configureCoachModel } from "./coachModel";
import { EnvironmentPanel } from "./environmentPanel";
import { EnvironmentReport } from "./environmentTypes";
import { OpenAICompatProvider } from "./openaiProvider";
import { extensionBuild } from "./buildInfo";

export function registerEnvironmentCheck(context: vscode.ExtensionContext, bridge: Bridge,
  showDiagnostics: () => void, backendReady: () => boolean = () => bridge.isAlive()): EnvironmentPanel {
  const panel = new EnvironmentPanel(context.extensionUri, async () => {
    const config = vscode.workspace.getConfiguration("sparktutor");
    const configuredMode = config.get<string>("executionMode") || "local";
    let report: EnvironmentReport;
    if (backendReady() && bridge.isAlive()) {
      report = await bridge.call<EnvironmentReport>("getEnvironmentReport", {}, 15000);
    } else {
      report = { schemaVersion: 1, staticOnly: true, sparkVerified: false, checks: [], capabilities: [],
        backend: { available: false, pythonPath: config.get<string>("pythonPath") || "python3",
          summary: "Python 后端尚未启动。请设置已安装 SparkTutor 依赖的解释器，修改后重载窗口。" },
        execution: { configuredMode, localPrerequisites: false, summary: "后端启动后才能检查课程和 Spark 依赖。",
          nextSteps: ["先设置 Python 路径；若依赖未安装，在该解释器中安装项目依赖。", "重载窗口后刷新环境检查。"] } };
    }
    const provider = new OpenAICompatProvider(context.secrets);
    await provider.refreshSecret();
    const compatible = provider.isConfigured();
    const coach = provider.hasExplicitModelConfig() && compatible;
    const anthropic = Boolean(config.get<string>("anthropicApiKey") || process.env.ANTHROPIC_API_KEY);
    const selected = config.get<string>("aiProvider") || "auto";
    const copilot = Boolean(vscode.extensions.getExtension("GitHub.copilot-chat"));
    const configured = selected === "anthropic" ? anthropic : selected === "copilot" ? copilot
      : ["openai-compatible", "openai"].includes(selected) ? compatible : anthropic || compatible || copilot;
    report.ai = { provider: selected, configured,
      summary: `${configured ? "导师存在可用的配置线索，连接尚未验证。" : "导师尚未配置；仍可阅读、使用课程提示和进行本地判题。"} ` +
        `${coach ? "调试教练已明确配置兼容接口，回复能力尚未验证。" : "调试教练需明确填写兼容接口地址、模型和认证配置。"} ` +
        "本次仅检查配置，不调用模型；Copilot 是否已登录或授权仍需实际检查连接。" };
    report.checks.push({ id: "extension", title: "当前扩展与教材来源", status: "available",
      summary: `扩展版本 ${context.extension.packageJSON.version}`,
      details: [`当前扩展目录：${context.extensionPath}`, `当前加载构建（UTC）：${extensionBuild}`],
      nextSteps: ["开发版修改后重新构建并重启 F5 开发窗口；安装版需重新打包安装并重载窗口。"] });
    return report;
  }, async (id) => {
    const settings: Partial<Record<typeof id, string>> = {
      pythonSettings: "sparktutor.pythonPath", modeSettings: "sparktutor.executionMode", aiSettings: "sparktutor.aiProvider",
    };
    if (settings[id]) { await vscode.commands.executeCommand("workbench.action.openSettings", settings[id]); }
    else if (id === "configureCoach") { await configureCoachModel(context.secrets); }
    else if (id === "reload") { await vscode.commands.executeCommand("workbench.action.reloadWindow"); }
    else if (id === "diagnostics") { showDiagnostics(); }
    else if (id === "checkAi") {
      if (!backendReady() || !bridge.isAlive()) {
        await vscode.window.showInformationMessage("请先修复 Python 路径并重载窗口，再检查 AI 连接。");
      } else { await vscode.commands.executeCommand("sparktutor.checkAiConnection"); }
    }
  });
  context.subscriptions.push(panel, vscode.commands.registerCommand("sparktutor.checkEnvironment", () => panel.show()));
  return panel;
}
