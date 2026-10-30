const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const template = fs.readFileSync(path.join(__dirname, '..', 'xiaohongshu_read.js'), 'utf8');
const firstId = '6add751a000000002d004019';
const secondId = '6adca990000000002b0013a2';
const urlFor = (id) => `https://www.xiaohongshu.com/explore/${id}?xsec_token=test-token`;
const detailFor = (id) => ({ note: {
  noteId: id, title: 'Example note', desc: 'Complete note body', type: 'normal',
  user: { nickname: 'Example author', userId: 'author' },
  imageList: [{ urlDefault: 'https://example.test/image.jpg' }],
  interactInfo: { likedCount: '3' }, tagList: [{ name: 'tag' }]
} });

function fixture({ id = firstId, detail = detailFor(firstId), loggedIn = true } = {}) {
  const calls = [];
  const route = { path: `/explore/${id}`, query: { xsec_token: 'test-token' } };
  const noteStore = {
    noteDetailMap: detail ? { [firstId]: detail } : {},
    setCurrentNoteId(value) { calls.push(['setCurrentNoteId', value]); },
    getNoteDetailByNoteId(value) { calls.push(['getNoteDetailByNoteId', value]); return Promise.resolve(); }
  };
  const router = {
    currentRoute: { value: route },
    push(value) { calls.push(['push', value]); Object.assign(route, value); return Promise.resolve(); }
  };
  const stores = new Map([
    ['user', { loggedIn }], ['note', noteStore], ['search', { feeds: [] }]
  ]);
  const globals = { $pinia: { _s: stores }, $router: router };
  const forbidden = () => { throw new Error('Page timers/network fetch are not allowed'); };
  const context = vm.createContext({
    URL, location: { hostname: 'www.xiaohongshu.com', pathname: `/explore/${id}`, href: urlFor(id) },
    document: { querySelector() { return { __vue_app__: { config: { globalProperties: globals } } }; } },
    setTimeout: forbidden, setInterval: forbidden, fetch: forbidden,
    __bbBrowserXhsHelper: new Proxy({}, { get() { throw new Error('Must not use cached helper'); } })
  });
  function run(request = {}) {
    const expression = template.replace('__BB_READ_REQUEST__', JSON.stringify({
      mode: 'note', phase: 'start', note_id_or_url: urlFor(firstId), ...request
    }));
    const result = vm.runInContext(expression, context, { timeout: 1000 });
    assert.equal(typeof result?.then, 'undefined', 'read step must return synchronously');
    return JSON.parse(JSON.stringify(result));
  }
  return { run, calls, noteStore, stores, router, route, context };
}

test('reads already loaded current note immediately, without navigation or cached helper', () => {
  const { run, calls } = fixture();
  const result = run();
  assert.equal(result.status, 'ready');
  assert.equal(result.data.note_id, firstId);
  assert.equal(result.data.desc, 'Complete note body');
  assert.deepEqual(result.data.images, ['https://example.test/image.jpg']);
  assert.deepEqual(calls, []);
});

test('rejects invalid, foreign, credentialed, short-link and malformed note URLs', () => {
  const { run, calls } = fixture();
  for (const input of [
    '', 'not-an-id', 'a'.repeat(23), `https://evil.test/explore/${firstId}`,
    `https://www.xiaohongshu.com.evil.test/explore/${firstId}`,
    `https://someone@www.xiaohongshu.com/explore/${firstId}`,
    `https://www.xiaohongshu.com:444/explore/${firstId}`,
    `http://www.xiaohongshu.com/explore/${firstId}`, 'https://xhslink.com/a/short',
    `https://www.xiaohongshu.com/user/profile/${firstId}`, 'javascript:alert(1)'
  ]) assert.equal(run({ note_id_or_url: input }).error_code, 'INVALID_ARGUMENT', input);
  assert.deepEqual(calls, []);
});

test('reports login requirement before reading cached note or navigating', () => {
  const { run, calls } = fixture({ loggedIn: false });
  assert.equal(run().error_code, 'LOGIN_REQUIRED');
  assert.deepEqual(calls, []);
});

