// Platform-neutral sync core. The parent plugin owns authentication, the vault
// wrapper, and the single-flight lock. No credentials enter the local index.
export interface Transport {
  request(method: 'GET' | 'POST', path: string, body?: unknown): Promise<any>;
  binary(path: string): Promise<ArrayBuffer>;
}
export interface VaultAdapter {
  read(path: string): Promise<string>;
  write(path: string, text: string): Promise<void>;
  exists(path: string): Promise<boolean>;
  mkdir(path: string): Promise<void>;
  writeBinary(path: string, bytes: ArrayBuffer): Promise<void>;
  readBinary(path: string): Promise<ArrayBuffer>;
  list(path: string): Promise<{ files: string[]; folders: string[] }>;
  /** Implement with a temporary sibling + rename when the host supports it. */
  writeAtomic?(path: string, text: string): Promise<void>;
}
export type Selection = { scope: 'all' } | { scope: 'sources'; ids: number[] };
export interface SyncProgress { phase: string; completed: number; total: number }
export interface SyncOptions {
  transport: Transport; vault: VaultAdapter; root?: string; selection?: Selection;
  onProgress?: (progress: SyncProgress) => void;
  hash?: (bytes: Uint8Array) => Promise<string>;
}
export interface SyncCounts { created: number; updated: number; unchanged: number; conflicts: number; attachments: number; retained: number }
type Entry = { revision: string; hash: string; remote_deleted?: boolean };
type Index = { version: 1; notes: Record<string, Entry> };
type Attachment = { filename: string; sha256: string; size: number | null; available: boolean; url: string };
type Manifest = { protocol: 1; snapshot: string; scope: { mode: 'all' | 'sources'; source_ids: number[] }; notes: { id: number; revision: string }[]; attachments: Attachment[] };
const HEX = /^[a-f0-9]{64}$/;
const IMAGE = /^([a-f0-9]{64})\.(jpg|jpeg|png|gif|webp)$/;
const MAX_IMAGE = 32 * 1024 * 1024;
const positive = (n: unknown): n is number => Number.isSafeInteger(n) && Number(n) > 0;
function requireValue(ok: unknown, message: string): asserts ok { if (!ok) throw new Error(message); }
function rootPath(root: string): string {
  requireValue(typeof root === 'string' && root.length > 0 && root.length <= 200, 'Invalid managed root');
  requireValue(root.split('/').every(p => p && p !== '.' && p !== '..' && !/[\\:*?"<>|\[\]#^\x00-\x1f]/.test(p) && !/[. ]$/.test(p) && !/^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/i.test(p)), 'Unsafe managed root');
  return root;
}
function selectionValue(selection: Selection): Selection {
  requireValue(selection && (selection.scope === 'all' || selection.scope === 'sources'), 'Invalid selection');
  if (selection.scope === 'all') return { scope: 'all' };
  requireValue(Array.isArray(selection.ids) && selection.ids.length <= 1000 && selection.ids.every(positive) && new Set(selection.ids).size === selection.ids.length, 'Invalid source IDs');
  return { scope: 'sources', ids: [...selection.ids].sort((a, b) => a - b) };
}
function manifestPath(s: Selection): string {
  return s.scope === 'all' ? '/api/sync/manifest' : '/api/sync/manifest?scope=sources' + s.ids.map(id => '&source_ids=' + id).join('');
}
function validateManifest(m: any, s: Selection): asserts m is Manifest {
  requireValue(m && m.protocol === 1 && typeof m.snapshot === 'string' && HEX.test(m.snapshot), 'Invalid manifest');
  requireValue(m.scope && m.scope.mode === s.scope && Array.isArray(m.scope.source_ids) && JSON.stringify(m.scope.source_ids) === JSON.stringify(s.scope === 'sources' ? s.ids : []), 'Manifest scope mismatch');
  requireValue(Array.isArray(m.notes) && m.notes.length <= 100000 && Array.isArray(m.attachments) && m.attachments.length <= 100000, 'Invalid manifest indexes');
  let previous = 0;
  for (const n of m.notes) { requireValue(n && positive(n.id) && n.id > previous && typeof n.revision === 'string' && HEX.test(n.revision), 'Invalid note index'); previous = n.id; }
  let last = '';
  for (const a of m.attachments) {
    requireValue(a && typeof a.filename === 'string' && IMAGE.test(a.filename) && a.filename > last && a.sha256 === a.filename.slice(0, 64), 'Invalid attachment filename/hash');
    requireValue(a.url === '/api/attachments/' + a.filename && typeof a.available === 'boolean', 'Unsafe attachment URL');
    requireValue(a.available ? Number.isSafeInteger(a.size) && a.size >= 0 && a.size <= MAX_IMAGE : a.size === null, 'Invalid attachment size');
    last = a.filename;
  }
}
function validateIndex(i: any): asserts i is Index {
  requireValue(i && i.version === 1 && i.notes && typeof i.notes === 'object' && !Array.isArray(i.notes), 'Invalid local index');
  for (const [id, e] of Object.entries(i.notes) as [string, any][]) {
    requireValue(/^[1-9][0-9]*$/.test(id) && positive(Number(id)) && e && HEX.test(e.revision) && HEX.test(e.hash) && (e.remote_deleted === undefined || typeof e.remote_deleted === 'boolean'), 'Invalid local index entry');
  }
}
const escapeText = (text: string): string => text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/([\\`*_{}\[\]()!#|~])/g, '\\$1');
function jsonValue(value: unknown): string { return JSON.stringify(value).replace(/[\u2028\u2029]/g, c => c === '\u2028' ? '\\u2028' : '\\u2029'); }
/** Strip control/format chars (Cc/Cf approximation) while keeping \n and \t. */
const INVISIBLE = /[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F\u200B-\u200F\u2028\u2029\u2060\uFEFF]/g;
const INVISIBLE_CHAR = /[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F\u200B-\u200F\u2028\u2029\u2060\uFEFF]/;
function sectionHeadings(text: string): string { return text.replace(/^【([^】]+)】$/gm, '### $1'); }
/**
 * Preserve passive structural Markdown (headings, lists, emphasis, fences) from
 * untrusted content while making active syntax visibly literal: raw HTML,
 * autolinks and Markdown/wiki links become inert entities, every fence is
 * normalized to a passive "text" language, and unbalanced fences are closed.
 * Mirrors backend obsidian_adapter/render.py _markdown().
 */
function structuralMarkdown(text: string): string {
  let clean = String(text ?? '').replace(/\r\n/g, '\n').replace(/\r/g, '\n').replace(INVISIBLE, '');
  const output: string[] = [];
  let fenceChar: string | null = null;
  let fenceSize = 0;
  for (const raw of clean.split('\n')) {
    let line = raw;
    const fence = /(`{3,}|~{3,})([^`~]*)$/.exec(line);
    if (fence) {
      const prefix = line.slice(0, fence.index);
      const marker = fence[1], info = fence[2];
      if (fenceChar === null) { fenceChar = marker[0]; fenceSize = marker.length; line = prefix + marker + 'text'; }
      else if (marker[0] === fenceChar && marker.length >= fenceSize && !info.trim()) { fenceChar = null; line = prefix + marker; }
      else line = prefix + marker + 'text';
    }
    line = line.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/\\/g, '&#92;').replace(/\[/g, '&#91;').replace(/\]/g, '&#93;')
      .replace(/(`+)(\s*\$?=)/g, (m, ticks, tail) => ticks + tail.replace('=', '&#61;'));
    output.push(line);
  }
  if (fenceChar !== null) output.push(fenceChar.repeat(fenceSize));
  return output.join('\n');
}
/** Quote every line (blank lines as bare ">") so the result works inside a callout. */
function calloutLines(text: string): string { return text.split('\n').map(line => line ? '> ' + line : '>').join('\n'); }
/** Same-origin-safe external link check: only plain http(s) URLs with a host. */
function safeLink(url: unknown): string | null {
  if (typeof url !== 'string' || !url) return null;
  if (/\s/.test(url) || INVISIBLE_CHAR.test(url) || url.includes('\\')) return null;
  try {
    const parsed = new URL(url);
    if ((parsed.protocol !== 'http:' && parsed.protocol !== 'https:') || !parsed.hostname || parsed.username || parsed.password) return null;
    return encodeURI(url);
  } catch { return null; }
}
/** User-supplied HTML/Markdown is text, never executable HTML or a remote embed. */
export function renderNote(note: any, root = 'PKM'): string {
  rootPath(root);
  const fields: Record<string, unknown> = { 'PKM编号': note.id, '标题': note.title, '来源': note.source ?? null, '原文地址': note.source_url ?? null, '标签': note.tags ?? [], '创建时间': note.created_at ?? null, '更新时间': note.updated_at ?? null, '远端版本': note.revision };
  const front = Object.entries(fields).map(([key, value]) => key + ': ' + jsonValue(value)).join('\n');
  let result = '---\n' + front + '\n---\n\n# ' + escapeText(note.title).replace(/\r?\n/g, ' ') + '\n\n';
  const link = safeLink(note.source_url);
  if (link) result += '原文链接：<' + link + '>\n\n';
  if (note.ai_summary) result += '> [!abstract]+ AI 摘要\n' + calloutLines(structuralMarkdown(sectionHeadings(note.ai_summary))) + '\n\n';
  const content = typeof note.content === 'string' && note.content.trim() ? note.content : '暂无正文。';
  result += '> [!quote]- 原文\n' + calloutLines(structuralMarkdown(content)) + '\n';
  const names = new Set<string>();
  for (const image of note.images ?? []) if (image && typeof image.path === 'string' && IMAGE.test(image.path)) names.add(image.path);
  if (names.size) result += '\n## 图片\n\n' + [...names].map(name => '![[' + root + '/附件/' + name + ']]').join('\n\n') + '\n';
  return result;
}
function validateNote(n: any, expected: { id: number; revision: string }, attachments: Set<string>): void {
  requireValue(n && n.id === expected.id && n.revision === expected.revision && typeof n.title === 'string' && typeof n.content === 'string' && (n.ai_summary == null || typeof n.ai_summary === 'string') && Array.isArray(n.images), 'Invalid batch note');
  requireValue(n.tags == null || (Array.isArray(n.tags) && n.tags.every((t: unknown) => typeof t === 'string')), 'Invalid note tags');
  for (const image of n.images) if (image && typeof image.path === 'string' && IMAGE.test(image.path)) requireValue(attachments.has(image.path), 'Note attachment absent from manifest');
}
class Restart extends Error {}
function retryable(e: any): boolean { return e instanceof Restart || e?.status === 409 || e?.statusCode === 409; }
export async function runSync(options: SyncOptions): Promise<SyncCounts> {
  const { transport, vault } = options;
  const root = rootPath(options.root ?? 'PKM');
  const selection = selectionValue(options.selection ?? { scope: 'all' });
  const indexPath = root + '/.pkm/index.json';
  const hash = async (bytes: Uint8Array): Promise<string> => {
    let result: string;
    if (options.hash) result = await options.hash(bytes);
    else {
      requireValue(globalThis.crypto?.subtle, 'SHA-256 requires Web Crypto or an injected hash implementation');
      const digest = await globalThis.crypto.subtle.digest('SHA-256', new Uint8Array(bytes).buffer);
      result = [...new Uint8Array(digest)].map(n => n.toString(16).padStart(2, '0')).join('');
    }
    requireValue(typeof result === 'string' && HEX.test(result), 'Invalid SHA-256 implementation');
    return result;
  };
  const textHash = (text: string) => hash(new TextEncoder().encode(text));
  const progress = (phase: string, completed: number, total: number) => options.onProgress?.({ phase, completed, total });
  let baseline: Index = { version: 1, notes: {} };
  if (await vault.exists(indexPath)) { baseline = JSON.parse(await vault.read(indexPath)); validateIndex(baseline); }
  else if (await vault.exists(root)) {
    const listing = await vault.list(root);
    requireValue(listing.files.length === 0 && listing.folders.length === 0, 'Managed root is not empty and has no PKM index; choose an empty folder');
  }
  // No vault writes take place until all batches, bytes and final manifest validate.
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      progress('manifest', 0, 1);
      const manifest = await transport.request('GET', manifestPath(selection));
      validateManifest(manifest, selection);
      const counts: SyncCounts = { created: 0, updated: 0, unchanged: 0, conflicts: 0, attachments: 0, retained: 0 };
      const next: Index = { version: 1, notes: Object.fromEntries(Object.entries(baseline.notes).map(([id, entry]) => [id, { revision: entry.revision, hash: entry.hash, ...(entry.remote_deleted ? { remote_deleted: true } : {}) }])) };
      const notePlans: { path: string; text: string; before: string | null }[] = [];
      const binaryPlans: { path: string; bytes: ArrayBuffer }[] = [];
      const locals = new Map<number, { path: string; text: string | null; hash: string | null }>();
      const changed: { id: number; revision: string }[] = [];
      for (const item of manifest.notes) {
        const path = root + '/笔记/pkm-' + item.id + '.md';
        const text = await vault.exists(path) ? await vault.read(path) : null;
        const localHash = text === null ? null : await textHash(text);
        locals.set(item.id, { path, text, hash: localHash });
        const old = baseline.notes[String(item.id)];
        if (old?.revision === item.revision && text !== null) { counts.unchanged++; delete next.notes[String(item.id)].remote_deleted; }
        else changed.push(item);
      }
      const attachmentNames = new Set<string>(manifest.attachments.map((a: Attachment) => a.filename));
      for (let start = 0; start < changed.length; start += 50) {
        const group = changed.slice(start, start + 50);
        const body: any = { snapshot: manifest.snapshot, ids: group.map(n => n.id) };
        if (selection.scope === 'sources') { body.scope = 'sources'; body.source_ids = selection.ids; }
        const batch = await transport.request('POST', '/api/sync/batch', body);
        requireValue(batch && batch.snapshot === manifest.snapshot && Array.isArray(batch.notes) && batch.notes.length === group.length, 'Invalid batch response');
        for (let j = 0; j < group.length; j++) {
          const item = group[j], note = batch.notes[j];
          validateNote(note, item, attachmentNames);
          const text = renderNote(note, root), expectedHash = await textHash(text);
          const local = locals.get(item.id)!;
          const old = baseline.notes[String(item.id)];
          if (local.hash === expectedHash) {
            next.notes[String(item.id)] = { revision: item.revision, hash: expectedHash }; counts.unchanged++;
          } else if (local.text !== null && (!old || local.hash !== old.hash)) {
            const conflictPath = root + '/笔记/pkm-' + item.id + '-conflict-' + item.revision + '.md';
            const existing = await vault.exists(conflictPath) ? await vault.read(conflictPath) : null;
            requireValue(existing === null || existing === text, 'Conflict copy was locally edited; refusing overwrite');
            if (existing === null) notePlans.push({ path: conflictPath, text, before: null });
            // Keep the previous baseline, so the conflict is never silently accepted.
            counts.conflicts++;
          } else {
            notePlans.push({ path: local.path, text, before: local.text });
            next.notes[String(item.id)] = { revision: item.revision, hash: expectedHash };
            if (old) counts.updated++; else counts.created++;
          }
        }
        progress('notes', Math.min(start + 50, changed.length), changed.length);
      }
      for (let j = 0; j < manifest.attachments.length; j++) {
        const a: Attachment = manifest.attachments[j];
        const path = root + '/附件/' + a.filename;
        let valid = false;
        if (await vault.exists(path)) {
          const bytes = await vault.readBinary(path);
          valid = bytes.byteLength <= MAX_IMAGE && (!a.available || bytes.byteLength === a.size) && await hash(new Uint8Array(bytes)) === a.sha256;
        }
        requireValue(valid || a.available, 'Attachment unavailable and no verified local copy; snapshot not committed');
        if (!valid && a.available) {
          let bytes: ArrayBuffer;
          try { bytes = await transport.binary('/api/attachments/' + a.filename); }
          catch (e: any) { if (e?.status === 404 || e?.statusCode === 404) throw new Restart('Attachment changed'); throw e; }
          requireValue(bytes instanceof ArrayBuffer && bytes.byteLength === a.size && bytes.byteLength <= MAX_IMAGE && await hash(new Uint8Array(bytes)) === a.sha256, 'Attachment integrity check failed');
          binaryPlans.push({ path, bytes }); counts.attachments++;
        }
        progress('attachments', j + 1, manifest.attachments.length);
      }
      const finalManifest = await transport.request('GET', manifestPath(selection));
      validateManifest(finalManifest, selection);
      if (JSON.stringify(finalManifest) !== JSON.stringify(manifest)) throw new Restart('Snapshot changed before commit');
      if (selection.scope === 'all') {
        const present = new Set(manifest.notes.map((n: { id: number }) => String(n.id)));
        for (const [id, entry] of Object.entries(next.notes)) if (!present.has(id)) { entry.remote_deleted = true; counts.retained++; }
      }
      // Detect user edits made while the network plan was being fetched.
      for (const plan of notePlans) {
        const now = await vault.exists(plan.path) ? await vault.read(plan.path) : null;
        requireValue(now === plan.before, 'Local note changed during sync; retry without overwriting');
      }
      const mkdir = async (path: string) => {
        let current = '';
        for (const segment of path.split('/')) { current = current ? current + '/' + segment : segment; if (!(await vault.exists(current))) await vault.mkdir(current); }
      };
      await mkdir(root + '/.pkm'); await mkdir(root + '/笔记'); await mkdir(root + '/附件');
      for (const plan of binaryPlans) await vault.writeBinary(plan.path, plan.bytes);
      for (const plan of notePlans) {
        if (vault.writeAtomic) await vault.writeAtomic(plan.path, plan.text); else await vault.write(plan.path, plan.text);
      }
      const indexText = JSON.stringify(next, null, 2) + '\n';
      if (vault.writeAtomic) await vault.writeAtomic(indexPath, indexText); else await vault.write(indexPath, indexText);
      progress('complete', manifest.notes.length, manifest.notes.length);
      return counts;
    } catch (e) { if (retryable(e) && attempt < 2) continue; throw e; }
  }
  throw new Error('Sync retry limit reached');
}
