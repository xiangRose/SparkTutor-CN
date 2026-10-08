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

function diagnosis(overrides = {}) {
  return {
    diagnosis: '先通过变式练习巩固知识，再观察新的表现。',
    disclaimer: '这是形成性反馈，不是能力定论。', eventCount: 12, eligibleEventCount: 8,
    dimensions: [
      ['knowledge', '知识掌握度', 75], ['debugging', '调试自修复能力', 0],
      ['hint_dependency', '提示使用率（Hint Dependency）', 80], ['transfer', '知识迁移能力', null],
    ].map(([key, name, score]) => ({ key, name, score, confidence: 'limited',
      evidenceLevel: score === null ? 'none' : 'limited', evidenceCount: score === null ? 0 : 3,
      direction: key === 'hint_dependency' ? 'descriptive' : 'higher_is_better',
      summary: score === null ? '尚无迁移任务证据。' : '基于当前任务记录。',
      metrics: key === 'hint_dependency' ? { hintProductivity: 50 } : {},
    })),
    recommendedExercise: { courseId: 'learning_spark', lessonId: '02_dataframes_schemas',
      lessonIndex: 1, stepId: '19', title: 'DataFrame 变式练习', dimension: 'knowledge', reason: '通过新数据检验掌握情况。' },
    recommendationReason: '', ...overrides,
  };
}

function vscodeFixture() {
  const panels = [];
  const commands = new Map();
  const vscode = {
    Uri: { joinPath: (base, ...parts) => `${base}/${parts.join('/')}` }, ViewColumn: { One: 1, Two: 2 },
    commands: {
      registerCommand: (name, handler) => { commands.set(name, handler); return { dispose() {} }; },
      executeCommand: async () => {},
    },
    window: {
      createWebviewPanel: (_kind, _title, _column, options) => {
        const panel = {
          options, messages: [],
          webview: {
            html: '', cspSource: 'vscode-webview:', asWebviewUri: (uri) => uri,
            onDidReceiveMessage(handler) { panel.receive = handler; },
            postMessage(message) { panel.messages.push(message); },
          },
          onDidDispose(handler) { panel.didDispose = handler; },
          reveal() {}, dispose() { panel.didDispose(); },
        };
        panels.push(panel);
        return panel;
      },
      showErrorMessage: (message) => { throw new Error(message); },
    },
  };
  return { vscode, panels, commands };
}

const view = loadTs('diagnosisView.ts');

test('diagnosis distinguishes zero from missing evidence and keeps hint usage descriptive', () => {
  const html = view.diagnosisContent(diagnosis());
  assert.equal((html.match(/class="dimension"/g) || []).length, 4);
  assert.ok(html.includes('0.0<span> / 100</span>'));
  assert.ok(html.includes('80.0<span>%</span>'));
  assert.ok(html.includes('不参与能力强弱排序'));
  assert.ok(html.includes('证据量：少量证据'));
  assert.ok(!html.includes('置信度'));
  assert.ok(html.includes('证据不足'));
  assert.ok(html.includes('其中 8 条可用于本次诊断'));
  assert.ok(html.includes('打开这道推荐练习'));
  assert.ok(!html.includes('hintDependencyRisk'));
});

test('empty history offers clear next actions without fabricating scores or a recommendation', () => {
  const result = diagnosis({ eventCount: 0, eligibleEventCount: 0, recommendedExercise: null,
    recommendationReason: '完成一项练习后再生成推荐。' });
  result.dimensions.forEach((item) => { item.score = null; item.evidenceCount = 0; });
  const html = view.diagnosisContent(result);
  assert.ok(html.includes('还没有学习记录'));
  assert.ok(html.includes('完成一项练习后再生成推荐'));
  assert.ok(!html.includes('<progress'));
  assert.ok(!html.includes('id="open-exercise"'));
});

test('debugging and transfer descriptions include assisted success and keep hint effectiveness noncausal', () => {
  const html = view.diagnosisContent(diagnosis());
  assert.ok(html.includes('经有效评估确认修复的过程占比'));
  assert.ok(html.includes('提示辅助后的修复也计入'));
  assert.ok(html.includes('新情境任务首次有效评估通过率'));
  assert.ok(html.includes('提示辅助后的通过也计入'));
  assert.ok(html.includes('提示后出现有效通过的请求比例'));
  assert.ok(html.includes('时间关联，不代表因果'));
  assert.ok(!html.includes('独立完成的修复过程'));
  assert.ok(!html.includes('首次独立通过率'));
  assert.ok(!html.includes('无平台帮助'));
});

