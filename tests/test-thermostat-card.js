import { installHargassnerThermostatCard } from "../dist/hargassner-thermostat-popup.js";

// Isolated HA helpers/service stand-ins only. Never connect this fixture to HA.
const PRESETS = ["Aus", "Auto", "Heizen", "Absenken", "1x Heizen", "1x Absenken"];
const BASIS = "a".repeat(32), OTHER = "b".repeat(32);
const fixtures = document.querySelector("#fixtures");
let requests = [], created = [], helperCalls = 0;
// HA emits new hass/state objects on one authenticated Connection. A new
// Connection means a different session and must deliberately discard intent.
let fixtureConnection = { connected: true };
class TemperatureControl extends HTMLElement {
  constructor(){ super(); this._debouncedCallService=type=>{clearTimeout(this.timer);this.timer=setTimeout(()=>this._callService(type),1000)};this._debouncedCallService.cancel=()=>clearTimeout(this.timer); }
  get _api(){return this.rawApi} set _api(value){this.rawApi=value}
  get stateObj(){return this.value} set stateObj(value){this.value=value;this._targetTemperature={value:value.attributes.temperature}}
  _callService(type){if(type==='value')return this._api.callService('climate','set_temperature',{entity_id:this.stateObj.entity_id,temperature:this._targetTemperature.value})}
}
customElements.define('ha-state-control-climate-temperature',TemperatureControl);
class NativeThermostat extends HTMLElement {
  constructor() { super(); this.attachShadow({ mode: "open" });this.control=document.createElement('ha-state-control-climate-temperature');this.shadowRoot.append(this.control);this.updateComplete=Promise.resolve(); }
  setConfig(config) { this.config = config; }
  set hass(value) { this._hass = value; this.control.stateObj=value.states[this.config.entity]; }
  get hass() { return this._hass; }
  connectedCallback() {
    const event = new Event("context-request", { bubbles: true, composed: true });
    event.context = "hassApi"; event.callback = api => { this.api = api;this.control._api=api; };
    this.dispatchEvent(event);
  }
  syntheticTemperature(value) { this.control._targetTemperature={value};this.control._callService('value'); }
}
customElements.define("hui-thermostat-card", NativeThermostat);
const helpers = { createCardElement(config) {
  const card = document.createElement("hui-thermostat-card"); card.setConfig(config); created.push(card); return card;
} };
const load = () => { helperCalls++; return Promise.resolve(helpers); };
const state = (changes = {}, attrs = {}) => ({ entity_id: "climate.synthetic_one", state: "auto", ...changes,
  attributes: { friendly_name: "Heizkreis 1", hargassner_control_schema: "configured_room_setpoint_v1",
    hvac_modes: ["off", "heat", "auto"], supported_features: 385, preset_modes: ["none", ...PRESETS], preset_mode: "Auto",
    temperature: 19, min_temp: 16, max_temp: 22, target_temp_step: 0.5, current_temperature: null,
    "Zieltemperatur verfügbar": true, Sollwertbasis: BASIS, Sollwertphase:"night", Sollwertparameter:"5", Raumkorrektur: "0", "Heizkreisstatus": "Tag/Nacht Abschaltung",
    "Heizkreisstatus-Code": 4, "Heizkreisstatus verfügbar": true, ...attrs } });
const hass = (value = state()) => ({ connected: true, connection: fixtureConnection, states: { [value.entity_id]: value },
  config: { unit_system: { temperature: "°C" } }, locale: { language: "de" },
  callService: async (domain, service, data) => { requests.push({ domain, service, data: { ...data } }); } });
