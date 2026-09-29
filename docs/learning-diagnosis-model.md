# SparkTutor 学习诊断模型

## 定位与边界

学习诊断用于把运行、提交、提示和迁移练习等行为轨迹转成可行动的形成性反馈。它不是成绩、心理测量或能力定论，也没有经过目标学习者群体的信效度标定。分数只应用于提示下一步练习，不应用于排名、录取或教师评价。

模型输出四个同向维度（越高代表当前行为证据越积极）：知识掌握度、调试自修复能力、提示使用自主性、知识迁移能力。证据不足时返回 `null`，不以“没有求助”推断高能力。

## 研究依据

1. **使用可观测轨迹，但限制推断范围。** Winne 的自我调节学习分析框架认为细粒度 trace data 可作为元认知监控与控制的可观测指标，同时强调测量工具、数据属性和推断限制。因此本模型展示证据数、置信度和原始指标，不把行为直接等同于稳定能力。[Winne, 2022, *Learning Analytics for Self-Regulated Learning*](https://doi.org/10.18608/hla22.008)
2. **练习、反馈与知识掌握。** National Academies 的综述指出，技能习得需要带反馈的练习，解释性反馈优于只标记对错；深层学习还要求理解原则及其适用条件。因此知识维度同时观察通过率、首次通过和独立完成，而不是只统计最终成功。[Education for Life and Work: Guide for Practitioners](https://nap.nationalacademies.org/resource/13398/dbasse_084153.pdf)
3. **调试使用序列而非单点结果。** 编程学习研究使用提交日志分析调试策略及其与成功调试的关系；本模型以“首次失败到后续成功”为任务级修复过程，并区分期间是否使用提示。[Liu & Paquette, LAK 2023](https://www.solaresearch.org/events/lak/lak23/table-of-contents/)
4. **帮助寻求不等于能力低。** 学习分析研究显示，帮助寻求行为与先验知识、渠道和情境相关，不能把求助次数简单解释为能力。因此模型分开报告 `hintDependencyRisk`（使用覆盖率）与 `hintProductivity`（求助后完成率），四维主分使用方向明确的“自主性”。[Cloude, Baker & Fouh, LAK 2023](https://www.solaresearch.org/events/lak/lak23/table-of-contents/)
5. **迁移必须由新情境任务直接取证。** *How People Learn* 指出理解而非机械记忆、主动选择策略和任务间相似性都会影响迁移。因此只有显式标注来源知识与新情境的题目才计入迁移维度。[National Research Council, 2000, Chapter 3](https://nap.nationalacademies.org/skim.php?chap=51-78&record_id=9853)
6. **分析必须闭环到行动。** 学习分析应包含目标、当前位置和下一步改善建议，而不仅是展示数据。因此每次诊断都把最弱且有证据的维度映射到可直接打开的课程练习。[SoLAR, Learning Analytics: 3 Challenges and Opportunities](https://www.solaresearch.org/2021/03/learning-analytics-3-challenges-and-opportunities/)

## 评分规则

### 知识掌握度

`50% × 总通过率 + 30% × 首次通过率 + 20% × 独立通过率`。

三项指标及权重是透明的工程启发式规则，不是上述研究给出的固定公式，也未经过参数拟合。权重让重复提交后的最终成功不会完全掩盖首次表现与独立完成情况。

### 调试自修复能力

每个任务从第一次运行/提交失败开启一个失败过程；中间允许多次失败。后续运行成功或提交通过时关闭过程。期间未请求提示或查看答案的过程计为独立修复。

`独立修复过程数 ÷ 全部失败过程数`。

### 提示使用自主性

`100 - 使用过提示的任务数 ÷ 已尝试任务数 × 100`。

同时单独显示：

- `hintDependencyRisk`：使用过提示的任务占比，越高表示依赖风险越高；
- `hintProductivity`：请求提示后最终提交成功的比例，越高表示提示使用更有效；
- `prematureHints`：首次尝试前请求提示的次数。

这些指标描述行为模式，不判断求助本身好坏。

### 知识迁移能力

只有课程中包含 `KnowledgeComponents`、`IsTransfer` 和 `SourceTaskId` 标签的提交才参与：

`未使用提示且通过的迁移提交数 ÷ 全部迁移提交数`。

## 置信度与推荐

- 少于 5 条适用证据：低；
- 5–11 条：中；
- 12 条及以上：高。

阈值同样是产品显示规则，不代表统计置信区间。综合诊断选择当前有数据的最低维度，附带低样本提示，并返回课程、课节、步骤、目标维度与推荐原因。未来应通过学习增益、专家评分和跨群体公平性研究校准权重、阈值与推荐效果。
