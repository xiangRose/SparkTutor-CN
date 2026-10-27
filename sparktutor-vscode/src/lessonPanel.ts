/**
 * Lesson webview panel: renders step content, feedback, navigation, and chat.
 */

import * as vscode from "vscode";
import { EvalResult, StepData } from "./types";
import { choiceButton, escapeHtml, shuffledChoices, stepIdentity } from "./lessonHelpers";

export class LessonPanel {
  private panel: vscode.WebviewPanel | null = null;
  private readonly extensionUri: vscode.Uri;

  // Callbacks for webview -> extension messages
  public onSubmit?: () => void;
  public onRun?: () => void;
  public onNext?: () => void;
  public onBack?: () => void;
  public onHint?: () => void;
  public onChat?: (question: string) => void;
  public onChoiceSelect?: (choice: string) => void;
  public onDiagnosis?: () => void;
  public onCoach?: () => void;
  public onHome?: () => void;

  constructor(extensionUri: vscode.Uri) {
    this.extensionUri = extensionUri;
  }

  show(): void {
    if (this.panel) {
      this.panel.reveal(vscode.ViewColumn.Two);
      return;
    }

    this.panel = vscode.window.createWebviewPanel(
      "sparktutorLesson",
      "SparkTutor 课程",
      vscode.ViewColumn.Two,
      {
        enableScripts: true,
        retainContextWhenHidden: false,
        localResourceRoots: [
          vscode.Uri.joinPath(this.extensionUri, "media"),
        ],
      }
    );

    this.panel.onDidDispose(() => {
      this.panel = null;
    });

    this.panel.webview.onDidReceiveMessage((msg) => {
      switch (msg.type) {
        case "home":
          this.onHome?.();
          break;
        case "submit":
          this.onSubmit?.();
          break;
        case "run":
          this.onRun?.();
          break;
        case "next":
          this.onNext?.();
          break;
        case "back":
          this.onBack?.();
          break;
        case "hint":
          this.onHint?.();
          break;
        case "chat":
          this.onChat?.(msg.question);
          break;
        case "choiceSelect":
          this.onChoiceSelect?.(msg.choice);
          break;
        case "diagnosis":
          this.onDiagnosis?.();
          break;
        case "coach":
          this.onCoach?.();
          break;
      }
    });
  }

  updateStep(
    step: StepData,
    currentIndex: number,
    totalSteps: number,
    lessonTitle: string,
    depth: string = "beginner",
    practiceMode = false
  ): void {
    this.show();
    this.setHtml(step, currentIndex, totalSteps, lessonTitle, depth, practiceMode);
  }

  showFeedback(result: EvalResult): void {
    this.panel?.webview.postMessage({
      type: "feedback",
      passed: result.passed,
      feedback: result.feedback,
      encouragement: result.encouragement,
    });
  }

  showHint(hint: string): void {
    this.panel?.webview.postMessage({
      type: "hint",
      hint,
    });
  }

  showChatResponse(answer: string): void {
    this.panel?.webview.postMessage({
      type: "chatResponse",
      answer,
    });
  }

  notifyExecDone(): void {
    this.panel?.webview.postMessage({
      type: "execDone",
    });
  }

  showFinished(practiceMode = false): void {
    this.panel?.webview.postMessage({
      type: "finished",
      practiceMode,
    });
  }

