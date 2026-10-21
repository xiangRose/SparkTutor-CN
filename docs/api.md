# SparkTutor API 配置与使用指南

本文面向刚拉取或安装 SparkTutor-CN 的用户，介绍如何在 **VS Code 扩展中**配置 AI 模型接口、验证配置并使用相关功能。示例使用公共服务地址、通用占位符或用户自行运行的本地服务，不依赖项目维护者的电脑路径。Python 终端界面的配置入口与扩展不同，不能直接套用这里的兼容接口设置。

首次使用建议按 **准备环境 → 配置兼容接口 → 检查连接 → 测试导师问答与教练** 的顺序操作。

## 1. 哪些功能需要 API

- **不需要 AI API**：阅读课程、选择题、本地代码检查、综合题的 Spark 课程测试、学习画像、规则诊断、推荐练习和学习历史。
- **需要 AI 服务**：导师问答，以及部分无法仅靠本地检查完成的代码评审。
- **需要明确配置兼容接口**：AI 调试教练。目前支持 M&M 糖果计数与传感器聚合两道练习，提供概念提问、检查方法和局部示例；是否通过仍由提交判题确认。

AI 接口负责生成反馈，Spark 环境负责执行练习。配置好模型不会自动安装 Java、PySpark，也不能解决 Spark 执行超时。

## 2. 配置前先启动项目

