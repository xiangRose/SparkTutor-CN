const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const test = require('node:test');
const esbuild = require('esbuild');

const compiled = esbuild.buildSync({
  entryPoints: [path.resolve(__dirname, '../src/aiRouter.ts')], bundle: true,
  platform: 'node', format: 'cjs', write: false, external: ['vscode'], logLevel: 'silent',
}).outputFiles[0].text;

function fixture({ provider = 'auto', baseUrl = 'https://api.deepseek.com/v1' } = {}) {
  const values = { aiProvider: provider, openaiBaseUrl: baseUrl, openaiModel: 'deepseek-flash' };
  const requests = [], warnings = [], backend = [];
  let respond = async () => ({ ok: true, json: async () => ({ choices: [{ message: { content: 'OK' } }] }) });
  const vscode = {
    workspace: { getConfiguration: () => ({ get: (key) => values[key], inspect: (key) => ({ globalValue: values[key] }) }) },
    window: { showWarningMessage: (text) => warnings.push(text) },
  };
  const loaded = { exports: {} };
  const evaluate = new Function('require', 'module', 'exports', 'process', 'fetch', 'AbortSignal', compiled);
  evaluate((id) => id === 'vscode' ? vscode : require(id), loaded, loaded.exports,
    { env: {} }, async (url, options) => {
      requests.push({ url, ...options, body: JSON.parse(options.body) });
      return respond(url, options);
    }, AbortSignal);
  const router = new loaded.exports.AiRouter({ call: async (method, params) => {
    backend.push({ method, params });
    if (method === 'buildCoachPrompt') return { requestId: 'test-request', messages: [{ role: 'user', content: 'test evidence' }] };
    if (method === 'parseCoachResponse') return { messageId: 'test-message' };
    return {};
  } }, { get: async () => 'test-secret' });
  return { router, values, requests, warnings, backend, setResponse: (callback) => { respond = callback; } };
}

test('compatible connection probe has a bounded budget and deadline, and only successful text is ready', async () => {
  const f = fixture();
  const result = await f.router.checkConnection();
  assert.equal(result.ok, true);
  assert.equal(result.state, 'ready');
  assert.ok(result.message.includes('已返回正文'));
  assert.ok(result.message.includes('仍需实际请求验证'));
  assert.equal(f.requests.length, 1);
  assert.equal(f.requests[0].body.max_tokens, 256);
  assert.equal(f.requests[0].body.messages[0].content, '请只回复 OK，不要解释。');
  assert.ok(f.requests[0].signal instanceof AbortSignal);
  assert.deepEqual(f.requests[0].body.thinking, { type: 'disabled' });
  assert.ok(!Object.hasOwn(f.requests[0].body, 'connectionProbe'));
});

test('empty or reasoning-only probes remain unverified and cannot push auto routing into failure cooldown', async (t) => {
  for (const reply of [
    { message: { content: '' }, finish_reason: 'stop' },
    { message: { content: null, reasoning_content: 'PRIVATE THINKING' }, finish_reason: 'length' },
    { message: { content: '', reasoning_content: 'PRIVATE THINKING' }, finish_reason: 'stop' },
  ]) {
    await t.test(reply.finish_reason + ':' + String(reply.message.content), async () => {
      const f = fixture();
      f.setResponse(async () => ({ ok: true, json: async () => ({ choices: [reply] }) }));
      const result = await f.router.checkConnection();
      assert.equal(result.ok, false);
      assert.equal(result.state, 'unverified');
      assert.ok(result.message.includes('接口已响应'));
      assert.ok(!result.message.includes('PRIVATE'));
      assert.equal(f.router.inHealthCooldown('openai-compatible'), false);
      assert.equal(await f.router.resolveProvider(), 'openai-compatible');
      assert.equal(f.requests.length, 1, 'No automatic generation retry');
    });
  }
});

test('an inconclusive current response clears a stale failure while never claiming generation success', async () => {
  const f = fixture();
  f.router.recordHealth('openai-compatible', false);
  f.setResponse(async () => ({ ok: true, json: async () => ({ choices: [{ message: { content: '' } }] }) }));
  const result = await f.router.checkConnection();
  assert.equal(result.ok, false);
  assert.equal(result.state, 'unverified');
  assert.equal(f.router.inHealthCooldown('openai-compatible'), false);
  f.values.aiProvider = 'auto';
  assert.equal(await f.router.resolveProvider(), 'openai-compatible');
});

