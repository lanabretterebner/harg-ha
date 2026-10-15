import { connectionFixture } from './connection-fixture.mjs';
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
    Sollwertbasis: qualified && preset !== 'Aus' ? BASIS : null, Sollwertphase:qualified?"night":"unbekannt", Sollwertparameter:qualified?"5":null, Raumkorrektur:0, Temperaturauftrag:envelope([], {confirmedTarget:qualified&&preset!=="Aus"?19:null,confirmedBasis:qualified&&preset!=="Aus"?BASIS:null,confirmedPreset:qualified?preset:null}), ...attrs,
  } };
}
const marker = (from, to, reason = 'mode_report', changes = {}) => ({ id: 'fixed_transition', basis: BASIS, phase: 'readback', version: 2, reason, previousPreset: from, nextPreset: to, ...changes });
async function fixture(entity = 'climate.synthetic_hc1', preset = 'Auto', connection) {
  const dom = environment(); const controller = module.installHargassnerThermostatPopup(dom.env); await controller.ready;
  const popup = new dom.Popup(); if (connection) popup.hass.connection = connection; popup.stateObj = state(entity, preset); popup.hass.states[entity] = popup.stateObj; dom.doc.append(popup); popup.render(); await settle();
  const update = value => { popup.stateObj = value; popup.hass.states[value.entity_id] = value; popup.render(); };
  return { ...dom, controller, popup, update, entity };
}
function fireTimers(env, delay) { const tasks = [...env.timers].filter(([, task]) => task.delay === delay); for (const [id, task] of tasks) { env.timers.delete(id); task.fn(); } }
async function test(name, callback) { try { await callback(); results.push({ name, passed: true }); } catch (error) { results.push({ name, passed: false, error: error.stack }); } }
function envelope(records=[],extra={}) { return {schemaVersion:1,temperatureUnit:"°C",context:"c".repeat(32),sequence:records.at(-1)?.sequence||0,revision:records.length?records.at(-1).sequence*2+(records.at(-1).status==="pending"?0:1):0,records,confirmedTarget:19,confirmedBasis:BASIS,confirmedPreset:"Auto",...extra}; }
function operation(sequence=1,status="pending",target=19.5) { return {sequence,basis:BASIS,target,status,reason:null,origin:"HA_TARGET",updatedAt:"2026-10-15T00:00:00Z",startedAt:"2026-10-15T00:00:00Z"}; }
// Transport completion/reconnect does not release a submitted request.
function confirmedState(entity){return state(entity,'Auto',true,{temperature:19.5,Sollwertbasis:FRESH,Raumkorrektur:.5,Temperaturauftrag:envelope([operation(1,'confirmed')],{confirmedTarget:19.5,confirmedBasis:FRESH})})}
for(const entity of ['climate.synthetic_hc1','climate.synthetic_hc2'])await test(`${entity} popup submission freezes temperature and denies follow-on APIs until qualified readback`,async()=>{
 const f=await fixture(entity),node=f.popup.node;node._targetTemperature.value=19.5;node._debouncedCallService('value');fireTimers(f.env,250);
 assert.equal(f.popup.requests.length,1);assert.equal(node.inert,true);assert.equal(node.stateObj.attributes.temperature,19.5);
 const api=node._api;node._callService('value');node._debouncedCallService('value');await api.callService('climate','set_temperature',{entity_id:entity,temperature:20});fireTimers(f.env,250);
 assert.equal(f.popup.requests.length,1);assert.equal(node.stateObj.attributes.temperature,19.5);
 const menu=f.popup.shadowRoot.querySelectorAll('ha-control-select-menu').find(n=>n.dataset.hargassnerModus);assert.equal(menu.disabled,false);
 f.update(state(entity,'Auto'));await settle();assert.equal(node.inert,true);assert.equal(node.stateObj.attributes.temperature,19.5);
 f.update(state(entity,'Auto',false,{Temperaturauftrag:envelope([operation()])}));await settle();assert.equal(node.stateObj.attributes.temperature,19.5);
 f.update(confirmedState(entity));await settle();assert.equal(node.inert,false);assert.equal(f.popup.requests.length,1);
 node._targetTemperature.value=20;node._debouncedCallService('value');fireTimers(f.env,250);assert.equal(f.popup.requests.length,2);assert.equal(f.popup.requests[1][2].temperature,20);assert.equal(f.popup.node,node);f.controller.stop();
});
for(const entity of ['climate.synthetic_hc1','climate.synthetic_hc2'])await test(`${entity} popup terminal uncertainty plus fresh different actual target restores explicit gesture admission`,async()=>{
 const f=await fixture(entity),node=f.popup.node;node._targetTemperature.value=19.5;node._debouncedCallService('value');fireTimers(f.env,250);const api=node._api;
 f.update(state(entity,'Auto',false,{Temperaturauftrag:envelope([operation()])}));await settle();
 f.update(state(entity,'Auto',true,{temperature:19,Sollwertbasis:FRESH,Temperaturauftrag:envelope([operation(1,'sent_unconfirmed')],{confirmedTarget:19,confirmedBasis:FRESH})}));await settle();
 assert.equal(node.inert,false);assert.equal(node.stateObj.attributes.temperature,19.5);
 assert.match(node.parentNode.children.map(n=>n.textContent||'').join(' '),/Bestätigung unklar/);
 await api.callService('climate','set_temperature',{entity_id:entity,temperature:20});assert.equal(f.popup.requests.length,1);
 node._targetTemperature.value=20;node._debouncedCallService('value');fireTimers(f.env,250);assert.equal(f.popup.requests.length,2);assert.equal(f.popup.requests[1][2].temperature,20);assert.equal(node.inert,true);assert.equal(f.popup.node,node);f.controller.stop();
});
for(const entity of ['climate.synthetic_hc1','climate.synthetic_hc2']){
 await test(`${entity} draft survives old state before native debounce`,async()=>{const f=await fixture(entity);const old=f.popup.node;old._targetTemperature.value=19.5;old._debouncedCallService('value');f.update(state(entity,'Auto'));await settle();assert.equal(old.stateObj.attributes.temperature,19.5);fireTimers(f.env,250);assert.equal(f.popup.requests[0][2].temperature,19.5);assert.equal(f.popup.node,old);f.controller.stop()});
 await test(`${entity} pending, unknown and recovery retain exact node and requested value`,async()=>{const f=await fixture(entity),node=f.popup.node;node._targetTemperature.value=19.5;node._debouncedCallService('value');fireTimers(f.env,250);f.update(state(entity,'Auto',false,{Temperaturauftrag:envelope([operation()])}));await settle();assert.equal(node.stateObj.attributes.temperature,19.5);assert(node.inert);f.update(state(entity,'Auto',false,{Temperaturauftrag:envelope([operation(1,'sent_unconfirmed')])}));await settle();assert.equal(node.stateObj.attributes.temperature,19.5);assert.match(f.popup.shadowRoot.children.map(n=>n.textContent||'').join(' ')+node.parentNode.children.map(n=>n.textContent||'').join(' '),/unklar/);f.update(state(entity,'Auto',true,{temperature:19.5,Sollwertbasis:FRESH,Raumkorrektur:.5,Temperaturauftrag:envelope([operation(1,'confirmed')],{revision:4,confirmedTarget:19.5,confirmedBasis:FRESH})}));await settle();assert.equal(f.popup.node,node);assert.equal(node.stateObj.attributes.temperature,19.5);assert.equal(node.inert,false);f.controller.stop()});
 await test(`${entity} external dashboard offset uses requested metadata; other circuit retains confirmed`,async()=>{const f=await fixture(entity),node=f.popup.node;const actual=state(entity,'Auto',false,{Temperaturauftrag:envelope([operation(1,'pending',20)])}),before=JSON.stringify(actual);f.update(actual);await settle();assert.equal(node.stateObj.attributes.temperature,20);assert.equal(JSON.stringify(actual),before);assert.equal(f.popup.requests.length,0);assert(node.inert);f.controller.stop()});
 await test(`${entity} missing long-lived marker never unmounts old confirmed display`,async()=>{const f=await fixture(entity),node=f.popup.node;f.update(state(entity,'Auto',false));await settle();fireTimers(f.env,8000);await settle();assert.equal(f.popup.node,node);assert.equal(node.stateObj.attributes.temperature,19);assert(node.inert);assert.equal(f.popup.requests.length,0);f.controller.stop()});
 await test(`${entity} old cancelled timer cannot consume a later gesture`,async()=>{const f=await fixture(entity),node=f.popup.node;node._targetTemperature.value=19.5;node._debouncedCallService('value');const old=[...f.env.timers.values()].find(t=>t.delay===250).fn;const api=node._api;node._targetTemperature.value=20;node._debouncedCallService('value');old();await api.callService('climate','set_temperature',{entity_id:entity,temperature:19.5});fireTimers(f.env,250);assert.equal(f.popup.requests.length,1);assert.equal(f.popup.requests[0][2].temperature,20);f.controller.stop()});
 await test(`${entity} genuine Off keeps node, exposes real off and never sends`,async()=>{const f=await fixture(entity),node=f.popup.node,api=node._api;f.update(state(entity,'Aus',false));await settle();assert.equal(f.popup.node,node);assert.equal(node.stateObj.state,'off');assert.equal(node.stateObj.attributes.temperature,null);await api.callService('climate','set_temperature',{entity_id:entity,temperature:20});node._callService('value');assert.equal(f.popup.requests.length,0);f.controller.stop()});
}
await test('popup and later second popup share last user intent; late failure cannot undo it',async()=>{const f=await fixture(),first=f.popup;let reject;first._api.callService=()=>new Promise((_,r)=>{reject=r});const second=new f.Popup();second.hass.connection=first.hass.connection;second.hass.states=first.hass.states;second.stateObj=first.stateObj;f.doc.append(second);second.render();await settle();first.node._targetTemperature.value=19.5;first.node._debouncedCallService('value');fireTimers(f.env,250);f.update(confirmedState(f.entity));second.stateObj=f.popup.stateObj;second.render();await settle();second.node._targetTemperature.value=20;second.node._debouncedCallService('value');fireTimers(f.env,250);reject(Error('old'));await settle();assert.equal(first.node.stateObj.attributes.temperature,20);assert.equal(second.node.stateObj.attributes.temperature,20);f.controller.stop()});
await test('reopening popup during pending uses shared confirmed range and current requested target',async()=>{const f=await fixture(),first=f.popup;first.node._targetTemperature.value=20;first.node._debouncedCallService('value');fireTimers(f.env,250);const actual=state(f.entity,'Auto',false,{Temperaturauftrag:envelope([operation(1,'pending',20)])});f.update(actual);const second=new f.Popup();second.hass.connection=first.hass.connection;second.hass.states=first.hass.states;second.stateObj=actual;f.doc.append(second);second.render();await settle();assert.equal(second.node.stateObj.attributes.temperature,20);assert.equal(second.node.stateObj.attributes.min_temp,16);assert(second.node.inert);assert.equal(second.requests.length,0);f.controller.stop()});
await test('card plus popup share requested target without old surface failure rollback',async()=>{
 const f=await fixture(),popup=f.popup;let reject;popup._api.callService=()=>new Promise((_,r)=>{reject=r});
 f.env.loadCardHelpers=async()=>({createCardElement(){const native=new f.env.HTMLElement('hui-thermostat-card');native.attachShadow();native.node=new f.Temperature();native.node._api={callService:async()=>{throw Error('Unscoped provider')}};native.shadowRoot.append(native.node);native.updateComplete=Promise.resolve();Object.defineProperty(native,'hass',{get(){return this.value},set(value){this.value=value;native.node.stateObj=value.states[f.entity]}});return native}});
 const Card=module.installHargassnerThermostatCard(f.env),card=new Card();card.setConfig({entity:f.entity});f.doc.append(card);card.hass=popup.hass;await settle();
 popup.node._targetTemperature.value=19.5;popup.node._debouncedCallService('value');fireTimers(f.env,250);f.update(confirmedState(f.entity));card.hass=popup.hass;await settle();const control=card._native.node;control._targetTemperature.value=20;control._debouncedCallService('value');fireTimers(f.env,250);reject(Error('first'));await settle();assert.equal(control.stateObj.attributes.temperature,20);assert.equal(popup.node.stateObj.attributes.temperature,20);assert.equal(card._native.node,control);card.disconnectedCallback();f.controller.stop();
});
for(const entity of['climate.synthetic_hc1','climate.synthetic_hc2'])await test(`${entity} unknown target leaves independently available native preset mode usable`,async()=>{const f=await fixture(entity);f.update(state(entity,'Auto',false));await settle();const menu=f.popup.shadowRoot.querySelectorAll('ha-control-select-menu').find(n=>n.dataset.hargassnerModus);assert(menu);assert.equal(menu.disabled,false);f.popup.preset.disabled=true;f.popup.updated();await settle();assert.equal(menu.disabled,true);f.controller.stop()});
for(const entity of['climate.synthetic_hc1','climate.synthetic_hc2'])for(const from of PRESETS)for(const to of PRESETS)for(const reason of REASONS)await test(`matrix ${entity}:${from}->${to}:${reason}`,async()=>{
 const f=await fixture(entity,from),node=f.popup.node,api=node._api,call=node._callService;
 if(from!=='Aus'){node._targetTemperature.value=19.5;node._debouncedCallService('value')}
 const timer=[...f.env.timers.values()].find(t=>t.delay===250)?.fn;
 const actual=state(entity,to,false,{Sollwertaktualisierung:marker(from,to,reason)}),unchanged=JSON.stringify(actual);f.update(actual);await settle();assert.equal(f.popup.node,node);assert.equal(JSON.stringify(actual),unchanged);assert(node.inert);
 if(from!=='Aus'&&to!=='Aus')assert.equal(node.stateObj.attributes.temperature,19);else assert.equal(node.stateObj.attributes.temperature,null);
 timer?.();call.call(node,'value');await api.callService('climate','set_temperature',{entity_id:entity,temperature:19.5});assert.equal(f.popup.requests.length,0);
 f.update(state(entity,to,to!=='Aus',{temperature:to==='Aus'?null:20,Sollwertbasis:to==='Aus'?null:FRESH,Temperaturauftrag:envelope([],{revision:1,confirmedTarget:to==='Aus'?null:20,confirmedBasis:to==='Aus'?null:FRESH,confirmedPreset:to})}));await settle();assert.equal(f.popup.node,node);await api.callService('climate','set_temperature',{entity_id:entity,temperature:19.5});assert.equal(f.popup.requests.length,0);
 if(to!=='Aus'){node._targetTemperature.value=20.5;node._callService('value');assert.equal(f.popup.requests.length,1);assert.equal(f.popup.requests[0][2].temperature,20.5)}else{assert.equal(node.stateObj.state,'off');assert.equal(node.stateObj.attributes.temperature,null)}
 f.controller.stop();
});
for (const shape of ['throw', 'error', 'connection_lost', 'service_validation_error']) await test(`popup service ${shape} retains uncertain target until bound rejection`, async () => {
  const f = await fixture(), node = f.popup.node;
  f.popup._api.callService = (...args) => {
    f.popup.requests.push(structuredClone(args));
    if (shape === 'throw') throw Error('response lost after send');
    return Promise.reject(shape === 'error' ? Error('response lost after send') : { code: shape, message: 'service response unavailable' });
  };
  node._targetTemperature.value = 19.5; node._debouncedCallService('value'); fireTimers(f.env, 250); await settle();
  const note = () => node.parentNode.children.map(n => n.textContent || '').join(' ');
  assert.equal(f.popup.requests.length, 1); assert.equal(node.stateObj.attributes.temperature, 19.5);
  assert.match(note(), /Bestätigung unklar/); assert.doesNotMatch(note(), /abgelehnt/);
  f.update(state(f.entity, 'Auto', true, { Temperaturauftrag: envelope([operation()]) })); await settle();
  f.update(state(f.entity, 'Auto', true, { Temperaturauftrag: envelope([operation(1, 'rejected')]) })); await settle();
  assert.equal(node.stateObj.attributes.temperature, 19); assert.match(note(), /abgelehnt/);
  assert.equal(f.popup.node, node); f.controller.stop();
});
for (const change of ['connection', 'user']) for (const aba of [false, true]) await test(`popup ${change}${aba ? ' ABA' : ''} replacement retires APIs and pending debounce`, async () => {
  const f = await fixture(), popup = f.popup, node = popup.node;
  popup.hass = { ...popup.hass, user: { id: 'original_user' } }; popup.updated(); await settle();
  const original = popup.hass;
  node._targetTemperature.value = 19.5; node._debouncedCallService('value');
  const api = node._api, call = node._callService, debounce = node._debouncedCallService;
  const callback = [...f.env.timers.values()].find(t => t.delay === 250).fn;
  popup.hass = { ...original, ...(change === 'connection' ? { connection: { connected: true } } : { user: { id: 'replacement_user' } }) };
  await api.callService('climate', 'set_temperature', { entity_id: f.entity, temperature: 19.5 });
  assert.equal(popup.requests.length, 0);
  popup.updated(); await settle();
  assert.equal([...f.env.timers.values()].filter(t => t.delay === 250).length, 0);
  if (aba) { popup.hass = original; popup.updated(); await settle(); }
  callback(); call.call(node, 'value'); debounce.call(node, 'value');
  await api.callService('climate', 'set_temperature', { entity_id: f.entity, temperature: 19.5 });
  assert.equal(popup.requests.length, 0); assert.equal(popup.node, node);
  assert.equal(node.stateObj.attributes.temperature, 19);
  node._targetTemperature.value = 20; node._debouncedCallService('value'); fireTimers(f.env, 250);
  assert.equal(popup.requests.length, 1); assert.equal(popup.requests[0][2].temperature, 20);
  f.controller.stop();
});

