/** Theme-aware workbench content; all course and learner text is rendered as data. */
import { dimensionCard } from "./diagnosisView";
import { escapeHtml } from "./lessonHelpers";
import { DashboardCourse, DashboardLesson, LearningDashboardResult } from "./types";

const STATUS: Record<DashboardLesson["status"], string> = {
  not_started: "未开始", in_progress: "学习中", completed: "已完成", unavailable: "暂不可用",
};
const DEPTH: Record<string, string> = { beginner: "初级", intermediate: "中级", advanced: "高级" };
const KNOWLEDGE_NAMES: Record<string, string> = {
  "dataframe.aggregate": "DataFrame 聚合计算", "dataframe.array": "数组类型与操作",
  "dataframe.cast": "数据类型转换", "dataframe.column": "列与列表达式", "dataframe.filter": "DataFrame 行筛选",
  "dataframe.group_by": "DataFrame 分组", "dataframe.hof": "高阶函数", "dataframe.io": "数据读写",
  "dataframe.join": "DataFrame 连接", "dataframe.schema": "数据结构与 Schema", "dataframe.select": "列选择与投影",
  "dataframe.window": "窗口函数", "dataframe.with_column": "新增与转换列",
  "lakehouse.catalog": "湖仓目录管理", "lakehouse.delta": "Delta 表", "lakehouse.formats": "湖仓表格式",
  "lakehouse.time_travel": "数据版本与时间旅行", "lakehouse.transactions": "湖仓事务",
  "ml.evaluation": "模型评估", "ml.features": "特征处理", "ml.regression": "回归模型",
  "pipeline.bronze": "Bronze 原始数据层", "pipeline.silver": "Silver 清洗数据层", "pipeline.gold": "Gold 业务汇总层",
  "pipeline.decorator": "管道装饰器", "pipeline.dependencies": "管道依赖", "pipeline.framework": "教学用管道框架",
  "spark.aqe": "自适应查询执行", "spark.architecture": "Spark 架构", "spark.broadcast": "广播机制",
  "spark.bucketing": "数据分桶", "spark.cache": "缓存与持久化", "spark.catalyst": "Catalyst 查询优化器",
  "spark.execution": "Spark 执行过程", "spark.partition": "数据分区", "spark.session": "SparkSession 会话",
  "spark.shuffle": "Shuffle 数据交换", "spark.sql": "Spark SQL", "spark.streaming": "结构化流处理",
  "spark.udf": "用户自定义函数", "spark.watermark": "流处理水位线",
};
const count = (value: number | undefined) => Number.isFinite(value) ? Math.max(0, Math.trunc(value!)) : 0;

function header(courses: DashboardCourse[], selected: string, loading = false): string {
  return `<header class="dashboard-header"><div class="brand"><span class="brand-mark" aria-hidden="true">S</span>
    <div><div class="eyebrow">SPARKTUTOR · 边学边练 Spark</div><h1>学习首页</h1></div></div>
    <div class="toolbar"><label for="course-filter">进度与诊断范围</label><select id="course-filter">
      <option value=""${selected ? "" : " selected"}>全部课程</option>${courses.map((course) =>
        `<option value="${escapeHtml(course.id)}"${selected === course.id ? " selected" : ""}>${escapeHtml(course.title)}</option>`).join("")}
      </select><button data-action="refresh"${loading ? " disabled" : ""}>刷新</button></div></header>
    <p class="scope-note">这里可以开始或继续课程、寻找调试帮助、查看学习记录。范围筛选影响继续学习、诊断与推荐；课程目录始终显示全部课程。</p>`;
}

function learningGuide(): string {
  return `<section class="learning-guide" aria-labelledby="guide-title"><div class="guide-heading"><div>
    <h2 id="guide-title">从一课开始，完成你的第一道 Spark 练习</h2>
    <p>按课程理解概念，在编辑器里写代码，用测试检查结果。</p></div><a class="home-link" href="#courses-title">浏览全部课程 ↓</a></div>
    <ol class="learning-path" aria-label="学习流程">
      <li><span aria-hidden="true">1</span><div><strong>打开课程</strong><small>阅读讲解，进入练习</small></div></li>
      <li><span aria-hidden="true">2</span><div><strong>编写并提交</strong><small>运行看输出，提交看判题</small></div></li>
      <li><span aria-hidden="true">3</span><div><strong>卡住时求助</strong><small>先看提示，再找教练排查</small></div></li>
      <li><span aria-hidden="true">4</span><div><strong>复盘并巩固</strong><small>查看诊断，练习推荐题</small></div></li>
    </ol></section>`;
}

