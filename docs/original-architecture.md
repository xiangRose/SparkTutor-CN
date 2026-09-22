# SparkTutor 原项目架构（文件 → 函数 → 记录表）

> 方法：以「文件 → 函数 → 记录表」逐层阅读，不做泛读。
> 基准：本地 clone `D:\project\AI+KY\sparktutor`（upstream HEAD 39f16af），行号以本地代码为准。
> 状态：Submit 链已完整核实；Run / Hint / Chat / Next / Progress 仅标入口，未逐行核实。

## 0. 仓库总览

```
sparktutor/
├── sparktutor-vscode/src/        # VS Code 扩展（TypeScript）
│   ├── extension.ts              # 入口：启动 Bridge、注册命令、初始化 UI
│   ├── commands.ts               # 命令层：UI 回调 → 具体动作
│   ├── aiRouter.ts               # AI Provider 路由（anthropic / copilot / none）
│   ├── bridge.ts                 # JSON-lines RPC：spawn Python 子进程
│   ├── copilotProvider.ts        # Copilot 直连（仅 VS Code 内可用）
│   ├── lessonPanel.ts            # 课程 Webview 面板
│   ├── courseTree.ts             # 课程树视图
│   ├── diagnostics.ts            # 编辑器诊断（下划线）
│   ├── workspaceManager.ts       # 编辑器文件读写
│   └── types.ts                  # 共享类型
└── src/sparktutor/               # Python 引擎
    ├── server/__main__.py        # stdin 循环：读 JSON 行 → dispatch → 写 JSON 行
    ├── server/handler.py         # RPC 分发（方法表 dispatch）
    ├── server/protocol.py        # Notification / Response 序列化
    ├── engine/
    │   ├── lesson_runner.py      # 课程状态机：load → present → evaluate → advance
    │   ├── evaluator.py          # 双层评估：本地 AST 检查 + Claude 评审
    │   ├── executor.py           # 代码执行（本地 / Lakehouse / Databricks）
    │   ├── adaptive.py           # Depth + LearnerProfile（内存态，不落盘）
    │   ├── lesson_loader.py      # 解析 course.yaml / lesson.yaml → Lesson/Step
    │   ├── feedback.py           # stderr 解析
    │   ├── normalizer.py         # 答案/代码归一化匹配
    │   ├── scaffolding.py        # 脚手架代码生成
    │   └── spark_knowledge.py    # Chat 系统 prompt
    ├── courses/registry.py       # 课程注册表（从 YAML 目录加载）
    ├── state/progress.py         # SQLite 进度存储（progress.db）
    └── config/settings.py        # 配置：Claude / 执行模式 / Databricks / data_dir
```

## 1. 通信骨架（先记住这一张）

**不是 HTTP，是子进程 + stdin/stdout JSON-lines RPC。**

```
VS Code (bridge.ts)
    │  spawn("python", ["-m", "sparktutor.server"])
    │  请求: {"id":1, "method":"submit", "params":{...}} + "\n"  → stdin
    ▼
Python Server (__main__.py)
    │  后台线程读 stdin → asyncio 队列 → handler.dispatch(msg)
    │  响应: {"id":1, "result":{...}} 或 {"id":1, "error":"..."}  → stdout
    ▼
bridge.ts handleLine() 按 id 匹配 pending Map → Promise resolve/reject
```

- 日志全部走 stderr（`sparktutor-server: ready` / `dispatch {method}` / error），保证 stdout 只跑协议。
- Windows 兼容：stdin 用后台线程读 + `loop.call_soon_threadsafe` 喂 asyncio 队列（`__main__.py:50-63`）。

## 2. 逐文件记录表（Submit 链已核实）

### ① commands.ts — 命令层 / UI 接线

