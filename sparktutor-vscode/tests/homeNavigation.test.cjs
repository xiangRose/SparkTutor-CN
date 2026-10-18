const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const assert = require('node:assert/strict');
const test = require('node:test');
const esbuild = require('esbuild');

function loadTs(name, mocks) {
  const filename = path.resolve(__dirname, '../src', name);
  const { code } = esbuild.transformSync(fs.readFileSync(filename, 'utf8'), {
    loader: 'ts', format: 'cjs', target: 'node18',
  });
  const loaded = new Module(filename, module);
  loaded.paths = module.paths;
  loaded.require = (id) => {
    if (Object.hasOwn(mocks, id)) return mocks[id];
    throw new Error(`Unexpected runtime dependency: ${id}`);
  };
  loaded._compile(code, filename);
  return loaded.exports;
}

const flush = () => new Promise((resolve) => setImmediate(resolve));
const saved = { courseId: 'course', lessonIdx: 2, lessonTitle: '上次课程', depth: 'beginner' };
function activationFixture({ session, seen = false, choice, failHome = false, failStart = false } = {}) {
  const calls = [], updates = [], messages = [], logs = [];
  let cleared = false;
  const noop = () => {};
  class Bridge {
    async start() { if (failStart) throw new Error('backend unavailable'); }
    on() {}
    onNotification() {}
    async call() { return { mode: 'local' }; }
    dispose() {}
  }
  class Manager { dispose() {} }
  class Output extends Manager { show() {} appendLine(message) { logs.push(message); } }
  class Status extends Manager { setMode() {} setAiProvider() {} }
  class Router { async resolveProvider() { return 'none'; } async runStartupHealthCheck() {} }
  const vscode = {
    workspace: { onDidChangeTextDocument: () => ({ dispose: noop }) },
    window: {
      registerTreeDataProvider() {}, showErrorMessage() {},
      async showInformationMessage(...args) { messages.push(args); return choice; },
    },
    commands: { async executeCommand(...args) {
      calls.push(args);
      if (failHome && args[0] === 'sparktutor.openLearningDashboard') throw new Error('home unavailable');
    } },
  };
  const mocks = {
    vscode,
    './bridge': { Bridge },
    './aiRouter': { AiRouter: Router },
    './commands': {
      registerCommands() {}, getSavedSession: () => session,
      clearSavedSession() { cleared = true; },
    },
    './courseTree': { CourseTreeProvider: Manager },
    './diagnostics': { DiagnosticsManager: Manager },
    './lessonPanel': { LessonPanel: Manager },
    './outputChannel': { SparkOutputChannel: Output },
    './statusBar': { StatusBarManager: Status },
    './workspaceManager': { WorkspaceManager: Manager },
    './learningEventTracker': { LearningEventTracker: Manager, createTrackerShutdown: () => async () => {} },
  };
  const extension = loadTs('extension.ts', mocks);
  const state = new Map(seen ? [['sparktutor.learningHomeOpened', true]] : []);
  const context = { extensionPath: 'extension', extensionUri: 'extension', subscriptions: [], secrets: {},
    globalState: { get: (key) => state.get(key), async update(key, value) { state.set(key, value); updates.push([key, value]); } },
  };
  return { async activate() { await extension.activate(context); await flush(); }, calls, updates, messages, logs,
    state, wasCleared: () => cleared };
}
const homes = (fixture) => fixture.calls.filter(([command]) => command === 'sparktutor.openLearningDashboard');

test('the learning home opens once after successful activation without replacing later sessions', async () => {
  const fixture = activationFixture();
  await fixture.activate();
  assert.equal(homes(fixture).length, 1);
  assert.equal(fixture.state.get('sparktutor.learningHomeOpened'), true);
  await fixture.activate();
  assert.equal(homes(fixture).length, 1);
});

test('continuing a saved lesson takes priority over first-use home onboarding', async () => {
  const fixture = activationFixture({ session: saved, choice: '继续' });
  await fixture.activate();
  assert.equal(fixture.messages.length, 1);
  assert.deepEqual(fixture.calls.find(([command]) => command === 'sparktutor.openLesson'),
    ['sparktutor.openLesson', 'course', 2, 'beginner', true]);
  assert.equal(homes(fixture).length, 0);
  assert.equal(fixture.updates.length, 0);
  assert.equal(fixture.wasCleared(), false);
});

test('dismissing the resume prompt opens the first-use home while retaining the saved session', async () => {
  const fixture = activationFixture({ session: saved });
  await fixture.activate();
  assert.equal(homes(fixture).length, 1);
  assert.ok(!fixture.calls.some(([command]) => command === 'sparktutor.openLesson'));
  assert.equal(fixture.wasCleared(), false);
});

test('starting over clears the saved session and then opens the first-use home', async () => {
  const fixture = activationFixture({ session: saved, choice: '重新开始' });
  await fixture.activate();
  assert.equal(fixture.wasCleared(), true);
  assert.equal(homes(fixture).length, 1);
});

test('a returning learner retains the resume prompt without another automatic home', async () => {
  const fixture = activationFixture({ session: saved, seen: true });
  await fixture.activate();
  assert.equal(fixture.messages.length, 1);
  assert.equal(homes(fixture).length, 0);
});

test('failed activation or opening the home never marks onboarding as complete', async () => {
  const failedStart = activationFixture({ failStart: true });
  await failedStart.activate();
  assert.equal(homes(failedStart).length, 0);
  assert.equal(failedStart.updates.length, 0);
  const failedHome = activationFixture({ failHome: true });
  await failedHome.activate();
  assert.equal(failedHome.updates.length, 0);
  assert.ok(failedHome.logs.some((line) => line.includes('侧栏顶部')));
});

test('the course panel keeps a home route and explains when to use each help tool', () => {
  let panel;
  const vscode = {
    Uri: { joinPath: (base, ...parts) => `${base}/${parts.join('/')}` }, ViewColumn: { Two: 2 },
    window: { createWebviewPanel() {
      panel = { webview: { asWebviewUri: (uri) => uri, html: '',
        onDidReceiveMessage(callback) { panel.receive = callback; } }, onDidDispose() {} };
      return panel;
    } },
  };
  const source = esbuild.buildSync({ entryPoints: [path.resolve(__dirname, '../src/lessonPanel.ts')],
    bundle: true, platform: 'node', format: 'cjs', write: false, external: ['vscode'], logLevel: 'silent' }).outputFiles[0].text;
  const loaded = new Module(__filename, module);
  loaded.require = (id) => id === 'vscode' ? vscode : require(id);
  loaded._compile(source, __filename);
  const lesson = new loaded.exports.LessonPanel('extension');
  let homeCalls = 0;
  lesson.onHome = () => { homeCalls += 1; };
  lesson.updateStep({ cls: 'script', output: '练习题' }, 0, 3, '测试课程');
  assert.ok(panel.webview.html.includes('学习首页'));
  assert.ok(panel.webview.html.includes('卡住了？'));
  assert.ok(panel.webview.html.includes('课程提示'));
  assert.ok(panel.webview.html.includes('导师问答'));
  assert.match(panel.webview.html, /当前试点为 M&amp;M 糖果计数与传感器聚合.*两道练习/);
  assert.ok(panel.webview.html.includes('是否通过以<strong>提交判题</strong>的反馈为准'));
  panel.receive({ type: 'home' });
  assert.equal(homeCalls, 1);
});
