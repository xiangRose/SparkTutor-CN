const path = require('node:path');
const Module = require('node:module');
const { EventEmitter } = require('node:events');
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

const { LearningEventTracker, createTrackerShutdown } = loadTs('learningEventTracker.ts');
const { exerciseContent } = loadTs('lessonHelpers.ts');
const identity = { courseId: 'course', lessonId: 'lesson', taskId: 'lesson:19',
  documentUri: 'file:///workspace/course/lesson/script_00aa.py' };
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
};
function event(text = 'answer()', options = {}) {
  return { document: { uri: { toString: () => options.uri || identity.documentUri },
    version: options.version || 2, getText: () => options.documentText ?? text },
  contentChanges: [{ text, rangeLength: options.removed || 0, rangeOffset: options.offset || 0 }] };
}
function trackerFixture(call) {
  const calls = [], warnings = [];
  const tracker = new LearningEventTracker({ call: async (...args) => {
    calls.push(args);
    return call ? call(...args) : { recorded: true };
  } }, (message) => warnings.push(message));
  return { tracker, calls, warnings };
}
async function bind(tracker, context = identity, text = '') {
  await tracker.transition(async () => tracker.bind(context, text));
}

test('script edits debounce at 500ms, transmit only counters, and never trigger diagnosis or assessment', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout', 'Date'], now: 1000 });
  const { tracker, calls } = trackerFixture();
  await bind(tracker);
  tracker.observe(event('private_source'));
  t.mock.timers.tick(300);
  tracker.observe(event('秘密🙂', { removed: 2, version: 3 }));
  tracker.observe(event('other secret', { uri: 'file:///elsewhere.py' }));
  tracker.observe(event('solution secret', { uri: 'file:///workspace/course/lesson/step_19_solution.py' }));
  t.mock.timers.tick(499);
  assert.equal(calls.length, 0);
  t.mock.timers.tick(1);
  await tracker.flush();
  assert.equal(calls.length, 1);
  const [method, params, timeout] = calls[0];
  assert.equal(method, 'recordLearningEvent');
  assert.equal(params.eventType, 'code_edit');
  assert.deepEqual(params, { eventType: 'code_edit', courseId: 'course', lessonId: 'lesson', taskId: 'lesson:19',
    data: { changeCount: 2, addedChars: 18, removedChars: 2, documentVersion: 3, burstDurationMs: 300 } });
  assert.equal(timeout, 1500);
  assert.ok(!JSON.stringify(calls).includes('private_source'));
  assert.ok(!JSON.stringify(calls).includes('workspace'));
  assert.equal(tracker.pending, null);
});

test('navigation waits for an in-flight old edit and suppresses starter/restored writes', async () => {
  const sent = deferred();
  const { tracker, calls } = trackerFixture(() => calls.length === 1 ? sent.promise : { recorded: true });
  await bind(tracker);
  tracker.observe(event('old answer'));
  const earlyFlush = tracker.flush();
  let entered = false;
  const navigation = tracker.transition(async () => {
    entered = true;
    tracker.observe(event('restored code'));
    tracker.bind({ ...identity, taskId: 'lesson:23' });
    tracker.observe(event('starter code'));
  });
  await Promise.resolve();
  assert.equal(entered, false);
  tracker.observe(event('during switch'));
  sent.resolve({ recorded: true });
  await Promise.all([navigation, earlyFlush]);
  tracker.observe(event('new answer'));
  await tracker.flush();
  assert.deepEqual(calls.map((call) => call[1].taskId), ['lesson:19', 'lesson:23']);
  assert.deepEqual(calls.map((call) => call[1].data.addedChars), [10, 10]);
});

test('a failed navigation resumes the old task; non-code steps are unbound', async () => {
  const { tracker, calls } = trackerFixture();
  await bind(tracker);
  await assert.rejects(tracker.transition(async () => { throw new Error('navigation failed'); }), /navigation failed/);
  tracker.observe(event());
  await tracker.flush();
  assert.equal(calls[0][1].taskId, 'lesson:19');
  await bind(tracker, null);
  tracker.observe(event());
  await tracker.flush();
  assert.equal(calls.length, 1);
});