| 项 | 内容 |
|---|---|
| 负责什么 | 把 webview 按钮、命令面板动作接成具体动作；维护当前课程/课时/步骤的会话状态 |
| 入口函数 | `registerCommands()`（65）；核心 `submitCode()`（495）、`runCode()`（464）、`openLesson()`（347） |
| 被谁调用 | `extension.ts` 激活时调用；webview 按钮回调（`lessonPanel.onSubmit = () => executeCommand("sparktutor.submit")`，78） |
| 调用了谁 | Submit → `aiRouter.submitCode({ code })`；Run/Next/Back/Hint → `bridge.call(...)` |
| 输入数据 | `workspace.getCurrentCode()`（编辑器当前代码） |
| 输出数据 | `lessonPanel.showFeedback(result)`、`diagnostics.setFeedback(uri, feedback)`（仅代码步骤）、outputChannel 日志、toast 消息 |
| 修改点 | 可能。接新 provider 不需动此层；但如果要传额外上下文（如学习画像）进 submit，这里会变 |

### ② aiRouter.ts — AI Provider 路由（接国产模型的第一现场）

| 项 | 内容 |
|---|---|
| 负责什么 | 决定本次请求用哪个 AI：`anthropic` / `copilot` / `none` |
| 入口函数 | `submitCode()`（80）、`chat()`（170）；路由判定 `resolveProvider()`（39） |
| 被谁调用 | commands.ts（submit / chat 两条入口） |
| 调用了谁 | 见下方三条路径 |
| 输入数据 | `{ code }`（submit）；`{ question, code }`（chat） |
| 输出数据 | `EvalResult` / `{ answer }` |
| 修改点 | **是（核心）**。国产 provider 分支就插在 `resolveProvider()` 判定 + `submitCode()` 分叉处 |

**resolveProvider() 选择依据（39-70）：**

1. 配置 `sparktutor.aiProvider` = "anthropic" → anthropic
2. 配置 = "copilot" → Copilot 可用则 copilot，否则 none
3. auto：有 `anthropicApiKey` 配置 或 `ANTHROPIC_API_KEY` 环境变量 → anthropic
4. auto：Copilot 可用 → copilot
5. 都没有 → none

**submitCode() 三条路径（80-165）：**

| Provider | Prompt 谁生成 | AI 谁调用 | 结果谁解析 |
|---|---|---|---|
| anthropic | Python（evaluator 内） | Python（evaluator.claude_review） | Python |
| copilot | Python（`buildReviewPrompt` RPC） | TypeScript（`copilot.sendRequest`） | Python（`parseReviewResponse` RPC） |
| none | —（本地检查够则直接返回） | 无 | Python |

copilot 路径是一次三段往返：`bridge.call("buildReviewPrompt")` → 本地够则直接返回 localResult；不够则 `copilot.sendRequest(messages)` → `bridge.call("parseReviewResponse", {rawText})`。

> 记录（先不回答）：国产模型接入 = 在 aiRouter 增加 provider 分支 + 决定走「Python 全包」还是「TS 调 API、Python 解析」；会牵动 bridge、handler、evaluator 三处，而不是只改一处。

### ③ bridge.ts — RPC 通道

| 项 | 内容 |
|---|---|
| 负责什么 | 启动 Python 子进程；建立 stdin/stdout 通道；RPC 编号、收发 JSON、超时 |
| 入口函数 | `call<T>(method, params, timeoutMs)`（206）；启动 `start()` → `spawn()`（42） |
| 被谁调用 | aiRouter、commands 等所有 TS 侧 RPC 请求 |
| 调用了谁 | `python -m sparktutor.server`（104），stdio pipe |
| 输入数据 | `{ id, method, params }\n` 写入 stdin |
| 输出数据 | 按 `id` 匹配的 `result` / `error`，resolve/reject Promise |
| 修改点 | 通常否（通信层，与 AI 无关） |

关键点：
- 请求编号 `nextId`（17），`pending: Map<id, {resolve, reject, timer}>`（17-24）
- 超时默认 120s（209）
- `handleLine()`（167）：无 `id` 且含 `method` → 当作 notification 广播；有 `id` → 匹配 pending
- spawn 前把 VS Code 设置灌成环境变量：`ANTHROPIC_API_KEY`、`SPARKTUTOR_CLAUDE_MODEL`、`SPARKTUTOR_EXECUTION_MODE`、湖仓/Databricks 配置（71-102）

