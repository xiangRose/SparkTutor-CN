# AI 证据教练：配对评估方案与病例集

本文的主比较对应首版两题范围的教练功能。病例文件为 [coach_cases.json](../tests/fixtures/coach_cases.json)。**2026-10-28 已完成同一 DeepSeek Flash 的 72 项真实请求；两名独立人工语义评审尚未完成，也没有教学实验结果。** 原始病例仍是未执行的作者模板，`results: []` 和 `evaluationStatus: not_run` 保持不变；实际执行记录另存新目录，不能回填或改写原始病例。

采用用户配置的自定义兼容接口模型；接口地址、模型标识由使用者填写，调用参数按客户端实际支持情况记录。通用工具不预设已验证的供应商；具体批次实际调用了什么模型，以该轮清单和下文记录为准。执行评估可能产生接口费用。准备工具始终离线；执行工具默认也不联网，只有明确添加 `--execute` 后才调用指定模型，不生成自动语义评分。

下文的统计来自维护者保留的本机运行记录；源码仓库提供病例、评价工具和方法说明，模型响应、运行数据库和已导出的人审包保存在被 Git 忽略的本机目录中。新环境可按后续命令重新构建批次，实际调用需另行配置自己的模型与预算。

## 2026-10-28 真实主比较状态

本机执行目录为 `coach-evaluation-output/deepseek-flash-main-v2-run-20261028/`，清单和逐项输入、模型原文、解析结果及实际可见提示均已保留，并逐项核对指纹。该轮使用 12 个作者病例，每个病例 Tutor/Coach 各 3 次，共 72 个请求项；全部得到 HTTP 200，无 HTTP 重试。Tutor 有 36 个可见输出；Coach 有 34 个可见提示，另 2 个因 `COACH_RESPONSE_PROBE` 被结构校验拒绝。失败对应 `transfer_missing_source`、`probe_not_course_execution` 的各一次重复，原文没有删除或被重试结果覆盖。**这些是端到端可用性和结构校验结果，不是语义正确率。**

两条件每次返回的模型均为 `deepseek-flash`，该轮观察到相同的后端 `system_fingerprint`；这不等同于供应商承诺固定模型快照。Tutor 的模型响应耗时中位数为 2906.5 毫秒、样本 p95 为 4000.5 毫秒；Coach 分别为 984 毫秒和 1203 毫秒。两种流程的输出形式和长度不同，不能把差异解释成相同输出长度的生成速度或教学效果。

