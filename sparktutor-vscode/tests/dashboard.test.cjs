const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const test = require('node:test');
const esbuild = require('esbuild');

function loadTs(name, mocks = {}) {
  const filename = path.resolve(__dirname, '../src', name);
  const { outputFiles } = esbuild.buildSync({ entryPoints: [filename], bundle: true,
    platform: 'node', format: 'cjs', write: false, external: ['vscode'], logLevel: 'silent' });
  const loaded = new Module(filename, module);
  loaded.paths = module.paths;
  loaded.require = (id) => Object.hasOwn(mocks, id) ? mocks[id] : require(id);
  loaded._compile(outputFiles[0].text, filename);
  return loaded.exports;
}
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
};
function report() {
  const lesson = (id, index, status) => ({ id, index, status, available: true, title: `真实课题 ${id}`,
    currentStep: status === 'in_progress' ? 4 : 0, totalSteps: 12, depth: 'intermediate', estimatedMinutes: 30 });
  const courses = [{ id: 'a', title: 'Spark 基础', description: '通过真实代码学习', lessonCount: 3,
    prerequisites: ['Python 基础'], requiresLakehouse: false,
    lessons: [lesson('first', 0, 'not_started'), lesson('middle', 1, 'in_progress'), lesson('last', 2, 'completed')] },
  { id: 'b', title: '声明式管道', description: '构建数据管道', lessonCount: 1, prerequisites: [], requiresLakehouse: true,
    lessons: [lesson('pipeline', 0, 'not_started')] }];
  const dimensions = [['knowledge', 0, 'latestPassedTasks', 'evaluatedTasks', 0, 3],
    ['debugging', 50, 'confirmedRepairs', 'failureEpisodes', 1, 2],
    ['hint_dependency', 80, 'hintedBeforeFirstAssessment', 'evaluatedTasks', 4, 5],
    ['transfer', null, 'firstPassedTasks', 'eligibleTransferTasks', 0, 0]].map(([key, score, numerator, denominator, n, d]) => ({
      key, name: key, score, direction: key === 'hint_dependency' ? 'descriptive' : 'higher_is_better',
      summary: '依据实际记录', evidenceCount: d, sampleSize: d, evidenceLevel: d ? 'limited' : 'none',
      metrics: { [numerator]: n, [denominator]: d }, evidence: { totalCount: d, limit: 100,
        items: key === 'debugging' ? [{ courseId: 'a', lessonId: 'middle', taskId: 'middle:19', stepId: '19',
          title: '修复聚合计算', outcome: 'repaired', assisted: true, episodeIndex: 1 }] : [] },
    }));
  const knowledgeComponents = { 'dataframe.filter': { evaluatedTasks: 3, latestPassedTasks: 1 },
    'unknown.topic': { evaluatedTasks: 0, latestPassedTasks: 0 } };
  return { courses, selectedCourseId: '', catalogScope: 'all', knowledgeComponents,
    resume: { courseId: 'a', courseTitle: 'Spark 基础', lessonId: 'middle', lessonTitle: '真实课题 middle',
      lessonIdx: 1, depth: 'intermediate', currentStep: 4, stepId: '19', action: 'resume' },
    diagnosis: { diagnosis: '先练习行筛选，再观察新的表现。', disclaimer: '形成性反馈，不是能力定论。',
      dimensions, eventCount: 9, eligibleEventCount: 6, knowledgeComponents,
      recommendedExercise: { courseId: 'a', lessonId: 'middle', lessonIndex: 1, stepId: '23', title: '筛选变式题',
        dimension: 'knowledge', reason: '通过新场景巩固筛选。' }, recommendationReason: '' }, warnings: [] };
}
function vscodeFixture() {
  const panels = [], commands = new Map();
  const vscode = {
    Uri: { joinPath: (base, ...parts) => `${base}/${parts.join('/')}` }, ViewColumn: { One: 1 },
    commands: { registerCommand: (name, callback) => { commands.set(name, callback); return { dispose() {} }; }, executeCommand: async () => {} },
    window: { createWebviewPanel: () => {
      const panel = { messages: [], webview: { html: '', cspSource: 'vscode:', asWebviewUri: (uri) => uri,
        onDidReceiveMessage(callback) { panel.receive = callback; }, postMessage(message) { panel.messages.push(message); } },
        onDidDispose(callback) { panel.didDispose = callback; }, reveal() {}, dispose() { panel.didDispose(); } };
      panels.push(panel); return panel;
    } },
  };
  return { vscode, panels, commands };
}
const version = (panel) => Number(panel.webview.html.match(/data-version="(\d+)"/)[1]);
const receive = (panel, message) => panel.receive({ version: version(panel), ...message });
const { dashboardContent } = loadTs('dashboardView.ts');

