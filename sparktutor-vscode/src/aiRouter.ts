/**
 * AI provider resolution and routing.
 *
 * Determines which AI backend to use (Anthropic, Copilot, OpenAI-compatible,
 * or none) and routes code review and chat requests accordingly.
 *
 * Connectivity strategy (no per-submit ping):
 *   - On startup, probe the resolved provider once and cache the outcome.
 *   - If a probe failed, the provider stays in a short cooldown window during
 *     which auto mode falls back to the next option (Copilot, local-only).
 *   - The user can re-run the probe via the "Check AI Connection" command;
 *     real request failures always surface a clear error message.
 */

import * as vscode from "vscode";
import { Bridge } from "./bridge";
import { CopilotProvider } from "./copilotProvider";
import { OpenAICompatProvider } from "./openaiProvider";
import { EvalResult } from "./types";
import { CoachMessage, CoachPrompt } from "./coachTypes";

export type AiProviderKind = "anthropic" | "copilot" | "openai-compatible" | "none";

/** Cooldown after a failed probe during which auto mode skips the provider. */
const HEALTH_COOLDOWN_MS = 60_000;

/** Only these backend-owned categories may be surfaced; never echo a model/provider error body. */
const COACH_RESPONSE_ERRORS: Record<string, string> = {
  COACH_RESPONSE_FORMAT: "模型未返回有效的单个 JSON 对象。",
  COACH_RESPONSE_FIELDS: "模型回复的字段不符合教练接口约定。",
  COACH_RESPONSE_HYPOTHESIS: "模型选择的假设不适用于当前练习或执行状态。",
  COACH_RESPONSE_REFERENCES: "模型没有正确引用本次提供的学习证据。",
  COACH_RESPONSE_PROBE: "模型提供的样例数据不符合当前练习的核查规则。",
  COACH_RESPONSE_TOO_LARGE: "模型回复超过 8 KB 限制。",
};

function coachResponseFailure(error: unknown): Error {
  const code = error instanceof Error ? error.message.match(/\[(COACH_RESPONSE_[A-Z_]+)\]$/)?.[1] : undefined;
  if (code && Object.hasOwn(COACH_RESPONSE_ERRORS, code)) {
    return new Error(`${COACH_RESPONSE_ERRORS[code]} [${code}] 本次未显示提示，也未计入提示记录。请点击同一层级重试；若反复出现，请检查模型是否支持按要求输出 JSON。`);
  }
  return new Error("模型回复未通过证据与格式校验，或练习已变化。本次未显示提示，请刷新后重试。");
}

interface BuildReviewResult {
  needsAiReview: boolean;
  reviewId?: string;
  localResult?: EvalResult;
  messages?: { role: string; content: string }[];
}

interface BuildChatResult {
  messages: { role: string; content: string }[];
}

interface HealthRecord {
  ok: boolean;
  checkedAt: number;
}

export class AiRouter {
  private bridge: Bridge;
  private copilot: CopilotProvider;
  private openai: OpenAICompatProvider;
  private cachedProvider: AiProviderKind | undefined;
  private healthCache: Map<string, HealthRecord> = new Map();

  constructor(bridge: Bridge, secrets?: vscode.SecretStorage) {
    this.bridge = bridge;
    this.copilot = new CopilotProvider();
    this.openai = new OpenAICompatProvider(secrets);
  }

  /**
   * Determine which AI provider to use based on settings,
   * API key presence, and Copilot availability.
   */
  async resolveProvider(): Promise<AiProviderKind> {
    await this.openai.refreshSecret();
    const config = vscode.workspace.getConfiguration("sparktutor");
    const setting = config.get<string>("aiProvider") || "auto";

    if (setting === "anthropic") {
      this.cachedProvider = "anthropic";
      return "anthropic";
    }

    if (setting === "copilot") {
      const available = await this.copilot.isAvailable();
      this.cachedProvider = available ? "copilot" : "none";
      return this.cachedProvider;
    }

    // Accept both the new "openai-compatible" name and the legacy "openai".
    if (setting === "openai-compatible" || setting === "openai") {
      // Explicit selection: still use it even if a previous probe failed —
      // the real request will surface a clear error.
      this.cachedProvider = this.openai.isConfigured()
        ? "openai-compatible"
        : "none";
      return this.cachedProvider;
    }

    // Auto mode: Anthropic if API key set, else OpenAI-compatible if configured
    // and not in failure cooldown, else Copilot if available, else none.
    const apiKey = config.get<string>("anthropicApiKey");
    const envKey = process.env.ANTHROPIC_API_KEY;
    if (apiKey || envKey) {
      this.cachedProvider = "anthropic";
      return "anthropic";
    }

    if (this.openai.isConfigured() && !this.inHealthCooldown("openai-compatible")) {
      this.cachedProvider = "openai-compatible";
      return "openai-compatible";
    }

    const copilotAvailable = await this.copilot.isAvailable();
    if (copilotAvailable) {
      this.cachedProvider = "copilot";
      return "copilot";
    }

    this.cachedProvider = "none";
    return "none";
  }

