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

// src/main.ts
var main_exports = {};
__export(main_exports, {
  default: () => PKMPlugin
});
module.exports = __toCommonJS(main_exports);
var import_obsidian = require("obsidian");

// src/core.ts
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
function renderNote(note, root = "PKM") {
  rootPath(root);
  const fields = { "PKM\u7F16\u53F7": note.id, "\u6807\u9898": note.title, "\u6765\u6E90": note.source ?? null, "\u539F\u6587\u5730\u5740": note.source_url ?? null, "\u6807\u7B7E": note.tags ?? [], "\u521B\u5EFA\u65F6\u95F4": note.created_at ?? null, "\u66F4\u65B0\u65F6\u95F4": note.updated_at ?? null, "\u8FDC\u7AEF\u7248\u672C": note.revision };
  const front = Object.entries(fields).map(([key, value]) => key + ": " + jsonValue(value)).join("\n");
  let result = "---\n" + front + "\n---\n\n# " + escapeText(note.title).replace(/\r?\n/g, " ") + "\n\n";
  if (note.ai_summary) result += "## \u6458\u8981\n\n" + escapeText(note.ai_summary) + "\n\n";
  result += "> [!quote]- \u539F\u6587\n" + escapeText(note.content).split(/\r?\n/).map((line) => "> " + line).join("\n") + "\n";
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

// src/main.ts
var defaults = { server: "http://8.133.220.164:8091", root: "PKM", testMode: true, auto: false, minutes: 15, scope: "all", sourceIds: [], secretId: "" };
function origin(text) {
  const u = new URL(text.trim());
  if (!["https:", "http:"].includes(u.protocol) || u.username || u.password || !u.hostname || u.pathname !== "/" || u.search || u.hash) throw new Error("\u8BF7\u8F93\u5165\u4E0D\u542B\u8DEF\u5F84\u7684\u670D\u52A1\u5668\u5730\u5740");
  return u.origin;
}
var Confirm = class extends import_obsidian.Modal {
  constructor(app, message, resolve) {
    super(app);
    this.message = message;
    this.resolve = resolve;
  }
  onOpen() {
    this.contentEl.createEl("p", { text: this.message });
    new import_obsidian.Setting(this.contentEl).addButton((b) => b.setButtonText("\u53D6\u6D88").onClick(() => this.close())).addButton((b) => b.setButtonText("\u786E\u8BA4").setCta().onClick(() => {
      this.resolve(true);
      this.resolve = () => {
      };
      this.close();
    }));
  }
  onClose() {
    this.resolve(false);
    this.contentEl.empty();
  }
};
var PKMPlugin = class extends import_obsidian.Plugin {
  constructor() {
    super(...arguments);
    this.config = { ...defaults };
    this.token = "";
    this.approved = "";
    this.busy = false;
    this.timer = null;
    this.status = "\u5C1A\u672A\u540C\u6B65";
    this.sources = [];
  }
  async onload() {
    const saved = await this.loadData();
    this.config = { ...defaults, server: typeof saved?.server === "string" ? saved.server : defaults.server, root: typeof saved?.root === "string" ? saved.root : defaults.root, testMode: typeof saved?.testMode === "boolean" ? saved.testMode : defaults.testMode, auto: saved?.auto === true, minutes: Number(saved?.minutes) || 15, scope: saved?.scope === "sources" ? "sources" : "all", sourceIds: [], secretId: typeof saved?.secretId === "string" ? saved.secretId : "" };
    this.config.server = origin(this.config.server);
    this.config.sourceIds = Array.isArray(saved?.sourceIds) ? saved.sourceIds.filter((x) => Number.isSafeInteger(x) && Number(x) > 0) : [];
    if (![5, 15, 30, 60].includes(this.config.minutes)) this.config.minutes = 15;
    if (!this.config.testMode) this.loadToken();
    this.addSettingTab(new PKMSettings(this.app, this));
    this.addCommand({ id: "sync", name: "\u540C\u6B65\u7B14\u8BB0", callback: () => void this.sync(true) });
    this.addRibbonIcon("refresh-cw", "PKM \u540C\u6B65", () => void this.sync(true));
    this.registerDomEvent(document, "visibilitychange", () => this.arm());
    this.arm();
  }
  loadToken() {
    if (!this.config.secretId) return;
    try {
      this.token = this.app.secretStorage.getSecret(this.config.secretId) || "";
    } catch {
      this.token = "";
      this.status = "\u672C\u673A\u51ED\u8BC1\u65E0\u6CD5\u8BFB\u53D6\uFF0C\u8BF7\u5173\u95ED\u6D4B\u8BD5\u6A21\u5F0F\u540E\u91CD\u65B0\u914D\u5BF9";
    }
  }
  onunload() {
    this.stop();
    this.token = "";
    this.approved = "";
  }
  async save() {
    const c = this.config;
    await this.saveData({ server: c.server, root: c.root, testMode: c.testMode, auto: c.auto, minutes: c.minutes, scope: c.scope, sourceIds: c.sourceIds, secretId: c.secretId });
    this.arm();
  }
  stop() {
    if (this.timer !== null) {
      window.clearTimeout(this.timer);
      this.timer = null;
    }
  }
  arm() {
    this.stop();
    if (!this.config.auto || document.hidden || !this.config.testMode && !this.token || this.busy || !this.config.server.startsWith("https:") && this.approved !== this.config.server) return;
    this.timer = window.setTimeout(() => {
      this.timer = null;
      void this.sync(false);
    }, this.config.minutes * 6e4);
  }
  async consent() {
    if (this.config.server.startsWith("https:") || this.approved === this.config.server) return true;
    const ok = await new Promise((resolve) => new Confirm(this.app, "HTTP\u672A\u52A0\u5BC6\uFF0C\u914D\u5BF9\u7801\u3001\u53EA\u8BFB\u51ED\u8BC1\u548C\u7B14\u8BB0\u53EF\u80FD\u88AB\u622A\u83B7\u3002\u4EC5\u7528\u4E8E\u516C\u5F00\u5185\u5BB9\u5185\u6D4B\uFF0C\u662F\u5426\u7EE7\u7EED\uFF1F", resolve).open());
    if (ok) this.approved = this.config.server;
    return ok;
  }
  async call(method, path, body, claim = false) {
    if (!path.startsWith("/api/") || path.includes("://")) throw new Error("\u975E\u6CD5\u8BF7\u6C42\u8DEF\u5F84");
    const headers = { "Origin": this.config.server };
    if (!claim && !this.config.testMode) {
      if (!this.token) throw new Error("\u8BF7\u5148\u914D\u5BF9");
      headers.Authorization = "Bearer " + this.token;
    }
    const response = await (0, import_obsidian.requestUrl)({ url: this.config.server + path, method, headers, body: body === void 0 ? void 0 : JSON.stringify(body), contentType: body === void 0 ? void 0 : "application/json", throw: false });
    if (response.status === 401 && !claim && this.config.testMode) throw new Error("\u670D\u52A1\u5668\u672A\u542F\u7528\u514D\u914D\u5BF9\u6D4B\u8BD5\u6A21\u5F0F\uFF08HTTP 401\uFF09\u3002\u8BF7\u7531\u7BA1\u7406\u5458\u542F\u7528\u670D\u52A1\u5668\u6D4B\u8BD5\u6A21\u5F0F\uFF0C\u6216\u5173\u95ED\u672C\u63D2\u4EF6\u7684\u6D4B\u8BD5\u6A21\u5F0F\u540E\u914D\u5BF9\u3002");
    if (response.status === 401 && !claim) {
      this.token = "";
      if (this.config.secretId) this.app.secretStorage.setSecret(this.config.secretId, "");
      this.stop();
      throw new Error("\u8BBE\u5907\u5DF2\u64A4\u9500\u6216\u51ED\u8BC1\u5931\u6548\uFF0C\u8BF7\u91CD\u65B0\u914D\u5BF9");
    }
    if (response.status < 200 || response.status >= 300) {
      const error = new Error("\u8BF7\u6C42\u5931\u8D25\uFF08HTTP " + response.status + "\uFF09");
      error.status = response.status;
      throw error;
    }
    return response;
  }
  async pair(code, name) {
    if (this.busy) return;
    this.busy = true;
    this.stop();
    try {
      if (!await this.consent()) return;
      const data = (await this.call("POST", "/api/devices/claim", { code: code.trim(), name: name.trim() || "Obsidian\u8BBE\u5907" }, true)).json;
      if (typeof data?.token !== "string" || !/^[A-Za-z0-9_-]{43}$/.test(data.token)) throw new Error("\u914D\u5BF9\u54CD\u5E94\u65E0\u6548");
      this.token = data.token;
      const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(this.config.server));
      const hash = Array.from(new Uint8Array(digest)).map((x) => x.toString(16).padStart(2, "0")).join("");
      this.config.secretId = "pkm-" + hash.slice(0, 56);
      try {
        this.app.secretStorage.setSecret(this.config.secretId, this.token);
        if (this.app.secretStorage.getSecret(this.config.secretId) !== this.token) throw new Error("storage readback");
        await this.save();
        new import_obsidian.Notice("\u914D\u5BF9\u6210\u529F\uFF1B\u53EA\u8BFB\u51ED\u8BC1\u4FDD\u5B58\u5230Obsidian SecretStorage");
        this.status = "\u5DF2\u914D\u5BF9\uFF0C\u70B9\u51FB\u540C\u6B65";
      } catch {
        this.config.secretId = "";
        await this.save();
        this.status = "\u670D\u52A1\u5668\u914D\u5BF9\u6210\u529F\uFF0C\u4F46\u672C\u673A\u5B89\u5168\u5B58\u50A8\u5931\u8D25\u3002\u5F53\u524D\u4F1A\u8BDD\u53EF\u540C\u6B65\uFF1B\u4E0D\u8981\u91CD\u590D\u4F7F\u7528\u65E7\u914D\u5BF9\u7801\u3002\u91CD\u542F\u540E\u8BF7\u751F\u6210\u65B0\u7801\u91CD\u65B0\u914D\u5BF9\u3002";
        new import_obsidian.Notice(this.status);
      }
    } catch (e) {
      new import_obsidian.Notice(e instanceof Error ? e.message : "\u914D\u5BF9\u5931\u8D25");
    } finally {
      this.busy = false;
      this.arm();
    }
  }
  async disconnect() {
    if (this.busy) return;
    this.token = "";
    this.approved = "";
    this.stop();
    if (this.config.secretId) this.app.secretStorage.setSecret(this.config.secretId, "");
    this.config.secretId = "";
    await this.save();
    this.status = "\u5DF2\u6E05\u9664\u672C\u673A\u51ED\u8BC1\uFF0C\u5DF2\u6709\u7B14\u8BB0\u4FDD\u7559";
    new import_obsidian.Notice(this.status);
  }
  adapter() {
    const a = this.app.vault.adapter;
    return { read: (p) => a.read((0, import_obsidian.normalizePath)(p)), write: (p, v) => a.write((0, import_obsidian.normalizePath)(p), v), exists: (p) => a.exists((0, import_obsidian.normalizePath)(p)), mkdir: (p) => a.mkdir((0, import_obsidian.normalizePath)(p)), writeBinary: (p, v) => a.writeBinary((0, import_obsidian.normalizePath)(p), v), readBinary: (p) => a.readBinary((0, import_obsidian.normalizePath)(p)), list: (p) => a.list((0, import_obsidian.normalizePath)(p)), writeAtomic: async (p, v) => {
      const tmp = p + ".pkm-tmp";
      if (await a.exists(tmp)) throw new Error("\u4E0A\u6B21\u4E34\u65F6\u6587\u4EF6\u4ECD\u5B58\u5728\uFF0C\u8BF7\u68C0\u67E5\u540E\u91CD\u8BD5");
      await a.write(tmp, v);
      try {
        await a.rename(tmp, p);
      } catch (e) {
        if (await a.exists(tmp)) await a.remove(tmp);
        throw e;
      }
    } };
  }
  async sync(manual) {
    if (this.busy) return;
    if (!this.config.testMode && !this.token) {
      if (manual) new import_obsidian.Notice("\u8BF7\u5728\u8BBE\u7F6E\u4E2D\u914D\u5BF9");
      return;
    }
    this.busy = true;
    this.stop();
    try {
      if (document.hidden && !manual) return;
      if (!manual && this.config.server.startsWith("http:") && this.approved !== this.config.server) return;
      if (manual && !await this.consent()) return;
      const transport = { request: async (method, path, body) => (await this.call(method, path, body)).json, binary: async (path) => (await this.call("GET", path)).arrayBuffer };
      const result = await runSync({ transport, vault: this.adapter(), root: this.config.root, selection: this.config.scope === "all" ? { scope: "all" } : { scope: "sources", ids: this.config.sourceIds }, onProgress: (p) => {
        this.status = p.phase + " " + p.completed + "/" + p.total;
      } });
      this.status = "\u540C\u6B65\u5B8C\u6210 " + (/* @__PURE__ */ new Date()).toLocaleString() + "\uFF1B\u65B0\u589E " + result.created + "\uFF0C\u66F4\u65B0 " + result.updated + "\uFF0C\u51B2\u7A81 " + result.conflicts;
      new import_obsidian.Notice(this.status);
    } catch (e) {
      this.status = e instanceof Error ? e.message : "\u540C\u6B65\u5931\u8D25\uFF0C\u539F\u7B14\u8BB0\u4FDD\u7559";
      new import_obsidian.Notice(this.status);
    } finally {
      this.busy = false;
      this.arm();
    }
  }
  async refreshSources() {
    if (this.busy) return;
    this.busy = true;
    this.stop();
    try {
      if (!await this.consent()) return;
      const rows = (await this.call("GET", "/api/sync/sources")).json;
      if (!Array.isArray(rows) || rows.some((x) => !Number.isSafeInteger(x.id) || typeof x.name !== "string")) throw new Error("\u6765\u6E90\u5217\u8868\u65E0\u6548");
      this.sources = rows;
    } finally {
      this.busy = false;
      this.arm();
    }
  }
};
var PKMSettings = class extends import_obsidian.PluginSettingTab {
  constructor(app, owner) {
    super(app, owner);
    this.owner = owner;
  }
  display() {
    const { containerEl } = this;
    containerEl.empty();
    const p = this.owner;
    if (p.busy) {
      containerEl.createEl("p", { text: "\u4EFB\u52A1\u8FDB\u884C\u4E2D\uFF0C\u8BF7\u5B8C\u6210\u540E\u91CD\u65B0\u6253\u5F00\u8BBE\u7F6E\uFF0C\u671F\u95F4\u4E0D\u5141\u8BB8\u66F4\u6539\u670D\u52A1\u5668\u6216\u76EE\u5F55\u3002" });
      return;
    }
    containerEl.createEl("h2", { text: "PKM \u8FDE\u63A5\u5668" });
    containerEl.createEl("p", { text: "\u53EA\u8BFB\u63A5\u6536\u670D\u52A1\u5668\u6574\u7406\u7ED3\u679C\uFF0C\u5199\u5165\u4E13\u7528\u5B50\u76EE\u5F55\u3002\u672C\u5730\u4FEE\u6539\u4F1A\u4FDD\u7559\u51B2\u7A81\u526F\u672C\u3002\u9ED8\u8BA4\u4E0D\u81EA\u52A8\u540C\u6B65\u3002" });
    new import_obsidian.Setting(containerEl).setName("\u670D\u52A1\u5668").addText((t) => t.setValue(p.config.server).onChange((value) => {
      try {
        const next = origin(value);
        if (p.busy) return;
        if (next !== p.config.server) {
          p.token = "";
          p.approved = "";
          p.config.secretId = "";
          p.config.server = next;
          void p.save();
        }
      } catch {
      }
    }));
    new import_obsidian.Setting(containerEl).setName("\u4E13\u7528\u76EE\u5F55").setDesc("\u5EFA\u8BAEPKM\uFF0C\u4E0D\u53EF\u9009\u62E9Vault\u6839\u76EE\u5F55\u6216\u73B0\u6709\u975E\u6258\u7BA1\u6587\u4EF6\u5939").addText((t) => t.setValue(p.config.root).onChange((value) => {
      if (p.busy) return;
      p.config.root = value.trim();
      void p.save();
    }));
    new import_obsidian.Setting(containerEl).setName("\u4E34\u65F6\u514D\u914D\u5BF9\u6D4B\u8BD5\u6A21\u5F0F").setDesc("\u5371\u9669\uFF1A\u670D\u52A1\u5668\u542F\u7528\u6D4B\u8BD5\u6A21\u5F0F\u540E\uFF0C\u4EFB\u4F55\u4EBA\u90FD\u53EF\u4EE5\u8BFB\u53D6\u670D\u52A1\u5668\u6570\u636E\u3002\u4EC5\u7528\u4E8E\u5DF2\u6279\u51C6\u7684\u4E34\u65F6\u516C\u5F00\u8D44\u6599\u6D4B\u8BD5\uFF1B\u4E0D\u53D1\u9001\u4EFB\u4F55\u51ED\u8BC1\u3002\u5173\u95ED\u540E\u6062\u590D\u8BBE\u5907\u914D\u5BF9\u3002").addToggle((t) => t.setValue(p.config.testMode).onChange(async (v) => {
      if (p.busy) return;
      p.config.testMode = v;
      if (!v && !p.token) p.loadToken();
      await p.save();
      this.display();
    }));
    if (!p.config.testMode) {
      let code = "", name = "Obsidian\u8BBE\u5907";
      new import_obsidian.Setting(containerEl).setName("\u8BBE\u5907\u540D\u79F0").addText((t) => t.setValue(name).onChange((v) => name = v));
      new import_obsidian.Setting(containerEl).setName("\u914D\u5BF9\u7801").setDesc("\u7BA1\u7406\u7F51\u9875\u751F\u6210\uFF0C10\u5206\u949F\u6709\u6548\uFF1B\u4E0D\u4F1A\u4FDD\u5B58\u914D\u5BF9\u7801").addText((t) => {
        t.inputEl.type = "password";
        t.onChange((v) => code = v);
      });
      new import_obsidian.Setting(containerEl).addButton((b) => b.setButtonText("\u914D\u5BF9").onClick(async () => {
        await p.pair(code, name);
        code = "";
        this.display();
      })).addButton((b) => b.setButtonText("\u65AD\u5F00\u672C\u673A").onClick(async () => {
        await p.disconnect();
        this.display();
      }));
    }
    new import_obsidian.Setting(containerEl).addButton((b) => b.setButtonText("\u7ACB\u5373\u540C\u6B65").onClick(async () => {
      await p.sync(true);
      this.display();
    }));
    new import_obsidian.Setting(containerEl).setName("\u524D\u53F0\u5B9A\u65F6\u540C\u6B65").setDesc("\u4EC5Obsidian\u6253\u5F00\u4E14\u53EF\u89C1\u65F6\uFF1BHTTP\u9700\u672C\u6B21\u5148\u624B\u52A8\u786E\u8BA4\u3002\u624B\u673A\u9000\u51FA\u540E\u4E0D\u627F\u8BFA\u6267\u884C\u3002").addToggle((t) => t.setValue(p.config.auto).onChange(async (v) => {
      p.config.auto = v;
      await p.save();
    }));
    new import_obsidian.Setting(containerEl).setName("\u95F4\u9694\uFF08\u5206\u949F\uFF09").addDropdown((d) => d.addOptions({ "5": "5", "15": "15", "30": "30", "60": "60" }).setValue(String(p.config.minutes)).onChange(async (v) => {
      p.config.minutes = Number(v);
      await p.save();
    }));
    new import_obsidian.Setting(containerEl).setName("\u63A5\u6536\u8303\u56F4").addDropdown((d) => d.addOptions({ all: "\u5168\u90E8", sources: "\u9009\u5B9A\u6570\u636E\u6E90" }).setValue(p.config.scope).onChange(async (v) => {
      p.config.scope = v === "sources" ? "sources" : "all";
      await p.save();
      this.display();
    }));
    new import_obsidian.Setting(containerEl).addButton((b) => b.setButtonText("\u5237\u65B0\u6570\u636E\u6E90").onClick(async () => {
      try {
        await p.refreshSources();
        this.display();
      } catch (e) {
        new import_obsidian.Notice(e instanceof Error ? e.message : "\u6765\u6E90\u8BFB\u53D6\u5931\u8D25\uFF0C\u8BF7\u68C0\u67E5\u8FDE\u63A5\u548C\u63A5\u5165\u6A21\u5F0F");
      }
    }));
    if (p.config.scope === "sources") {
      containerEl.createEl("p", { text: "\u672A\u52FE\u9009\u65F6\u4E0D\u63A5\u6536\u65B0\u5185\u5BB9\uFF1B\u5DF2\u6709\u672C\u5730\u7B14\u8BB0\u4E0D\u5220\u9664\u3002" });
      for (const s of p.sources) new import_obsidian.Setting(containerEl).setName(s.name).addToggle((t) => t.setValue(p.config.sourceIds.includes(s.id)).onChange(async (v) => {
        p.config.sourceIds = v ? [.../* @__PURE__ */ new Set([...p.config.sourceIds, s.id])] : p.config.sourceIds.filter((id) => id !== s.id);
        await p.save();
      }));
    }
    containerEl.createEl("p", { text: p.status });
    containerEl.createEl("p", { text: "\u51ED\u8BC1\u7531Obsidian 1.11.4+ SecretStorage\u7BA1\u7406\uFF0C\u63D2\u4EF6data.json\u53EA\u4FDD\u5B58\u975E\u654F\u611F\u914D\u7F6E\u3002\u4E0D\u8981\u5728\u4E0D\u53EF\u4FE1\u4ED3\u5E93\u542F\u7528\u7B2C\u4E09\u65B9\u63D2\u4EF6\u3002HTTP\u5E76\u975E\u5B89\u5168\u751F\u4EA7\u63A5\u5165\u3002" });
  }
};
