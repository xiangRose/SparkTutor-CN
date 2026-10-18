const path = require('node:path');
const Module = require('node:module');
const assert = require('node:assert/strict');
const test = require('node:test');
const esbuild = require('esbuild');

const filename = path.resolve(__dirname, '../src/openaiProvider.ts');
const { outputFiles } = esbuild.buildSync({ entryPoints: [filename], bundle: true,
  platform: 'node', format: 'cjs', write: false, external: ['vscode'], logLevel: 'silent' });
const loaded = new Module(filename, module);
loaded.paths = module.paths;
loaded.require = (id) => id === 'vscode' ? {} : require(id);
loaded._compile(outputFiles[0].text, filename);
const { OpenAICompatProvider, OpenAICompatError } = loaded.exports;

function fixture(t, replies, baseUrl = 'https://model.example/v1') {
  const calls = [], previous = global.fetch;
  global.fetch = async (url, options) => {
    calls.push({ url, options, body: JSON.parse(options.body) });
    const reply = replies.shift();
    assert.notEqual(reply, undefined, 'unexpected model request');
    if (reply instanceof Error) throw reply;
    return reply;
  };
  t.after(() => { global.fetch = previous; });
  const provider = new OpenAICompatProvider();
  provider.getConfig = () => ({ baseUrl, apiKey: 'test-only-key', model: 'test-model' });
  return { calls, provider, send: (budget = 256, options) => provider.sendRequest([{ role: 'user', content: 'OK' }], budget, 30000, options) };
}

const completion = (message, finish_reason = 'stop') => Response.json({ choices: [{ message, finish_reason }] });
const errorKind = (kind) => (error) => {
  assert.ok(error instanceof OpenAICompatError);
  assert.equal(error.kind, kind);
  assert.equal(error.status, 200);
  return true;
};

test('provider error metadata preserves the optional constructor contract', () => {
  assert.equal(new OpenAICompatError('network', 0).kind, 'transport');
  assert.equal(new OpenAICompatError('status', 401).kind, 'http');
  assert.equal(new OpenAICompatError('unfinished', 200, 'incomplete').kind, 'incomplete');
});

test('provider accepts response text and textual content arrays without exposing reasoning', async (t) => {
  const f = fixture(t, [completion({ content: ' OK ', reasoning_content: 'private reasoning' }),
    completion({ content: [{ type: 'text', text: 'part 1' }, { type: 'image_url', image_url: {} },
      { type: 'text', text: ' part 2' }] })]);
  assert.equal(await f.send(), ' OK ');
  assert.equal(await f.send(), 'part 1 part 2');
  assert.equal(f.calls[0].body.max_tokens, 256);
  assert.ok(f.calls[0].options.signal instanceof AbortSignal);
});

test('provider rejects empty content as an empty response rather than a transport error', async (t) => {
  const f = fixture(t, [completion({ content: ' ' }), completion({ content: null }),
    completion({}), completion({ content: [] })]);
  for (let i = 0; i < 4; ++i) await assert.rejects(f.send(), errorKind('empty'));
  assert.equal(f.calls.length, 4, 'empty responses must not cause an endpoint retry');
});

test('reasoning-only responses remain unfinished and never become the returned answer or error text', async (t) => {
  const privateText = 'PRIVATE_REASONING_SHOULD_NEVER_BE_EXPOSED';
  const f = fixture(t, [completion({ content: null, reasoning_content: privateText }),
    completion({ content: '', reasoning_content: privateText }), completion({ reasoning_content: privateText })]);
  for (let i = 0; i < 3; ++i) {
    await assert.rejects(f.send(), (error) => {
      errorKind('incomplete')(error);
      assert.ok(!error.message.includes(privateText));
      assert.ok(!error.stack.includes(privateText));
      return true;
    });
  }
  assert.equal(f.calls.length, 3, 'unfinished replies must not be silently retried');
});

test('length without text is unfinished, while nonempty content remains available for application validation', async (t) => {
  const f = fixture(t, [completion({ content: '' }, 'length'), completion({ content: '{"partial":' }, 'length')]);
  await assert.rejects(f.send(), errorKind('incomplete'));
  assert.equal(await f.send(), '{"partial":');
});

test('malformed envelopes and message shapes return safe format errors', async (t) => {
  const data = [null, [], {}, { choices: {} }, { choices: [] }, { choices: [null] },
    { choices: [{}] }, { choices: [{ message: null }] }, { choices: [{ message: [] }] },
    { choices: [{ message: { content: 7 } }] }];
  const f = fixture(t, data.map((value) => Response.json(value)));
  for (let i = 0; i < data.length; ++i) await assert.rejects(f.send(), errorKind('invalid-format'));
});

test('non-JSON successful responses never include upstream text in errors', async (t) => {
  const secret = 'SECRET_UPSTREAM_BODY';
  const f = fixture(t, [new Response(`<html>${secret}</html>`, { status: 200 })]);
  await assert.rejects(f.send(), (error) => {
    errorKind('invalid-format')(error);
    assert.ok(!error.message.includes(secret));
    return true;
  });
});