先按 [README 的安装说明](../README.md#安装) 安装依赖并启动扩展。仅克隆仓库，不会自动安装或启用 VS Code 扩展。

如果使用源码开发模式：

1. 在仓库根目录完成 Python 虚拟环境与项目依赖安装。
2. 进入 `sparktutor-vscode` 目录，执行 `npm ci` 和 `npm run build`。
3. 用 VS Code 打开 `sparktutor-vscode` 目录，在个人设置中配置 `sparktutor.pythonPath`，指向**你自己的虚拟环境 Python 解释器的完整路径**。Windows 通常选择当前仓库中的 `.venv/Scripts/python.exe`，Linux/macOS 选择 `.venv/bin/python`；填写时应转换为你机器上的绝对路径。
4. 选择 `Run Extension`，按 F5 打开“扩展开发宿主”。后面的配置与测试都在**扩展实际运行的窗口**进行。

如果已安装打包后的 VSIX，直接在启用了 SparkTutor 的窗口操作，无需再按 F5。

若开发窗口立即关闭，可选择 `SparkTutor：诊断启动（禁用其他扩展）`，详见 [启动排查](coach-acceptance.md#6-f5-打开的开发窗口立即关闭)。该模式会禁用其他已安装扩展，不适合验证依赖其他扩展的 Copilot 路径。

## 3. 快速配置：OpenAI 兼容接口

这里的“OpenAI 兼容”指请求和回复的协议格式，不要求购买 OpenAI 的服务。提供相同 Chat Completions 格式的其他模型服务也可以接入。

开始前，从你选择的服务商控制台准备三项信息：

- **API 基础地址（Base URL）**：模型 API 的服务地址。
- **模型 ID（Model）**：控制台或文档中支持调用的准确名称。
- **API 密钥（API Key）**：有权调用该模型的密钥。

网页聊天账号或订阅不一定包含 API 调用权限；以服务商控制台显示的权限与额度为准。

### 3.1 填写地址和模型

1. 在扩展运行窗口按 `Ctrl+Shift+P`，打开命令面板。
2. 运行 **`SparkTutor：配置 AI 调试教练模型`**。
3. 选择 **`填写 API 地址和模型名称`**。
4. 输入服务商的 API 基础地址，再输入准确的模型 ID。

这一步保存到 VS Code 的**个人设置**，不立即请求模型。

地址只填基础部分，不要带 `/chat/completions`。例如，服务商给出的完整请求地址是 `https://model.example.com/v1/chat/completions`，应填写 `https://model.example.com/v1`。这里的 `model.example.com` 只是占位符，不能直接使用。扩展会自行拼接 `/chat/completions`。

不要填网页聊天地址、API 密钥申请页面，或把密钥放进 URL 的查询参数。

### 3.2 安全保存密钥

1. 再次运行 **`SparkTutor：配置 AI 调试教练模型`**。
2. 选择 **`安全保存 API 密钥`**。
3. 在密码输入框中填写该服务商的密钥并确认。

密钥保存到 VS Code SecretStorage，不会由这个入口写入项目设置。重新执行此步骤可以替换已保存的兼容接口密钥。

**这个入口配置的地址、模型与密钥，也供普通 OpenAI 兼容的导师问答和代码评审使用；并非单独保存另一套教练模型。** 切换到另一家服务商时，应一起更新地址、模型和密钥。

### 3.3 让导师问答和评审也使用该接口

打开 VS Code 设置，搜索 `sparktutor.aiProvider`，在**用户／个人设置**中选择 `openai-compatible`。

这一步决定导师问答与需要 AI 的代码评审使用哪个供应商。教练始终直接使用已明确配置的兼容接口，不跟随这个选项改用 Anthropic 或 Copilot。

如果保留 `auto`，已有的 Anthropic 密钥可能使普通问答优先使用 Anthropic。首次验证兼容接口时，建议明确选择 `openai-compatible`，确认测试的是刚填写的服务。

### 3.4 公共服务示例：DeepSeek

按 [DeepSeek 官方接入说明](https://api-docs.deepseek.com/) 提供的 OpenAI 兼容地址，可以在上述配置窗口中填写：

- API 基础地址：`https://api.deepseek.com`
- 模型 ID：例如 `deepseek-flash`，或你的账户当前支持的其他准确 ID。
- API 密钥：在该服务商控制台申请，通过“安全保存 API 密钥”输入。

下面是**不含密钥**的个人设置示例，可通过 `Preferences: Open User Settings (JSON)` 打开用户设置后合并；中文界面可在命令面板搜索“用户设置 JSON”。不要覆盖已有的其他设置：

```json
{
  "sparktutor.aiProvider": "openai-compatible",
  "sparktutor.openaiBaseUrl": "https://api.deepseek.com",
  "sparktutor.openaiModel": "deepseek-flash"
}
```

地址与模型示例于 2026-10-21 核对；实际可用模型和调用权限以服务商最新文档及你的账户为准。使用其他兼容服务时，同时替换基础地址、模型 ID 和密钥，不要混用不同服务商的配置。

**教练要求地址和模型由你明确填写。仅保留扩展默认值，再填写密钥，不算完成教练配置。**

## 4. 配置好后怎样测试和使用

### 4.1 检查接口连接

在命令面板运行 **`SparkTutor：检查 AI 连接`**。

该命令检查当前普通 AI 路由选中的供应商。如果要验证第 3 节配置的兼容接口，先确认 `sparktutor.aiProvider` 为 `openai-compatible`。

兼容接口探测最多等待 30 秒。结果可能是：

- **返回了正文**：说明本次简单文本请求成功，可以继续测试实际功能。
- **接口已响应，但没有可用正文**：尚未确认正常生成；可能只返回思考内容，或输出被截断。不会把思考内容当作答案，也不会仅凭这一次空探测禁用接口。
- **认证、网络、路径或格式失败**：按提示检查密钥、基础地址、模型和网络，处理后手动重试。

连接检查通过不代表教练要求的结构化回复一定合格；还需要进行下一步真实功能测试。连接检查与实际求助会发送模型请求；扩展启动时也会自动探测已配置的供应商，可能按提供方规则消耗额度。

### 4.2 测试导师问答

1. 运行 **`SparkTutor：打开学习首页`**，打开任意课程。
2. 在课程面板找到 **`导师问答`**。
3. 输入简单问题，例如“SparkSession 有什么作用？”，点击发送。
4. 确认得到可读回复，而非密钥、连接或解析失败提示。

导师问答适合询问概念和题意；它的回复不等于课程测试已经通过。

### 4.3 测试 AI 调试教练

1. 从学习首页点击 **`找 AI 教练帮我排查`**，或运行 **`SparkTutor：打开 AI 调试教练`**。
2. 若当前没有支持的题目，从教练页选择 M&M 或传感器聚合练习。
3. 在编辑器尝试作答。建议先运行或提交一次，让教练有可参考的真实反馈；也可以先请求概念帮助。
4. 回到教练页，首次请求选择 **`1. 概念提问`**。
5. 提示显示后，先思考并修改代码；仍不清楚时，再选择已解锁的 **`2. 检查方法`**、**`3. 局部示例`**。
6. 修改代码后，刷新当前练习，再请求基于新代码的提示。最终用**提交判题**确认结果。

能够显示对应级别的提示，才说明该次模型回复通过了教练的格式和引用检查。失败、过期或未展示的回复不会被当作已使用提示。

若出现“比较样例的行数与总和”，该操作只核查一个教学小样例，不会运行你的 Spark 代码，也不会把当前题目改为通过。

## 5. 配置项与读取优先级

### 5.1 普通导师与评审使用哪个供应商

`sparktutor.aiProvider` 支持以下值：

- `openai-compatible`：使用兼容接口；需要配置地址、模型以及密钥或服务商要求的认证头。
- `anthropic`：使用 Python 后端的 Anthropic Claude 接口，见第 6 节。
- `copilot`：使用 VS Code 中可用的 Copilot 模型，见第 7 节。
- `auto`：依次考虑 Anthropic、已配置的兼容接口、可用的 Copilot；都不可用时保留本地检查。兼容接口连接探测真正失败后，自动选择可能在短暂冷却期内跳过它。

`auto` 是选择顺序，不代表任何一次模型调用失败后都会自动换供应商重试。AI 调试教练不按这条顺序回退。

### 5.2 兼容接口的设置项

- `sparktutor.openaiBaseUrl`：基础地址。
- `sparktutor.openaiModel`：准确模型 ID。
- `sparktutor.openaiApiKey`：兼容旧配置的明文密钥字段。新用户优先使用 SecretStorage 入口，不需要填写这个字段。
- `sparktutor.openaiExtraHeaders`：服务商确有要求时使用的额外 HTTP 请求头；普通 Bearer 密钥认证无需配置。它只能添加请求头，不能用于填写 `thinking` 等请求正文参数。

基础地址和模型的读取顺序为：**明确设置的 VS Code 值 → 对应环境变量 → 扩展默认值**。VS Code 中生效的工作区设置可以覆盖个人设置。

兼容接口密钥的读取顺序为：

1. 通过配置命令保存的 SecretStorage 密钥。
2. 生效的 `sparktutor.openaiApiKey` 设置。
3. 环境变量 `SPARKTUTOR_OPENAI_API_KEY`。
4. 环境变量 `OPENAI_API_KEY`。

如果改了环境变量却仍使用旧密钥，先检查是否已经通过配置命令保存了优先级更高的密钥。可以再次使用“安全保存 API 密钥”更新它。

### 5.3 使用环境变量（可选）

兼容接口支持：

- `SPARKTUTOR_OPENAI_BASE_URL`：基础地址。
- `SPARKTUTOR_OPENAI_MODEL`：模型 ID。
- `SPARKTUTOR_OPENAI_API_KEY`：密钥；也兼容 `OPENAI_API_KEY`。

环境变量必须能被**运行扩展的 VS Code 进程**读取。在已打开的 VS Code 终端里设置变量，不会反向修改已经启动的扩展宿主环境；仅新建 `.env` 文件也不会被当前扩展自动读取。

例如，在仓库根目录的 PowerShell 终端中设置本次会话变量，并通过密码输入接收密钥：

```powershell
$env:SPARKTUTOR_OPENAI_BASE_URL = "https://api.deepseek.com"
$env:SPARKTUTOR_OPENAI_MODEL = "deepseek-flash"
$modelKey = Read-Host "输入你的 API 密钥" -AsSecureString
$env:SPARKTUTOR_OPENAI_API_KEY = [System.Net.NetworkCredential]::new("", $modelKey).Password
Remove-Variable modelKey
code --new-window sparktutor-vscode
```

Linux/macOS 的终端示例：

```bash
export SPARKTUTOR_OPENAI_BASE_URL="https://api.deepseek.com"
export SPARKTUTOR_OPENAI_MODEL="deepseek-flash"
read -r -s -p "输入你的 API 密钥: " SPARKTUTOR_OPENAI_API_KEY
printf '\n'
export SPARKTUTOR_OPENAI_API_KEY
code --new-window sparktutor-vscode
```

上面的 Unix 示例使用 Bash；其他 shell 可使用各自的密码输入方式。先完全退出已有的 VS Code，再从设好变量的终端启动，避免复用未继承这些变量的旧进程。进入扩展开发窗口后再按第 4 节测试。

这些变量只作用于当前终端会话及其子进程；界面中已经明确保存的地址、模型和密钥仍有更高优先级。初次使用不必同时配置多种来源，按第 3 节操作即可。

## 6. 使用 Anthropic Claude（可选）

此路径用于**导师问答与需要 AI 的代码评审**。AI 调试教练仍需要另行完成第 3 节的兼容接口配置。

1. 准备你自己的 Anthropic API 密钥，让启动 VS Code 的环境能读取 `ANTHROPIC_API_KEY`。
2. 在个人设置中把 `sparktutor.aiProvider` 改为 `anthropic`。
3. 在 `sparktutor.claudeModel` 的设置下拉框中选择当前扩展支持且你的账户可调用的模型。
4. 重新加载扩展运行窗口，使 Python 后端读取新的配置，再运行 **`SparkTutor：检查 AI 连接`**。

也兼容个人设置中的 `sparktutor.anthropicApiKey`，但该字段是明文设置；优先使用环境变量，不要将真实值提交到项目。通过教练配置命令保存的是**兼容接口密钥**，不会自动变为 Anthropic 密钥。

改动 Anthropic 密钥或模型后，需要重新加载扩展窗口；如果使用环境变量，还应确保重新启动的 VS Code 继承了新变量。

## 7. 使用 GitHub Copilot（可选）

此路径用于**导师问答与需要 AI 的代码评审**，不会替代 AI 调试教练的兼容接口。

1. 在扩展实际运行的 VS Code 窗口安装并登录 GitHub Copilot，确保账户和编辑器能使用其模型功能。
2. 将 `sparktutor.aiProvider` 设为 `copilot`。
3. `sparktutor.copilotModel` 可以留空，由扩展选择可用模型；需要指定时填写该环境实际提供的模型 ID 或系列名称。
4. 运行连接检查，再在课程中测试导师问答。

如果通过“诊断启动（禁用其他扩展）”进入开发窗口，Copilot 扩展也可能不可用。需要改用正常启动方式验证这条路径，或使用兼容接口完成测试。

## 8. 使用本地 Ollama（可选）

先自行安装并启动 Ollama，准备一个已下载、可用的模型；可用名称通过 `ollama list` 查看。

在第 3 节的配置入口中填写：

- 基础地址：`http://localhost:11434/v1`。
- 模型 ID：`ollama list` 中对应的准确名称，不要填一个尚未下载的模型名。
- 密钥：对于未启用额外认证的本机 Ollama，安全保存一个非空占位值，例如 `ollama`。当前扩展需要非空认证配置才会发送兼容请求；这个占位值不是云服务的真实密钥。

本机地址及占位密钥的用法见 [Ollama 官方兼容接口文档](https://docs.ollama.com/api/openai-compatibility)。Ollama 的远端或云服务可能另有认证要求，应遵循对应服务配置。

`localhost` 指**扩展进程运行的环境**：在远程主机、WSL 或容器中运行扩展时，不一定能用它访问宿主机服务，需要改为该环境能够访问的地址。

将 `sparktutor.aiProvider` 设为 `openai-compatible` 后，按第 4 节验证。普通聊天可用，不代表每个本地模型都能稳定输出教练要求的结构化回复；应分别测试。

## 9. 常见问题

### 提示“尚未配置教练模型”，但密钥已经保存

教练还要求**明确填写基础地址和模型 ID**。再次运行配置命令，完成“填写 API 地址和模型名称”，不要只依赖默认值。确认设置发生在扩展实际运行的环境或配置文件中。

### 检查 AI 连接成功，教练仍然失败

先看检查结果对应的是哪个供应商。如果普通路由使用 Anthropic 或 Copilot，检查成功并没有验证教练的兼容接口。

即使兼容接口简单文本请求成功，教练回复仍需满足 JSON、真实证据引用和样例限制。出现 `COACH_RESPONSE_FORMAT`、`COACH_RESPONSE_REFERENCES`、`COACH_RESPONSE_PROBE` 等错误时，表示该次教学回复不合要求，不必据此立即更换密钥；可以重试同一级提示，持续失败再检查模型兼容性。

### 401、403：认证未通过

检查密钥是否属于当前基础地址对应的服务商，账户是否有该模型的调用权限，以及 SecretStorage 中是否仍保存旧密钥。更换服务商时应同步更新三项配置。

### 404、405：接口路径不可用

检查服务商提供的是不是 Chat Completions 兼容接口，是否误填网页地址或重复添加 `/chat/completions`。不是所有服务都以 `/v1` 结尾，以官方提供的基础地址为准。

### 429、5xx、连接超时

429 通常需要检查限流、账户额度或配额；5xx 需检查服务状态；连接超时需检查网络、地址与接口可达性。处理后重新运行连接检查，不必反复提交课程代码来测试网络。

### 空回复、只有思考内容、输出被截断

这不直接说明密钥错误。接口可能可达，但没有在本次请求预算内返回可用正文。先确认服务商支持的模型与接口格式，再测试实际教练提示；持续出现时可换用适合短文本、结构化回复的模型。不要把思考过程当成最终提示。

### 问答一直使用另一家模型

检查 `sparktutor.aiProvider` 是否为 `auto`，以及是否存在优先级更高的 Anthropic 配置。需要固定使用兼容接口时改为 `openai-compatible`，并检查工作区设置有没有覆盖个人设置。

### 改了环境变量，配置却没有变化

确认 VS Code 是从设好变量的进程启动，并检查是否存在优先级更高的个人／工作区设置或 SecretStorage 密钥。只在集成终端执行 `export` 或设置 `$env:`，不会更新已运行的扩展进程。

### 提交判题显示 Spark 执行超时

这是课程执行链的问题，应检查 Java、PySpark、执行模式及运行输出。M&M 等综合题依靠真实 Spark 和课程测试判定，不需要等待 AI 教练给分。API 连通与 Spark 运行应分开验证。

## 10. 密钥与分享说明

- 示例里的模型和地址可以分享，真实 API 密钥应由每位使用者自行准备。
- 不把密钥写入仓库的 `.vscode/settings.json`、代码、截图或提交记录；不要把个人配置复制成团队默认配置。
- 接口地址和模型使用个人设置，兼容接口密钥优先通过“安全保存 API 密钥”输入。
- 请求 AI 时，会把相关题目、代码或学习上下文发送给你配置的模型服务；使用自己认可的服务。
- 分享报错时提供错误类别、模型 ID 和不含凭据的基础地址即可，先去除认证头、密钥及无关私人信息。

更多操作说明：[项目 README](../README.md)、[学习首页验收](dashboard-acceptance.md)、[AI 调试教练配置与验收](coach-acceptance.md)。
