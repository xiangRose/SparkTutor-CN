# SparkTutor-CN

在 VS Code 中学习 PySpark：阅读中文讲解、动手编程、运行课程测试，并向 AI 导师提问。

基于 [lisancao/sparktutor](https://github.com/lisancao/sparktutor) 改造，遵循 MIT 协议。项目仓库：[xiangRose/SparkTutor-CN](https://github.com/xiangRose/SparkTutor-CN)。

## 学习内容

课程以 **Apache Spark / PySpark 4.1.1** 为验证基线，共 19 课、18 道综合编程练习。需要基础 Python 知识；SQL、装饰器和机器学习基础有助于学习后续章节。

- **Learning Spark（11 课）**：Spark 架构与入门、DataFrame/Schema、数据源与文件格式、复杂类型与高阶函数、SQL/Catalyst、性能优化、Join 策略、Structured Streaming、数据湖与湖仓、MLlib，以及调试与迁移诊断练习。
- **Spark 4.1 教学管道（8 课）**：SparkSession、数据转换、数据读写、教学用 Pipeline 框架、Bronze 原始层、Silver 清洗层、Gold 聚合层、完整管道。

第二套课程通过自制装饰器和拓扑排序讲解编排原理，使用临时视图连接各层，**不是官方 Spark Declarative Pipelines API 的完整教程，也不等同于生产级调度系统**。官方 SDP 使用 `pyspark.pipelines` 和 `spark-pipelines`，见 [官方指南](https://spark.apache.org/docs/4.1.1/declarative-pipelines-programming-guide.html)。

流处理综合练习使用批处理 DataFrame 模拟核心变换；湖仓练习用版本化 Parquet 目录模拟历史查询。它们需要真实 Spark，但不需要 Kafka、Delta 或 Iceberg 服务，不能据此验证真实流处理容错或湖仓事务能力。

## 安装

需要 VS Code 1.93+、Python 3.10+、Java 17+；构建扩展需要 Node.js 20+。Java 需独立安装并设置 `JAVA_HOME`，安装 PySpark 不会代替安装 JDK。

推荐在 Linux、WSL2 或仓库的 Dev Container 中运行真实 Spark。原生 Windows 的 Spark/Hadoop 运行环境需单独验证。4.1.1 修复了 4.1.0 的 Windows 导入问题，详见 [SPARK-54745](https://issues.apache.org/jira/browse/SPARK-54745)；修复启动器兼容性不代表所有外部 Spark/Java/Hadoop 组合都已受测。

```bash
git clone https://github.com/xiangRose/SparkTutor-CN.git
cd SparkTutor-CN
python -m venv .venv
# Linux / WSL / macOS
source .venv/bin/activate
pip install -e '.[spark,dev]'

cd sparktutor-vscode
npm ci
npm run lint
npm test
bash package-vsix.sh
code --install-extension sparktutor-0.3.0.vsix
```

Windows PowerShell 中可用 `.venv\Scripts\python.exe -m pip install -e ".[spark,dev]"` 安装依赖。打包脚本需要 Bash 和 zip，可使用 WSL 或已准备相应工具的 Git Bash。

将 VS Code 设置 `sparktutor.pythonPath` 指向项目虚拟环境解释器的完整路径，例如 `/path/SparkTutor-CN/.venv/bin/python` 或 `D:\project\SparkTutor-CN\.venv\Scripts\python.exe`。开发模式可打开 `sparktutor-vscode` 文件夹后按 F5。

只阅读、做选择题和语法检查时，可以安装 `pip install -e .` 而不装 Spark；这种环境不能通过需要执行的综合题。终端界面另需 `pip install -e '.[tui]'`。

## 使用与判题

1. 在 SparkTutor 侧边栏选择课程、课节和难度。
2. 阅读题面。短代码题保存在当前课节的练习文件中，综合练习打开独立文件。
3. **运行代码**：查看程序输出或错误；dry-run 只检查 Python 语法。
4. **提交判题**：选择题按选项内容判定；短代码题先做本地检查，必要时交由 AI 评审。
5. 综合题提交时，将学生实现交给**仓库原始 starter 中的测试入口**运行，不以学生编辑过的测试断言作为通过依据。真实执行成功且课程测试完成后才通过，无需 AI 密钥。
6. 根据反馈修改；通过当前题后进入下一步。阅读步骤可以直接继续。

综合题测试覆盖题面声明的样例与检查项，并非完整正确性证明。代码在选定环境执行，执行器不提供面向不可信 Python 的安全沙箱。

选择题稳定洗牌：不同题目答案位置不同，返回同一题时顺序保持稳定。中文答案和 API 符号不会在匹配时被删除。

快捷键：`Ctrl+Shift+R` 运行、`Ctrl+Shift+S` 提交、`Ctrl+Shift+N` 下一步、`Ctrl+Shift+B` 上一步、`Ctrl+Shift+H` 提示。

## AI 配置

支持 Anthropic Claude、GitHub Copilot 和 OpenAI 兼容接口（支持该协议的国内模型或 Ollama）。在设置中搜索 SparkTutor：

- `sparktutor.aiProvider`：auto、anthropic、copilot、openai-compatible。
- `sparktutor.anthropicApiKey` / `ANTHROPIC_API_KEY`：Claude 密钥。
- `sparktutor.openaiBaseUrl`、`sparktutor.openaiApiKey`、`sparktutor.openaiModel`：兼容接口地址、密钥和模型名称。
- 兼容接口密钥也可由 `SPARKTUTOR_OPENAI_API_KEY` / `OPENAI_API_KEY` 环境变量提供。
- `sparktutor.openaiExtraHeaders`：额外请求头；`sparktutor.copilotModel`：可选 Copilot 模型标识。

使用用户级设置或环境变量配置密钥，不要写入仓库。运行“SparkTutor：检查 AI 连接”确认可用性。自动模式依次尝试已配置的 Anthropic、兼容接口、Copilot，均不可用时仅使用本地检查。

AI 使用课程、代码及固定的 Spark 知识提示，输出简体中文；没有自动检索官方文档或训练专有模型。学习诊断通过下面的本地规则计算，无需 AI 密钥。难度仍由学习者选择，不会被诊断结果自动修改。

## 学习画像、诊断与推荐（Issue #5）

点击课程侧栏的诊断图标，或运行“SparkTutor：查看学习诊断”，即可从本地 Tracker 记录得到四维画像、一句中文诊断和一道推荐练习：

- **Knowledge**：每道题最近一次有效评估的通过率，一题只占一个样本；提示辅助的合理学习不扣分。
- **Debugging**：错误发生后，经过后续有效评估确认修复的过程比例；仅运行成功不能证明已经完成题目。
- **Hint Dependency**：首次有效评估前的平台提示使用覆盖率，同时展示提示后通过等指标；高低描述求助方式，不直接评价能力。
- **Transfer**：在已通过来源任务后，进入作者明确标注的新情境任务时的首次评估通过率；重复刷同一题不能增加迁移样本。

每项都显示证据数量；没有合格证据时显示“证据不足”，不会补成 0 分。dry-run、环境故障和未经独立验证的 AI 评审不进入知识/迁移评分，答案查看后的同题结果也不能伪装成新的掌握证据。旧版日志没有评估资格信息的部分会保留，但不会被追溯认定为可靠成绩。

推荐从实际课程中选择，检查难度、前置任务、知识点和已有记录。点击后直接打开指定练习，作为单题练习完成，保留原来的顺序学习进度；做完后刷新诊断查看变化。没有满足条件的候选时会说明原因。

设计参考了本地论文资料中的知识追踪、调试、求助和迁移研究，移除了旧原型的任意加权总分。**这些比例是有文献论证的工程操作定义，并非论文验证过的标准化能力量表，也未宣称训练了 DKT 模型。** 逐篇引用、公式、排除规则和后续效度验证方案见 [学习诊断设计说明](docs/learning-diagnosis-model.md)。

## 学习工作台（Issue #6）

点击课程侧栏的主页图标，或运行“SparkTutor：打开学习工作台”，可以在一页查看继续学习、全部课程与逐课进度、四维诊断、知识点证据和推荐练习。

继续学习优先打开最近更新的有效未完成课节，恢复保存的难度、题目位置和练习代码；没有历史时从首课开始。课节状态逐课读取，跳课不会把前面的课节误标为完成。回看保留已完成记录，主动重置该课后重新计进度；完成记录不代表每个难度都完成。课程完成率仅描述已完成课节的比例，不代表知识掌握度。

四维卡片可展开查看公式、分子分母和参与计算的具体题目或失败过程；提示使用单独作为求助行为描述，不合成为总分。知识点详情提供相关任务的最近有效评估结果，同一任务可关联多个知识点，计数不能直接相加。

页头范围筛选影响继续学习、诊断与推荐；课程目录仍完整展示，跨课迁移的来源检查仍使用完整历史。推荐以单题练习模式打开，保留顺序学习进度。工作台汇总接口只读取数据，打开或刷新前可先补发待记录编辑，不制造评分证据。基本验收无需 AI 密钥或 Spark 作业，操作步骤见 [第二阶段验收说明](docs/dashboard-acceptance.md)。

## 执行模式与保存

- `local`：当前 Python 环境的 spark-submit，建议用于内置综合练习。
- `lakehouse`：Docker 中的 Spark，需要自行准备服务和连接器。
- `databricks`：Spark Connect，需要配置远端环境。部分练习使用本地路径或 JVM 计划接口，不能保证直接适用于 Connect。
- `dry_run`：只检查语法，不计算结果，不记为综合题通过。
- `auto`：检测可用环境。切换模式会立即通知后端，无需重启扩展。

进度和事件在 `~/.sparktutor/progress.db`，代码在 `~/.sparktutor/workspace/<course>/<lesson>/`。短题用 exercise.py，综合题用 script_<题目标识>.py。旧版课程级作业保留；无法绑定题目的旧恢复代码单独备份，避免覆盖新题。

重置只清除当前课节进度和受管练习文件，保留其他课节、旧版作业与辅助数据。运行超时会结束本地进程树；终止 Docker CLI 不保证远端容器作业同时终止，远端资源需由运行环境管理。

## 开发与验证

### Tracker 行为记录验收

运行“SparkTutor：查看学习行为记录”，可查看最近 200 条学习事件，包括运行、提交、提示、答案查看、代码编辑和正常会话结束。编辑记录按短时间输入聚合，只保存修改次数、增删字符数等元数据，并绑定当前题目；这些过程记录本身不增加四维分数。界面自动准备起始代码、其他文件和参考答案文件的编辑不算学生作答。

Tracker 的操作步骤和验收勾选项见 [第一阶段验收说明](docs/tracker-acceptance.md)，学习工作台见 [第二阶段验收说明](docs/dashboard-acceptance.md)。各阶段分别验收；历史复盘等待后续指令。

```bash
pip install -e '.[dev]'
pytest tests -q
cd sparktutor-vscode
npm ci
npm run lint
npm test
npm run build
```

默认测试不启动 Spark。完整课程验证使用每道题的原始测试入口执行参考答案：

```bash
pip install -e '.[spark,dev]'
SPARKTUTOR_TEST_SPARK=1 pytest tests/test_course_execution.py -q
```

GitHub Actions 包含 Python 3.10/3.12 测试、扩展类型检查/测试/构建，以及 Linux + Java 17 + PySpark 4.1.1 的 18 道综合练习验证。课程检查还验证中文题面、引用文件、答案语法、执行要求和选择题干扰项。诊断测试覆盖历史重建、重复/乱序事件、跨课程隔离、缺数据、答案污染、迁移前置条件和推荐导航。

结构：courses/ 存课程；engine/ 管教学、判题、执行；server/ 提供逐行 JSON 通信；state/ 管 SQLite；config/ 管设置；app/ 提供终端界面；sparktutor-vscode/ 为扩展。更新内容见 [CHANGELOG.md](CHANGELOG.md)。

## 许可证

[MIT](LICENSE)，保留原项目版权与许可声明。
