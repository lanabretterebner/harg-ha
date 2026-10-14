import { installHargassnerThermostatPopup } from "../dist/hargassner-thermostat-popup.js";

const PRESETS = ["Aus", "Auto", "Heizen", "Absenken", "1x Heizen", "1x Absenken"];
let updates = 0, serviceCalls = 0, networkCalls = 0, serviceRequests = [];
const nativeFetch = window.fetch;
window.fetch = () => { networkCalls++; throw new Error("Unexpected network request"); };

class Menu extends HTMLElement {
  constructor() { super(); this.attachShadow({ mode: "open" }); this._options = []; this._label = ""; }
  get options() { return this._options; }
  set options(value) { this._options = value; this.paint(); }
  get label() { return this._label; }
  set label(value) { this._label = value; this.paint(); }
  paint() { this.shadowRoot.textContent = `${this._label}: ${JSON.stringify(this._options)}`; updates++; }
}
customElements.define("ha-control-select-menu", Menu);
class Popup extends HTMLElement {
  constructor() {
    super(); this.attachShadow({ mode: "open" }); this.reverse = false;
    this.nativeLabel = "Preset";
    this._formatters = { formatEntityAttributeName: () => this.nativeLabel };
    this._api = { callService: async (domain, service, data) => {
      serviceCalls++; serviceRequests.push({ domain, service, data: JSON.parse(JSON.stringify(data)) });
    } };
  }
  updated(...args) { this.updatedArguments = args; return "native-update-return"; }
  connectedCallback() { this.render(); }
  render(replace = false) {
    if (!this.stateObj) return;
    let container = this.shadowRoot.querySelector("ha-more-info-control-select-container");
    if (!container || replace) {
      container = document.createElement("ha-more-info-control-select-container");
      this.hvac = document.createElement("ha-control-select-menu");
      this.preset = document.createElement("ha-control-select-menu");
      for (const menu of this.reverse ? [this.preset, this.hvac] : [this.hvac, this.preset]) container.append(menu);
      this.preset.addEventListener("wa-select", event => {
        const value = event.detail.item.value;
        if (value !== this.stateObj.attributes.preset_mode)
          this._api.callService("climate", "set_preset_mode", { entity_id: this.stateObj.entity_id, preset_mode: value });
      });
      this.hvac.addEventListener("wa-select", event => {
        this._api.callService("climate", "set_hvac_mode", { entity_id: this.stateObj.entity_id, hvac_mode: event.detail.item.value });
      });
      this.temperatureControl = document.createElement("ha-state-control-climate-temperature");
      this.temperatureControl.addEventListener("temperature-user-change", event => {
        this._api.callService("climate", "set_temperature", { entity_id: this.stateObj.entity_id, temperature: event.detail.value });
      });
      this.shadowRoot.replaceChildren(this.temperatureControl, container);
    }
    this.temperatureControl.stateObj = this.stateObj;
    this.hvac.label = "Mode";
    this.hvac.value = this.stateObj.state;
    this.hvac.options = this.stateObj.attributes.hvac_modes.map(value => ({ value, label: `Translated ${value}` }));
    this.preset.label = this.nativeLabel;
    this.preset.value = this.stateObj.attributes.preset_mode;
    this.preset.options = this.stateObj.attributes.preset_modes.map(value => ({ value, label: `Translated ${value}` }));
    this.updated(new Map([["stateObj", null]]));
  }
}
customElements.define("more-info-climate", Popup);
const state = (changes = {}, attributes = {}) => ({
  entity_id: "climate.synthetic_hargassner", state: "auto",
  ...changes,
  attributes: { hargassner_control_schema: "configured_room_setpoint_v1", hvac_modes: ["off", "heat", "auto"],
    preset_modes: ["none", ...PRESETS], preset_mode: "Auto", ...attributes },
});
const assert = (condition, message = "Assertion failed") => { if (!condition) throw new Error(message); };
const equal = (a, b, message) => assert(JSON.stringify(a) === JSON.stringify(b), message || `${JSON.stringify(a)} != ${JSON.stringify(b)}`);
const settle = async () => { await Promise.resolve(); await new Promise(resolve => { const channel = new MessageChannel(); channel.port1.onmessage = () => { channel.port1.close(); channel.port2.close(); resolve(); }; channel.port2.postMessage(null); }); await Promise.resolve(); };
const fixtures = document.querySelector("#fixtures");
const results = [];
let controller;
async function reset() {
  window.hargassnerThermostatPopup?.stop(); fixtures.replaceChildren();
  updates = 0; serviceCalls = 0; networkCalls = 0; serviceRequests = [];
  controller = installHargassnerThermostatPopup(window); await controller.ready; await settle();
}
function mount(value = state(), reverse = false) {
  const popup = document.createElement("more-info-climate"); popup.stateObj = value; popup.reverse = reverse;
  fixtures.append(popup); return popup;
}
function modus(popup) { return popup.shadowRoot.querySelector('[data-hargassner-modus]'); }
function select(menu, value) {
  menu.dispatchEvent(new CustomEvent("wa-select", { detail: { item: { value } }, bubbles: true, composed: true }));
}
function patched(popup) {
  for (const menu of [popup.hvac,popup.preset]) {
    equal(menu.style.getPropertyValue("display"), "none");
    equal(menu.style.getPropertyPriority("display"), "important");
  }
  const menu = modus(popup); assert(menu, "Missing replacement menu");
  equal(menu.label, "Modus"); equal(menu.options.map(option => option.value), PRESETS);
  equal(menu.value, popup.stateObj.attributes.preset_mode);
  equal(popup.shadowRoot.querySelectorAll('[data-hargassner-modus]').length,1);
  equal(popup.preset.label,popup.nativeLabel);
}
function native(popup) {
  equal(popup.hvac.style.getPropertyValue("display"), "");
  equal(popup.preset.style.getPropertyValue("display"), "");
  equal(popup.preset.label, popup.nativeLabel);
  assert(!modus(popup), "Replacement menu remained after cleanup");
}
async function test(name, run) {
  window.popupTestProgress = { name, completed: results.length };
  try { await reset(); await run(); assert(serviceCalls === 0, "Resource dispatched a selection"); assert(networkCalls === 0, "Resource used network"); results.push({ name, passed: true }); }
  catch (error) { results.push({ name, passed: false, error: error.message }); }
}