test('dashboard distinguishes a measured zero from insufficient evidence, uses rates, and separates hint behavior', () => {
  const html = dashboardContent(report());
  assert.ok(html.includes('0.0<span>%</span>'));
  assert.ok(html.includes('80.0<span>%</span>'));
  assert.ok(html.includes('知识任务表现'));
  assert.ok(html.includes('新情境首评通过率'));
  assert.ok(html.includes('证据不足'));
  assert.ok(!html.includes(' / 100'));
  assert.ok(html.indexOf('求助方式 · 描述性指标') > html.indexOf('新情境首评通过率'));
  assert.ok(html.includes('不合成为总分'));
});

test('all completed lessons have a clear no-resume state without inventing a mastery claim', () => {
  const result = report();
  result.resume = null;
  result.courses.forEach((course) => course.lessons.forEach((lesson) => { lesson.status = 'completed'; }));
  const html = dashboardContent(result);
  assert.ok(html.includes('所选范围的课节均有完成记录'));
  assert.ok(!html.includes('data-action="resume"'));
  assert.ok(html.includes('不表示所有难度均已完成'));
  assert.ok(html.includes('data-action="openRecommendation"'));
});

test('course progress uses per-lesson facts and unavailable lessons remain in the denominator', () => {
  const result = report();
  result.courses[0].lessons[1].available = false;
  result.courses[0].lessons[1].status = 'unavailable';
  const html = dashboardContent(result);
  assert.ok(html.includes('课节完成 1 / 3'));
  assert.ok(html.includes('33%'));
  assert.match(html, /data-lesson="first"[^>]*>开始/);
  assert.match(html, /data-lesson="last"[^>]*>回看/);
  assert.match(html, /data-lesson="middle"\s+disabled data-permanent-disabled="true"/);
});

test('filtered reports keep the complete catalog, and partial diagnosis failure leaves lesson controls usable', () => {
  const result = report(); result.selectedCourseId = 'a'; result.diagnosis = null;
  result.warnings = [{ section: 'diagnosis', message: '诊断暂不可用' }];
  const html = dashboardContent(result);
  assert.ok(html.includes('value="a" selected'));
  assert.ok(html.includes('声明式管道'));
  assert.ok(html.includes('课程目录始终显示全部课程'));
  assert.ok(html.includes('缺少诊断数据不表示分数为零'));
  assert.ok(html.includes('data-action="openLesson"'));
});

test('dimension details disclose concrete counted tasks, formula, sample limit, and one-based episode IDs', () => {
  const result = report();
  const html = dashboardContent(result);
  assert.ok(html.includes('查看计算方式与样本说明'));
  assert.ok(html.includes('可观察失败过程数 × 100'));
  assert.ok(html.includes('尚未修复的过程也在分母中'));
  assert.ok(html.includes('修复聚合计算'));
  assert.ok(html.includes('共 2 条，当前展示 1 条（最多 100 条）'));
  assert.ok(html.includes('过程 1'));
  assert.ok(!html.includes('过程 2'));
  assert.ok(html.includes('DataFrame 行筛选'));
  assert.ok(html.includes('dataframe.filter'));
  assert.ok(html.includes('unknown.topic'));
});

