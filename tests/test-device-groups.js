import { installHargassnerDeviceGroups } from "../dist/hargassner-thermostat-popup.js";
const fixtures = document.querySelector("#fixtures");
const roots = {
  original: { manufacturer: "Hargassner", model: "Nano-PK", identifiers: [["mqtt", "hargassner_synthetic"]] },
  unrelated: { manufacturer: "Other", model: "Other", identifiers: [["mqtt", "unrelated"]] },
};
const entry = (slug, domain = "sensor", changes = {}) => ({ entity_id: `${domain}.${slug}`, unique_id: `hargassner_synthetic_test_${domain}_${slug}`, platform: "mqtt", device_id: "original", stateName: slug, ...changes });
const sample = () => [entry("aussentemperatur"), entry("solar_quellentemperatur"), entry("heizkreis_2_vorlauftemperatur"),
  entry("kesseltemperatur"), entry("pufferfuehler_oben"), entry("boiler_1_boilertemperatur"),
  entry("heizkreis_1_vorlauftemperatur"), entry("lagerstand"), entry("future_unknown")];
const hass = entries => ({ devices: roots, states: Object.fromEntries(entries.filter(x => !x.disabled_by).map(x => [x.entity_id, { state: "synthetic" }])) });
let nativeActions = [], nativeRenderCalls = [], requestUpdates = 0, fetches = 0;
const originalFetch = window.fetch;
window.fetch = () => { fetches++; throw new Error("unexpected_fetch"); };
class DevicePage extends HTMLElement { constructor() { super(); this.attachShadow({ mode: "open" }); this.deviceId = "original"; } }
customElements.define("ha-config-device-page", DevicePage);
class NativeEntitiesCard extends HTMLElement {
  constructor() { super(); this.attachShadow({ mode: "open" }); this.entities = []; this.showHidden = false; }
  connectedCallback() { this.requestUpdate(); }
  requestUpdate() {
    requestUpdates++;
    const card = document.createElement("ha-card"), header = document.createElement("h2"), list = document.createElement("ha-list");
    header.textContent = this.header || "Sensoren";
    const enabled = this.entities.filter(x => !x.disabled_by), disabled = this.entities.filter(x => x.disabled_by);
    const main = enabled.filter(x => x.main), additional = enabled.filter(x => !x.main);
    const append = value => { if (Array.isArray(value)) value.forEach(append); else if (value instanceof Node) list.append(value); };
    append(this._renderEntities(main)); append(this._renderEntities(additional));
    card.append(header, list);
    if (disabled.length) {
      const toggle = document.createElement("button"); toggle.dataset.nativeDisabled = "";
      toggle.textContent = this.showHidden ? "Weniger anzeigen" : `${disabled.length} deaktiviert`;
      toggle.onclick = () => { this.showHidden = !this.showHidden; this.requestUpdate(); };
      if (this.showHidden) disabled.forEach(x => card.append(this._renderUnavailableEntity(x)));
      card.append(toggle);
    }
    const add = document.createElement("button"); add.dataset.nativeAdd = ""; add.textContent = "Zum Dashboard hinzufügen";
    add.onclick = () => nativeActions.push({ kind: "add", entities: this.entities.filter(x => !x.disabled_by).map(x => x.entity_id) });
    card.append(add); this.shadowRoot.replaceChildren(card);
  }
  _renderEntities(entries) {
    nativeRenderCalls.push({ host: this, entries: [...entries] });
    return entries.map(x => this.hass.states[x.entity_id] ? this._renderEntity(x) : this._renderUnavailableEntity(x));
  }
  _renderEntity(value) {
    const wrapper = document.createElement("div"), row = document.createElement("hui-synthetic-entity-row");
    row.dataset.nativeEntity = value.entity_id; row.entry = value; row.textContent = value.stateName;
    row.onclick = () => nativeActions.push({ kind: "row", entity: value.entity_id });
    wrapper.append(row); return wrapper;
  }
  _renderUnavailableEntity(value) {
    const row = document.createElement("ha-list-item"); row.dataset.nativeEntity = value.entity_id; row.entry = value;
    row.textContent = value.stateName; row.onclick = () => nativeActions.push({ kind: "edit", entity: value.entity_id });return row;
  }
}
customElements.define("ha-device-entities-card", NativeEntitiesCard);
const originalRenderer = NativeEntitiesCard.prototype._renderEntities;
const assert = (condition, message = "Assertion failed") => { if (!condition) throw new Error(message); };
const equal = (a, b) => assert(JSON.stringify(a) === JSON.stringify(b), `${JSON.stringify(a)} != ${JSON.stringify(b)}`);
const settle = async () => { for (let i = 0; i < 2; i++) { await Promise.resolve(); await new Promise(resolve => {
  const c = new MessageChannel(); c.port1.onmessage = () => { c.port1.close(); c.port2.close(); resolve(); }; c.port2.postMessage(null);
}); } };
let controller;
function mount(entries = sample(), options = {}) {
  const page = document.createElement("ha-config-device-page"); page.deviceId = options.deviceId || "original";
  const card = document.createElement("ha-device-entities-card"); card.entities = entries; card.hass = hass(entries); card.deviceName = "Hargassner Nano-PK";
  if (options.devices) card.hass.devices = options.devices;
  page.shadowRoot.append(card); fixtures.append(page); return { page, card };
}
const headings = card => [...card.shadowRoot.querySelectorAll("[data-hargassner-device-group]")].map(x => x.textContent);
const rows = card => [...card.shadowRoot.querySelectorAll("[data-native-entity]")];
const ids = card => rows(card).map(x => x.dataset.nativeEntity);
const results = [];
async function test(name, run) {
  window.hargassnerDeviceGroups?.stop(); fixtures.replaceChildren(); nativeActions = []; nativeRenderCalls = []; requestUpdates = 0; fetches = 0;
  controller = installHargassnerDeviceGroups(window); await controller.ready; await settle();
  try { await run(); equal(fetches, 0); results.push({ name, passed: true }); }
  catch (error) { results.push({ name, passed: false, error: error.message }); }
}
await test("one existing device gains logical sections with exact native entity multiset", async () => {
  const values = sample(), before = JSON.stringify(values), { card } = mount(values); await settle();
  equal(headings(card), ["Heizkreis 1", "Heizkreis 2", "Puffer", "Kessel", "Solar", "Warmwasser", "Pelletslager", "Steuerung"]);
  equal([...ids(card)].sort(), values.map(x => x.entity_id).sort()); equal(JSON.stringify(values), before);
  equal(new Set(card.entities.map(x => x.device_id)).size, 1); equal(nativeActions, []);
});
await test("native renderer receives original entry objects and host context", async () => {
  const values = sample(), { card } = mount(values); await settle();
  const calls = nativeRenderCalls.filter(x => x.host === card).flatMap(x => x.entries);
  for (const value of values) assert(calls.includes(value));
  for (const row of rows(card)) assert(values.includes(row.entry)); equal(nativeActions, []);
});
await test("all90 immutable discovery identities map without relying on current entity IDs", async () => {
  const mapping = await import("./device-group-fixture-data.js");
  const values = Object.entries(mapping.ENTITY_GROUPS).map(([id, group], index) => {
    const [domain, slug] = id.split("."); return entry(slug, domain, { entity_id: `${domain}.renamed_${index}`, expected: group });
  });
  const { card } = mount(values); await settle(); equal(rows(card).length, 90);
  let group;
  for (const node of card.shadowRoot.querySelector("ha-list").children) {
    if (node.dataset.hargassnerDeviceGroup) group = node.dataset.hargassnerDeviceGroup;
    else {
      const row = node.matches("[data-native-entity]") ? node : node.querySelector("[data-native-entity]");
      if (row) equal(row.entry.expected, group);
    }
  }
});
await test("renamed and suffixed entities retain immutable identity classification", async () => {
  const values = [entry("aussentemperatur", "sensor", { entity_id: "sensor.aussentemperatur_2" }), entry("heizkreis_1_tagtemperatur", "number", { entity_id: "number.custom_room_day" })];
  const { card } = mount(values); await settle(); equal(headings(card), ["Heizkreis 1", "Steuerung"]);
  equal(ids(card), ["number.custom_room_day", "sensor.aussentemperatur_2"]);
});
await test("unknown root entities remain visible in Steuerung", async () => {
  const value = entry("future_unknown"), { card } = mount([value]); await settle();
  equal(headings(card), ["Steuerung"]); equal(ids(card), [value.entity_id]);
});
await test("native row and add-to-dashboard handlers remain unchanged", async () => {
  const values = sample(), { card } = mount(values); await settle(); rows(card)[0].click();
  card.shadowRoot.querySelector("[data-native-add]").click();
  equal(nativeActions, [{ kind: "row", entity: rows(card)[0].dataset.nativeEntity }, { kind: "add", entities: values.map(x => x.entity_id) }]);
});
await test("missing state uses original native unavailable-row handler", async () => {
  const values = sample(), { card } = mount(values); delete card.hass.states[values[0].entity_id]; card.requestUpdate(); await settle();
  const row = rows(card).find(x => x.entry === values[0]); equal(row.localName, "ha-list-item"); row.click();
  equal(nativeActions, [{ kind: "edit", entity: values[0].entity_id }]);
});
await test("native disabled expansion and dashboard actions stay native", async () => {
  const values = [entry("kesseltemperatur"), entry("heizkreis_1_vorlauftemperatur", "sensor", { disabled_by: "user" })];
  const { card } = mount(values); await settle(); equal(ids(card), [values[0].entity_id]);
  card.shadowRoot.querySelector("[data-native-disabled]").click(); await settle(); equal(rows(card).length, 2);
  rows(card).find(x => x.entry === values[1]).click(); equal(nativeActions, [{ kind: "edit", entity: values[1].entity_id }]);
  card.shadowRoot.querySelector("[data-native-disabled]").click(); await settle(); equal(rows(card).length, 1);
});
for (const [name, change] of [
  ["different manufacturer", values => ({ devices: { ...roots, original: { ...roots.original, manufacturer: "Other" } } })],
  ["different model", values => ({ devices: { ...roots, original: { ...roots.original, model: "Other" } } })],
  ["missing MQTT root identifier", values => ({ devices: { ...roots, original: { ...roots.original, identifiers: [] } } })],
  ["different active device page", values => ({ deviceId: "unrelated" })],
]) await test(`${name} keeps native rows ungrouped`, async () => {
  const values = sample(), { card } = mount(values, change(values)); await settle(); equal(headings(card), []); equal(ids(card), values.map(x => x.entity_id));
});
await test("non MQTT or mixed-device lists keep native rendering", async () => {
  for (const changes of [{ platform: "other" }, { device_id: "unrelated" }, { unique_id: "other" }]) {
    const values = sample(); Object.assign(values[0], changes); const { card, page } = mount(values); await settle();
    equal(headings(card), []); equal(ids(card), values.map(x => x.entity_id)); page.remove();
  }
});
await test("same native card reused for another device removes sections", async () => {
  const { card, page } = mount(); await settle(); assert(headings(card).length);
  card.entities = sample().map(x => ({ ...x, device_id: "unrelated" })); card.hass = hass(card.entities); page.deviceId = "unrelated";
  card.requestUpdate(); await settle(); equal(headings(card), []); equal(controller.status().activeCards, 0);
});
await test("state updates do not duplicate headings, rows or perform actions", async () => {
  const { card } = mount(); await settle(); const before = ids(card);
  for (let i = 0; i < 20; i++) card.requestUpdate(); await settle();
  equal(ids(card), before); equal(headings(card).length, 8); equal(nativeActions, []); equal(controller.status().activeCards, 1);
});
await test("empty main lists preserve native call and nonempty additional groups", async () => {
  const { card } = mount(); await settle(); assert(nativeRenderCalls.some(x => x.host === card && x.entries.length === 0)); equal(rows(card).length, sample().length);
});
await test("separate native main and additional lists retain all rows", async () => {
  const values = sample(); values[0].main = true; values[6].main = true;
  const { card } = mount(values); await settle(); equal([...ids(card)].sort(), values.map(x => x.entity_id).sort()); equal(nativeActions, []);
});
await test("removing a nested ancestor releases scoped observers and references", async () => {
  const shell = document.createElement("section"), { page, card } = mount(); shell.append(page); fixtures.append(shell); await settle();
  equal(controller.status().activeCards, 1); shell.remove(); await settle(); equal(controller.status().activeCards, 0); equal(nativeActions, []);
});
await test("stop restores the exact renderer and clears connected headings", async () => {
  const { card } = mount(); await settle(); controller.stop(); await settle();
  assert(NativeEntitiesCard.prototype._renderEntities === originalRenderer); equal(headings(card), []); equal(controller.status().activeCards, 0); equal(nativeActions, []);
});
await test("late installation redraws an already connected device card", async () => {
  controller.stop(); const { card } = mount(); await settle(); equal(headings(card), []);
  controller = installHargassnerDeviceGroups(window); await controller.ready; await settle(); assert(headings(card).length); equal(nativeActions, []);
});
await test("unsupported native renderer contract remains untouched", async () => {
  controller.stop();
  const descriptor = Object.getOwnPropertyDescriptor(NativeEntitiesCard.prototype, "_renderEntities");
  const different = function (entries, extra) { return originalRenderer.call(this, entries); };
  Object.defineProperty(NativeEntitiesCard.prototype, "_renderEntities", { ...descriptor, value: different });
  try {
    controller = installHargassnerDeviceGroups(window); await controller.ready;
    const values = sample(), { card } = mount(values); await settle();
    assert(NativeEntitiesCard.prototype._renderEntities === different);
    equal(headings(card), []); equal(ids(card), values.map(x => x.entity_id)); equal(nativeActions, []);
  } finally {
    controller.stop(); Object.defineProperty(NativeEntitiesCard.prototype, "_renderEntities", descriptor);
  }
});
await test("duplicate installation is idempotent and pagehide restores native UI", async () => {
  const { card } = mount(); await settle(); assert(installHargassnerDeviceGroups(window) === controller);
  window.dispatchEvent(new Event("pagehide")); await settle(); equal(headings(card), []); equal(controller.running, false); equal(controller.status().activeCards, 0);
});
controller.stop(); window.fetch = originalFetch;
window.deviceGroupTestResults = { passed: results.every(x => x.passed), tests: results.length, results,
  scope: "Browser Custom Elements/shadow DOM/scoped MutationObserver with synthetic original native renderers/actions; no HA connections or registry/service writes." };
document.querySelector("#results").textContent = JSON.stringify(window.deviceGroupTestResults, null, 2);

if (window.parent !== window) window.parent.postMessage({
  type: "hargassner-device-group-tests", version: "1.4.3", groups: window.deviceGroupTestResults,
}, "*");
