/**
 * OpenAI-compatible chat completions provider.
 *
 * Talks to any endpoint implementing the OpenAI /chat/completions schema,
 * which covers most domestic (Chinese) model APIs and local servers:
 *   - DeepSeek          https://api.deepseek.com/v1
 *   - Zhipu GLM         https://open.bigmodel.cn/api/paas/v4
 *   - Alibaba Qwen      https://dashscope.aliyuncs.com/compatible-mode/v1
 *   - Moonshot Kimi     https://api.moonshot.cn/v1
 *   - Volcengine Doubao https://ark.cn-beijing.volces.com/api/v3
 *   - SiliconFlow       https://api.siliconflow.cn/v1
 *   - Ollama (local)    http://localhost:11434/v1
 *
 * Robustness against non-uniform implementations:
 *   - URL probing: if `{baseUrl}/chat/completions` fails with 404/405/network
 *     error and the base URL has no /v1 suffix, retry `{baseUrl}/v1/chat/completions`.
 *   - max_tokens fallback: if the endpoint rejects `max_tokens`, retry without it.
 *   - Content extraction: handles string, multimodal array, and empty responses.
 *   - Extra headers: `sparktutor.openaiExtraHeaders` (JSON) is merged into the
 *     request, overriding the default `Authorization: Bearer <key>` when present
 *     (e.g. services that expect `x-api-key` or a custom auth scheme).
 */

import * as vscode from "vscode";

export const OPENAI_SECRET_KEY = "sparktutor.openaiApiKey";

export interface OpenAICompatOptions {
  baseUrl: string;
  apiKey: string;
  model: string;
  extraHeaders?: Record<string, string>;
}

export interface OpenAICompatRequestOptions {
  connectionProbe?: boolean;
}

export type OpenAICompatErrorKind = "transport" | "http" | "empty" | "incomplete" | "invalid-format";

/** Status and kind are safe routing metadata; upstream bodies never become messages. */
export class OpenAICompatError extends Error {
  status: number;
  kind: OpenAICompatErrorKind;
  constructor(message: string, status: number, kind?: OpenAICompatErrorKind) {
    super(message);
    this.name = "OpenAICompatError";
    this.status = status;
    this.kind = kind ?? (status === 0 ? "transport" : "http");
  }
}

export class OpenAICompatProvider {
  private savedKey = "";
  constructor(private readonly secrets?: vscode.SecretStorage) {}

  async refreshSecret(): Promise<void> {
    this.savedKey = this.secrets ? (await this.secrets.get(OPENAI_SECRET_KEY) || "") : "";
  }

  /** Defaults alone are not consent to send coach code to a model endpoint. */
  hasExplicitModelConfig(): boolean {
    const config = vscode.workspace.getConfiguration("sparktutor");
    return Boolean((this.explicitSetting(config, "openaiBaseUrl") || process.env.SPARKTUTOR_OPENAI_BASE_URL) &&
      (this.explicitSetting(config, "openaiModel") || process.env.SPARKTUTOR_OPENAI_MODEL));
  }

  private explicitSetting(config: vscode.WorkspaceConfiguration, key: string): string | undefined {
    const inspected = config.inspect<string>(key);
    return inspected?.workspaceFolderLanguageValue ?? inspected?.workspaceFolderValue ??
      inspected?.workspaceLanguageValue ?? inspected?.workspaceValue ?? inspected?.globalLanguageValue ?? inspected?.globalValue;
  }

  /** Read base URL / API key / model from VS Code settings or env vars. */
  getConfig(): OpenAICompatOptions {
    const config = vscode.workspace.getConfiguration("sparktutor");
    const baseUrl =
      this.explicitSetting(config, "openaiBaseUrl") ||
      process.env.SPARKTUTOR_OPENAI_BASE_URL ||
      config.get<string>("openaiBaseUrl") ||
      "https://api.openai.com/v1";
    const apiKey =
      this.savedKey ||
      config.get<string>("openaiApiKey") ||
      process.env.SPARKTUTOR_OPENAI_API_KEY ||
      process.env.OPENAI_API_KEY ||
      "";
    const model =
      this.explicitSetting(config, "openaiModel") ||
      process.env.SPARKTUTOR_OPENAI_MODEL ||
      config.get<string>("openaiModel") ||
      "gpt-4o-mini";
    const extraHeaders =
      config.get<Record<string, string>>("openaiExtraHeaders") || {};
    return { baseUrl, apiKey, model, extraHeaders };
  }

