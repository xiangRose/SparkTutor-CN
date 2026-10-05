const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');
const Module = require('node:module');
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
const helpers = loadTs('lessonHelpers.ts');

test('choice order is stable, preserves answer text, and varies across questions', () => {
  const choices = ['正确答案', '错误答案一', '错误答案二', '错误答案三'];
  const positions = new Set();
  for (let i = 0; i < 48; i++) {
    const shuffled = helpers.shuffledChoices(choices, `lesson:question:${i}`);
    assert.deepEqual(shuffled, helpers.shuffledChoices(choices, `lesson:question:${i}`));
    assert.deepEqual([...shuffled].sort(), [...choices].sort());
    positions.add(shuffled.indexOf('正确答案'));
  }
  assert.equal(positions.size, 4);
  assert.equal(choices[0], '正确答案');
});

test('quoted choices are inert HTML data and submit their exact text', () => {
  const choice = `cast('N/A' AS DOUBLE) 与 "引号" <标签> & 符号`;
  const html = helpers.choiceButton(choice);
  assert.ok(html.includes('data-choice="'));
  assert.ok(html.includes('&#39;'));
  assert.ok(html.includes('&quot;'));
  assert.ok(html.includes('&lt;标签&gt;'));
  assert.ok(!html.includes('onclick'));
  let click;
  const attributes = {};
  const posted = [];
  const button = {
    dataset: { choice }, textContent: choice,
    classList: { add() {}, remove() {} },
    setAttribute(key, value) { attributes[key] = value; },
    addEventListener(name, callback) { if (name === 'click') click = callback; },
  };
  vm.runInNewContext(fs.readFileSync(path.resolve(__dirname, '../media/lesson.js'), 'utf8'), {
    acquireVsCodeApi: () => ({ postMessage: (message) => posted.push(message) }),
    document: { querySelectorAll: () => [button] }, window: { addEventListener() {} },
  });
  click();
  assert.equal(posted[0].type, 'choiceSelect');
  assert.equal(posted[0].choice, choice);
  assert.equal(attributes['aria-pressed'], 'true');
});

test('unfiltered step id survives depth and wording changes', () => {
  assert.equal(helpers.stepIdentity({ id: '19', cls: 'script', output: '原题面' }),
    helpers.stepIdentity({ id: '19', cls: 'script', output: '修订题面' }));
});

function workspaceFixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'sparktutor-frontend-'));
  t.after(() => {
    assert.equal(path.dirname(path.resolve(root)), path.resolve(os.tmpdir()));
    assert.ok(path.basename(root).startsWith('sparktutor-frontend-'));
    fs.rmSync(root, { recursive: true, force: true });
  });
  const documents = [];
  class TabInputText { constructor(uri) { this.uri = uri; } }
  class TabInputTextDiff {}
  const tabs = [];
  const vscode = {
    Uri: { file: (fsPath) => ({ fsPath }) },
    ViewColumn: { One: 1 }, TabInputText, TabInputTextDiff,
    Range: class {},
    WorkspaceEdit: class { replace(uri, range, content) { this.uri = uri; this.content = content; } },
    workspace: {
      textDocuments: documents,
      getConfiguration: () => ({ get: () => true }),
      applyEdit: async (edit) => {
        const document = documents.find((item) => item.uri.fsPath === edit.uri.fsPath);
        document.text = edit.content;
        document.isDirty = true;
        return true;
      },
      openTextDocument: async (uri) => {
        const existing = documents.find((item) => item.uri.fsPath === uri.fsPath);
        if (existing) return existing;
        const document = {
          uri, text: fs.readFileSync(uri.fsPath, 'utf8'), isDirty: false,
          getText() { return this.text; }, positionAt(offset) { return offset; },
          async save() { fs.writeFileSync(uri.fsPath, this.text); this.isDirty = false; return true; },
        };
        documents.push(document);
        return document;
      },
    },
    window: {
      showInformationMessage() {},
      async showTextDocument(document) { tabs.push({ input: new TabInputText(document.uri) }); },
      tabGroups: {
        all: [{ tabs }], async close(closing) {
          for (const tab of closing) {
            const documentIndex = documents.findIndex((doc) => doc.uri.fsPath === tab.input.uri.fsPath);
            if (documentIndex >= 0) documents.splice(documentIndex, 1);
          }
          return true;
        },
      },
    },
  };
  const { WorkspaceManager } = loadTs('workspaceManager.ts', { vscode });
  return { root, manager: new WorkspaceManager(root), documents };
}