  private setHtml(
    step: StepData,
    currentIndex: number,
    totalSteps: number,
    lessonTitle: string,
    depth: string = "beginner",
    practiceMode = false
  ): void {
    if (!this.panel) {
      return;
    }

    const cssUri = this.panel.webview.asWebviewUri(
      vscode.Uri.joinPath(this.extensionUri, "media", "lesson.css")
    );
    const jsUri = this.panel.webview.asWebviewUri(
      vscode.Uri.joinPath(this.extensionUri, "media", "lesson.js")
    );

    const progressPercent = Math.round(
      ((currentIndex + 1) / totalSteps) * 100
    );

    // Build step-type-specific content
    let instructionHtml = "";
    let choicesHtml = "";
    let actionButtonsHtml = "";

    if (step.cls === "text") {
      // Read-only content step — just Next
      instructionHtml = `<div class="instruction-banner info">阅读以上内容，然后点击<strong>下一步</strong>继续。</div>`;
      actionButtonsHtml = `<div class="actions">
        <button class="btn btn-primary" onclick="send('next')">下一步 &rarr;</button>
      </div>`;
    } else if (step.cls === "mult_question") {
      // Multiple choice — pick an answer, then Submit
      const sourceChoices = step.answerChoices
        ? step.answerChoices.split(";").map((c) => c.trim())
        : [];
      const choices = shuffledChoices(sourceChoices, `${lessonTitle}:${stepIdentity(step)}`);
      instructionHtml = `<div class="instruction-banner prompt">在下方选择一个答案，然后点击<strong>提交</strong>。</div>`;
      choicesHtml = `<div class="choices">${choices
        .map(choiceButton)
        .join("")}</div>`;
      actionButtonsHtml = `<div class="actions">
        <button class="btn btn-success" onclick="send('submit')">&check; 提交</button>
      </div>`;
    } else if (step.cls === "cmd_question" || step.cls === "script") {
      // Code step — write code in editor, Run/Submit
      const label =
        step.cls === "script"
          ? "在<strong>左侧独立练习文件</strong>中完成综合练习。<strong>运行代码</strong>查看输出；<strong>提交判题</strong>执行课程测试并检查答案。需要可用的 Spark 环境；仅语法检查不能通过综合题。"
          : "在<strong>左侧本课练习文件</strong>中编写代码。<strong>运行代码</strong>查看执行结果；<strong>提交判题</strong>检查本题答案。每次运行都会启动新进程，所需变量和导入须在文件中定义。";
      instructionHtml = `<div class="instruction-banner prompt">${label}</div>
        <p class="help-note">练习代码会在所选环境中执行。请仅运行可信代码；当前执行器不提供不可信 Python 的安全隔离。</p>`;
      actionButtonsHtml = `<div class="actions">
        <button class="btn btn-primary" onclick="send('run')">&#9654; 运行代码</button>
        <button class="btn btn-success" onclick="send('submit')">&check; 提交判题</button>
      </div>`;
    }

    this.panel.webview.html = `<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <link rel="stylesheet" href="${cssUri}">
  <title>SparkTutor</title>
</head>
<body>
  <div class="lesson-topbar">
    <button class="btn btn-secondary" onclick="send('home')">&larr; 学习首页</button>
    <span>阅读题目 → 编写答案 → 提交判题 → 查看反馈</span>
  </div>
  <div class="lesson-header">
    <h2>${escapeHtml(lessonTitle)}</h2>
    <div class="progress-bar">
      <div class="progress-fill" style="width: ${progressPercent}%"></div>
    </div>
    <div class="step-meta">
      <span class="step-label">第 ${currentIndex + 1} 步 / 共 ${totalSteps} 步</span>
      <span class="depth-badge depth-${depth}">${depthLabel(depth)}</span>
    </div>
  </div>

  ${practiceMode ? `<div class="instruction-banner info">推荐单题练习：完成本题后刷新学习画像，原课节的学习进度会保留。</div>` : ""}

  <div class="step-content">
    <div class="step-output">${markdownToHtml(step.output)}</div>
    ${choicesHtml}
  </div>

  ${instructionHtml}

  <div id="loading-overlay" class="loading-overlay hidden">
    <div class="loading-spinner"></div>
    <span class="loading-text">处理中...</span>
  </div>

  <div id="feedback-section" class="feedback-section hidden"></div>

  ${actionButtonsHtml}

  <div class="nav-buttons">
    <div class="step-navigation">
      ${practiceMode ? "" : `<button class="btn btn-secondary" onclick="send('back')">&larr; 上一步</button>`}
      <button class="btn btn-secondary" onclick="send('next')">${practiceMode ? "完成练习" : "下一步 &rarr;"}</button>
    </div>
    <section class="lesson-help" aria-label="学习帮助">
      <h3>卡住了？选择适合你的帮助</h3>
      <div class="help-option">
        <button class="btn btn-secondary" onclick="send('hint')">课程提示</button>
        <p>想找解题方向：查看本题预设的提示。</p>
      </div>
      <div class="help-option">
        <button class="btn btn-secondary" onclick="send('coach')">AI 调试教练</button>
        <p>代码不对、却不知道从哪查：结合当前代码和学习记录，逐步给出排查建议。当前试点为 M&amp;M 糖果计数与传感器聚合（迁移练习）两道练习。</p>
      </div>
      <p class="help-note">想问概念或题意？使用下方的<strong>导师问答</strong>。教练的建议用于帮助排查，是否通过以<strong>提交判题</strong>的反馈为准。</p>
    </section>
  </div>

  <div id="hint-section" class="hint-section hidden"></div>

  <hr>
  <div class="chat-section">
    <h3>导师问答</h3>
    <p class="help-note">对概念、题意或当前步骤有疑问，在这里直接提问。</p>
    <div id="chat-messages" class="chat-messages"></div>
    <div class="chat-input-row">
      <input type="text" id="chat-input" placeholder="就本步骤提问..." onkeydown="if(event.key==='Enter')sendChat()">
      <button class="btn btn-primary" onclick="sendChat()">发送</button>
    </div>
  </div>

  <script src="${jsUri}"></script>
</body>
</html>`;
  }

