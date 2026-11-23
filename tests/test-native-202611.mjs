/** HA 2026.11 native target control (frontend 20261028): PendingValueSync.
 * The pinned official willUpdate/_syncTargetTemperature/_callService/_handleButton/
 * _valueChanged bodies and the PendingValueSync class run against the popup and
 * the card. A gesture the lease drops unsent must release the native lock, or the
 * HA dial keeps a never-sent number and ignores every later state. Source-level
 * DOM model only: no browser, network, HA or controller.
 */
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import assert from 'node:assert/strict';
import { pathToFileURL, fileURLToPath } from 'node:url';
const HERE = path.dirname(fileURLToPath(import.meta.url));
const sourcePath = path.resolve(process.argv[2] || path.join(HERE, '../dist/hargassner-thermostat-popup.js'));
const module = await import(pathToFileURL(sourcePath));
const sha = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const bytes = fs.readFileSync(path.join(HERE, 'ha-native-202611-bodies.json'));
assert.equal(sha(bytes), 'db12d81fcdaf170181fe5b2f4a11e49d81272fc8bfd1b728d54a5a2577c04498');
const pinned = JSON.parse(bytes);
assert.equal(pinned.upstream_tag, '20261028.6');
assert.equal(pinned.sources[0].sha256, '121e8aab80d6ed9d026de4622c17b9db40f4a31a2f77930bcecd52b4319951cc');
assert.equal(pinned.sources[1].sha256, '18e65dbba58936ccd02e1a54eedd97cbfaacb9b51a408329eb87c6e89817d860');
const legacy = JSON.parse(fs.readFileSync(path.join(HERE, 'ha-native-bodies.json')));
assert.equal(legacy.sources[1].sha256, 'ebe061124268366ccf08901cddb4b2a0d2b7c023fd37b5a62b5bd373b8bc7971'); // debounce.ts unchanged in 20261028.6
// Mechanical TypeScript erasure only.
const erase = body => body.replaceAll('stateObj!', 'stateObj').replaceAll(' as Target', '').replaceAll(' as number', '');
const PendingValueSync = new Function(pinned.pending_value_sync_class
  .replace('export class', 'class').replace('private _scheduled = false;', '_scheduled = false;')
  .replace('private _latestCall?: symbol;', '_latestCall;').replace('public get pending(): boolean', 'get pending()')
  .replace('public schedule(): void', 'schedule()').replace('public reset(): void', 'reset()')
  .replace('public shouldSync(): boolean', 'shouldSync()')
  .replace(/public async run\(\s*call: \(\) => Promise<unknown>,\s*resync: \(\) => void\s*\): Promise<void>/, 'async run(call, resync)')
  + '\nreturn PendingValueSync;')();
const willUpdate = new Function('changedProp', erase(pinned.bodies.willUpdate).replace('super.willUpdate(changedProp);', ''));
const syncTarget = new Function(erase(pinned.bodies._syncTargetTemperature));
const callService = new Function('type', erase(pinned.bodies._callService));
const handleButton = new Function('ev', 'clamp', erase(pinned.bodies._handleButton));
const valueChanged = new Function('ev', erase(pinned.bodies._valueChanged));
const valueChanging = new Function('ev', erase(pinned.bodies._valueChanging));
const nativeDebounce = new Function('func', 'wait', 'immediate', 'window', legacy.native_debounce_body
  .replace('let timeout: number | undefined;', 'let timeout;').replace('let trailingArgs: T | undefined;', 'let trailingArgs;')
  .replace('(...args: T): void =>', '(...args) =>'));