test('scripts have independent Spark lifecycles and keep edited content on revisit', async (t) => {
  const { manager, documents } = workspaceFixture(t);
  manager.setStepType('script');
  const first = await manager.openExercise('course', 'lesson', 'one', 'spark.stop() # one');
  const second = await manager.openExercise('course', 'lesson', 'two', 'spark.stop() # two');
  assert.notEqual(first.fsPath, second.fsPath);
  documents.find((doc) => doc.uri.fsPath === first.fsPath).text = 'student_answer()';
  await manager.openExercise('course', 'lesson', 'one', 'spark.stop() # one');
  assert.equal(manager.getCurrentCode(), 'student_answer()');
  assert.equal(fs.readFileSync(second.fsPath, 'utf8'), 'spark.stop() # two');
});

test('short answers accumulate within one lesson, without re-adding edited starters', async (t) => {
  const { manager, documents } = workspaceFixture(t);
  manager.setStepType('cmd_question');
  const first = await manager.openExercise('course', 'lesson', 'one', 'answer = None');
  const document = documents.find((doc) => doc.uri.fsPath === first.fsPath);
  document.text = document.text.replace('answer = None', 'answer = 42');
  document.isDirty = true;
  await manager.openExercise('course', 'lesson', 'one', 'answer = None');
  assert.ok(!manager.getCurrentCode().includes('answer = None'));
  const second = await manager.openExercise('course', 'lesson', 'two', 'other = 1');
  assert.equal(first.fsPath, second.fsPath);
  assert.ok(manager.getCurrentCode().includes('answer = 42'));
  assert.ok(manager.getCurrentCode().includes('other = 1'));
  const otherLesson = await manager.openExercise('course', 'other', 'one', 'fresh = 1');
  assert.notEqual(first.fsPath, otherLesson.fsPath);
  assert.ok(!manager.getCurrentCode().includes('answer = 42'));
});

test('reading and saving use hidden dirty buffers rather than stale disk files', async (t) => {
  const { manager, documents } = workspaceFixture(t);
  manager.setStepType('script');
  const file = await manager.openExercise('course', 'lesson', 'one', 'original');
  documents[0].text = 'unsaved';
  documents[0].isDirty = true;
  assert.equal(manager.getCurrentCode(), 'unsaved');
  await manager.saveCurrentExercise();
  assert.equal(fs.readFileSync(file.fsPath, 'utf8'), 'unsaved');
  manager.setStepType('text');
  assert.equal(manager.getCurrentCode(), '');
});

test('reset only removes the selected lesson exercises and preserves legacy work/data', async (t) => {
  const { root, manager } = workspaceFixture(t);
  manager.setStepType('script');
  const first = await manager.openExercise('course', 'lesson', 'one', 'one');
  const other = await manager.openExercise('course', 'other', 'one', 'other');
  const legacy = path.join(root, 'course', 'exercise.py');
  const data = path.join(root, 'course', 'lesson', 'data.csv');
  fs.writeFileSync(legacy, 'legacy');
  fs.writeFileSync(data, 'data');
  await manager.deleteExerciseFile('course', 'lesson');
  assert.ok(!fs.existsSync(first.fsPath));
  assert.equal(fs.readFileSync(other.fsPath, 'utf8'), 'other');
  assert.equal(fs.readFileSync(legacy, 'utf8'), 'legacy');
  assert.equal(fs.readFileSync(data, 'utf8'), 'data');
});