test('a manual check can retry the configured provider during cooldown and recover after a genuine failure', async () => {
  const f = fixture();
  f.setResponse(async () => ({ ok: false, status: 503, text: async () => 'PRIVATE FAILURE' }));
  assert.equal((await f.router.checkConnection()).state, 'unavailable');
  assert.equal(await f.router.resolveProvider(), 'none');
  f.setResponse(async () => ({ ok: true, json: async () => ({ choices: [{ message: { content: 'OK' } }] }) }));
  assert.equal((await f.router.checkConnection()).state, 'ready');
  assert.equal(await f.router.resolveProvider(), 'openai-compatible');
  assert.equal(f.requests.length, 2);
});

test('startup surfaces an honest inconclusive warning, and a later coach request still reaches its model', async () => {
  const f = fixture();
  f.setResponse(async () => ({ ok: true, json: async () => ({ choices: [{ message: { content: '' }, finish_reason: 'length' }] }) }));
  await f.router.runStartupHealthCheck();
  assert.equal(f.warnings.length, 1);
  assert.ok(f.warnings[0].includes('尚未确认正文生成可用'));
  f.setResponse(async () => ({ ok: true, json: async () => ({ choices: [{ message: { content: '{"hypothesisId":"uncertain"}' } }] }) }));
  const message = await f.router.requestCoach({ code: '# test', level: 1 }, async () => '# test', () => true);
  assert.equal(message.messageId, 'test-message');
  assert.equal(f.requests.length, 2);
  assert.equal(f.requests[1].body.max_tokens, 1024);
  assert.ok(!Object.hasOwn(f.requests[1].body, 'thinking'), 'A probe must not change the actual coach thinking mode');
  assert.ok(f.backend.some((item) => item.method === 'parseCoachResponse'));
});

test('HTTP errors keep existing auto cooldown and never leak upstream bodies', async (t) => {
  for (const status of [401, 403, 404, 429, 503]) {
    await t.test(String(status), async () => {
      const f = fixture();
      f.setResponse(async () => ({ ok: false, status, text: async () => 'PRIVATE UPSTREAM BODY test-secret' }));
      const result = await f.router.checkConnection();
      assert.equal(result.ok, false);
      assert.equal(result.state, 'unavailable');
      assert.ok(!result.message.includes('PRIVATE'));
      assert.ok(!result.message.includes('test-secret'));
      assert.equal(f.router.inHealthCooldown('openai-compatible'), true);
      assert.equal(await f.router.resolveProvider(), 'none');
    });
  }
});

test('an unfinished real coach reply is rejected with a precise safe explanation and no manufactured help', async () => {
  const f = fixture();
  f.setResponse(async () => ({ ok: true, json: async () => ({ choices: [
    { message: { content: '', reasoning_content: 'PRIVATE THINKING' }, finish_reason: 'length' },
  ] }) }));
  await assert.rejects(f.router.requestCoach({ code: '# test', level: 1 }, async () => '# test', () => true), (error) => {
    assert.ok(error.message.includes('接口已响应'));
    assert.ok(error.message.includes('没有生成可用正文'));
    assert.ok(!error.message.includes('PRIVATE'));
    assert.ok(!error.message.includes('密钥'));
    return true;
  });
  assert.equal(f.requests.length, 1);
  assert.deepEqual(f.backend.map((item) => item.method), ['buildCoachPrompt', 'completeCoachFailure']);
});

test('invalid successful payloads and network failures are not mistaken for empty but reachable model replies', async (t) => {
  for (const value of [null, {}, { choices: [] }]) {
    await t.test(JSON.stringify(value), async () => {
      const f = fixture();
      f.setResponse(async () => ({ ok: true, json: async () => value }));
      const result = await f.router.checkConnection();
      assert.equal(result.state, 'unavailable');
      assert.ok(result.message.includes('不符合兼容协议'));
    });
  }
  const f = fixture();
  f.setResponse(async () => { throw Error('PRIVATE URL OR CREDENTIAL'); });
  const result = await f.router.checkConnection();
  assert.equal(result.state, 'unavailable');
  assert.ok(!result.message.includes('PRIVATE'));
  assert.ok(result.message.includes('网络请求未完成'));
});