### ④ server/handler.py — RPC 分发

| 项 | 内容 |
|---|---|
| 负责什么 | 把 RPC method 分发给引擎组件，序列化结果 |
| 入口函数 | `dispatch(msg)`（75）：`handler_map` 方法表 |
| 被谁调用 | `__main__.py` 每读一行 JSON 调一次 |
| 调用了谁 | 见下方 Submit 三条 handler |
| 输入数据 | `{ id, method, params }` |
| 输出数据 | 各 handler 返回 dict（已是 JSON 友好） |
| 修改点 | 可能。新 provider 若走 Python 全包，`_submit` 不需要改；若走 TS 调 API，`_build_review_prompt` / `_parse_review_response` 是入口 |

**Submit 相关的三个 handler：**

- `_submit(params)`（205）：校验 `self._runner` 非空 → `await self._runner.submit(code)` → 返回 `{passed, feedback[], encouragement, skillSignals}`
- `_build_review_prompt(params)`（324）：`self._runner.submit_local(code)` → `(result, needs_ai, review_kwargs)`；本地够判 → `localResult`；需 AI → `evaluator.build_review_prompt(**review_kwargs)` 生成 messages
- `_parse_review_response(params)`（391）：`evaluator.parse_review_response(raw_text)` → 合并 stderr → `record_attempt` + `_save_progress`（补上 submit_local 未记的账）

> 注意：`self._profile` 是 ServerHandler 进程级单例（72），server 重启即清零；`self._runner` 加载课程前为 None（`_submit` 会报 "No lesson loaded"）。

### ⑤ engine/lesson_runner.py — 学习步骤状态机

| 项 | 内容 |
|---|---|
| 负责什么 | 一次学习步骤的运行：load → present → evaluate → advance |
| 入口函数 | `submit(user_input)`（104）；`submit_local(user_input)`（152） |
| 被谁调用 | handler._submit / _build_review_prompt |
| 调用了谁 | `executor.execute()`（如需执行）→ `evaluator.evaluate()/evaluate_local()` → `parse_stderr()` → `profile.record_attempt()` → `_save_progress()` |
| 输入数据 | `user_input`（代码/选择）；内部持有 step、depth、executor、evaluator、progress、profile |
| 输出数据 | `EvalResult`（submit）；`(result, needs_ai, review_kwargs)`（submit_local） |
| 修改点 | **是（学习流程）**。学习事件埋点、attempt 序列记录都在这里加 |

`submit()` 流程（104-150）：
1. `step_state = EVALUATING`，`attempts += 1`
2. 若 `requires_execution` 且 `cls in (script, cmd_question)` → `executor.execute(user_input)`，存 `last_exec`
3. `evaluator.evaluate(code, step, depth, exec_result, lesson_title)` → EvalResult
4. 合并 stderr 反馈
5. `last_result = result`，`step_state = FEEDBACK`
6. `profile.record_attempt(passed, used_hint=False, signals=skill_signals)`
7. `_save_progress(user_input)`

`submit_local()`（152-199）：同 submit，但用 `evaluator.evaluate_local()`；本地够判 → 同样 record_attempt + save；需要 AI → 不 record、不 save（留给 `_parse_review_response` 补）。

`advance()`（201）：`current_index += 1`，重置 attempts/last_result/last_exec，`_save_progress(current_code)`（注意：不校验当前步骤是否通过）。

### ⑥ engine/evaluator.py — 双层评估（本地 AST + Claude）

| 项 | 内容 |
|---|---|
| 负责什么 | 判断学生代码/选择是否正确；需要时调 Claude 评审 |
| 入口函数 | `evaluate()`（302，完整版）；`evaluate_local()`（388，AI 前停住） |
| 被谁调用 | lesson_runner.submit / submit_local |
| 调用了谁 | 本地：`check_syntax` / `check_mult_choice` / `check_code_exact` / `check_ast_contains`；AI：`build_review_prompt` → `claude_review` → Anthropic client |
| 输入数据 | `code, step, depth, exec_result, lesson_title` |
| 输出数据 | `EvalResult`（passed / feedback[] / encouragement / skill_signals） |
| 修改点 | **是（AI Provider 抽象层）**。Python 直连 Anthropic 的代码集中在这里 |

