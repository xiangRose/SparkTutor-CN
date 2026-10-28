'use strict';

const $ = (selector, root = document) => root.querySelector(selector);
const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
const token = $('meta[name="sparktutor-token"]').content;
const accountUser = $('meta[name="sparktutor-user"]')?.content || '';
const storagePrefix = accountUser ? `sparktutor.user.${accountUser}.` : 'sparktutor.';
if (sessionStorage.getItem('sparktutor.activeUser') !== accountUser) {
  for (const key of ['session','lastLesson','studySession','scope','depth']) sessionStorage.removeItem(`sparktutor.${key}`);
  sessionStorage.setItem('sparktutor.activeUser',accountUser);
}
const isStudyTab = new URLSearchParams(location.search).get('study') === '1';
// A newly opened window inherits sessionStorage. Give learning its own server context.
if (isStudyTab && !sessionStorage.getItem('sparktutor.studySession')) {
  sessionStorage.removeItem('sparktutor.session');
  sessionStorage.removeItem('sparktutor.lastLesson');
  sessionStorage.setItem('sparktutor.studySession','1');
}
let session = sessionStorage.getItem('sparktutor.session');
if (!session) {
  // getRandomValues also works over plain HTTP on a LAN; randomUUID requires
  // a secure context and is unavailable at http://192.168.x.x, for example.
  session = Array.from(crypto.getRandomValues(new Uint8Array(16)), value => value.toString(16).padStart(2,'0')).join('');
  sessionStorage.setItem('sparktutor.session', session);
}
const state = { storagePrefix, dashboard: null, lesson: null, courseId: '', lessonIdx: 0, depth: sessionStorage.getItem('sparktutor.depth') || 'beginner', choice: '', passed: false,
  busy: false, routeVersion: 0, courseFilter: '', query: '', historyOffset: 0, historyWindow: 'all', historyCourse: '',
  helpTab: 'tutor', chats: [], coach: null, coachMessage: null, edit: null, draftTimer: null, lastCode: '', draftDirty: false, scopeCourseId: sessionStorage.getItem('sparktutor.scope') || '', notificationCursor: 0, operationId: '', depthExplicit: sessionStorage.getItem('sparktutor.depth') !== null };
const names = { home: '学习工作台', courses: '课程目录', lesson: '课程学习', diagnosis: '学习画像', history: '学习历史', demo: '学习诊断示例', settings: '设置', events: '学习行为记录', account:'我的账号', admin:'管理后台' };
const eventNames = { code_submit: '提交判题', code_run: '运行代码', hint_request: '查看提示', code_edit: '编辑代码', task_start: '开始练习', task_complete: '完成步骤', solution_view: '查看答案', chat_request: '导师问答', lesson_loaded: '进入课程' };
const statusNames = { not_started: '尚未开始', in_progress: '学习中', completed: '已完成', unavailable: '暂不可用' };
let toastTimer;

const iconPaths = {
  grid: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  book: '<path d="M12 6c-3-2-6-2-9-1v14c3-1 6-1 9 1 3-2 6-2 9-1V5c-3-1-6-1-9 1Z"/><path d="M12 6v14"/>',
  chart: '<path d="M4 4v16h16M8 15v-4m5 4V7m5 8v-5"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  play: '<path d="m9 5 11 7-11 7V5Z"/>',
  settings: '<path d="m9 3-1 3-3 1-2 3 2 2v3l3 1 1 3h5l1-3 3-1 2-3-2-2V7l-3-1-1-3H9Z"/><circle cx="11.5" cy="11" r="3"/>',
  spark: '<path d="m12 2 2.7 7.3L22 12l-7.3 2.7L12 22l-2.7-7.3L2 12l7.3-2.7L12 2Z"/>',
  sprout: '<path d="M12 21v-9M12 16C5 17 3 12 3 7c6 0 10 3 9 9ZM12 12c0-6 4-9 9-9 0 6-3 9-9 9Z"/>',
  arrow: '<path d="M4 12h16m-6-6 6 6-6 6"/>',
  arrowUp: '<path d="M5 19 19 5M5 5h14v14"/>',
  search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
  menu: '<path d="M4 6h16M4 12h16M4 18h16"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
  calendar: '<rect x="3" y="5" width="18" height="16" rx="3"/><path d="M7 3v4m10-4v4M3 11h18M8 15h2m4 0h2"/>',
  layers: '<path d="m12 3 10 5-10 5L2 8l10-5Zm-9 9 9 5 9-5M3 16l9 5 9-5"/>',
  route: '<circle cx="5" cy="5" r="2"/><circle cx="19" cy="19" r="2"/><path d="M7 5h8a4 4 0 0 1 0 8H9a3 3 0 0 0 0 6h8"/>',
  code: '<path d="m8 6-6 6 6 6m8-12 6 6-6 6M14 3l-4 18"/>',
  target: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1"/>',
};
function icon(name, className = '') {
  return `<svg class="icon ${className}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${iconPaths[name] || iconPaths.spark}</svg>`;
}
document.querySelectorAll('[data-icon]').forEach(node => { node.innerHTML = icon(node.dataset.icon); });

function courseArtwork(index) {
  return index
    ? `<div class="pipeline-art" aria-hidden="true"><span class="pipeline-label">RAW DATA</span><div class="pipeline-nodes"><span>${icon('layers')}</span><i></i><span>${icon('code')}</span><i></i><span>${icon('chart')}</span></div><div class="pipeline-captions"><span>BRONZE</span><span>SILVER</span><span>GOLD</span></div></div>`
    : `<div class="dataframe-art" aria-hidden="true"><span class="art-window-dots"><i></i><i></i><i></i></span><div class="dataframe-code"><span class="code-purple">spark</span><span>.read</span><span class="code-green">.transform</span><span>(your_ideas)</span></div><div class="dataframe-grid"><i></i><i></i><i></i><i></i><i></i><i></i><i></i><i></i><i></i><i></i><i></i><i></i></div><span class="dataframe-badge">${icon('check')} DataFrame ready</span></div>`;
}

function heroArtwork() {
  return `<div class="hero-art" aria-hidden="true"><div class="hero-orbit"></div><div class="hero-orbit orbit-two"></div><div class="hero-code-float"><span class="terminal-dot"></span> your_next_step.py</div><div class="hero-cube"><div class="cube-layer layer-bottom"></div><div class="cube-layer layer-middle"></div><div class="cube-layer layer-top">${icon('spark')}</div></div><span class="hero-floating-tag tag-spark">${icon('check')} 准备就绪</span><span class="hero-floating-tag tag-code">${icon('code')} 让想法运行起来</span><span class="hero-art-label">SMALL STEPS. REAL PROGRESS.</span></div>`;
}

async function rpc(method, params = {}, keepalive = false) {
  const dependent=['saveDraft','getDraft','run','webSubmit','advance','goBack','webChat','webCoach','ackCoachMessage','runCoachProbe','getHint','getSolution','getLearningCoach','webLastExecution','recordLearningEvent'];
  const values={...params};
  if(dependent.includes(method)&&state.lesson?.contextToken&&!Object.hasOwn(values,'contextToken'))values.contextToken=state.lesson.contextToken;
  const requestSession=session;
  const response=await fetch('/api/rpc',{method:'POST',headers:{'Content-Type':'application/json','X-SparkTutor-Token':token,'X-SparkTutor-Session':requestSession},body:JSON.stringify({method,params:values}),keepalive});
  let data;try{data=await response.json();}catch{throw new Error('学习空间连接暂时中断，公网地址可能已失效。请确认最新地址后刷新重试。');}
  if(!data||typeof data!=='object'||Array.isArray(data))throw new Error('学习服务暂时无法连接，请稍后刷新重试。');
  if(response.status===401&&data.loginRequired){location.reload();throw new Error('登录已过期，请重新登录。');}
  if(data.sessionReset){if(session===requestSession){rotateSession(data.sessionId);queueMicrotask(()=>route());}throw new Error(data.error||'学习记录已清除，请重新开始。');}
  if(requestSession!==session)throw new Error('学习会话已更新，旧操作结果未应用。');
  if(!response.ok||data.error)throw new Error(data.error||'服务暂时不可用，请稍后重试。');return data.result;
}
function toast(message, error = false) {
  clearTimeout(toastTimer); const node = $('#toast'); node.textContent = message; node.className = 'toast' + (error ? ' error' : ''); node.hidden = false;
  toastTimer = setTimeout(() => { node.hidden = true; }, error ? 7000 : 3500);
}
function date(value, short = false) {
  const d = value ? new Date(value) : new Date();
  if (Number.isNaN(d.getTime())) return esc(value);
  return d.toLocaleString('zh-CN', {timeZone:'Asia/Shanghai', year:'numeric', month:'2-digit', day:'2-digit', ...(short ? {} : {hour:'2-digit', minute:'2-digit', hour12:false})});
}
function pct(score) { return score == null ? '证据不足' : `${Number(score.toFixed(1))}%`; }
function heading(title, subtitle, extra = '') { return `<div class="page-heading"><div><p class="eyebrow">YOUR SPARK JOURNEY</p><h1>${esc(title)}</h1><p>${esc(subtitle)}</p></div>${extra}</div>`; }
function empty(title, description, action = '<a class="btn primary" href="#courses">探索课程 <span>→</span></a>') { return `<div class="panel empty-state"><div class="empty-icon">◈</div><h2>${esc(title)}</h2><p>${esc(description)}</p>${action}</div>`; }
function loading() { return '<div class="loading-page"><span class="spinner"></span>正在加载…</div>'; }

