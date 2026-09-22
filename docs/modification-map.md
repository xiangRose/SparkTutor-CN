# 修改地图（原 SparkTutor → SparkTutor-CN）

> 目标：把"我们改哪里"精确到文件与函数。先完成 Before（Issue #1），再逐项决策。

## 1. 创新点与现状的对应关系

| 我们宣称的贡献 | 上游现状 | 严谨表述（改后） |
|---|---|---|
| Learning Events | progress 表只有进度状态，无行为历史 | 在 progress 之上新增事件日志，记录提交/hint/chat/运行等行为 |
| Enhanced Learner Model | 已有 LearnerProfile（4 个字段，内存态，不落盘） | 扩展现有 LearnerProfile：修正语义、持久化、增加课程/步骤维度 |
| Diagnosis Engine | 无 | 新增：从事件流聚合诊断信号 |
| Diagnostic Dashboard | 无 | 新增：Webview / 树视图呈现 |
| Targeted Practice | 无 | 新增：基于诊断推荐步骤/练习 |

**结论**：不能说"原项目没有学习模型"，应说"从原有的简单 performance signals 扩展为可追踪、可解释、多维度的学习诊断机制"。

## 2. 改动点清单（候选，按区域）

### 2.1 国产模型 Provider（上游动刀点，见 ai-call-flow.md）

- `aiRouter.ts`：`resolveProvider()` 加新 Provider 类型；`submitCode()/chat()` 加分支（推荐仿 Copilot 三段式，或直接 Python 全包）。
- `handler.py`：`dispatch()` 方法表加 method；或复用 `buildReviewPrompt/parseReviewResponse` 协议（零新增 RPC）。

### 2.2 Learning Events（新增存储层）

- 新文件（候选）：`src/sparktutor/state/events.py` — SQLite 事件表。
- 埋点位置（已在 data-flow.md §6 列出）：
  - `handler.py:_load_lesson` → lesson_loaded
  - `handler.py:_run` → code_run
  - `lesson_runner.py:submit/submit_local`、`handler.py:_parse_review_response` → code_submitted（补齐 attempts 语义、hint 关联）
  - `lesson_runner.py:get_hint` → hint_requested
  - `handler.py:_chat` → chat_asked
- 迁移路径：沿用 `progress.db` 同一文件加表，或新库。待定。

### 2.3 Enhanced Learner Profile（改 adaptive.py）

- 修正 `correct_first_try` 语义（→ 通过率 / 真首次尝试正确率）。
- `record_attempt` 增加时间戳、course_id/lesson_id/step_id 维度。
- 持久化（随事件写库，而非内存）。
- skill_signals 去重 + 来源标注。

### 2.4 诊断 Dashboard

- 新 Webview 或复用 courseTree 节点。数据来源：events 聚合。
- 待 issue #1 全部链路完成后设计。

## 3. 风险与决策点（需要讨论）

1. 事件表 schema：宽表（每事件一行 JSON）vs 规范表（多表）。
2. 埋点是否要覆盖"打开课程/浏览步骤"等被动行为（隐私与噪音权衡）。
3. 国产模型接入走 A/B/C 哪个方案（ai-call-flow.md §4）。
4. Dashboard 放扩展侧（TS Webview）还是 server 侧（Textual TUI，上游已有 app/）。

## 4. 变更清单（随决策更新）

- [ ] （待决）Provider 方案 A/B/C
- [ ] （待决）events 表 schema
- [ ] （待决）profile 持久化方式
- [ ] （待决）dashboard 载体