**本地检查四件套：**

| 函数 | 判断什么 |
|---|---|
| `check_syntax`（47） | Python 语法（ast.parse） |
| `check_mult_choice`（63） | 选择题答案匹配（choices_match 归一化） |
| `check_code_exact`（75） | 是否与参考答案匹配（code_match 归一化） |
| `check_ast_contains`（81） | AST 是否含要求元素：class_def / method / function / args / import / call |

**evaluate() 决策树（302-386）：**

```
evaluate()
├─ mult_question 且有 correct_answer → check_mult_choice（直接返回）
├─ check_syntax → 失败即返回
├─ 有 correct_answer → check_code_exact → 过即返回「Correct!」
├─ validation 有 ast_contains → check_ast_contains → 不过即返回
├─ 有 claude_review 校验 或 (script 且无 correct_answer) → claude_review()
├─ cmd_question 且做了 ast_checks → 本地判执行结果（exit_code==0 即过，跳过 AI）
├─ 有 correct_answer（走到这 = 没匹配上）→ claude_review() 兜底（带 solution_hint）
└─ 无任何校验规则 → 语法 OK 即通过
```

**什么时候走 AI（最关键信息）：**
1. step.validation 里有 `claude_review` 类型
2. script 步骤且没有 correct_answer（开放题，必须 AI 判）
3. 有 correct_answer 但本地 exact match 没匹配上（兜底评审，带 solution_hint）

**AI 三个函数：**

- `build_review_prompt(code, lesson_title, objective, depth, stdout, stderr, solution_hint)`（162）：AI 看到的 = 课程名 + 学生深度 + 目标 + 可选提示 + 学生代码 + 执行 stdout/stderr；要求输出 JSON（passed / feedback[] / encouragement / skill_signals）
- `parse_review_response(text)`（218）：剥 markdown fence → 正则提 `{...}` → json.loads → EvalResult（兼容 GPT-4o 风格输出）
- `claude_review(...)`（250）：`client.messages.create(model=settings.claude.get_model(), max_tokens=1024)` → `parse_review_response`；异常 → 不抛错，返回 `passed=False + "Claude review failed"` 反馈

**模型配置（settings.py:41-49）：** 默认 `claude-sonnet-4-6`；env `SPARKTUTOR_CLAUDE_MODEL` 可覆盖；API key 来自配置或 `ANTHROPIC_API_KEY`。

### ⑦ engine/adaptive.py — Depth + LearnerProfile（已存在的最小学习信号）

| 项 | 内容 |
|---|---|
| 负责什么 | 学习难度等级 + 学习者画像（MVP 框架） |
| 入口函数 | `record_attempt(passed, used_hint, signals)`（40） |
| 被谁调用 | lesson_runner.submit / submit_local、handler._parse_review_response、lesson_runner.get_hint |
| 调用了谁 | 无（纯数据类） |
| 输入数据 | passed / used_hint / skill_signals |
| 输出数据 | 更新 `LearnerProfile` 字段 |
| 修改点 | **是（学习画像核心）** |

**原项目已记录：**

| 字段 | 含义 | 谁写入 |
|---|---|---|
| `depth` | 难度等级（用户自报） | handler._load_lesson |
| `total_attempts` | 总提交次数 | record_attempt |
| `correct_first_try` | 通过次数（注意：**语义是"本次通过"**，不是严格的 first-try） | record_attempt |
| `hint_usage` | 使用 hint 次数 | get_hint / record_attempt(used_hint=True) |
| `skill_signals` | AI 评审返回的能力信号列表（append 累积） | record_attempt |
| `accuracy`（property） | correct_first_try / total_attempts | 派生 |

**缺口（原项目没有，是你们创新点的证据）：**

