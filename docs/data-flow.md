# 数据流与数据落点

> 目标：回答"用户点一个按钮后，数据经过了哪些文件，最后落在哪里、哪些被丢弃"。
> 这是未来 Learning Events 埋点的精确坐标。

## 1. 三条独立存储

| 存储 | 位置 | 生命周期 | 内容 |
|---|---|---|---|
| SQLite progress | `settings.data_dir/progress.db`（默认 `~/.sparktutor/progress.db`） | 持久 | 每课一行进度状态 |
| LearnerProfile | `handler.py:72` `self._profile`（内存） | server 进程生命周期，重启清零 | 聚合计数 + skill_signals |
| 课程 YAML | `src/sparktutor/courses/*/` | 静态文件 | 课程/步骤定义 |

**重要**：LearnerProfile 与 SQLite 完全独立。目前没有任何代码把 profile 写入磁盘。

## 2. SQLite progress 表（progress.py:32-44，已核实）

```sql
CREATE TABLE progress (
    course_id   TEXT NOT NULL,
    lesson_id   TEXT NOT NULL,
    current_step INTEGER DEFAULT 0,
    total_steps  INTEGER DEFAULT 0,
    completed    INTEGER DEFAULT 0,
    depth        TEXT DEFAULT 'beginner',
    last_code    TEXT DEFAULT '',
    updated_at   TEXT NOT NULL,
    PRIMARY KEY (course_id, lesson_id)
)
```

- 写入时机：`lesson_runner._save_progress()`（submit / advance / go_back 后），`handler._parse_review_response()` 补写。
- 写入方式：`INSERT OR REPLACE`（整行覆盖），`updated_at = datetime.now().isoformat()`。
- **只存进度状态，没有任何行为历史**（无 attempts、无 hint、无 feedback、无代码版本历史）。

## 3. LearnerProfile（adaptive.py:26-47，已核实）

```python
@dataclass
class LearnerProfile:
    depth: Depth = Depth.BEGINNER
    total_attempts: int = 0
    correct_first_try: int = 0
    hint_usage: int = 0
    skill_signals: list[str] = []

    def record_attempt(self, passed, used_hint, signals=None):
        self.total_attempts += 1
        if passed: self.correct_first_try += 1
        if used_hint: self.hint_usage += 1
        if signals: self.skill_signals.extend(signals)
```

### record_attempt 全部调用点

| 调用点 | 场景 | passed | used_hint |
|---|---|---|---|
| lesson_runner.py:141 | submit()（Anthropic 全包路径） | 真实结果 | **硬编码 False** |
| lesson_runner.py:192 | submit_local() 本地够判 | 真实结果 | **硬编码 False** |
| handler.py:408 | _parse_review_response()（Copilot 路径） | 真实结果 | **硬编码 False** |
| lesson_runner.py:250 | get_hint() | **False** | **True** |

### 发现的问题（诊断模型要纠正的）

1. **`correct_first_try` 名不符实**：实际是"通过次数"，`accuracy = correct_first_try / total_attempts` 是**通过率**而非"首次尝试正确率"。字段命名与语义不符。
2. **used_hint 三处硬编码 False**：只有 get_hint 记录 hint 使用；若"先看 hint 再提交"发生在同一 step，提交侧的 record_attempt 不会联动。hint_usage 计数本身正确（get_hint +1），但**无法关联到具体某次提交**。
3. **skill_signals 只增不减**：无去重、无时间戳、无来源区分（本地/AI）。
4. **无课程/步骤维度**：profile 是全局的，无法回答"这个学生在哪类步骤最薄弱"。

## 4. Submit 动作的数据流（完整）

```
用户点击 Submit
  ↓ code（编辑器全文）                        workspace.getCurrentCode()（commands.ts:502）
  ↓ EvalResult（passed/feedback/encouragement/skillSignals）
  │   ├→ lessonPanel.showFeedback()          Webview 展示
  │   ├→ diagnostics.setFeedback()           编辑器下划线（仅代码步骤）
  │   ├→ outputChannel                       日志（PASSED / 各条 feedback）
  │   └→ handler 侧：
  │       ├→ runner.state.attempts += 1      内存
  │       ├→ runner.state.last_result        内存（Chat 上下文用）
  │       ├→ runner.state.last_exec          内存（Chat 上下文用）
  │       ├→ profile.record_attempt(...)     内存（不落盘）
  │       └→ progress.save(...)              SQLite（每课一行，整行覆盖）
  └ 丢弃的数据
      ├→ feedback 历史（只保留最近一次 last_result）
      ├→ 代码版本历史（last_code 只存最新）
      ├→ 每次 submit 的时间戳（updated_at 只有最后一次）
      └→ hint 与提交的关联
```

## 5. Chat 发送给 AI 的上下文（handler.py:259-301，已核实）

```
- lesson_title         当前课程标题
- step_context         当前 step 的 output（题目说明）
- code_context         当前编辑器代码
- last_exec.stdout     最近一次执行 stdout（截 2000 字符）
- last_exec.stderr     最近一次执行 stderr（截 2000 字符）
- last_result.feedback 最近一次反馈前 10 条（[category] message）
- depth                学习深度
未发送：LearnerProfile（attempts/hint_usage/skill_signals）、历史反馈、课程进度
```

## 6. 未来 Learning Events 的候选埋点（草案）

| 事件 | 触发点 | 现有数据缺口 |
|---|---|---|
| lesson_loaded | handler._load_lesson | 无时间戳 |
| step_advanced | lesson_runner.advance | 无停留时长 |
| code_run | handler._run | 有 stdout/stderr，无耗时 |
| code_submitted | lesson_runner.submit/submit_local + _parse_review_response | 缺"第几次尝试"的语义、缺 hint 关联 |
| hint_requested | lesson_runner.get_hint | 有 used_hint，缺与提交的关联 |
| chat_asked | handler._chat | 缺问题分类、缺答案是否被采纳 |