test('shared short-answer buffers count only current marker block, with pre-change offsets', async () => {
  const { tracker, calls } = trackerFixture();
  const context = { ...identity, documentUri: 'file:///exercise.py', blockKey: 'current' };
  let text = '# --- SparkTutor 练习 old ---\nold()\n\n# --- SparkTutor 练习 current ---\nanswer()\n';
  await bind(tracker, context, text);
  const oldOffset = text.indexOf('old()');
  text = text.slice(0, oldOffset) + 'older()' + text.slice(oldOffset + 5);
  tracker.observe(event('older()', { uri: context.documentUri, offset: oldOffset, removed: 5, documentText: text }));
  const currentOffset = text.indexOf('answer()');
  text = text.slice(0, currentOffset) + 'fixed()\n';
  tracker.observe(event('fixed()', { uri: context.documentUri, offset: currentOffset, removed: 8, documentText: text }));
  await tracker.flush();
  assert.equal(calls.length, 1);
  assert.equal(calls[0][1].data.addedChars, 7);
  assert.equal(calls[0][1].data.removedChars, 8);
  assert.ok(!JSON.stringify(tracker).includes('fixed()'));
  // Deleting the marker crosses the boundary and is never inferred as an answer edit.
  const markerOffset = text.indexOf('# --- SparkTutor 练习 current');
  tracker.observe(event('', { uri: context.documentUri, offset: markerOffset, removed: text.length - markerOffset,
    documentText: text.slice(0, markerOffset) }));
  tracker.observe(event('unmarked answer', { uri: context.documentUri, offset: markerOffset,
    documentText: text.slice(0, markerOffset) + 'unmarked answer' }));
  await tracker.flush();
  assert.equal(calls.length, 1);
});