test('course text, diagnosis, KC keys, evidence titles, and warning text cannot inject markup', () => {
  const result = report(), payload = '<script>alert("x")</script>';
  result.courses[0].title = payload; result.courses[0].lessons[0].title = payload;
  result.diagnosis.diagnosis = payload; result.warnings.push({ message: payload });
  result.knowledgeComponents[payload] = { evaluatedTasks: 1, latestPassedTasks: 0 };
  result.diagnosis.dimensions[1].evidence.items[0].title = payload;
  const html = dashboardContent(result);
  assert.ok(!html.includes('<script>'));
  assert.ok(html.includes('&lt;script&gt;'));
});

test('empty catalog and no learning events explain missing evidence rather than failure to learn', () => {
  const result = report(); result.courses = []; result.resume = null; result.knowledgeComponents = {};
  result.diagnosis.eventCount = 0; result.diagnosis.recommendedExercise = null;
  result.diagnosis.dimensions.forEach((item) => { item.score = null; });
  const html = dashboardContent(result);
  assert.ok(html.includes('暂无可用课程'));
  assert.ok(html.includes('还没有学习证据'));
  assert.ok(html.includes('这不代表尚未学会'));
  assert.ok(!html.includes('data-action="resume"'));
});

test('panel ignores forged resume IDs, uses catalog indices, and rejects unavailable or unknown lessons', async () => {
  const { vscode, panels } = vscodeFixture();
  const { DashboardPanel } = loadTs('dashboardPanel.ts', { vscode });
  const opened = [], result = report();
  result.courses[1].lessons[0].available = false;
  const panel = new DashboardPanel('extension', async () => result, async (target) => opened.push(target), async () => {}, async () => {});
  await panel.show();
  await receive(panels[0], { type: 'resume', courseId: 'evil', lessonIdx: 999, depth: 'advanced' });
  assert.deepEqual(opened[0], { courseId: 'a', lessonId: 'middle', lessonIdx: 1, depth: 'intermediate' });
  await receive(panels[0], { type: 'openLesson', courseId: 'a', lessonId: 'last', lessonIdx: 999, depth: 'advanced' });
  assert.equal(opened[1].lessonIdx, 2);
  assert.equal(opened[1].depth, 'intermediate');
  await receive(panels[0], { type: 'openLesson', courseId: 'b', lessonId: 'pipeline' });
  await receive(panels[0], { type: 'openLesson', courseId: 'a', lessonId: 'made_up' });
  await receive(panels[0], { type: 'resume', version: -1 });
  assert.equal(opened.length, 2);
});

test('filter refresh races retain the latest scope, reject obsolete actions, and recommend within that scope', async () => {
  const { vscode, panels } = vscodeFixture();
  const { DashboardPanel } = loadTs('dashboardPanel.ts', { vscode });
  const old = deferred(), fresh = deferred(), opened = [], queries = [];
  const panel = new DashboardPanel('extension', async (scope) => {
    queries.push(scope);
    return queries.length === 1 ? report() : scope === 'a' ? old.promise : fresh.promise;
  }, async () => {}, async (...args) => opened.push(args), async () => {});
  await panel.show();
  const obsoleteVersion = version(panels[0]);
  const loadingA = receive(panels[0], { type: 'filter', courseId: 'a' });
  const loadingB = receive(panels[0], { type: 'filter', courseId: 'b' });
  const b = report(); b.selectedCourseId = 'b'; b.diagnosis.diagnosis = '最新范围';
  b.diagnosis.recommendedExercise.courseId = 'b';
  fresh.resolve(b); await loadingB;
  const a = report(); a.selectedCourseId = 'a'; a.diagnosis.diagnosis = '旧范围';
  old.resolve(a); await loadingA;
  assert.ok(panels[0].webview.html.includes('最新范围'));
  assert.ok(!panels[0].webview.html.includes('旧范围'));
  await receive(panels[0], { type: 'openRecommendation', version: obsoleteVersion });
  assert.equal(opened.length, 0);
  await receive(panels[0], { type: 'openRecommendation', courseId: 'injected', stepId: '999' });
  assert.equal(opened[0][0], b.diagnosis.recommendedExercise);
  assert.equal(opened[0][1], 'b');
  await receive(panels[0], { type: 'filter', courseId: 'not_a_course' });
  assert.deepEqual(queries, [undefined, 'a', 'b']);
});

