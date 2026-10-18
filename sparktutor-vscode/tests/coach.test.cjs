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
const task = { courseId: 'course', lessonId: 'lesson', stepId: '7', taskId: 'lesson:7', title: '传感器聚合练习' };
const evidence = [{ id: 'evidence-1', title: '最近有效评估', description: '有效课程测试尚未通过。' }];
const state = (overrides = {}) => ({ available: true, reason: '', task, lastLevel: 0, maxLevel: 1,
  evidence, answerViewed: false, exerciseOptions: [task], dimensions:
    ['knowledge', 'debugging', 'hint_dependency', 'transfer'].map((key, index) => ({ key, name: key,
      score: index ? null : 0, numerator: 0, denominator: index ? 0 : 1, sampleSize: index ? 0 : 1,
      direction: key === 'hint_dependency' ? 'descriptive' : 'higher_is_better', metrics: {} })), ...overrides });
const message = (overrides = {}) => ({ messageId: 'message-1', requestId: 'request-1', level: 1, task,
  hypothesisId: 'count-vs-sum', hypothesis: '可能混淆了行数与数量总和。', question: '两条记录的数量都为三时，应该统计什么？',
  guidance: '先区分每条记录和记录中的数量字段。', reflection: '同样的判断能迁移到传感器数据吗？',
  evidence, probe: { values: [3, 3] }, answerViewed: false, disclaimer: '假设需要通过实际课程测试核查。', ...overrides });
const probe = { verified: true, engine: 'python-reference', values: [3, 3], rowCount: 2, quantitySum: 6, disclaimer: '仅参考模板计算。' };

function fixture(overrides = {}) {
  const panels = [], commands = new Map(), calls = [];
  const vscode = { Uri: { joinPath: (base, ...parts) => `${base}/${parts.join('/')}` }, ViewColumn: { One: 1, Two: 2 },
    commands: { registerCommand: (name, callback) => { commands.set(name, callback); return { dispose() {} }; }, executeCommand: async () => {} },
    window: { createWebviewPanel: () => {
      const panel = { webview: { html: '', cspSource: 'vscode:', asWebviewUri: (uri) => uri,
        onDidReceiveMessage(callback) { panel.receive = callback; }, postMessage() {} },
        onDidDispose(callback) { panel.didDispose = callback; }, reveal() {}, dispose() { panel.didDispose(); } };
      panels.push(panel); return panel;
    } } };
  const actions = { load: async () => state(), request: async () => message(), acknowledge: async () => ({ recorded: true }),
    probe: async () => probe, discard: async () => {}, openExercise: async () => {}, configure: async () => {}, ...overrides };
  for (const key of Object.keys(actions)) {
    const original = actions[key];
    actions[key] = (...args) => { calls.push([key, ...args]); return original(...args); };
  }
  const { CoachPanel } = loadTs('coachPanel.ts', { vscode });
  return { coach: new CoachPanel('extension', actions), panels, commands, vscode, calls };
}
const version = (panel) => Number(panel.webview.html.match(/data-version="(\d+)"/)[1]);
const receive = (panel, value) => panel.receive({ version: version(panel), ...value });
const display = (panel) => receive(panel, { type: 'displayed', messageId: 'message-1' });
const { coachContent } = loadTs('coachView.ts');

test('coach renders null, zero, neutral hint usage, escaped evidence and honest template provenance', () => {
  const payload = '<img src=x onerror="oops()">';
  const html = coachContent({ state: state(), message: message({ hypothesis: payload, evidence: [{ id: payload, title: payload, description: payload }] }),
    acknowledged: true, probe, reflection: true, busy: false, loading: false, error: '', notice: '' });
  assert.ok(html.includes('0.0%'));
  assert.ok(html.includes('证据不足'));
  assert.ok(html.includes('提示使用率（描述性）'));
  assert.ok(html.includes('不用于判断能力强弱'));
  assert.ok(html.includes('以下汇总当前课程历史，不代表当前这道题已通过'));
  assert.ok(html.includes('未运行 Spark 或学生代码，不影响成绩'));
  assert.ok(html.includes('教学措辞来自审核节点'));
  assert.ok(html.includes('未验证'));
  assert.ok(!html.includes('<img'));
  assert.ok(html.includes('&lt;img'));
});