for (const entity of ['climate.synthetic_hc1', 'climate.synthetic_hc2']) for (const path of ['hass-flag', 'connection-flag', 'events-before-render']) await test(`${entity} popup reconnect ${path} retires draft and captured authority`, async () => {
  const connection = connectionFixture(), f = await fixture(entity, 'Auto', connection), node = f.popup.node;
  node._targetTemperature.value = 19.5; node._debouncedCallService('value');
  const api = node._api, call = node._callService, debounce = node._debouncedCallService;
  assert.equal([...f.env.timers.values()].filter(t => t.delay === 250).length, 1);
  if (path === 'events-before-render') {
    connection.disconnect(); assert.equal([...f.env.timers.values()].filter(t => t.delay === 250).length, 0); connection.reconnect();
  } else {
    const owner = path === 'hass-flag' ? f.popup.hass : connection;
    owner.connected = false; f.popup.updated(); await settle(); assert.equal(node.inert, true);
    assert.equal([...f.env.timers.values()].filter(t => t.delay === 250).length, 0);
    owner.connected = true; f.popup.updated(); await settle();
  }
  await api.callService('climate', 'set_temperature', { entity_id: entity, temperature: 20 });
  call.call(node, 'value'); debounce.call(node, 'value'); fireTimers(f.env, 250); await settle();
  assert.equal(f.popup.requests.length, 0); assert.equal(f.popup.node, node); assert.equal(node.stateObj.attributes.temperature, 19);
  node._targetTemperature.value = 20; node._debouncedCallService('value'); fireTimers(f.env, 250);
  assert.equal(f.popup.requests.length, 1); assert.equal(f.popup.requests[0][2].temperature, 20); f.controller.stop();
});
for (const entity of ['climate.synthetic_hc1', 'climate.synthetic_hc2']) await test(`${entity} popup reconnect keeps submitted lock until qualified readback`, async () => {
  const connection = connectionFixture(), f = await fixture(entity, 'Auto', connection), node = f.popup.node;
  node._targetTemperature.value = 19.5; node._debouncedCallService('value'); fireTimers(f.env, 250); const api = node._api;
  connection.disconnect(); connection.reconnect(); await settle();
  assert.equal(node.stateObj.attributes.temperature, 19.5);
  assert.match(node.parentNode.children.map(n => n.textContent || '').join(' '), /Bestätigung unklar/);
  await api.callService('climate', 'set_temperature', { entity_id: entity, temperature: 20 }); assert.equal(f.popup.requests.length, 1);
  node._targetTemperature.value = 20; node._debouncedCallService('value'); fireTimers(f.env, 250);
  assert.equal(f.popup.requests.length, 1); assert.equal(node.inert, true);
  f.update(confirmedState(entity)); await settle(); assert.equal(node.inert, false);
  node._targetTemperature.value = 20; node._debouncedCallService('value'); fireTimers(f.env, 250);
  assert.equal(f.popup.requests.length, 2); assert.equal(f.popup.requests[1][2].temperature, 20); assert.equal(f.popup.node, node); f.controller.stop();
});
for (const path of ['hass-flag', 'connection-flag', 'events']) await test(`popup cached operating status is unavailable on ${path} disconnect`, async () => {
  const connection = connectionFixture(), f = await fixture('climate.synthetic_hc1', 'Auto', connection);
  const actual = state(f.entity, 'Auto', true, { 'Heizkreisstatus': 'Heizen', 'Heizkreisstatus-Code': 3, 'Heizkreisstatus verfügbar': true });
  f.update(actual); await settle();
  const status = f.popup.shadowRoot.children.find(n => n.dataset.hargassnerOperatingState);
  assert.equal(status.dataset.available, 'true'); assert.equal(status.children[1].textContent, 'Heizen');
  if (path === 'events') connection.disconnect();
  else { (path === 'hass-flag' ? f.popup.hass : connection).connected = false; f.popup.updated(); }
  await settle(); assert.equal(status.dataset.available, 'false'); assert.equal(status.children[1].textContent, 'Nicht verfügbar'); assert.equal(status.title, '');
  assert.equal(f.popup.stateObj, actual); assert.equal(actual.attributes['Heizkreisstatus verfügbar'], true);
  if (path === 'events') connection.reconnect();
  else { (path === 'hass-flag' ? f.popup.hass : connection).connected = true; f.popup.updated(); }
  await settle(); assert.equal(status.dataset.available, 'true'); assert.equal(status.children[1].textContent, 'Heizen'); f.controller.stop();
});
await test('popup lifecycle subscriptions move with session and stop with controller', async () => {
  const connection = connectionFixture(), f = await fixture('climate.synthetic_hc1', 'Auto', connection), second = connectionFixture();
  assert.equal(connection.listenerCount(), 2);
  f.popup.hass = { ...f.popup.hass, connection: second }; f.popup.updated(); await settle();
  assert.equal(connection.listenerCount(), 0); assert.equal(second.listenerCount(), 2);
  f.controller.stop(); assert.equal(second.listenerCount(), 0);
});
await test('removing popup releases lifecycle listeners during reconciliation', async () => {
  const connection = connectionFixture(), f = await fixture('climate.synthetic_hc1', 'Auto', connection);
  f.popup.remove(); f.popup.updated(); await settle(); assert.equal(connection.listenerCount(), 0); f.controller.stop();
});

console.log(JSON.stringify({tests:results.length,passed:results.filter(r=>r.passed).length,failed:results.filter(r=>!r.passed).length,results},null,2));if(results.some(r=>!r.passed))process.exitCode=1;
