const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const Module = require('node:module');
const test = require('node:test');
const esbuild = require('esbuild');

function fixture(action, options = {}) {
  const calls = [];
  const overview = {
    databasePath: 'local/progress.db', workspacePath: 'local/workspace', databaseExists: true,
    progressCount: 2, completedLessonCount: 1, eventCount: 3,
    privacy: { summary: '摘要不含代码', backup: '完整备份可能包含 progress.last_code',
      clear: '保留练习文件和模型密钥', model: 'AI 会发送当前代码', restore: '关闭后端再替换数据库' },
  };
  const vscode = {
    workspace: {
      async openTextDocument(document) { calls.push(['document', document]); return document; },
    },
    window: {
      async showQuickPick(items) { return items.find(item => item.action === action); },
      async showWarningMessage(message, detail, cancel, confirm) {
        calls.push(['confirm', message, detail, cancel, confirm]);
        return options.confirm ? confirm : cancel;
      },
      async showSaveDialog() { calls.push(['saveDialog']); return options.destination; },
      async showTextDocument(document) { calls.push(['showDocument', document]); },
      async showInformationMessage(message) { calls.push(['info', message]); },
      async showErrorMessage(message) { calls.push(['error', message]); },
    },
  };
  const callbacks = {
    async getOverview() { calls.push(['overview']); return overview; },
    async exportSummary() { calls.push(['summary']); return { totals: { eventCount: 3 } }; },
    async backupDatabase(destination) { calls.push(['backup', destination]); return { destination }; },
    async clearRecords() {
      calls.push(['clear']);
      if (options.failClear) throw new Error('database busy');
      return { cleared: true, deletedProgress: 2, deletedEvents: 3 };
    },
    async afterClear() { calls.push(['afterClear']); },
  };
  const filename = path.resolve(__dirname, '../src/dataManagement.ts');
  const { outputFiles } = esbuild.buildSync({ entryPoints: [filename], bundle: true, platform: 'node',
    format: 'cjs', write: false, external: ['vscode'], logLevel: 'silent' });
  const loaded = new Module(filename, module);
  loaded.paths = module.paths;
  loaded.require = id => id === 'vscode' ? vscode : require(id);
  loaded._compile(outputFiles[0].text, filename);
  return { calls, run: () => loaded.exports.openDataManagement(callbacks) };
}

function temporaryDestination(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'sparktutor-data-export-'));
  t.after(() => {
    assert.equal(path.dirname(path.resolve(root)), path.resolve(os.tmpdir()));
    assert.ok(path.basename(root).startsWith('sparktutor-data-export-'));
    fs.rmSync(root, { recursive: true, force: true });
  });
  return { scheme: 'file', fsPath: path.join(root, 'summary.json') };
}

test('overview explains local files, model data and backup migration without mutation', async () => {
  const { calls, run } = fixture('overview');
  await run();
  const content = calls.find(call => call[0] === 'document')[1].content;
  assert.match(content, /local\/progress.db/);
  assert.match(content, /progress.last_code/);
  assert.match(content, /AI 会发送当前代码/);
  assert.match(content, /关闭后端再替换数据库/);
  assert.ok(!calls.some(call => ['clear', 'backup', 'summary'].includes(call[0])));
});

test('clear defaults to cancel and never clears live state on cancellation', async () => {
  const { calls, run } = fixture('clear');
  await run();
  const confirmation = calls.find(call => call[0] === 'confirm');
  assert.equal(confirmation[2].modal, true);
  assert.equal(confirmation[3].title, '取消');
  assert.equal(confirmation[3].isCloseAffordance, true);
  assert.ok(!calls.some(call => ['clear', 'afterClear'].includes(call[0])));
});

test('failed database deletion preserves live state', async () => {
  const { calls, run } = fixture('clear', { confirm: true, failClear: true });
  await run();
  assert.ok(calls.some(call => call[0] === 'clear'));
  assert.ok(!calls.some(call => call[0] === 'afterClear'));
  assert.match(calls.find(call => call[0] === 'error')[1], /database busy/);
});

test('successful confirmed deletion resets live state only after backend acknowledgement', async () => {
  const { calls, run } = fixture('clear', { confirm: true });
  await run();
  assert.ok(calls.findIndex(call => call[0] === 'clear') < calls.findIndex(call => call[0] === 'afterClear'));
  assert.match(calls.find(call => call[0] === 'info')[1], /练习文件和模型配置已保留/);
});

test('database backup requires explicit choice after stored-code disclosure', async () => {
  const cancelled = fixture('backup');
  await cancelled.run();
  assert.ok(!cancelled.calls.some(call => ['backup', 'saveDialog'].includes(call[0])));
  const destination = { scheme: 'file', fsPath: 'chosen-backup.db' };
  const accepted = fixture('backup', { confirm: true, destination });
  await accepted.run();
  assert.match(accepted.calls.find(call => call[0] === 'confirm')[2].detail, /progress.last_code/);
  assert.deepEqual(accepted.calls.find(call => call[0] === 'backup'), ['backup', destination.fsPath]);
});

test('summary writes only the backend summary to a new user-chosen local file', async t => {
  const destination = temporaryDestination(t);
  const { calls, run } = fixture('summary', { destination });
  await run();
  assert.deepEqual(JSON.parse(fs.readFileSync(destination.fsPath, 'utf8')), { totals: { eventCount: 3 } });
  assert.ok(!calls.some(call => ['clear', 'backup'].includes(call[0])));
});

test('summary refuses existing destinations and remote destinations', async t => {
  const destination = temporaryDestination(t);
  fs.writeFileSync(destination.fsPath, 'preserve original');
  const existing = fixture('summary', { destination });
  await existing.run();
  assert.equal(fs.readFileSync(destination.fsPath, 'utf8'), 'preserve original');
  assert.match(existing.calls.find(call => call[0] === 'error')[1], /新的文件名/);
  const remote = fixture('summary', { destination: { scheme: 'vscode-remote', fsPath: destination.fsPath } });
  await remote.run();
  assert.ok(!remote.calls.some(call => call[0] === 'summary'));
  assert.match(remote.calls.find(call => call[0] === 'error')[1], /本机文件/);
});