test('read-only opening never generates AI content; forged levels and exercise targets cannot bypass loaded state', async () => {
  const f = fixture(); await f.coach.show(); const panel = f.panels[0];
  await receive(panel, { type: 'request', level: 3 });
  await receive(panel, { type: 'request', level: '1' });
  await receive(panel, { type: 'open', index: 0, courseId: 'forged' });
  assert.deepEqual(f.calls.map(([name]) => name), ['load']);
  const unavailable = fixture({ load: async () => state({ available: false, task: null, reason: '请选择支持的练习。' }) });
  await unavailable.coach.show();
  await receive(unavailable.panels[0], { type: 'open', index: 99 });
  await receive(unavailable.panels[0], { type: 'open', index: 0, task: { courseId: 'forged' } });
  assert.deepEqual(unavailable.calls.find(([name]) => name === 'openExercise')[1], task);
});

test('response is rendered before display ACK; only acknowledged help unlocks the next level and actions', async () => {
  const f = fixture(); await f.coach.show(); const panel = f.panels[0];
  await receive(panel, { type: 'request', level: 1 });
  assert.ok(panel.webview.html.includes('class="coach-answer"'));
  assert.ok(!panel.webview.html.includes('class="coach-answer" hidden'));
  assert.equal(f.calls.filter(([name]) => name === 'acknowledge').length, 0);
  await receive(panel, { type: 'probe' });
  await receive(panel, { type: 'request', level: 2 });
  await receive(panel, { type: 'displayed', messageId: 'forged' });
  assert.equal(f.calls.filter(([name]) => name === 'request').length, 1);
  await display(panel);
  assert.ok(!panel.webview.html.includes('class="coach-answer" hidden'));
  assert.ok(!panel.webview.html.includes('2. 检查方法（待解锁）'));
  await display(panel);
  assert.equal(f.calls.filter(([name]) => name === 'acknowledge').length, 1);
  await receive(panel, { type: 'reflection' });
  assert.ok(panel.webview.html.includes('同样的判断能迁移到传感器数据吗？'));
  await receive(panel, { type: 'probe', messageId: 'forged', values: [99] });
  assert.equal(f.calls.find(([name]) => name === 'probe')[1].messageId, 'message-1');
  assert.ok(panel.webview.html.includes('行数：2 · 数量总和：6'));
  assert.equal(f.calls.filter(([name]) => name === 'request').length, 1);
});

test('failed ACK removes content and keeps deeper hints locked', async () => {
  const f = fixture({ acknowledge: async () => ({ recorded: false }) });
  await f.coach.show(); const panel = f.panels[0];
  await receive(panel, { type: 'request', level: 1 }); await display(panel);
  assert.ok(!panel.webview.html.includes('可能混淆了行数'));
  assert.ok(panel.webview.html.includes('内容已撤下'));
  await receive(panel, { type: 'request', level: 2 });
  assert.equal(f.calls.filter(([name]) => name === 'request').length, 1);
  assert.equal(f.calls.find(([name]) => name === 'discard')[1], 'request-1');
});

test('an ACK in flight cannot unlock or repopulate a changed task; acknowledged retries remain idempotent', async () => {
  const pending = deferred(), f = fixture({ acknowledge: () => pending.promise });
  await f.coach.show(); const panel = f.panels[0];
  await receive(panel, { type: 'request', level: 1 });
  assert.ok(!panel.webview.html.includes('class="coach-answer" hidden'));
  const acknowledging = display(panel);
  f.coach.invalidate(); await f.coach.refreshIfOpen();
  pending.resolve({ recorded: true }); await acknowledging;
  assert.ok(!panel.webview.html.includes('可能混淆了行数'));
  assert.ok(panel.webview.html.includes('2. 检查方法（待解锁）'));
  const retry = fixture({ acknowledge: async () => ({ recorded: false, reason: 'already_acknowledged' }) });
  await retry.coach.show(); await receive(retry.panels[0], { type: 'request', level: 1 }); await display(retry.panels[0]);
  assert.ok(!retry.panels[0].webview.html.includes('内容已撤下'));
  assert.ok(!retry.panels[0].webview.html.includes('2. 检查方法（待解锁）'));
});

