# Submit 链路考古（用户点击 → VS Code 反馈）

> 方法：文件 → 函数 → 记录表。逐层沿代码走，不背结论。
> 基准：本地 clone `D:\project\AI+KY\sparktutor`，行号为本地实际行号。
> 状态：已完整核实（commands.ts → aiRouter.ts → bridge.ts → handler.py → lesson_runner.py → evaluator.py + __main__.py / settings.py 补全）。

## 0. 最终调用链（理解结果，供对照）

```
用户点击 Submit
   ↓
commands.ts:121  registerCommand("sparktutor.submit")
   ↓  commands.ts:495  submitCode()
   ↓  取 workspace.getCurrentCode() → { code }
   ↓  aiRouter.submitCode({ code })
   ↓
aiRouter.ts:80  submitCode()
   ├─ anthropic ──────────────► bridge.call("submit", { code })
   ├─ copilot ─► bridge.call("buildReviewPrompt") → Copilot.sendRequest() → bridge.call("parseReviewResponse")
   └─ none ────► bridge.call("buildReviewPrompt") → 本地检查结果
   ↓
bridge.ts:206  call(method, params) → {"id":N,"method":"submit","params":{...}}\n → stdin
   ↓
Python: server/__main__.py:30  stdin 线程 → asyncio 队列 → handler.dispatch()
   ↓
handler.py:75  dispatch → handler_map → _submit() / _build_review_prompt() / _parse_review_response()
   ↓
lesson_runner.py:104  submit(user_input)  （或 :152 submit_local）
   ↓  executor.execute()（脚本/cmd_question 时）
   ↓  evaluator.evaluate()（或 evaluate_local()）
   ↓
evaluator.py:302  evaluate()
   ├─ 本地：check_syntax / check_mult_choice / check_code_exact / check_ast_contains
   └─ AI：build_review_prompt() → claude_review() → Claude
   ↓
EvalResult（passed / feedback / encouragement / skill_signals）
   ↓   stdout 写回 {"id":N,"result":{...}}\n
bridge.ts:167  handleLine() → 按 id 匹配 pending → Promise resolve
   ↓
commands.ts:523  lessonPanel.showFeedback(result) + diagnostics.setFeedback() + output
```

## 1. 逐层记录表

| 层级 | 文件 | 函数 | 输入 | 调用谁 | 输出 | 我们是否需要修改 |
|---|---|---|---|---|---|---|
| VS Code | commands.ts | submit handler（495 submitCode） | 用户代码（workspace.getCurrentCode） | aiRouter.submitCode() | EvalResult | 可能（若 submit 要带画像上下文） |
| AI 路由 | aiRouter.ts | submitCode()（80） | code | bridge / Copilot | EvalResult | **是（国产模型入口）** |
| 通信 | bridge.ts | call()（206） | method + params | Python server（stdin） | JSON result | 通常否 |
| RPC | handler.py | _submit（205）/ _build_review_prompt（324）/ _parse_review_response（391） | JSON params | LessonRunner / Evaluator | result dict | 可能（Python 侧新 provider） |
| 学习流程 | lesson_runner.py | submit()（104）/ submit_local()（152） | code + step | Executor / Evaluator / profile / progress | EvalResult | **是（学习事件埋点）** |
| 评估 | evaluator.py | evaluate()（302）/ evaluate_local()（388） | code + step + depth + exec | 本地检查 / claude_review | EvalResult | **是（AI 抽象层）** |
| AI | evaluator.py | claude_review()（250） | prompt | Anthropic client | EvalResult | 是（换模型/加 provider） |
| 学习画像 | adaptive.py | record_attempt()（40） | passed / hint / signals | （无） | LearnerProfile 字段更新 | **是（画像扩展）** |
| 数据 | progress.py | save()（68） | 进度字段 | SQLite | progress 行 | 可能（learning_events 表） |

## 2. 三个 Provider 路径的 Prompt/AI/解析归属

