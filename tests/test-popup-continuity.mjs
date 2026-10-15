/** Source-level DOM/epoch model only. No browser, network, HA or controller.
 * The service and debounce bodies are the pinned official HA implementation,
 * with TypeScript annotations removed mechanically below from bundled fixtures. Real Lit rendering
 * and context consumption require root's separate native component review.
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
const fixtureBytes = fs.readFileSync(path.join(HERE, 'ha-native-bodies.json'));
assert.equal(sha(fixtureBytes), '0cda1154ba86bfd4c12f4023b2a181705f2767beb0661a8bc52a82931163d970');
const nativeFixture = JSON.parse(fixtureBytes);
assert.equal(nativeFixture.sources[0].sha256, 'c5aa68f9857b02ec9200f6f359ebc7d700e8c9b406648a1ae2e1f775f98e80f6');
assert.equal(nativeFixture.sources[1].sha256, 'ebe061124268366ccf08901cddb4b2a0d2b7c023fd37b5a62b5bd373b8bc7971');
const callBody = nativeFixture.native_call_service_body.replaceAll('stateObj!', 'stateObj');
const nativeCall = new Function('type', callBody);
const debounceBody = nativeFixture.native_debounce_body
  .replace('let timeout: number | undefined;', 'let timeout;')
  .replace('let trailingArgs: T | undefined;', 'let trailingArgs;')
  .replace('(...args: T): void =>', '(...args) =>');
const nativeDebounce = new Function('func', 'wait', 'immediate', 'window', debounceBody);
const PRESETS = ['Aus', 'Auto', 'Heizen', 'Absenken', '1x Heizen', '1x Absenken'];
const BASIS = 'a'.repeat(32), FRESH = 'b'.repeat(32);
const REASONS = ['mode_report', 'parameter_report', 'phase_report', 'pending_change'];
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
  class Temperature extends Element {
    constructor() { super('ha-state-control-climate-temperature'); this._debouncedCallService = nativeDebounce(target => this._callService(target), 1000, false, env); }
    get _api() { return this.rawApi; } set _api(value) { this.rawApi = value; }
    get stateObj() { return this.value; } set stateObj(value) { this.value = value; this._targetTemperature = { value: value.attributes.temperature }; }
    _callService(type) { return nativeCall.call(this, type); }
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
        this.preset.addEventListener('wa-select', event => this._api.callService('climate', 'set_preset_mode', { entity_id: this.stateObj.entity_id, preset_mode: event.detail.item.value }));
      }
      this.node.stateObj = this.stateObj;
      this.hvac.options = ['off', 'auto', 'heat'].map(value => ({ value })); this.hvac.value = this.stateObj.state;
      this.preset.options = ['none', ...PRESETS].map(value => ({ value })); this.preset.value = this.stateObj.attributes.preset_mode; this.preset.label = 'Preset';
      this.updated();
    }
  }
  registry.set('more-info-climate', Popup);
  return { env, doc, Popup, Temperature };
}
const settle = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };
function state(entity, preset, qualified = true, attrs = {}) {
  return { entity_id: entity, state: hvac(preset), attributes: {
    hargassner_control_schema: 'configured_room_setpoint_v1', hvac_modes: ['off', 'heat', 'auto'],
    preset_modes: ['none', ...PRESETS], preset_mode: preset, supported_features: 17, temperature: qualified && preset !== 'Aus' ? 19 : null,
    min_temp: 16, max_temp: 22, target_temp_step: .5, 'Zieltemperatur verfügbar': qualified && preset !== 'Aus',
    Sollwertbasis: qualified && preset !== 'Aus' ? BASIS : null, ...attrs,
  } };
}
const marker = (from, to, reason = 'mode_report', changes = {}) => ({ id: 'fixed_transition', basis: BASIS, phase: 'readback', version: 2, reason, previousPreset: from, nextPreset: to, ...changes });
async function fixture(entity = 'climate.synthetic_hc1', preset = 'Auto') {
  const dom = environment(); const controller = module.installHargassnerThermostatPopup(dom.env); await controller.ready;
  const popup = new dom.Popup(); popup.stateObj = state(entity, preset); popup.hass.states[entity] = popup.stateObj; dom.doc.append(popup); popup.render(); await settle();
  const update = value => { popup.stateObj = value; popup.hass.states[value.entity_id] = value; popup.render(); };
  return { ...dom, controller, popup, update, entity };
}
function fireTimers(env, delay) { const tasks = [...env.timers].filter(([, task]) => task.delay === delay); for (const [id, task] of tasks) { env.timers.delete(id); task.fn(); } }
async function test(name, callback) { try { await callback(); results.push({ name, passed: true }); } catch (error) { results.push({ name, passed: false, error: error.stack }); } }
for (const entity of ['climate.synthetic_hc1', 'climate.synthetic_hc2']) {
  for (const from of PRESETS) for (const to of PRESETS) for (const reason of REASONS) {
    await test(`${entity}: ${from} -> ${to}, ${reason}`, async () => {
      const f = await fixture(entity, from), p = f.popup, old = p.node, oldApi = old._api, oldCall = old._callService, oldDebounce = old._debouncedCallService;
      if (from !== 'Aus') { old._targetTemperature.value = 20; oldDebounce('value'); }
      const originalState = state(entity, to, false, { Sollwertaktualisierung: marker(from, to, reason) }), unchanged = JSON.stringify(originalState);
      f.update(originalState); await settle(); assert.equal(p.node, old); assert.equal(JSON.stringify(originalState), unchanged); assert.equal(p.stateObj, originalState);
      if (from !== 'Aus' && to !== 'Aus') {
        assert.equal(old.stateObj.attributes.temperature, 19); assert.equal(old.stateObj.state, hvac(to));
        assert.equal(old.stateObj.attributes.preset_mode, to); assert.equal(old.stateObj.attributes['Zieltemperatur verfügbar'], false);
        assert.equal(old.inert, true); assert.equal(old.getAttribute('aria-disabled'), 'true');
        assert.equal(p.shadowRoot.querySelectorAll('ha-control-select-menu').find(item => item.dataset.hargassnerModus)?.disabled, true);
        await oldApi.callService('climate', 'set_temperature', { entity_id: entity, temperature: 20 });
        oldCall.call(old, 'value'); oldDebounce('value'); fireTimers(f.env, 1000); assert.deepEqual(p.requests, []);
      } else { assert.equal(old.stateObj.attributes.temperature, null); assert.equal(old.inert, false); }
      f.update(state(entity, to, true, { Sollwertbasis: FRESH, temperature: to === 'Aus' ? null : 20 })); await settle();
      assert.equal(p.node, old); assert.equal(old.inert, false);
      if (from !== 'Aus' && to !== 'Aus') {
        await oldApi.callService('climate', 'set_temperature', { entity_id: entity, temperature: 20 }); oldCall.call(old, 'value'); oldDebounce('value'); fireTimers(f.env, 1000); assert.deepEqual(p.requests, []);
      }
      if (to !== 'Aus') { old._targetTemperature.value = 20; old._callService('value'); assert.equal(p.requests.length, 1); }
      f.controller.stop();
    });
  }
}
await test('legacy offset marker remains strict, without authorizing changed mode', async () => {
  const f = await fixture(); f.update(state(f.entity, 'Auto', false, { Sollwertaktualisierung: { id: 'offset', basis: BASIS, phase: 'pending' } })); assert.equal(f.popup.node.inert, true);
  f.update(state(f.entity, 'Heizen', false, { Sollwertaktualisierung: { id: 'offset', basis: BASIS, phase: 'pending' } })); assert.equal(f.popup.node.inert, false); assert.equal(f.popup.node.stateObj.attributes.temperature, null); f.controller.stop();
});
await test('superseding mode/parameter/phase reports share one bounded window and original target', async () => {
  const f = await fixture();
  for (const [to, reason] of [['Heizen','mode_report'],['Absenken','parameter_report'],['1x Heizen','phase_report']]) {
    f.update(state(f.entity, to, false, { Sollwertaktualisierung: marker('Auto', to, reason) })); assert.equal(f.popup.node.inert, true); assert.equal(f.popup.node.stateObj.attributes.temperature, 19);
  }
  assert.equal([...f.env.timers.values()].filter(task => task.delay === 8000).length, 1);
  fireTimers(f.env, 8000); await settle(); assert.equal(f.popup.node.inert, false); assert.equal(f.popup.node.stateObj.attributes.temperature, null);
  f.update(state(f.entity, 'Heizen', false, { Sollwertaktualisierung: marker('Auto','Heizen') })); assert.equal(f.popup.node.stateObj.attributes.temperature, null); f.controller.stop();
});
await test('new marker id cannot chain the eight-second window', async () => {
  const f = await fixture(); f.update(state(f.entity,'Heizen',false,{Sollwertaktualisierung:marker('Auto','Heizen')}));
  f.update(state(f.entity,'Absenken',false,{Sollwertaktualisierung:marker('Auto','Absenken','mode_report',{id:'replacement'})})); assert.equal(f.popup.node.stateObj.attributes.temperature,null); f.controller.stop();
});
for (const change of [null, {}, {version:1}, {nextPreset:'Auto'}, {previousPreset:'Heizen'}, {basis:FRESH}, {reason:'unknown'}, {extra:true}, {id:'bad id'}]) {
  await test(`malformed/mismatched marker clears ${JSON.stringify(change)}`, async () => {
    const f = await fixture(); const value = change === null ? null : Object.keys(change).length === 0 ? {} : { ...marker('Auto','Heizen'), ...change };
    f.update(state(f.entity,'Heizen',false,{Sollwertaktualisierung:value})); assert.equal(f.popup.node.inert,false); assert.equal(f.popup.node.stateObj.attributes.temperature,null); f.controller.stop();
  });
}
for (const kind of ['offline', 'unavailable', 'unknown', 'entity-reuse', 'unmarked', 'bad-hvac', 'lost-schema']) {
  await test(`cancellation and stale callback cleanup: ${kind}`, async () => {
    const f = await fixture(); const node=f.popup.node, api=node._api, call=node._callService, debounced=node._debouncedCallService;
    f.update(state(f.entity,'Heizen',false,{Sollwertaktualisierung:marker('Auto','Heizen')}));
    let current=state(f.entity,'Heizen',false,{Sollwertaktualisierung:marker('Auto','Heizen')});
    if(kind==='offline') f.popup.hass.connection.connected=false;
    if(kind==='unavailable'||kind==='unknown') current.state=kind;
    if(kind==='entity-reuse') current.entity_id='climate.other';
    if(kind==='unmarked') current.attributes.Sollwertaktualisierung=null;
    if(kind==='bad-hvac') current.state='auto';
    if(kind==='lost-schema') current.attributes.hargassner_control_schema='other';
    f.update(current); assert.equal(node.inert,false); assert.equal(node.stateObj.attributes.temperature,null);
    f.popup.hass.connection.connected=true; f.update(state(current.entity_id,'Heizen',true));
    await api.callService('climate','set_temperature',{entity_id:f.entity,temperature:20}); call.call(node,'value'); debounced('value'); fireTimers(f.env,1000); assert.deepEqual(f.popup.requests,[]); f.controller.stop();
  });
}
await test('disconnect poll clears hold without any host state mutation', async () => {
  const f=await fixture(); const current=state(f.entity,'Heizen',false,{Sollwertaktualisierung:marker('Auto','Heizen')});
  f.update(current); f.popup.hass.connected=false; fireTimers(f.env,250); await settle(); assert.equal(f.popup.node.inert,false); assert.equal(f.popup.stateObj,current); f.controller.stop();
});
await test('dispose restores owned descriptors while captured guards stay permanently closed', async () => {
  const f=await fixture(); const node=f.popup.node, api=node._api, call=node._callService, debounced=node._debouncedCallService;
  f.update(state(f.entity,'Heizen',false,{Sollwertaktualisierung:marker('Auto','Heizen')})); f.controller.stop();
  assert.equal(Object.hasOwn(node,'_api'),false); assert.equal(Object.hasOwn(node,'_callService'),true); // permanent string-only retirement shim
  assert.equal(node._debouncedCallService.cancel instanceof Function,true);
  f.update(state(f.entity,'Heizen',true)); await api.callService('climate','set_temperature',{entity_id:f.entity,temperature:20}); call.call(node,'value'); debounced('value'); fireTimers(f.env,1000); assert.deepEqual(f.popup.requests,[]);
});
await test('defensive target feature drop is retained only inside the inert private presentation', async () => {
  const f=await fixture(); const raw=state(f.entity,'Heizen',false,{supported_features:16,Sollwertaktualisierung:marker('Auto','Heizen'),'Heizkreisstatus':'Heizen'}), original=JSON.stringify(raw);
  f.update(raw); assert.equal(f.popup.node.inert,true); assert.equal(f.popup.node.stateObj.attributes.supported_features,17);
  assert.equal(f.popup.node.stateObj.attributes.temperature,19); assert.equal(f.popup.node.stateObj.attributes.preset_mode,'Heizen');
  assert.equal(f.popup.node.stateObj.attributes['Heizkreisstatus'],'Heizen'); assert.equal(JSON.stringify(raw),original); assert.equal(raw.attributes.supported_features,16);
  await f.popup.node._api.callService('climate','set_temperature',{entity_id:f.entity,temperature:20}); assert.deepEqual(f.popup.requests,[]); f.controller.stop();
});
await test('malformed feature metadata revokes the visual hold rather than inventing capability', async () => {
  for(const supported_features of [null,'17',NaN,-1]) {
    const f=await fixture(); f.update(state(f.entity,'Heizen',false,{supported_features,Sollwertaktualisierung:marker('Auto','Heizen')}));
    assert.equal(f.popup.node.inert,false); assert.equal(f.popup.node.stateObj.attributes.temperature,null); f.controller.stop();
  }
});
await test('a queued old API or native wrapper cannot survive active -> Off -> active', async () => {
  const f=await fixture(), node=f.popup.node, api=node._api, call=node._callService, debounced=node._debouncedCallService;
  f.update(state(f.entity,'Aus',false,{Sollwertaktualisierung:marker('Auto','Aus')})); f.update(state(f.entity,'Heizen',true,{Sollwertbasis:FRESH}));
  await api.callService('climate','set_temperature',{entity_id:f.entity,temperature:20}); call.call(node,'value'); debounced('value'); fireTimers(f.env,1000);
  assert.deepEqual(f.popup.requests,[]); assert.equal(node.inert,false); f.controller.stop();
});
await test('a cancelled already-queued native debounce cannot revive after dispose', async () => {
  const f=await fixture(), node=f.popup.node; node._targetTemperature.value=20; node._debouncedCallService('value');
  const late=[...f.env.timers.values()].find(task=>task.delay===1000).fn;
  f.update(state(f.entity,'Heizen',false,{Sollwertaktualisierung:marker('Auto','Heizen')})); f.controller.stop();
  f.update(state(f.entity,'Heizen',true)); late(); assert.deepEqual(f.popup.requests,[]);
  node._targetTemperature.value=20; node._callService('value'); assert.equal(f.popup.requests.length,1);
});
await test('a cancelled already-queued native debounce cannot revive after a newer qualified mode', async () => {
  const f=await fixture(), node=f.popup.node; node._targetTemperature.value=20; node._debouncedCallService('value');
  const late=[...f.env.timers.values()].find(task=>task.delay===1000).fn;
  f.update(state(f.entity,'Heizen',false,{Sollwertaktualisierung:marker('Auto','Heizen')})); f.update(state(f.entity,'Heizen',true)); late();
  assert.deepEqual(f.popup.requests,[]); f.controller.stop();
});
await test('unit context changes revoke rather than relabel the prior target', async () => {
  const f=await fixture(); f.popup.hass.config.unit_system.temperature='°F';
  f.update(state(f.entity,'Heizen',false,{Sollwertaktualisierung:marker('Auto','Heizen')}));
  assert.equal(f.popup.node.inert,false); assert.equal(f.popup.node.stateObj.attributes.temperature,null); f.controller.stop();
});
await test('atomic qualified basis/mode change retires old API/method/timer without removing the native dial', async () => {
  const f=await fixture(), node=f.popup.node, api=node._api, call=node._callService, debounced=node._debouncedCallService;
  node._targetTemperature.value=19.5; debounced('value'); const late=[...f.env.timers.values()].find(task=>task.delay===1000).fn;
  f.update(state(f.entity,'Heizen',true,{Sollwertbasis:FRESH,temperature:22,min_temp:19,max_temp:25}));
  assert.equal(f.popup.node,node); assert.equal(node.inert,false); assert.equal(node.stateObj.attributes.temperature,22);
  await api.callService('climate','set_temperature',{entity_id:f.entity,temperature:19.5}); call.call(node,'value'); debounced('value'); late(); assert.deepEqual(f.popup.requests,[]);
  node._targetTemperature.value=22.5; node._callService('value'); assert.equal(f.popup.requests.length,1); f.controller.stop();
});
await test('ordinary measurement/status updates do not discard a legitimate unchanged-context native debounce', async () => {
  const f=await fixture(), node=f.popup.node; node._targetTemperature.value=20; node._debouncedCallService('value');
  const late=[...f.env.timers.values()].find(task=>task.delay===1000).fn;
  f.update(state(f.entity,'Auto',true,{current_temperature:21.1,'Heizkreisstatus':'Heizen'}));
  // Original native code reads its latest local target; this source test asserts
  // preservation of the event epoch, not an optimistic target-number policy.
  node._targetTemperature.value=20; late(); assert.equal(f.popup.requests.length,1); f.controller.stop();
});
await test('event-time guard blocks new qualified context even before parent updated runs', async () => {
  const f=await fixture(), node=f.popup.node, api=node._api, call=node._callService;
  f.popup.stateObj=state(f.entity,'Heizen',true,{Sollwertbasis:FRESH,temperature:22,min_temp:19,max_temp:25});
  await api.callService('climate','set_temperature',{entity_id:f.entity,temperature:19.5}); call.call(node,'value'); assert.deepEqual(f.popup.requests,[]);
  f.popup.render(); node._targetTemperature.value=22.5; node._callService('value'); assert.equal(f.popup.requests.length,1); f.controller.stop();
});
await test('wrong service/entity/out-of-range input cannot use scoped child API', async () => {
  const f=await fixture(), api=f.popup.node._api;
  await api.callService('climate','set_preset_mode',{entity_id:f.entity,preset_mode:'Heizen'});
  await api.callService('climate','set_temperature',{entity_id:'climate.other',temperature:20});
  for(const temperature of [null,NaN,Infinity,15,23]) await api.callService('climate','set_temperature',{entity_id:f.entity,temperature});
  assert.deepEqual(f.popup.requests,[]); f.controller.stop();
});
const report={schema_version:1,source_sha256:sha(fs.readFileSync(sourcePath)),native_temperature_sha256:nativeFixture.sources[0].sha256,native_debounce_sha256:nativeFixture.sources[1].sha256,native_fixture_sha256:sha(fixtureBytes),passed:results.filter(x=>x.passed).length,failed:results.filter(x=>!x.passed).length,scope:'Node fake DOM/event host; exact pinned native service/debounce bodies with TypeScript annotations erased; no actual Lit/browser/network',results};
console.log(JSON.stringify(report,null,2)); if(report.failed) process.exitCode=1;