test('code invalidation and close/reopen drop late replies, preserve the old run lock and never ACK unseen content', async () => {
  const pending = deferred(), f = fixture({ request: () => pending.promise });
  await f.coach.show(); const old = f.panels[0], run = receive(old, { type: 'request', level: 1 });
  await receive(old, { type: 'request', level: 1 });
  f.coach.invalidate(); old.dispose(); await f.coach.show(); const fresh = f.panels[1];
  await receive(fresh, { type: 'request', level: 1 });
  pending.resolve(message()); await run;
  assert.equal(f.calls.filter(([name]) => name === 'request').length, 1);
  assert.equal(f.calls.filter(([name]) => name === 'acknowledge').length, 0);
  assert.ok(!fresh.webview.html.includes('可能混淆了行数'));
  assert.equal(f.calls.find(([name]) => name === 'discard')[1], 'request-1');
  await receive(fresh, { type: 'refresh' });
  await receive(fresh, { type: 'request', level: 1 });
  fresh.dispose();
  assert.equal(f.calls.filter(([name]) => name === 'acknowledge').length, 0);
});

test('late state and probe responses cannot repopulate changed tasks, and refresh failure clears actionable targets', async () => {
  const first = deferred(), waitingProbe = deferred(); let loads = 0;
  const f = fixture({ load: async () => { if (++loads === 1) return first.promise; if (loads === 3) throw Error('server'); return state(); },
    probe: () => waitingProbe.promise });
  const opening = f.coach.show(); const panel = f.panels[0];
  f.coach.invalidate(); await f.coach.refreshIfOpen(); first.resolve(state({ task: { ...task, title: 'old title' } })); await opening;
  assert.ok(!panel.webview.html.includes('old title'));
  await receive(panel, { type: 'request', level: 1 }); await display(panel);
  const probing = receive(panel, { type: 'probe' });
  f.coach.invalidate(); waitingProbe.resolve(probe); await probing;
  assert.ok(!panel.webview.html.includes('数量总和：6'));
  await f.coach.refreshIfOpen();
  await receive(panel, { type: 'open', index: 0 });
  assert.ok(panel.webview.html.includes('暂时无法读取教练状态'));
  assert.equal(f.calls.filter(([name]) => name === 'openExercise').length, 0);
});

test('actual coach media script waits for a visible render, ACKs once and suppresses repeated clicks', () => {
  const sent = [], frames = [], handlers = {};
  const button = { dataset: { action: 'request', level: '1' }, disabled: false, addEventListener(_type, callback) { this.click = callback; } };
  const node = { dataset: { awaitingAck: 'true', messageId: 'message-1' }, isConnected: true };
  const document = { body: { dataset: { version: '9' } }, visibilityState: 'hidden',
    getElementById: () => node, querySelectorAll: () => [button], addEventListener: (name, callback) => { handlers[name] = callback; } };
  vm.runInNewContext(fs.readFileSync(path.resolve(__dirname, '../media/coach.js'), 'utf8'), {
    acquireVsCodeApi: () => ({ postMessage: (value) => sent.push(value) }), document,
    requestAnimationFrame: (callback) => frames.push(callback),
  });
  assert.equal(sent.length, 0);
  document.visibilityState = 'visible'; handlers.visibilitychange();
  assert.equal(sent.length, 0);
  frames.shift()(); assert.equal(sent.length, 0); frames.shift()(); handlers.visibilitychange();
  assert.deepEqual({ ...sent[0] }, { type: 'displayed', version: 9, messageId: 'message-1' });
  assert.equal(sent.length, 1);
  button.click(); button.click();
  assert.equal(sent.length, 2);
  assert.equal(sent[1].level, 1);
  assert.equal(button.disabled, true);
});

function routerFixture(providerOverrides = {}, bridgeOverride) {
  const calls = [];
  const { AiRouter } = loadTs('aiRouter.ts', { vscode: {} });
  const router = new AiRouter({ call: async (method, params, timeout) => {
    calls.push([method, params, timeout]);
    if (bridgeOverride) return bridgeOverride(method, params);
    if (method === 'buildCoachPrompt') return { requestId: 'request-1', messages: [{ role: 'user', content: 'bounded evidence' }] };
    if (method === 'parseCoachResponse') return message();
    return {};
  } });
  router.resolveProvider = () => { assert.fail('Coach must never use automatic Anthropic routing'); };
  router.openai = { refreshSecret: async () => {}, hasExplicitModelConfig: () => true, isConfigured: () => true,
    sendRequest: async (_messages, _tokens, timeout) => { calls.push(['model', timeout]); return '{"hypothesisId":"count-vs-sum"}'; }, ...providerOverrides };
  return { router, calls };
}