  /** Whether the provider has enough config to make a request. */
  isConfigured(): boolean {
    const cfg = this.getConfig();
    const hasAuth =
      Boolean(cfg.apiKey) ||
      Object.keys(cfg.extraHeaders || {}).length > 0;
    return Boolean(cfg.baseUrl && cfg.model && hasAuth);
  }

  /**
   * Send a chat request to an OpenAI-compatible endpoint.
   * Messages keep their {role, content} shape — system role is natively supported.
   */
  async sendRequest(
    messages: { role: string; content: string }[],
    maxTokens = 1024,
    timeoutMs?: number,
    options?: OpenAICompatRequestOptions
  ): Promise<string> {
    await this.refreshSecret();
    const cfg = this.getConfig();
    if (!cfg.apiKey && Object.keys(cfg.extraHeaders || {}).length === 0) {
      throw new Error("OpenAI-compatible API key is not configured.");
    }

    const candidates = this.buildUrls(cfg.baseUrl);
    const signal = timeoutMs === undefined ? undefined : AbortSignal.timeout(timeoutMs);
    let lastErr: Error | null = null;

    for (const url of candidates) {
      try {
        return await this.postChat(url, cfg, messages, maxTokens, signal, options);
      } catch (err) {
        lastErr = err instanceof Error ? err : new Error(String(err));
        // Only try the next URL on endpoint-level failures (404/405/network).
        if (!this.isUrlLevelError(err)) {
          break;
        }
      }
    }

    throw lastErr || new Error("OpenAI-compatible request failed.");
  }

  /** Candidate endpoint URLs: base + /chat/completions, plus /v1 variant. */
  private buildUrls(baseUrl: string): string[] {
    const base = baseUrl.replace(/\/+$/, "");
    const urls = [`${base}/chat/completions`];
    if (!/\/v1$/i.test(base)) {
      urls.push(`${base}/v1/chat/completions`);
    }
    return urls;
  }

  private isUrlLevelError(err: unknown): boolean {
    if (err instanceof OpenAICompatError) {
      return err.status === 0 || err.status === 404 || err.status === 405;
    }
    return false;
  }