  /** Return the last resolved provider without re-checking availability. */
  getProvider(): AiProviderKind {
    return this.cachedProvider || "none";
  }

  /** Whether the provider failed a probe recently and auto mode should skip it. */
  private inHealthCooldown(provider: AiProviderKind): boolean {
    const rec = this.healthCache.get(provider);
    if (!rec || rec.ok) {
      return false;
    }
    return Date.now() - rec.checkedAt < HEALTH_COOLDOWN_MS;
  }

  /** Record the outcome of a connectivity probe for a provider. */
  private recordHealth(provider: AiProviderKind, ok: boolean): void {
    this.healthCache.set(provider, { ok, checkedAt: Date.now() });
  }

  /**
   * Startup probe: check the resolved provider once, cache the result,
   * and surface a warning if it is unreachable. Non-blocking.
   */
  async runStartupHealthCheck(): Promise<void> {
    const result = await this.checkConnection();
    if (!result.ok) {
      vscode.window.showWarningMessage(`SparkTutor: ${result.message}`);
    }
  }

  /**
   * Test connectivity to the active AI provider.
   * Returns { ok, message } — used by the "check AI connection" command
   * and the startup probe; the outcome is cached for the cooldown logic.
   */
  async checkConnection(): Promise<{ ok: boolean; message: string }> {
    const provider = await this.resolveProvider();

    if (provider === "openai-compatible") {
      const cfg = this.openai.getConfig();
      const started = Date.now();
      try {
        await this.openai.sendRequest(
          [{ role: "user", content: "ping" }],
          8
        );
        const message = `OpenAI 兼容接口连接成功（${cfg.baseUrl} · ${cfg.model}），用时 ${Date.now() - started} 毫秒`;
        this.recordHealth("openai-compatible", true);
        return { ok: true, message };
      } catch (err) {
        const message = `OpenAI 兼容接口检查失败：${
          err instanceof Error ? err.message : err
        }`;
        this.recordHealth("openai-compatible", false);
        return { ok: false, message };
      }
    }

    if (provider === "copilot") {
      try {
        const available = await this.copilot.isAvailable();
        if (!available) {
          this.recordHealth("copilot", false);
          return { ok: false, message: "Copilot 不可用。" };
        }
        await this.copilot.sendRequest(
          [{ role: "user", content: "ping" }],
          8
        );
        this.recordHealth("copilot", true);
        return { ok: true, message: "Copilot 连接成功。" };
      } catch (err) {
        const message = `Copilot 检查失败：${
          err instanceof Error ? err.message : err
        }`;
        this.recordHealth("copilot", false);
        return { ok: false, message };
      }
    }

    if (provider === "anthropic") {
      try {
        const result = await this.bridge.call<{ ok: boolean; message: string }>(
          "ping",
          {}
        );
        this.recordHealth("anthropic", result.ok);
        return result;
      } catch (err) {
        const message = `Anthropic 检查失败：${
          err instanceof Error ? err.message : err
        }`;
        this.recordHealth("anthropic", false);
        return { ok: false, message };
      }
    }

    return {
      ok: false,
      message:
        "未配置 AI 提供方，请先在 SparkTutor 设置中配置。",
    };
  }

  /**
   * Submit code for evaluation. Routes to the appropriate AI provider.
   */
  async submitCode(params: { code: string }): Promise<EvalResult> {
    const provider = await this.resolveProvider();

    if (provider === "anthropic") {
      // Existing flow: Python handles everything
      return this.bridge.call<EvalResult>("submit", params);
    }

    if (provider === "copilot") {
      return this.reviewViaExternal(
        params,
        (messages) => this.copilot.sendRequest(messages),
        "Copilot"
      );
    }

    if (provider === "openai-compatible") {
      return this.reviewViaExternal(
        params,
        (messages) => this.openai.sendRequest(messages),
        "AI"
      );
    }

    // No provider — run local checks only via the same buildReviewPrompt path.
    const buildResult = await this.bridge.call<BuildReviewResult>(
      "buildReviewPrompt",
      params
    );
    if (buildResult.localResult) {
      return buildResult.localResult;
    }

    return this.completeReviewFailure(buildResult.reviewId,
      "本题需要 AI 评审，但尚未配置 AI 提供方。" +
      "请设置 Anthropic API 密钥、配置 OpenAI 兼容接口，或安装 GitHub Copilot 后重新提交。");
  }

  /**
   * Handle a chat question. Routes to the appropriate AI provider.
   */
  async chat(params: {
    question: string;
    code?: string;
  }): Promise<{ answer: string }> {
    const provider = await this.resolveProvider();

    if (provider === "anthropic") {
      // Existing flow: Python handles everything
      return this.bridge.call<{ answer: string }>("chat", params);
    }

    if (provider === "copilot") {
      return this.chatViaExternal(
        params,
        (messages) => this.copilot.sendRequest(messages),
        "Copilot"
      );
    }

    if (provider === "openai-compatible") {
      return this.chatViaExternal(
        params,
        (messages) => this.openai.sendRequest(messages),
        "AI"
      );
    }

    return {
      answer:
        "未配置 AI 提供方。请设置 Anthropic API 密钥、配置 OpenAI 兼容接口，或安装 GitHub Copilot 以启用对话。",
    };
  }