function helpTools(): string {
  return `<section class="help-tools" aria-labelledby="help-title"><h2 id="help-title">需要帮助时，选对工具</h2>
    <div class="help-grid"><article class="help-card coach-entry"><div class="eyebrow">写代码卡住了</div><h3>AI 调试教练</h3>
      <p>结合当前代码和已有运行记录，逐步给出排查问题、检查方法和局部示例，帮助你自己修正代码。</p>
      <p class="muted">目前支持 M&amp;M 糖果计数与传感器聚合两道练习，使用前需配置模型。是否通过由提交判题确认。</p>
      <button data-action="coach">找 AI 教练帮我排查 →</button></article>
    <article class="help-card"><div class="eyebrow">不知道接下来练什么</div><h3>学习画像与推荐</h3>
      <p>从已有练习记录了解知识、调试、求助和迁移表现，查看诊断与下一道推荐练习。</p>
      <p class="muted">没有足够记录时会说明证据不足；不会把缺少记录算成零分。</p>
      <a class="home-link" href="#diagnosis-title">查看我的学习诊断 ↓</a></article>
    <article class="help-card"><div class="eyebrow">想回看做题过程</div><h3>学习历史与复盘</h3>
      <p>按题目回看运行、提交和求助过程，找到哪里卡住、后来怎样修复。</p>
      <p class="muted">学习首页随时可从左侧 SparkTutor 的「学习首页」返回。</p>
      <button data-action="learningHistory">回看我的学习过程 →</button></article></div>
    <details class="quick-help"><summary>第一次使用？了解运行、判题、提示和教练的区别</summary>
      <ul><li><strong>运行代码：</strong>查看程序输出与报错，不等于本题已通过。</li>
      <li><strong>提交判题：</strong>按本题规则检查答案；综合练习会运行真实 Spark 和课程测试。可以直接提交，无需先点运行。</li>
      <li><strong>题目提示：</strong>查看课程为当前题准备的提示。</li>
      <li><strong>AI 调试教练：</strong>在支持的练习中，按概念提问、检查方法、局部示例逐级求助，修改代码后再提交验证。</li>
      <li><strong>导师问答：</strong>在课程面板自由提问，帮助理解 Spark 概念。</li></ul>
      <p class="muted">综合练习需要可用的 Spark 环境；教练需要配置模型。可先从课程讲解和题目提示开始。</p></details></section>`;
}

