const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const assert = require('node:assert/strict');
const test = require('node:test');
const esbuild = require('esbuild');

function treeFixture(catalogue, progress = {}) {
  const refreshes = [];
  const vscode = {
    EventEmitter: class { event() {} fire(value) { refreshes.push(value); } },
    TreeItem: class { constructor(label) { this.label = label; } },
    TreeItemCollapsibleState: { None: 0, Expanded: 2 },
    ThemeIcon: class {}, ThemeColor: class {},
  };
  const filename = path.resolve(__dirname, '../src/courseTree.ts');
  const { outputFiles } = esbuild.buildSync({ entryPoints: [filename], bundle: true,
    platform: 'node', format: 'cjs', write: false, external: ['vscode'], logLevel: 'silent' });
  const loaded = new Module(filename, module);
  loaded.require = (id) => id === 'vscode' ? vscode : require(id);
  loaded._compile(outputFiles[0].text, filename);
  const state = { catalogue, progress, failure: false };
  const provider = new loaded.exports.CourseTreeProvider({ call: async (method, params) => {
    if (method === 'listCourses') return state.catalogue;
    if (method === 'getCourseProgress') {
      if (state.failure) throw new Error('Progress store unavailable');
      return state.progress[params.courseId] || { started: false };
    }
    assert.fail(`Unexpected request: ${method}`);
  } });
  return { provider, state, refreshes };
}

async function readTree(provider) {
  const roots = await provider.getChildren();
  return Promise.all(roots.filter((node) => node.course).map(async (node) => ({
    id: node.course.id, label: node.label, children: await provider.getChildren(node),
  })));
}

test('catalogue titles survive missing progress, progress failures, and refreshed lesson additions', async () => {
  const catalogue = { courses: [{ id: 'course', title: '中文课程', lessons: ['01_new_topic'],
    lessonCount: 1, lessonTitles: { '01_new_topic': '新知识点练习' } }] };
  const { provider, state, refreshes } = treeFixture(catalogue);
  let tree = await readTree(provider);
  assert.equal(tree[0].children[0].label, '1. 新知识点练习');
  state.progress.course = { started: true, lessons: [{ id: '01_new_topic', title: 'Stale English Title', status: 'completed' }] };
  tree = await readTree(provider);
  assert.equal(tree[0].children[0].label, '1. 新知识点练习');
  assert.equal(tree[0].children[0].status, 'completed');
  state.failure = true;
  catalogue.courses[0].lessons.push('02_added_lesson');
  catalogue.courses[0].lessonTitles['02_added_lesson'] = '后来新增的课节';
  provider.refresh();
  tree = await readTree(provider);
  assert.equal(refreshes.length, 1);
  assert.deepEqual(tree[0].children.map((node) => node.label), ['1. 新知识点练习', '2. 后来新增的课节']);
  assert.ok(tree[0].children.every((node) => node.status === 'not-started'));
});

test('legacy servers use their lesson titles and unknown titles show a readable Chinese fallback', async () => {
  const { provider } = treeFixture({ courses: [{ id: 'course', title: '课程', lessonCount: 2,
    lessons: ['01_existing', '02_not_yet_translated'] }] }, { course: { started: false,
    lessons: [{ id: '01_existing', title: '教材标题', status: 'not_started' }] } });
  const tree = await readTree(provider);
  assert.deepEqual(tree[0].children.map((node) => node.label), ['1. 教材标题', '2. 课节标题暂不可用']);
});

// Used by the Python protocol integration test: real server payloads must
// reach actual TreeItem labels, rather than merely matching lesson YAML.
if (require.main === module && process.argv.includes('--protocol-fixture')) {
  (async () => {
    const payload = JSON.parse(fs.readFileSync(0, 'utf8'));
    const { provider, state } = treeFixture(payload.fresh.catalogue, payload.fresh.progress);
    for (const snapshot of [payload.fresh, payload.saved]) {
      state.catalogue = snapshot.catalogue;
      state.progress = snapshot.progress;
      for (const failed of [false, true]) {
        state.failure = failed;
        provider.refresh();
        const tree = await readTree(provider);
        assert.equal(tree.length, 2);
        assert.equal(tree.flatMap((course) => course.children).length, 19);
        for (const course of tree) {
          assert.equal(course.label, payload.expected[course.id].title);
          assert.deepEqual(course.children.map((node) => node.label), payload.expected[course.id].labels);
          if (failed) assert.ok(course.children.every((node) => node.status === 'not-started'));
          else assert.deepEqual(course.children.map((node) => node.status),
            snapshot.progress[course.id].lessons.map((lesson) => lesson.status.replaceAll('_', '-')));
        }
      }
    }
    console.log('Verified two course labels and all 19 lesson labels across fresh, saved, failed, and refreshed progress.');
  })().catch((error) => { console.error(error); process.exitCode = 1; });
}

module.exports = { treeFixture, readTree };