  private buildHeaders(cfg: OpenAICompatOptions): Record<string, string> {
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
    };
    if (cfg.apiKey) {
      headers.Authorization = `Bearer ${cfg.apiKey}`;
    }
    // User-supplied headers override defaults (x-api-key, custom auth, ...).
    for (const [key, value] of Object.entries(cfg.extraHeaders || {})) {
      headers[key] = String(value);
    }
    return headers;
  }

  private buildBody(
    cfg: OpenAICompatOptions,
    messages: { role: string; content: string }[],
    maxTokens?: number,
    options?: OpenAICompatRequestOptions
  ): Record<string, unknown> {
    const body: Record<string, unknown> = {
      model: cfg.model,
      messages,
      stream: false,
    };
    if (maxTokens !== undefined) {
      body.max_tokens = maxTokens;
    }
    // A connection probe needs a short final answer, not a reasoning trace.
    // This provider-specific parameter is restricted to documented official IDs.
    if (options?.connectionProbe && this.isOfficialDeepSeekThinkingModel(cfg)) {
      body.thinking = { type: "disabled" };
    }
    return body;
  }

  private isOfficialDeepSeekThinkingModel(cfg: OpenAICompatOptions): boolean {
    if (cfg.model !== "deepseek-flash" && cfg.model !== "deepseek-v4-pro") {
      return false;
    }
    try {
      return new URL(cfg.baseUrl).hostname === "api.deepseek.com";
    } catch {
      return false;
    }
  }

  private async postChat(
    url: string,
    cfg: OpenAICompatOptions,
    messages: { role: string; content: string }[],
    maxTokens: number,
    signal?: AbortSignal,
    options?: OpenAICompatRequestOptions
  ): Promise<string> {
    let resp: Response;
    try {
      resp = await fetch(url, {
        signal,
        method: "POST",
        headers: this.buildHeaders(cfg),
        body: JSON.stringify(this.buildBody(cfg, messages, maxTokens, options)),
      });
    } catch {
      throw new OpenAICompatError(
        "无法连接模型接口，请检查网络、接口地址或请求是否超时。",
        0
      );
    }

    if (!resp.ok) {
      const detail = await resp.text().catch(() => "");
      // Endpoint doesn't support max_tokens — retry once without it.
      if (resp.status === 400 && /max[_ -]?tokens/i.test(detail)) {
        return this.postChatWithoutMaxTokens(url, cfg, messages, signal, options);
      }
      throw new OpenAICompatError(
        `模型接口返回 HTTP ${resp.status}，请检查地址、模型、鉴权或服务商状态。`,
        resp.status
      );
    }

    const data = await this.readResponse(resp);
    return this.extractContent(data);
  }

  private async postChatWithoutMaxTokens(
    url: string,
    cfg: OpenAICompatOptions,
    messages: { role: string; content: string }[],
    signal?: AbortSignal,
    options?: OpenAICompatRequestOptions
  ): Promise<string> {
    let resp: Response;
    try {
      resp = await fetch(url, {
        signal,
        method: "POST",
        headers: this.buildHeaders(cfg),
        body: JSON.stringify(this.buildBody(cfg, messages, undefined, options)),
      });
    } catch {
      throw new OpenAICompatError(
        "无法连接模型接口，请检查网络、接口地址或请求是否超时。",
        0
      );
    }

    if (!resp.ok) {
      throw new OpenAICompatError(
        `模型接口返回 HTTP ${resp.status}，请检查地址、模型、鉴权或服务商状态。`,
        resp.status
      );
    }

    const data = await this.readResponse(resp);
    return this.extractContent(data);
  }

  private async readResponse(resp: Response): Promise<unknown> {
    try {
      return await resp.json();
    } catch {
      throw new OpenAICompatError("模型接口未返回有效 JSON，请确认使用兼容聊天补全的接口。", 200, "invalid-format");
    }
  }

  /**
   * Extract response text. Handles:
   *   - content: "string"
   *   - content: [{type:"text", text:"..."}, ...]  (multimodal)
   *   - missing / empty / malformed responses
   */
  private extractContent(data: unknown): string {
    if (!data || typeof data !== "object" || Array.isArray(data)) {
      throw this.invalidFormat();
    }
    const choices = (data as { choices?: unknown }).choices;
    if (!Array.isArray(choices) || !choices.length) {
      throw this.invalidFormat();
    }
    const choice = choices[0];
    if (!choice || typeof choice !== "object" || Array.isArray(choice)) {
      throw this.invalidFormat();
    }
    const message = (choice as { message?: unknown }).message;
    if (!message || typeof message !== "object" || Array.isArray(message)) {
      throw this.invalidFormat();
    }
    const content = (message as { content?: unknown }).content;

    if (typeof content === "string") {
      if (content.trim()) {
        return content;
      }
    }

    if (Array.isArray(content)) {
      const parts = content
        .map((part) => {
          if (
            part &&
            typeof part === "object" &&
            typeof (part as { text?: unknown }).text === "string"
          ) {
            return (part as { text: string }).text;
          }
          return "";
        })
        .join("");
      if (parts.trim()) {
        return parts;
      }
    }

    if (content !== undefined && content !== null && typeof content !== "string" && !Array.isArray(content)) {
      throw this.invalidFormat();
    }
    const reasoning = (message as { reasoning_content?: unknown }).reasoning_content;
    if ((choice as { finish_reason?: unknown }).finish_reason === "length" ||
        (typeof reasoning === "string" && Boolean(reasoning.trim()))) {
      // Presence alone diagnoses an unfinished answer. Never expose private reasoning.
      throw new OpenAICompatError(
        "模型接口已响应，但尚未生成回答正文；可能是生成预算不足或模型仍在推理。",
        200,
        "incomplete"
      );
    }
    throw new OpenAICompatError(
      "模型接口已响应，但没有返回回答正文。",
      200,
      "empty"
    );
  }

  private invalidFormat(): OpenAICompatError {
    return new OpenAICompatError("模型接口响应结构不符合兼容聊天补全格式。", 200, "invalid-format");
  }
}