test('empty starters create a trackable marker, while legacy and duplicate-marker buffers are not attributed', async () => {
  const { tracker, calls } = trackerFixture();
  const context = { ...identity, blockKey: 'blank' };
  const content = exerciseContent('previous_work()', '', undefined, 'blank', false);
  assert.equal((content.match(/# --- SparkTutor 练习 blank ---/g) || []).length, 1);
  assert.equal(exerciseContent(content, '', undefined, 'blank', false), content);
  await bind(tracker, context, content);
  tracker.observe(event('new_answer()', { offset: content.length, documentText: content + 'new_answer()' }));
  await tracker.flush();
  assert.equal(calls.length, 1);
  await bind(tracker, context, 'legacy_code()');
  tracker.observe(event('more', { offset: 13, documentText: 'legacy_code()more' }));
  await tracker.flush();
  await bind(tracker, context, content + '\n# --- SparkTutor 练习 blank ---\n');
  tracker.observe(event('ambiguous'));
  await tracker.flush();
  assert.equal(calls.length, 1);
});

test('deleting the next marker invalidates attribution until a trusted task bind', async () => {
  const { tracker, calls } = trackerFixture();
  const text = '# --- SparkTutor 练习 current ---\nanswer_a()\n# --- SparkTutor 练习 next ---\nanswer_b()\n';
  const context = { ...identity, blockKey: 'current' };
  await bind(tracker, context, text);
  const offset = text.indexOf('# --- SparkTutor 练习 next');
  const end = text.indexOf('answer_b()');
  const after = text.slice(0, offset) + text.slice(end);
  tracker.observe(event('', { offset, removed: end - offset, documentText: after }));
  tracker.observe(event('wrong_task()', { offset, removed: 10, documentText: after.replace('answer_b()', 'wrong_task()') }));
  // Even restoring a marker does not make the already-invalid context trustworthy.
  tracker.observe(event('# --- SparkTutor 练习 next ---\n', { offset, documentText: text }));
  tracker.observe(event('later()', { offset: text.indexOf('answer_a()'), removed: 10,
    documentText: text.replace('answer_a()', 'later()') }));
  await tracker.flush();
  assert.equal(calls.length, 0);
  await bind(tracker, context, text);
  tracker.observe(event('correct()', { offset: text.indexOf('answer_a()'), removed: 10,
    documentText: text.replace('answer_a()', 'correct()') }));
  await tracker.flush();
  assert.equal(calls.length, 1);
});

test('shutdown waits for the final edit and session_end before one disposal, ignoring late edits', async () => {
  const edits = deferred(), end = deferred();
  const { tracker, calls } = trackerFixture((_method, params) => params.eventType === 'code_edit' ? edits.promise : end.promise);
  await bind(tracker);
  tracker.observe(event());
  let disposed = 0;
  const shutdown = createTrackerShutdown(tracker, () => disposed++);
  const first = shutdown(), second = shutdown();
  assert.equal(first, second);
  assert.equal(calls.length, 1);
  assert.equal(disposed, 0);
  tracker.observe(event('late'));
  edits.resolve({ recorded: true });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(calls.length, 2);
  assert.deepEqual(calls[1].slice(0, 2), ['recordLearningEvent', { eventType: 'session_end', data: { reason: 'extension_deactivated' } }]);
  assert.equal(disposed, 0);
  end.resolve({ recorded: true });
  await first;
  assert.equal(disposed, 1);
  await shutdown();
  assert.equal(calls.length, 2);
});

test('stale records are reported, timeouts are uncertain, and shutdown still completes', async () => {
  let fail = false;
  const { tracker, warnings, calls } = trackerFixture(async () => {
    if (fail) throw new Error('timeout');
    return { recorded: false, reason: 'stale_task' };
  });
  await bind(tracker);
  tracker.observe(event());
  await tracker.flush();
  assert.match(warnings[0], /未保存/);
  fail = true;
  tracker.observe(event());
  let disposed = false;
  await createTrackerShutdown(tracker, () => { disposed = true; })();
  assert.equal(disposed, true);
  assert.equal(calls.length, 3);
  assert.match(warnings[1], /未能确认保存/);
  assert.match(warnings[2], /会话结束记录未能确认保存/);
});

function viewFixture(call, retry) {
  const lines = [], queries = [];
  const channel = { disposed: false, show() {}, clear() { assert.ok(!this.disposed); lines.length = 0; },
    appendLine(line) { assert.ok(!this.disposed); lines.push(line); }, dispose() { this.disposed = true; } };
  const vscode = { window: { createOutputChannel: () => channel, showWarningMessage: async () => retry?.() } };
  const exports = loadTs('learningEventsView.ts', { vscode });
  const view = new exports.LearningEventsView({ call: async (...args) => { queries.push(args); return call(...args); } });
  return { view, lines, queries, ...exports };
}

test('Chinese event history includes counters and results without raw code, messages, or paths', () => {
  const { formatLearningEvent } = viewFixture(() => ({}));
  const line = formatLearningEvent({ timestamp: '2026-10-08T03:04:05Z', eventType: 'code_edit', courseId: 'course', taskId: 'lesson:19',
    data: { changeCount: 2, addedChars: 10, removedChars: 1, passed: false, code: 'SECRET_CODE', path: '/private/path', message: 'SECRET_MESSAGE' } });
  assert.match(line, /编辑代码.*lesson:19.*变更 2.*新增字符 10.*删除字符 1.*未通过/);
  assert.ok(!line.includes('SECRET'));
  assert.ok(!line.includes('/private'));
});

test('event history supports empty state and retry, and requests only the latest 200 records', async () => {
  let calls = 0;
  const { view, lines, queries } = viewFixture(async () => {
    if (++calls === 1) throw new Error('temporary');
    return { events: [], hasMore: false };
  }, () => '重试');
  await view.show();
  assert.equal(calls, 2);
  assert.deepEqual(queries[0], ['getLearningEvents', { latest: true, limit: 200 }, 5000]);
  assert.match(lines.join('\n'), /暂无学习行为记录/);
});

test('event history ignores an old response and safely disposes while a request is pending', async () => {
  const old = deferred(), later = deferred();
  let callCount = 0;
  const { view, lines } = viewFixture(() => ++callCount === 1 ? old.promise : callCount === 2
    ? Promise.resolve({ events: [], hasMore: false }) : later.promise);
  const loading = view.show();
  await view.show();
  old.resolve({ events: [{ eventType: 'code_edit', timestamp: '', taskId: 'obsolete' }], hasMore: true });
  await loading;
  assert.ok(!lines.join('').includes('obsolete'));
  const pending = view.show();
  view.dispose();
  later.reject(new Error('closed'));
  await pending;
});

test('intentional Bridge disposal cannot restart the Python server and create a new session', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  let spawnCount = 0;
  const proc = new EventEmitter();
  proc.stdout = new EventEmitter(); proc.stderr = new EventEmitter(); proc.stdin = { write() {} };
  proc.kill = () => { proc.killed = true; proc.emit('close', 0); };
  const { Bridge } = loadTs('bridge.ts', {
    child_process: { spawn: () => { spawnCount++; return proc; } },
    readline: { createInterface: () => Object.assign(new EventEmitter(), { close() {} }) },
    vscode: { workspace: { getConfiguration: () => ({ get: () => undefined }) }, window: { showErrorMessage() {} } },
  });
  const bridge = new Bridge(path.resolve(__dirname, '..'));
  const starting = bridge.start();
  proc.stderr.emit('data', Buffer.from('sparktutor-server: ready'));
  await starting;
  bridge.dispose();
  bridge.dispose();
  assert.equal(spawnCount, 1);
  assert.equal(bridge.isAlive(), false);
});

function commandFixture() {
  const commands = new Map(), calls = [], notices = [], shownSteps = [];
  const step = { id: '19', cls: 'script', output: '题面', solutionCode: 'solution.py' };
  const loaded = { step, currentIndex: 3, totalSteps: 5, lessonId: 'lesson', lessonTitle: '课节', starterCode: 'starter()' };
  let code = 'student_answer()', uri = identity.documentUri, failNavigation = false;
  const output = { show() {}, clear() {}, appendLine() {}, dispose() {} };
  const vscode = { ViewColumn: { One: 1 }, commands: {
    registerCommand: (name, handler) => { commands.set(name, handler); return { dispose() {} }; },
    executeCommand: async () => {},
  }, window: {
    showInformationMessage: async (message) => { notices.push(message); return undefined; },
    showWarningMessage: async (message) => { notices.push(message); },
    showErrorMessage: async (message) => { notices.push(message); },
    createOutputChannel: () => output,
  } };
  const bridge = { call: async (method, params) => {
    calls.push([method, params]);
    if (method === 'recordLearningEvent') return { recorded: true };
    if (method === 'loadLesson') { if (failNavigation) throw new Error('offline'); return loaded; }
    if (method === 'run') return { exitCode: 0, mode: 'dry_run' };
    if (method === 'getHint') return { hint: '提示' };
    if (method === 'getSolution') return { solution: 'reference_solution()' };
    if (method === 'getLearningEvents') return { events: [], hasMore: false };
    throw new Error(`unexpected RPC ${method}`);
  } };
  const tracker = new LearningEventTracker(bridge);
  const workspace = {
    saveCurrentExercise: async () => {}, switchCourse: async () => {}, setStepType() {},
    getCurrentCode: () => code, getCurrentUri: () => ({ toString: () => uri }),
    openExercise: async (_course, _lesson, stepKey) => {
      uri = `file:///workspace/course/lesson/script_${stepKey}.py`;
      tracker.observe(event('programmatic_starter()', { uri }));
    },
    writeSolutionFile: () => ({ toString: () => 'file:///solution.py' }),
  };
  const { registerCommands } = loadTs('commands.ts', { vscode });
  registerCommands({ extensionUri: 'extension', subscriptions: [], globalState: { update() {} } }, bridge,
    { refresh() {} }, { updateStep: (...args) => shownSteps.push(args), notifyExecDone() {}, showFeedback() {}, showHint() {} },
    workspace, { clear() {}, setFeedback() {} }, output, { setStep() {}, setDepth() {} },
    { submitCode: async (params) => { calls.push(['submit', params]); return { passed: false, feedback: [] }; } }, tracker);
  return { commands, calls, notices, shownSteps, tracker,
    edit: () => { code += '\nnew_answer()'; tracker.observe(event('new_answer()', { uri })); },
    failNavigation: () => { failNavigation = true; } };
}

test('command wiring tracks the unfiltered script task and flushes before run, submit, hint, solution, and history', async () => {
  const { commands, calls, tracker, edit } = commandFixture();
  await commands.get('sparktutor.openLesson')('course', 0, 'beginner', true);
  await tracker.flush();
  assert.equal(calls.filter(([method]) => method === 'recordLearningEvent').length, 0);
  for (const [command, method] of [['run', 'run'], ['submit', 'submit'], ['hint', 'getHint'],
    ['showSolution', 'getSolution'], ['showLearningEvents', 'getLearningEvents']]) {
    calls.length = 0;
    edit();
    await commands.get(`sparktutor.${command}`)();
    assert.deepEqual(calls.map(([rpc]) => rpc), ['recordLearningEvent', method]);
    assert.equal(calls[0][1].taskId, 'lesson:19');
    assert.equal(calls[0][1].data.addedChars, 12);
  }
});

test('dismissing the restore prompt still displays and tracks the loaded task; navigation failure resumes it', async () => {
  const { commands, calls, notices, shownSteps, tracker, edit, failNavigation } = commandFixture();
  await commands.get('sparktutor.openLesson')('course', 0, 'beginner');
  assert.equal(shownSteps.length, 1);
  assert.equal(shownSteps[0][0].id, '19');
  assert.match(notices[0], /关闭此提示将继续当前进度/);
  failNavigation();
  edit();
  await commands.get('sparktutor.openLesson')('course', 1, 'beginner');
  assert.match(notices.at(-1), /加载课程失败/);
  edit();
  await tracker.flush();
  assert.deepEqual(calls.filter(([method]) => method === 'recordLearningEvent').map(([, params]) => params.taskId),
    ['lesson:19', 'lesson:19']);
});