test('load and action failures support retry; disposed panels ignore late load/action callbacks', async () => {
  const { vscode, panels } = vscodeFixture();
  const { DashboardPanel } = loadTs('dashboardPanel.ts', { vscode });
  let reads = 0, opens = 0;
  const waiting = deferred();
  const panel = new DashboardPanel('extension', async () => {
    if (++reads === 1) throw new Error('<bad connection>');
    return report();
  }, async () => { if (++opens === 1) throw new Error('保存失败'); await waiting.promise; }, async () => {}, async () => {});
  await panel.show();
  assert.ok(panels[0].webview.html.includes('&lt;bad connection&gt;'));
  await receive(panels[0], { type: 'refresh' });
  await receive(panels[0], { type: 'resume' });
  assert.equal(panels[0].messages.at(-1).error, true);
  assert.equal(panels[0].messages.at(-1).busy, false);
  const opening = receive(panels[0], { type: 'resume' });
  const before = panels[0].messages.length;
  panels[0].dispose();
  await panel.show();
  waiting.resolve(); await opening;
  assert.equal(panels[0].messages.length, before);
  assert.equal(panels[1].messages.length, 0);
  const late = deferred();
  const closing = new DashboardPanel('extension', () => late.promise, async () => {}, async () => {}, async () => {});
  const showing = closing.show();
  const html = panels[2].webview.html; panels[2].dispose();
  late.resolve(report()); await showing;
  assert.equal(panels[2].webview.html, html);
});

test('webview disables duplicate actions, preserves unavailable controls, and renders errors as text', () => {
  const element = (dataset) => ({ dataset, disabled: false, textContent: '', classList: { remove() {}, toggle() {} },
    addEventListener(_event, fn) { this.handle = fn; } });
  const resume = element({ action: 'resume' }), unavailable = element({ action: 'openLesson', permanentDisabled: 'true' });
  const filter = element({}), status = element({}), sent = [];
  let callback;
  vm.runInNewContext(fs.readFileSync(path.resolve(__dirname, '../media/dashboard.js'), 'utf8'), {
    acquireVsCodeApi: () => ({ postMessage: (message) => sent.push(message) }),
    document: { body: { dataset: { version: '8' } }, querySelectorAll: (query) => query.includes(', select')
      ? [resume, unavailable, filter] : [resume, unavailable],
      getElementById: (id) => id === 'course-filter' ? filter : status },
    window: { addEventListener: (_event, fn) => { callback = fn; } },
  });
  resume.handle(); resume.handle();
  assert.equal(sent.length, 1);
  assert.equal(resume.disabled, true);
  callback({ data: { type: 'actionStatus', version: 7, busy: false } });
  assert.equal(resume.disabled, true);
  callback({ data: { type: 'actionStatus', version: 8, busy: false, message: '<img onerror=bad>', error: true } });
  assert.equal(resume.disabled, false);
  assert.equal(unavailable.disabled, true);
  assert.equal(status.textContent, '<img onerror=bad>');
  assert.equal(status.innerHTML, undefined);
  filter.handle({ target: { value: 'b' } });
  assert.equal(sent.at(-1).courseId, 'b');
  assert.equal(sent.at(-1).version, 8);
});