| Provider | Prompt 谁生成 | AI 谁调用 | 结果谁解析 | 判定依据（resolveProvider） |
|---|---|---|---|---|
| anthropic | Python（evaluator.build_review_prompt，162） | Python（evaluator.claude_review，250） | Python（parse_review_response，218） | 配置=anthropic，或 auto 且有 API key |
| copilot | Python（handler._build_review_prompt，324） | TypeScript（copilotProvider.sendRequest） | Python（handler._parse_review_response，391） | 配置=copilot 且可用，或 auto 无 key 但有 Copilot |
| none | —（本地检查） | 无 | Python（evaluate_local 直接返回） | 配置=copilot 但不可用，或 auto 都无 |

**关键事实：AI Provider 不是完全抽象在 Python 里的。**
- Python 侧 `evaluator.claude_review()` 直接 `import anthropic`、直接建 client（evaluator.py:37-43），没有 Provider 抽象接口。
- TS 侧 `aiRouter.ts` 又有独立 Copilot 路径（三段往返）。
- 两套 AI 接入方式并存：**「Python 全包」（anthropic）** 与 **「Python 造 prompt + TS 调 API + Python 解析」（copilot）**。

## 3. evaluate() 决策树（什么时候走 AI —— 最关键信息）

```
evaluate()                                    evaluate_local()（AI 前停住）
│                                             │
├─ mult_question? → check_mult_choice         ├─ 同左（本地直接返回）
├─ check_syntax → 不过即返回                  ├─ 同左
├─ correct_answer? → check_code_exact         ├─ 同左；过即返回
│     过 → 返回 "Correct!"                    │
├─ validation ast_contains? → check_ast_contains  ├─ 同左；不过即返回
│     不过 → 返回                             │
├─ 有 claude_review 或 (script 且无 correct_answer)
│      → claude_review()  ◀── 走 AI 分支 1    ├─ 返回 (None, needs_ai=True, kwargs)
├─ cmd_question 且做了 ast_checks             ├─ 同左（本地判执行 exit_code，过即返回）
│      → 本地判执行结果，跳过 AI              │
├─ correct_answer 兜底（exact 没匹配上）      ├─ 返回 (None, needs_ai=True, kwargs)
│      → claude_review(solution_hint=答案)    │   （走 AI 分支 2）
└─ 无校验规则 → 语法 OK 即通过                └─ 同左
```

**走 AI 的三种情况：**
1. step.validation 含 `claude_review` 类型（显式要求 AI）
2. script 步骤且没有 correct_answer（开放题，只能 AI 判）
3. 有 correct_answer 但本地 exact match 未命中（兜底评审，附 solution_hint=答案）

**本地即可判、不走 AI 的情况：** 选择题、语法错误、exact 命中、AST 不满足、cmd_question 执行成功且 AST 满足、无校验规则。

## 4. EvalResult 数据流

```
code
   ↓
LessonRunner.submit()
   ↓
Evaluator.evaluate()
   ↓
EvalResult
   ├── passed          → 是否通过
   ├── feedback[]      → line / severity / message / suggestion / category
   ├── encouragement   → 按深度校准的鼓励语
   └── skill_signals   → AI 评审观察到的能力/短板（字符串列表）
          │
          ├──► adaptive.LearnerProfile.record_attempt(passed, used_hint=False, signals)
          │       ├── total_attempts += 1
          │       ├── correct_first_try += 1（if passed）
          │       ├── skill_signals.extend(signals)
          │       └── accuracy = correct_first_try / total_attempts（派生）
          │
          └──► progress.save() → SQLite progress 表
                  （course_id, lesson_id, current_step, total_steps, completed, depth, last_code, updated_at）
```

**两条记账路径（重要）：**
- anthropic / 本地路径：`lesson_runner.submit()` 里一次性 record_attempt + _save_progress。
- copilot 路径：`submit_local()` 先跑本地 → 本地够判则同样记账；需要 AI 则**先不记** → `_parse_review_response()` 拿到 AI 结果后**补记**（handler.py:408-414）。