await test("one Modus selector replaces both native menus without changing state", async () => {
  const value = state(), before = JSON.stringify(value), p = mount(value); await settle(); patched(p);
  equal(p.hvac.value, "auto"); equal(p.preset.value, "Auto"); equal(JSON.stringify(value), before);
});
await test("matches exact option values despite reversed order and translated labels", async () => {
  const p = mount(state(), true); await settle(); patched(p);
  const extra = document.createElement("ha-control-select-menu"); extra.options = [{ value: "low" }, { value: "high" }]; extra.label = "Fan";
  p.shadowRoot.querySelector("ha-more-info-control-select-container").prepend(extra); await settle(); patched(p); equal(extra.label, "Fan");
});
for (const [name, value] of [
  ["unmarked entity", state({}, { hargassner_control_schema: undefined })],
  ["wrong schema", state({}, { hargassner_control_schema: "configured_room_setpoint_v2" })],
  ["nonduplicate cooling HVAC capability", state({}, { hvac_modes: ["off", "heat", "auto", "cool"] })],
  ["unexpected single HVAC mode", state({}, { hvac_modes: ["auto"] })],
  ["non-climate entity", state({ entity_id: "sensor.synthetic" })],
  ["missing real preset", state({}, { preset_modes: PRESETS.slice(0, 5) })],
  ["duplicate preset", state({}, { preset_modes: [...PRESETS, "Auto"] })],
  ["unexpected extra preset", state({}, { preset_modes: [...PRESETS, "Boost"] })],
  ["synthetic none is the current preset", state({}, { preset_mode: "none" })],
  ["unknown current preset", state({}, { preset_mode: "unknown" })],
]) await test(`${name} leaves native UI intact`, async () => { const p = mount(value); await settle(); native(p); equal(controller.status().activePopups, 0); });
await test("native presets without synthetic none support the replacement menu", async () => {
  const p = mount(state({}, { preset_modes: [...PRESETS] })); await settle(); patched(p);
});
await test("unavailable climate preserves disabled/current state without choosing a value", async () => {
  const p = mount(state({ state: "unavailable" })); p.hvac.disabled = true; p.preset.disabled = true; await settle(); patched(p);
  equal(p.hvac.value, "unavailable"); assert(p.hvac.disabled && p.preset.disabled && modus(p).disabled);
});
await test("ambiguous duplicate HVAC controls restore native UI", async () => {
  const p = mount(); await settle(); const extra = document.createElement("ha-control-select-menu"); extra.options = p.hvac.options; extra.value = "auto";
  p.shadowRoot.querySelector("ha-more-info-control-select-container").append(extra); await settle(); native(p); equal(controller.status().activePopups, 0);
});
await test("unexpected HVAC values restore native UI", async () => {
  const p = mount(); await settle(); p.hvac.options = [{ value: "heat" }]; await settle(); native(p);
});
await test("unsupported primitive options restore native UI", async () => {
  const p = mount(); await settle(); p.preset.options = [...PRESETS]; await settle(); native(p);
});
await test("same-instance native rerender preserves current preset and finite observer work", async () => {
  const p = mount(); await settle(); p.stateObj = state({}, { preset_mode: "Absenken" }); p.render(); await settle(); patched(p);
  equal(modus(p).value, "Absenken"); const count = updates; await settle(); equal(updates, count); assert(count < 40);
});
await test("replacement menu nodes restore old nodes and patch the new pair", async () => {
  const p = mount(); await settle(); const oldHvac = p.hvac, oldPreset = p.preset; p.render(true); await settle(); patched(p);
  equal(oldHvac.style.display, ""); equal(oldPreset.label, "Preset"); assert(oldPreset.options.some(option => option.value === "none"));
});
await test("marker removal restores labels, options and observers", async () => {
  const p = mount(); await settle(); delete p.stateObj.attributes.hargassner_control_schema; p.updated(); await settle(); native(p);
  assert(p.preset.options.some(option => option.value === "none")); equal(controller.status().activePopups, 0);
});
await test("entity/locale reuse restores native current formatting", async () => {
  const p = mount(); await settle(); p.nativeLabel = "Mode prédéfini"; p.stateObj = state({ entity_id: "climate.unrelated" }, { hargassner_control_schema: undefined });
  p.render(); await settle(); native(p); equal(controller.status().activePopups, 0);
});
await test("removing an ancestor across shadow DOM disconnects scoped observers", async () => {
  const shell = document.createElement("div"); shell.attachShadow({ mode: "open" }); fixtures.append(shell);
  const p = document.createElement("more-info-climate"); p.stateObj = state(); shell.shadowRoot.append(p); await settle(); patched(p);
  shell.remove(); await settle(); native(p); equal(controller.status().activePopups, 0);
  fixtures.append(shell); await settle(); patched(p);
});
await test("late installation finds an already-open nested popup", async () => {
  controller.stop(); const shell = document.createElement("div"); shell.attachShadow({ mode: "open" }); fixtures.append(shell);
  const p = document.createElement("more-info-climate"); p.stateObj = state(); shell.shadowRoot.append(p);
  controller = installHargassnerThermostatPopup(window); await controller.ready; await settle(); patched(p);
});
await test("stop restores pre-existing inline display and original update hook", async () => {
  const p = mount(); p.hvac.style.setProperty("display", "inline-flex", "important"); await settle(); patched(p);
  controller.stop(); equal(p.hvac.style.getPropertyValue("display"), "inline-flex"); equal(p.hvac.style.getPropertyPriority("display"), "important");
  equal(p.preset.label, "Preset"); equal(p.updated("test"), "native-update-return"); equal(p.updatedArguments, ["test"]);
});
await test("external display change is not overwritten during stop", async () => {
  const p = mount(); await settle(); p.hvac.style.setProperty("display", "grid"); controller.stop(); equal(p.hvac.style.display, "grid");
});
await test("duplicate installation is idempotent", async () => {
  const p = mount(); await settle(); equal(installHargassnerThermostatPopup(window), controller); patched(p); equal(controller.status().activePopups, 1);
});
await test("pagehide cleanup restores native UI and clears tracking", async () => {
  const p = mount(); await settle(); window.dispatchEvent(new Event("pagehide")); native(p); equal(controller.status().activePopups, 0); equal(controller.running, false);
});
await test("native preset event handler is retained, with no synthetic selection", async () => {
  const p = mount(); await settle(); patched(p); equal(serviceCalls, 0);
  p.preset.dispatchEvent(new CustomEvent("wa-select", { detail: { item: { value: "Heizen" } } })); equal(serviceCalls, 1);
  serviceCalls = 0; // Explicit synthetic user event above; resource emitted none.
});

