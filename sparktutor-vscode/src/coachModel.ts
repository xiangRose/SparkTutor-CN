import * as vscode from "vscode";
import { OPENAI_SECRET_KEY } from "./openaiProvider";

/** Invoked only by the user; credentials never enter a workspace file or webview. */
export async function configureCoachModel(secrets: vscode.SecretStorage): Promise<void> {
  const choice = await vscode.window.showQuickPick([
    { label: "填写 API 地址和模型名称", id: "model", description: "保存到个人 VS Code 设置，沿用 OpenAI 兼容接口" },
    { label: "安全保存 API 密钥", id: "key", description: "使用 VS Code SecretStorage，不写入项目文件" },
    { label: "打开相关设置", id: "settings", description: "检查地址、模型和兼容选项；不要在项目中保存密钥" },
  ], { title: "配置 AI 调试教练模型", placeHolder: "教练仅使用你明确填写的兼容接口，不自动回退到其他模型" });
  if (!choice) { return; }
  if (choice.id === "settings") {
    await vscode.commands.executeCommand("workbench.action.openSettings", "sparktutor.openai");
    return;
  }
  if (choice.id === "key") {
    const key = await vscode.window.showInputBox({ title: "安全保存教练 API 密钥", password: true,
      prompt: "仅保存到 VS Code SecretStorage，并供 SparkTutor 的 OpenAI 兼容接口使用；取消则保留原密钥。",
      ignoreFocusOut: true, validateInput: (value) => value.trim() ? undefined : "请输入非空密钥。" });
    if (key === undefined) { return; }
    await secrets.store(OPENAI_SECRET_KEY, key.trim());
    await vscode.window.showInformationMessage("API 密钥已安全保存。请确认 API 地址与模型名称也已填写，然后回到教练请求辅导。");
    return;
  }
  const address = await vscode.window.showInputBox({ title: "教练 API 基础地址", ignoreFocusOut: true,
    prompt: "填写模型服务的 OpenAI 兼容基础地址（不包含 /chat/completions）。保存到个人设置，不立即发出请求。",
    placeHolder: "https://你的模型服务/v1", validateInput: (value) => {
      try {
        const url = new URL(value.trim());
        return ["http:", "https:"].includes(url.protocol) && !url.username && !url.password && !url.search && !url.hash
          ? undefined : "请输入不含密钥、查询参数或账户信息的 HTTP(S) 基础地址。";
      } catch { return "请输入有效的 HTTP(S) 基础地址。"; }
    } });
  if (address === undefined) { return; }
  const model = await vscode.window.showInputBox({ title: "教练模型名称", ignoreFocusOut: true,
    prompt: "填写服务商支持的准确模型 ID。此项与其他 OpenAI 兼容功能共用。",
    validateInput: (value) => value.trim() ? undefined : "请输入模型名称。" });
  if (model === undefined) { return; }
  const config = vscode.workspace.getConfiguration("sparktutor");
  await config.update("openaiBaseUrl", address.trim(), vscode.ConfigurationTarget.Global);
  await config.update("openaiModel", model.trim(), vscode.ConfigurationTarget.Global);
  await vscode.window.showInformationMessage("API 地址和模型名称已保存到个人设置。可再次打开配置入口安全保存密钥；未发出模型请求。");
}