  dispose(): void {
    this.panel?.dispose();
    this.panel = null;
  }
}

/** Map internal depth level to a Chinese label for the badge. */
function depthLabel(depth: string): string {
  switch (depth) {
    case "beginner":
      return "入门";
    case "intermediate":
      return "中级";
    case "advanced":
      return "高级";
    default:
      return depth;
  }
}

/** Markdown-to-HTML: code blocks, inline code, bold, italic, lists, links, paragraphs. */
function markdownToHtml(md: string): string {
  // Extract fenced code blocks BEFORE escaping (they need raw content)
  const codeBlocks: string[] = [];
  let processed = md.replace(
    /```(\w*)\n([\s\S]*?)```/g,
    (_m, lang, code) => {
      codeBlocks.push(
        `<pre><code${lang ? ` class="language-${lang}"` : ""}>${escapeHtml(code)}</code></pre>`
      );
      return `%%CODE_BLOCK_${codeBlocks.length - 1}%%`;
    }
  );

  // Now escape the rest
  processed = escapeHtml(processed);

  // Restore code blocks (already escaped internally)
  codeBlocks.forEach((block, i) => {
    processed = processed.replace(`%%CODE_BLOCK_${i}%%`, block);
  });

  // Headings: ## ... (h2–h4, after escaping so # is literal)
  processed = processed.replace(/^#### (.+)$/gm, "<h4>$1</h4>");
  processed = processed.replace(/^### (.+)$/gm, "<h3>$1</h3>");
  processed = processed.replace(/^## (.+)$/gm, "<h2>$1</h2>");

  // Inline code: `...`
  processed = processed.replace(/`([^`\n]+)`/g, "<code>$1</code>");

  // Bold: **...**
  processed = processed.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");

  // Italic: *...* (but not inside <strong>)
  processed = processed.replace(/(?<!\*)\*([^*\n]+)\*(?!\*)/g, "<em>$1</em>");

  // Links: [text](url)
  processed = processed.replace(
    /\[([^\]]+)\]\((https?:\/\/[^)]+)\)/g,
    '<a href="$2">$1</a>'
  );

  // Numbered lists: lines starting with 1. 2. etc
  processed = processed.replace(/^(\d+)\. (.+)$/gm, "<li>$2</li>");
  processed = processed.replace(/((?:<li>.*<\/li>\n?)+)/g, (match) => {
    // Detect if original had numbers (ol) or bullets (ul) — we already converted both to <li>
    return `<ol>${match}</ol>`;
  });

  // Bullet lists: lines starting with - or *  (after numbered, so * doesn't clash)
  processed = processed.replace(/^[*-] (.+)$/gm, "<li>$1</li>");
  processed = processed.replace(/((?:<li>.*<\/li>\n?)+)/g, (match) => {
    // If already wrapped in <ol>, skip
    if (match.startsWith("<ol>")) { return match; }
    return `<ul>${match}</ul>`;
  });

  // Paragraphs: split on blank lines, wrap non-block content in <p>
  const blocks = processed.split(/\n{2,}/);
  const result = blocks
    .map((block) => {
      const trimmed = block.trim();
      if (!trimmed) { return ""; }
      // Don't wrap block-level elements
      if (
        trimmed.startsWith("<pre>") ||
        trimmed.startsWith("<ul>") ||
        trimmed.startsWith("<ol>") ||
        trimmed.startsWith("<h")
      ) {
        return trimmed;
      }
      // Convert single newlines within a paragraph to <br>
      return `<p>${trimmed.replace(/\n/g, "<br>")}</p>`;
    })
    .join("\n");

  return result;
}
