const { test } = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const path = require('node:path');
const { webcrypto } = require('node:crypto');
const esbuild = require('esbuild');

// Test the real exported plugin, with only the public Obsidian boundary mocked.
// No network, actual vault, user settings, or actual SecretStorage is accessed.
const bundle = esbuild.buildSync({
  entryPoints: [path.join(__dirname, '../src/main.ts')], bundle: true,
  external: ['obsidian'], platform: 'browser', format: 'cjs', write: false,
}).outputFiles[0].text;

function fixture(saved = {}) {
  const state = { saved: [], requests: [], secrets: new Map(), notices: [], settings: [], timers: new Map(), nextTimer: 1, events: new Map(), response: { status: 200, json: {}, arrayBuffer: new ArrayBuffer(0) } };
  const element = () => ({ empty() {}, createEl() { return element(); } });
  class Plugin {
    constructor() {
      this.app = { vault: { adapter: {} }, secretStorage: {
        getSecret(id) { return state.secrets.get(id) ?? null; },
        setSecret(id, value) { assert.match(id, /^[a-z0-9-]+$/); assert(id.length <= 64, 'Actual Obsidian SecretStorage ID limit'); assert.equal(typeof value, 'string'); state.secrets.set(id, value); },
      } };
    }
    async loadData() { return structuredClone(saved); }
    async saveData(data) { state.saved.push(structuredClone(data)); }
    addSettingTab(tab) { state.tab = tab; }
    addCommand() {}
    addRibbonIcon() {}
    registerDomEvent(target, event, callback) { state.events.set(event, callback); }
  }
  class PluginSettingTab { constructor(app) { this.app = app; this.containerEl = element(); } }
  class Setting {
    constructor() { this.controls = []; state.settings.push(this); }
    setName(value) { this.name = value; return this; }
    setDesc(value) { this.description = value; return this; }
    addText(callback) { return this.control(callback); }
    addButton(callback) { return this.control(callback); }
    addToggle(callback) { return this.control(callback); }
    addDropdown(callback) { return this.control(callback); }
    control(callback) {
      const control = { inputEl: {}, setValue() { return this; }, setButtonText(value) { this.label = value; return this; }, setCta() { return this; }, setDisabled() { return this; }, addOptions() { return this; }, onChange(fn) { this.change = fn; return this; }, onClick(fn) { this.click = fn; return this; } };
      this.controls.push(control); callback(control); return this;
    }
  }
  class Modal { constructor(app) { this.app = app; this.contentEl = element(); } open() { this.onOpen(); } close() { this.onClose(); } }
  const sdk = { Plugin, PluginSettingTab, Setting, Modal, App: class {}, Notice: class { constructor(message) { state.notices.push(message); } }, normalizePath: value => value, requestUrl: async request => { state.requests.push(structuredClone(request)); return typeof state.response === 'function' ? state.response(request) : state.response; } };
  const document = { hidden: false };
  const window = { setTimeout(callback, delay) { const id = state.nextTimer++; state.timers.set(id, { callback, delay }); return id; }, clearTimeout(id) { state.timers.delete(id); } };
  const context = { module: { exports: {} }, exports: {}, require(name) { assert.equal(name, 'obsidian'); return sdk; }, document, window, URL, TextEncoder, TextDecoder, crypto: webcrypto, console };
  context.exports = context.module.exports;
  vm.runInNewContext(bundle, context, { filename: 'main.test.bundle.cjs' });
  const plugin = new context.module.exports.default();
  let serverControl;
  const changeServer = async value => {
    if (!serverControl) {
      state.settings = []; state.tab.display();
      const setting = state.settings.find(row => row.name === '服务器');
      assert(setting, 'Server setting is present');
      serverControl = setting.controls[0];
    }
    await serverControl.change(value);
    await Promise.resolve();
  };
  return { plugin, state, document, changeServer };
}

const token = 'A'.repeat(43); // Synthetic token, never an actual credential.

test('pair persists the synthetic token only in SecretStorage, not plugin data', async () => {
  const { plugin, state } = fixture({ testMode: false, server: 'https://first.invalid' });
  await plugin.onload(); state.response = { status: 200, json: { token } };
  await plugin.pair('synthetic-pair-code', 'Mock device');
  assert.equal(plugin.token, token);
  assert.equal(state.secrets.get(plugin.config.secretId), token);
  assert(plugin.config.secretId.length <= 64);
  assert(state.saved.length > 0);
  for (const saved of state.saved) {
    assert(!JSON.stringify(saved).includes(token));
    assert(!JSON.stringify(saved).includes('synthetic-pair-code'));
  }
  assert.equal(state.requests[0].url, 'https://first.invalid/api/devices/claim');
  assert.equal(state.requests[0].headers.Authorization, undefined);
});