  /** The coach explicitly uses the user's compatible model, never an automatic provider fallback. */
  async requestCoach(params: { code: string; level: number }, readCurrentCode: () => Promise<string>,
    current: () => boolean): Promise<CoachMessage> {
    let requestId: string | undefined;
    const assertCurrent = () => { if (!current()) { throw new Error("当前代码或练习已变化，请刷新教练后重新请求。"); } };
    try {
      assertCurrent();
      const built = await this.bridge.call<CoachPrompt>("buildCoachPrompt", params, 180000);
      requestId = built.requestId;
      assertCurrent();
      await this.openai.refreshSecret();
      if (!this.openai.hasExplicitModelConfig() || !this.openai.isConfigured()) {
        throw new Error("尚未配置教练模型。请点击「配置其他模型」，填写 API 地址和模型名称，并安全保存密钥。不会自动改用 Anthropic，也不会生成替代 AI 回复。");
      }
      let rawText: string;
      try {
        rawText = await this.openai.sendRequest(built.messages, 1024, 180000);
      } catch {
        throw new Error("教练模型请求未完成。请检查自定义 API 地址、模型、密钥和网络后重试；没有生成 AI 辅导结果。");
      }
      assertCurrent();
      const code = await readCurrentCode();
      assertCurrent();
      if (code !== params.code) { throw new Error("代码已变化，旧回复已撤下。请刷新教练后重新请求。"); }
      try {
        return await this.bridge.call<CoachMessage>("parseCoachResponse", { requestId, rawText, code }, 180000);
      } catch (error) {
        throw coachResponseFailure(error);
      }
    } catch (error) {
      if (requestId) {
        // The backend applies cleanup only to this request; stale cleanup cannot end a newer one.
        await this.bridge.call("completeCoachFailure", { requestId }, 180000).catch(() => {});
      }
      throw error;
    }
  }

  /**
   * Shared review flow for external providers (Copilot, OpenAI-compatible):
   * Python builds the prompt → TS calls the provider → Python parses the response.
   */
  private async reviewViaExternal(
    params: { code: string },
    send: (messages: { role: string; content: string }[]) => Promise<string>,
    providerName: string
  ): Promise<EvalResult> {
    const buildResult = await this.bridge.call<BuildReviewResult>(
      "buildReviewPrompt",
      params
    );

    // If local checks were sufficient, return immediately
    if (!buildResult.needsAiReview && buildResult.localResult) {
      return buildResult.localResult;
    }

    // AI review needed — call the external provider
    if (buildResult.messages) {
      let rawText: string;
      try {
        rawText = await send(buildResult.messages);
      } catch (err) {
        return this.completeReviewFailure(buildResult.reviewId,
          `${providerName} 评审失败：${err instanceof Error ? err.message : err}`);
      }
      try {
        return await this.bridge.call<EvalResult>(
          "parseReviewResponse",
          { rawText, reviewId: buildResult.reviewId }
        );
      } catch (err) {
        // Parsing can reject a stale submission. Do not finalize it again or
        // accidentally alter the newer pending review on the backend.
        return this.failedReview(`评审结果未应用：${err instanceof Error ? err.message : err}`);
      }
    }

    // No messages and no local result — shouldn't happen, but handle gracefully
    return buildResult.localResult || this.completeReviewFailure(buildResult.reviewId,
      "未收到有效的 AI 评审请求，请重新提交。");
  }

  /** Finish a failed external request so code and attempt statistics are persisted. */
  private async completeReviewFailure(reviewId: string | undefined, message: string): Promise<EvalResult> {
    if (!reviewId) { return this.failedReview(message); }
    try {
      return await this.bridge.call<EvalResult>("completeReviewFailure", { reviewId, message });
    } catch (err) {
      // An expired id must never complete the active step's newer submission.
      return this.failedReview(`${message} 失败记录未应用：${err instanceof Error ? err.message : err}`);
    }
  }

  private failedReview(message: string): EvalResult {
    return {
      passed: false,
      feedback: [{ line: null, severity: "warning", message, suggestion: null, category: null }],
      encouragement: "",
      skillSignals: [],
    };
  }

  /**
   * Shared chat flow for external providers (Copilot, OpenAI-compatible):
   * Python builds the messages → TS calls the provider → return the text.
   */
  private async chatViaExternal(
    params: { question: string; code?: string },
    send: (messages: { role: string; content: string }[]) => Promise<string>,
    providerName: string
  ): Promise<{ answer: string }> {
    const buildResult = await this.bridge.call<BuildChatResult>(
      "buildChatPrompt",
      params
    );

    try {
      const answer = await send(buildResult.messages);
      return { answer };
    } catch (err) {
      return {
        answer: `${providerName} 对话失败：${
          err instanceof Error ? err.message : err
        }`,
      };
    }
  }
}
