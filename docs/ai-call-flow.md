# AI 调用流（Submit / Chat 的 Provider 分叉）

> 已核实。这是未来接入国产模型 Provider 的精确动刀位置。

## 1. Provider 解析（aiRouter.ts:39-70）

```
resolveProvider():
  setting = sparktutor.aiProvider 配置（auto 默认）
  ├─ "anthropic" → 直接返回 anthropic
  ├─ "copilot"   → copilot.isAvailable() ? copilot : none
  └─ "auto"      → 有 ANTHROPIC_API_KEY（配置或环境变量）→ anthropic
                   → 否则 Copilot 可用 → copilot
                   → 否则 none
```

## 2. Submit 三分叉（aiRouter.ts:80-165）

```
submitCode({code})
  provider = resolveProvider()
  │
  ├─ anthropic ──────────────────────────────────────────────────┐
  │   bridge.call("submit", {code})                              │  Python 全包：
  │   ↑                                                          │  本地检查 + Claude 评审
  │   handler._submit → runner.submit → evaluator.evaluate       │  都在 Python 侧完成
  │   └── evaluator.evaluate() 内部可能调 claude_review()        │
  │                                                              │
  ├─ copilot ────────────────────────────────────────────────────┤
  │   bridge.call("buildReviewPrompt", {code})  ──┐              │  三段式往返：
  │   handler._build_review_prompt                │              │  Python 造 prompt，
  │     └─ runner.submit_local(code)              │              │  TS 直连 Copilot，
  │        → (result, needs_ai, review_kwargs)    │              │  Python 解析结果
  │   │                                            │              │
  │   ├─ !needsAiReview && localResult             │              │  （Copilot 只能从
  │   │   → 直接返回 localResult（本地已够判）      │              │   VS Code 内访问）
  │   │                                            │              │
  │   └─ needsAiReview ────────────────────────────┘              │
  │       messages = evaluator.build_review_prompt(**kwargs)      │
  │       rawText = copilot.sendRequest(messages)                 │
  │       parsed = bridge.call("parseReviewResponse", {rawText})  │
  │       handler._parse_review_response                          │
  │         └─ evaluator.parse_review_response(raw)               │
  │            + 合并 stderr 反馈                                  │
  │            + record_attempt(passed, used_hint=False, ...)     │
  │            + _save_progress("")                               │
  │                                                              │
  └─ none ────────────────────────────────────────────────────────┘
      bridge.call("buildReviewPrompt", {code})
      → 只返回 localResult（本地检查）；需 AI 但无 Provider 时给 info 提示
```

## 3. Chat 二分叉（aiRouter.ts:170-203）

```
chat({question, code?})
  ├─ anthropic → bridge.call("chat", params)
  │              handler._chat → evaluator.chat()（Claude 直答）
  ├─ copilot   → bridge.call("buildChatPrompt")
  │              handler._build_chat_prompt → evaluator.build_chat_messages()
  │              → copilot.sendRequest(messages) → 直接返回文本
  └─ none      → 返回 "No AI provider available..."
```

## 4. 国产模型接入点（候选，待决策）

| 方案 | 改动位置 | 代价 | 说明 |
|---|---|---|---|
| A. 仿 Copilot 三段式 | aiRouter.ts 加分支 + handler 复用 buildReviewPrompt/parseReviewResponse | TS 侧加 Provider client | 复用现有 RPC，改动最小 |
| B. Python 全包（仿 anthropic） | handler 加 method（如 `submitCN`）+ evaluator 加国产 client | 只改 Python | 需要 Python 侧能访问模型 API |
| C. Provider 抽象层 | aiRouter 重构为 Provider 接口 + 注册表 | 重构较大 | 长期最优，适合多模型并存 |

> 注意：三种方案都不需要动 `evaluator.evaluate()` 的决策树——那是本地检查逻辑，与 AI Provider 无关。

## 5. 已知细节与坑

- `handler._submit` / `_build_review_prompt` 都要求 `_runner` 非空（必须先 loadLesson）。
- Copilot 失败时降级返回 localResult（aiRouter.ts:109-128）。
- `_parse_review_response` 硬编码 `used_hint=False`（handler.py:408-412）。
- `buildReviewPrompt` 的 prompt 已内建 JSON schema 与深度校准（evaluator.py:162-216），国产模型若不走 Anthropic 兼容接口，需注意响应格式解析（parse_review_response 已兼容 markdown code fence）。