function inlineMarkdown(text) {
  return esc(text).replace(/`([^`]+)`/g, '<code>$1</code>').replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>').replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
}
function markdown(text = '') {
  // Parse blocks before inline formatting, so Python snippets retain their exact text.
  const lines = String(text).replace(/\r\n/g, '\n').split('\n'); let html = '', para = [], list = null;
  const flush = () => { if (para.length) { html += `<p>${inlineMarkdown(para.join('\n'))}</p>`; para = []; } };
  const closeList = () => { if (list) { html += `</${list}>`; list = null; } };
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (/^\s*```/.test(line)) { flush(); closeList(); const code = []; while (++i < lines.length && !/^\s*```/.test(lines[i])) code.push(lines[i]); html += `<pre><code>${esc(code.join('\n'))}</code></pre>`; continue; }
    const title = /^(#{1,3})\s+(.+)$/.exec(line);
    if (title) { flush(); closeList(); html += `<h${title[1].length}>${inlineMarkdown(title[2])}</h${title[1].length}>`; continue; }
    const item = /^\s*(?:[-*]|\d+\.)\s+(.+)$/.exec(line);
    if (item) { flush(); const type = /^\s*\d/.test(line) ? 'ol' : 'ul'; if (list !== type) { closeList(); list = type; html += `<${type}>`; } html += `<li>${inlineMarkdown(item[1])}</li>`; continue; }
    closeList(); if (!line.trim()) flush(); else para.push(line);
  }
  flush(); closeList(); return html;
}

async function dashboard(courseId=state.scopeCourseId) { state.dashboard=await rpc('getLearningDashboard',{courseId}); return state.dashboard; }
function dimensions() { return state.dashboard?.diagnosis?.dimensions || []; }
function courseCard(course, index) {
  const progress = course.progress || {}; const done = progress.lessonsCompleted || 0; const started = progress.started;
  return `<article class="course-card"><div class="course-art ${index ? 'purple' : ''}"><span class="course-type">${index ? '02 / 项目实战' : '01 / 核心基础'}</span>${courseArtwork(index)}</div><div class="course-card-body"><div class="course-card-kicker">${index ? 'BUILD YOUR DATA PIPELINE' : 'YOUR FOUNDATION IN SPARK'}</div><h3>${esc(course.title)}</h3><p>${esc(course.description)}</p><div class="course-meta"><span>${icon('book')}${course.lessonCount} 个课节</span><span>${icon('clock')}${course.lessons.reduce((sum, lesson) => sum + (lesson.estimatedMinutes || 0), 0)} 分钟</span></div><div class="course-progress-label"><span>${started ? '正在积累的进步' : '等待你的第一步'}</span><span><b>${done}</b> / ${course.lessonCount} 课节</span></div><div class="progress-track" role="progressbar" aria-label="${esc(course.title)}完成进度" aria-valuenow="${Number(progress.completionPercent || 0)}" aria-valuemin="0" aria-valuemax="100"><span style="width:${Number(progress.completionPercent || 0)}%"></span></div><button class="btn" data-action="course-start" data-course="${esc(course.id)}">${started ? '继续这门课程' : '开始学习'} ${icon('arrow')}</button></div></article>`;
}
function renderHome() {
  const d = state.dashboard; const total = d.courses.reduce((n,c) => n + c.lessonCount, 0); const done = d.courses.reduce((n,c) => n + (c.progress?.lessonsCompleted || 0), 0); const resume = d.resume;
  const hasHistory = resume?.action === 'resume'; const diagnosis = d.diagnosis;
  $('#content').innerHTML = heading('每一步，都更懂 Spark。', '保持好奇，把今天学到的，变成明天做得到的。', `<span class="date-label">${icon('calendar')}${date(null, true)}<span class="date-divider"></span>今天也有新收获</span>`) +
    `<section class="hero"><div class="hero-copy"><span class="pill"><i></i>${hasHistory ? '接着上一次的探索' : '一段新的学习旅程，正在展开'}</span><h2>${hasHistory ? esc(resume.lessonTitle) : '从第一行 <em>PySpark</em> 开始。'}</h2><p>${hasHistory ? esc(resume.courseTitle) + '<br>进度和练习代码已经保存，随时接着往下探索。' : '理解分布式计算，动手处理真实数据。<br>让每一个「原来如此」，都来自你自己的实践。'}</p><div class="hero-actions"><button class="btn primary" data-action="resume">${hasHistory ? '继续学习' : '开启第一课'} ${icon('arrow')}</button><a class="btn subtle" href="#courses">浏览全部课程 ${icon('arrowUp')}</a></div></div>${heroArtwork()}</section>` +
    `<section class="stats" aria-label="学习概览">${[
      ['book','可探索课节',total,'课节'], ['check','已完成课节',done,'课节'], ['target','完成进度',`${Math.round(done / Math.max(total,1) * 100)}%`,''], ['route','学习路径',d.courses.length,'条路径']
    ].map(([symbol,label,value,unit]) => `<div class="stat"><span class="stat-icon">${icon(symbol)}</span><div><div class="stat-label">${label}</div><div class="stat-value">${value}<small>${unit}</small></div></div></div>`).join('')}</section>` +
    `<div class="home-grid"><section><div class="section-title"><div><span class="section-kicker">CURATED LEARNING PATHS</span><h2>找到你的下一步</h2><p>从核心概念出发，在实践中串起知识。</p></div><a class="text-link" href="#courses">全部课程 ${icon('arrowUp')}</a></div><div class="course-cards">${d.courses.map(courseCard).join('')}</div><div class="path-help"><span class="path-icon">${icon('route')}</span><div><strong>看懂，是开始。做出来，才是你的。</strong><p>阅读理解 <span>→</span> 动手编程 <span>→</span> 提交判题 <span>→</span> 回顾改进</p></div><a class="text-link" href="#history">学习足迹 ${icon('arrow')}</a></div></section><aside class="home-aside"><section class="panel diagnosis-mini"><div class="aside-heading"><span class="aside-icon">${icon('chart')}</span><h3>你的学习画像</h3><a class="icon-button" href="#diagnosis" aria-label="查看完整学习画像">${icon('arrowUp')}</a></div><p>每次练习，都让自己更清晰一点。</p><div class="dimension-overview">${dimensions().map((dim,i) => `<div class="dimension-summary"><div class="dimension-row"><span class="dimension-dot dot-${i}"></span>${esc(dim.name)}<strong>${pct(dim.score)}</strong></div><div class="dimension-meter ${dim.score == null ? 'no-evidence' : ''}"><span style="width:${dim.score == null ? 0 : Math.max(0,Math.min(100,dim.score))}%"></span></div></div>`).join('')}</div><p class="diagnosis-note">${diagnosis?.eventCount ? esc(diagnosis.diagnosis) : '画像随练习逐步建立。没有记录的维度保留「证据不足」，每一次进步都有迹可循。'}</p><a class="text-link" href="#diagnosis">了解我的学习状态 ${icon('arrow')}</a></section><section class="tool-card"><span class="tool-icon">${icon('spark')}</span><span class="tool-kicker">A LITTLE HELP GOES A LONG WAY</span><h3>卡住了？<br>换个角度，就有新思路。</h3><p>让 AI 导师陪你拆解问题，<br>在恰到好处的提示中找到答案。</p><a class="text-link" href="#settings">认识你的 AI 导师 ${icon('arrowUp')}</a></section></aside></div>`;
  const primary=$('.home-grid > section');
  primary.classList.add('home-primary');
  const knowledge=d.knowledgeComponents||diagnosis?.knowledgeComponents||{};
  if(Object.values(knowledge).some(item=>item.evaluatedTasks>0)) primary.insertAdjacentHTML('beforeend',features.knowledgeSection());
  const help=$('.home-aside > .tool-card');
  if(help){help.classList.add('home-help-card');primary.append(help);}
  const aside=$('.home-aside');
  aside.insertAdjacentHTML('afterbegin','<section class="panel checkin-card" id="learning-checkin" aria-label="学习签到"></section>');
  if (checkinPanel) checkinPanel.mount($('#learning-checkin'));
  else $('#learning-checkin').innerHTML = `<div class="aside-heading"><span class="aside-icon">${icon('calendar')}</span><h3>学习签到</h3></div><p class="disclaimer">签到功能暂不可用，请管理员更新服务后再试。</p>`;
}
function renderCourses() {
  $('#content').innerHTML = heading('找到你的学习路径', '19 个课节，从 Spark 核心概念走向完整数据管道。') + `<div class="catalog-toolbar"><div class="chips"><button class="chip ${!state.courseFilter ? 'active' : ''}" data-action="course-filter" data-course="">全部课程</button>${state.dashboard.courses.map(c => `<button class="chip ${state.courseFilter === c.id ? 'active' : ''}" data-action="course-filter" data-course="${esc(c.id)}">${esc(c.title)}</button>`).join('')}</div><input class="search" id="course-search" type="search" placeholder="搜索课节…" aria-label="搜索课节" value="${esc(state.query)}"></div><div id="catalog-results"></div>`;
  const resume = state.dashboard.resume;
  $('.catalog-toolbar').insertAdjacentHTML('beforebegin',`<section class="panel catalog-resume"><div><strong>${resume ? '接着上次的进度学习' : '开始你的第一课'}</strong><p>${resume ? `${esc(resume.courseTitle)} · ${esc(resume.lessonTitle)}` : '在独立学习标签页阅读、做题与编程，随时切回查看其他页面。'}</p></div><button class="btn primary" data-action="resume">${resume ? '继续学习' : '开始学习'} ${icon('arrowUp')}</button></section>`);
  const depthSelect = document.createElement('select'); depthSelect.id = 'depth-select'; depthSelect.className = 'search'; depthSelect.setAttribute('aria-label','练习难度');
  depthSelect.innerHTML = [['beginner','入门 · 详细引导'],['intermediate','进阶 · 适量引导'],['advanced','挑战 · 独立完成']].map(([value,label])=>`<option value="${value}" ${state.depth === value ? 'selected' : ''}>${label}</option>`).join('');
  $('.catalog-toolbar').insertBefore(depthSelect,$('#course-search'));
  renderCatalogResults();
  if (state.focusSearch) { $('#course-search').focus(); state.focusSearch = false; }
}
function renderCatalogResults() {
  let count = 0;
  $('#catalog-results').innerHTML = state.dashboard.courses.filter(c => !state.courseFilter || c.id === state.courseFilter).map(course => {
    const lessons = course.lessons.filter(l => l.title.toLowerCase().includes(state.query.toLowerCase())); count += lessons.length;
    if (!lessons.length) return '';
    return `<section class="course-section"><div class="course-section-header"><div><h2>${esc(course.title)}</h2><p>${esc(course.description)}</p></div><span class="badge">${course.lessonCount} 个课节</span></div><div class="lesson-list">${lessons.map(l => `<button class="lesson-item ${esc(l.status)}" data-action="load" data-course="${esc(course.id)}" data-index="${l.index}" ${l.available === false ? 'disabled' : ''}><span class="lesson-number">${l.status === 'completed' ? '✓' : String(l.index + 1).padStart(2,'0')}</span><div><h3>${esc(l.title)}</h3><p>${l.estimatedMinutes || '—'} 分钟 <span>·</span> ${esc(statusNames[l.status] || l.status)}${l.status === 'in_progress' ? ` · ${l.currentStep + 1}/${l.totalSteps} 步` : ''}</p></div><span class="lesson-chevron">↗</span></button>`).join('')}</div></section>`;
  }).join('');
  if (!count) $('#catalog-results').innerHTML = empty('没有找到相关课节', '试试其他关键词，或查看全部课程。', '');
}

function dimensionCard(dim) {
  const metrics = dim.metrics || {};
  const calculation = {
    knowledge: `最近通过题目 ${metrics.latestPassedTasks || 0} ÷ 有效评估题目 ${metrics.evaluatedTasks || 0}`,
    debugging: `确认修复过程 ${metrics.confirmedRepairs || 0} ÷ 可观察失败过程 ${metrics.failureEpisodes || 0}`,
    hint_dependency: `首次评估前使用提示题目 ${metrics.hintedBeforeFirstAssessment || 0} ÷ 有效评估题目 ${metrics.evaluatedTasks || 0}`,
    transfer: `新情境首次通过题目 ${metrics.firstPassedTasks || 0} ÷ 合格迁移题目 ${metrics.eligibleTransferTasks || 0}`,
  }[dim.key];
  const outcomes = {passed:'通过',failed:'未通过',repaired:'已确认修复',unresolved:'尚未确认修复',hinted:'使用过提示',not_hinted:'未使用提示'};
  return `<article class="panel dimension-card"><h3>${esc(dim.name)}</h3><div class="score">${dim.score == null ? '—' : Number(dim.score.toFixed(1)) + '<small>%</small>'}</div><span class="badge ${dim.score == null ? 'purple' : ''}">${dim.score == null ? '证据不足' : `${dim.sampleSize ?? dim.evidenceCount} 个样本`}</span><p>${esc(dim.summary)}</p><details><summary>查看计算依据与证据</summary><div class="evidence-list"><p>${esc(calculation || '')}${dim.score == null ? '；当前样本不足，暂不计算比例。' : ' × 100%'}</p>${(dim.evidence?.items || []).map(item => `<p>${esc(item.title || item.taskId)} · ${esc(outcomes[item.outcome] || (item.passed ? '通过' : '未通过'))}</p>`).join('')}</div></details></article>`;
}
function renderDiagnosis() {
  const diagnosis = state.dashboard.diagnosis;
  $('#content').innerHTML = heading('让每次练习，都留下线索', '基于实际学习记录的四维观察，帮助你选择下一步练习。', '<button class="btn" data-action="refresh">↻ 刷新画像</button>') +
    `<div class="notice">${esc(diagnosis?.diagnosis || '开始练习后，画像将随有效评估逐步建立。')}</div><div class="dimension-grid">${dimensions().map(dimensionCard).join('')}</div>` +
    `<section class="panel"><div class="section-title"><h2>下一道，练得更有方向</h2><span class="badge">推荐练习</span></div>${diagnosis?.recommendedExercise ? `<h3>${esc(diagnosis.recommendedExercise.title)}</h3><p class="disclaimer">${esc(diagnosis.recommendedExercise.reason)}</p><button class="btn primary" data-action="recommended">开始推荐练习 →</button>` : `<p class="disclaimer">${esc(diagnosis?.recommendationReason || '完成课程练习后，这里会给出适合当前记录的建议。')}</p><a class="btn" href="#courses">浏览课程</a>`}</section><p class="disclaimer">${esc(diagnosis?.disclaimer || '')}</p>`;
  $('#content').insertAdjacentHTML('afterbegin',features.scopeControls()); $('#content').insertAdjacentHTML('beforeend',features.knowledgeSection());
}

function draftKey() { return `${state.storagePrefix}draft.${state.courseId}.${state.lesson?.lessonId}.${state.depth}.${state.lesson?.step?.id}`; }
function currentCode() { return window.activeSparkTutorEditor?.getValue() ?? $('#code-editor')?.value ?? state.lastCode ?? ''; }
function currentAnswer() { return state.lesson?.step?.cls === 'mult_question' ? state.choice : currentCode(); }
function rememberDraft() { if(!state.lesson?.step?.id)return true;try{localStorage.setItem(draftKey(),currentCode());return true;}catch{return false;} }
async function flushEdits() {
 const work=async()=>{
  clearTimeout(state.draftTimer);const edit=state.edit;state.edit=null;
  if(edit){try{await rpc('recordLearningEvent',edit);}catch(error){if(!state.edit)state.edit=edit;throw error;}}
  if(!state.draftDirty||!state.lesson?.step?.id)return;
  const identity=draftIdentity(),code=currentCode();const result=await rpc('saveDraft',{...identity,code});
  if(state.lesson?.contextToken===identity.contextToken&&state.lesson?.step?.id===identity.stepId){
   if(result.saved){state.lesson.draftRevision=result.draftRevision??result.revision;state.draftDirty=currentCode()!==code;}
   if($('#save-state'))$('#save-state').textContent=result.saved?'草稿已保存到服务端':'草稿仅保存在浏览器，请重试';
  }
  if(!result.saved)throw new Error(result.message||'服务端未保存草稿，浏览器副本已保留。');
 };
 draftFlush=draftFlush.catch(()=>{}).then(work);return draftFlush;
}
function draftIdentity(){return{courseId:state.courseId,lessonId:state.lesson?.lessonId,stepId:state.lesson?.step?.id,depth:state.depth,contextToken:state.lesson?.contextToken,draftRevision:state.lesson?.draftRevision};}
function saveNavigation(){if(state.lesson)sessionStorage.setItem('sparktutor.lastLesson',JSON.stringify({courseId:state.courseId,lessonIdx:state.lessonIdx,depth:state.depth,practiceMode:Boolean(state.lesson.practiceMode),stepId:state.lesson.step?.id}));}
function openStudyTab(request = null) {
  if (isStudyTab && !request) { location.hash='lesson'; return; }
  const tab = window.open('', `sparktutor-study-${accountUser||'local'}`);
  if (!tab) { toast('浏览器拦截了学习标签页，请允许此网站打开新标签页后重试。',true); return; }
  const url = new URL(location.pathname,location.origin);
  url.searchParams.set('study','1');
  url.hash = request ? `study/${encodeURIComponent(JSON.stringify(request))}` : 'lesson';
  if (request || tab.location.href === 'about:blank') tab.location.href=url.href;
  tab.focus();
}
async function loadLesson(courseId, index, depth = state.depth) {
  await flushEdits();
  const result = await rpc('loadLesson', {courseId, lessonIdx: Number(index), depth});
  state.courseId = courseId; state.lessonIdx = Number(index); state.depth = depth;
  acceptLesson(result,true); saveNavigation();
  if (location.hash === '#lesson') renderLesson(); else location.hash = 'lesson';
}
function acceptLesson(result, fresh = false) {
  state.lesson = fresh ? result : {...state.lesson, ...result};
  state.choice = ''; state.passed = false; state.coachMessage = null; state.coach = null; state.helpTab = 'tutor';
  if (fresh) state.chats = [];
  let draft = null; try { draft = localStorage.getItem(draftKey()); } catch {}
  state.lastCode=result.draftFound?(result.restoredCode??''):(draft??(result.restoredCode||result.starterCode||''));
  state.edit=null;state.draftDirty=!result.draftFound&&draft!==null&&['script','cmd_question'].includes(state.lesson?.step?.cls);saveNavigation();
}
function renderLesson() {
  destroyEditor();
  const lesson = state.lesson;
  if (!lesson) { $('#content').innerHTML = empty('选择一课，开始探索', '从课程目录进入学习，或回到首页继续上次的进度。'); return; }
  const step = lesson.step; const isQuestion = ['script','cmd_question','mult_question'].includes(step.cls); const isCode = ['script','cmd_question'].includes(step.cls);
  $('#content').innerHTML = `<div class="lesson-header"><div><p class="eyebrow">${lesson.practiceMode ? 'FOCUSED PRACTICE / 单题练习' : 'LEARNING BY DOING'}</p><h1>${esc(lesson.lessonTitle)}</h1></div><div class="inline-actions">${features.difficultySelect()}<button class="btn small" data-action="feature-reset-lesson">重置本课</button><a class="btn" href="#home">学习首页</a><a class="btn" href="#courses">课程目录</a></div></div><div class="lesson-layout"><section class="panel lesson-reading"><div class="lesson-step-meta"><span>${{text:'阅读与理解',mult_question:'概念自测',cmd_question:'动手练习',script:'综合编程'}[step.cls] || '学习步骤'}</span><span>步骤 ${lesson.currentIndex + 1} / ${lesson.totalSteps}</span></div><div class="progress-track"><span style="width:${(lesson.currentIndex + 1) / lesson.totalSteps * 100}%"></span></div><div class="markdown">${markdown(step.output)}</div>${step.cls === 'mult_question' ? `<div class="choice-list">${choices(step).map((choice, index) => `<button class="choice ${state.choice === choice ? 'selected' : ''}" data-action="choice" data-choice="${esc(choice)}" aria-pressed="${state.choice === choice}"><span>${'ABCD'[index] || index + 1}</span>${esc(choice)}</button>`).join('')}</div>` : ''}<div id="feedback" aria-live="polite"></div><div id="hint-area" aria-live="polite"></div><div class="lesson-controls"><button class="btn small" data-action="back" ${lesson.currentIndex === 0 || lesson.practiceMode ? 'disabled' : ''}>← 上一步</button><div>${isQuestion ? '<button class="btn small" data-action="hint">✧ 提示</button>' : ''}${step.cls === 'mult_question' ? '<button class="btn small primary" data-action="submit">提交答案</button>' : ''}<button class="btn small primary" id="next-button" data-action="next" ${isQuestion && !state.passed ? 'disabled' : ''}>${lesson.practiceMode ? '完成练习' : '下一步'} →</button></div></div></section><div class="lesson-tools">${isCode ? `<section class="editor-panel"><div class="editor-toolbar"><span>● &nbsp; ${step.cls === 'script' ? esc(step.starterCode || 'exercise.py') : 'exercise.py'}</span><div class="editor-tool-buttons"><button class="btn small" data-action="feature-editor-search">搜索 / 替换</button><span>Python · 基础补全</span></div></div><div class="editor-wrap"><div class="line-numbers" id="line-numbers" aria-hidden="true"></div><textarea id="code-editor" class="code-editor" spellcheck="false" autocapitalize="off" autocomplete="off" aria-label="Python 代码编辑器">${esc(state.lastCode)}</textarea></div><div class="editor-footer"><span id="save-state">自动保存草稿</span><span>UTF-8 &nbsp; · &nbsp; 4 空格缩进</span></div></section><div class="editor-actions"><button class="btn small" data-action="feature-editor-download">下载代码</button><button class="btn small" data-action="solution">查看参考答案</button><button class="btn small" data-action="run">▷ 运行代码</button><button class="btn small primary" data-action="submit">提交判题 →</button></div><section class="panel output-panel"><div class="output-heading"><span>运行输出</span><span class="muted" id="execution-mode">等待运行</span></div><pre id="run-output" role="log">运行代码后，结果会显示在这里。</pre></section>` : ''}<section class="panel help-panel"><div class="help-tabs"><button class="active" data-action="help-tab" data-tab="tutor">✦ 导师问答</button><button data-action="help-tab" data-tab="coach">调试教练</button></div><div id="help-content"></div></section></div></div>`;
  renderHelp(); if(isCode){updateLines();if(window.SparkTutorEditor)window.activeSparkTutorEditor=window.SparkTutorEditor.create($('#code-editor'));}
}
function choices(step) {
  const items = (Array.isArray(step.answerChoices) ? step.answerChoices : (step.answerChoices || '').split(';')).filter(Boolean);
  // Stable shuffle by task ID; never reveal which choice is correct in presentation.
  let seed = [...`${state.courseId}:${state.lesson.lessonId}:${step.id}`].reduce((n,c) => (Math.imul(n,31) + c.charCodeAt(0)) >>> 0, 7);
  for (let i = items.length - 1; i > 0; i--) { seed = (Math.imul(seed,1664525) + 1013904223) >>> 0; const j = seed % (i+1); [items[i],items[j]] = [items[j],items[i]]; }
  return items;
}
function updateLines() { const editor = $('#code-editor'); if (!editor) return; $('#line-numbers').textContent = Array.from({length:editor.value.split('\n').length},(_,i) => i+1).join('\n'); $('#line-numbers').scrollTop = editor.scrollTop; }
function showFeedback(result) {
  const unfinished = !result.passed && (['infrastructure','dry_run','unverified'].includes(result.failureKind) || result.assessmentSource === 'ai_unavailable' || (result.assessmentSource === 'unverified' && result.assessmentEligible === false));
  const title = result.passed ? '✓ 通过，做得好！' : unfinished ? '尚未完成判题' : result.assessmentSource === 'ai_review' ? 'AI 评审建议修改' : '再检查一下，你正在接近答案。';
  $('#feedback').innerHTML = `<div class="feedback ${result.passed ? 'success' : unfinished ? 'pending' : ''}"><strong>${title}</strong>${(result.feedback || []).map(f => `<p>${f.line ? `第 ${f.line} 行 · ` : ''}${esc(f.message)}${f.suggestion ? `<br>${esc(f.suggestion)}` : ''}</p>`).join('')}${result.encouragement ? `<p>${esc(result.encouragement)}</p>` : ''}${unfinished ? '<p><a href="#settings">检查环境与 AI 设置 →</a></p>' : ''}</div>`;
  state.passed=result.passed;$('#next-button').disabled=!result.passed;window.activeSparkTutorEditor?.showDiagnostics(unfinished ? [] : result.feedback||[]);
}
function showExecution(result, submission = false) {
  const output = $('#run-output'); if (!output) return;
  const dryRun = result.mode === 'dry_run';
  const explanation = dryRun ? '仅检查 Python 语法，未执行代码或课程测试，不代表答案正确。\n真实运行请在“环境与 AI 设置”中选择可用的 Spark 环境；运行代码不需要模型密钥。' : '';
  output.textContent = [explanation,result.stdout,result.stderr].filter(Boolean).join('\n') || (submission ? '本地检查已完成，请查看左侧反馈。' : '程序执行结束，没有输出。');
  const mode = {local:'本机 Spark',lakehouse:'Docker Spark',docker:'Docker Spark',databricks:'Databricks',dry_run:'仅语法检查 · 未执行代码'}[result.mode] || result.mode || '本地检查';
  $('#execution-mode').textContent = `${mode}${Number.isInteger(result.exitCode) ? ` · exit ${result.exitCode}` : ''}`;
}
async function moveStep(method) {
  rememberDraft(); await flushEdits();
  const result = await rpc(method, {code:currentAnswer()});
  if (result.atStart) return;
  if (result.finished) {
    $('#content').innerHTML = `<section class="panel empty-state"><div class="finish-art">✳</div><p class="eyebrow">ONE MORE STEP FORWARD</p><h2>${result.practiceMode ? '这道练习，完成了。' : '这一课，学完了。'}</h2><p>将刚刚的收获带进下一次实践。<br>你可以回看学习画像，找到下一步的方向。</p><div class="inline-actions" style="justify-content:center"><a class="btn primary" href="#courses">继续探索课程 →</a><a class="btn" href="#diagnosis">查看学习画像</a></div></section>`;
    state.lesson = null; await dashboard(); return;
  }
  acceptLesson(result);saveNavigation();renderLesson(); window.scrollTo({top:0,behavior:'smooth'});
}
function renderHelp() {
  const host = $('#help-content'); if (!host) return;
  document.querySelectorAll('[data-action="help-tab"]').forEach(b => b.classList.toggle('active', b.dataset.tab === state.helpTab));
  if (state.helpTab === 'tutor') {
    host.innerHTML = `<div class="chat-messages" id="chat-messages">${state.chats.length ? state.chats.map(chat => `<div class="chat-bubble ${chat.role === 'user' ? 'user' : ''}"><div class="markdown">${markdown(chat.text)}</div></div>`).join('') : '<p class="disclaimer no-margin">从一个问题开始。导师会结合当前题面与代码，帮你理解概念和下一步思路。<a href="#settings"> 配置模型 ↗</a></p>'}</div><form class="chat-form" id="chat-form"><label class="sr-only" for="chat-question">向导师提问</label><input id="chat-question" placeholder="有什么想不明白的？" required maxlength="10000"><button class="btn primary small" type="submit">发送 ↑</button></form>`;
    const messages = $('#chat-messages'); messages.scrollTop = messages.scrollHeight;
  } else {
    const c = state.coach;
    host.innerHTML = !c ? '<div class="busy-indicator"><span class="spinner"></span>正在读取练习上下文…</div>' : `<p class="disclaimer no-margin">${esc(c.reason || '教练将结合当前代码与可追溯记录，逐层提供思考方向。')}</p>${c.available ? `<div class="inline-actions">${[1,2,3].map(level => `<button class="btn small" data-action="coach-request" data-level="${level}" ${level > c.maxLevel ? 'disabled' : ''}>${['概念追问','检查方法','局部示例'][level-1]}</button>`).join('')}</div>` : ''}${!c.available && c.exerciseOptions?.length ? `<div class="inline-actions">${c.exerciseOptions.map((task,i) => `<button class="btn small" data-action="coach-open" data-index="${i}">${esc(task.title)} →</button>`).join('')}</div>` : ''}<div id="coach-message"></div><p class="disclaimer"><a href="#settings">配置调试教练模型 ↗</a></p>`;
  }
  if(state.helpTab==='coach'&&state.coach)host.insertAdjacentHTML('beforeend',features.coachDetails(state.coach));
}

async function renderHistory() {
  const version=state.routeVersion;
  const data = await rpc('getLearningHistory', {offset:state.historyOffset, limit:15, window:state.historyWindow, courseId:state.historyCourse});
  if(version!==state.routeVersion||location.hash!=='#history')return;
  $('#content').innerHTML = heading('回看走过的每一步', '运行、提交、求助与修复，都可以成为理解学习过程的线索。') + `<div class="section-title"><div class="history-filters"><select id="history-course" aria-label="筛选课程"><option value="">全部课程</option>${data.courses.map(c => `<option value="${esc(c.id)}" ${state.historyCourse === c.id ? 'selected' : ''}>${esc(c.title)}</option>`).join('')}</select><select id="history-window" aria-label="时间范围">${[['all','全部历史'],['7d','近 7 天'],['30d','近 30 天']].map(([value,label]) => `<option value="${value}" ${state.historyWindow === value ? 'selected' : ''}>${label}</option>`).join('')}</select></div><span class="muted">${data.total} 道题目</span></div>` + (data.tasks.length ? `<section class="panel"><div class="history-wrap"><table class="history-table"><thead><tr><th>练习题目</th><th>最近评估</th><th>运行 / 提交</th><th>求助</th><th>最近活动</th></tr></thead><tbody>${data.tasks.map((task,i) => `<tr><td><button class="text-link" data-action="task-history" data-index="${i}">${esc(task.title)}</button><small>${esc(task.lessonTitle)}</small></td><td>${task.latestAssessment ? (task.latestAssessment.passed ? '<span class="badge">已通过</span>' : '<span class="badge purple">待改进</span>') : '暂无有效评估'}</td><td>${task.counts.runs} / ${task.counts.submissions}</td><td>${task.counts.hints} 次提示</td><td>${date(task.lastActivityAt)}</td></tr>`).join('')}</tbody></table></div><div class="pagination"><button class="btn small" data-action="history-prev" ${!state.historyOffset ? 'disabled' : ''}>← 上一页</button><span>${Math.floor(state.historyOffset / 15) + 1}</span><button class="btn small" data-action="history-next" ${!data.hasMore ? 'disabled' : ''}>下一页 →</button></div></section><div id="history-detail" class="history-detail"></div>` : empty('学习足迹，等待你的第一笔', '完成一次练习、提交或求助后，就能在这里回顾自己的学习过程。')) + `<p class="disclaimer">${esc(data.disclaimer)}</p>`;
  state.historyData=data;$('#content').insertAdjacentHTML('beforeend',features.historySupplement());
}
async function taskHistory(index, offset = 0) {
  const task = state.historyData.tasks[index]; const data = await rpc('getTaskHistory', {...taskRef(task), offset, limit:20, snapshotEventId:state.historyData.snapshotEventId}); state.taskHistory = data; state.taskHistoryIndex = index;
  $('#history-detail').innerHTML = `<section class="panel"><div class="section-title"><h2>${esc(task.title)}</h2><span class="muted">${data.total} 条记录</span></div><div class="event-list">${data.events.map((event,i) => `<div class="event-item"><time>${date(event.timestamp)}</time><strong>${esc(eventNames[event.eventType] || event.eventType)}</strong>${event.passed != null ? ` · ${event.passed ? '通过' : '未通过'}` : ''}<p>${esc(event.reason || '')}</p>${event.reviewable ? `<button class="text-link" data-action="review" data-index="${i}">查看此刻前后画像 →</button>` : ''}</div>`).join('')}</div><div class="pagination"><button class="btn small" data-action="events-prev" ${!data.offset ? 'disabled' : ''}>← 上一页</button><button class="btn small" data-action="events-next" ${!data.hasMore ? 'disabled' : ''}>下一页 →</button></div><div id="review-result"></div></section>`;
  if (offset === 0) $('#history-detail').scrollIntoView({behavior:'smooth',block:'start'});
}
function taskRef(task) { return {courseId:task.courseId, lessonId:task.lessonId, taskId:task.taskId}; }

function renderDemo() {
  $('#content').innerHTML = `<a class="text-link" href="#settings">← 返回设置</a>` + heading('学习诊断示例', '让系统演示一次做题过程，了解学习画像、推荐和复盘如何生成。') + `<div class="notice warning">这是设置中的辅助功能，使用预设案例，与自己的学习记录隔离，不计入我的成绩。适合了解系统、团队展示与功能验收，不代表真实学生成绩或教学效果。</div><section class="panel"><div class="section-title"><h2>选择一条演示轨迹</h2><span class="badge">可重复运行</span></div><div class="settings-grid"><div class="field"><label for="demo-scenario">学习情境</label><select id="demo-scenario"><option value="transfer_retry">新情境首次失败，修复后通过</option><option value="transfer_first_pass">新情境首次通过</option></select></div><div class="field"><label for="demo-mode">运行方式</label><select id="demo-mode"><option value="recorded">合成轨迹 · 无需 Spark</option><option value="spark">真实 Spark · 运行课程测试</option></select></div></div><button class="btn primary" data-action="run-demo">▷ 运行演示</button><div id="demo-result" aria-live="polite"></div></section>`;
}
async function runDemo() {
  $('#demo-result').innerHTML = '<div class="busy-indicator"><span class="spinner"></span>正在运行独立演示，请稍候…</div>';
  const data = await rpc('runDemoScenario',{scenarioId:$('#demo-scenario').value,mode:$('#demo-mode').value,operationId:state.operationId}); state.demo = data;
  $('#demo-result').innerHTML = `<div class="notice" style="margin-top:20px">${esc(data.provenanceLabel)} · ${data.checks.filter(c=>c.passed).length}/${data.checks.length} 项验证通过</div><div class="demo-stages">${data.stages.map((stage,i)=>features.demoStage(stage,i)).join('')}</div><p class="disclaimer">${esc(data.disclaimer)}</p><button class="btn" data-action="export-demo">下载演示报告 ↓</button>`;
}
async function renderSettings(){if(!settingsPanel)throw new Error('设置页面未能加载，请刷新网站。');await settingsPanel.render(document.querySelector('#content'));}
async function route(){
 const version=++state.routeVersion;
 try{
  if(state.busy){toast('当前操作正在进行，请等待完成。');return;}
  const localCopy=rememberDraft();
  try{await flushEdits();}catch(error){if(!localCopy){toast('服务端与浏览器均未能保存，当前代码仍留在编辑器中。请先下载代码再离开。',true);return;}toast(`${error.message} 浏览器副本已保留，请回到练习页重试保存或下载当前代码。`,true);}
  const rawName=location.hash.slice(1)||'home';
  const studyRequest=rawName.startsWith('study/')?JSON.parse(decodeURIComponent(rawName.slice(6))):null;
  const name=window.SparkTutorAccount?.user.mustChange?'account':(studyRequest?'lesson':rawName);features.invalidate();checkinPanel?.destroy();destroyEditor();
  $('.sidebar').classList.remove('open');$('#menu-toggle').setAttribute('aria-expanded','false');
  document.querySelectorAll('[data-nav]').forEach(link=>link.classList.toggle('active',link.dataset.nav===name||(name==='demo'&&link.dataset.nav==='settings')));
  $('#breadcrumb').textContent=name==='demo'?'设置 / 学习诊断示例':names[name]||'学习工作台';document.title=`${names[name]||'学习工作台'} · SparkTutor`;
  $('#content').innerHTML=loading();window.scrollTo(0,0);
  if(['home','courses','diagnosis'].includes(name))await dashboard(name==='home'?'':state.scopeCourseId);if(version!==state.routeVersion)return;
  switch(name){
   case 'home':renderHome();break;case 'courses':renderCourses();break;case 'diagnosis':renderDiagnosis();break;
   case 'lesson':{
    if(studyRequest){
     let result;
     if(studyRequest.recommended){result=await rpc('openRecommendedExercise',studyRequest.recommended);}
     else {result=await rpc('loadLesson',{courseId:studyRequest.courseId,lessonIdx:Number(studyRequest.lessonIdx),depth:studyRequest.depth||state.depth});}
     if(version!==state.routeVersion)return;
     state.courseId=result.courseId||studyRequest.courseId;state.lessonIdx=result.lessonIdx??Number(studyRequest.lessonIdx);state.depth=result.depth||studyRequest.depth||state.depth;
     acceptLesson(result,true);history.replaceState(null,'','#lesson');
    }
    if(!state.lesson){let saved={};try{saved=JSON.parse(sessionStorage.getItem('sparktutor.lastLesson')||'{}');}catch{}
     const restored=await rpc('resumeWebLesson',saved);if(version!==state.routeVersion)return;
     if(restored.lesson){state.courseId=restored.courseId;state.lessonIdx=restored.lessonIdx;state.depth=restored.depth;acceptLesson(restored.lesson,true);}
     else if(saved.courseId&&!saved.practiceMode)await loadLesson(saved.courseId,saved.lessonIdx??saved.index,saved.depth);
     else {
      await dashboard();if(version!==state.routeVersion)return;
      const resume=state.dashboard.resume,first=state.dashboard.courses[0];
      if(resume)await loadLesson(resume.courseId,resume.lessonIdx,resume.depth);
      else if(first)await loadLesson(first.id,0);
     }
    }renderLesson();break;
   }
   case 'history':await renderHistory();break;case 'settings':await renderSettings();break;case 'data':location.hash='home';break;case 'events':await features.renderEvents();break;case 'demo':renderDemo();break;case 'account':window.SparkTutorAccount.renderAccount();break;case 'admin':await window.SparkTutorAccount.renderAdmin();break;default:location.hash='home';
  }
 }catch(error){if(version===state.routeVersion)$('#content').innerHTML=`<div class="panel route-error"><h2>这一步暂时没有完成</h2><p>${esc(error.message)}</p><button class="btn" data-action="refresh">重新加载</button></div>`;}
}
async function action(button) {
  const type = button.dataset.action;
  if (state.busy) return;
  if (type === 'study-tab') { openStudyTab(); return; }
  if (!isStudyTab && ['resume','load','course-start','recommended'].includes(type)) {
    if (type === 'resume' && state.dashboard.resume) {
      // Focusing the existing study window preserves its unsent answer and cursor.
      openStudyTab(); return;
    }
    let request;
    if (type === 'recommended') { const r=state.dashboard.diagnosis.recommendedExercise; request={recommended:{courseId:r.courseId,lessonId:r.lessonId,stepId:r.stepId,scopeCourseId:state.scopeCourseId}}; }
    else if (type === 'load') request={courseId:button.dataset.course,lessonIdx:Number(button.dataset.index),depth:state.depth};
    else {const c=state.dashboard.courses.find(c=>c.id===button.dataset.course)||state.dashboard.courses[0];const l=c.lessons.find(l=>l.status==='in_progress')||c.lessons.find(l=>l.status!=='completed')||c.lessons[0];request={courseId:c.id,lessonIdx:l.index,depth:state.depthExplicit?state.depth:(l.depth||state.depth)};}
    openStudyTab(request); return;
  }
  if (type === 'open-search') {
    if (location.hash === '#courses') $('#course-search')?.focus();
    else { state.focusSearch = true; location.hash = 'courses'; }
    return;
  }
  if (type === 'choice') {
    state.choice = button.dataset.choice; state.passed = false; $('#next-button').disabled = true; document.querySelectorAll('.choice').forEach(node => { const chosen = node === button; node.classList.toggle('selected',chosen); node.setAttribute('aria-pressed', String(chosen)); }); return;
  }
  if (type === 'course-filter') { state.courseFilter = button.dataset.course; renderCourses(); return; }
  if (type === 'refresh') { await route(); return; }
  state.busy = true; button.disabled = true; const oldMarkup = button.innerHTML; button.textContent = '处理中…';
  const lockedEditor = $('#code-editor'); if (lockedEditor) lockedEditor.readOnly = true;
  try {
    if(settingsPanel && await settingsPanel.handleAction(button))return;
    if(await features.handleAction(button))return;
    if(['run','submit','run-demo'].includes(type))await beginOperation();
    switch (type) {
      case 'resume': { const r = state.dashboard.resume; if (r) await loadLesson(r.courseId, r.lessonIdx,r.depth); else await loadLesson(state.dashboard.courses[0].id,0); break; }
      case 'load': await loadLesson(button.dataset.course,button.dataset.index); break;
      case 'course-start': { const c = state.dashboard.courses.find(c=>c.id === button.dataset.course); const l = c.lessons.find(l=>l.status === 'in_progress') || c.lessons.find(l=>l.status !== 'completed') || c.lessons[0]; await loadLesson(c.id,l.index,state.depthExplicit?state.depth:l.depth); break; }
      case 'recommended': { await flushEdits(); const r = state.dashboard.diagnosis.recommendedExercise; const result = await rpc('openRecommendedExercise',{courseId:r.courseId,lessonId:r.lessonId,stepId:r.stepId,scopeCourseId:state.scopeCourseId}); state.courseId = result.courseId; state.lessonIdx = result.lessonIdx; state.depth = result.depth; acceptLesson(result,true); location.hash = 'lesson'; break; }
      case 'next': await moveStep('advance'); break;
      case 'back': await moveStep('goBack'); break;
      case 'submit': {
        if (state.lesson.step.cls === 'mult_question' && !state.choice) throw new Error('先选择一个答案，再提交。');
        rememberDraft(); await flushEdits(); if ($('#run-output')) $('#run-output').textContent = '正在提交判题…';
        const result = await rpc('webSubmit',{code:currentAnswer(),operationId:state.operationId}); showFeedback(result);
        if ($('#run-output')) showExecution(await rpc('webLastExecution'), true);
        break;
      }
      case 'run': { rememberDraft(); await flushEdits(); $('#run-output').textContent = '正在运行代码…'; const result = await rpc('run',{code:currentCode(),operationId:state.operationId}); showExecution(result); break; }
      case 'hint': { const r = await rpc('getHint'); $('#hint-area').innerHTML = `<div class="feedback"><strong>✧ 试着这样想</strong><div class="markdown">${markdown(r.hint)}</div></div>`; break; }
      case 'solution': { await flushEdits(); const r = await rpc('getSolution'); $('#hint-area').innerHTML = r.solution ? `<details class="feedback" open><summary>你的代码与参考答案</summary>${features.comparison(currentCode(),r.solution)}</details>` : '<div class="feedback">当前步骤没有独立参考答案文件。</div>'; break; }
      case 'help-tab': state.helpTab = button.dataset.tab; renderHelp(); if (state.helpTab === 'coach') { await flushEdits(); state.coach = await rpc('getLearningCoach'); renderHelp(); } break;
      case 'coach-open': { await flushEdits(); const task = state.coach.exerciseOptions[Number(button.dataset.index)]; const result = await rpc('openCoachExercise',{courseId:task.courseId,lessonId:task.lessonId,stepId:task.stepId}); state.courseId = result.courseId; state.lessonIdx = result.lessonIdx; state.depth = result.depth; acceptLesson(result,true); renderLesson(); break; }
      case 'coach-request': {
        await flushEdits(); const code = currentCode(); const result = await rpc('webCoach',{code,level:Number(button.dataset.level)}); state.coachMessage = result;
        if (code !== currentCode()) { await rpc('completeCoachFailure',{requestId:result.requestId}); throw new Error('代码已更新，请重新请求教练。'); }
        const host = $('#coach-message'); host.innerHTML = `<article class="coach-message"><h3>${esc(result.hypothesis)}</h3><p>${esc(result.question)}</p><p>${esc(result.guidance)}</p><details><summary>反思与迁移</summary><p>${esc(result.reflection)}</p></details>${features.coachEvidence(result.evidence,result.answerViewed)}<small>${esc(result.disclaimer)}</small>${result.probe ? '<div class="inline-actions"><button class="btn small" data-action="coach-probe">核查小样例 →</button></div>' : ''}<div id="coach-probe"></div></article>`;
        try { const ack = await rpc('ackCoachMessage',{messageId:result.messageId,code}); if (!ack.recorded && ack.reason !== 'already_acknowledged') throw new Error('提示展示记录未能确认。'); state.coach.lastLevel = Math.max(state.coach.lastLevel,result.level); state.coach.maxLevel = Math.min(3,state.coach.lastLevel+1); document.querySelectorAll('[data-action="coach-request"]').forEach(b=>b.disabled = Number(b.dataset.level) > state.coach.maxLevel); } catch(error) { host.innerHTML = ''; throw error; } break;
      }
      case 'coach-probe': { const r = await rpc('runCoachProbe',{messageId:state.coachMessage.messageId,code:currentCode()}); $('#coach-probe').innerHTML = `<div class="notice">${esc(r.values.join(', '))}<br>记录条数：${r.rowCount} · 数量之和：${r.quantitySum}<br>${esc(r.disclaimer)}</div>`; break; }
      case 'history-prev': state.historyOffset = Math.max(0,state.historyOffset-15); await renderHistory(); break;
      case 'history-next': state.historyOffset += 15; await renderHistory(); break;
      case 'task-history': await taskHistory(Number(button.dataset.index)); break;
      case 'events-prev': await taskHistory(state.taskHistoryIndex,Math.max(0,state.taskHistory.offset-20)); break;
      case 'events-next': await taskHistory(state.taskHistoryIndex,state.taskHistory.offset+20); break;
      case 'review': { const event = state.taskHistory.events[Number(button.dataset.index)]; const r = await rpc('getLearningReview',{...taskRef(state.taskHistory.task),eventId:event.eventId,scopeCourseId:state.historyCourse,snapshotEventId:state.taskHistory.snapshotEventId}); $('#review-result').innerHTML = `<div class="review-changes">${r.changes.map(change=>`<div class="review-change">${esc(change.name)}<strong>${change.beforeScore == null ? '—' : change.beforeScore+'%'} → ${change.afterScore == null ? '—' : change.afterScore+'%'}</strong>${esc(change.explanation)}</div>`).join('')}</div><p class="disclaimer">${esc(r.reason)}</p>`; break; }
      case 'run-demo': await runDemo(); break;
      case 'export-demo': { const blob = new Blob([JSON.stringify(state.demo,null,2)],{type:'application/json'}); const url = URL.createObjectURL(blob); const a = document.createElement('a'); a.href = url; a.download = `sparktutor-demo-${state.demo.scenarioId}.json`; a.click(); setTimeout(()=>URL.revokeObjectURL(url),1000); break; }
      case 'detect-mode': { const r = await rpc('detectMode'); $('#mode-result').textContent = `检测结果：${r.mode === 'dry_run' ? '语法检查模式（未检测到可用 Spark 执行环境）' : r.mode}`; break; }
      case 'test-ai': { await saveSettings(); const r = await rpc('webChat',{question:'请用一句简短中文确认你已连接。',code:''}); toast(r.answer); break; }
      case 'clear-key': { await rpc('configureWebSettings',{baseUrl:$('#ai-base').value,model:$('#ai-model').value,clearKey:true}); $('#ai-key').value = ''; $('#ai-key').placeholder = '密钥已清除'; toast('已清除当前标签页中的密钥。'); break; }
    }
  } catch (error) {
    toast(error.message,true); if (type === 'run-demo' && $('#demo-result')) $('#demo-result').innerHTML = `<div class="feedback">${esc(error.message)}</div>`;
  } finally {
    state.operationId='';
    state.busy = false;
    if (lockedEditor?.isConnected) lockedEditor.readOnly = false;
    if (button.isConnected) { button.innerHTML = oldMarkup; button.disabled = false; }
    if ($('#next-button')) $('#next-button').disabled = ['script','cmd_question','mult_question'].includes(state.lesson?.step?.cls) && !state.passed;
    if (['load','resume','course-start','recommended'].includes(type) && location.hash === '#lesson' && !$('#code-editor') && !$('.lesson-reading')) await route();
  }
}
async function saveSettings(){return settingsPanel.save();}

document.addEventListener('click', event => {
  if (!event.target.closest('.sidebar') && !event.target.closest('#menu-toggle')) { $('.sidebar').classList.remove('open'); $('#menu-toggle').setAttribute('aria-expanded','false'); }
  const button = event.target.closest('[data-action]'); if (button) { if(button.tagName==='A')event.preventDefault(); action(button); return; }
  if (state.busy && event.target.closest('a[href^="#"]')) { event.preventDefault(); toast('当前操作正在进行，请等待完成。'); }
});
document.addEventListener('input', event => {
  if (event.target.id === 'course-search') { state.query = event.target.value; renderCatalogResults(); }
  if (event.target.id !== 'code-editor') return;
  const code = event.target.value; const old = state.lastCode; state.lastCode = code; rememberDraft(); updateLines();
  state.draftDirty = true; if ($('#save-state')) $('#save-state').textContent = '正在保存…';
  if (!state.edit) state.edit = {eventType:'code_edit',courseId:state.courseId,lessonId:state.lesson.lessonId,taskId:`${state.lesson.lessonId}:${state.lesson.step.id}`,data:{changeCount:0,addedChars:0,removedChars:0}};
  state.edit.data.changeCount++; state.edit.data.addedChars += Math.max(0,code.length - old.length); state.edit.data.removedChars += Math.max(0,old.length - code.length);
  if (code.length === old.length && code !== old) { state.edit.data.addedChars++; state.edit.data.removedChars++; }
  state.passed = false; $('#next-button').disabled = true;
  if ($('#coach-message')) $('#coach-message').innerHTML = '';
  clearTimeout(state.draftTimer); state.draftTimer = setTimeout(()=>{ if (!state.busy) flushEdits().catch(e=>toast(e.message,true)); },900);
});
document.addEventListener('change', async event => {
  if(state.busy)return;
  try{if(await features.handleChange(event))return;}catch(error){toast(error.message,true);return;}
  if(event.target.id==='lesson-depth'){state.depthExplicit=true;sessionStorage.setItem('sparktutor.depth',event.target.value);state.busy=true;try{await loadLesson(state.courseId,state.lessonIdx,event.target.value);}catch(error){toast(error.message,true);}finally{state.busy=false;}return;}
  if (event.target.id === 'depth-select') { state.depth = event.target.value; state.depthExplicit=true; sessionStorage.setItem('sparktutor.depth',state.depth); return; }
  if (event.target.id === 'history-course' || event.target.id === 'history-window') {
    state.historyCourse = $('#history-course').value; state.historyWindow = $('#history-window').value; state.historyOffset = 0;
    try { await renderHistory(); } catch(error) { toast(error.message,true); }
  }
});
document.addEventListener('scroll', event => { if (event.target.id === 'code-editor') $('#line-numbers').scrollTop = event.target.scrollTop; },true);
document.addEventListener('keydown', event => {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') { event.preventDefault(); action($('.header-search')); return; }
  if (event.key === 'Escape') { $('.sidebar').classList.remove('open'); $('#menu-toggle').setAttribute('aria-expanded','false'); }
  if (event.target.id === 'code-editor' && event.key === 'Tab') { event.preventDefault(); const editor = event.target; const a = editor.selectionStart, b = editor.selectionEnd; editor.setRangeText('    ',a,b,'end'); editor.dispatchEvent(new Event('input',{bubbles:true})); }
  if ((event.ctrlKey || event.metaKey) && event.shiftKey && ['R','S','N','B','H'].includes(event.key.toUpperCase()) && location.hash === '#lesson') { event.preventDefault(); const types = {R:'run',S:'submit',N:'next',B:'back',H:'hint'}; const button = $(`[data-action="${types[event.key.toUpperCase()]}"]`); if (button && !button.disabled) action(button); }
});
document.addEventListener('submit', async event => {
  if (!['chat-form','settings-form'].includes(event.target.id)) return; event.preventDefault(); if (state.busy) return;
  const button = $('button[type="submit"]',event.target); state.busy = true; button.disabled = true;
  try {
    if(event.target.id==='settings-form'){await settingsPanel.handleSubmit(event);}
    else { const question = $('#chat-question').value.trim(); if (!question) return; state.chats.push({role:'user',text:question}); renderHelp(); $('#chat-question').disabled = true; await flushEdits(); const r = await rpc('webChat',{question,code:currentCode()}); state.chats.push({role:'tutor',text:r.answer}); renderHelp(); }
  } catch(error) { toast(error.message,true); if ($('#chat-question')) $('#chat-question').disabled = false; }
  finally { state.busy = false; if (button.isConnected) button.disabled = false; }
});
$('#menu-toggle').addEventListener('click',()=>{ const open = $('.sidebar').classList.toggle('open'); $('#menu-toggle').setAttribute('aria-expanded',String(open)); });
window.addEventListener('hashchange',route);
window.addEventListener('pagehide',()=>{
  rememberDraft(); if (state.edit) rpc('recordLearningEvent',state.edit,true).catch(()=>{});
  if (state.draftDirty && state.lesson?.step?.id) rpc('saveDraft',{...draftIdentity(),code:currentCode()},true).catch(()=>{});
});

let draftFlush=Promise.resolve();
const features=window.createSparkTutorFeatures({rpc,esc,state,heading,date,pct,toast,currentCode,
 refresh:async()=>{const busy=state.busy;state.busy=false;try{await route();}finally{state.busy=busy;}},rotateSession});
const settingsPanel=window.createSparkTutorSettings?window.createSparkTutorSettings({rpc,esc,icon,toast,renderEnvironment:host=>features.renderEnvironment(host)}):null;
const checkinPanel=window.createSparkTutorCheckIn?window.createSparkTutorCheckIn({rpc,esc,icon,toast}):null;
function destroyEditor(){if(window.activeSparkTutorEditor){window.activeSparkTutorEditor.destroy();window.activeSparkTutorEditor=null;}}
function rotateSession(newSessionId){
 clearTimeout(state.draftTimer);state.edit=null;state.draftDirty=false;state.lesson=null;state.lastCode='';state.operationId='';state.notificationCursor=0;state.busy=false;destroyEditor();
 for(const key of Object.keys(localStorage))if(key.startsWith(`${state.storagePrefix||'sparktutor.'}draft.`))localStorage.removeItem(key);
 sessionStorage.removeItem('sparktutor.lastLesson');session=newSessionId||Array.from(crypto.getRandomValues(new Uint8Array(16)),v=>v.toString(16).padStart(2,'0')).join('');sessionStorage.setItem('sparktutor.session',session);location.hash='home';
}
async function pollNotifications(){
 if(window.SparkTutorAccount?.user.mustChange)return;
 if(state.polling)return;state.polling=true;
 try{
  const data=await rpc('pollWebNotifications',{after:state.notificationCursor});state.notificationCursor=data.cursor??state.notificationCursor;
  for(const item of data.notifications||[]){const params=item.params||{};
   if(item.method==='sessionReset'){rotateSession(params.sessionId);toast('学习记录已在另一标签页清除，请重新开始。');await route();break;}
   if(item.method==='lessonReset'){
    const prefix=`${state.storagePrefix||'sparktutor.'}draft.${params.courseId}.${params.lessonId}.`;
    if(state.courseId===params.courseId&&state.lesson?.lessonId===params.lessonId&&state.draftDirty){try{localStorage.setItem(`${state.storagePrefix||'sparktutor.'}recovery.${Date.now()}`,JSON.stringify({courseId:state.courseId,lessonId:state.lesson.lessonId,stepId:state.lesson.step?.id,code:currentCode()}));}catch{}}
    for(const key of Object.keys(localStorage))if(key.startsWith(prefix))localStorage.removeItem(key);
    if(state.courseId===params.courseId&&state.lesson?.lessonId===params.lessonId){const target={courseId:state.courseId,index:state.lessonIdx,depth:state.depth};state.lesson=null;state.edit=null;state.draftDirty=false;state.lastCode='';state.busy=false;state.operationId='';destroyEditor();sessionStorage.removeItem('sparktutor.lastLesson');toast('本课已在另一标签页重置，正在读取新进度。');if(location.hash==='#lesson')await loadLesson(target.courseId,target.index,target.depth);else await route();}
    continue;
   }
   if(!state.operationId||params.operationId!==state.operationId)continue;
   if(item.method==='output'&&$('#run-output')){const host=$('#run-output');if(!state.outputStarted){host.textContent='';state.outputStarted=true;}host.textContent+=String(params.line??'')+'\n';host.scrollTop=host.scrollHeight;}
   if(item.method==='demoProgress'&&$('#demo-result')){let host=$('#demo-progress');if(!host){host=document.createElement('p');host.id='demo-progress';host.className='notification-progress';$('#demo-result').append(host);}host.textContent=params.message||params.title||`已完成阶段 ${params.completed??params.stageIndex??''} / ${params.total??''}`;}
  }
 }catch(error){if(state.operationId)console.warn('SparkTutor output updates paused.');}
 finally{state.polling=false;}
}
async function beginOperation(){await pollNotifications();state.operationId=Array.from(crypto.getRandomValues(new Uint8Array(12)),v=>v.toString(16).padStart(2,'0')).join('');state.outputStarted=false;}
if(window.SparkTutorAccount){
 window.SparkTutorAccount.beforeLogout=async()=>{rememberDraft();await flushEdits();};
 window.SparkTutorAccount.afterLogout=()=>{for(const key of Object.keys(localStorage))if(key.startsWith(state.storagePrefix))localStorage.removeItem(key);};
}
setInterval(pollNotifications,700);
route();
