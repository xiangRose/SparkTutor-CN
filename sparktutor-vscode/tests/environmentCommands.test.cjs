const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const test = require('node:test');
const esbuild = require('esbuild');

/** Isolate both configuration and process.env: tests never read real credentials. */
function loadTs(name, mocks, environment, request) {
  const filename = path.resolve(__dirname, '../src', name);
  const { code } = esbuild.transformSync(fs.readFileSync(filename, 'utf8'), {
    loader: 'ts', format: 'cjs', target: 'node18',
  });
  const loaded = { exports: {} };
  const localRequire = (id) => {
    if (Object.hasOwn(mocks, id)) return mocks[id];
    throw new Error(`Unexpected runtime dependency: ${id}`);
  };
  new Function('require', 'module', 'exports', 'process', 'fetch', code)(
    localRequire, loaded, loaded.exports, { env: environment }, request,
  );
  return loaded.exports;
}
function backendReport() {
  return { schemaVersion: 1, staticOnly: true, sparkVerified: false,
    checks: [{ id: 'python', title: 'Python', status: 'available', summary: 'Python 已启动', details: [], nextSteps: [] }],
    capabilities: [], backend: { available: true, pythonPath: 'D:/test/python.exe' },
    execution: { configuredMode: 'local', localPrerequisites: false, summary: '需要配置 Spark', nextSteps: [] } };
}
function fixture(options = {}) {
  const values = { executionMode: 'local', pythonPath: 'D:/test/python.exe', aiProvider: 'auto',
    openaiBaseUrl: 'https://api.openai.com/v1', openaiModel: 'gpt-4o-mini', ...options.values };
  const explicit = options.explicit || {};
  const registered = new Map(), calls = [], bridgeCalls = [], secretReads = [], modelRequests = [], messages = [];
  let ready = options.ready ?? true, alive = options.alive ?? true, diagnostics = 0, coachConfigurations = 0;
  const secretStore = { get: async (key) => { secretReads.push(key); return options.secret; } };
  const context = { extensionUri: 'test-extension-uri', extensionPath: 'D:/test/extension',
    extension: { packageJSON: { version: '0.3.0-test' } }, subscriptions: [], secrets: secretStore };
  const vscode = {
    workspace: { getConfiguration: () => ({ get: (key) => values[key], inspect: (key) => ({ globalValue: explicit[key] }) }) },
    extensions: { getExtension: (id) => options.copilot && id === 'GitHub.copilot-chat' ? { id } : undefined },
    window: { showInformationMessage: async (...args) => { messages.push(args); } },
    commands: {
      registerCommand(id, action) { registered.set(id, action); return { id, dispose() {} }; },
      async executeCommand(...args) {
        calls.push(args);
        if (args[0] === 'sparktutor.checkAiConnection' && options.aiRegistered === false) {
          throw new Error('AI command is not registered while startup is incomplete');
        }
      },
    },
  };
  const request = async (...args) => {
    modelRequests.push(args);
    throw new Error('Environment configuration inspection must not request a model');
  };
  const provider = loadTs('openaiProvider.ts', { vscode }, options.environment || {}, request);
  class EnvironmentPanel {
    constructor(extensionUri, load, action) { this.extensionUri = extensionUri; this.load = load; this.action = action; this.opens = 0; }
    async show() { this.opens += 1; this.report = await this.load(); }
    dispose() {}
  }
  const { registerEnvironmentCheck } = loadTs('environmentCommands.ts', {
    vscode, './environmentPanel': { EnvironmentPanel }, './openaiProvider': provider,
    './buildInfo': { extensionBuild: '2026-10-21T00:00:00.000Z' },
    './coachModel': { async configureCoachModel(secrets) {
      assert.equal(secrets, secretStore); coachConfigurations += 1;
    } },
  }, options.environment || {}, request);
  const bridge = { isAlive: () => alive, async call(...args) {
    bridgeCalls.push(args);
    if (options.failBackend) throw new Error('MOCK_BACKEND_FAILURE');
    return backendReport();
  } };
  const panel = registerEnvironmentCheck(context, bridge, () => { diagnostics += 1; }, () => ready);
  return { panel, context, registered, calls, bridgeCalls, secretReads, modelRequests, messages,
    diagnostics: () => diagnostics, coachConfigurations: () => coachConfigurations,
    setReady: (value) => { ready = value; }, setAlive: (value) => { alive = value; } };
}

test('registration exposes environment setup before any backend or model request', async () => {
  const f = fixture({ ready: false, alive: false });
  assert.deepEqual([...f.registered.keys()], ['sparktutor.checkEnvironment']);
  assert.equal(f.context.subscriptions[0], f.panel);
  assert.equal(f.context.subscriptions[1].id, 'sparktutor.checkEnvironment');
  assert.deepEqual(f.bridgeCalls, []);
  assert.deepEqual(f.secretReads, []);
  assert.deepEqual(f.modelRequests, []);
  await f.registered.get('sparktutor.checkEnvironment')();
  assert.equal(f.panel.opens, 1);
  assert.equal(f.panel.report.backend.available, false);
  assert.equal(f.panel.report.backend.pythonPath, 'D:/test/python.exe');
});

test('a spawned backend without its ready handshake gets an immediate offline report and no queued RPC', async () => {
  const f = fixture({ ready: false, alive: true });
  let report = await f.panel.load();
  assert.equal(report.backend.available, false);
  assert.equal(report.execution.configuredMode, 'local');
  assert.ok(report.execution.nextSteps.some((step) => step.includes('Python')));
  assert.deepEqual(f.bridgeCalls, []);
  f.setReady(true);
  report = await f.panel.load();
  assert.equal(report.backend.available, true);
  assert.deepEqual(f.bridgeCalls, [['getEnvironmentReport', {}, 15000]]);
  assert.deepEqual(f.modelRequests, []);
});