test('sidebar uses individual lesson facts and clears a previous successful progress after a read failure', async () => {
  const EventEmitter = class { event() {} fire() {} };
  const vscode = { EventEmitter, TreeItem: class { constructor(label) { this.label = label; } },
    TreeItemCollapsibleState: { None: 0, Expanded: 2 }, ThemeIcon: class {}, ThemeColor: class {} };
  const { CourseTreeProvider } = loadTs('courseTree.ts', { vscode });
  const lessons = Array.from({ length: 10 }, (_, index) => ({ id: `lesson_${index}`, title: `真实课题${index}`,
    index, status: index === 9 ? 'completed' : 'not_started' }));
  let failure = false;
  const provider = new CourseTreeProvider({ call: async (method) => {
    if (method === 'listCourses') return { courses: [{ id: 'a', title: '课程', lessonCount: 10, lessons: lessons.map((item) => item.id) }] };
    if (failure) throw new Error('failed');
    return { started: true, lessonsCompleted: 1, lessons };
  } });
  const roots = await provider.getChildren();
  const children = await provider.getChildren(roots[0]);
  assert.deepEqual(children.map((node) => node.status), [...Array(9).fill('not-started'), 'completed']);
  assert.equal(children[9].label, '10. 真实课题9');
  failure = true;
  const failedRoots = await provider.getChildren();
  assert.match(failedRoots[0].description, /读取失败/);
  assert.ok((await provider.getChildren(failedRoots[0])).every((node) => node.status !== 'completed'));
});

test('dashboard command resumes saved step after saving current code and sends the recommendation filter scope', async () => {
  const { vscode, panels, commands } = vscodeFixture();
  const { registerCommands } = loadTs('commands.ts', { vscode });
  const operations = [], displayed = [];
  let failLoad = false;
  const result = report(); result.selectedCourseId = 'a';
  const loaded = { step: { id: '19', cls: 'script', output: 'saved step' }, currentIndex: 4,
    totalSteps: 12, lessonId: 'middle', lessonTitle: '恢复课节', starterCode: '', restoredCode: 'saved_work()' };
  const bridge = { call: async (method, params) => {
    operations.push({ method, params });
    if (method === 'getLearningDashboard') return result;
    if (method === 'loadLesson') { if (failLoad) throw new Error('加载失败'); return loaded; }
    if (method === 'openRecommendedExercise') return { ...loaded, courseId: 'a', lessonIdx: 1, depth: 'intermediate', practiceMode: true };
    assert.fail(`unexpected ${method}`);
  } };
  const workspace = { saveCurrentExercise: async () => operations.push({ method: 'save' }),
    switchCourse: async () => {}, setStepType() {}, openExercise: async () => {}, getCurrentCode: () => 'dirty_work()' };
  registerCommands({ extensionUri: 'extension', subscriptions: [], globalState: { update() {} } }, bridge,
    { refresh() {} }, { updateStep: (...args) => displayed.push(args) }, workspace,
    { clear() {} }, { clear() {} }, { setStep() {}, setDepth() {} }, {});
  await commands.get('sparktutor.openLearningDashboard')();
  operations.length = 0;
  await receive(panels[0], { type: 'resume', lessonIdx: 0 });
  assert.deepEqual(operations.map((item) => item.method), ['save', 'loadLesson']);
  assert.deepEqual(operations[1].params, { courseId: 'a', lessonIdx: 1, depth: 'intermediate' });
  assert.equal(displayed[0][1], 4);
  assert.equal(displayed[0][0].id, '19');
  await receive(panels[0], { type: 'openRecommendation', stepId: 'evil' });
  assert.deepEqual(operations.find((item) => item.method === 'openRecommendedExercise').params,
    { courseId: 'a', lessonId: 'middle', stepId: '23', scopeCourseId: 'a' });
  assert.equal(displayed.at(-1)[5], true);
  failLoad = true;
  await receive(panels[0], { type: 'resume' });
  assert.equal(panels[0].messages.at(-1).error, true);
  assert.match(panels[0].messages.at(-1).message, /加载失败/);
});

test('workbench has a discoverable home command and keeps the previous diagnosis entry', () => {
  const manifest = JSON.parse(fs.readFileSync(path.resolve(__dirname, '../package.json'), 'utf8'));
  assert.ok(manifest.contributes.commands.some((command) => command.command === 'sparktutor.openLearningDashboard' && command.icon === '$(home)'));
  assert.ok(manifest.contributes.menus['view/title'].some((item) => item.command === 'sparktutor.openLearningDashboard'));
  assert.ok(manifest.contributes.commands.some((command) => command.command === 'sparktutor.showLearningDiagnosis'));
});