test('coach router uses configured compatible model, rereads current code before parse, and uses bounded RPCs', async () => {
  const f = routerFixture();
  await f.router.requestCoach({ code: 'current', level: 1 }, async () => { f.calls.push(['flush-reread']); return 'current'; }, () => true);
  assert.deepEqual(f.calls.map(([name]) => name), ['buildCoachPrompt', 'model', 'flush-reread', 'parseCoachResponse']);
  assert.equal(f.calls[1][1], 180000);
  assert.deepEqual(f.calls[3][1], { requestId: 'request-1', code: 'current', rawText: '{"hypothesisId":"count-vs-sum"}' });
  assert.equal(f.calls[3][2], 180000);
});

test('missing explicit endpoint/model or failed transport cannot manufacture AI results and cleans only its request', async () => {
  const noConfig = routerFixture({ hasExplicitModelConfig: () => false });
  await assert.rejects(noConfig.router.requestCoach({ code: '', level: 1 }, async () => '', () => true), /尚未配置教练模型/);
  assert.deepEqual(noConfig.calls.map(([name]) => name), ['buildCoachPrompt', 'completeCoachFailure']);
  assert.deepEqual(noConfig.calls[1][1], { requestId: 'request-1' });
  const failed = routerFixture({ sendRequest: async () => { throw Error('SECRET vendor body containing code'); } });
  await assert.rejects(failed.router.requestCoach({ code: '', level: 1 }, async () => '', () => true), (error) => {
    assert.ok(!error.message.includes('SECRET')); return /请求未完成/.test(error.message);
  });
  assert.ok(!failed.calls.some(([name]) => name === 'parseCoachResponse'));
});

test('code changes and expired parsing reject old responses without ACK or retries', async () => {
  const changed = routerFixture();
  await assert.rejects(changed.router.requestCoach({ code: 'before', level: 1 }, async () => 'after', () => true), /代码已变化/);
  assert.ok(!changed.calls.some(([name]) => name === 'parseCoachResponse'));
  const stale = routerFixture({}, (method) => {
    if (method === 'buildCoachPrompt') return { requestId: 'old-id', messages: [] };
    throw Error('expired');
  });
  await assert.rejects(stale.router.requestCoach({ code: 'same', level: 1 }, async () => 'same', () => true), /未通过证据与格式校验/);
  assert.deepEqual(stale.calls.at(-1)[1], { requestId: 'old-id' });
  assert.equal(stale.calls.filter(([name]) => name === 'completeCoachFailure').length, 1);
});

test('coach response failures explain only fixed safe categories and never automatically call the model again', async (t) => {
  const reasons = {
    COACH_RESPONSE_FORMAT: '有效的单个 JSON 对象',
    COACH_RESPONSE_FIELDS: '字段不符合教练接口约定',
    COACH_RESPONSE_HYPOTHESIS: '假设不适用于当前练习或执行状态',
    COACH_RESPONSE_REFERENCES: '没有正确引用本次提供的学习证据',
    COACH_RESPONSE_PROBE: '样例数据不符合当前练习的核查规则',
    COACH_RESPONSE_TOO_LARGE: '超过 8 KB 限制',
  };
  for (const [code, reason] of Object.entries(reasons)) {
    await t.test(code, async () => {
      const f = routerFixture({}, (method) => {
        if (method === 'buildCoachPrompt') return { requestId: 'request-1', messages: [] };
        if (method === 'parseCoachResponse') throw Error(`PRIVATE provider body or student code [${code}]`);
        return {};
      });
      await assert.rejects(f.router.requestCoach({ code: 'same', level: 2 }, async () => 'same', () => true), (error) => {
        assert.ok(error.message.includes(reason));
        assert.ok(error.message.includes(`[${code}]`));
        assert.ok(error.message.includes('请点击同一层级重试'));
        assert.ok(error.message.includes('未计入提示记录'));
        assert.ok(!error.message.includes('PRIVATE'));
        assert.ok(!error.message.includes('student code'));
        return true;
      });
      assert.deepEqual(f.calls.map(([name]) => name), ['buildCoachPrompt', 'model', 'parseCoachResponse', 'completeCoachFailure']);
      assert.deepEqual(f.calls.at(-1)[1], { requestId: 'request-1' });
    });
  }
});