- [ ] 无「代码修改次数」（同一 step 的 attempts 序列）
- [ ] 无「错误类型」记录（只记 passed，不记错在哪类：语法/结构/执行/AI 判定）
- [ ] 无「从错误到正确用了几次」
- [ ] 无「Hint 后是否成功」（只记 hint 次数，不关联结果）
- [ ] 无「Chat 使用」记录
- [ ] 无「迁移题表现」（同类题换场景的成绩对比）
- [ ] 无「自动推荐下一题」（depth 是用户自报，无动态调整）
- [ ] 不落盘（server 重启清零，与 progress.db 完全隔离）

### ⑧ state/progress.py — SQLite 进度持久化

| 项 | 内容 |
|---|---|
| 负责什么 | 学习进度持久化（**不是**学习分析） |
| 入口函数 | `save()`（68）、`get()`（49）、`get_course_progress()`（87）、`get_course_summary()`（102）、`reset_lesson()`（142）、`reset_course()`（149） |
| 被谁调用 | lesson_runner._save_progress；handler._get_course_progress / _reset_lesson |
| 调用了谁 | SQLite |
| 输入数据 | 进度字段（见下表） |
| 输出数据 | progress 行 / 课程汇总 dict |
| 修改点 | 可能（加 learning_events 表） |

**progress 表（8 字段）：**

| 字段 | 含义 |
|---|---|
| course_id / lesson_id | 主键 |
| current_step | 当前步索引 |
| total_steps | 总步数 |
| completed | 是否完成 |
| depth | 难度等级 |
| last_code | 上次代码（resume 用） |
| updated_at | 最后更新（ISO） |

**核心结论：progress ≠ learning analytics。** 它回答"我学到哪了"，不回答"我是怎么学的 / 哪里容易错 / 是否依赖 hint / 能否迁移 / 哪个维度薄弱"。

## 3. 架构图（Submit 链，已核实）

```
                 VS Code
                   │
            commands.ts  submitCode()
                   │  { code }
                   ▼
            aiRouter.ts  submitCode()
              ├─ anthropic ────────────────► bridge.call("submit")
              ├─ copilot ─► bridge.call("buildReviewPrompt")
              │                └─ Copilot ─► bridge.call("parseReviewResponse")
              └─ none ────► bridge.call("buildReviewPrompt") → localResult
                   │
                   ▼  JSON-lines RPC (stdin/stdout)
            handler.py  _submit / _build_review_prompt / _parse_review_response
                   │
                   ▼
            lesson_runner.py  submit() / submit_local()
                   │
                   ▼
            evaluator.py  evaluate() / evaluate_local()
              ├── Local: syntax / mult_choice / exact / ast_contains
              └── AI: build_review_prompt → claude_review → Claude
                   │
                   ▼
                EvalResult (passed / feedback / encouragement / skill_signals)
                   │
                   ▼
              VS Code UI (lessonPanel.showFeedback + diagnostics + output)
```

## 4. 数据落点全景（谁产生、谁消费、谁丢失）

```
用户提交
   │
   ▼
attempts / last_exec / last_result      ← RunnerState（内存，advance 时重置）
   │
   ├──► EvalResult.skill_signals ──► LearnerProfile.skill_signals（内存，append）
   ├──► record_attempt ──► total_attempts / correct_first_try / hint_usage（内存）
   └──► _save_progress ──► SQLite progress 表（8 字段，仅位置 + last_code + depth）
```

**丢失点：** 每次 advance/go_back 重置 attempts 与 last_exec/last_result；LearnerProfile 不落盘；skill_signals 只在内存累积，无时间维、无结果关联。

## 5. 你们扩展方向的锚点（先记录，不动手）

```
progress（当前学习状态，SQLite）
   │
   ▼
learning_events（学习过程数据，新表）  ← 需要新增
   │
   ▼
learner_model（学习者画像，从内存升级） ← 需要新增
   │
   ▼
diagnosis（诊断）                        ← 需要新增
   │
   ▼
recommendation（下一题推荐）             ← 需要新增
```

原项目已有的骨架：课程、深度等级、AI 反馈、AI Tutor、SQLite 进度、基础 LearnerProfile。真正能做的不是"加按钮"，而是把现有过程中「产生即丢失」的数据接成 Learning Event → Learner Model → Diagnosis → Recommendation 的闭环。