test('refuses to operate on a different website', () => {
  const state = fixture();
  state.context.location.hostname = 'evil.test';
  assert.equal(state.run().error_code, 'BROWSER_PAGE_MISMATCH');
  assert.deepEqual(state.calls, []);
});

test('does not mistake a mismatched stored note ID or title-only placeholder for complete detail', () => {
  for (const detail of [detailFor(secondId), { note: { noteId: firstId, title: 'Loading' } }]) {
    const state = fixture({ detail });
    const result = state.run({ phase: 'poll' });
    assert.equal(result.status, 'pending');
    assert.equal(result.stage, 'note_detail_pending');
    assert.deepEqual(state.calls, []);
  }
});

test('starts navigation to a new note and only returns it after a later matching poll', async () => {
  const state = fixture();
  const request = { note_id_or_url: urlFor(secondId) };
  const started = state.run(request);
  assert.equal(started.status, 'pending');
  assert.equal(started.started, true);
  assert.equal(started.stage, 'navigation_started');
  assert.equal(state.calls[0][0], 'push');
  assert.equal(state.calls[0][1].path, `/explore/${secondId}`);
  await Promise.resolve();
  assert.equal(state.run({ ...request, phase: 'poll' }).status, 'pending');
  const callsAfterStart = state.calls.length;
  state.noteStore.noteDetailMap[secondId] = detailFor(secondId);
  const ready = state.run({ ...request, phase: 'poll' });
  assert.equal(ready.status, 'ready');
  assert.equal(ready.data.note_id, secondId);
  assert.equal(state.calls.length, callsAfterStart, 'poll must not trigger page actions');
});

test('does not return cached target detail while router is still on another note', () => {
  const state = fixture({ id: secondId });
  const result = state.run({ phase: 'poll' });
  assert.equal(result.status, 'pending');
  assert.equal(result.stage, 'navigation_pending');
  assert.deepEqual(state.calls, []);
});

test('resolves a bare note ID from matching search feeds without stale helper cache', () => {
  const state = fixture();
  state.stores.get('search').feeds = [{ id: secondId, xsecToken: 'search-token' }];
  const result = state.run({ note_id_or_url: secondId });
  assert.equal(result.status, 'pending');
  assert.equal(state.calls[0][1].query.xsec_token, 'search-token');
});

test('never borrows another note token for a bare ID', () => {
  const state = fixture();
  assert.equal(state.run({ note_id_or_url: secondId }).error_code, 'MISSING_XSEC_TOKEN');
  assert.deepEqual(state.calls, []);
});

test('maps first-page comments and distinguishes an unfinished empty page', () => {
  const state = fixture();
  const detail = state.noteStore.noteDetailMap[firstId];
  detail.comments = { list: [], firstRequestFinish: false };
  assert.equal(state.run({ mode: 'comments', phase: 'poll' }).status, 'pending');
  detail.comments = { list: [{
    id: 'comment', content: 'Example comment', user_info: { nickname: 'Reader', user_id: 'reader' },
    like_count: '2', sub_comment_count: '1', create_time: 123
  }], has_more: true, cursor: 'cursor' };
  const result = state.run({ mode: 'comments' });
  assert.equal(result.status, 'ready');
  assert.equal(result.data.count, 1);
  assert.equal(result.data.has_more, true);
  assert.deepEqual(result.data.comments[0], {
    id: 'comment', content: 'Example comment', author: 'Reader', author_id: 'reader',
    likes: '2', sub_comment_count: '1', created_time: 123
  });
  detail.comments = { list: [], firstRequestFinish: true, hasMore: false };
  assert.equal(state.run({ mode: 'comments' }).data.count, 0);
});

test('reports not-yet-ready page without claiming navigation was started', () => {
  const state = fixture();
  state.context.document.querySelector = () => null;
  assert.deepEqual(state.run(), { status: 'pending', stage: 'page_not_ready', started: false });
  assert.deepEqual(state.calls, []);
});