test('local storage failure preserves claimed session and gives actionable message', async () => {
  const { plugin, state } = fixture({testMode:false,server:'https://first.invalid'});
  await plugin.onload(); state.response={status:200,json:{token}};
  plugin.app.secretStorage.setSecret=()=>{throw new Error('Invalid key');};
  await plugin.pair('synthetic-code','Device');
  assert.equal(plugin.token,token);
  assert.equal(plugin.config.secretId,'');
  assert(plugin.status.includes('服务器配对成功'));
  assert(plugin.status.includes('不要重复使用旧配对码'));
  assert(!JSON.stringify(state.saved).includes(token));
});

test('legacy sensitive/unknown data keys are not written back into plugin data', async () => {
  const { plugin, state } = fixture({ testMode: false, server: 'https://first.invalid', token: 'synthetic-legacy-secret', pairingCode: 'synthetic-old-code', unexpected: 'not config' });
  await plugin.onload(); await plugin.save();
  assert.equal(state.saved.length, 1);
  for (const key of ['token', 'pairingCode', 'unexpected']) assert.equal(Object.hasOwn(state.saved[0], key), false, `${key} must not survive config migration`);
});

test('server change clears active credentials and HTTP consent', async () => {
  const { plugin, state, changeServer } = fixture({ testMode: false, server: 'https://first.invalid' });
  await plugin.onload(); plugin.token = token; plugin.approved = plugin.config.server; plugin.config.secretId = 'pkm-old';
  await changeServer('https://second.invalid');
  assert.equal(plugin.config.server, 'https://second.invalid');
  assert.equal(plugin.token, ''); assert.equal(plugin.approved, ''); assert.equal(plugin.config.secretId, '');
  assert(!JSON.stringify(state.saved).includes(token));
});

test('server setting cannot switch origin while pairing is in flight', async () => {
  const { plugin, state, changeServer } = fixture({ testMode: false, server: 'https://first.invalid' });
  await plugin.onload();
  // Capture the already-rendered callback: hiding settings on a later redraw is
  // insufficient to guard controls that existed when the operation began.
  await changeServer('https://first.invalid');
  let release;
  state.response = () => new Promise(resolve => { release = resolve; });
  const pairing = plugin.pair('synthetic-code', 'Mock device');
  await Promise.resolve(); await Promise.resolve();
  assert.equal(plugin.busy, true); assert.equal(typeof release, 'function');
  try {
    await changeServer('https://second.invalid');
    assert.equal(plugin.config.server, 'https://first.invalid', 'Busy operation must retain its original origin');
  } finally {
    release({ status: 200, json: { token } }); await pairing;
  }
});

test('401 revocation clears memory and stored secret, and stops scheduled sync', async () => {
  const { plugin, state } = fixture({ testMode: false, server: 'https://first.invalid', auto: true });
  await plugin.onload(); plugin.token = token; plugin.config.secretId = 'pkm-test'; state.secrets.set('pkm-test', token); plugin.arm();
  assert.equal(state.timers.size, 1);
  state.response = { status: 401, json: {} };
  await assert.rejects(plugin.call('GET', '/api/sync/sources'), /撤销|失效/);
  assert.equal(plugin.token, ''); assert.equal(state.secrets.get('pkm-test'), ''); assert.equal(state.timers.size, 0);
  plugin.arm(); assert.equal(state.timers.size, 0);
});

test('auto sync defaults off, and enabled auto sync is gated by document.hidden', async () => {
  const { plugin, state, document } = fixture({ testMode: false, server: 'https://first.invalid' });
  await plugin.onload(); plugin.token = token; plugin.arm();
  assert.equal(plugin.config.auto, false); assert.equal(state.timers.size, 0);
  plugin.config.auto = true; plugin.arm(); assert.equal(state.timers.size, 1);
  document.hidden = true; state.events.get('visibilitychange')(); assert.equal(state.timers.size, 0);
  await plugin.sync(false); assert.equal(state.requests.length, 0);
  document.hidden = false; state.events.get('visibilitychange')(); assert.equal(state.timers.size, 1);
});

test('testMode defaults true, validates saved booleans and stays in config whitelist', async () => {
  for (const value of [undefined, true, false, 'false', 0, null]) {
    const { plugin, state } = fixture({ testMode: value, token, unexpected: 'drop' });
    await plugin.onload(); await plugin.save();
    assert.equal(plugin.config.testMode, typeof value === 'boolean' ? value : true);
    assert.equal(state.saved[0].testMode, plugin.config.testMode);
    assert.deepEqual(Object.keys(state.saved[0]).sort(), ['auto','minutes','root','scope','secretId','server','sourceIds','testMode'].sort());
  }
});

test('testMode skips SecretStorage and disabling it safely handles invalid legacy secret IDs', async () => {
  const { plugin, state } = fixture({ secretId: 'pkm-' + 'a'.repeat(64) });
  let reads = 0;
  plugin.app.secretStorage.getSecret = () => { reads++; throw new Error('Invalid key'); };
  await plugin.onload(); assert.equal(reads, 0);
  state.tab.display();
  const toggle = state.settings.find(row => row.name === '临时免配对测试模式');
  await toggle.controls[0].change(false);
  assert.equal(reads, 1); assert.equal(plugin.token, ''); assert.match(plugin.status, /重新配对/);
});

