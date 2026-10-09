const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const test = require('node:test');
const esbuild = require('esbuild');

function loadTs(name, mocks = {}) {
  const filename = path.resolve(__dirname, '../src', name);
  const { outputFiles } = esbuild.buildSync({ entryPoints: [filename], bundle: true, platform: 'node',
    format: 'cjs', write: false, external: ['vscode'], logLevel: 'silent' });
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
const windowEnd = '2026-10-09T03:00:00+00:00';
const ref = { courseId: 'a', lessonId: 'lesson', taskId: 'lesson:19' };
const event = (eventId = 'e1', overrides = {}) => ({ eventId, eventType: 'code_submit', timestamp: windowEnd,
  attemptNumber: 2, passed: false, rawPassed: false, assessmentSource: 'course_tests', mode: 'local',
  category: 'assessment', reason: '本次为有效课程评估，是否改变各维度请查看复盘。', answerInfluenced: false,
  reviewable: true, hintUsed: false, available: true, errorTypes: [], ...overrides });
const task = (taskId = ref.taskId) => ({ ...ref, taskId, stepId: '19', title: `真实题目 ${taskId}`,
  courseTitle: 'Spark 基础', lessonTitle: '数据处理', available: true, taskType: 'script', lastActivityAt: windowEnd,
  counts: { submissions: 2, eligibleAssessments: 1, hints: 1, answers: 0, runs: 3, edits: 4 },
  latestSubmission: event('ai', { passed: null, rawPassed: true, category: 'excluded', reason: 'AI 评审不作为四维量化评估证据。' }),
  latestAssessment: event(), answerViewed: false });
const history = (overrides = {}) => ({ courses: [{ id: 'a', title: 'Spark 基础', available: true, lessons: [] },
  { id: 'b', title: '管道', available: true, lessons: [] }], courseId: '', window: 'all', windowStart: null, windowEnd,
  tasks: [task(), task('lesson:23')], offset: 0, limit: 20, total: 25, hasMore: true,
  snapshotEventId: 'snapshot-1', dataQuality: {}, disclaimer: '时间筛选仅影响展示，不裁剪评分历史。', ...overrides });
const detail = (selectedTask = task(), overrides = {}) => ({ task: selectedTask, events: [event(), event('e2')],
  offset: 0, limit: 50, total: 51, hasMore: true, snapshotEventId: 'snapshot-1',
  dataQuality: {}, disclaimer: '完整任务过程。', ...overrides });
const dimensions = (score) => ['knowledge', 'debugging', 'transfer', 'hint_dependency'].map((key) => ({
  key, name: key, score, sampleSize: score === null ? 0 : 2, direction: key === 'hint_dependency' ? 'descriptive' : 'higher_is_better',
  numerator: score === null || score === 0 ? 0 : 1, denominator: score === null ? 0 : 2, metrics: {},
}));
const review = (overrides = {}) => ({ task: task(), event: event(), before: { dimensions: dimensions(null) },
  after: { dimensions: dimensions(0) }, changes: dimensions(0).map((dimension) => ({ key: dimension.key, name: dimension.name,
    beforeScore: null, afterScore: 0, delta: null, numeratorDelta: 0, denominatorDelta: 2, sampleDelta: 2,
    explanation: '新增可用证据，不计算百分点变化。' })), reason: '本次记录新增两个样本。',
  disclaimer: '按当前规则重建，不是当时保存的诊断快照。', dataQuality: {}, ...overrides });
const trend = () => ({ courseId: '', window: 'all', windowEnd, snapshotEventId: 'snapshot-1',
  total: 23, limit: 20, hasMore: true, dataQuality: {}, disclaimer: '完整前缀评分，最近点仅限制展示。',
  points: [50, null, 0].map((score, index) => ({ ...ref, eventId: `p${index}`, timestamp: windowEnd,
    title: `证据点 ${index}`, type: 'code_submit', eventLabel: '提交答案', dimensions: dimensions(score) })) });
const state = (overrides = {}) => ({ courses: history().courses, courseId: '', window: 'all', report: history(), loading: false,
  error: '', task: null, detail: null, detailLoading: false, detailError: '', selectedEvent: null,
  selectedTrendIndex: null, review: null, reviewLoading: false, reviewError: '', trend: null, trendLoading: false, trendError: '', ...overrides });
const view = loadTs('learningHistoryView.ts');

function fixture(api = {}) {
  const panels = [], commands = new Map(), calls = [];
  const vscode = { Uri: { joinPath: (base, ...parts) => `${base}/${parts.join('/')}` }, ViewColumn: { One: 1 },
    commands: { registerCommand: (name, callback) => { commands.set(name, callback); return { dispose() {} }; }, executeCommand: async () => {} },
    window: { createWebviewPanel: () => {
      const panel = { webview: { html: '', cspSource: 'vscode:', asWebviewUri: (uri) => uri,
        onDidReceiveMessage(handler) { panel.receive = handler; }, postMessage() {} },
        onDidDispose(handler) { panel.didDispose = handler; }, reveal() {}, dispose() { panel.didDispose(); } };
      panels.push(panel); return panel;
    } } };
  const { LearningHistoryPanel } = loadTs('learningHistoryPanel.ts', { vscode });
  const defaults = { history: async (query) => history({ courseId: query.courseId || '', window: query.window, offset: query.offset }),
    task: async (query) => detail(task(query.taskId), { offset: query.offset }), review: async () => review(), trend: async () => trend() };
  const handlers = Object.fromEntries(Object.keys(defaults).map((method) => [method, async (query) => {
    calls.push([method, query]); return (api[method] || defaults[method])(query);
  }]));
  return { panel: new LearningHistoryPanel('extension', handlers), panels, vscode, calls, commands };
}
const version = (panel) => Number(panel.webview.html.match(/data-version="(\d+)"/)[1]);
const receive = (panel, message) => panel.receive({ version: version(panel), ...message });

test('history cards separate window counts from complete-history assessment and do not trust raw pass values', () => {
  const html = view.learningHistoryContent(state());
  assert.ok(html.includes('最近提交：<strong>未评估</strong>'));
  assert.ok(html.includes('最近未受答案查看影响的有效评估：未通过'));
  assert.ok(html.includes('卡片计数仅统计所选时间窗口'));
  assert.ok(html.includes('编辑记录仅用于回看过程，不参与诊断评分'));
  assert.ok(html.includes('真实题目 lesson:19'));
  assert.ok(html.includes('<dt>运行</dt><dd>3</dd>'));
});

test('timeline explains excluded, syntax-only and answer-influenced records without exposing arbitrary payloads', () => {
  const events = [event('old', { category: 'excluded', passed: null, reason: '旧日志不追认为成绩。', data: { code: 'SECRET_CODE', path: '/private/file' } }),
    event('dry', { eventType: 'code_run', mode: 'dry_run', category: 'excluded', passed: null, reason: '仅语法检查。' }),
    event('answer', { passed: true, answerInfluenced: true, reason: '答案后结果不新增知识证据。' }),
    event('edit', { eventType: 'code_edit', passed: null, category: 'behavior', editCounts: { changeCount: 3, addedChars: 20, removedChars: 4 } })];
  const html = view.learningHistoryContent(state({ task: task(), detail: detail(task(), { events }) }));
  assert.ok(html.includes('旧日志不追认为成绩'));
  assert.ok(html.includes('未执行 Spark'));
  assert.ok(html.includes('不能作为未受答案影响的通过证据'));
  assert.ok(html.includes('3 次变更'));
  assert.ok(!html.includes('SECRET_CODE'));
  assert.ok(!html.includes('/private/file'));
  assert.equal((html.match(/第 2 次提交/g) || []).length, 2);
  assert.ok(html.includes('查看这条记录的前后变化'));
});

test('review shows new evidence without a null-to-zero gain, and uses neutral percentage-point and sample changes', () => {
  let html = view.reviewContent(review());
  assert.ok(html.includes('新增可用证据，不计算百分点变化'));
  assert.ok(html.includes('0.0%'));
  assert.ok(html.includes('分子 / 分母：0 / 2'));
  assert.ok(html.includes('样本变化 +2'));
  assert.ok(html.includes('求助行为 · 不表示能力高低'));
  assert.ok(!html.includes('比例变化 +0'));
  const measured = review({ before: { dimensions: dimensions(0) }, after: { dimensions: dimensions(50) } });
  measured.changes.forEach((change) => { change.delta = 50; });
  html = view.reviewContent(measured);
  assert.ok(html.includes('比例变化 +50 个百分点'));
  assert.ok(html.includes('完整历史前缀'));
});

test('trend gaps remain disconnected, measured zero is plotted, and every selected point exposes counts', () => {
  const html = view.learningHistoryContent(state({ trend: trend(), selectedTrendIndex: 2 }));
  assert.ok(html.includes('当前展示 3 / 23 个记录点'));
  assert.equal((html.match(/<polyline /g) || []).length, 8); // two disjoint segments per dimension
  assert.equal((html.match(/<circle /g) || []).length, 8); // null has no plotted point
  assert.ok(html.includes('0.0% · 样本 2 · 分子 / 分母 0 / 2'));
  assert.ok(html.includes('不解释为越高或越低越好'));
  assert.ok(html.includes('横轴为记录顺序'));
  assert.ok(html.includes('data-point-index="2" aria-current="true"'));
});

test('titles, event explanations, review explanations and filters are escaped; missing history has an honest empty state', () => {
  const payload = '<img src=x onerror="attack()">', report = history();
  report.tasks[0].title = payload; report.courses[0].title = payload;
  const current = state({ courses: report.courses, report, task: task(), detail: detail(task(), { events: [event('x', { reason: payload })] }),
    review: review({ reason: payload }) });
  current.review.changes[0].explanation = payload;
  let html = view.learningHistoryContent(current);
  assert.ok(!html.includes('<img'));
  assert.ok(html.includes('&lt;img'));
  html = view.learningHistoryContent(state({ report: history({ tasks: [], total: 0, hasMore: false }), trend: { ...trend(), points: [], total: 0 } }));
  assert.ok(html.includes('这个范围内还没有任务记录'));
  assert.ok(html.includes('没有记录不表示尚未学会'));
  assert.ok(html.includes('仅有编辑或求助记录时，不生成评分趋势'));
});

test('task, event and trend pages keep the frozen event bound and time window; refresh and filters reset them', async () => {
  const { panel, panels, calls } = fixture();
  await panel.show();
  assert.equal(Object.hasOwn(calls[0][1], 'snapshotEventId'), false);
  assert.equal(Object.hasOwn(calls[0][1], 'windowEnd'), false);
  await receive(panels[0], { type: 'tasksNext', offset: 9000 });
  assert.deepEqual(calls.at(-1)[1], { courseId: '', window: 'all', offset: 20, limit: 20, snapshotEventId: 'snapshot-1', windowEnd });
  await receive(panels[0], { type: 'selectTask', taskIndex: 0, courseId: 'forged', taskId: 'wrong' });
  assert.deepEqual(calls.at(-1)[1], { ...ref, offset: 0, limit: 50, snapshotEventId: 'snapshot-1' });
  await receive(panels[0], { type: 'eventsNext' });
  assert.equal(calls.at(-1)[1].offset, 50);
  assert.equal(calls.at(-1)[1].snapshotEventId, 'snapshot-1');
  await receive(panels[0], { type: 'loadTrend' });
  assert.deepEqual(calls.at(-1)[1], { courseId: '', window: 'all', snapshotEventId: 'snapshot-1', windowEnd });
  await receive(panels[0], { type: 'filter', courseId: 'a', window: '7d' });
  assert.deepEqual(calls.at(-1)[1], { courseId: 'a', window: '7d', offset: 0, limit: 20 });
  await receive(panels[0], { type: 'refresh' });
  assert.deepEqual(calls.at(-1)[1], { courseId: 'a', window: '7d', offset: 0, limit: 20 });
});

test('review IDs come only from the loaded detail or trend and preserve course scope', async () => {
  const { panel, panels, calls } = fixture();
  await panel.show();
  await receive(panels[0], { type: 'filter', courseId: 'a', window: '30d' });
  await receive(panels[0], { type: 'selectTask', taskIndex: 0 });
  await receive(panels[0], { type: 'reviewEvent', eventIndex: 0, eventId: 'fake', courseId: 'other' });
  assert.deepEqual(calls.at(-1), ['review', { ...ref, eventId: 'e1', scopeCourseId: 'a', snapshotEventId: 'snapshot-1' }]);
  const before = calls.length;
  for (const message of [{ type: 'reviewEvent', eventIndex: 100 }, { type: 'selectTask', taskIndex: -1 },
    { type: 'selectTask', taskIndex: '0' }, { type: 'filter', courseId: 'unknown', window: 'all' },
    { type: 'filter', courseId: 'a', window: 'injected' }, { type: 'refresh', version: -1 }]) await receive(panels[0], message);
  assert.equal(calls.length, before);
  await receive(panels[0], { type: 'loadTrend' });
  await receive(panels[0], { type: 'selectTrend', pointIndex: 2, eventId: 'bad' });
  assert.deepEqual(calls.at(-1), ['review', { ...ref, eventId: 'p2', scopeCourseId: 'a', snapshotEventId: 'snapshot-1' }]);
});

test('changing course ignores old list and trend responses, without restoring stale targets', async () => {
  const oldList = deferred(), oldTrend = deferred();
  let reads = 0;
  const { panel, panels, calls } = fixture({ history: async (query) => ++reads === 2 ? oldList.promise : history({ courseId: query.courseId }),
    trend: () => oldTrend.promise });
  await panel.show();
  const trendLoading = receive(panels[0], { type: 'loadTrend' });
  const listLoading = receive(panels[0], { type: 'filter', courseId: 'a', window: '7d' });
  await receive(panels[0], { type: 'filter', courseId: 'b', window: '30d' });
  oldList.resolve(history({ courseId: 'a' })); oldTrend.resolve(trend());
  await Promise.all([listLoading, trendLoading]);
  assert.match(panels[0].webview.html, /value="b" selected/);
  assert.ok(!panels[0].webview.html.includes('data-point-index='));
  const before = calls.length;
  await receive(panels[0], { type: 'selectTrend', pointIndex: 0 });
  assert.equal(calls.length, before);
});

test('changing task or event rejects late detail and review responses', async () => {
  const oldDetail = deferred(), oldReview = deferred(), newReview = deferred();
  let details = 0, reviews = 0;
  const { panel, panels } = fixture({ task: (query) => ++details === 1 ? oldDetail.promise : detail(task(query.taskId)),
    review: () => ++reviews === 1 ? oldReview.promise : newReview.promise });
  await panel.show();
  const first = receive(panels[0], { type: 'selectTask', taskIndex: 0 });
  await receive(panels[0], { type: 'selectTask', taskIndex: 1 });
  oldDetail.resolve(detail(task(), { events: [event('obsolete')] })); await first;
  assert.ok(!panels[0].webview.html.includes('记录标识 obsolete'));
  const old = receive(panels[0], { type: 'reviewEvent', eventIndex: 0 });
  const latest = receive(panels[0], { type: 'reviewEvent', eventIndex: 1 });
  newReview.resolve(review({ reason: '最新的复盘结果' })); await latest;
  oldReview.resolve(review({ reason: '过期的复盘结果' })); await old;
  assert.ok(panels[0].webview.html.includes('最新的复盘结果'));
  assert.ok(!panels[0].webview.html.includes('过期的复盘结果'));
});

test('refresh failure clears old detail, trend and review and retry rebuilds usable targets', async () => {
  let fail = false;
  const { panel, panels, calls } = fixture({ history: async () => { if (fail) throw new Error('暂时断线'); return history(); } });
  await panel.show();
  await receive(panels[0], { type: 'selectTask', taskIndex: 0 });
  await receive(panels[0], { type: 'loadTrend' });
  await receive(panels[0], { type: 'selectTrend', pointIndex: 0 });
  fail = true;
  await receive(panels[0], { type: 'refresh' });
  const html = panels[0].webview.html;
  assert.ok(html.includes('读取学习历史失败'));
  assert.ok(!html.includes('data-task-index='));
  assert.ok(!html.includes('data-event-index='));
  assert.ok(!html.includes('data-point-index='));
  const before = calls.length;
  await receive(panels[0], { type: 'retryReview' });
  assert.equal(calls.length, before);
  fail = false;
  await receive(panels[0], { type: 'refresh' });
  assert.ok(panels[0].webview.html.includes('data-task-index="0"'));
});

test('switching task invalidates a pending review and a failed trend can be retried independently', async () => {
  const late = deferred();
  let trends = 0;
  const { panel, panels } = fixture({ review: () => late.promise,
    trend: async () => { if (++trends === 1) throw new Error('趋势读取失败'); return trend(); } });
  await panel.show();
  await receive(panels[0], { type: 'selectTask', taskIndex: 0 });
  const reviewLoading = receive(panels[0], { type: 'reviewEvent', eventIndex: 0 });
  await receive(panels[0], { type: 'selectTask', taskIndex: 1 });
  late.resolve(review({ reason: '上一题的复盘' })); await reviewLoading;
  assert.ok(!panels[0].webview.html.includes('上一题的复盘'));
  await receive(panels[0], { type: 'loadTrend' });
  assert.ok(panels[0].webview.html.includes('趋势读取失败'));
  assert.ok(panels[0].webview.html.includes('记录标识 e1'));
  await receive(panels[0], { type: 'loadTrend' });
  assert.ok(panels[0].webview.html.includes('data-point-index="0"'));
});

test('detail and review errors retain only retryable current targets, and closing the panel drops pending callbacks', async () => {
  let tasks = 0, reviews = 0;
  const late = deferred();
  const { panel, panels } = fixture({ task: async () => { if (++tasks === 1) throw new Error('详情失败'); return detail(); },
    review: async () => { if (++reviews === 1) throw new Error('复盘失败'); return late.promise; } });
  await panel.show();
  await receive(panels[0], { type: 'selectTask', taskIndex: 0 });
  assert.ok(panels[0].webview.html.includes('data-action="retryTask"'));
  await receive(panels[0], { type: 'retryTask' });
  await receive(panels[0], { type: 'reviewEvent', eventIndex: 0 });
  assert.ok(panels[0].webview.html.includes('data-action="retryReview"'));
  const waiting = receive(panels[0], { type: 'retryReview' });
  const oldHtml = panels[0].webview.html;
  panels[0].dispose();
  await panel.show();
  late.resolve(review({ reason: '已关闭窗口的结果' })); await waiting;
  assert.equal(panels[0].webview.html, oldHtml);
  assert.ok(!panels[1].webview.html.includes('已关闭窗口的结果'));
});

test('actual history script sends only current indices and version, prevents duplicates, and focuses the requested section', () => {
  const element = (dataset = {}) => ({ dataset, disabled: false, value: '', textContent: '',
    addEventListener(_event, handler) { this.handle = handler; }, scrollIntoView() { this.scrolled = true; }, focus() { this.focused = true; } });
  const button = element({ action: 'reviewEvent', eventIndex: '1' }), disabled = element({ action: 'eventsNext' });
  disabled.disabled = true;
  const course = element(), windowFilter = element(), status = element(), focus = element(), sent = [];
  const elements = { 'history-course': course, 'history-window': windowFilter, 'history-action-status': status, 'history-review': focus };
  vm.runInNewContext(fs.readFileSync(path.resolve(__dirname, '../media/learningHistory.js'), 'utf8'), {
    acquireVsCodeApi: () => ({ postMessage: (message) => sent.push(message) }),
    document: { body: { dataset: { version: '12', focus: 'history-review' } }, getElementById: (id) => elements[id],
      querySelectorAll: (query) => query === 'button, select' ? [button, disabled, course, windowFilter] : [button, disabled] },
  });
  disabled.handle(); assert.equal(sent.length, 0);
  button.handle(); button.handle();
  assert.equal(sent.length, 1);
  assert.equal(sent[0].eventIndex, 1);
  assert.equal(sent[0].eventId, undefined);
  assert.equal(sent[0].version, 12);
  assert.equal(course.disabled, true);
  assert.equal(focus.scrolled, true);
  assert.equal(focus.focused, true);
  // A newly rendered page has a fresh listener and sends both filter values.
  course.value = 'b'; windowFilter.value = '30d';
  vm.runInNewContext(fs.readFileSync(path.resolve(__dirname, '../media/learningHistory.js'), 'utf8'), {
    acquireVsCodeApi: () => ({ postMessage: (message) => sent.push(message) }),
    document: { body: { dataset: { version: '13' } }, getElementById: (id) => elements[id],
      querySelectorAll: (query) => query === 'button, select' ? [button, course, windowFilter] : [] },
  });
  windowFilter.handle();
  assert.deepEqual({ ...sent.at(-1) }, { type: 'filter', courseId: 'b', window: '30d', version: 13 });
});

test('history command flushes tracker metadata before readonly RPC, and dashboard retains both history entry points', async () => {
  const { vscode, panels, commands } = fixture();
  const { registerCommands } = loadTs('commands.ts', { vscode });
  const operations = [];
  const bridge = { call: async (method, query) => {
    operations.push([method, query]);
    if (method === 'getLearningHistory') return history();
    if (method === 'getTaskHistory') return detail();
    if (method === 'getLearningReview') return review();
    if (method === 'getLearningTrend') return trend();
    if (method === 'getLearningDashboard') return { courses: [], selectedCourseId: '', resume: null, diagnosis: null, warnings: [], knowledgeComponents: {} };
    assert.fail(`unexpected mutation or RPC: ${method}`);
  } };
  const tracker = { flush: async () => operations.push(['flush']) };
  registerCommands({ extensionUri: 'extension', subscriptions: [], globalState: { update() {} } },
    bridge, {}, {}, {}, {}, {}, {}, {}, tracker);
  await commands.get('sparktutor.showLearningHistory')();
  assert.deepEqual(operations.map(([method]) => method), ['flush', 'getLearningHistory']);
  await receive(panels[0], { type: 'selectTask', taskIndex: 0 });
  assert.deepEqual(operations.slice(-2).map(([method]) => method), ['flush', 'getTaskHistory']);
  await receive(panels[0], { type: 'reviewEvent', eventIndex: 0 });
  assert.deepEqual(operations.slice(-2).map(([method]) => method), ['flush', 'getLearningReview']);
  await commands.get('sparktutor.openLearningDashboard')();
  assert.ok(panels[1].webview.html.includes('data-action="learningHistory"'));
  assert.ok(panels[1].webview.html.includes('data-action="history"'));
  await receive(panels[1], { type: 'learningHistory' });
  assert.deepEqual(operations.slice(-2).map(([method]) => method), ['flush', 'getLearningHistory']);
  const manifest = JSON.parse(fs.readFileSync(path.resolve(__dirname, '../package.json'), 'utf8'));
  assert.ok(manifest.contributes.commands.some((item) => item.command === 'sparktutor.showLearningHistory'));
  assert.ok(manifest.contributes.menus['view/title'].some((item) => item.command === 'sparktutor.showLearningHistory'));
});