const clamp = (value, min, max) => Math.min(max, Math.max(min, value));
const PRESETS = ['Aus', 'Auto', 'Heizen', 'Absenken', '1x Heizen', '1x Absenken'];
const BASIS = 'a'.repeat(32), FRESH = 'b'.repeat(32);
const hvac = preset => preset === 'Aus' ? 'off' : preset === 'Auto' ? 'auto' : 'heat';
const results = [];
function environment() {
  const timers = new Map(); let next = 0;
  const registry = new Map();
  class Style {
    constructor() { this.values = new Map(); }
    setProperty(key, value, priority = '') { this.values.set(key, [value, priority]); }
    getPropertyValue(key) { return this.values.get(key)?.[0] || ''; }
    getPropertyPriority(key) { return this.values.get(key)?.[1] || ''; }
    removeProperty(key) { this.values.delete(key); }
  }
  class Element extends EventTarget {
    constructor(tag = '') { super(); this.localName = tag; this.children = []; this.parentNode = null; this.style = new Style(); this.dataset = {}; this.attrs = new Map(); this.inert = false; }
    get isConnected() { return !!this.connected || !!this.parentNode?.isConnected || !!this.host?.isConnected; }
    get parentElement() { return this.parentNode?.localName ? this.parentNode : null; }
    get classList() { return { contains: value => (this.className || '').split(' ').includes(value) }; }
    attachShadow() { this.shadowRoot = new Element(); this.shadowRoot.host = this; return this.shadowRoot; }
    append(...children) { for (const child of children) { child.remove(); this.children.push(child); child.parentNode = this; } }
    insertBefore(child, anchor) { child.remove(); this.children.splice(this.children.indexOf(anchor), 0, child); child.parentNode = this; }
    insertAdjacentElement(where, child) { assert.equal(where, 'afterend'); child.remove(); this.parentNode.children.splice(this.parentNode.children.indexOf(this) + 1, 0, child); child.parentNode = this.parentNode; }
    replaceChildren(...children) { for (const old of this.children) old.parentNode = null; this.children = []; this.append(...children); }
    remove() { if (this.parentNode) this.parentNode.children.splice(this.parentNode.children.indexOf(this), 1); this.parentNode = null; }
    setAttribute(key, value) { this.attrs.set(key, String(value)); }
    getAttribute(key) { return this.attrs.get(key) ?? null; }
    removeAttribute(key) { this.attrs.delete(key); }
    querySelectorAll(selector) { return this.children.flatMap(child => [ ...(selector === '*' || child.localName === selector ? [child] : []), ...child.querySelectorAll(selector) ]); }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  }
  const doc = new Element(); doc.connected = true;
  doc.createElement = tag => { const Ctor = registry.get(tag); return Ctor ? new Ctor() : new Element(tag); };
  const env = { document: doc, HTMLElement: Element, customElements: { get: name => registry.get(name), define: (name, value) => registry.set(name, value), whenDefined: async name => registry.get(name) },
    MutationObserver: class { observe() {} disconnect() {} },
    queueMicrotask, CustomEvent: class extends Event { constructor(type, options) { super(type, options); this.detail = options?.detail; } },
    setTimeout(fn, delay, ...args) { const id = ++next; timers.set(id, { fn: () => fn(...args), delay }); return id; },
    clearTimeout(id) { timers.delete(id); }, addEventListener() {}, removeEventListener() {}, timers,
  };
  // The 20261028 control: a stateObj change copies into the dial only while
  // PendingValueSync allows it (Lit property semantics: identical objects are no change).
  class Temperature extends Element {
    constructor() { super('ha-state-control-climate-temperature'); this._targetTemperature = {}; this._pendingSync = new PendingValueSync();
      this._debouncedCallService = nativeDebounce(target => this._callService(target), 1000, false, env); }
    get _api() { return this.rawApi; } set _api(value) { this.rawApi = value; }
    get stateObj() { return this.value; }
    set stateObj(value) { const old = this.value; if (old === value) return; this.value = value; willUpdate.call(this, new Map([['stateObj', old]])); }
    get _min() { return this.stateObj.attributes.min_temp; } get _max() { return this.stateObj.attributes.max_temp; }
    _syncTargetTemperature() { return syncTarget.call(this); }
    _callService(type) { return callService.call(this, type); }
    // Browser inert suppresses pointer/keyboard gestures before the native handler.
    press(step) { if (!this.inert && !this.parentNode?.parentNode?.host?.inert) handleButton.call(this, { currentTarget: { dataset: { target: 'value' }, step } }, clamp); }
    release(value) { if (!this.inert) valueChanged.call(this, { type: 'value-changed', detail: { value } }); }
    drag(value) { if (!this.inert) valueChanging.call(this, { type: 'value-changing', detail: { value } }); }
    get shown() { return this._targetTemperature.value; }
  }
  class Popup extends Element {
    constructor() { super('more-info-climate'); this.attachShadow(); this.requests = [];
      this._api = { callService: async (...args) => { this.requests.push(structuredClone(args)); } };
      this.hass = { states: {}, config: { unit_system: { temperature: '°C' } }, connected: true, connection: { connected: true }, callService: this._api.callService };
    }
    updated() {}
    render() {
      if (!this.node) {
        const controls = new Element('div'); controls.className = 'controls';
        this.node = new Temperature(); this.node._api = this._api; controls.append(this.node);
        const container = new Element('ha-more-info-control-select-container');
        this.hvac = new Element('ha-control-select-menu'); this.preset = new Element('ha-control-select-menu');
        container.append(this.hvac, this.preset); this.shadowRoot.append(controls, container);
      }
      this.node.stateObj = this.stateObj;
      this.hvac.options = ['off', 'auto', 'heat'].map(value => ({ value })); this.hvac.value = this.stateObj.state;
      this.preset.options = ['none', ...PRESETS].map(value => ({ value })); this.preset.value = this.stateObj.attributes.preset_mode; this.preset.label = 'Preset';
      this.updated();
    }
  }
  registry.set('more-info-climate', Popup);
  return { env, doc, Popup, Temperature, Element };
}
const settle = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };
function envelope(records = [], extra = {}) { return { schemaVersion: 1, temperatureUnit: '°C', context: 'c'.repeat(32), sequence: records.at(-1)?.sequence || 0, revision: records.length ? records.at(-1).sequence * 2 + (records.at(-1).status === 'pending' ? 0 : 1) : 0, records, confirmedTarget: 19, confirmedBasis: BASIS, confirmedPreset: 'Auto', ...extra }; }
function operation(sequence = 1, status = 'pending', target = 19.5) { return { sequence, basis: BASIS, target, status, reason: null, origin: 'HA_TARGET', updatedAt: '2026-11-23T00:00:00Z', startedAt: '2026-11-23T00:00:00Z' }; }
function state(entity, preset, qualified = true, attrs = {}) {
  return { entity_id: entity, state: hvac(preset), attributes: {
    hargassner_control_schema: 'configured_room_setpoint_v1', hvac_modes: ['off', 'heat', 'auto'],
    preset_modes: ['none', ...PRESETS], preset_mode: preset, supported_features: 17, temperature: qualified && preset !== 'Aus' ? 19 : null,
    min_temp: 16, max_temp: 22, target_temp_step: .5, 'Zieltemperatur verfügbar': qualified && preset !== 'Aus',
    Sollwertbasis: qualified && preset !== 'Aus' ? BASIS : null, Sollwertphase: qualified ? 'night' : 'unbekannt', Sollwertparameter: qualified ? '5' : null, Raumkorrektur: 0,
    Temperaturauftrag: envelope([], { confirmedTarget: qualified && preset !== 'Aus' ? 19 : null, confirmedBasis: qualified && preset !== 'Aus' ? BASIS : null, confirmedPreset: qualified ? preset : null }), ...attrs,
  } };
}
// A fresh phase report: new basis and target in the same mode (Auto, setback -> day).
const phaseChanged = entity => state(entity, 'Auto', true, { temperature: 21, Sollwertbasis: FRESH, Sollwertphase: 'day', Sollwertparameter: '4',
  Temperaturauftrag: envelope([], { revision: 1, confirmedTarget: 21, confirmedBasis: FRESH }) });