test('diagnosis and failure text cannot inject scripts or webview actions', () => {
  const payload = '<script>alert("x")</script>';
  const result = diagnosis({ diagnosis: payload });
  result.recommendedExercise.reason = payload;
  assert.ok(!view.diagnosisContent(result).includes('<script>'));
  assert.ok(view.diagnosisError(payload).includes('&lt;script&gt;'));
  assert.ok(view.diagnosisError(payload).includes('刷新诊断'));
});

test('panel refresh can recover from failure and rejects identifiers supplied by the webview', async () => {
  const { vscode, panels } = vscodeFixture();
  const { DiagnosisPanel } = loadTs('diagnosisPanel.ts', { vscode });
  const opened = [];
  let attempts = 0;
  const result = diagnosis();
  const panel = new DiagnosisPanel('extension', async () => {
    if (++attempts === 1) throw new Error('后端暂时不可用');
    return result;
  }, async (exercise) => { opened.push(exercise); });
  await panel.show();
  assert.ok(panels[0].webview.html.includes('后端暂时不可用'));
  assert.ok(!panels[0].webview.html.includes('id="refresh" disabled'));
  await panels[0].receive({ type: 'refresh' });
  assert.ok(panels[0].webview.html.includes('DataFrame 变式练习'));
  await panels[0].receive({ type: 'openExercise', courseId: '../invalid', stepId: 'wrong' });
  assert.equal(opened[0], result.recommendedExercise);
  assert.equal(panels[0].messages.at(-1).error, false);
});

test('panel ignores a superseded refresh and supports retrying a failed recommendation', async () => {
  const { vscode, panels } = vscodeFixture();
  const { DiagnosisPanel } = loadTs('diagnosisPanel.ts', { vscode });
  let resolveOld;
  let loads = 0;
  let opens = 0;
  const panel = new DiagnosisPanel('extension', () => {
    if (++loads === 1) return new Promise((resolve) => { resolveOld = resolve; });
    return Promise.resolve(diagnosis({ diagnosis: '最新诊断' }));
  }, async () => { if (++opens === 1) throw new Error('推荐已更新'); });
  const initial = panel.show();
  assert.ok(panels[0].webview.html.includes('正在整理学习记录'));
  await panels[0].receive({ type: 'refresh' });
  resolveOld(diagnosis({ diagnosis: '过期诊断' }));
  await initial;
  assert.ok(panels[0].webview.html.includes('最新诊断'));
  assert.ok(!panels[0].webview.html.includes('过期诊断'));
  await panels[0].receive({ type: 'openExercise' });
  assert.equal(panels[0].messages.at(-1).error, true);
  assert.match(panels[0].messages.at(-1).message, /推荐已更新/);
  await panels[0].receive({ type: 'openExercise' });
  assert.equal(panels[0].messages.at(-1).error, false);
});

