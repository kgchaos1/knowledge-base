"use strict";
var __defProp = Object.defineProperty;
var __getOwnPropDesc = Object.getOwnPropertyDescriptor;
var __getOwnPropNames = Object.getOwnPropertyNames;
var __hasOwnProp = Object.prototype.hasOwnProperty;
var __export = (target, all) => {
  for (var name in all)
    __defProp(target, name, { get: all[name], enumerable: true });
};
var __copyProps = (to, from, except, desc) => {
  if (from && typeof from === "object" || typeof from === "function") {
    for (let key of __getOwnPropNames(from))
      if (!__hasOwnProp.call(to, key) && key !== except)
        __defProp(to, key, { get: () => from[key], enumerable: !(desc = __getOwnPropDesc(from, key)) || desc.enumerable });
  }
  return to;
};
var __toCommonJS = (mod) => __copyProps(__defProp({}, "__esModule", { value: true }), mod);

// src/core.ts
var core_exports = {};
__export(core_exports, {
  renderNote: () => renderNote,
  runSync: () => runSync
});
module.exports = __toCommonJS(core_exports);
var HEX = /^[a-f0-9]{64}$/;
var IMAGE = /^([a-f0-9]{64})\.(jpg|jpeg|png|gif|webp)$/;
var MAX_IMAGE = 32 * 1024 * 1024;
var positive = (n) => Number.isSafeInteger(n) && Number(n) > 0;
function requireValue(ok, message) {
  if (!ok) throw new Error(message);
}
function rootPath(root) {
  requireValue(typeof root === "string" && root.length > 0 && root.length <= 200, "Invalid managed root");
  requireValue(root.split("/").every((p) => p && p !== "." && p !== ".." && !/[\\:*?"<>|\[\]#^\x00-\x1f]/.test(p) && !/[. ]$/.test(p) && !/^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/i.test(p)), "Unsafe managed root");
  return root;
}
function selectionValue(selection) {
  requireValue(selection && (selection.scope === "all" || selection.scope === "sources"), "Invalid selection");
  if (selection.scope === "all") return { scope: "all" };
  requireValue(Array.isArray(selection.ids) && selection.ids.length <= 1e3 && selection.ids.every(positive) && new Set(selection.ids).size === selection.ids.length, "Invalid source IDs");
  return { scope: "sources", ids: [...selection.ids].sort((a, b) => a - b) };
}
function manifestPath(s) {
  return s.scope === "all" ? "/api/sync/manifest" : "/api/sync/manifest?scope=sources" + s.ids.map((id) => "&source_ids=" + id).join("");
}
function validateManifest(m, s) {
  requireValue(m && m.protocol === 1 && typeof m.snapshot === "string" && HEX.test(m.snapshot), "Invalid manifest");
  requireValue(m.scope && m.scope.mode === s.scope && Array.isArray(m.scope.source_ids) && JSON.stringify(m.scope.source_ids) === JSON.stringify(s.scope === "sources" ? s.ids : []), "Manifest scope mismatch");
  requireValue(Array.isArray(m.notes) && m.notes.length <= 1e5 && Array.isArray(m.attachments) && m.attachments.length <= 1e5, "Invalid manifest indexes");
  let previous = 0;
  for (const n of m.notes) {
    requireValue(n && positive(n.id) && n.id > previous && typeof n.revision === "string" && HEX.test(n.revision), "Invalid note index");
    previous = n.id;
  }
  let last = "";
  for (const a of m.attachments) {
    requireValue(a && typeof a.filename === "string" && IMAGE.test(a.filename) && a.filename > last && a.sha256 === a.filename.slice(0, 64), "Invalid attachment filename/hash");
    requireValue(a.url === "/api/attachments/" + a.filename && typeof a.available === "boolean", "Unsafe attachment URL");
    requireValue(a.available ? Number.isSafeInteger(a.size) && a.size >= 0 && a.size <= MAX_IMAGE : a.size === null, "Invalid attachment size");
    last = a.filename;
  }
}
function validateIndex(i) {
  requireValue(i && i.version === 1 && i.notes && typeof i.notes === "object" && !Array.isArray(i.notes), "Invalid local index");
  for (const [id, e] of Object.entries(i.notes)) {
    requireValue(/^[1-9][0-9]*$/.test(id) && positive(Number(id)) && e && HEX.test(e.revision) && HEX.test(e.hash) && (e.remote_deleted === void 0 || typeof e.remote_deleted === "boolean"), "Invalid local index entry");
  }
}
var escapeText = (text) => text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/([\\`*_{}\[\]()!#|~])/g, "\\$1");
function jsonValue(value) {
  return JSON.stringify(value).replace(/[\u2028\u2029]/g, (c) => c === "\u2028" ? "\\u2028" : "\\u2029");
}
var INVISIBLE = /[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F\u200B-\u200F\u2028\u2029\u2060\uFEFF]/g;
var INVISIBLE_CHAR = /[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F\u200B-\u200F\u2028\u2029\u2060\uFEFF]/;
function sectionHeadings(text) {
  return text.replace(/^【([^】]+)】$/gm, "### $1");
}
function structuralMarkdown(text) {
  let clean = String(text ?? "").replace(/\r\n/g, "\n").replace(/\r/g, "\n").replace(INVISIBLE, "");
  const output = [];
  let fenceChar = null;
  let fenceSize = 0;
  for (const raw of clean.split("\n")) {
    let line = raw;
    const fence = /(`{3,}|~{3,})([^`~]*)$/.exec(line);
    if (fence) {
      const prefix = line.slice(0, fence.index);
      const marker = fence[1], info = fence[2];
      if (fenceChar === null) {
        fenceChar = marker[0];
        fenceSize = marker.length;
        line = prefix + marker + "text";
      } else if (marker[0] === fenceChar && marker.length >= fenceSize && !info.trim()) {
        fenceChar = null;
        line = prefix + marker;
      } else line = prefix + marker + "text";
    }
    line = line.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/\\/g, "&#92;").replace(/\[/g, "&#91;").replace(/\]/g, "&#93;").replace(/(`+)(\s*\$?=)/g, (m, ticks, tail) => ticks + tail.replace("=", "&#61;"));
    output.push(line);
  }
  if (fenceChar !== null) output.push(fenceChar.repeat(fenceSize));
  return output.join("\n");
}
function calloutLines(text) {
  return text.split("\n").map((line) => line ? "> " + line : ">").join("\n");
}
function safeLink(url) {
  if (typeof url !== "string" || !url) return null;
  if (/\s/.test(url) || INVISIBLE_CHAR.test(url) || url.includes("\\")) return null;
  try {
    const parsed = new URL(url);
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:" || !parsed.hostname || parsed.username || parsed.password) return null;
    return encodeURI(url);
  } catch {
    return null;
  }
}
function renderNote(note, root = "PKM") {
  rootPath(root);
  const fields = { "PKM\u7F16\u53F7": note.id, "\u6807\u9898": note.title, "\u6765\u6E90": note.source ?? null, "\u539F\u6587\u5730\u5740": note.source_url ?? null, "\u6807\u7B7E": note.tags ?? [], "\u521B\u5EFA\u65F6\u95F4": note.created_at ?? null, "\u66F4\u65B0\u65F6\u95F4": note.updated_at ?? null, "\u8FDC\u7AEF\u7248\u672C": note.revision };
  const front = Object.entries(fields).map(([key, value]) => key + ": " + jsonValue(value)).join("\n");
  let result = "---\n" + front + "\n---\n\n# " + escapeText(note.title).replace(/\r?\n/g, " ") + "\n\n";
  const link = safeLink(note.source_url);
  if (link) result += "\u539F\u6587\u94FE\u63A5\uFF1A<" + link + ">\n\n";
  if (note.ai_summary) result += "> [!abstract]+ AI \u6458\u8981\n" + calloutLines(structuralMarkdown(sectionHeadings(note.ai_summary))) + "\n\n";
  const content = typeof note.content === "string" && note.content.trim() ? note.content : "\u6682\u65E0\u6B63\u6587\u3002";
  result += "> [!quote]- \u539F\u6587\n" + calloutLines(structuralMarkdown(content)) + "\n";
  const names = /* @__PURE__ */ new Set();
  for (const image of note.images ?? []) if (image && typeof image.path === "string" && IMAGE.test(image.path)) names.add(image.path);
  if (names.size) result += "\n## \u56FE\u7247\n\n" + [...names].map((name) => "![[" + root + "/\u9644\u4EF6/" + name + "]]").join("\n\n") + "\n";
  return result;
}
function validateNote(n, expected, attachments) {
  requireValue(n && n.id === expected.id && n.revision === expected.revision && typeof n.title === "string" && typeof n.content === "string" && (n.ai_summary == null || typeof n.ai_summary === "string") && Array.isArray(n.images), "Invalid batch note");
  requireValue(n.tags == null || Array.isArray(n.tags) && n.tags.every((t) => typeof t === "string"), "Invalid note tags");
  for (const image of n.images) if (image && typeof image.path === "string" && IMAGE.test(image.path)) requireValue(attachments.has(image.path), "Note attachment absent from manifest");
}
var Restart = class extends Error {
};
function retryable(e) {
  return e instanceof Restart || e?.status === 409 || e?.statusCode === 409;
}
async function runSync(options) {
  const { transport, vault } = options;
  const root = rootPath(options.root ?? "PKM");
  const selection = selectionValue(options.selection ?? { scope: "all" });
  const indexPath = root + "/.pkm/index.json";
  const hash = async (bytes) => {
    let result;
    if (options.hash) result = await options.hash(bytes);
    else {
      requireValue(globalThis.crypto?.subtle, "SHA-256 requires Web Crypto or an injected hash implementation");
      const digest = await globalThis.crypto.subtle.digest("SHA-256", new Uint8Array(bytes).buffer);
      result = [...new Uint8Array(digest)].map((n) => n.toString(16).padStart(2, "0")).join("");
    }
    requireValue(typeof result === "string" && HEX.test(result), "Invalid SHA-256 implementation");
    return result;
  };
  const textHash = (text) => hash(new TextEncoder().encode(text));
  const progress = (phase, completed, total) => options.onProgress?.({ phase, completed, total });
  let baseline = { version: 1, notes: {} };
  if (await vault.exists(indexPath)) {
    baseline = JSON.parse(await vault.read(indexPath));
    validateIndex(baseline);
  } else if (await vault.exists(root)) {
    const listing = await vault.list(root);
    requireValue(listing.files.length === 0 && listing.folders.length === 0, "Managed root is not empty and has no PKM index; choose an empty folder");
  }
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      progress("manifest", 0, 1);
      const manifest = await transport.request("GET", manifestPath(selection));
      validateManifest(manifest, selection);
      const counts = { created: 0, updated: 0, unchanged: 0, conflicts: 0, attachments: 0, retained: 0 };
      const next = { version: 1, notes: Object.fromEntries(Object.entries(baseline.notes).map(([id, entry]) => [id, { revision: entry.revision, hash: entry.hash, ...entry.remote_deleted ? { remote_deleted: true } : {} }])) };
      const notePlans = [];
      const binaryPlans = [];
      const locals = /* @__PURE__ */ new Map();
      const changed = [];
      for (const item of manifest.notes) {
        const path = root + "/\u7B14\u8BB0/pkm-" + item.id + ".md";
        const text = await vault.exists(path) ? await vault.read(path) : null;
        const localHash = text === null ? null : await textHash(text);
        locals.set(item.id, { path, text, hash: localHash });
        const old = baseline.notes[String(item.id)];
        if (old?.revision === item.revision && text !== null) {
          counts.unchanged++;
          delete next.notes[String(item.id)].remote_deleted;
        } else changed.push(item);
      }
      const attachmentNames = new Set(manifest.attachments.map((a) => a.filename));
      for (let start = 0; start < changed.length; start += 50) {
        const group = changed.slice(start, start + 50);
        const body = { snapshot: manifest.snapshot, ids: group.map((n) => n.id) };
        if (selection.scope === "sources") {
          body.scope = "sources";
          body.source_ids = selection.ids;
        }
        const batch = await transport.request("POST", "/api/sync/batch", body);
        requireValue(batch && batch.snapshot === manifest.snapshot && Array.isArray(batch.notes) && batch.notes.length === group.length, "Invalid batch response");
        for (let j = 0; j < group.length; j++) {
          const item = group[j], note = batch.notes[j];
          validateNote(note, item, attachmentNames);
          const text = renderNote(note, root), expectedHash = await textHash(text);
          const local = locals.get(item.id);
          const old = baseline.notes[String(item.id)];
          if (local.hash === expectedHash) {
            next.notes[String(item.id)] = { revision: item.revision, hash: expectedHash };
            counts.unchanged++;
          } else if (local.text !== null && (!old || local.hash !== old.hash)) {
            const conflictPath = root + "/\u7B14\u8BB0/pkm-" + item.id + "-conflict-" + item.revision + ".md";
            const existing = await vault.exists(conflictPath) ? await vault.read(conflictPath) : null;
            requireValue(existing === null || existing === text, "Conflict copy was locally edited; refusing overwrite");
            if (existing === null) notePlans.push({ path: conflictPath, text, before: null });
            counts.conflicts++;
          } else {
            notePlans.push({ path: local.path, text, before: local.text });
            next.notes[String(item.id)] = { revision: item.revision, hash: expectedHash };
            if (old) counts.updated++;
            else counts.created++;
          }
        }
        progress("notes", Math.min(start + 50, changed.length), changed.length);
      }
      for (let j = 0; j < manifest.attachments.length; j++) {
        const a = manifest.attachments[j];
        const path = root + "/\u9644\u4EF6/" + a.filename;
        let valid = false;
        if (await vault.exists(path)) {
          const bytes = await vault.readBinary(path);
          valid = bytes.byteLength <= MAX_IMAGE && (!a.available || bytes.byteLength === a.size) && await hash(new Uint8Array(bytes)) === a.sha256;
        }
        requireValue(valid || a.available, "Attachment unavailable and no verified local copy; snapshot not committed");
        if (!valid && a.available) {
          let bytes;
          try {
            bytes = await transport.binary("/api/attachments/" + a.filename);
          } catch (e) {
            if (e?.status === 404 || e?.statusCode === 404) throw new Restart("Attachment changed");
            throw e;
          }
          requireValue(bytes instanceof ArrayBuffer && bytes.byteLength === a.size && bytes.byteLength <= MAX_IMAGE && await hash(new Uint8Array(bytes)) === a.sha256, "Attachment integrity check failed");
          binaryPlans.push({ path, bytes });
          counts.attachments++;
        }
        progress("attachments", j + 1, manifest.attachments.length);
      }
      const finalManifest = await transport.request("GET", manifestPath(selection));
      validateManifest(finalManifest, selection);
      if (JSON.stringify(finalManifest) !== JSON.stringify(manifest)) throw new Restart("Snapshot changed before commit");
      if (selection.scope === "all") {
        const present = new Set(manifest.notes.map((n) => String(n.id)));
        for (const [id, entry] of Object.entries(next.notes)) if (!present.has(id)) {
          entry.remote_deleted = true;
          counts.retained++;
        }
      }
      for (const plan of notePlans) {
        const now = await vault.exists(plan.path) ? await vault.read(plan.path) : null;
        requireValue(now === plan.before, "Local note changed during sync; retry without overwriting");
      }
      const mkdir = async (path) => {
        let current = "";
        for (const segment of path.split("/")) {
          current = current ? current + "/" + segment : segment;
          if (!await vault.exists(current)) await vault.mkdir(current);
        }
      };
      await mkdir(root + "/.pkm");
      await mkdir(root + "/\u7B14\u8BB0");
      await mkdir(root + "/\u9644\u4EF6");
      for (const plan of binaryPlans) await vault.writeBinary(plan.path, plan.bytes);
      for (const plan of notePlans) {
        if (vault.writeAtomic) await vault.writeAtomic(plan.path, plan.text);
        else await vault.write(plan.path, plan.text);
      }
      const indexText = JSON.stringify(next, null, 2) + "\n";
      if (vault.writeAtomic) await vault.writeAtomic(indexPath, indexText);
      else await vault.write(indexPath, indexText);
      progress("complete", manifest.notes.length, manifest.notes.length);
      return counts;
    } catch (e) {
      if (retryable(e) && attempt < 2) continue;
      throw e;
    }
  }
  throw new Error("Sync retry limit reached");
}
// Annotate the CommonJS export names for ESM import in node:
0 && (module.exports = {
  renderNote,
  runSync
});