test('testMode omits Authorization entirely for all requests, including stale tokens', async () => {
  const { plugin, state } = fixture({ server: 'https://first.invalid' });
  await plugin.onload();
  await plugin.call('GET', '/api/sync/sources');
  plugin.token = token;
  await plugin.call('GET', '/api/sync/manifest');
  await plugin.call('POST', '/api/sync/batch', { ids: [1] });
  await plugin.call('GET', '/api/sync/images/' + 'a'.repeat(64));
  await plugin.call('POST', '/api/devices/claim', {}, true);
  for (const request of state.requests) assert.equal(Object.hasOwn(request.headers, 'Authorization'), false);
  plugin.config.testMode = false;
  await plugin.call('GET', '/api/sync/sources');
  assert.equal(state.requests.at(-1).headers.Authorization, 'Bearer ' + token);
  plugin.token = '';
  await assert.rejects(plugin.call('GET', '/api/sync/sources'), /请先配对/);
});

test('testMode 401 explains server mode, preserves stored token and never reports revocation', async () => {
  const { plugin, state } = fixture({ server: 'https://first.invalid', auto: true, secretId: 'pkm-old' });
  state.secrets.set('pkm-old', token); await plugin.onload(); plugin.token = token;
  state.response = { status: 401, json: {} };
  await assert.rejects(plugin.call('GET', '/api/sync/sources'), error => {
    assert.match(error.message, /服务器未启用免配对测试模式/);
    assert.doesNotMatch(error.message, /撤销|失效/); return true;
  });
  assert.equal(plugin.token, token); assert.equal(state.secrets.get('pkm-old'), token);
  assert.equal(Object.hasOwn(state.requests[0].headers, 'Authorization'), false);
});

test('testMode sync and timer work without token but retain visibility and HTTP consent gates', async () => {
  const { plugin, state, document } = fixture({ server: 'https://first.invalid', auto: true });
  await plugin.onload(); assert.equal(plugin.token, ''); assert.equal(state.timers.size, 1);
  plugin.adapter = () => ({ exists: async () => false });
  state.response = { status: 401, json: {} };
  await plugin.sync(true);
  assert.equal(state.requests.length, 1); assert.match(plugin.status, /服务器未启用/);
  const [timerId, timer] = state.timers.entries().next().value;
  state.timers.delete(timerId); await timer.callback();
  // Timer callback starts an async sync; wait for its request/error path to settle.
  for (let i = 0; i < 8; i++) await Promise.resolve();
  assert.equal(state.requests.length, 2);
  document.hidden = true; plugin.arm(); assert.equal(state.timers.size, 0);
  plugin.config.server = 'http://first.invalid'; document.hidden = false; plugin.arm();
  assert.equal(state.timers.size, 0);
  await plugin.sync(false); assert.equal(state.requests.length, 2);
  let consentCalls = 0; plugin.consent = async () => { consentCalls++; return false; };
  await plugin.sync(true); assert.equal(consentCalls, 1); assert.equal(state.requests.length, 2);
  plugin.approved = plugin.config.server; plugin.arm(); assert.equal(state.timers.size, 1);
  plugin.config.testMode = false; plugin.arm(); assert.equal(state.timers.size, 0);
});

test('settings warn of public server data, hide pairing in testMode, and restore pairing when disabled', async () => {
  const { plugin, state } = fixture({ server: 'https://first.invalid' }); await plugin.onload();
  const draw = () => { state.settings = []; state.tab.display(); };
  const labels = () => state.settings.flatMap(row => row.controls.map(control => control.label));
  draw();
  const toggle = state.settings.find(row => row.name === '临时免配对测试模式');
  assert.match(toggle.description, /任何人都可以读取服务器数据/);
  assert(!state.settings.some(row => ['设备名称', '配对码'].includes(row.name)));
  assert(!labels().includes('配对')); assert(!labels().includes('断开本机')); assert(labels().includes('立即同步'));
  plugin.busy = true; await toggle.controls[0].change(false); assert.equal(plugin.config.testMode, true);
  plugin.busy = false; await toggle.controls[0].change(false); draw();
  assert.equal(state.saved.at(-1).testMode, false);
  assert(state.settings.some(row => row.name === '配对码'));
  assert(labels().includes('配对')); assert(labels().includes('断开本机'));
});

test('HTTP auto sync requires explicit per-origin consent', async () => {
  const { plugin, state } = fixture(); await plugin.onload(); plugin.token = token; plugin.config.auto = true;
  plugin.arm(); assert.equal(state.timers.size, 0);
  plugin.approved = plugin.config.server; plugin.arm(); assert.equal(state.timers.size, 1);
  plugin.approved = 'http://different.invalid'; plugin.arm(); assert.equal(state.timers.size, 0);
});