## 5. 关键文件 · 函数速查

| 文件 | 函数 | 行号 | 一句话 |
|---|---|---|---|
| commands.ts | submitCode | 495 | 接住提交动作，取代码，调 aiRouter，显示结果 |
| aiRouter.ts | resolveProvider | 39 | 按配置/API key/Copilot 判定用哪个 AI |
| aiRouter.ts | submitCode | 80 | 三路分叉：anthropic 全包 / copilot 三段 / none 本地 |
| bridge.ts | spawn | 42 | 启动 `python -m sparktutor.server`，环境变量注入 |
| bridge.ts | call | 206 | 编号 + JSON 写 stdin，按 id 等 stdout 响应 |
| bridge.ts | handleLine | 167 | 解析 stdout 行，匹配 pending |
| handler.py | dispatch | 75 | RPC 方法表分发 |
| handler.py | _submit | 205 | submit RPC → lesson_runner.submit |
| handler.py | _build_review_prompt | 324 | copilot/none 路径：本地先判，需 AI 则造 prompt |
| handler.py | _parse_review_response | 391 | 解析 AI 文本 → EvalResult + 补记账 |
| lesson_runner.py | submit | 104 | 完整评估流程（执行→评估→记账→存进度） |
| lesson_runner.py | submit_local | 152 | 本地评估，AI 前停住 |
| evaluator.py | evaluate | 302 | 决策树总入口 |
| evaluator.py | evaluate_local | 388 | 决策树，AI 前停住，返回 needs_ai |
| evaluator.py | build_review_prompt | 162 | 构造 AI 评审 prompt（JSON 输出规范） |
| evaluator.py | claude_review | 250 | 直连 Anthropic client.messages.create |
| evaluator.py | parse_review_response | 218 | AI 文本 → EvalResult |
| adaptive.py | record_attempt | 40 | 更新 LearnerProfile |
| progress.py | save | 68 | 写 SQLite progress 表 |

## 6. AI 评审的输入与输出（build_review_prompt / claude_review）

**AI 看到了什么（prompt 内容）：**
- lesson_title（课程/课时名）
- depth（学生自报难度：beginner / intermediate / advanced）
- objective（来自 claude_review validation 的 criteria，缺省用 step.output）
- solution_hint（仅 exact 未命中兜底时，附正确答案）
- student code
- execution stdout / stderr（如有执行）

**AI 输出（要求 JSON）：**
```
{ "passed": bool,
  "feedback": [{ "line", "severity", "category", "message", "suggestion" }],
  "encouragement": str,
  "skill_signals": [str] }
```

**异常处理：** client 未配置 → 返回 passed=False + "Claude API not configured"；API 调用异常 → 返回 passed=False + "Claude review failed: {e}"（**不抛错**，UI 不会红屏，但学生拿到 failed）。

## 7. 我们未来动刀的位置（先记录，不动手）

1. **国产模型 Provider 入口**：`aiRouter.ts` resolveProvider + submitCode 增加 provider 分支；决定走「Python 全包」还是「TS 调 API + Python 解析」。
2. **Python AI 抽象层**：evaluator.py 的 `_get_client()` / `claude_review()` 目前硬编码 anthropic，需抽象成统一接口（不要到处 `if qwen: ... elif claude: ...`）。
3. **学习事件埋点**：lesson_runner.submit() 是唯一完整的评估记账点；copilot 路径的账在 handler._parse_review_response 补记——**两条路径必须统一埋点**，否则事件流不完整。
4. **LearnerProfile 扩展**：adaptive.py 现有字段太少（无 attempts 序列、无错误类型、无 hint-结果关联、无落盘）。
5. **progress.db**：只有 8 字段进度表；learning_events 需要新表。

> 下一轮：Run / Hint / Chat / Next / Progress 五条链路逐行核实，补 run-flow.md / hint-flow.md / chat-flow.md / progress-flow.md / ai-provider.md。
