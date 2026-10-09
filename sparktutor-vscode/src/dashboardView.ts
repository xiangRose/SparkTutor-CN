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
    <div><div class="eyebrow">SPARKTUTOR · 从实践到理解</div><h1>学习工作台</h1></div></div>
    <div class="toolbar"><label for="course-filter">学习范围</label><select id="course-filter">
      <option value=""${selected ? "" : " selected"}>全部课程</option>${courses.map((course) =>
        `<option value="${escapeHtml(course.id)}"${selected === course.id ? " selected" : ""}>${escapeHtml(course.title)}</option>`).join("")}
      </select><button data-action="refresh"${loading ? " disabled" : ""}>刷新</button></div></header>
    <p class="scope-note">范围筛选用于继续学习、诊断与推荐；课程目录始终显示全部课程。</p>`;
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
    <ul class="lesson-list">${course.lessons.map((lesson) => {
      const status = STATUS[lesson.status] || "未开始";
      const unavailable = lesson.available === false || lesson.status === "unavailable";
      const position = lesson.status === "in_progress" && lesson.totalSteps > 0
        ? ` · 当前第 ${count(lesson.currentStep) + 1} / ${count(lesson.totalSteps)} 步` : "";
      return `<li class="lesson-row"><span class="lesson-index" aria-hidden="true">${count(lesson.index) + 1}</span>
        <div class="lesson-text"><h4>${escapeHtml(lesson.title)}</h4><p><span class="lesson-status ${lesson.status}">${status}</span>${position}
        ${lesson.estimatedMinutes ? ` · 约 ${count(lesson.estimatedMinutes)} 分钟` : ""}</p></div>
        <button class="lesson-open" data-action="openLesson" data-course="${escapeHtml(course.id)}" data-lesson="${escapeHtml(lesson.id)}"
          ${unavailable ? `disabled data-permanent-disabled="true"` : ""} aria-label="打开${escapeHtml(lesson.title)}">${unavailable ? "不可用" : lesson.status === "completed" ? "回看" : lesson.status === "in_progress" ? "继续" : "开始"}</button></li>`;
    }).join("")}</ul>${!total ? `<p class="muted">该课程暂无可打开的课节。</p>` : ""}</article>`;
}

function resumeCard(result: LearningDashboardResult): string {
  const resume = result.resume;
  if (resume) {
    return `<section class="continue-card"><div><div class="eyebrow">${resume.action === "resume" ? "接着上次继续" : "开启一段新练习"}</div>
      <h2>${escapeHtml(resume.lessonTitle)}</h2><p>${escapeHtml(resume.courseTitle)} · ${escapeHtml(DEPTH[resume.depth] || resume.depth)}
      ${resume.action === "resume" ? ` · 当前第 ${count(resume.currentStep) + 1} 步` : ""}</p>
      <p class="muted">${resume.action === "resume" ? "继续已保存的题目位置，保留练习文件中的代码。" : "从本课第一步开始，在编辑器里边学边练。"}</p></div>
      <button class="primary" data-action="resume">${resume.action === "resume" ? "继续学习" : "开始学习"}<span aria-hidden="true"> →</span></button></section>`;
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
    return `<section class="notice" role="status"><h2>学习诊断暂时不可用</h2><p>课程目录仍可使用。请刷新重试；缺少诊断数据不表示分数为零。</p></section>`;
  }
  const recommendation = diagnosis.recommendedExercise;
  const dimensions = diagnosis.dimensions.filter((item) => item.key !== "hint_dependency");
  const hint = diagnosis.dimensions.find((item) => item.key === "hint_dependency");
  const components = Object.entries(result.knowledgeComponents || diagnosis.knowledgeComponents || {});
  return `<section class="diagnosis-section" aria-labelledby="diagnosis-title"><div class="section-heading"><div><div class="eyebrow">从行为证据观察学习</div>
    <h2 id="diagnosis-title">学习诊断</h2></div><span class="badge">形成性反馈</span></div>
    <div class="diagnosis-summary"><p>${escapeHtml(diagnosis.diagnosis)}</p><span class="muted">已记录 ${count(diagnosis.eventCount)} 条事件，其中 ${count(diagnosis.eligibleEventCount)} 条可用于本次诊断。</span></div>
    ${diagnosis.eventCount === 0 ? `<p class="notice">还没有学习证据。先打开一课并提交练习；证据不足时不会生成能力分数。</p>` : ""}
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
    <p class="disclaimer">${escapeHtml(diagnosis.disclaimer)}</p></section>
    <section class="recommendation" aria-labelledby="recommendation-title"><div><div class="eyebrow">下一道练习</div>
      <h2 id="recommendation-title">${recommendation ? escapeHtml(recommendation.title) : "继续积累有效练习证据"}</h2>
      <p>${escapeHtml(recommendation?.reason || diagnosis.recommendationReason || "完成练习后刷新，再查看适合的下一步。")}</p>
      ${recommendation ? `<p class="muted">直接打开具体题目，按单题练习完成，不跳过课程进度。</p>` : ""}</div>
      ${recommendation ? `<button class="primary" data-action="openRecommendation">打开推荐练习 →</button>` : ""}</section>`;
}

export function dashboardContent(result: LearningDashboardResult): string {
  return `${header(result.courses, result.selectedCourseId)}
    ${(result.warnings || []).map((warning) => `<p class="notice warning" role="status">${escapeHtml(warning.message)}</p>`).join("")}
    ${resumeCard(result)}
    <section aria-labelledby="courses-title"><div class="section-heading"><div><div class="eyebrow">把理解变成代码</div><h2 id="courses-title">课程与进度</h2></div>
      <span class="muted">进度表示课节完成情况，不是知识掌握度</span></div>
      <p class="section-help">完成率按有完成记录的课节计算，不表示所有难度均已完成。回看保留完成记录，重置后重新计算。</p>
      <div class="course-grid">${result.courses.map(courseCard).join("")}</div>
      ${result.courses.length ? "" : `<p class="notice">暂无可用课程。请检查课程安装情况后刷新。</p>`}</section>
    ${diagnosisSection(result)}
    <footer><div><h2>回看学习过程</h2><p>按任务查看学习历史与评估前后变化，或快速打开最近的行为记录。编辑计数不参与诊断评分。</p></div>
      <div class="history-links"><button data-action="learningHistory">历史与复盘</button>
      <button data-action="history">打开行为记录</button></div></footer><p id="dashboard-status" role="status" aria-live="polite"></p>`;
}

export function dashboardLoading(courses: DashboardCourse[], selected: string): string {
  return `${header(courses, selected, true)}<section class="notice loading" role="status" aria-live="polite"><h2>正在整理学习工作台……</h2>
    <p>读取课程进度和学习证据。你可以切换范围，页面将显示最后一次选择的结果。</p></section>`;
}

export function dashboardError(message: string, courses: DashboardCourse[], selected: string): string {
  return `${header(courses, selected)}<section class="notice error" role="alert"><h2>暂时无法加载学习工作台</h2>
    <p>${escapeHtml(message)}</p><p>请点击上方「刷新」重试，也可从侧栏直接打开课程。</p></section>`;
}
