/* Maintained bounded installed-native diagnostic probe. Original progressive
 * source and scoped receipts remain under integration/ha_optimistic_browser/.
 * Uses isolated synthetic contexts/services; requires already-loaded native HA. */
function startHargassnerNativeProbeV2(options = {}) {
  const namespace = "hargassnerNativeProbeV2", version = "1.4.3";
  if (window[namespace]?.running) throw Error("An owned probe is already running; stop it first");
  if (options.installedVersion !== version || !/^[a-f0-9]{64}$/.test(options.verifiedResourceSha256 || ""))
    throw Error("Verified installed version/resource hash required");
  const controller = window.hargassnerThermostatPopup;
  if (controller?.version !== version || controller.running !== true) throw Error("Installed controller mismatch");
  const kind = options.kind || "native";
  if (!["native", "stock", "card", "popup"].includes(kind)) throw Error("Unknown single-component probe kind");
  const gesture = options.gesture || "slider";
  if (!["slider", "debounced"].includes(gesture)) throw Error("Unknown synthetic gesture path");
  const tag = { native: "ha-state-control-climate-temperature", stock: "hui-thermostat-card",
    card: "hargassner-thermostat-card", popup: "more-info-climate" }[kind];
  if (!customElements.get(tag)) throw Error("Requested native class must already be loaded");
  const timeoutMs = Number.isInteger(options.timeoutMs) && options.timeoutMs >= 200 && options.timeoutMs <= 8000 ? options.timeoutMs : 6000;
  const mark = Symbol("hargassner.synthetic.api.v2"), id = "climate.harg_progressive_fixture", basis = "a".repeat(32), nextBasis = "b".repeat(32);
  const started = Date.now(), timers = new Set(), observed = new WeakSet(), contextCounts = Object.create(null), subscriptions = new Map();
  let wrapper, host, control, native, initialControl, initialNative, observer, finished = false, step = 0;
  const report = { schemaVersion: 2, version, resourceSha256: options.verifiedResourceSha256, kind, gesture,
    exercise: options.exercise === true, running: true, status: "starting", stage: "preflight", progress: [],
    contexts: {}, mutations: 0, fakeServiceCalls: [], deniedPaths: [], realServiceCalls: 0, syntheticOnly: true,
    limitation: "Timer watchdog cannot preempt JavaScript main-thread or microtask starvation." };
  function progress(stage, details = {}) {
    report.stage = stage;
    if (report.progress.length < 64) report.progress.push({ ms: Date.now() - started, stage, ...details });
  }
  function cleanup() {
    for (const timer of timers) clearTimeout(timer); timers.clear();
    subscriptions.clear();
    observer?.disconnect(); observer = null;
    try {
      if (control?.stateObj?.entity_id === id && control._api?.[mark] === true)
        control._debouncedCallService?.cancel?.();
    } catch { /* Only this owned synthetic control may be cancelled. */ }
    try { host?.remove(); } catch { /* Owned detached component only. */ }
    try { wrapper?.remove(); } catch { /* Owned wrapper only. */ }
    host = control = native = initialControl = initialNative = wrapper = null;
  }
  function finish(status, reason) {
    if (finished) return;
    finished = true; report.running = false; report.status = status;
    if (reason) report.reason = String(reason).slice(0, 180);
    progress("cleanup", { result: status }); cleanup(); report.stage = "finished";
  }
  function later(fn, ms) {
    const timer = setTimeout(() => { timers.delete(timer); if (finished) return;
      try { fn(); } catch (error) { finish("failed", error.message || error); }
    }, ms); timers.add(timer); return timer;
  }
  report.stop = () => finish("stopped", "Explicit operator stop");
  window[namespace] = report;
  const denied = name => () => { if (report.deniedPaths.length < 16) report.deniedPaths.push(name); finish("blocked", "Unexpected API path: " + name); return Promise.resolve(); };
  const config = { unit_system: { temperature: "°C", length: "km", mass: "kg", pressure: "hPa", volume: "L", wind_speed: "km/h" },
    time_zone: "UTC", latitude: 0, longitude: 0, elevation: 0, components: ["climate"], state: "RUNNING", version: "2026.9.3" };
  const locale = { language: "de", number_format: "language", time_format: "24", date_format: "language", time_zone: "local", first_weekday: "monday" };
  const localize = key => String(key).split(".").at(-1);
  const formatters = { formatNumber: value => String(value), formatEntityName: state => state?.attributes?.friendly_name || "Fixture",
    formatEntityState: state => String(state?.state || ""), formatEntityAttributeName: (_state, key) => key,
    formatEntityAttributeValue: (state, key) => String(state?.attributes?.[key] ?? "") };
  formatters.formatEntityStateToParts = state => [{ type: "literal", value: formatters.formatEntityState(state) }];
  formatters.formatEntityAttributeValueToParts = (state, key) => [{ type: "literal", value: formatters.formatEntityAttributeValue(state, key) }];
  const connection = { connected: true };
  for (const key of ["sendMessage", "sendMessagePromise", "subscribeMessage", "subscribeEvents"]) connection[key] = denied("connection." + key);
  const fake = { [mark]: true, config, locale, localize, language: "de", ...formatters, connection, connected: true,
    user: { id: "progressive-synthetic", name: "Fixture", is_admin: false }, entities: {}, devices: {}, areas: {}, panels: {},
    services: { climate: {} }, themes: { darkMode: false, themes: {}, default_theme: "default" }, selectedTheme: null,
    callService: (domain, service, data) => {
      if (domain !== "climate" || service !== "set_temperature" || data?.entity_id !== id ||
          !Number.isFinite(data.temperature) || Object.keys(data).some(key => !["entity_id", "temperature"].includes(key))) {
        finish("blocked", "Unexpected synthetic command shape"); return Promise.resolve();
      }
      if (report.fakeServiceCalls.length >= 4) { finish("blocked", "Synthetic call budget exceeded"); return Promise.resolve(); }
      report.fakeServiceCalls.push({ service, temperature: data.temperature }); return Promise.resolve();
    } };
  for (const key of ["callWS", "callApi", "callApiRaw", "fetchWithAuth", "sendWS", "sendMessage", "refreshCurrentUser"]) fake[key] = denied("hass." + key);
  const i18n = { language: "de", locale, localize, translationMetadata: {},
    loadBackendTranslation: async () => {}, loadFragmentTranslation: async () => {} };
  const contextValues = {
    hassApi: fake, hass: fake, hassConfig: { auth: null, config, user: fake.user, userData: {}, systemData: {} },
    hassInternationalization: i18n, hassFormatters: formatters,
    hassConnection: { connection, connected: true, debugConnection: false }, connection,
    hassUi: { themes: fake.themes, selectedTheme: null, panels: {}, suspendWhenHidden: false },
    hassRegistries: { entities: {}, devices: {}, areas: {}, floors: {} }, entities: {}, devices: {}, areas: {}, floors: {},
    services: fake.services, narrowViewport: false,
  };
  function state(temperature = 22, seq = 0, status = "pending", qualified = true) {
    const activeBasis = temperature === 22.5 && qualified ? nextBasis : basis;
    const records = seq ? [{ sequence: seq, basis, target: 22.5, status, reason: null, origin: "ha_target",
      startedAt: "2026-10-15T04:00:00Z", updatedAt: "2026-10-15T04:00:01Z" }] : [];
    return { entity_id: id, state: "auto", last_changed: "2026-10-15T04:00:00Z", last_updated: "2026-10-15T04:00:01Z",
      attributes: { friendly_name: "Progressive synthetic Heizkreis", hargassner_control_schema: "configured_room_setpoint_v1",
        supported_features: qualified ? 385 : 384, hvac_modes: ["off", "auto", "heat"],
        preset_modes: ["none", "Aus", "Auto", "Heizen", "Absenken", "1x Heizen", "1x Absenken"], preset_mode: "Auto",
        temperature: qualified ? temperature : null, current_temperature: 20, min_temp: 19, max_temp: 25, target_temp_step: .5,
        "Zieltemperatur verfügbar": qualified, Sollwertbasis: qualified ? activeBasis : null,
        Sollwertphase: qualified ? "day" : "unbekannt", Sollwertparameter: qualified ? "4" : null,
        Raumkorrektur: temperature === 22.5 ? .5 : 0, Heizkreisstatus: "Heizbetrieb", "Heizkreisstatus-Code": 1, "Heizkreisstatus verfügbar": true,
        Temperaturauftrag: { schemaVersion: 1, temperatureUnit: "°C", context: "c".repeat(32), sequence: seq,
          revision: step + 1, records, confirmedTarget: qualified ? temperature : null, confirmedBasis: qualified ? activeBasis : null, confirmedPreset: "Auto" } } };
  }
  function update(value) {
    fake.states = { [id]: value };
    const hass = { ...fake, states: { [id]: value } };
    host.hass = hass;
    if (kind === "native") { host.stateObj = value; host._api = hass; }
    if (kind === "popup") { host.stateObj = value; host._api = hass; }
    for (const callback of subscriptions.get("states") || []) callback(fake.states, () => subscriptions.get("states")?.delete(callback));
  }
  function descendants(root, selector, depth = 0, budget = { count: 0 }) {
    if (!root || depth > 10 || budget.count > 2000) return [];
    const rows = [...root.querySelectorAll(selector)];
    for (const node of root.querySelectorAll("*")) {
      if (++budget.count > 2000) break;
      if (node.shadowRoot) rows.push(...descendants(node.shadowRoot, selector, depth + 1, budget));
    }
    return rows;
  }
  function inspect() {
    const controls = kind === "native" ? [host] : descendants(host.shadowRoot || host, "ha-state-control-climate-temperature");
    if (controls.length !== 1) return false;
    control = controls[0]; native = kind === "card" ? descendants(host.shadowRoot, "hui-thermostat-card")[0] : host;
    if (!control.shadowRoot || !descendants(control.shadowRoot, "ha-control-circular-slider").length ||
        !Number.isFinite(control._targetTemperature?.value)) return false;
    if (kind === "card" || kind === "popup") {
      if (typeof Object.getOwnPropertyDescriptor(control, "_api")?.get !== "function") return false;
    }
    if (control._api?.[mark] !== true || control.stateObj?.entity_id !== id) { finish("blocked", "Native API/state is not provably synthetic"); return false; }
    for (const root of [host.shadowRoot, native?.shadowRoot, control.shadowRoot].filter(Boolean)) if (!observed.has(root)) {
      observed.add(root); observer.observe(root, { childList: true, subtree: true, attributes: true });
    }
    return true;
  }
  function watchOwnedRoots(root = wrapper, depth = 0, budget = { count: 0 }) {
    if (!root || finished || depth > 10 || budget.count > 2000) return;
    if (!observed.has(root)) { observed.add(root); observer.observe(root, { childList: true, subtree: true, attributes: true }); }
    for (const node of root.querySelectorAll("*")) {
      if (++budget.count > 2000) break;
      if (node.shadowRoot) watchOwnedRoots(node.shadowRoot, depth + 1, budget);
    }
  }
  function assertPresentation(expected) {
    if (!inspect()) throw Error("Native control is not ready");
    if (control !== initialControl || native !== initialNative) throw Error("Native control/card identity changed");
    if (control._targetTemperature.value !== expected) throw Error("Native projected number differs from expected synthetic target");
  }
  function presentationStep(stage, value, next) {
    progress(stage); step++; update(value());
    const revision = step + 1, deadline = Math.min(started + timeoutMs, Date.now() + 1200);
    function poll() {
      const ready = inspect() && control.stateObj?.attributes?.Temperaturauftrag?.revision === revision;
      if (finished) return;
      if (ready) { assertPresentation(22.5); next(); return; }
      if (Date.now() >= deadline) { finish("timeout", "Native presentation did not receive the bounded synthetic revision"); return; }
      later(poll, 25);
    }
    later(poll, 25);
  }
  function afterFakeReceipt() {
    if (report.fakeServiceCalls.length !== 1 || report.fakeServiceCalls[0].temperature !== 22.5) throw Error("Expected exactly one fake native call");
    progress("fake_receipt", { temperature: 22.5 });
    if (kind === "native" || kind === "stock") { finish("passed", "Native gesture reached only fake API"); return; }
    assertPresentation(22.5);
    presentationStep("old_report", () => state(22), () => {
      presentationStep("pending_readback_gap", () => state(22, 1, "pending", false), () => {
        const count = report.fakeServiceCalls.length;
        control._callService("value");
        if (report.fakeServiceCalls.length !== count) throw Error("Readback gap authorized a fake call");
        presentationStep("qualified_confirmation", () => state(22.5, 1, "confirmed", true), () => {
          if (report.fakeServiceCalls.length !== 1) throw Error("Projection generated extra calls");
          finish("passed", "One component completed bounded intent/readback checks");
        });
      });
    });
  }
  function pollReady() {
    if (!inspect()) { if (!finished) later(pollReady, 50); return; }
    initialControl = control; initialNative = native;
    progress("mounted", { temperature: control._targetTemperature.value, syntheticApi: true });
    if (options.exercise !== true) { finish("passed", "Single-component mount-only check"); return; }
    progress("synthetic_gesture", { gesture });
    if (gesture === "debounced") {
      if (typeof control._debouncedCallService !== "function" || typeof control._debouncedCallService.cancel !== "function")
        throw Error("Native cancellable debounce unavailable");
      control._targetTemperature = { ...control._targetTemperature, value: 22.5 };
      control._debouncedCallService("value");
    } else {
      const slider = descendants(control.shadowRoot, "ha-control-circular-slider")[0];
      slider.dispatchEvent(new CustomEvent("value-changed", { detail: { value: 22.5 }, bubbles: true, composed: true }));
    }
    const receiptDeadline = Math.min(started + timeoutMs, Date.now() + 1800);
    progress("waiting_fake_receipt");
    function pollReceipt() {
      if (report.fakeServiceCalls.length) { afterFakeReceipt(); return; }
      if (Date.now() >= receiptDeadline) { finish("timeout", "No fake native receipt within bounded debounce allowance"); return; }
      later(pollReceipt, 25);
    }
    later(pollReceipt, 0);
  }
  try {
    observer = new MutationObserver(records => {
      report.mutations += records.length;
      if (report.mutations > 500) finish("blocked", "Owned subtree mutation budget exceeded");
      else watchOwnedRoots();
    });
    wrapper = document.createElement("div"); wrapper.dataset.hargassnerProgressiveFixture = version;
    Object.assign(wrapper.style, { position: "fixed", left: "-10000px", top: "0", width: "440px", opacity: "0", pointerEvents: "none" });
    wrapper.addEventListener("context-request", event => {
      event.stopPropagation(); const name = String(event.context);
      if (!(name in contextCounts) && Object.keys(contextCounts).length >= 32) { finish("blocked", "Context name budget exceeded"); return; }
      contextCounts[name] = (contextCounts[name] || 0) + 1; report.contexts = { ...contextCounts };
      if (contextCounts[name] > 64) { finish("blocked", "Repeated context request budget exceeded"); return; }
      if (typeof event.callback !== "function" || finished) return;
      // Exact primary names/types from src/data/context/index.ts. In
      // particular _config.config.unit_system and hassInternationalization.
      const value = name === "states" ? fake.states : Object.hasOwn(contextValues, name) ? contextValues[name] : undefined;
      if (value === undefined) { finish("blocked", "Unsupported synthetic context: " + name); return; }
      const callback = event.callback;
      if (event.subscribe) {
        if (!subscriptions.has(name)) subscriptions.set(name, new Set());
        subscriptions.get(name).add(callback);
      }
      callback(value, () => subscriptions.get(name)?.delete(callback));
    });
    progress("watchdog_armed"); later(() => finish("timeout", "Single-component readiness/step deadline exceeded"), timeoutMs);
    host = document.createElement(tag);
    if (kind === "stock" || kind === "card") host.setConfig({ type: kind === "stock" ? "thermostat" : "custom:hargassner-thermostat-card", entity: id, features: [] });
    update(state()); progress("props_assigned"); wrapper.append(host); document.body.append(wrapper);
    if (!finished) { watchOwnedRoots(); progress("attached"); later(pollReady, 50); }
  } catch (error) { finish("failed", error.message || error); }
  return { namespace, running: report.running, stage: report.stage, kind, syntheticOnly: true };
}