const assert = (v, m = "Assertion failed") => { if (!v) throw new Error(m); };
const equal = (a, b) => assert(JSON.stringify(a) === JSON.stringify(b), `${JSON.stringify(a)} != ${JSON.stringify(b)}`);
const settle = async () => { for (let i = 0; i < 3; i++) { await Promise.resolve(); await new Promise(resolve => {
  const c = new MessageChannel(); c.port1.onmessage = () => { c.port1.close(); c.port2.close(); resolve(); }; c.port2.postMessage(null);
}); } };
const Card = customElements.get("hargassner-thermostat-card");
const menu = card => card.shadowRoot.querySelector("ha-control-select-menu");
const native = card => card.shadowRoot.querySelector("hui-thermostat-card");
const pick = (node, value) => node.dispatchEvent(new CustomEvent("wa-select", { detail: { item: { value } }, bubbles: true, composed: true }));
function mount(value = hass(), config = { entity: "climate.synthetic_one" }) {
  const card = document.createElement("hargassner-thermostat-card"); card.setConfig(config); card.hass = value; fixtures.append(card); return card;
}
const results = [];
async function test(name, run) {
  fixtures.replaceChildren(); fixtureConnection = { connected: true }; requests = []; created = []; helperCalls = 0; window.loadCardHelpers = load;
  try { await run(); results.push({ name, passed: true }); }
  catch (e) { results.push({ name, passed: false, error: e.message }); }
}
await test("picker registration preserves the existing registry array and is idempotent", async () => {
  const before = window.customCards; installHargassnerThermostatCard(window);
  equal(window.customCards, before); equal(window.customCards.filter(x => x.type === "hargassner-thermostat-card").length, 1);
  const entry = window.customCards.find(x => x.type === "hargassner-thermostat-card"); equal(entry.name, "Hargassner Thermostat");
  assert(entry.getEntitySuggestion(hass(), "climate.synthetic_one")); equal(entry.getEntitySuggestion(hass(), "light.unrelated"), null);
});
await test("default stub selects a marked climate without inventing temperatures or modes", async () => {
  const h = hass(); h.states["climate.unrelated"] = { ...state(), entity_id: "climate.unrelated", attributes: {} };
  equal(Card.getStubConfig(h, ["climate.unrelated"], ["climate.synthetic_one"]), { entity: "climate.synthetic_one" });
  equal(Card.getStubConfig({ states: {} }), { entity: "" });
});
await test("native visual editor exposes entity and optional name only", async () => {
  const schema = Card.getConfigForm().schema; equal(schema.map(x => x.name), ["entity", "name"]);
  equal(schema[0].selector.entity.domain, "climate"); assert(schema[0].required);
});
await test("native thermostat receives exact climate and original hass with explicit target label", async () => {
  const h = hass(), card = mount(h); await settle();
  equal(native(card).config, { type: "thermostat", entity: "climate.synthetic_one", name: "Zieltemperatur", show_current_as_primary: false, features: [] });
  equal(native(card).hass, h); equal(created.length, 1); equal(requests, []);
  assert(card.shadowRoot.textContent.includes("Ist: Nicht verfügbar"));
  assert(card.shadowRoot.textContent.includes("Raumkorrektur: 0 °C"));
});
await test("all six card presets have the same embedded icons and current reported mode", async () => {
  const card = mount(); await settle(); equal(menu(card).options.map(x => x.value), PRESETS);
  assert(menu(card).options.every(x => x.iconPath?.startsWith("M"))); equal(menu(card).value, "Auto"); equal(requests, []);
});
for (const value of PRESETS) await test(`explicit card ${value} selection calls only exact native preset service`, async () => {
  const card = mount(hass(state({}, { preset_mode: value === "Auto" ? "Heizen" : "Auto" }))); await settle();
  const before = menu(card).value; pick(menu(card), value); await settle();
  equal(requests, [{ domain: "climate", service: "set_preset_mode", data: { entity_id: "climate.synthetic_one", preset_mode: value } }]);
  equal(menu(card).value, before);
});
await test("reported mode and status updates do not send a command", async () => {
  const card = mount(); await settle(); card.hass = hass(state({}, { preset_mode: "Absenken", "Heizkreisstatus": "Absenken", "Heizkreisstatus-Code": 3 })); await settle();
  equal(menu(card).value, "Absenken"); assert(card.shadowRoot.textContent.includes("HeizkreisstatusAbsenken")); equal(requests, []);
});
await test("same or invalid mode choices do not call HA", async () => {
  const card = mount(); await settle(); pick(menu(card), "Auto"); pick(menu(card), "boost"); await settle(); equal(requests, []);
});
await test("native temperature path remains separate from card mode logic", async () => {
  const card = mount(); await settle(); native(card).syntheticTemperature(19.5);
  equal(requests, [{ domain: "climate", service: "set_temperature", data: { entity_id: "climate.synthetic_one", temperature: 19.5 } }]);
});
await test("missing target and phase qualification never instantiate a native dial", async () => {
  for (const attrs of [{ temperature: null }, { "Zieltemperatur verfügbar": false }, { min_temp: null },
    { max_temp: null }, { target_temp_step: null }, { target_temp_step: 0 }, { temperature: 90 }, { temperature: NaN }]) {
    const card = mount(hass(state({}, attrs))); await settle(); equal(native(card), null);
    assert(card.shadowRoot.textContent.includes("Zieltemperatur nicht verfügbar")); assert(menu(card)); card.remove();
  }
  equal(created.length, 0); equal(requests, []);
});
await test("target qualification can recover without changing mode or entity", async () => {
  const card = mount(hass(state({}, { temperature: null }))); await settle(); equal(native(card), null);
  card.hass = hass(); await settle(); assert(native(card)); equal(menu(card).value, "Auto"); equal(requests, []);
});
await test("zero measured temperature is real but missing measurement stays unavailable", async () => {
  const card = mount(hass(state({}, { current_temperature: 0 }))); await settle();
  assert(card.shadowRoot.textContent.includes("Ist: 0 °C"));
  card.hass = hass(); await settle(); assert(card.shadowRoot.textContent.includes("Ist: Nicht verfügbar"));
});
await test("raw Celsius room correction is not mislabeled in a Fahrenheit HA system", async () => {
  const h = hass(state({}, { temperature: 66.2, min_temp: 60, max_temp: 72, target_temp_step: 0.9,
    current_temperature: 68, Raumkorrektur: "0.5" })); h.config.unit_system.temperature = "°F";
  const card = mount(h); await settle();
  assert(card.shadowRoot.textContent.includes("Ist: 68 °F"));
  assert(card.shadowRoot.textContent.includes("Raumkorrektur: 0,5 °C"));
  equal(native(card).hass, h); equal(h.states["climate.synthetic_one"].attributes.temperature, 66.2); equal(requests, []);
});
await test("disconnected HA retains an inert dial and blocks late selection", async () => {
  const card = mount(); await settle(); const old = menu(card), h = hass(); h.connected = false; card.hass = h; await settle();
  assert(native(card)?.inert); assert(menu(card).disabled); pick(old, "Heizen"); await settle(); equal(requests, []);
  assert(card.shadowRoot.textContent.includes("HeizkreisstatusNicht verfügbar"));
});
await test("unavailable climate blocks modes and hides old status", async () => {
  const card = mount(); await settle(); card.hass = hass(state({ state: "unavailable" })); await settle();
  assert(native(card)?.inert); assert(menu(card).disabled); pick(menu(card), "Heizen"); await settle(); equal(requests, []);
  assert(card.shadowRoot.textContent.includes("HeizkreisstatusNicht verfügbar"));
});
await test("unmarked entities never receive a native temperature control or preset action", async () => {
  const card = mount(hass(state({}, { hargassner_control_schema: undefined }))); await settle();
  equal(native(card), null); equal(menu(card), null); equal(helperCalls, 0); equal(requests, []);
});
await test("stale status and fresh unknown codes preserve truthful labels", async () => {
  const card = mount(hass(state({}, { "Heizkreisstatus verfügbar": false }))); await settle();
  assert(card.shadowRoot.textContent.includes("HeizkreisstatusNicht verfügbar"));
  card.hass = hass(state({}, { "Heizkreisstatus": "Code 99 (unbekannt)", "Heizkreisstatus-Code": 99 })); await settle();
  assert(card.shadowRoot.textContent.includes("HeizkreisstatusCode 99 (unbekannt)")); equal(requests, []);
});
await test("entity reuse rejects a queued event from the previous selector", async () => {
  const card = mount(); await settle(); const old = menu(card);
  card.setConfig({ entity: "climate.synthetic_two" }); card.hass = hass(state({ entity_id: "climate.synthetic_two" })); await settle();
  pick(old, "Heizen"); await settle(); equal(requests, []); equal(native(card).config.entity, "climate.synthetic_two");
});
await test("late helper completion cannot recreate a disconnected card", async () => {
  let resolve; window.loadCardHelpers = () => new Promise(r => { resolve = r; });
  const card = mount(); card.remove(); resolve(helpers); await settle(); equal(native(card), null); equal(created, []); equal(requests, []);
});
await test("entity change during helper loading discards the previous native result", async () => {
  let resolve; window.loadCardHelpers = () => new Promise(r => { resolve = r; });
  const card = mount(); card.setConfig({ entity: "climate.synthetic_two" }); card.hass = hass(state({ entity_id: "climate.synthetic_two" }));
  window.loadCardHelpers = load; resolve(helpers); await settle();
  assert(created.every(x => x.config.entity === "climate.synthetic_two")); equal(requests, []);
});
await test("target loss during helper loading cannot create an interactive dial", async () => {
  let resolve; window.loadCardHelpers = () => new Promise(r => { resolve = r; });
  const card = mount(); card.hass = hass(state({}, { temperature: null, "Zieltemperatur verfügbar": false }));
  resolve(helpers); await settle(); equal(native(card), null); equal(created, []); equal(requests, []);
});
await test("dependency timeout produces an inert retry state", async () => {
  const originalTimeout = window.setTimeout;
  window.setTimeout = (callback, _delay) => originalTimeout(callback, 0);
  try {
    window.loadCardHelpers = () => new Promise(() => {}); const card = mount(); await settle();
    equal(native(card), null); assert(card.shadowRoot.textContent.includes("Thermostat derzeit nicht verfügbar"));
    equal(requests, []); card.remove();
  } finally { window.setTimeout = originalTimeout; }
});
await test("failed dependency remains inert and explicit retry can recover", async () => {
  window.loadCardHelpers = () => Promise.reject(new Error("synthetic failure")); const card = mount(); await settle();
  equal(native(card), null); assert(card.shadowRoot.textContent.includes("Thermostat derzeit nicht verfügbar"));
  const before = helperCalls; card.hass = hass(); await settle(); equal(helperCalls, before);
  window.loadCardHelpers = load; card.shadowRoot.querySelector("button").click(); await settle(); assert(native(card)); equal(requests, []);
});
await test("service failure preserves reported mode and shows a bounded error", async () => {
  const h = hass(); h.callService = async () => { throw new Error("synthetic private error"); };
  const card = mount(h); await settle(); pick(menu(card), "Heizen"); await settle();
  equal(menu(card).value, "Auto"); assert(card.shadowRoot.textContent.includes("Modus konnte nicht geändert werden"));
  assert(!card.shadowRoot.textContent.includes("synthetic private error"));
});
await test("pending service blocks duplicate selections without optimistic readback", async () => {
  let resolve; const h = hass(); h.callService = (...args) => { requests.push(args); return new Promise(r => { resolve = r; }); };
  const card = mount(h); await settle(); pick(menu(card), "Heizen"); pick(menu(card), "Absenken");
  equal(requests.length, 1); equal(menu(card).value, "Auto"); assert(menu(card).disabled); resolve(); await settle(); assert(!menu(card).disabled);
});
await test("removing a card clears handlers and pending work without service calls", async () => {
  const card = mount(); await settle(); const old = menu(card); card.remove(); pick(old, "Heizen"); await settle();
  equal(requests, []); equal(card.shadowRoot.children.length, 0);
});
const activeMode=p=>p==='Aus'?'off':p==='Auto'?'auto':'heat';
const modeHass=(preset,attrs={},entity='climate.synthetic_one')=>hass(state({entity_id:entity,state:activeMode(preset)},{preset_mode:preset,'Zieltemperatur verfügbar':preset!=='Aus',temperature:preset==='Aus'?null:19,...attrs}));
for(const entity of['climate.synthetic_one','climate.synthetic_two'])for(const from of PRESETS)for(const to of PRESETS)
await test(`stable native ${entity} ${from} → ${to}, no stale API`,async()=>{
 const card=mount(modeHass(from,{},entity),{entity});await settle();const old=native(card),api=old?.control._api;
 card.hass=modeHass(to,{temperature:null,'Zieltemperatur verfügbar':false,Sollwertbasis:null},entity);await settle();
 if(old){equal(native(card),old);assert(old.inert);if(to!=='Aus')equal(old.control.stateObj.attributes.temperature,19);}
 assert(!menu(card).disabled,'Independently available modes must remain usable');
 if(api)await api.callService('climate','set_temperature',{entity_id:entity,temperature:19.5});equal(requests,[]);
 card.hass=modeHass(to,{temperature:to==='Aus'?null:20,Sollwertbasis:OTHER},entity);await settle();
 if(to!=='Aus'){assert(native(card));if(old)equal(native(card),old);assert(!native(card).inert);equal(native(card).control.stateObj.attributes.temperature,20)}else if(old){equal(native(card),old);equal(old.control.stateObj.state,'off')}
 if(api)await api.callService('climate','set_temperature',{entity_id:entity,temperature:19.5});equal(requests,[]);
});
for(const kind of['basis','mode','target','min','max','step','unit','capabilities'])
await test(`atomic qualified ${kind} change keeps native and retires old API`,async()=>{
 const card=mount();await settle();const old=native(card),node=old.control,api=node._api,h=modeHass('Auto'),a=h.states.climate;
 const attrs=h.states['climate.synthetic_one'].attributes;
 if(kind==='basis')attrs.Sollwertbasis=OTHER;if(kind==='mode'){h.states['climate.synthetic_one'].state='heat';attrs.preset_mode='Heizen';attrs.temperature=22}
 if(kind==='target')attrs.temperature=20;if(kind==='min')attrs.min_temp=15;if(kind==='max')attrs.max_temp=23;if(kind==='step')attrs.target_temp_step=1;
 if(kind==='unit')h.config.unit_system.temperature='°F';if(kind==='capabilities')attrs.supported_features=387;
 card.hass=h;await settle();equal(native(card),old);equal(old.control,node);await api.callService('climate','set_temperature',{entity_id:'climate.synthetic_one',temperature:19.5});equal(requests,[]);
});
await test('draft survives old report and no native node is replaced',async()=>{
 const h=hass(),card=mount(h);await settle();const old=native(card),node=old.control;old.syntheticTemperature(19.5);await settle();
 equal(node.stateObj.attributes.temperature,19.5);card.hass=h;await settle();equal(node.stateObj.attributes.temperature,19.5);equal(native(card),old);equal(requests.length,1);
 card.hass=modeHass('Auto',{temperature:null,'Zieltemperatur verfügbar':false});await settle();equal(native(card),old);equal(node.stateObj.attributes.temperature,19.5);assert(old.inert);
});
await test('a genuinely replaced HA session never inherits old requested value or confirmed range',async()=>{
 const h=hass(),card=mount(h);await settle();const old=native(card);old.syntheticTemperature(19.5);await settle();equal(old.control.stateObj.attributes.temperature,19.5);
 const replacement=modeHass('Auto',{temperature:null,'Zieltemperatur verfügbar':false});replacement.connection={connected:true};card.hass=replacement;await settle();
 equal(native(card),old);equal(old.control.stateObj.attributes.temperature,null);assert(old.inert);equal(card._presentation.intent,null);equal(card._presentation.confirmed,null);
});
fixtures.replaceChildren();delete window.loadCardHelpers;
window.cardTestResults={passed:results.every(x=>x.passed),tests:results.length,results,scope:'Real browser custom elements/lifecycle with synthetic native/control/helper/service handlers; no HA connection.'};
document.querySelector('#results').textContent+='\n'+JSON.stringify(window.cardTestResults,null,2);