test('HTTP failures keep status without echoing response bodies', async (t) => {
  const f = fixture(t, [new Response('test-only-key SECRET_UPSTREAM_BODY', { status: 401 })]);
  await assert.rejects(f.send(), (error) => {
    assert.ok(error instanceof OpenAICompatError);
    assert.equal(error.kind, 'http');
    assert.equal(error.status, 401);
    assert.ok(!error.message.includes('test-only-key'));
    assert.ok(!error.message.includes('SECRET_UPSTREAM_BODY'));
    return true;
  });
  assert.equal(f.calls.length, 1);
});

test('network failures are safe and still support the existing URL fallback', async (t) => {
  const f = fixture(t, [new Error('SECRET_TRANSPORT_DETAIL'), completion({ content: 'OK' })], 'https://model.example');
  assert.equal(await f.send(), 'OK');
  assert.deepEqual(f.calls.map((call) => call.url), [
    'https://model.example/chat/completions', 'https://model.example/v1/chat/completions']);
});

test('unrecovered network failure never echoes upstream transport detail', async (t) => {
  const f = fixture(t, [new Error('SECRET_TRANSPORT_DETAIL')]);
  await assert.rejects(f.send(), (error) => {
    assert.ok(error instanceof OpenAICompatError);
    assert.equal(error.kind, 'transport');
    assert.equal(error.status, 0);
    assert.ok(!error.message.includes('SECRET_TRANSPORT_DETAIL'));
    return true;
  });
});

test('404 still uses the existing endpoint fallback and max_tokens rejection retries without that parameter', async (t) => {
  const f = fixture(t, [new Response('not found', { status: 404 }),
    new Response('unsupported max_tokens parameter', { status: 400 }), completion({ content: 'OK' })], 'https://model.example');
  assert.equal(await f.send(256), 'OK');
  assert.equal(f.calls.length, 3);
  assert.equal(f.calls[1].body.max_tokens, 256);
  assert.ok(!Object.hasOwn(f.calls[2].body, 'max_tokens'));
});

test('max_tokens fallback retains strict empty and unfinished response rejection', async (t) => {
  const f = fixture(t, [new Response('unsupported max_tokens parameter', { status: 400 }),
    completion({ content: null, reasoning_content: 'PRIVATE' })]);
  await assert.rejects(f.send(), errorKind('incomplete'));
  assert.equal(f.calls.length, 2);
});

test('official DeepSeek thinking-model probes alone disable thinking, including max_tokens fallback', async (t) => {
  const f = fixture(t, [completion({ content: 'OK' }),
    new Response('unsupported max_tokens parameter', { status: 400 }), completion({ content: 'OK' })]);
  f.provider.getConfig = () => ({ baseUrl: 'https://api.deepseek.com/v1', model: 'deepseek-flash', apiKey: 'test-only-key' });
  assert.equal(await f.send(256, { connectionProbe: true }), 'OK');
  assert.deepEqual(f.calls[0].body.thinking, { type: 'disabled' });
  assert.ok(!Object.hasOwn(f.calls[0].body, 'connectionProbe'));
  f.provider.getConfig = () => ({ baseUrl: 'https://api.deepseek.com', model: 'deepseek-v4-pro', apiKey: 'test-only-key' });
  assert.equal(await f.send(256, { connectionProbe: true }), 'OK');
  assert.deepEqual(f.calls[1].body.thinking, { type: 'disabled' });
  assert.deepEqual(f.calls[2].body.thinking, { type: 'disabled' });
  assert.ok(!Object.hasOwn(f.calls[2].body, 'max_tokens'));
});

test('ordinary DeepSeek requests preserve model defaults and do not receive probe parameters', async (t) => {
  const f = fixture(t, [completion({ content: 'OK' }), completion({ content: 'OK' })]);
  f.provider.getConfig = () => ({ baseUrl: 'https://api.deepseek.com/v1', model: 'deepseek-flash', apiKey: 'test-only-key' });
  await f.send();
  await f.send(256, { connectionProbe: false });
  for (const { body } of f.calls) assert.ok(!Object.hasOwn(body, 'thinking'));
});

test('probe-specific thinking parameter never leaks to proxy, lookalike, other model, or invalid URL', async (t) => {
  const configs = [
    { baseUrl: 'https://api.deepseek.com.proxy.example/v1', model: 'deepseek-flash' },
    { baseUrl: 'https://api.deepseek.com@proxy.example/v1', model: 'deepseek-flash' },
    { baseUrl: 'https://gateway.example/deepseek/v1', model: 'deepseek-flash' },
    { baseUrl: 'https://api.deepseek.com/v1', model: 'deepseek-chat' },
    { baseUrl: 'not-a-valid-url', model: 'deepseek-flash' },
  ];
  const f = fixture(t, configs.map(() => completion({ content: 'OK' })));
  for (const config of configs) {
    f.provider.getConfig = () => ({ ...config, apiKey: 'test-only-key' });
    await f.send(256, { connectionProbe: true });
  }
  for (const { body } of f.calls) assert.ok(!Object.hasOwn(body, 'thinking'));
});
