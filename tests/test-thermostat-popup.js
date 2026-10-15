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
    this.hass={connected:true,connection:{connected:true},states:{},config:{unit_system:{temperature:"°C"}},callService:async()=>{throw Error("Unexpected parent service")}};
    this._formatters = { formatEntityAttributeName: () => this.nativeLabel };
    this._api = { callService: async (domain, service, data) => {
      serviceCalls++; serviceRequests.push({ domain, service, data: JSON.parse(JSON.stringify(data)) });
    } };
  }
  updated(...args) { this.updatedArguments = args; return "native-update-return"; }
  connectedCallback() { this.render(); }
  render(replace = false) {
    if (!this.stateObj) return;
    this.hass.states[this.stateObj.entity_id]=this.stateObj;
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
function operatingStatus(popup) { return popup.shadowRoot.querySelector('[data-hargassner-operating-state]'); }
function reportedStatus(label = "Tag/Nacht Abschaltung", code = 4, available = true) {
  return { "Heizkreisstatus": label, "Heizkreisstatus-Code": code, "Heizkreisstatus verfügbar": available };
}
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
await test("all six options expose embedded native icon paths and preserve native options and exact mode values", async () => {
  const p = mount(); await settle();
  const menu = modus(p), original = JSON.stringify([p.hvac.options, p.preset.options]);
  equal(menu.options, [
    { value: "Aus", label: "Aus", iconPath: "M16.56,5.44L15.11,6.89C16.84,7.94 18,9.83 18,12A6,6 0 0,1 12,18A6,6 0 0,1 6,12C6,9.83 7.16,7.94 8.88,6.88L7.44,5.44C5.36,6.88 4,9.28 4,12A8,8 0 0,0 12,20A8,8 0 0,0 20,12C20,9.28 18.64,6.88 16.56,5.44M13,3H11V13H13" },
    { value: "Auto", label: "Auto", iconPath: "M15,13H16.5V15.82L18.94,17.23L18.19,18.53L15,16.69V13M19,8H5V19H9.67C9.24,18.09 9,17.07 9,16A7,7 0 0,1 16,9C17.07,9 18.09,9.24 19,9.67V8M5,21C3.89,21 3,20.1 3,19V5C3,3.89 3.89,3 5,3H6V1H8V3H16V1H18V3H19A2,2 0 0,1 21,5V11.1C22.24,12.36 23,14.09 23,16A7,7 0 0,1 16,23C14.09,23 12.36,22.24 11.1,21H5M16,11.15A4.85,4.85 0 0,0 11.15,16C11.15,18.68 13.32,20.85 16,20.85A4.85,4.85 0 0,0 20.85,16C20.85,13.32 18.68,11.15 16,11.15Z" },
    { value: "Heizen", label: "Heizen", iconPath: "M12,7A5,5 0 0,1 17,12A5,5 0 0,1 12,17A5,5 0 0,1 7,12A5,5 0 0,1 12,7M12,9A3,3 0 0,0 9,12A3,3 0 0,0 12,15A3,3 0 0,0 15,12A3,3 0 0,0 12,9M12,2L14.39,5.42C13.65,5.15 12.84,5 12,5C11.16,5 10.35,5.15 9.61,5.42L12,2M3.34,7L7.5,6.65C6.9,7.16 6.36,7.78 5.94,8.5C5.5,9.24 5.25,10 5.11,10.79L3.34,7M3.36,17L5.12,13.23C5.26,14 5.53,14.78 5.95,15.5C6.37,16.24 6.91,16.86 7.5,17.37L3.36,17M20.65,7L18.88,10.79C18.74,10 18.47,9.23 18.05,8.5C17.63,7.78 17.1,7.15 16.5,6.64L20.65,7M20.64,17L16.5,17.36C17.09,16.85 17.62,16.22 18.04,15.5C18.46,14.77 18.73,14 18.87,13.21L20.64,17M12,22L9.59,18.56C10.33,18.83 11.14,19 12,19C12.82,19 13.63,18.83 14.37,18.56L12,22Z" },
    { value: "Absenken", label: "Absenken", iconPath: "M17.75,4.09L15.22,6.03L16.13,9.09L13.5,7.28L10.87,9.09L11.78,6.03L9.25,4.09L12.44,4L13.5,1L14.56,4L17.75,4.09M21.25,11L19.61,12.25L20.2,14.23L18.5,13.06L16.8,14.23L17.39,12.25L15.75,11L17.81,10.95L18.5,9L19.19,10.95L21.25,11M18.97,15.95C19.8,15.87 20.69,17.05 20.16,17.8C19.84,18.25 19.5,18.67 19.08,19.07C15.17,23 8.84,23 4.94,19.07C1.03,15.17 1.03,8.83 4.94,4.93C5.34,4.53 5.76,4.17 6.21,3.85C6.96,3.32 8.14,4.21 8.06,5.04C7.79,7.9 8.75,10.87 10.95,13.06C13.14,15.26 16.1,16.22 18.97,15.95M17.33,17.97C14.5,17.81 11.7,16.64 9.53,14.5C7.36,12.31 6.2,9.5 6.04,6.68C3.23,9.82 3.34,14.64 6.35,17.66C9.37,20.67 14.19,20.78 17.33,17.97Z" },
    { value: "1x Heizen", label: "1x Heizen", iconPath: "M18.1 9.3L19.6 6L15.5 5.6C16.1 6.1 16.6 6.7 17 7.5C17.3 8.1 17.5 8.6 17.7 9.2C17.2 9.1 16.6 9 16 9H15.6C14.8 7.2 13.1 6 11 6C8.2 6 6 8.2 6 11C6 13.1 7.2 14.8 9 15.6V16C9 19.9 12.1 23 16 23S23 19.9 23 16C23 12.9 20.9 10.2 18.1 9.3M16 21C13.2 21 11 18.8 11 16S13.2 11 16 11 21 13.2 21 16 18.8 21 16 21M16.5 16.2L19.4 17.9L18.6 19.1L15 17V12H16.5V16.2M11 4C10.2 4 9.4 4.2 8.6 4.4L11 1L13.4 4.4C12.6 4.2 11.8 4 11 4M4.9 14.5C5.3 15.2 5.9 15.9 6.5 16.4L2.4 16L4.2 12.2C4.3 13 4.5 13.8 4.9 14.5M4.1 9.8L2.3 6L6.5 5.7C5.9 6.2 5.4 6.8 4.9 7.5C4.5 8.2 4.2 9 4.1 9.8Z" },
    { value: "1x Absenken", label: "1x Absenken", iconPath: "M17.75,4.09L15.22,6.03L16.13,9.09L13.5,7.28L10.87,9.09L11.78,6.03L9.25,4.09L12.44,4L13.5,1L14.56,4L17.75,4.09M21.25,11L19.61,12.25L20.2,14.23L18.5,13.06L16.8,14.23L17.39,12.25L15.75,11L17.81,10.95L18.5,9L19.19,10.95L21.25,11M18.97,15.95C19.8,15.87 20.69,17.05 20.16,17.8C19.84,18.25 19.5,18.67 19.08,19.07C15.17,23 8.84,23 4.94,19.07C1.03,15.17 1.03,8.83 4.94,4.93C5.34,4.53 5.76,4.17 6.21,3.85C6.96,3.32 8.14,4.21 8.06,5.04C7.79,7.9 8.75,10.87 10.95,13.06C13.14,15.26 16.1,16.22 18.97,15.95M17.33,17.97C14.5,17.81 11.7,16.64 9.53,14.5C7.36,12.31 6.2,9.5 6.04,6.68C3.23,9.82 3.34,14.64 6.35,17.66C9.37,20.67 14.19,20.78 17.33,17.97Z" },
  ]);
  for (const value of PRESETS) {
    p.stateObj = state({}, { preset_mode: value }); p.render(); await settle();
    equal(modus(p).value, value);
    equal(modus(p).options.find(option => option.value === value).iconPath,
      menu.options.find(option => option.value === value).iconPath);
  }
  equal(JSON.stringify([p.hvac.options, p.preset.options]), original);
});
await test("operating status uses the reported label independently of Auto and temperature", async () => {
  const value = state({}, { ...reportedStatus(), temperature: 19 }), before = JSON.stringify(value);
  const p = mount(value); await settle(); patched(p);
  const node = operatingStatus(p);
  equal(node.textContent, "HeizkreisstatusTag/Nacht Abschaltung"); equal(node.title, "Code 4");
  equal(node.getAttribute("role"), "status"); equal(node.getAttribute("aria-live"), "polite");
  equal(node.previousElementSibling.localName, "ha-more-info-control-select-container");
  equal(node.dataset.available, "true"); equal(JSON.stringify(value), before);
});
await test("stale reported status clears its former label and code", async () => {
  const p = mount(state({}, reportedStatus())); await settle();
  p.stateObj = state({}, reportedStatus("Tag/Nacht Abschaltung", 4, false)); p.render(); await settle();
  equal(operatingStatus(p).textContent, "HeizkreisstatusNicht verfügbar");
  equal(operatingStatus(p).title, ""); equal(operatingStatus(p).dataset.available, "false"); patched(p);
});
await test("missing and malformed status fields never invent an operating state", async () => {
  const p = mount(); await settle();
  equal(operatingStatus(p).textContent, "HeizkreisstatusNicht verfügbar");
  for (const attributes of [reportedStatus("", 4), reportedStatus("Heizbetrieb", "1"),
    reportedStatus("Heizbetrieb", 1, "true"), reportedStatus(null, 1), reportedStatus("x".repeat(161), 1)]) {
    p.stateObj = state({}, attributes); p.render(); await settle();
    equal(operatingStatus(p).textContent, "HeizkreisstatusNicht verfügbar"); equal(operatingStatus(p).title, "");
  }
});
await test("fresh unknown numeric status remains explicitly unknown", async () => {
  const p = mount(state({}, reportedStatus("Code 99 (unbekannt)", 99))); await settle();
  equal(operatingStatus(p).textContent, "HeizkreisstatusCode 99 (unbekannt)");
  equal(operatingStatus(p).title, "Code 99"); equal(operatingStatus(p).dataset.available, "true");
});
await test("unavailable or unknown entity state overrides apparently fresh status", async () => {
  const p = mount(state({}, reportedStatus())); await settle();
  for (const unavailable of ["unavailable", "unknown"]) {
    p.stateObj = state({ state: unavailable }, reportedStatus()); p.render(); await settle();
    equal(operatingStatus(p).textContent, "HeizkreisstatusNicht verfügbar");
    equal(operatingStatus(p).title, "");
  }
});
await test("status text renders as text and cannot inject markup", async () => {
  const label = '<img src=x onerror="throw 1">';
  const p = mount(state({}, reportedStatus(label))); await settle();
  equal(operatingStatus(p).querySelector("img"), null);
  equal(operatingStatus(p).querySelector('[data-hargassner-operating-state-value]').textContent, label);
});
await test("status updates without selecting a new mode or duplicating nodes", async () => {
  const p = mount(state({}, reportedStatus())); await settle();
  p.stateObj = state({}, reportedStatus("Heizbetrieb", 1)); p.render(); await settle(); patched(p);
  equal(operatingStatus(p).textContent, "HeizkreisstatusHeizbetrieb"); equal(modus(p).value, "Auto");
  equal(p.shadowRoot.querySelectorAll('[data-hargassner-operating-state]').length, 1);
  const count = updates; await settle(); equal(updates, count); assert(count < 40); equal(serviceRequests, []);
});
await test("unknown preset leaves native menus while independently displaying reported status", async () => {
  const p = mount(state({}, { ...reportedStatus(), preset_mode: "none" })); await settle(); native(p);
  equal(operatingStatus(p).textContent, "HeizkreisstatusTag/Nacht Abschaltung");
});
await test("entity reuse and schema removal cannot retain another circuit status", async () => {
  const p = mount(state({}, reportedStatus())); await settle(); const old = operatingStatus(p);
  p.stateObj = state({ entity_id: "climate.synthetic_second" }, reportedStatus("Heizbetrieb", 1)); p.render(); await settle();
  assert(!old.isConnected); equal(operatingStatus(p).textContent, "HeizkreisstatusHeizbetrieb");
  p.stateObj = state({ entity_id: "climate.unrelated" }, { hargassner_control_schema: undefined }); p.render(); await settle();
  equal(operatingStatus(p), null); equal(controller.status().statusPopups, 0); native(p);
});
await test("replacement native container relocates status and ambiguous layout removes it", async () => {
  const p = mount(state({}, reportedStatus())); await settle(); const old = operatingStatus(p);
  p.render(true); await settle(); assert(!old.isConnected); equal(operatingStatus(p).textContent, "HeizkreisstatusTag/Nacht Abschaltung");
  p.shadowRoot.append(document.createElement("ha-more-info-control-select-container")); await settle();
  equal(operatingStatus(p), null); equal(controller.status().statusPopups, 0);
});
await test("status observer cleans up when a nested popup is removed", async () => {
  const shell = document.createElement("div"); shell.attachShadow({ mode: "open" }); fixtures.append(shell);
  const p = document.createElement("more-info-climate"); p.stateObj = state({}, reportedStatus()); shell.shadowRoot.append(p);
  await settle(); assert(operatingStatus(p)); shell.remove(); await settle();
  equal(operatingStatus(p), null); equal(controller.status().statusPopups, 0);
});
await test("stop removes operating status and restores both native menus", async () => {
  const p = mount(state({}, reportedStatus())); await settle(); controller.stop(); await settle();
  equal(operatingStatus(p), null); equal(controller.status().statusPopups, 0); native(p);
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
await test("older installed resource is stopped before version 1.4.2 starts", async () => {
  controller.stop(); let stopped=0;
  const prior={version:"1.1.0",running:true,stop(){stopped++;this.running=false;}};
  window[Symbol.for("hargassner.thermostat.popup.v1")]=prior;
  controller=installHargassnerThermostatPopup(window); await controller.ready;
  equal(stopped,1); equal(controller.version,"1.4.2");
  const p=mount(); await settle(); patched(p);
});

controller.stop(); window.fetch = nativeFetch;
window.popupTestResults = { passed: results.every(result => result.passed), tests: results.length, results,
  scope: "Actual browser Custom Elements, shadow DOM and MutationObserver; synthetic HA native handlers record service payloads; no HA connection or real services." };
document.querySelector("#results").textContent = JSON.stringify(window.popupTestResults, null, 2);

await import("./test-thermostat-card.js");
window.hargassnerFrontendTests = { type: "hargassner-frontend-tests", version: "1.4.2", popup: window.popupTestResults, card: window.cardTestResults };
if (window.parent !== window) window.parent.postMessage(window.hargassnerFrontendTests, "*");