function courseCard(course: DashboardCourse): string {
  const completed = course.lessons.filter((lesson) => lesson.status === "completed").length;
  const total = course.lessons.length;
  const percent = total ? Math.round(completed / total * 100) : 0;
  return `<article class="course-card"><div class="course-heading"><div><h3>${escapeHtml(course.title)}</h3>
    <p>${escapeHtml(course.description)}</p></div><span class="badge">${total} 节课</span></div>
    <div class="completion"><span>课节完成 ${completed} / ${total}</span><strong>${percent}%</strong></div>
    <progress value="${percent}" max="100" aria-label="${escapeHtml(course.title)}课节完成比例"></progress>
    ${course.requiresLakehouse ? `<p class="muted">课程包含湖仓环境练习，请先确认相应环境可用。</p>` : ""}
    ${course.prerequisites?.length ? `<details class="prerequisites"><summary>学习前置要求</summary><ul>${course.prerequisites.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul></details>` : ""}
    <details class="course-lessons"><summary>查看课节目录（${total} 节）</summary><ul class="lesson-list">${course.lessons.map((lesson) => {
      const status = STATUS[lesson.status] || "未开始";
      const unavailable = lesson.available === false || lesson.status === "unavailable";
      const position = lesson.status === "in_progress" && lesson.totalSteps > 0
        ? ` · 当前第 ${count(lesson.currentStep) + 1} / ${count(lesson.totalSteps)} 步` : "";
      return `<li class="lesson-row"><span class="lesson-index" aria-hidden="true">${count(lesson.index) + 1}</span>
        <div class="lesson-text"><h4>${escapeHtml(lesson.title)}</h4><p><span class="lesson-status ${lesson.status}">${status}</span>${position}
        ${lesson.estimatedMinutes ? ` · 约 ${count(lesson.estimatedMinutes)} 分钟` : ""}</p></div>
        <button class="lesson-open" data-action="openLesson" data-course="${escapeHtml(course.id)}" data-lesson="${escapeHtml(lesson.id)}"
          ${unavailable ? `disabled data-permanent-disabled="true"` : ""} aria-label="打开${escapeHtml(lesson.title)}">${unavailable ? "不可用" : lesson.status === "completed" ? "回看" : lesson.status === "in_progress" ? "继续" : "开始"}</button></li>`;
    }).join("")}</ul></details>${!total ? `<p class="muted">该课程暂无可打开的课节。</p>` : ""}</article>`;
}

function resumeCard(result: LearningDashboardResult): string {
  const resume = result.resume;
  if (resume) {
    return `<section class="continue-card"><div><div class="eyebrow">${resume.action === "resume" ? "接着上次继续" : "开启一段新练习"}</div>
      <h2>${escapeHtml(resume.lessonTitle)}</h2><p>${escapeHtml(resume.courseTitle)} · ${escapeHtml(DEPTH[resume.depth] || resume.depth)}
      ${resume.action === "resume" ? ` · 当前第 ${count(resume.currentStep) + 1} 步` : ""}</p>
      <p class="muted">${resume.action === "resume" ? "继续已保存的题目位置，保留练习文件中的代码。" : "从本课第一步开始，在编辑器里边学边练。"}</p></div>
      <button class="primary" data-action="resume">${resume.action === "resume" ? "继续学习" : "从这一课开始"}<span aria-hidden="true"> →</span></button></section>`;
  }
  const scoped = result.courses.filter((course) => !result.selectedCourseId || course.id === result.selectedCourseId);
  const lessons = scoped.flatMap((course) => course.lessons);
  const completed = lessons.length > 0 && lessons.every((lesson) => lesson.status === "completed");
  return `<section class="continue-card"><div><div class="eyebrow">下一步</div>
    <h2>${completed ? "所选范围的课节均有完成记录" : "暂时没有可继续的课节"}</h2>
    <p>${completed ? "可以回看课程，或通过下方推荐练习继续巩固。课节完成不等于已经掌握全部知识。" : "请从课程目录选择一课；如果课程尚未加载，请刷新重试。"}</p></div></section>`;
}

function diagnosisSection(result: LearningDashboardResult): string {
  const diagnosis = result.diagnosis;
  if (!diagnosis) {
    return `<section class="notice" role="status"><h2 id="diagnosis-title">学习诊断暂时不可用</h2><p>课程目录仍可使用。请刷新重试；缺少诊断数据不表示分数为零。</p></section>`;
  }
  const recommendation = diagnosis.recommendedExercise;
  const dimensions = diagnosis.dimensions.filter((item) => item.key !== "hint_dependency");
  const hint = diagnosis.dimensions.find((item) => item.key === "hint_dependency");
  const components = Object.entries(result.knowledgeComponents || diagnosis.knowledgeComponents || {});
  return `<section class="diagnosis-section" aria-labelledby="diagnosis-title"><div class="section-heading"><div><div class="eyebrow">从行为证据观察学习</div>
    <h2 id="diagnosis-title">学习画像与推荐</h2></div><span class="badge">根据已有练习记录</span></div>
    <div class="diagnosis-summary"><p>${escapeHtml(diagnosis.diagnosis)}</p><span class="muted">已记录 ${count(diagnosis.eventCount)} 条事件，其中 ${count(diagnosis.eligibleEventCount)} 条可用于本次诊断。</span></div>
    ${diagnosis.eventCount === 0 ? `<p class="notice">还没有学习证据。先打开一课并提交练习；证据不足时不会生成能力分数。</p>` : ""}
    <details class="profile-details"><summary>展开四维画像与知识点证据</summary>
    <p class="section-help">分别观察知识、修复与迁移；各维度使用不同样本，不合成为总分。</p>
    <div class="dimensions abilities">${dimensions.map((item) => dimensionCard(item, true)).join("")}</div>
    ${hint ? `<div class="hint-section"><div class="eyebrow">求助方式 · 描述性指标</div>${dimensionCard(hint, true)}</div>` : ""}
    <details class="knowledge-details"><summary>知识点证据（KC）<span>${components.length} 个有记录的知识点</span></summary>
      <p class="muted">每个知识点统计相关任务的最近一次有效评估。同一任务可能关联多个知识点，以下计数不能直接相加。</p>
      ${components.length ? `<ul class="knowledge-list">${components.map(([name, item]) => {
        const n = count(item.evaluatedTasks), passed = count(item.latestPassedTasks);
        return `<li><span class="kc-name">${escapeHtml(KNOWLEDGE_NAMES[name] || name)}${KNOWLEDGE_NAMES[name] ? `<small>${escapeHtml(name)}</small>` : ""}</span><span>最近评估通过 ${passed} / ${n} 个任务</span>
          <strong>${n ? `${(passed / n * 100).toFixed(1)}%` : "证据不足"}</strong></li>`;
      }).join("")}</ul>` : `<p>暂无可用的知识点证据；这不代表尚未学会。</p>`}</details>
    <p class="disclaimer">${escapeHtml(diagnosis.disclaimer)}</p></details></section>
    <section class="recommendation" aria-labelledby="recommendation-title"><div><div class="eyebrow">下一道练习</div>
      <h2 id="recommendation-title">${recommendation ? escapeHtml(recommendation.title) : "继续积累有效练习证据"}</h2>
      <p>${escapeHtml(recommendation?.reason || diagnosis.recommendationReason || "完成练习后刷新，再查看适合的下一步。")}</p>
      ${recommendation ? `<p class="muted">直接打开具体题目，按单题练习完成，不跳过课程进度。</p>` : ""}</div>
      ${recommendation ? `<button class="primary" data-action="openRecommendation">打开推荐练习 →</button>` : ""}</section>`;
}

export function dashboardContent(result: LearningDashboardResult): string {
  return `${header(result.courses, result.selectedCourseId)}
    ${(result.warnings || []).map((warning) => `<p class="notice warning" role="status">${escapeHtml(warning.message)}</p>`).join("")}
    ${learningGuide()}
    ${resumeCard(result)}
    ${helpTools()}
    <section aria-labelledby="courses-title"><div class="section-heading"><div><div class="eyebrow">把理解变成代码</div><h2 id="courses-title">课程与进度</h2></div>
      <span class="muted">进度表示课节完成情况，不是知识掌握度</span></div>
      <p class="section-help">完成率按有完成记录的课节计算，不表示所有难度均已完成。回看保留完成记录，重置后重新计算。</p>
      <div class="course-grid">${result.courses.map(courseCard).join("")}</div>
      ${result.courses.length ? "" : `<p class="notice">暂无可用课程。请检查课程安装情况后刷新。</p>`}</section>
    ${diagnosisSection(result)}
    <footer><details class="more-tools"><summary>更多工具：行为记录与诊断演示</summary>
      <p>行为记录列出最近的学习操作；诊断演示使用独立的预设轨迹，便于了解规则，不计入你的正常学习记录。</p>
      <div class="history-links"><button data-action="history">打开行为记录</button><button data-action="demo">打开诊断演示</button></div>
      </details></footer><p id="dashboard-status" role="status" aria-live="polite"></p>`;
}

export function dashboardLoading(courses: DashboardCourse[], selected: string): string {
  return `${header(courses, selected, true)}<section class="notice loading" role="status" aria-live="polite"><h2>正在加载学习首页……</h2>
    <p>读取课程进度和学习证据。你可以切换范围，页面将显示最后一次选择的结果。</p></section>`;
}

export function dashboardError(message: string, courses: DashboardCourse[], selected: string): string {
  return `${header(courses, selected)}<section class="notice error" role="alert"><h2>暂时无法加载学习首页</h2>
    <p>${escapeHtml(message)}</p><p>请点击上方「刷新」重试，也可从侧栏直接打开课程。</p></section>`;
}