72 次都返回了可核验 usage。Tutor 累计输入 89445 token、输出 14495 token；Coach 累计输入 87114 token、输出 3346 token。两条件合计缓存命中输入 143488 token、未命中输入 33071 token、输出 17841 token；未返回的推理 token 细分保持未知，不补成零。按 2026-10-28 官方 USD 高峰/非高峰报价计算，该轮估算区间为 **0.016095714–0.032191428 美元，非账单费用**；实际账单金额未知。程序统计在 `deepseek-flash-main-v2-program-metrics-20261028.json`，报价估算在 `deepseek-flash-main-v2-cost-estimate-20261028.json`，均位于本机 `coach-evaluation-output/` 下，不与其他试运行费用混合。[报价来源](https://api-docs.deepseek.com/quick_start/pricing/)

两份各 72 项的独立匿名人审包已导出到 `coach-evaluation-output/deepseek-flash-main-v2-human-review-20261028/reviewer-1/` 与 `reviewer-2/`。评分和独立性声明保持空白，需两名真实评审者分别完成；尚未生成语义评分汇总或宣称教练优于 Tutor。早期思考模式试运行和中断批次另行保留，不合并到本轮 72 项记录。

这轮结果对应清单中的冻结源码，另存有 112 个匹配指纹的源文件快照。2026-10-29 合入检查后修复了重复取消时的作业清理和配额释放流程；该修复没有修改模型提示、课程配置或上述原始运行记录。历史模型结果不能表述为针对后续所有代码版本重新执行过的结果。

另外六条修正版课程配置新例先冻结于 `coach-evaluation-output/deepseek-flash-catalog-exploration-inputs-20261028/`，使用真实函数签名、题目与代码，不伪造 Spark 执行或已掌握来源任务的证据。用户明确授权这六条输入外发后，已在独立的 `deepseek-flash-catalog-exploration-run-20261028/` 中完成 **6 次请求、6 次 HTTP 200，6 条响应通过结构校验并生成提示**，没有附加连接测试或语义重试；执行前后核对的输入与获批冻结内容全部一致，核验记录为 `approved-input-verification.json`。六例初始四维均为 `null`，没有伪造评估事件。模型响应中位耗时为 1109.5 毫秒，六次实际 usage 对应的独立官方报价估算为 **0.002189304–0.004378608 美元，非账单费用**；本轮程序统计和报价依据保存在该目录 `program-metrics-and-estimate.json`，不混入原 72 项或早期试运行费用。它们参考过协议回归病例，因此仅属于新例探索，不能包装成严格统计留出集；人工语义评审仍未完成，也不能据此宣称教学效果或跨主题准确率。旧的 `coach_catalog_cases.json` 继续仅表示模拟选择的协议测试，不能改写成真实模型记录。

## 离线准备一个可审阅批次

在已安装项目依赖的 Python 环境中，从仓库根目录运行（输出目录必须尚不存在）：

```bash
python tools/prepare_coach_evaluation.py --output demo-output/coach-evaluation-batch --model 模型标记 --seed 42 --repeats 3
```

默认生成 12 病例 × Tutor/Coach 两条件 × 3 次重复，共 72 项请求计划，相邻配对、顺序随机化。输出包含 `manifest.json`、`cases.frozen.json`、`request-plan.json` 及两份独立评审表 `reviewer-1.csv`、`reviewer-2.csv`。清单记录源文件和病例哈希、模型标记、共同参数及顺序随机种子；两份评审表隐藏条件标签并保留空白评分。

这些是计划项，不是已发出的请求。未指定模型记为 `unknown`；未明确控制的采样参数记为提供方默认或未知，顺序种子不代表模型采样种子。冻结病例含预期答案与人工判据，只供审阅，不能整份发送给模型。工具不构建实际模型输入、不读用户配置或学习库、不联网、不调用模型、不修改原病例状态。实际调用前仍须按下文固定共同参数与预算，通过产品的真实构建函数产生两条件输入，保存全部失败与最终展示结果，然后由评审者独立填写。

## 构建实际输入与显式执行

新增 [run_coach_evaluation.py](../tools/run_coach_evaluation.py) 读取上述冻结批次，检查病例、计划、评审表和产品提示构建源文件的哈希。每个病例、条件和重复使用新的临时学习数据库；只重放该病例的人工事件，按真实产品构建 Tutor 或 Coach 提示，不发送 `expected`、病例标题或 `input.description`。`code: null` 保持为空代码，不补入参考答案。脚本不读取现有账号数据、个人配置文件或保存的密钥。

冻结范围还包括执行客户端、评估工具、课程内容以及教练配置。应在代码和配置稳定后重新准备批次；来源文件新增、移除或修改后，旧批次会被拒绝，不能跨版本混合结果。

先用两项请求计划检查输入，**这一步没有模型输出，也不是对比评估结果**：

```bash
python tools/run_coach_evaluation.py --batch demo-output/coach-evaluation-batch --output demo-output/coach-input-check --limit 2
```

默认构建模式生成 `input.json` 和 `request-status.json`，不生成模型结果文件或评审正文；清单保留 `evaluationStatus: not_run`、`completedRequests: 0`、`networkRequestsMade: 0`。`builtInputCount` 仅表示构建的输入数，不能写成已调用模型的次数。省略 `--limit` 可构建完整计划，仍不调用模型。

需要实际评估时，在执行进程中配置 `SPARKTUTOR_OPENAI_API_KEY` 或 `OPENAI_API_KEY`，再显式指定同一个兼容接口和模型：

```bash
python tools/run_coach_evaluation.py --batch demo-output/coach-evaluation-batch --output demo-output/coach-model-run --execute --model 实际模型名称 --base-url https://你的兼容接口/v1
```

端点地址不能包含密钥、用户密码、查询参数或片段；密钥不通过命令行参数传入。若准备批次时指定过非 `unknown` 的模型标记，执行模型必须与之相同，否则重新准备批次。首次可另选新输出目录加 `--limit 2` 做小批次检查；该记录明确是部分计划，不能当作完整的 72 项评估。所有输出目录都必须尚不存在，工具不会覆盖以往证据，也不会静默续跑或筛掉失败记录。

执行使用产品兼容客户端，保留其每一次实际 HTTP 尝试、发送正文、失败状态和参数变化，包括接口拒绝 `max_tokens` 后的兼容重试；不额外重试或切换供应商。不会导出请求头、API 密钥、端点地址或模型私有推理。请求参数没有显式控制的 `temperature`、`top_p`、`seed` 仍记为提供方默认。usage 能从实际响应取得时记录，无法取得则保持未知；没有账单或可核验计价依据时，费用仍为 `unknown`。

执行目录中每项保存 `input.json`、`result.json`；Coach 原文还要经过产品原始解析器，正文由作者节点呈现。非法 JSON、非法引用、超时、截断和其他失败保留原始尝试，不补造可见答案。`contract_valid_semantics_unreviewed` 只表示通过结构约束，**不表示假设正确、提示有效或教学有效**。未成功生成正文时，评审包明确提示本项不可用。

`review-output/` 只提供随机评审 ID 对应的最终可见文本，两份 CSV 保持独立空白评分；条件映射在运行清单中，应与评审者隔离。工具没有调用任何真人学生，也不会给 K/D/T 增加真实成绩。实际模型请求结束后，状态为 `model_calls_finished_manual_review_pending`；仍须按第 6 节完成人工评审，并另行开展真人教学研究。

## 两名独立人工评审与维度汇总

真实请求完成后，使用 [summarize_coach_evaluation.py](../tools/summarize_coach_evaluation.py) 导出两个独立评审包：

```bash
python tools/summarize_coach_evaluation.py export-reviews --run demo-output/coach-model-run --output demo-output/coach-human-review
```

把 `reviewer-1/` 和 `reviewer-2/` 分别交给两名真实评审者，勿提供条件映射或另一人的评分。每包含匿名 `reviewItemId`、实际可见提示、相同的病例事实和仅供人工使用的判据，以及空白 `ratings.csv` 和 `declaration.json`。工具不会预填通过、失败、不可评、评审身份或独立性声明；`visibleOutputReference` 仅是文件定位信息。病例事实和判据不能回传给待评模型。输出结构和引用仍可能暴露条件，只能称遮蔽条件标签。

每名评审者须独立填写：

- `declaration.json`：自己的固定匿名 `reviewerId`、`reviewerType: human`、`completedIndependently: true`、`reviewedAllItems: true` 和带时区的 `completedAt`。
- CSV 每行使用同一 `reviewerId`，`availability` 填 `available` 或 `unavailable`。有正文但定位错误或建议无用仍是 `available`；没有生成可见正文才是 `unavailable`。
- 六个维度分别填 `pass`、`fail` 或 `unassessable`，不填加权总分；每项填写 `reason`，任何不可评项还须填写 `unassessableReason`。无可见正文时六维必须全部不可评，不能把超时直接算作语义错误，也不能从分母中悄悄删除失败请求。

两人完成后分别导入汇总，保留原始独立评分，不用共识表覆盖：

```bash
python tools/summarize_coach_evaluation.py summarize --run demo-output/coach-model-run --reviewer-1 demo-output/coach-human-review/reviewer-1 --reviewer-2 demo-output/coach-human-review/reviewer-2 --output demo-output/coach-reviewed-summary
```

导入会拒绝同一声明身份、非人工声明、缺失或重复项目、未知取值、不完整 CSV、被改写的提示/病例事实，以及把没有正文的请求写成可用等情况。声明的完成时间不能早于该批次请求结束。**这些检查验证声明与数据完整性，不能证明两名真实人员确实独立完成工作。** 未取得两份独立人工评审时，必须保持“人工评审未完成”，不能让工具或另一个模型代填后声称双人评审完成。

`summary.json` 和 `summary.md` 分别报告每个条件、每名评审者、每个维度的通过数、失败数、不可评数和可评分母，不把两名评审者当作翻倍的独立模型输出。原始分歧逐项保留，不自动裁决。双人一致率和 Cohen κ 只使用两人都填通过/失败的项目；没有可评样本、样本太少或边际分布退化时，κ 保持空值并给原因。κ 不是建议正确率，也不证明教学有效。

同时报告端到端可用输出数、首个 HTTP 尝试失败、重试、失败原因、模型响应耗时，以及有可见正文时的完成耗时。耗时以毫秒计，p95 使用样本内线性插值；小样本分位数须谨慎解释。实际 usage 只汇总已返回的字段并显示缺失覆盖；费用保持未知，不能把缺失值当作零。多次调用属于相同病例的重复，不是独立学生样本。

## 可选的同证据自由文本对照

默认主批次仍为 12 病例 × Tutor/Coach × 3 次，共 72 项。在主比较后，可准备一个**独立的新批次**，增加 `MatchedTutor`：

```bash
python tools/prepare_coach_evaluation.py --output demo-output/coach-matched-batch --model 实际模型名称 --seed 42 --repeats 3 --include-matched-evidence
```

该批次有 108 项：原 Tutor、Coach 和额外的 MatchedTutor，每个病例/重复内随机排列三种条件。MatchedTutor 沿用普通 Tutor 的实际系统提示和题目构建，另外提供 Coach 实际构建的相同题面、当前代码、事件依据、画像、环境与来源任务事实；不复制受约束选择目录、响应示例或 JSON 输出合同。仍要求只给下一步提示，输出自由文本。Coach 与 MatchedTutor 的共同事实单独计算 `matchingEvidenceSha256`，便于核对；模型仍看不到人工 `expected` 等标签。

汇总中 MatchedTutor 单独列出，不并入原 Tutor，也不把 108 项伪称为原计划的 72 项。该对照能帮助检查相同证据下两种支持流程的差异，不能单凭它将效果全部归因于某一个提示词或作者节点。

## DeepSeek Flash 兼容配置与可核验用量

截至 2026-10-28，DeepSeek 官方将 `deepseek-flash` 映射至已发布的 `DeepSeek-V4.1-Flash-1017`，兼容基础地址为 `https://api.deepseek.com`。这是文档中的当时映射，不代表服务承诺固定模型快照；执行记录仍保留每次实际返回的 `model` 和 `system_fingerprint`（若有），不能把未验证的模型版本写成固定快照。[官方接入说明](https://api-docs.deepseek.com/quick_start/agent_integrations/)

在产品代码和教练配置稳定后，重新准备真实模型批次；密钥只放执行进程环境中，不写入下面的参数：

```bash
python tools/prepare_coach_evaluation.py --output demo-output/deepseek-flash-main-plan --model deepseek-flash --seed 42 --repeats 3
python tools/run_coach_evaluation.py --batch demo-output/deepseek-flash-main-plan --output demo-output/deepseek-flash-main-run --execute --model deepseek-flash --base-url https://api.deepseek.com
```

首次连接可另建新输出目录，添加 `--limit 2` 做独立小批次；它不并入主批次。主比较继续使用同一客户端、同一模型和共同参数。当前产品对官方 `api.deepseek.com` 的 `deepseek-flash` 与 `deepseek-v4-pro`，在 Tutor、Coach 和连接检查中统一显式发送 `thinking: {"type": "disabled"}`，使有界教育提示请求优先生成最终正文；其他兼容提供方和模型保持原请求方式。正式请求仍为 `max_tokens: 4096`、90 秒期限，连接检查为 256 token；未显式覆盖 `reasoning_effort`、`temperature` 或 `top_p`。以每次记录的实际请求正文为准，不能把本地默认设置当作服务端已验证参数。此前仅思考或截断的试运行应保留为失败证据，另建新版本批次重跑，不能覆盖旧记录。即使显式关闭思考，仍会拒绝只有推理内容或 `finish_reason: length` 的不完整响应。以后再改模式或输出上限时，同样要对所有条件统一修改并重新冻结。[官方 Chat Completions 参数](https://api-docs.deepseek.com/api/create-chat-completion/)

记录器在实际返回时保存 `prompt_tokens`、`completion_tokens`、`total_tokens`、`prompt_cache_hit_tokens`、`prompt_cache_miss_tokens`，以及数量型的 `completion_tokens_details.reasoning_tokens` 和 `prompt_tokens_details.cached_tokens`。只保存数量，不保存模型私有推理正文。缺失字段保持未知，不用文本字数估计 token，也不把推理 token 在 `completion_tokens` 之外重复计费。[官方 usage 定义](https://api-docs.deepseek.com/api/create-chat-completion/)

可独立运行 [estimate_coach_evaluation_cost.py](../tools/estimate_coach_evaluation_cost.py)，使用真实返回的缓存命中、未命中及输出 token，并显式选择带来源、查询时间、币种和单位的报价快照：

```bash
python tools/estimate_coach_evaluation_cost.py --run demo-output/deepseek-flash-main-run --quote tools/pricing/deepseek-flash-20261028.json --output demo-output/deepseek-flash-estimated-cost.json
```

本仓库 2026-10-28 查询的官方 USD 报价快照按每百万 token 记录缓存命中、未命中和输出的高峰/非高峰费率。工具分别计算两种报价下的区间，**明确标为“按官方报价与实际返回 usage 估算，非账单费用”**。它不推断具体高峰时段或中国节假日，不查询余额，也不考虑赠送余额、折扣、账单舍入等因素。任一 HTTP 尝试缺少必要用量时，整批估算总额保持未知，只展示具有完整用量部分的小计；实际账单费用始终未知。价格可能调整，后续运行须重新核对报价来源与日期。[官方价格页](https://api-docs.deepseek.com/quick_start/pricing/)

## 1. 要回答的问题

首版只覆盖：

- `learning_spark / 01_getting_started / 15`，完整任务 ID 为 `01_getting_started:15`，M&M 分类数量聚合。
- `learning_spark / 11_diagnosis_practice / sensor_transfer`，完整任务 ID 为 `11_diagnosis_practice:sensor_transfer`，传感器上报数量聚合。

研究问题是：与现有自由对话 Tutor 相比，在这些固定案例中，教练是否更经常提供与证据相符、没有越权判分且可采取行动的提示？比较对象是两个产品流程的输出质量，不能直接回答学生是否学得更快、知识掌握是否提高或迁移能力是否增强。

这里的“创新”是把可核查的课程证据、受约束的模型选择、分级提示、真实课程验证与已有迁移推荐连接起来的流程和交互改进；不主张全球首创、不声称覆盖全部课程，也不声称训练了知识追踪模型。

## 2. 模型、作者节点和评分引擎各自负责什么

普通 Tutor 使用现有题面、代码和反馈构建提示，由模型生成自由文本。教练模型仅返回结构化选择：

```json
{
  "hypothesisId": "row_count_vs_quantity",
  "evidenceIds": ["task:learning_spark/01_getting_started:15"],
  "probe": {"values": [2, 4]}
}
```

允许的假设节点为 `missing_group_key`、`row_count_vs_quantity`、`target_site_parameter`、`tie_break_order`、`uncertain`；站点和并列排序节点只适用于传感器题。证据引用须来自服务端本次实际提供的集合，至少包含一个题目或事件证据，不能仅引用代码哈希。

用户选择帮助深度：概念提问、检查方法、局部示例。每层均可包含提问、行动建议和反思，因此“定位—反思—验证”是教学过程，不是模型自动选择的三个层级。第二、三层须在前一层已展示并确认后才能请求。

AI 的实际职责是结合输入选择待验证假设、选择相关证据，以及在允许时生成数字小样例。中文定位、反思和验证提示由作者审核的节点渲染；模型原文不直接作为学生可见正文。模型可能选错合法节点，结构校验只能约束格式和可引用范围，不能证明语义选择正确。

`probe` 可为 `null`，或只含 `values` 的对象。数字列表含 2–5 个 0..100 的严格整数，布尔值不算整数；要求数量累计不等于元素个数，以保证该示例能区分求和与计数。只有 `row_count_vs_quantity` 或 `uncertain` 节点可提供此示例，环境故障下不可提供；与该比较无关时应为 `null`。参考答案由 Python 固定计算，不执行模型生成代码、不运行学生函数，也不代表 Spark 课程测试通过。

课程通过、调试修复、答案影响和首次迁移仍由原有可信测试与诊断规则决定。只有实际展示的教练帮助才按协议记录提示；请求失败或未展示不冒充已提供帮助。实际完整答案必须走答案查看流程，事件名称为 `solution_view`，不能伪装成 `hint_request`。分层节点的边界仍需作者人工检查：即使没有完整代码，一条提示也可能直接泄露极短题目的核心答案，不能只按是否出现代码块判断。

## 3. 病例文件怎么使用

文件顶层记录版本、两题契约、模型输出约束和 12 个独立病例。每例包括：

- `task`：真实课程、课节、步骤及任务 ID。
- `stage`：人工评审重点是定位、反思还是验证行动；它不是 RPC 的 `level`，不表示已完成或解锁所有层级。
- `input`：作者自写的错误函数或错误描述、共同问题与合成事件。
- `expected`：可接受假设、必须说明的事实、不能声称的结论，以及既有规则的预期值。
- `evaluationStatus`、`results`：默认未执行和空结果；不预填成功率。

所有代码是为评估新写的公开小函数样例，不是用户工作区代码。函数契约取自仓库课程，代码不会在读取 JSON 时执行。部分病例仅提供历史而不提供当前代码，以检验模型是否保留不确定性。

事件采用 `LearningEvent.as_dict()` 的字段形状，但以 `fixture-` 标识，时间固定为合成时间。包括 `assessmentSource: course_tests` 的事件也只是作者设定的场景条件，**不是本次真的启动了 Spark**。只能在隔离临时存储中构建评估输入，不得导入真实学习数据库或当作真人成绩。准备真实运行证据时，需要另行执行课程并保存实际结果；不能把这些固定事件改名后充当执行日志。

本次病例覆盖：M&M 缺少分组键、把数量求和写成行计数、传感器写死站点、缺少并列排序、没有失败证据、环境启动错误、看答案后通过、迁移首评失败后重试通过、只编辑未重评、缺少迁移来源、合理提示辅助，以及数字示例不等于课程执行。

`expected`、`results`、病例标题、`input.description` 和其他病例的事件不得发送给任一模型。标题和描述用于组织人工场景，可能直接点出预设错误；模型只能使用该产品流程实际收到的代码、题目和事件证据。预期诊断值是人工推导的规则检查目标，模型输入中的画像须从隔离事件通过现有引擎计算，不能直接把期望答案注入输入。案例描述中的推导结果要明确标为人工案例说明；不得重新包装为执行器实际观测。

适配器将 `code: null` 转成空字符串，表示本例没有提供代码，不补入参考答案。`input.question` 是普通 Tutor 的共同提问；教练接口没有自由提问字段，使用自身受约束提示模板。建立隔离课程状态后，按顺序装入本例事件并通过实际构建函数取得模型输入；不得把整个病例 JSON 当作模型提示。每次保存最终实际发送的输入，以便核查对照差异。

代码证据哈希由服务端按本次实际代码计算。只有进入当前请求证据集合的事件才可以引用；所有事件在测试文件中存在，不代表全部应发送给模型。每个病例重置会话和临时状态，禁止前一病例的答案、提示或响应泄漏到下一病例。

## 4. 配对执行，先固定条件再查看结果

建议初次运行每个病例、每种条件各重复 3 次，共 12 × 2 × 3 = 72 个计划请求。这个数量是可操作的探索性方案，不是统计功效计算。调用前根据实际预算确认次数并固定方案，不能看完输出后只保留表现较好的重复。

每次评估固定并记录：

- 提交版本、病例集版本及内容哈希、提示模板和节点版本、运行日期。
- 用户配置的兼容接口类型、模型标识；如提供方不能保证固定模型快照，明确说明。
- temperature、top_p、最大输出长度，以及接口支持时的 seed；不支持的字段标记为“不支持”，不能假装已经生效。两条件使用相同的共同参数和足够的输出上限。
- 语言、用户自选难度、当前代码、题面版本、相同的病例事实、请求超时、重试规则。
- 各病例与条件的调用顺序；建议交错并随机化顺序，记录随机种子，不连续先跑完一种条件。

当前共享兼容客户端发送 `model`、`messages`、`stream: false` 和输出长度限制，不显式发送 temperature、top_p 或 seed；这些参数应记为“提供方默认，具体值未知”，不能写成已经固定为 0。接口拒绝 `max_tokens` 时客户端可能按原兼容逻辑去掉该字段重试，应在评估批次说明这一差异。若后续需要严格控制采样参数，须先补齐评估调用配置，再运行新批次；不能把文档中的期望配置当作当前已实现能力。

主比较使用现有普通 Tutor 的真实流程与新教练真实流程。二者共享病例、模型和共同可用参数，但教练增加结构化历史证据、输出约束和作者节点，因此结果是**完整方案对比**，不能将差异全部归因于模型、更丰富历史或某一个提示词。

如时间允许，再做一个可选对照：向自由文本条件提供与教练相同的证据摘要，并同样要求只给提示而不给整解。这有助于分辨“提供了更多信息”和“采用受约束流程”的影响；它是额外的匹配信息对照，不是原版 Tutor，结果必须分开报告。

主配对每个病例均从第 1 层开始，`stage` 只是判断输出的关注点；不能把它直接转换成第 2 或第 3 层请求，也不能伪造已展示提示来解锁高层。完整分级交互另行验收，实际展示并确认上一层后再继续，记录新增提示后的真实画像；病例中的 `diagnosisBefore` 仅对应初始事件。不能把一次第 1 层输出与另一个条件的三轮对话混比，或为了使教练胜出而给对照条件更少信息、更严格预算。

## 5. 保留模型输出与最终展示两层结果

每次请求至少保留：匿名 run ID、case ID、条件、重复序号、模型参数、请求/响应时间、输入摘要哈希、模型原始输出、解析及校验状态、最终学生可见文本、引用证据、回退原因、人工评分和评分理由。模型与提示内容仅使用这些作者病例；凭据、完整请求头和无关个人信息不得进入报告。

普通 Tutor 的自由文本与教练的最终作者节点是学生实际看到的内容，应比较这层的可靠性。同时单独报告模型层的结构合法率、假设选择和证据选择，不能把固定节点本身的正确性全部归功于模型生成。

所有失败都保留，包括网络失败、超时、非法 JSON、未知节点、失效证据和无效数字。当前教练在未配置或调用失败时停止，不自动切换其他模型或生成替代 AI 回复。若后续版本增加本地回退，必须单列，回退显示正确文字不代表模型成功；分别报告请求总数、模型有效返回数、模型选择有效数、回退数和最终可用提示数。提供方报错不直接判为教学建议语义错误，但须计入端到端可用性分母。

一次重试不能覆盖原始失败。需记录首请求结果、重试原因、重试结果，以及实际调用次数；若发生参数或提示调整，建立新的评估批次，不混合汇总。

## 6. 人工评审规则

评审前先根据课程契约和病例 `expected` 确定规则。去除模型名称、条件标签和品牌标识后打乱输出顺序；保留真实展示结构以免改变含义。评审者可能从分层格式猜出条件，因此只能称“遮蔽条件标签”，不能保证完全盲评。优先由两名熟悉 PySpark 的评审者独立判断，保留分歧及讨论后的结论；只有一名评审者时如实说明。

不设置任意加权总分。分别报告以下项目的通过数、可评数、不可评数和失败例子：

1. **证据真实性**：引用是否属于本请求、匹配当前任务和时点，引用内容是否真正支持陈述。合法 ID 只能证明存在，不能证明推理正确。无显式引用的普通 Tutor 标记为“未提供可核查引用”，同时独立判断其事实是否与输入一致。
2. **假设恰当性**：是否与已给代码/错误一致；没有代码、环境问题、已通过或证据不足时，是否保留不确定性。不得把历史错误硬套给未提供内容的新代码。记录具体错误类型，而非只检查字符串匹配。
3. **答案边界**：是否提供整段可提交解答，或在文本中泄露足以直接完成任务的全部关键步骤。作者批准的一个概念提示不自动等同整解；遇到短题边界分歧需说明，不靠正则表达式冒充最终判定。
4. **建议可用性**：是否给出一个与当前问题相关、学生能够执行或回答的下一步；是否区分预测、小样例参考和真实验证。避免无依据要求重装环境、反复运行同一代码或强制长反思。
5. **判题与迁移边界**：是否擅自宣称课程通过、提高四维成绩、从缺失证据推断弱能力、按提示使用扣分，或把目标重试改成新迁移成功。
6. **输出与呈现来源**：是否正确说明模型选择、作者节点和 Python 参考结果的分工；不得将本地回退写成模型建议或把生成数字写成真实学生结果。

`acceptableHypothesisIds` 是本病例的语义目标，不代表所有观点都能仅凭节点枚举自动评价。合法但不相关的节点仍应判为选择失当；在信息确实不足时，谨慎的 `uncertain` 应按具体案例判断，不能为了提高定位率强迫猜错。

还需验收帮助记账：同一展示只产生一次提示事件；失败、取消、过期响应和未展示内容不记为已帮助；提示归属原请求题目。实际显示后再读取诊断，K/D/T 不应凭提示产生新通过，H 是否改变按首评时间规则判断。完整答案出口必须记录 `solution_view`。这些是协议与规则检查，和模型文本评审分开报告。

## 7. 延迟、成本和结果报告

延迟记录从发出请求到完成校验，以及到提示可见的时间；保存每次原始值。每条件报告有效样本数、中位数、范围或分位数，并单列超时及回退。并发数、网络条件不同会影响结果，不能将跨环境数字解释成固有性能。

令牌数优先使用提供方实际返回的 usage；当前产品接口只返回模型正文，不保留 usage，后续评估需从实际调用记录或服务商账单取得，无法取得时记为未知。费用只有在获得实际账单，或注明模型计价来源、日期和计算方式时才报告；不得把“未知”写成零费用，不用估计输出字数冒充已计费令牌。

汇总时以病例为配对单位列出各次结果；多次调用是同一个病例的重复，不等于多个独立学生。必须同时给出代表性正确例、失败例和当前限制。禁止只挑展示效果最好的对话。

可使用以下表述：“在固定的作者构造案例中，该版本在证据引用/提示边界/可采取行动方面出现了……”。只有实际执行后才能填入数量。不能写成“学生成绩提高……”“已验证迁移能力提升”“提示减少证明依赖降低”，也不能用 K/D/T 在预设轨迹中的变化作为教学效果。

## 8. 研究依据和范围限制

- Klahr、Carver（1988），*Cognitive Objectives in a LOGO Debugging Curriculum: Instruction, Learning, and Transfer*，[DOI](https://doi.org/10.1016/0010-0285(88)90004-7)。本地 PDF 第 5–9 页支持分解调试过程，第 19 页提示最终成功不等于策略优良；没有验证本项目三级节点或四维比率。
- Choi 等（2023），*The benefit of reflection prompts for encouraging learning with hints in an online programming course*，[DOI](https://doi.org/10.1016/j.iheduc.2023.100903)。本地 PDF 第 4–8 页研究提示与反思的组合及后续任务。本项目据此设计可选反思，不把该论文效果量移植为本项目效果，也不要求每次提示前完成长问卷。
- Aleven 等（2006），*Toward Meta-cognitive Tutoring: A Model of Help-Seeking with a Cognitive Tutor*，[DOI](https://doi.org/10.3233/IRG-2006-16%282%2902)。本地 PDF 第 7–9、13、22 页强调情境化求助解释；不支持对高提示覆盖直接扣能力分。
- Du、Hew、Liu（2023），*What can online traces tell us about students’ self-regulated learning? A systematic review of online trace data analysis*，[DOI](https://doi.org/10.1016/j.compedu.2023.104828)。本地 PDF 第 11–13 页支持对行为含义作限定并寻求多来源核验；日志、模型解释和学生实际理解不是同一个概念。

完整书目、PDF 版本、页码说明和四维工程定义见 [学习诊断模型](learning-diagnosis-model.md)。论文支持设计问题的选择，不等于本项目算法获得心理测量标定。

病例数量有限、由作者选择，且模型看到的证据经过整理，不能代表真实用户代码、所有错误类型或所有接口模型。固定案例可能利于针对性优化，应冻结版本并保留未用于调试的新案例才能进一步评估泛化。短期真人试用可补充易用性观察；证明教学效果仍需另行设计适当的新题、评价方法、对照和样本，不能用这份工程评估替代。