const confirmed = (entity, target) => state(entity, 'Auto', true, { temperature: target, Raumkorrektur: target - 19,
  Temperaturauftrag: envelope([operation(1, 'confirmed', target)], { confirmedTarget: target }) });
async function popupFixture(entity) {
  const dom = environment(); const controller = module.installHargassnerThermostatPopup(dom.env); await controller.ready;
  const popup = new dom.Popup(); popup.stateObj = state(entity, 'Auto'); popup.hass.states[entity] = popup.stateObj; dom.doc.append(popup); popup.render(); await settle();
  const update = value => { popup.stateObj = value; popup.hass.states[value.entity_id] = value; popup.render(); };
  return { ...dom, controller, popup, node: popup.node, update, requests: popup.requests, stop: () => controller.stop() };
}
async function cardFixture(entity) {
  const dom = environment(); const requests = [];
  const hass = { states: { [entity]: state(entity, 'Auto') }, config: { unit_system: { temperature: '°C' } }, connected: true, connection: { connected: true },
    callService: async (...args) => { requests.push(structuredClone(args)); } };
  dom.env.loadCardHelpers = async () => ({ createCardElement() {
    const native = new dom.Element('hui-thermostat-card'); native.attachShadow(); native.node = new dom.Temperature(); native.shadowRoot.append(native.node); native.updateComplete = Promise.resolve();
    // The native card's context provider: the scoped API the card installs.
    native.addEventListener('context-request', event => {});
    Object.defineProperty(native, 'hass', { get() { return this.value; }, set(value) { this.value = value; native.node.stateObj = value.states[entity]; } });
    return native;
  } });
  const Card = module.installHargassnerThermostatCard(dom.env), card = new Card(); card.setConfig({ entity }); dom.doc.append(card); card.hass = hass; await settle();
  const node = card._native.node;
  node._api = card._makeNativeApi(card._native, card._generation);
  const update = value => { hass.states[entity] = value; card.hass = { ...hass, states: { ...hass.states } }; };
  return { ...dom, card, node, update, requests, stop: () => card.disconnectedCallback() };
}
function fire(env, delay) { const tasks = [...env.timers].filter(([, task]) => task.delay === delay); for (const [id, task] of tasks) { env.timers.delete(id); task.fn(); } }
async function test(name, callback) { try { await callback(); results.push({ name, passed: true }); } catch (error) { results.push({ name, passed: false, error: error.stack }); } }
const shownState = f => f.node.stateObj.attributes.temperature;
for (const [surface, make] of [['popup', popupFixture], ['card', cardFixture]]) for (const entity of ['climate.synthetic_hc1', 'climate.synthetic_hc2']) {
  await test(`${surface} ${entity}: 2026.11 normal press sends once at 250 ms, then follows the confirmed readback`, async () => {
    const f = await make(entity);
    f.node.press(.5); assert.equal(f.node.shown, 19.5); assert.equal(f.node._pendingSync.pending, true);
    f.update(state(entity, 'Auto')); await settle(); assert.equal(f.node.shown, 19.5); // same context: the draft survives
    fire(f.env, 250); await settle();
    assert.equal(f.requests.length, 1); assert.equal(f.requests[0][2].temperature, 19.5);
    assert.equal(f.node._pendingSync.pending, false); assert.equal(f.node.shown, 19.5);
    f.update(confirmed(entity, 19.5)); await settle(); assert.equal(f.node.shown, 19.5); assert.equal(f.node.inert || false, false);
    f.update(confirmed(entity, 20)); await settle(); assert.equal(f.node.shown, 20); // an external change still reaches the dial
    f.stop();
  });
  await test(`${surface} ${entity}: 2026.11 slider release sends its own value immediately`, async () => {
    const f = await make(entity);
    f.node.release(20.5); await settle();
    assert.equal(f.requests.length, 1); assert.equal(f.requests[0][2].temperature, 20.5); assert.equal(f.node.shown, 20.5);
    f.stop();
  });
  await test(`${surface} ${entity}: 2026.11 context change inside the send window releases the native dial`, async () => {
    const f = await make(entity);
    f.node.press(.5); assert.equal(f.node.shown, 19.5);
    f.update(phaseChanged(entity)); await settle();
    fire(f.env, 250); await settle();
    assert.equal(f.requests.length, 0); // the dropped gesture is never sent
    assert.equal(f.node._pendingSync.pending, false);
    assert.equal(f.node.shown, shownState(f)); // the dial shows what the surface displays, never the unsent 19.5
    assert.notEqual(f.node.shown, 19.5);
    f.update(confirmed(entity, 20)); await settle();
    assert.equal(shownState(f), 20); assert.equal(f.node.shown, 20); // later states reach the dial again
    f.node.press(.5); fire(f.env, 250); await settle(); assert.equal(f.requests.length, 1); assert.equal(f.requests[0][2].temperature, 20.5);
    f.stop();
  });
  await test(`${surface} ${entity}: 2026.11 disconnect inside the send window releases the native dial`, async () => {
    const f = await make(entity);
    f.node.press(.5); assert.equal(f.node.shown, 19.5);
    if (surface === 'popup') { f.popup.hass.connected = false; f.popup.updated(); } else { f.card.hass = { ...f.card.hass, connected: false }; }
    await settle(); fire(f.env, 250); await settle();
    assert.equal(f.requests.length, 0); assert.equal(f.node._pendingSync.pending, false); assert.equal(f.node.shown, shownState(f));
    f.stop();
  });
  await test(`${surface} ${entity}: 2026.11 removal inside the send window leaves a working native control`, async () => {
    const f = await make(entity);
    f.node.press(.5); f.stop(); await settle(); fire(f.env, 250); await settle();
    assert.equal(f.requests.length, 0); assert.equal(f.node._pendingSync.pending, false);
    const next = state(entity, 'Auto', true, { temperature: 18 }); f.node.stateObj = next; assert.equal(f.node.shown, 18);
  });
}
for (const [surface, make] of [['popup', popupFixture], ['card', cardFixture]]) for (const entity of ['climate.synthetic_hc1', 'climate.synthetic_hc2']) {
  await test(`${surface} ${entity}: 2026.11 + then - back to the confirmed value inside the window sends nothing and releases the dial`, async () => {
    const f = await make(entity);
    f.node.press(.5); f.node.press(-.5); await settle(); assert.equal(f.node.shown, 19);
    fire(f.env, 250); await settle();
    assert.equal(f.requests.length, 0); assert.equal(f.node._pendingSync.pending, false);
    f.update(confirmed(entity, 20)); await settle(); assert.equal(f.node.shown, 20);
    f.stop();
  });
  await test(`${surface} ${entity}: 2026.11 presses inside the window coalesce: the lock holds the draft and one send carries the last value`, async () => {
    const f = await make(entity);
    f.node.press(.5); await settle(); f.node.press(.5); await settle();
    assert.equal(f.node.shown, 20); assert.equal(f.node._pendingSync.pending, true);
    f.update(state(entity, 'Auto')); await settle(); assert.equal(f.node.shown, 20);
    fire(f.env, 250); await settle();
    assert.equal(f.requests.length, 1); assert.equal(f.requests[0][2].temperature, 20);
    f.stop();
  });
  await test(`${surface} ${entity}: 2026.11 a press refused before the surface re-renders (connection lost) releases the dial`, async () => {
    const f = await make(entity);
    // The connection flag changes; no render has made the control inert yet.
    if (surface === 'popup') f.popup.hass.connected = false; else f.card._hass.connected = false;
    f.node.press(.5); await settle(); fire(f.env, 250); await settle();
    assert.equal(f.requests.length, 0); assert.equal(f.node._pendingSync.pending, false); assert.equal(f.node.shown, 19);
    f.stop();
  });
  await test(`${surface} ${entity}: 2026.11 a send refused when its timer fires (connection lost) releases the dial`, async () => {
    const f = await make(entity);
    f.node.press(.5); assert.equal(f.node._pendingSync.pending, true);
    if (surface === 'popup') f.popup.hass.connected = false; else f.card._hass.connected = false;
    fire(f.env, 250); await settle();
    assert.equal(f.requests.length, 0); assert.equal(f.node._pendingSync.pending, false); assert.equal(f.node.shown, 19);
    f.stop();
  });
  await test(`${surface} ${entity}: 2026.11 an older captured function never resets a slider being dragged`, async () => {
    const f = await make(entity);
    f.node.press(.5); const old = f.node._debouncedCallService, oldCall = f.node._callService;
    fire(f.env, 250); await settle(); f.update(confirmed(entity, 19.5)); await settle();
    f.node.drag(21); old.call(f.node, 'value'); oldCall.call(f.node, 'value'); await settle();
    assert.equal(f.node.shown, 21); assert.equal(f.requests.length, 1);
    f.node.release(21); await settle(); assert.equal(f.requests.length, 2); assert.equal(f.requests[1][2].temperature, 21);
    f.stop();
  });
  await test(`${surface} ${entity}: 2026.11 a service call the lease refuses releases the dial`, async () => {
    const f = await make(entity);
    f.node.release(23); await settle(); // beyond max_temp 22: the guarded API refuses it
    assert.equal(f.requests.length, 0); assert.equal(f.node._pendingSync.pending, false); assert.equal(f.node.shown, shownState(f));
    f.stop();
  });
  await test(`${surface} ${entity}: 2026.11 an older captured gesture function never releases a newer draft`, async () => {
    const f = await make(entity);
    f.node.press(.5); const old = f.node._debouncedCallService, oldCall = f.node._callService;
    f.node.press(.5); assert.equal(f.node.shown, 20);
    old.call(f.node, 'value'); oldCall.call(f.node, 'value'); await settle();
    assert.equal(f.node._pendingSync.pending, true); assert.equal(f.node.shown, 20);
    fire(f.env, 250); await settle(); assert.equal(f.requests.length, 1); assert.equal(f.requests[0][2].temperature, 20);
    f.stop();
  });
  await test(`${surface} ${entity}: 2026.11 a refused call of the current epoch never releases a gesture still waiting to send`, async () => {
    const f = await make(entity);
    f.node.press(.5);
    await f.node._api.callService('climate', 'set_temperature', { entity_id: entity, temperature: 30 }); // refused: out of range
    assert.equal(f.node._pendingSync.pending, true); assert.equal(f.node.shown, 19.5);
    fire(f.env, 250); await settle(); assert.equal(f.requests.length, 1); assert.equal(f.requests[0][2].temperature, 19.5);
    f.stop();
  });
}
await test('popup: an older captured gesture function never releases a newer draft', async () => {
  const f = await popupFixture('climate.synthetic_hc1');
  f.node.press(.5); const old = f.node._debouncedCallService, oldCall = f.node._callService;
  f.node.press(.5); assert.equal(f.node.shown, 20);
  old.call(f.node, 'value'); oldCall.call(f.node, 'value');
  assert.equal(f.node._pendingSync.pending, true); assert.equal(f.node.shown, 20);
  fire(f.env, 250); await settle(); assert.equal(f.requests.length, 1); assert.equal(f.requests[0][2].temperature, 20);
  f.stop();
});
console.log(JSON.stringify({ tests: results.length, passed: results.filter(r => r.passed).length, failed: results.filter(r => !r.passed).length, results }, null, 2));
if (results.some(r => !r.passed)) process.exitCode = 1;
