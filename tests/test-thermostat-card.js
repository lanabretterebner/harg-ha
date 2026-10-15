import { installHargassnerThermostatCard } from "../dist/hargassner-thermostat-popup.js";

// Isolated HA helpers/service stand-ins only. Never connect this fixture to HA.
const PRESETS = ["Aus", "Auto", "Heizen", "Absenken", "1x Heizen", "1x Absenken"];
const BASIS = "a".repeat(32), OTHER = "b".repeat(32);
const fixtures = document.querySelector("#fixtures");
let requests = [], created = [], helperCalls = 0;
class NativeThermostat extends HTMLElement {
  constructor() { super(); this.attachShadow({ mode: "open" }); }
  setConfig(config) { this.config = config; }
  set hass(value) { this._hass = value; this.shadowRoot.textContent = String(value.states[this.config.entity]?.attributes.temperature); }
  get hass() { return this._hass; }
  connectedCallback() {
    const event = new Event("context-request", { bubbles: true, composed: true });
    event.context = "hassApi"; event.callback = api => { this.api = api; };
    this.dispatchEvent(event);
  }
  syntheticTemperature(value) {
    this.api.callService("climate", "set_temperature", { entity_id: this.config.entity, temperature: value });
  }
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
    "Zieltemperatur verfügbar": true, Raumkorrektur: "0", "Heizkreisstatus": "Tag/Nacht Abschaltung",
    "Heizkreisstatus-Code": 4, "Heizkreisstatus verfügbar": true, ...attrs } });
