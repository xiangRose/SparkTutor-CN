const path = require('node:path');
const Module = require('node:module');
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
const report = () => ({ schemaVersion: 1, staticOnly: true, sparkVerified: false,
  backend: { available: true, pythonPath: 'D:/venv/python.exe' },
  checks: [{ id: 'python', title: 'Python', status: 'available', summary: '已启动后端', details: ['D:/venv/python.exe'], nextSteps: [] }],
  execution: { configuredMode: 'local', localPrerequisites: true, summary: '尚未运行 Spark', nextSteps: ['运行一题验证'] },
  capabilities: [{ id: 'spark', title: '真实 Spark 综合题', availability: 'unverified', detail: '需要实际运行验证' }],
  ai: { provider: 'auto', configured: false, summary: '尚未配置模型' } });
const state = (overrides = {}) => ({ report: report(), loading: false, busy: false, error: '', notice: '', ...overrides });
const { environmentContent } = loadTs('environmentView.ts');

function fixture(load = async () => report(), action = async () => {}) {
  const panels = [], actions = [];
  const vscode = { Uri: { joinPath: (base, ...parts) => `${base}/${parts.join('/')}` }, ViewColumn: { One: 1 },
    window: { createWebviewPanel: () => {
      const panel = { webview: { html: '', cspSource: 'vscode:', asWebviewUri: (uri) => uri,
        onDidReceiveMessage(callback) { panel.receive = callback; } },
        onDidDispose(callback) { panel.didDispose = callback; }, reveal() {}, dispose() { panel.didDispose(); } };
      panels.push(panel); return panel;
    } } };
  const { EnvironmentPanel } = loadTs('environmentPanel.ts', { vscode });
  const environment = new EnvironmentPanel('extension', load, async (id) => { actions.push(id); await action(id); });
  return { environment, panels, actions };
}
const version = (panel) => Number(panel.webview.html.match(/data-version="(\d+)"/)[1]);
const receive = (panel, message) => panel.receive({ version: version(panel), ...message });

test('environment distinguishes found prerequisites, actual Spark execution, and AI configuration', () => {
  const html = environmentContent(state());
  assert.ok(html.includes('只读检查 · 未运行 Spark'));
  assert.ok(html.includes('需要实际运行验证'));
  assert.ok(html.includes('尚未配置模型'));
  assert.ok(html.includes('找到安装项不等于真实 Spark 执行已通过'));
  assert.ok(!html.includes('已验证 Spark'));
  const data = report(); data.ai.configured = true;
  assert.ok(environmentContent(state({ report: data })).includes('连接与输出质量仍需单独验证'));
});

test('offline failures retain practical setup, diagnostic, and AI buttons without echoing errors', async () => {
  const { environment, panels, actions } = fixture(async () => { throw new Error('SECRET_KEY /private/home/user.py'); });
  await environment.show();
  const html = panels[0].webview.html;
  assert.ok(html.includes('Python 后端尚未就绪'));
  for (const id of ['pythonSettings', 'modeSettings', 'reload', 'diagnostics', 'configureCoach', 'checkAi']) {
    assert.ok(html.includes(`data-action="${id}"`));
  }
  assert.ok(!html.includes('SECRET_KEY') && !html.includes('/private/home'));
  assert.ok(html.includes("default-src 'none'"));
  assert.ok(!html.includes('onclick='));
  await receive(panels[0], { type: 'pythonSettings', path: 'untrusted.exe' });
  assert.deepEqual(actions, ['pythonSettings']);
});

test('all dynamic environment report fields are HTML escaped and unknown fields stay hidden', () => {
  const data = report(), attack = '<img src=x onerror="bad()">';
  data.checks[0].title = data.checks[0].summary = attack;
  data.checks[0].details = data.checks[0].nextSteps = [attack];
  data.execution.configuredMode = data.execution.summary = attack;
  data.capabilities[0].title = data.capabilities[0].detail = attack;
  data.ai.provider = data.ai.summary = attack;
  data.apiKey = 'SECRET_KEY'; data.code = 'PRIVATE_SOURCE';
  const html = environmentContent(state({ report: data }));
  assert.ok(!html.includes('<img'));
  assert.ok(html.includes('&lt;img'));
  assert.ok(!html.includes('SECRET_KEY') && !html.includes('PRIVATE_SOURCE'));
});

test('actions accept only known IDs from the current version and reject duplicate callbacks', async () => {
  const pending = deferred();
  const { environment, panels, actions } = fixture(undefined, () => pending.promise);
  await environment.show();
  const oldVersion = version(panels[0]);
  await receive(panels[0], { type: 'executeCommand', command: 'delete' });
  await receive(panels[0], { type: 'reload', version: -1 });
  assert.deepEqual(actions, []);
  const performing = receive(panels[0], { type: 'configureCoach' });
  await receive(panels[0], { type: 'checkAi' });
  assert.deepEqual(actions, ['configureCoach']);
  pending.resolve(); await performing;
  await receive(panels[0], { type: 'checkAi', version: oldVersion });
  assert.deepEqual(actions, ['configureCoach']);
});

test('late reports from superseded refreshes or closed panels never replace current state', async () => {
  const old = deferred(), fresh = deferred(); let count = 0;
  const { environment, panels } = fixture(() => ++count === 1 ? old.promise : fresh.promise);
  const first = environment.show();
  panels[0].dispose();
  const second = environment.show();
  const oldData = report(); oldData.execution.summary = 'OLD_REPORT';
  old.resolve(oldData); await first;
  assert.ok(!panels[1].webview.html.includes('OLD_REPORT'));
  const newData = report(); newData.execution.summary = 'NEW_REPORT';
  fresh.resolve(newData); await second;
  assert.ok(panels[1].webview.html.includes('NEW_REPORT'));
});

test('reports claiming Spark verification are rejected; a subsequent refresh recovers', async () => {
  let count = 0;
  const { environment, panels } = fixture(async () => ++count === 1 ? { ...report(), sparkVerified: true } : report());
  await environment.show();
  assert.ok(panels[0].webview.html.includes('检查未完成'));
  assert.ok(!panels[0].webview.html.includes('真实 Spark 综合题'));
  await receive(panels[0], { type: 'refresh' });
  assert.ok(panels[0].webview.html.includes('真实 Spark 综合题'));
});