test('unknown parse errors and unknown category codes are not echoed into the coach panel', async () => {
  for (const raw of ['PRIVATE provider body or student code', 'PRIVATE [COACH_RESPONSE_NEW_CATEGORY]',
    'PRIVATE [COACH_RESPONSE_FORMAT] with trailing PRIVATE text']) {
    const f = routerFixture({}, (method) => {
      if (method === 'buildCoachPrompt') return { requestId: 'request-1', messages: [] };
      if (method === 'parseCoachResponse') throw Error(raw);
      return {};
    });
    await assert.rejects(f.router.requestCoach({ code: 'same', level: 1 }, async () => 'same', () => true), (error) => {
      assert.ok(error.message.includes('未通过证据与格式校验，或练习已变化'));
      assert.ok(!error.message.includes('PRIVATE'));
      assert.ok(!error.message.includes('COACH_RESPONSE_'));
      return true;
    });
    assert.equal(f.calls.filter(([name]) => name === 'model').length, 1);
    assert.equal(f.calls.filter(([name]) => name === 'completeCoachFailure').length, 1);
  }
});

test('custom endpoint credentials use SecretStorage, explicit settings and one shared abort deadline without real network', async (t) => {
  const values = { openaiBaseUrl: 'https://model.invalid/v1', openaiModel: 'custom-model', openaiApiKey: 'legacy-key' };
  const vscode = { workspace: { getConfiguration: () => ({ get: (key) => values[key], inspect: (key) => ({ globalValue: values[key] }) }) } };
  const { OpenAICompatProvider } = loadTs('openaiProvider.ts', { vscode });
  const provider = new OpenAICompatProvider({ get: async () => 'secret-storage-key' });
  const originalFetch = global.fetch; t.after(() => { global.fetch = originalFetch; });
  const requests = [];
  global.fetch = async (url, options) => {
    requests.push({ url, ...options });
    return { ok: true, json: async () => ({ choices: [{ message: { content: 'bounded model response' } }] }) };
  };
  assert.equal(provider.hasExplicitModelConfig(), true);
  const result = await provider.sendRequest([{ role: 'user', content: 'example' }], 512, 180000);
  assert.equal(result, 'bounded model response');
  assert.equal(requests[0].headers.Authorization, 'Bearer secret-storage-key');
  assert.equal(JSON.parse(requests[0].body).model, 'custom-model');
  assert.ok(requests[0].signal instanceof AbortSignal);
  assert.equal(requests[0].signal.aborted, false);
});

test('provider defaults do not authorize coach requests, and a hung mocked endpoint is aborted', async (t) => {
  const previous = [process.env.SPARKTUTOR_OPENAI_BASE_URL, process.env.SPARKTUTOR_OPENAI_MODEL];
  delete process.env.SPARKTUTOR_OPENAI_BASE_URL; delete process.env.SPARKTUTOR_OPENAI_MODEL;
  const originalFetch = global.fetch;
  t.after(() => {
    global.fetch = originalFetch;
    for (const [key, value] of [['SPARKTUTOR_OPENAI_BASE_URL', previous[0]], ['SPARKTUTOR_OPENAI_MODEL', previous[1]]]) {
      if (value === undefined) delete process.env[key]; else process.env[key] = value;
    }
  });
  const config = { get: (key) => ({ openaiBaseUrl: 'https://default.invalid/v1', openaiModel: 'default-model', openaiApiKey: 'test-key' })[key],
    inspect: () => ({ defaultValue: 'default-only' }) };
  const { OpenAICompatProvider } = loadTs('openaiProvider.ts', { vscode: { workspace: { getConfiguration: () => config } } });
  const provider = new OpenAICompatProvider();
  assert.equal(provider.hasExplicitModelConfig(), false);
  let aborted = false;
  global.fetch = async (_url, { signal }) => new Promise((_resolve, reject) => {
    const timer = setTimeout(() => reject(Error('test should have aborted first')), 300);
    signal.addEventListener('abort', () => { clearTimeout(timer); aborted = true; reject(Error('aborted')); }, { once: true });
  });
  await assert.rejects(provider.sendRequest([], 100, 5), /aborted/);
  assert.equal(aborted, true);
});

test('configuration saves the key only in secret storage and cancellation never writes settings', async () => {
  const writes = [], notices = [];
  const vscode = { window: { showQuickPick: async () => ({ id: 'key' }),
    showInputBox: async (options) => { assert.equal(options.password, true); return '  local-secret  '; },
    showInformationMessage: async (text) => notices.push(text) },
    workspace: { getConfiguration: () => ({ update() { assert.fail('key must never enter settings'); } }) } };
  const { configureCoachModel } = loadTs('coachModel.ts', { vscode });
  await configureCoachModel({ store: async (...args) => writes.push(args) });
  assert.deepEqual(writes, [['sparktutor.openaiApiKey', 'local-secret']]);
  assert.ok(notices.every((text) => !text.includes('local-secret')));
  vscode.window.showQuickPick = async () => undefined;
  await configureCoachModel({ store() { assert.fail('cancelled'); } });
});

