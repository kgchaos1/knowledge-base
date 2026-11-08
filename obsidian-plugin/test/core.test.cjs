const { test } = require('node:test');
const assert = require('node:assert/strict');
const { createHash } = require('node:crypto');
const { mkdirSync } = require('node:fs');
const path = require('node:path');
// Build with the existing development dependency, no SDK or install step.
const esbuild = require('esbuild');
mkdirSync(path.join(__dirname, '../.test-build'), { recursive: true });
esbuild.buildSync({ entryPoints: [path.join(__dirname, '../src/core.ts')], bundle: true, platform: 'node', format: 'cjs', outfile: path.join(__dirname, '../.test-build/core.cjs') });
const { runSync, renderNote } = require('../.test-build/core.cjs');
const digest = bytes => createHash('sha256').update(bytes).digest('hex');
const rev = n => digest(String(n));
class Vault {
  files = new Map(); dirs = new Set(); writes = [];
  async exists(p) { return this.files.has(p) || this.dirs.has(p); }
  async read(p) { assert(this.files.has(p), p); return this.files.get(p); }
  async write(p, text) { this.writes.push(p); this.files.set(p, text); }
  async writeAtomic(p, text) { return this.write(p, text); }
  async mkdir(p) { this.dirs.add(p); }
  async writeBinary(p, data) { this.writes.push(p); this.files.set(p, data.slice(0)); }
  async readBinary(p) { return this.files.get(p).slice(0); }
  async list(p) { return { files: [...this.files.keys()].filter(x => x.startsWith(p + '/') && !x.slice(p.length + 1).includes('/')), folders: [...this.dirs].filter(x => x.startsWith(p + '/') && !x.slice(p.length + 1).includes('/')) }; }
}
function fixture() {
  const vault = new Vault();
  const state = { notes: [{ id: 1, title: '中文标题', content: '<script>x</script> ![](https://evil/img)', ai_summary: '摘要', images: [], revision: rev(1) }], attachments: [], binary: new Map(), calls: [], gets: 0, changeFinal: false };
  const transport = {
    async request(method, url, body) {
      state.calls.push({ method, url, body });
      const params = new URL('https://pkm.invalid' + url).searchParams;
      const scope = method === 'POST' ? { mode: body.scope || 'all', source_ids: body.source_ids || [] } : { mode: params.get('scope') || 'all', source_ids: params.getAll('source_ids').map(Number) };
      const notes = scope.mode === 'sources' && scope.source_ids.length === 0 ? [] : state.notes;
      const manifest = { protocol: 1, snapshot: rev(JSON.stringify([notes, state.attachments, scope])), scope, notes: notes.map(({ id, revision }) => ({ id, revision })), attachments: notes.length ? state.attachments : [] };
      if (method === 'GET') { state.gets++; if (state.changeFinal && state.gets === 2) manifest.snapshot = rev('race'); return manifest; }
      assert.equal(body.snapshot, manifest.snapshot);
      assert(body.ids.length <= 50);
      return { snapshot: manifest.snapshot, notes: body.ids.map(id => state.notes.find(n => n.id === id)) };
    },
    async binary(url) { state.calls.push({ binary: url }); const value = state.binary.get(url); assert(value); return value.slice(0); }
  };
  return { vault, state, transport, sync: extra => runSync({ vault, transport, ...extra }) };
}
test('Chinese Markdown, safe escaping, stable file and last index; unchanged sync skips batch', async () => {
  const f = fixture(); const counts = await f.sync(); assert.equal(counts.created, 1);
  const text = await f.vault.read('PKM/笔记/pkm-1.md');
  assert.match(text, /标题: "中文标题"/); assert.match(text, /\[!quote\]- 原文/); assert.match(text, /&lt;script&gt;/); assert(!text.includes('![](https'));
  assert.match(text, /\[!abstract\]\+ AI 摘要/);
  assert(text.indexOf('[!abstract]') < text.indexOf('[!quote]'));
  assert.equal(f.vault.writes.at(-1), 'PKM/.pkm/index.json');
  f.state.calls = []; await f.sync(); assert(!f.state.calls.some(c => c.method === 'POST'));
});
test('local edits survive server changes and repeated conflicts do not advance baseline', async () => {
  const f = fixture(); await f.sync();
  await f.vault.write('PKM/笔记/pkm-1.md', 'my annotation');
  f.state.notes[0] = { ...f.state.notes[0], content: 'new', revision: rev(2) };
  const first = await f.sync(); assert.equal(first.conflicts, 1);
  assert.equal(await f.vault.read('PKM/笔记/pkm-1.md'), 'my annotation');
  const index = JSON.parse(await f.vault.read('PKM/.pkm/index.json')); assert.equal(index.notes[1].revision, rev(1));
  assert.equal((await f.sync()).conflicts, 1);
  assert.equal(await f.vault.read('PKM/笔记/pkm-1-conflict-' + rev(2) + '.md'), renderNote(f.state.notes[0]));
});
test('scope uses repeated source_ids and filtered empty never marks deletion', async () => {
  const f = fixture(); await f.sync();
  await f.sync({ selection: { scope: 'sources', ids: [2, 1] } });
  assert(f.state.calls.some(c => c.url === '/api/sync/manifest?scope=sources&source_ids=1&source_ids=2'));
  f.state.notes[0] = { ...f.state.notes[0], revision: rev(3) };
  await f.sync({ selection: { scope: 'sources', ids: [2, 1] } });
  assert.deepEqual(f.state.calls.filter(c => c.method === 'POST').at(-1).body.source_ids, [1, 2]);
  await f.sync({ selection: { scope: 'sources', ids: [] } });
  assert(!JSON.parse(await f.vault.read('PKM/.pkm/index.json')).notes[1].remote_deleted);
  f.state.notes = []; await f.sync();
  assert.equal(JSON.parse(await f.vault.read('PKM/.pkm/index.json')).notes[1].remote_deleted, true);
  assert(await f.vault.exists('PKM/笔记/pkm-1.md'));
});
function image(f, bytes = new Uint8Array([1, 2, 3])) {
  const name = digest(bytes) + '.png';
  f.state.notes[0].images = [{ path: name }, { url: 'https://evil/secret' }];
  f.state.attachments = [{ filename: name, sha256: digest(bytes), size: bytes.length, available: true, url: '/api/attachments/' + name }];
  f.state.binary.set('/api/attachments/' + name, bytes.buffer); return name;
}
test('attachments validated, absolute vault embeds, repeated sync checks local bytes without fetching', async () => {
  const f = fixture(); const name = image(f); await f.sync();
  assert((await f.vault.read('PKM/笔记/pkm-1.md')).includes('![[PKM/附件/' + name + ']]'));
  f.state.calls = []; await f.sync(); assert(!f.state.calls.some(c => c.binary));
  f.vault.files.set('PKM/附件/' + name, new Uint8Array([5]).buffer); await f.sync();
  assert(f.state.calls.some(c => c.binary === '/api/attachments/' + name));
});
test('bad bytes, arbitrary attachment URLs, size and path reject before any writes', async () => {
  for (const corrupt of [f => f.state.binary.set(f.state.attachments[0].url, new Uint8Array([9,9,9]).buffer), f => f.state.attachments[0].url = 'https://evil/x', f => f.state.attachments[0].size = 32 * 1024 * 1024 + 1, f => f.state.attachments[0].filename = '../x.png']) {
    const f = fixture(); image(f); corrupt(f); await assert.rejects(f.sync()); assert.deepEqual(f.vault.writes, []);
  }
});
test('final snapshot race restarts staged plan; batch 409 restarts', async () => {
  const f = fixture(); f.state.changeFinal = true; await f.sync(); assert(f.state.gets >= 4); assert.equal(f.vault.writes.filter(p => p.endsWith('pkm-1.md')).length, 1);
  const g = fixture(); const original = g.transport.request; let fail = true;
  g.transport.request = async (...args) => { if (args[0] === 'POST' && fail) { fail = false; throw Object.assign(new Error('race'), { status: 409 }); } return original(...args); };
  await g.sync(); assert.equal(fail, false);
});
test('partial write failure recovers already matching expected note without conflict', async () => {
  const f = fixture(); await f.sync(); f.state.notes[0] = { ...f.state.notes[0], content: 'new', revision: rev(2) };
  const original = f.vault.writeAtomic.bind(f.vault); let fail = true;
  f.vault.writeAtomic = async (p, text) => { if (p.endsWith('index.json') && fail) { fail = false; throw new Error('disk interrupted'); } return original(p, text); };
  await assert.rejects(f.sync(), /disk interrupted/);
  const result = await f.sync(); assert.equal(result.conflicts, 0);
  assert.equal(JSON.parse(await f.vault.read('PKM/.pkm/index.json')).notes[1].revision, rev(2));
});
test('unavailable attachments keep valid local copy and never request remote bytes', async () => {
  const f = fixture(); const name = image(f); await f.sync();
  f.state.attachments[0] = { ...f.state.attachments[0], available: false, size: null }; f.state.calls = [];
  await f.sync(); assert(!f.state.calls.some(c => c.binary)); assert(await f.vault.exists('PKM/附件/' + name));
});
test('invalid later batch rejects the entire plan before publishing any note', async () => {
  const f = fixture(); const template = f.state.notes[0];
  f.state.notes = Array.from({ length: 51 }, (_, i) => ({ ...template, id: i + 1, revision: rev(i + 1) }));
  const original = f.transport.request;
  f.transport.request = async (...args) => { const result = await original(...args); if (args[0] === 'POST' && args[2].ids[0] === 51) result.notes = []; return result; };
  await assert.rejects(f.sync(), /Invalid batch/); assert.deepEqual(f.vault.writes, []);
});
test('injected hash works without platform imports', async () => {
  const f = fixture(); let calls = 0;
  await f.sync({ hash: async bytes => { calls++; return digest(bytes); } }); assert(calls > 0);
});
test('nonempty root without index is refused, unsafe root refused, no secrets in index', async () => {
  const f = fixture(); f.vault.dirs.add('PKM'); f.vault.files.set('PKM/my.md', 'keep'); await assert.rejects(f.sync(), /not empty/);
  await assert.rejects(f.sync({ root: '../escape' }), /Unsafe/);
  const g = fixture(); await g.sync(); const raw = await g.vault.read('PKM/.pkm/index.json'); assert(!/token|cookie|password|content|title/i.test(raw));
});