test('legacy cumulative restore is backed up, never inserted into a new script', async (t) => {
  const { root, manager } = workspaceFixture(t);
  manager.setStepType('script');
  const restored = 'old_code()\n# --- Step 10 ---\nold_script()';
  const file = await manager.openExercise('course', 'lesson', 'one', 'new_script()', restored);
  assert.equal(fs.readFileSync(file.fsPath, 'utf8'), 'new_script()');
  const backup = path.join(root, 'course', 'lesson', 'legacy_restored_one.py');
  assert.equal(fs.readFileSync(backup, 'utf8'), restored);
  await manager.deleteExerciseFile('course', 'lesson');
  assert.ok(fs.existsSync(backup));
});

test('workspace rejects course paths that escape its storage root', async (t) => {
  const { manager } = workspaceFixture(t);
  manager.setStepType('script');
  await assert.rejects(manager.openExercise('../outside', 'lesson', 'one', 'code'), /标识无效/);
});

test('external AI review echoes the backend review id when parsing the answer', async () => {
  const calls = [];
  const expected = { passed: true, feedback: [], encouragement: '课程测试通过', skillSignals: [] };
  const bridge = { async call(method, params) {
    calls.push({ method, params });
    return method === 'buildReviewPrompt'
      ? { needsAiReview: true, reviewId: 'review-for-current-step', messages: [{ role: 'user', content: 'review' }] }
      : expected;
  } };
  const { AiRouter } = loadTs('aiRouter.ts', { vscode: {} });
  const router = new AiRouter(bridge);
  const result = await router.reviewViaExternal({ code: 'answer()' }, async () => 'model response', 'AI');
  assert.equal(result, expected);
  assert.deepEqual(calls[1], { method: 'parseReviewResponse', params: {
    rawText: 'model response', reviewId: 'review-for-current-step',
  } });
});

test('missing AI provider completes the pending submission with a failure record', async () => {
  const calls = [];
  const expected = { passed: false, feedback: [], encouragement: '', skillSignals: [] };
  const bridge = { async call(method, params) {
    calls.push({ method, params });
    return method === 'buildReviewPrompt'
      ? { needsAiReview: true, reviewId: 'no-provider', messages: [] }
      : expected;
  } };
  const { AiRouter } = loadTs('aiRouter.ts', { vscode: {} });
  const router = new AiRouter(bridge);
  router.resolveProvider = async () => 'none';
  assert.equal(await router.submitCode({ code: 'answer()' }), expected);
  assert.equal(calls[1].method, 'completeReviewFailure');
  assert.equal(calls[1].params.reviewId, 'no-provider');
  assert.match(calls[1].params.message, /尚未配置/);
});

test('external transport failure is finalized once through the matching review id', async () => {
  const calls = [];
  const expected = { passed: false, feedback: [], encouragement: '', skillSignals: [] };
  const bridge = { async call(method, params) {
    calls.push({ method, params });
    return method === 'buildReviewPrompt'
      ? { needsAiReview: true, reviewId: 'network-failure', messages: [] }
      : expected;
  } };
  const { AiRouter } = loadTs('aiRouter.ts', { vscode: {} });
  const router = new AiRouter(bridge);
  const result = await router.reviewViaExternal({ code: 'answer()' }, async () => {
    throw new Error('connection refused');
  }, 'AI');
  assert.equal(result, expected);
  assert.deepEqual(calls.map((call) => call.method), ['buildReviewPrompt', 'completeReviewFailure']);
  assert.equal(calls[1].params.reviewId, 'network-failure');
  assert.match(calls[1].params.message, /connection refused/);
});

test('a completed local check never sends or finalizes an external review', async () => {
  const calls = [];
  const expected = { passed: true, feedback: [], encouragement: '', skillSignals: [] };
  const bridge = { async call(method) {
    calls.push(method);
    return { needsAiReview: false, localResult: expected };
  } };
  const { AiRouter } = loadTs('aiRouter.ts', { vscode: {} });
  const router = new AiRouter(bridge);
  const result = await router.reviewViaExternal({ code: 'answer()' }, async () => {
    assert.fail('local result must not contact AI');
  }, 'AI');
  assert.equal(result, expected);
  assert.deepEqual(calls, ['buildReviewPrompt']);
});