test('coach command preserves current work, uses validated exercise RPC, flushes before parse/ACK and invalidates edits', async () => {
  const f = fixture(), operations = [], steps = [], disposables = [];
  let editListener, code = 'old buffer', loaded = false;
  f.vscode.workspace = { onDidChangeTextDocument: (callback) => { editListener = callback; return { dispose() {} }; } };
  const { registerCommands } = loadTs('commands.ts', { vscode: f.vscode });
  const bridge = { async call(method, params, timeout) {
    operations.push([method, params, timeout]);
    if (method === 'getLearningCoach') return state({ available: loaded, task: loaded ? task : null });
    if (method === 'openCoachExercise') {
      loaded = true;
      return { step: { id: task.stepId, cls: 'script', output: 'task' }, courseId: task.courseId, lessonId: task.lessonId,
        lessonTitle: task.title, lessonIdx: 4, depth: 'beginner', practiceMode: true, currentIndex: 6, totalSteps: 9, starterCode: 'starter' };
    }
    if (method === 'ackCoachMessage') return { recorded: true };
    if (method === 'completeCoachFailure') return {};
    if (method === 'getLearningDashboard') return { courses: [], selectedCourseId: '', resume: null, diagnosis: null, knowledgeComponents: {} };
    assert.fail(`unexpected RPC: ${method}`);
  } };
  const workspace = { getCurrentCode() { operations.push(['read-code', code]); return code; }, getCurrentUri: () => 'exercise.py',
    saveCurrentExercise: async () => operations.push(['save']), switchCourse: async () => {}, setStepType() {},
    openExercise: async () => { code = 'current buffer'; } };
  const tracker = { flush: async () => operations.push(['flush']), transition: async (action) => { operations.push(['transition']); return action(); }, bind() {} };
  const router = { requestCoach: async (params, read, current) => {
    assert.equal(params.code, 'current buffer'); assert.equal(current(), true);
    const newest = await read(); operations.push(['parse', newest]); return message();
  } };
  const lesson = { updateStep: (...args) => steps.push(args) };
  registerCommands({ extensionUri: 'extension', subscriptions: disposables, secrets: {}, globalState: { update() { assert.fail('Practice must preserve ordered progress'); } } },
    bridge, { refresh() {} }, lesson, workspace, { clear() {} }, { clear() {} }, { setStep() {}, setDepth() {} }, router, tracker);
  await f.commands.get('sparktutor.openLearningCoach')(); const panel = f.panels[0];
  assert.deepEqual(operations.map(([name]) => name), ['flush', 'getLearningCoach']);
  await receive(panel, { type: 'open', index: 0, courseId: 'forged' });
  const open = operations.findIndex(([name]) => name === 'openCoachExercise');
  assert.equal(operations[open - 1][0], 'save');
  assert.deepEqual(operations[open][1], { courseId: task.courseId, lessonId: task.lessonId, stepId: task.stepId });
  assert.equal(steps[0][1], 6); assert.equal(steps[0][5], true);
  assert.ok(!operations.some(([name]) => name === 'loadLesson' || name === 'openRecommendedExercise'));
  await receive(panel, { type: 'request', level: 1 });
  await display(panel);
  const ack = operations.findIndex(([name]) => name === 'ackCoachMessage');
  assert.deepEqual(operations.slice(ack - 2, ack).map(([name]) => name), ['flush', 'read-code']);
  assert.equal(operations[ack][1].code, 'current buffer');
  editListener({ contentChanges: [{}], document: { uri: 'different.py' } });
  assert.ok(panel.webview.html.includes('可能混淆了行数'));
  editListener({ contentChanges: [{}], document: { uri: 'exercise.py' } });
  assert.ok(!panel.webview.html.includes('可能混淆了行数'));
  await f.commands.get('sparktutor.openLearningDashboard')();
  await receive(f.panels[1], { type: 'coach' });
  assert.equal(f.panels.length, 2);
  assert.equal(typeof lesson.onCoach, 'function');
  disposables.forEach((item) => item.dispose());
  const manifest = JSON.parse(fs.readFileSync(path.resolve(__dirname, '../package.json'), 'utf8'));
  assert.ok(manifest.contributes.commands.some((item) => item.command === 'sparktutor.configureCoachModel'));
  assert.ok(manifest.contributes.menus['view/title'].some((item) => item.command === 'sparktutor.openLearningCoach'));
});