const hass = (value = state()) => ({ connected: true, connection: { connected: true }, states: { [value.entity_id]: value },
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
  fixtures.replaceChildren(); requests = []; created = []; helperCalls = 0; window.loadCardHelpers = load;
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
  assert(card.shadowRoot.textContent.includes("Ist-Temperatur: Nicht verfügbar"));
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
  assert(card.shadowRoot.textContent.includes("Ist-Temperatur: 0 °C"));
  card.hass = hass(); await settle(); assert(card.shadowRoot.textContent.includes("Ist-Temperatur: Nicht verfügbar"));
});
await test("raw Celsius room correction is not mislabeled in a Fahrenheit HA system", async () => {
  const h = hass(state({}, { temperature: 66.2, min_temp: 60, max_temp: 72, target_temp_step: 0.9,
    current_temperature: 68, Raumkorrektur: "0.5" })); h.config.unit_system.temperature = "°F";
  const card = mount(h); await settle();
  assert(card.shadowRoot.textContent.includes("Ist-Temperatur: 68 °F"));
  assert(card.shadowRoot.textContent.includes("Raumkorrektur: 0,5 °C"));
  equal(native(card).hass, h); equal(h.states["climate.synthetic_one"].attributes.temperature, 66.2); equal(requests, []);
});
await test("disconnected HA removes the interactive dial and blocks late selection", async () => {
  const card = mount(); await settle(); const old = menu(card), h = hass(); h.connected = false; card.hass = h; await settle();
  equal(native(card), null); assert(menu(card).disabled); pick(old, "Heizen"); await settle(); equal(requests, []);
  assert(card.shadowRoot.textContent.includes("HeizkreisstatusNicht verfügbar"));
});
await test("unavailable climate blocks modes and hides old status", async () => {
  const card = mount(); await settle(); card.hass = hass(state({ state: "unavailable" })); await settle();
  equal(native(card), null); assert(menu(card).disabled); pick(menu(card), "Heizen"); await settle(); equal(requests, []);
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
const activeMode = preset => preset === "Auto" ? "auto" : preset === "Aus" ? "off" : "heat";
const modeHass = (preset, attrs = {}, entity = "climate.synthetic_one") => hass(state({ entity_id: entity, state: activeMode(preset) },
  { preset_mode: preset, Sollwertbasis: BASIS, "Zieltemperatur verfügbar": preset !== "Aus", ...attrs }));
const v2 = (from, to, reason = "mode_report") => ({ id: "mode_operation", basis: BASIS, phase: "readback", version: 2,
  reason, previousPreset: from, nextPreset: to });
for (const entity of ["climate.synthetic_one", "climate.synthetic_two"]) for (const from of PRESETS) for (const to of PRESETS)
await test(`V2 ${entity} ${from} → ${to} preserves only a confirmed active presentation`, async () => {
  const card = mount(modeHass(from, {}, entity), { entity }); await settle(); const old = native(card), api = old?.api;
  card.hass = modeHass(to, { temperature: null, "Zieltemperatur verfügbar": false, Sollwertbasis: null, Sollwertaktualisierung: v2(from, to) }, entity); await settle();
  if (from !== "Aus" && to !== "Aus") {
    equal(native(card), old); assert(old.inert); equal(menu(card).value, to); assert(menu(card).disabled);
    equal(old.hass.states[entity].attributes.temperature, 19); equal(old.hass.states[entity].state, activeMode(to));
    equal(old.hass.states[entity].attributes.preset_mode, to); equal(old.hass.states[entity].attributes["Zieltemperatur verfügbar"], false);
  } else equal(native(card), null);
  if (api) await api.callService("climate", "set_temperature", { entity_id: entity, temperature: 21 }); equal(requests, []);
  card.hass = modeHass(to, { Sollwertbasis: OTHER, temperature: 20 }, entity); await settle();
  if (to === "Aus") equal(native(card), null); else {
    assert(native(card)); assert(!native(card).inert); if (old) assert(native(card) !== old);
    equal(native(card).hass.states[entity].attributes.temperature, 20);
  }
  if (api) await api.callService("climate", "set_temperature", { entity_id: entity, temperature: 21 }); equal(requests, []);
});
await test("V2 superseding modes and known input/phase changes keep one fixed display epoch", async () => {
  const card = mount(modeHass("Auto")); await settle(); const old = native(card);
  for (const [next, reason] of [["Heizen", "mode_report"], ["Absenken", "mode_report"], ["1x Heizen", "parameter_report"], ["1x Absenken", "phase_report"], ["Auto", "pending_change"]]) {
    card.hass = modeHass(next, { temperature: null, "Zieltemperatur verfügbar": false, Sollwertbasis: null, Sollwertaktualisierung: v2("Auto", next, reason) }); await settle();
    equal(native(card), old); assert(old.inert); equal(menu(card).value, next); equal(old.hass.states["climate.synthetic_one"].attributes.temperature, 19);
  }
  equal(requests, []);
});
await test("V2 proof cannot invent a source mode, next mode, reason or version", async () => {
  for (const patch of [{ previousPreset: "Heizen" }, { nextPreset: "Heizen" }, { previousPreset: "Aus" },
    { reason: "anything" }, { version: 3 }, { extra: true }, { nextPreset: "unknown" }]) {
    const card = mount(modeHass("Auto")); await settle();
    card.hass = modeHass("Absenken", { temperature: null, "Zieltemperatur verfügbar": false, Sollwertaktualisierung: { ...v2("Auto", "Absenken"), ...patch } }); await settle();
    equal(native(card), null); card.remove();
  }
});
await test("V2 mismatched HVAC projection fails closed despite apparently valid mode labels", async () => {
  const card = mount(modeHass("Auto")); await settle(); const h = modeHass("Heizen", { temperature: null, "Zieltemperatur verfügbar": false, Sollwertaktualisierung: v2("Auto", "Heizen") });
  h.states["climate.synthetic_one"].state = "auto"; card.hass = h; await settle(); equal(native(card), null);
});
await test("V2 latest measured/status/correction data stays truthful beside the historical target", async () => {
  const card = mount(modeHass("Auto")); await settle();
  card.hass = modeHass("Heizen", { temperature: null, "Zieltemperatur verfügbar": false, current_temperature: 22,
    Raumkorrektur: "0.5", "Heizkreisstatus": "Heizen", Sollwertaktualisierung: v2("Auto", "Heizen") }); await settle();
  const shown = native(card).hass.states["climate.synthetic_one"].attributes;
  equal(shown.temperature, 19); equal(shown.current_temperature, 22); equal(shown.Raumkorrektur, "0.5");
  assert(card.shadowRoot.textContent.includes("Ist-Temperatur: 22 °C")); assert(card.shadowRoot.textContent.includes("HeizkreisstatusHeizen")); equal(requests, []);
});
await test("unit changes during an otherwise marked reconciliation cannot reinterpret an old target", async () => {
  const card = mount(modeHass("Auto")); await settle(); const h = modeHass("Heizen", { temperature: null,
    "Zieltemperatur verfügbar": false, Sollwertaktualisierung: v2("Auto", "Heizen") });
  h.config.unit_system.temperature = "°F"; card.hass = h; await settle(); equal(native(card), null);
});
await test("V2 readback repeats and superseding reason do not renew the eight-second visual deadline", async () => {
  const card = mount(modeHass("Auto")); await settle(); const set = window.setTimeout, clear = window.clearTimeout; let callback, scheduled = 0;
  window.setTimeout = (fn, delay, ...args) => delay === 8000 ? (scheduled++, callback = fn, -701) : set(fn, delay, ...args);
  window.clearTimeout = value => { if (value !== -701) clear(value); };
  try {
    for (const next of ["Heizen", "Absenken", "Auto"]) {
      card.hass = modeHass(next, { temperature: null, "Zieltemperatur verfügbar": false, Sollwertaktualisierung: v2("Auto", next) }); await settle();
    }
    equal(scheduled, 1); callback(); await settle(); equal(native(card), null);
  } finally { window.setTimeout = set; window.clearTimeout = clear; }
});
await test("V2 native handle capability survives only inside the inert historical projection", async () => {
  const card = mount(modeHass("Auto", { supported_features: 3 })); await settle(); const h = modeHass("Heizen", {
    supported_features: 128, temperature: null, "Zieltemperatur verfügbar": false,
    Sollwertaktualisierung: v2("Auto", "Heizen") }); card.hass = h; await settle();
  const shown = native(card).hass.states["climate.synthetic_one"].attributes;
  equal(shown.supported_features, 129); equal(h.states["climate.synthetic_one"].attributes.supported_features, 128);
  equal(h.states["climate.synthetic_one"].attributes.temperature, null); assert(native(card).inert); equal(requests, []);
});
for (const kind of ["basis", "mode", "target", "min", "max", "step", "unit", "capabilities"])
await test(`atomic qualified ${kind} change replaces native without a null-target stage`, async () => {
  const card = mount(modeHass("Auto")); await settle(); const old = native(card), api = old.api;
  assert(api); const h = modeHass("Auto"); const a = h.states["climate.synthetic_one"].attributes;
  if (kind === "basis") a.Sollwertbasis = OTHER;
  if (kind === "mode") { h.states["climate.synthetic_one"].state = "heat"; a.preset_mode = "Heizen"; a.temperature = 22; }
  if (kind === "target") a.temperature = 20;
  if (kind === "min") a.min_temp = 15; if (kind === "max") a.max_temp = 23;
  if (kind === "step") a.target_temp_step = 1;
  if (kind === "unit") h.config.unit_system.temperature = "°F";
  if (kind === "capabilities") a.supported_features = 387;
  card.hass = h;
  assert(native(card) && native(card) !== old); assert(!native(card).inert);
  equal(native(card).hass, h); equal(card.shadowRoot.querySelector(".placeholder").hidden, true);
  await api.callService("climate", "set_temperature", { entity_id: "climate.synthetic_one", temperature: 19.5 }); equal(requests, []);
  card.hass = modeHass("Auto"); await settle();
  await api.callService("climate", "set_temperature", { entity_id: "climate.synthetic_one", temperature: 19.5 }); equal(requests, []);
});
await test("measurement/status heartbeats keep the current native control and its scoped API", async () => {
  const card = mount(modeHass("Auto")); await settle(); const old = native(card), api = old.api;
  card.hass = modeHass("Auto", { current_temperature: 21, "Heizkreisstatus": "Heizen", Raumkorrektur: "0.5" });
  equal(native(card), old); await api.callService("climate", "set_temperature", { entity_id: "climate.synthetic_one", temperature: 19.5 });
  equal(requests.length, 1);
});
fixtures.replaceChildren(); delete window.loadCardHelpers;
window.cardTestResults = { passed: results.every(x => x.passed), tests: results.length, results,
  scope: "Real browser custom elements and lifecycle; synthetic native-card/helper/service handlers, no HA connections." };
document.querySelector("#results").textContent += "\n" + JSON.stringify(window.cardTestResults, null, 2);