test('recommendation saves current work and opens the exact backend step without reloading its lesson', async () => {
  const { vscode, panels, commands } = vscodeFixture();
  const { registerCommands } = loadTs('commands.ts', { vscode });
  const steps = [];
  const operations = [];
  const savedSessions = [];
  const finished = [];
  const notices = [];
  vscode.window.showInformationMessage = (message) => notices.push(message);
  const result = diagnosis();
  const step = { id: '19', cls: 'script', output: '完成具体推荐题' };
  const loaded = { step, courseId: 'learning_spark', lessonIdx: 1, depth: 'beginner', practiceMode: true,
    currentIndex: 11, totalSteps: 15, lessonTitle: 'DataFrame 与 Schema',
    lessonId: '02_dataframes_schemas', starterCode: 'start_here()', restoredCode: '' };
  const bridge = { async call(method, params) {
    operations.push({ method, params });
    if (method === 'getDiagnosis') return result;
    if (method === 'openRecommendedExercise') return loaded;
    if (method === 'advance') return { finished: true, practiceMode: true };
    assert.fail(`unexpected RPC: ${method}`);
  } };
  const workspace = {
    getCurrentCode() { return 'student_answer()'; },
    async saveCurrentExercise() { operations.push({ method: 'save' }); },
    async switchCourse(courseId) { operations.push({ method: 'switch', courseId }); },
    setStepType(cls) { operations.push({ method: 'stepType', cls }); },
    async openExercise(...args) { operations.push({ method: 'openFile', args }); },
  };
  registerCommands({ extensionUri: 'extension', subscriptions: [], globalState: { update(...args) { savedSessions.push(args); } } },
    bridge, { refresh() {} }, { updateStep(...args) { steps.push(args); }, showFinished(mode) { finished.push(mode); } }, workspace,
    { clear() {} }, { clear() {} }, { setStep() {}, setDepth() {} }, {});
  await commands.get('sparktutor.showLearningDiagnosis')();
  await panels[0].receive({ type: 'openExercise' });
  assert.deepEqual(operations.slice(0, 3).map((item) => item.method), ['getDiagnosis', 'save', 'openRecommendedExercise']);
  assert.deepEqual(operations[2].params, {
    courseId: 'learning_spark', lessonId: '02_dataframes_schemas', stepId: '19',
  });
  assert.equal(steps[0][0], step);
  assert.equal(steps[0][1], 11);
  assert.equal(steps[0][5], true);
  assert.equal(savedSessions.length, 0);
  assert.ok(!operations.some((item) => item.method === 'loadLesson'));
  assert.equal(operations.find((item) => item.method === 'openFile').args[3], 'start_here()');
  await commands.get('sparktutor.next')();
  assert.deepEqual(finished, [true]);
  assert.match(notices.at(-1), /推荐练习已完成/);
  assert.ok(!notices.at(-1).includes('完成本课程'));
});

test('diagnosis command is discoverable from the course sidebar', () => {
  const manifest = JSON.parse(fs.readFileSync(path.resolve(__dirname, '../package.json'), 'utf8'));
  assert.ok(manifest.contributes.commands.some((item) => item.command === 'sparktutor.showLearningDiagnosis'));
  assert.ok(manifest.contributes.menus['view/title'].some((item) =>
    item.command === 'sparktutor.showLearningDiagnosis' && item.when === 'view == sparktutorCourses'));
});

test('webview restores controls after a recommendation error and renders the error as text', () => {
  const elements = Object.fromEntries(['refresh', 'open-exercise', 'exercise-status'].map((id) => [id, {
    disabled: false, textContent: '', classList: { remove() {}, toggle() {} },
    addEventListener(_event, callback) { this.click = callback; },
  }]));
  const sent = [];
  let receive;
  vm.runInNewContext(fs.readFileSync(path.resolve(__dirname, '../media/diagnosis.js'), 'utf8'), {
    acquireVsCodeApi: () => ({ postMessage: (message) => sent.push(message) }),
    document: { getElementById: (id) => elements[id] },
    window: { addEventListener(_event, callback) { receive = callback; } },
  });
  elements['open-exercise'].click();
  assert.equal(sent[0].type, 'openExercise');
  assert.equal(elements.refresh.disabled, true);
  assert.equal(elements['open-exercise'].disabled, true);
  receive({ data: { type: 'exerciseStatus', error: true, message: '<script>invalid</script>' } });
  assert.equal(elements.refresh.disabled, false);
  assert.equal(elements['open-exercise'].disabled, false);
  assert.equal(elements['exercise-status'].textContent, '<script>invalid</script>');
  assert.equal(elements['exercise-status'].innerHTML, undefined);
});

test('completing a recommended practice shows its own completion state and diagnosis action', () => {
  let receive;
  const elements = {
    '.step-content': { innerHTML: '' }, '.actions': { style: {} }, '.nav-buttons': { style: {} },
  };
  vm.runInNewContext(fs.readFileSync(path.resolve(__dirname, '../media/lesson.js'), 'utf8'), {
    acquireVsCodeApi: () => ({ postMessage() {} }),
    document: { querySelectorAll: () => [], getElementById: () => null, querySelector: (selector) => elements[selector] },
    window: { addEventListener(_event, callback) { receive = callback; } },
  });
  receive({ data: { type: 'finished', practiceMode: true } });
  assert.ok(elements['.step-content'].innerHTML.includes('推荐练习已完成'));
  assert.ok(elements['.step-content'].innerHTML.includes('查看学习诊断'));
  assert.ok(!elements['.step-content'].innerHTML.includes('课程完成！'));
  assert.equal(elements['.nav-buttons'].style.display, 'none');
});