for (const mode of PRESETS) await test(`Modus ${mode} calls only native set_preset_mode once`, async () => {
  const current = mode === "Auto" ? "Aus" : "Auto";
  const value = state({ entity_id: "climate.synthetic_hk2" }, { preset_mode: current });
  const before = JSON.stringify(value), p = mount(value); await settle(); patched(p);
  const temperature = p.temperatureControl;
  select(modus(p), mode);
  equal(serviceRequests, [{ domain: "climate", service: "set_preset_mode",
    data: { entity_id: "climate.synthetic_hk2", preset_mode: mode } }]);
  equal(JSON.stringify(value), before); equal(modus(p).value, current);
  equal(p.temperatureControl, temperature); equal(temperature.stateObj, value);
  serviceCalls = 0; // Explicit synthetic user selection was the sole operation.
});
await test("all six reported modes update Modus while HVAC projection remains native", async () => {
  const p = mount(); await settle();
  for (const mode of PRESETS) {
    const hvac = mode === "Aus" ? "off" : mode === "Auto" ? "auto" : "heat";
    p.stateObj = state({ state: hvac }, { preset_mode: mode }); p.render(); await settle(); patched(p);
    equal(modus(p).value, mode); equal(p.hvac.value, hvac); equal(p.preset.value, mode);
  }
});
await test("same current choice and invalid choices cannot call any service", async () => {
  const p = mount(); await settle();
  for (const value of ["Auto", "none", "off", "heat", "Boost", null, true, undefined]) select(modus(p), value);
  equal(serviceRequests, []);
});
await test("disabled native control blocks user event immediately before an update", async () => {
  const p = mount(); await settle(); p.preset.disabled = true;
  select(modus(p), "Heizen"); equal(serviceRequests, []); assert(modus(p).disabled);
});
await test("unavailable state blocks selection without inventing a current mode", async () => {
  const p = mount(); await settle(); p.stateObj = state({ state: "unavailable" }); p.hvac.value = "unavailable";
  select(modus(p), "Heizen"); equal(serviceRequests, []); equal(modus(p).value,"Auto"); assert(modus(p).disabled);
});
await test("stale selector cannot address a newly reused entity", async () => {
  const p = mount(); await settle(); const old = modus(p);
  p.stateObj = state({ entity_id: "climate.synthetic_other" });
  select(old, "Heizen"); equal(serviceRequests, []); assert(modus(p) !== old);
});
await test("marker loss before observer processing blocks selection", async () => {
  const p = mount(); await settle(); const old = modus(p);
  delete p.stateObj.attributes.hargassner_control_schema;
  select(old, "Heizen"); equal(serviceRequests, []); native(p);
});
await test("detached selector cannot dispatch after popup removal", async () => {
  const p = mount(); await settle(); const old = modus(p); p.remove();
  select(old, "Heizen"); equal(serviceRequests, []); equal(controller.status().activePopups,0);
});
await test("stopped resource removes replacement handlers and restores both native styles", async () => {
  const p = mount(); p.preset.style.setProperty("display","inline-block"); await settle(); const old = modus(p);
  controller.stop(); select(old,"Heizen"); equal(serviceRequests,[]);
  equal(p.preset.style.display,"inline-block"); equal(p.hvac.style.display,""); assert(!modus(p));
});
await test("native temperature handler remains available and separate", async () => {
  const p = mount(); await settle(); const temperature = p.temperatureControl;
  temperature.dispatchEvent(new CustomEvent("temperature-user-change", { detail:{value:22.5} }));
  equal(serviceRequests,[{domain:"climate",service:"set_temperature",
    data:{entity_id:p.stateObj.entity_id,temperature:22.5}}]);
  equal(p.temperatureControl,temperature); patched(p); serviceCalls=0;
});
await test("repeated renders keep one mode handler and one service request", async () => {
  const p = mount(); await settle();
  for (let i=0;i<8;i++) { p.render(); await settle(); patched(p); }
  select(modus(p),"Heizen"); equal(serviceRequests.length,1);
  equal(serviceRequests[0].service,"set_preset_mode"); serviceCalls=0;
});
await test("separate native menu parents retain the native layout", async () => {
  const p = mount(); await settle(); const wrapper=document.createElement("div");
  p.shadowRoot.append(wrapper); wrapper.append(p.preset); await settle(); native(p);
});
await test("older installed resource is stopped before version 1.1.0 starts", async () => {
  controller.stop(); let stopped=0;
  const prior={version:"1.0.0",running:true,stop(){stopped++;this.running=false;}};
  window[Symbol.for("hargassner.thermostat.popup.v1")]=prior;
  controller=installHargassnerThermostatPopup(window); await controller.ready;
  equal(stopped,1); equal(controller.version,"1.1.0");
  const p=mount(); await settle(); patched(p);
});

controller.stop(); window.fetch = nativeFetch;
window.popupTestResults = { passed: results.every(result => result.passed), tests: results.length, results,
  scope: "Actual browser Custom Elements, shadow DOM and MutationObserver; synthetic HA native handlers record service payloads; no HA connection or real services." };
document.querySelector("#results").textContent = JSON.stringify(window.popupTestResults, null, 2);
