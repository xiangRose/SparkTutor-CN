const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const test = require('node:test');
const esbuild = require('esbuild');

function loadTs(name, mocks = {}) {
  const filename = path.resolve(__dirname, '../src', name);
  const { outputFiles } = esbuild.buildSync({ entryPoints: [filename], bundle: true, platform: 'node', format: 'cjs',
    write: false, external: ['vscode'], logLevel: 'silent' });
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
const selection = { mode: 'recorded', scenarioId: 'transfer_retry' };
function report(selected = selection) {
  const task = (name) => ({ courseId: 'demo', lessonId: 'lesson', stepId: name, taskId: `lesson:${name}`, title: `演示题 ${name}`, context: `${name} 的不同数据情境` });
  const diagnosis = (transfer) => ({ diagnosis: '先观察首次迁移表现，再回看修复过程。', eventCount: 5, eligibleEventCount: 3,
    dimensions: [['knowledge', 100], ['debugging', 100], ['hint_dependency', 50], ['transfer', transfer]].map(([key, score]) => ({
      key, name: key, score, direction: key === 'hint_dependency' ? 'descriptive' : 'higher_is_better', evidenceCount: score === null ? 0 : 1,
      summary: '演示证据', metrics: { evaluatedTasks: 1, latestPassedTasks: 1, failureEpisodes: 1, confirmedRepairs: 1,
        hintedBeforeFirstAssessment: 1, eligibleTransferTasks: score === null ? 0 : 1, firstPassedTasks: score === 100 ? 1 : 0 },
    })), recommendedExercise: { courseId: 'demo', lessonId: 'lesson', stepId: 'b', title: '核查用的单条推荐', reason: '根据演示证据推荐。' },
    recommendationReason: '', disclaimer: '这是合成轨迹或预设脚本证据，不是真实学习成绩。' });
  const event = { eventId: 'e-demo', eventType: 'code_submit', passed: false, rawPassed: false, mode: 'local',
    reason: '课程测试未通过。', answerInfluenced: false, data: { code: 'SECRET_SOURCE', filePath: '/private/demo.py' } };
  return { schemaVersion: 1, isDemo: true, isolated: true, ...selected, title: '独立迁移诊断演示',
    provenanceLabel: selected.mode === 'recorded' ? '合成轨迹' : '真实 Spark 预设脚本', disclaimer: '不计入正常学习数据。',
    taskA: task('a'), taskB: task('b'), eventCount: 5, startedAt: '2026-10-10T00:00:00Z', finishedAt: '2026-10-10T00:01:00Z',
    sparkVerified: selected.mode === 'spark', checks: [{ id: 'isolated', label: '使用独立存储', passed: true }],
    stages: [{ id: 'source', title: '来源题通过', explanation: '先建立来源证据。', diagnosis: diagnosis(null), review: null, events: [], checks: [] },
      { id: 'transfer', title: '迁移题首次表现', explanation: '修复不改写第一次评估。',
        diagnosis: diagnosis(selected.scenarioId === 'transfer_retry' ? 0 : 100), review: null, events: [event],
        checks: [{ id: 'first', label: '检查迁移首次表现', passed: true }, { id: 'example-fail', label: '示例未满足的断言', passed: false }] }] };
}
const viewState = (overrides = {}) => ({ selection, report: null, stageIndex: 0, running: false, detachedRun: false,
  progress: null, error: '', notice: '', ...overrides });
function fixture(run) {
  const panels = [], commands = new Map(), calls = [];
  const vscode = { Uri: { joinPath: (base, ...parts) => `${base}/${parts.join('/')}` }, ViewColumn: { One: 1 },
    commands: { registerCommand: (name, callback) => { commands.set(name, callback); return { dispose() {} }; }, executeCommand: async () => {} },
    window: { createWebviewPanel: () => {
      const panel = { webview: { html: '', cspSource: 'vscode:', asWebviewUri: (uri) => uri,
        onDidReceiveMessage(callback) { panel.receive = callback; }, postMessage() {} },
        onDidDispose(callback) { panel.didDispose = callback; }, reveal() {}, dispose() { panel.didDispose(); } };
      panels.push(panel); return panel;
    } } };
  const { DemoPanel } = loadTs('demoPanel.ts', { vscode });
  const demo = new DemoPanel('extension', async (selected) => {
    calls.push(selected);
    const result = run ? await run(selected) : report(selected);
    return { ...result, runId: result.runId ?? selected.runId };
  });
  return { demo, panels, calls, vscode, commands };
}
const version = (panel) => Number(panel.webview.html.match(/data-version="(\d+)"/)[1]);
const receive = (panel, message) => panel.receive({ version: version(panel), ...message });
const { demoContent } = loadTs('demoView.ts');

test('an unopened demo has no results and distinguishes synthetic expectations from actual measurements', () => {
  const html = demoContent(viewState());
  assert.ok(html.includes('尚未运行演示，没有预填成绩或验证结果'));
  assert.ok(html.includes('这不是实际学习成绩、实际运行结果或实验效果'));
  assert.ok(html.includes('场景预期（以运行后的核查为准）'));
  assert.ok(!html.includes('class="demo-report"'));
  assert.ok(!html.includes('核查用的单条推荐'));
});

test('stage reports show a measured transfer zero, truthful failed checks, and one non-navigable recommendation', () => {
  const html = demoContent(viewState({ report: report(), stageIndex: 1 }));
  assert.ok(html.includes('0.0<span>%</span>'));
  assert.ok(html.includes('本次计数：0 / 1'));
  assert.ok(html.includes('合成事件 · 未执行 Spark'));
  assert.equal((html.match(/核查用的单条推荐/g) || []).length, 1);
  assert.ok(!html.includes('data-action="openRecommendation"'));
  assert.ok(!html.includes('id="open-exercise"'));
  assert.ok(html.includes('不符合预期'));
  assert.ok(html.includes('1 / 2 项符合预期'));
  assert.ok(!html.includes('SECRET_SOURCE'));
  assert.ok(!html.includes('/private/demo.py'));
});

test('Spark provenance states preset-script limits and independent first-pass trajectories stay distinct', () => {
  const selected = { mode: 'spark', scenarioId: 'transfer_first_pass' };
  const html = demoContent(viewState({ selection: selected, report: report(selected), stageIndex: 1 }));
  assert.ok(html.includes('需要 PySpark 与 Java'));
  assert.ok(html.includes('最多等待 10 分钟'));
  assert.ok(html.includes('这不是学习者自主学习过程'));
  assert.ok(html.includes('不把上一条轨迹的重试伪装成新迁移'));
  assert.ok(html.includes('已完成预设 Spark 测试验证'));
});

test('all user-facing report, event, context and check text is escaped', () => {
  const result = report(), payload = '<img src=x onerror="bad()">';
  result.taskA.context = payload; result.title = payload; result.stages[1].events[0].reason = payload;
  result.stages[1].checks[0].label = payload; result.stages[1].diagnosis.diagnosis = payload;
  const html = demoContent(viewState({ report: result, stageIndex: 1 }));
  assert.ok(!html.includes('<img'));
  assert.ok(html.includes('&lt;img'));
});

test('opening and choosing do not run; the explicit run uses extension-owned validated selection', async () => {
  const { demo, panels, calls } = fixture();
  demo.show(); assert.equal(calls.length, 0);
  await receive(panels[0], { type: 'selection', scenarioId: 'transfer_first_pass', mode: 'recorded' });
  assert.equal(calls.length, 0);
  await receive(panels[0], { type: 'selection', scenarioId: 'injected', mode: 'spark' });
  await receive(panels[0], { type: 'run', scenarioId: 'injected', mode: 'spark' });
  assert.equal(calls.length, 1);
  assert.equal(calls[0].scenarioId, 'transfer_first_pass');
  assert.equal(calls[0].mode, 'recorded');
  assert.match(calls[0].runId, /^[a-f0-9-]{36}$/);
  await receive(panels[0], { type: 'stage', stageIndex: 1, score: 999 });
  assert.ok(panels[0].webview.html.includes('阶段 2 / 2'));
  assert.equal(calls.length, 1);
  const html = panels[0].webview.html;
  await receive(panels[0], { type: 'stage', stageIndex: 99 });
  await receive(panels[0], { type: 'run', version: -1 });
  assert.equal(panels[0].webview.html, html);
  assert.equal(calls.length, 1);
  await receive(panels[0], { type: 'selection', scenarioId: 'transfer_retry', mode: 'recorded' });
  assert.ok(!panels[0].webview.html.includes('class="demo-report"'));
});

test('run locks reject duplicates and selection changes, while progress is escaped metadata only', async () => {
  const waiting = deferred();
  const { demo, panels, calls } = fixture(() => waiting.promise);
  demo.show();
  const running = receive(panels[0], { type: 'run' });
  await receive(panels[0], { type: 'run' });
  await receive(panels[0], { type: 'selection', mode: 'spark', scenarioId: 'transfer_first_pass' });
  assert.equal(calls.length, 1);
  demo.updateProgress({ runId: calls[0].runId, id: 'source', title: '<script>progress</script>', index: 1, total: 2, code: 'SECRET_PROGRESS' });
  assert.ok(panels[0].webview.html.includes('&lt;script&gt;progress'));
  assert.ok(!panels[0].webview.html.includes('SECRET_PROGRESS'));
  const html = panels[0].webview.html;
  demo.updateProgress({ runId: calls[0].runId, id: 'bad', title: 'bad', index: -1, total: 2 });
  assert.equal(panels[0].webview.html, html);
  waiting.resolve(report()); await running;
  assert.ok(panels[0].webview.html.includes('class="demo-report"'));
});

test('closing and reopening does not cancel or duplicate the old run, and old notifications or reports stay hidden', async () => {
  const old = deferred(); let runs = 0;
  const { demo, panels, calls } = fixture((selected) => ++runs === 1 ? old.promise : report(selected));
  demo.show();
  const pending = receive(panels[0], { type: 'run' });
  panels[0].dispose(); demo.show();
  assert.ok(panels[1].webview.html.includes('先前演示请求仍在执行'));
  await receive(panels[1], { type: 'run' });
  assert.equal(calls.length, 1);
  demo.updateProgress({ runId: calls[0].runId, id: 'old', title: '旧进度不应出现', index: 1, total: 2 });
  assert.ok(!panels[1].webview.html.includes('旧进度不应出现'));
  old.resolve({ ...report(), title: '旧报告不应出现' }); await pending;
  assert.ok(!panels[1].webview.html.includes('旧报告不应出现'));
  assert.ok(panels[1].webview.html.includes('先前演示请求已结束'));
  await receive(panels[1], { type: 'run' });
  assert.equal(calls.length, 2);
  assert.ok(panels[1].webview.html.includes('class="demo-report"'));
});

test('failed or mismatched reports never display a verification result and retry can succeed without leaking errors', async () => {
  let count = 0;
  const { demo, panels } = fixture(() => {
    if (++count === 1) throw new Error('secret-key stderr /private/script.py');
    if (count === 2) return { ...report(), sparkVerified: true };
    return report();
  });
  demo.show();
  await receive(panels[0], { type: 'run' });
  assert.ok(panels[0].webview.html.includes('本次未收到可核查的完整报告'));
  assert.ok(!panels[0].webview.html.includes('secret-key'));
  assert.ok(!panels[0].webview.html.includes('/private'));
  await receive(panels[0], { type: 'run' });
  assert.ok(!panels[0].webview.html.includes('class="demo-report"'));
  await receive(panels[0], { type: 'run' });
  assert.ok(panels[0].webview.html.includes('class="demo-report"'));
});

test('a retry has a new run ID and discards progress and a report associated with the timed-out run', async () => {
  const first = deferred(), second = deferred(); let count = 0;
  const { demo, panels, calls } = fixture((selected) => ++count === 1 ? first.promise : count === 2 ? second.promise : report(selected));
  demo.show();
  const initial = receive(panels[0], { type: 'run' });
  first.reject(new Error('request timed out')); await initial;
  const retry = receive(panels[0], { type: 'run' });
  assert.notEqual(calls[0].runId, calls[1].runId);
  demo.updateProgress({ runId: calls[0].runId, id: 'stale', title: '旧请求进度', index: 1, total: 2 });
  assert.ok(!panels[0].webview.html.includes('旧请求进度'));
  demo.updateProgress({ runId: calls[1].runId, id: 'new', title: '新请求进度', index: 1, total: 2 });
  assert.ok(panels[0].webview.html.includes('新请求进度'));
  second.resolve({ ...report(), runId: calls[0].runId }); await retry;
  assert.ok(!panels[0].webview.html.includes('class="demo-report"'));
  assert.ok(panels[0].webview.html.includes('本次未收到可核查的完整报告'));
  await receive(panels[0], { type: 'run' });
  assert.ok(panels[0].webview.html.includes('class="demo-report"'));
});

test('actual demo script sends one explicit run action and selectors only request a selection change', () => {
  const element = (dataset = {}) => ({ dataset, value: '', disabled: false, textContent: '',
    addEventListener(_event, callback) { this.handle = callback; } });
  const button = element({ action: 'run' }), mode = element(), scenario = element(), status = element();
  mode.value = 'spark'; scenario.value = 'transfer_retry';
  const sent = [];
  const execute = () => vm.runInNewContext(fs.readFileSync(path.resolve(__dirname, '../media/demo.js'), 'utf8'), {
    acquireVsCodeApi: () => ({ postMessage: (message) => sent.push(message) }),
    document: { body: { dataset: { version: '7' } },
      getElementById: (id) => ({ 'demo-mode': mode, 'demo-scenario': scenario, 'demo-status': status })[id],
      querySelectorAll: (query) => query === 'button, select' ? [button, mode, scenario] : [button] },
  });
  execute(); assert.equal(sent.length, 0);
  button.handle(); button.handle();
  assert.deepEqual({ ...sent[0] }, { type: 'run', version: 7 });
  assert.equal(sent.length, 1);
  assert.equal(mode.disabled, true);
  mode.disabled = false; scenario.disabled = false; button.disabled = false;
  execute(); mode.handle();
  assert.deepEqual({ ...sent[1] }, { type: 'selection', mode: 'spark', scenarioId: 'transfer_retry', version: 7 });
});

test('command and homepage open an idle demo, and execution uses only the isolated RPC with a 600-second timeout', async () => {
  const { vscode, panels, commands } = fixture();
  const { registerCommands } = loadTs('commands.ts', { vscode });
  const operations = [], handlers = new Map(), disposables = [];
  const bridge = { onNotification: (method, handler) => handlers.set(method, handler), off: (name) => operations.push(['off', name]),
    call: async (method, params, timeout) => {
      operations.push([method, params, timeout]);
      if (method === 'runDemoScenario') return report(params);
      if (method === 'getLearningDashboard') return { courses: [], selectedCourseId: '', resume: null, diagnosis: null, knowledgeComponents: {} };
      assert.fail(`unexpected normal learning mutation: ${method}`);
    } };
  registerCommands({ extensionUri: 'extension', subscriptions: disposables, globalState: { update() { assert.fail('No progress writes'); } } },
    bridge, {}, {}, {}, {}, {}, {}, {}, { flush: async () => operations.push(['flush']) });
  await commands.get('sparktutor.openDiagnosisDemo')();
  assert.equal(operations.length, 0);
  await receive(panels[0], { type: 'run', mode: 'spark' });
  assert.equal(operations[0][0], 'runDemoScenario');
  assert.equal(operations[0][1].mode, selection.mode);
  assert.equal(operations[0][1].scenarioId, selection.scenarioId);
  assert.match(operations[0][1].runId, /^[a-f0-9-]{36}$/);
  assert.equal(operations[0][2], 600000);
  await commands.get('sparktutor.openLearningDashboard')();
  const before = operations.length;
  await receive(panels[1], { type: 'demo' });
  assert.equal(operations.length, before);
  assert.ok(handlers.has('demoProgress'));
  for (const disposable of disposables) disposable.dispose();
  assert.ok(operations.some(([name, method]) => name === 'off' && method === 'notification:demoProgress'));
  const manifest = JSON.parse(fs.readFileSync(path.resolve(__dirname, '../package.json'), 'utf8'));
  assert.ok(manifest.contributes.commands.some((item) => item.command === 'sparktutor.openDiagnosisDemo' && item.title === 'SparkTutor：打开诊断演示'));
  assert.ok(manifest.contributes.menus['view/title'].some((item) => item.command === 'sparktutor.openDiagnosisDemo'));
});