test('a formerly ready but closed bridge also stays offline without probing the backend', async () => {
  const f = fixture({ ready: true, alive: false });
  const report = await f.panel.load();
  assert.equal(report.backend.available, false);
  assert.deepEqual(f.bridgeCalls, []);
  assert.deepEqual(f.modelRequests, []);
});

test('AI state contains only configuration booleans and fixed summaries, never secret values or model traffic', async () => {
  const f = fixture({ secret: 'MOCK_SECRET_KEY_NEVER_RENDER', values: { aiProvider: 'openai-compatible' },
    explicit: { openaiBaseUrl: 'https://model.invalid/v1', openaiModel: 'mock-model' },
    environment: { ANTHROPIC_API_KEY: 'MOCK_ANTHROPIC_SECRET_NEVER_RENDER' } });
  const report = await f.panel.load();
  assert.deepEqual(Object.keys(report.ai).sort(), ['configured', 'provider', 'summary']);
  assert.equal(report.ai.configured, true);
  assert.equal(report.ai.provider, 'openai-compatible');
  assert.ok(report.ai.summary.includes('调试教练已明确配置'));
  assert.ok(report.ai.summary.includes('尚未验证'));
  const serialized = JSON.stringify(report);
  for (const privateValue of ['MOCK_SECRET_KEY', 'MOCK_ANTHROPIC_SECRET', 'https://model.invalid', 'mock-model']) {
    assert.ok(!serialized.includes(privateValue));
  }
  assert.deepEqual(f.secretReads, ['sparktutor.openaiApiKey']);
  assert.deepEqual(f.modelRequests, []);
  assert.deepEqual(f.bridgeCalls, [['getEnvironmentReport', {}, 15000]]);
  const extension = report.checks.find((check) => check.id === 'extension');
  assert.ok(extension.summary.includes('0.3.0-test'));
  assert.deepEqual(extension.details, ['当前扩展目录：D:/test/extension', '当前加载构建（UTC）：2026-10-21T00:00:00.000Z']);
});

test('defaults plus a key may configure a tutor but do not imply explicit coach consent', async () => {
  const f = fixture({ secret: 'MOCK_KEY', values: { aiProvider: 'openai-compatible' } });
  const report = await f.panel.load();
  assert.equal(report.ai.configured, true);
  assert.ok(report.ai.summary.includes('调试教练需明确填写'));
  assert.deepEqual(f.modelRequests, []);
});

test('AI summaries respect the selected provider without asserting a Copilot login or response', async (t) => {
  const cases = [
    { selected: 'auto', environment: {}, configured: false },
    { selected: 'auto', environment: { ANTHROPIC_API_KEY: 'MOCK_KEY' }, configured: true },
    { selected: 'anthropic', secret: 'MOCK_COMPATIBLE_KEY', configured: false },
    { selected: 'anthropic', environment: { ANTHROPIC_API_KEY: 'MOCK_KEY' }, configured: true },
    { selected: 'openai-compatible', environment: { ANTHROPIC_API_KEY: 'MOCK_KEY' }, configured: false },
    { selected: 'copilot', copilot: true, configured: true },
    { selected: 'copilot', copilot: false, secret: 'MOCK_COMPATIBLE_KEY', configured: false },
  ];
  for (const item of cases) {
    await t.test(`${item.selected}:${item.configured}`, async () => {
      const f = fixture({ ...item, values: { aiProvider: item.selected } });
      const report = await f.panel.load();
      assert.equal(report.ai.configured, item.configured);
      assert.ok(report.ai.summary.includes('Copilot 是否已登录或授权仍需实际检查连接'));
      assert.deepEqual(f.modelRequests, []);
    });
  }
});

test('offline connection checks explain recovery instead of calling commands not yet registered', async () => {
  for (const startup of [{ ready: false, alive: false }, { ready: false, alive: true }, { ready: true, alive: false }]) {
    const f = fixture({ ...startup, aiRegistered: false });
    await f.panel.action('checkAi');
    assert.deepEqual(f.calls, []);
    assert.deepEqual(f.bridgeCalls, []);
    assert.equal(f.messages.length, 1);
    assert.ok(f.messages[0][0].includes('Python'));
    assert.deepEqual(f.modelRequests, []);
  }
  const live = fixture();
  await live.panel.action('checkAi');
  assert.deepEqual(live.calls, [['sparktutor.checkAiConnection']]);
});

test('setup callbacks route only to fixed settings, explicit configuration, reload, and diagnostics', async () => {
  const f = fixture({ ready: false, alive: false });
  for (const id of ['pythonSettings', 'modeSettings', 'aiSettings', 'configureCoach', 'reload', 'diagnostics']) {
    await f.panel.action(id);
  }
  assert.deepEqual(f.calls, [
    ['workbench.action.openSettings', 'sparktutor.pythonPath'],
    ['workbench.action.openSettings', 'sparktutor.executionMode'],
    ['workbench.action.openSettings', 'sparktutor.aiProvider'],
    ['workbench.action.reloadWindow'],
  ]);
  assert.equal(f.coachConfigurations(), 1);
  assert.equal(f.diagnostics(), 1);
  assert.deepEqual(f.bridgeCalls, []);
  assert.deepEqual(f.modelRequests, []);
  assert.deepEqual(f.secretReads, []);
});