test('a stale parse rejection never tries to complete the newer pending review', async () => {
  const calls = [];
  const bridge = { async call(method) {
    calls.push(method);
    if (method === 'buildReviewPrompt') {
      return { needsAiReview: true, reviewId: 'old', messages: [] };
    }
    throw new Error('评审已过期');
  } };
  const { AiRouter } = loadTs('aiRouter.ts', { vscode: {} });
  const router = new AiRouter(bridge);
  const result = await router.reviewViaExternal({ code: 'answer()' }, async () => 'old response', 'AI');
  assert.equal(result.passed, false);
  assert.match(result.feedback[0].message, /评审已过期/);
  assert.deepEqual(calls, ['buildReviewPrompt', 'parseReviewResponse']);
});

test('a stale failure token reports failure without retrying or replacing the active review', async () => {
  const calls = [];
  const bridge = { async call(method) {
    calls.push(method);
    if (method === 'buildReviewPrompt') {
      return { needsAiReview: true, reviewId: 'old', messages: [] };
    }
    throw new Error('提交已变化');
  } };
  const { AiRouter } = loadTs('aiRouter.ts', { vscode: {} });
  const router = new AiRouter(bridge);
  const result = await router.reviewViaExternal({ code: 'answer()' }, async () => {
    throw new Error('network failure');
  }, 'AI');
  assert.equal(result.passed, false);
  assert.match(result.feedback[0].message, /提交已变化/);
  assert.deepEqual(calls, ['buildReviewPrompt', 'completeReviewFailure']);
});

test('unverified legacy database code is backed up by content without overwriting existing work', async (t) => {
  const { root, manager } = workspaceFixture(t);
  const code = 'old_answer = 42';
  const expected = path.join(root, 'course', 'lesson', `legacy_restored_${helpers.stableHash(code)}.py`);
  manager.backupLegacyCode('course', 'lesson', code);
  assert.equal(fs.readFileSync(expected, 'utf8'), code);
  manager.backupLegacyCode('course', 'lesson', code);
  assert.equal(fs.readdirSync(path.dirname(expected)).length, 1);
  // If the learner edited a previous backup, preserve it and create another.
  fs.writeFileSync(expected, 'learner_edited_backup = True');
  manager.backupLegacyCode('course', 'lesson', code);
  assert.equal(fs.readFileSync(expected, 'utf8'), 'learner_edited_backup = True');
  assert.equal(fs.readFileSync(expected.replace('.py', '_1.py'), 'utf8'), code);
  manager.setStepType('script');
  const exercise = await manager.openExercise('course', 'lesson', 'one', 'new_starter()');
  assert.equal(fs.readFileSync(exercise.fsPath, 'utf8'), 'new_starter()');
  await manager.deleteExerciseFile('course', 'lesson');
  assert.ok(fs.existsSync(expected));
  assert.ok(fs.existsSync(expected.replace('.py', '_1.py')));
});

test('execution RPC timeouts leave room for Spark timeout and cleanup', async (t) => {
  const { Bridge } = loadTs('bridge.ts', { vscode: {
    workspace: { getConfiguration: () => ({ get: () => 'python' }) },
  } });
  const bridge = new Bridge(__dirname);
  bridge.proc = { killed: false, stdin: { writable: true, write() {} } };
  t.mock.method(global, 'setTimeout', (callback, delay) => ({ callback, delay }));
  for (const [method, expected] of [
    ['run', 180000], ['submit', 180000], ['buildReviewPrompt', 180000], ['loadLesson', 120000],
  ]) {
    const pending = bridge.call(method);
    const rejection = assert.rejects(pending, new RegExp(`超过 ${expected / 1000} 秒`));
    const entry = [...bridge.pending.values()][0];
    assert.equal(entry.timer.delay, expected);
    entry.timer.callback();
    await rejection;
  }
  const pending = bridge.call('run', {}, 5000);
  const rejection = assert.rejects(pending, /超过 5 秒/);
  const entry = [...bridge.pending.values()][0];
  assert.equal(entry.timer.delay, 5000);
  entry.timer.callback();
  await rejection;
});
