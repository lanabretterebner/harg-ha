/* Hargassner thermostat popup and dashboard card, v1.4.2.
 * One native-styled Modus menu replaces both native HVAC/preset menus.
 * Only an explicit user selection forwards a manufacturer mode to HA's
 * existing preset handler. Temperature gestures use guarded native climate.set_temperature only.
 * See POPUP-INSTALL.md for the supported HA DOM contract and manual install.
 */
const VERSION = "1.4.2";
const SCHEMA = "configured_room_setpoint_v1";
const PRESETS = Object.freeze(["Aus", "Auto", "Heizen", "Absenken", "1x Heizen", "1x Absenken"]);
// Original SVG paths from @mdi/js 7.4.47, icon data Apache-2.0.
// Embedded native iconPath options render without runtime icon downloads.
// https://www.npmjs.com/package/@mdi/js/v/7.4.47
const ICON_PATHS = Object.freeze({
  "mdiPower": "M16.56,5.44L15.11,6.89C16.84,7.94 18,9.83 18,12A6,6 0 0,1 12,18A6,6 0 0,1 6,12C6,9.83 7.16,7.94 8.88,6.88L7.44,5.44C5.36,6.88 4,9.28 4,12A8,8 0 0,0 12,20A8,8 0 0,0 20,12C20,9.28 18.64,6.88 16.56,5.44M13,3H11V13H13",
  "mdiCalendarClock": "M15,13H16.5V15.82L18.94,17.23L18.19,18.53L15,16.69V13M19,8H5V19H9.67C9.24,18.09 9,17.07 9,16A7,7 0 0,1 16,9C17.07,9 18.09,9.24 19,9.67V8M5,21C3.89,21 3,20.1 3,19V5C3,3.89 3.89,3 5,3H6V1H8V3H16V1H18V3H19A2,2 0 0,1 21,5V11.1C22.24,12.36 23,14.09 23,16A7,7 0 0,1 16,23C14.09,23 12.36,22.24 11.1,21H5M16,11.15A4.85,4.85 0 0,0 11.15,16C11.15,18.68 13.32,20.85 16,20.85A4.85,4.85 0 0,0 20.85,16C20.85,13.32 18.68,11.15 16,11.15Z",
  "mdiWeatherSunny": "M12,7A5,5 0 0,1 17,12A5,5 0 0,1 12,17A5,5 0 0,1 7,12A5,5 0 0,1 12,7M12,9A3,3 0 0,0 9,12A3,3 0 0,0 12,15A3,3 0 0,0 15,12A3,3 0 0,0 12,9M12,2L14.39,5.42C13.65,5.15 12.84,5 12,5C11.16,5 10.35,5.15 9.61,5.42L12,2M3.34,7L7.5,6.65C6.9,7.16 6.36,7.78 5.94,8.5C5.5,9.24 5.25,10 5.11,10.79L3.34,7M3.36,17L5.12,13.23C5.26,14 5.53,14.78 5.95,15.5C6.37,16.24 6.91,16.86 7.5,17.37L3.36,17M20.65,7L18.88,10.79C18.74,10 18.47,9.23 18.05,8.5C17.63,7.78 17.1,7.15 16.5,6.64L20.65,7M20.64,17L16.5,17.36C17.09,16.85 17.62,16.22 18.04,15.5C18.46,14.77 18.73,14 18.87,13.21L20.64,17M12,22L9.59,18.56C10.33,18.83 11.14,19 12,19C12.82,19 13.63,18.83 14.37,18.56L12,22Z",
  "mdiWeatherNight": "M17.75,4.09L15.22,6.03L16.13,9.09L13.5,7.28L10.87,9.09L11.78,6.03L9.25,4.09L12.44,4L13.5,1L14.56,4L17.75,4.09M21.25,11L19.61,12.25L20.2,14.23L18.5,13.06L16.8,14.23L17.39,12.25L15.75,11L17.81,10.95L18.5,9L19.19,10.95L21.25,11M18.97,15.95C19.8,15.87 20.69,17.05 20.16,17.8C19.84,18.25 19.5,18.67 19.08,19.07C15.17,23 8.84,23 4.94,19.07C1.03,15.17 1.03,8.83 4.94,4.93C5.34,4.53 5.76,4.17 6.21,3.85C6.96,3.32 8.14,4.21 8.06,5.04C7.79,7.9 8.75,10.87 10.95,13.06C13.14,15.26 16.1,16.22 18.97,15.95M17.33,17.97C14.5,17.81 11.7,16.64 9.53,14.5C7.36,12.31 6.2,9.5 6.04,6.68C3.23,9.82 3.34,14.64 6.35,17.66C9.37,20.67 14.19,20.78 17.33,17.97Z",
  "mdiSunClock": "M18.1 9.3L19.6 6L15.5 5.6C16.1 6.1 16.6 6.7 17 7.5C17.3 8.1 17.5 8.6 17.7 9.2C17.2 9.1 16.6 9 16 9H15.6C14.8 7.2 13.1 6 11 6C8.2 6 6 8.2 6 11C6 13.1 7.2 14.8 9 15.6V16C9 19.9 12.1 23 16 23S23 19.9 23 16C23 12.9 20.9 10.2 18.1 9.3M16 21C13.2 21 11 18.8 11 16S13.2 11 16 11 21 13.2 21 16 18.8 21 16 21M16.5 16.2L19.4 17.9L18.6 19.1L15 17V12H16.5V16.2M11 4C10.2 4 9.4 4.2 8.6 4.4L11 1L13.4 4.4C12.6 4.2 11.8 4 11 4M4.9 14.5C5.3 15.2 5.9 15.9 6.5 16.4L2.4 16L4.2 12.2C4.3 13 4.5 13.8 4.9 14.5M4.1 9.8L2.3 6L6.5 5.7C5.9 6.2 5.4 6.8 4.9 7.5C4.5 8.2 4.2 9 4.1 9.8Z"
});
const MODE_ICONS = Object.freeze({
  "Aus": "mdiPower",
  "Auto": "mdiCalendarClock",
  "Heizen": "mdiWeatherSunny",
  "Absenken": "mdiWeatherNight",
  "1x Heizen": "mdiSunClock",
  "1x Absenken": "mdiWeatherNight"
});
const HVAC = Object.freeze(["off", "heat", "auto"]);
const KEY = Symbol.for("hargassner.thermostat.popup.v1");
const LABEL = "Modus";

function uniqueStrings(values, limit = 16) {
  return Array.isArray(values) && values.length > 0 && values.length <= limit &&
    values.every(value => typeof value === "string") && new Set(values).size === values.length;
}
function sameValues(left, right) {
  return uniqueStrings(left) && uniqueStrings(right) && left.length === right.length &&
    left.every(value => right.includes(value));
}
function optionValues(menu) {
  if (!Array.isArray(menu.options) || menu.options.length > 16) return null;
  const values = menu.options.map(option => option && typeof option === "object" ? option.value : null);
  return uniqueStrings(values) ? values : null;
}
function eligible(host) {
  const state = host.stateObj, attributes = state?.attributes;
  if (!state || typeof state.entity_id !== "string" || !state.entity_id.startsWith("climate.") ||
      attributes?.hargassner_control_schema !== SCHEMA ||
      !sameValues(attributes.hvac_modes, HVAC) ||
      !uniqueStrings(attributes.preset_modes) || !PRESETS.includes(attributes.preset_mode)) return null;
  const real = attributes.preset_modes.filter(value => value !== "none");
  if (!sameValues(real, PRESETS)) return null;
  return { state, attributes };
}

export function installHargassnerThermostatPopup(environment = window) {
  const prior = environment[KEY];
  if (prior?.running && prior.version === VERSION) return prior;
  if (prior?.running) prior.stop();
  const { document, customElements, MutationObserver } = environment;
  if (!document || !customElements || !MutationObserver) return null;
  const records = new Map();
  const statusRecords = new Map();
  const queued = new Set();
  let running = true, prototype, originalDescriptor, wrapped;

  // Presentation records are scoped to one native popup child. The real
  // more-info stateObj and Home Assistant states are never replaced.
  const targetRecords = new Map();
  const targetCallShims = new WeakMap();
  function popupHass(host) {
    let node = host;
    for (let depth = 0; node && depth < 64; depth++) {
      if (node.hass?.states && typeof node.hass.callService === "function") return node.hass;
      node = node.parentNode || node.host;
    }
    return document.querySelector("home-assistant")?.hass;
  }
  function popupConnected(host) {
    const hass = popupHass(host);
    return !!hass && hass.connected !== false && hass.connection?.connected !== false;
  }
  function popupMode(state) {
    const preset = state?.attributes?.preset_mode;
    return PRESETS.includes(preset) && preset !== "Aus" &&
      state.state === (preset === "Auto" ? "auto" : "heat");
  }
  function popupTarget(state) {
    return !!eligible({ stateObj: state }) && popupMode(state) && validTarget(state) &&
      Number.isSafeInteger(state.attributes.supported_features) && state.attributes.supported_features >= 0 &&
      (state.attributes.supported_features & 1) !== 0;
  }
  function popupMarker(state, previous) {
    const marker = state?.attributes?.Sollwertaktualisierung;
    if (!marker || typeof marker !== "object" || Array.isArray(marker) ||
        marker.basis !== previous.basis || typeof marker.id !== "string" ||
        !/^[A-Za-z0-9_-]{1,128}$/.test(marker.id) ||
        !["pending", "readback"].includes(marker.phase)) return null;
    const keys = Object.keys(marker).sort().join(",");
    if (keys === "basis,id,phase") return state.state === previous.state.state &&
      state.attributes.preset_mode === previous.state.attributes.preset_mode ? marker : null;
    return keys === "basis,id,nextPreset,phase,previousPreset,reason,version" && marker.version === 2 &&
      ["mode_report", "parameter_report", "phase_report", "pending_change"].includes(marker.reason) &&
      marker.previousPreset === previous.state.attributes.preset_mode &&
      marker.nextPreset === state.attributes.preset_mode && popupMode(previous.state) && popupMode(state)
      ? marker : null;
  }
  function descriptor(node, key) {
    for (let current = node; current; current = Object.getPrototypeOf(current)) {
      const found = Object.getOwnPropertyDescriptor(current, key);
      if (found) return found;
    }
  }
  function popupContext(state, host) {
    const a = state.attributes;
    return JSON.stringify([state.entity_id, state.state, a.preset_mode, a.Sollwertbasis,
      a.temperature, a.min_temp, a.max_temp, a.target_temp_step, a.supported_features,
      popupHass(host)?.config?.unit_system?.temperature]);
  }
  function targetCurrent(record) {
    const state = record.host.stateObj;
    return running && !record.closed && targetRecords.get(record.host) === record &&
      record.host.isConnected && record.node.isConnected && popupConnected(record.host) && popupTarget(state) && record.presentation.allows(state) &&
      record.host.shadowRoot?.querySelectorAll("ha-state-control-climate-temperature").length === 1 &&
      record.host.shadowRoot.querySelector("ha-state-control-climate-temperature") === record.node;
  }
  function clearTarget(host) {
    const record = targetRecords.get(host); if (!record) return;
    record.closed = true; record.lease?.dispose(); record.unsubscribe?.(); record.observer.disconnect(); record.note?.remove();
    record.node.inert = record.inert; if (record.node.style.opacity === "0.6" || record.node.style.opacity === record.opacity) record.node.style.opacity = record.opacity;
    if (record.aria === null) record.node.removeAttribute("aria-disabled"); else record.node.setAttribute("aria-disabled", record.aria);
    if (host.stateObj && record.node.isConnected) record.node.stateObj = host.stateObj;
    targetRecords.delete(host);
  }
  function applyTargetPresentation(record) {
    const state = record.host.stateObj, hass = popupHass(record.host), unit = hass?.config?.unit_system?.temperature;
    record.presentation.observe(state, unit, popupConnected(record.host));
    const view = record.presentation.display(state, popupConnected(record.host));
    record.held = view.inert ? { id: "presentation_only" } : null;
    if (record.node.stateObj !== view.state) record.node.stateObj = view.state;
    record.node.inert = view.inert; record.node.style.opacity = view.inert ? "0.6" : record.opacity;
    if (view.inert) record.node.setAttribute("aria-disabled", "true");
    else if (record.aria === null) record.node.removeAttribute("aria-disabled"); else record.node.setAttribute("aria-disabled", record.aria);
    record.note.textContent = view.note;
    if (view.note) { if (record.note.parentNode !== record.node.parentNode) record.node.insertAdjacentElement("afterend", record.note); }
    else record.note.remove();
    observe(record);
  }
  function makeTargetRecord(host, node, state) {
    const hass = popupHass(host), presentation = sharedTargetPresentation(hass, state);
    presentation.observe(state, hass?.config?.unit_system?.temperature, popupConnected(host));
    const record = { host, node, entity: state.entity_id, closed: false, held: null, presentation,
      unit: hass?.config?.unit_system?.temperature, context: popupContext(state, host), inert: node.inert, opacity: node.style.opacity || "", aria: node.getAttribute("aria-disabled"),
      observer: new MutationObserver(() => schedule(host)) };
    record.note = document.createElement("div"); record.note.dataset.hargassnerTargetUpdate = VERSION;
    record.note.setAttribute("role", "status"); record.note.setAttribute("aria-live", "polite");
    record.note.style.cssText = "text-align:center;color:var(--secondary-text-color);margin:8px 0;";
    targetRecords.set(host, record);
    record.lease = temperatureLease(node, {
      current: () => targetCurrent(record), context: () => popupContext(host.stateObj, host),
      allowed: data => data?.entity_id === record.entity && Number.isFinite(data.temperature) &&
        popupTarget(host.stateObj) && data.temperature >= host.stateObj.attributes.min_temp && data.temperature <= host.stateObj.attributes.max_temp,
      prepare: (target, submitted) => {
        if (!targetCurrent(record)) return null;
        const current = host.stateObj; let intent = presentation.intent;
        if (!submitted || !intent || intent.target !== target || intent.basis !== current.attributes.Sollwertbasis)
          intent = presentation.begin(current, record.unit, target);
        if (submitted && intent) { intent.status = "pending"; intent.submitted = true; }
        applyTargetPresentation(record); return intent;
      },
      intentCurrent: intent => !intent || presentation.intent === intent,
      rejected: intent => { presentation.reject(intent); if (!record.closed) applyTargetPresentation(record); },
      setTimeout: environment.setTimeout.bind(environment), clearTimeout: environment.clearTimeout.bind(environment),
    });
    if (!record.lease) { clearTarget(host); return null; }
    record.unsubscribe = presentation.subscribe(() => {
      if (!record.closed && record.unit === popupHass(host)?.config?.unit_system?.temperature) schedule(host);
    });
    return record;
  }
  function reconcileTarget(host) {
    const state = host.stateObj, children = host.shadowRoot?.querySelectorAll("ha-state-control-climate-temperature");
    let record = targetRecords.get(host);
    if (!host.isConnected || !eligible(host) || children?.length !== 1 ||
        children[0].parentElement?.classList.contains("controls") !== true) { clearTarget(host); return; }
    const node = children[0], unit = popupHass(host)?.config?.unit_system?.temperature;
    if (record && (record.node !== node || record.entity !== state.entity_id || record.unit !== unit)) {
      clearTarget(host); record = null;
    }
    if (!record) { record = makeTargetRecord(host, node, state); if (!record) return; }
    const context = popupContext(state, host);
    if (context !== record.context) {
      if (record.presentation.intent && !record.presentation.intent.submitted) record.presentation.reject(record.presentation.intent);
      record.context = context; record.lease.rotate(context);
    }
    applyTargetPresentation(record);
  }

  function restore(record) {
    record.observer?.disconnect();
    try {
      record.modus.removeEventListener("wa-select", record.select);
      record.modus.remove();
      for (const original of record.originals) {
        const { menu, display, priority } = original;
        if (menu.style.getPropertyValue("display") === "none" &&
            menu.style.getPropertyPriority("display") === "important") {
          if (display) menu.style.setProperty("display", display, priority);
          else menu.style.removeProperty("display");
        }
      }
    } finally {
      records.delete(record.host);
    }
  }
  function clear(host) {
    const record = records.get(host);
    if (record) restore(record);
  }
  function clearStatus(host) {
    const record = statusRecords.get(host);
    if (!record) return;
    record.observer.disconnect();
    record.node.remove();
    statusRecords.delete(host);
  }
  function schedule(host) {
    if (!running || queued.has(host)) return;
    queued.add(host);
    environment.queueMicrotask(() => {
      queued.delete(host);
      if (!running) return;
      try { reconcile(host); } catch {
        try { clear(host); } catch { /* Optional UI only. */ }
        try { clearStatus(host); } catch { /* Optional UI only. */ }
        try { clearTarget(host); } catch { /* Optional UI only. */ }
      }
    });
  }
  function observe(record) {
    record.observer.disconnect();
    record.observer.observe(record.host.shadowRoot, { childList: true, subtree: true, attributes: true });
    for (const menu of [record.hvac, record.preset, record.modus].filter(Boolean)) {
      if (menu.shadowRoot) record.observer.observe(menu.shadowRoot, { childList: true, subtree: true, attributes: true });
    }
    // Direct-parent observations, including shadow-root boundaries, detect
    // popup removal without watching the entire HA document subtree.
    let node = record.host;
    for (let depth = 0; node && depth < 64; depth++) {
      if (node.parentNode) {
        record.observer.observe(node.parentNode, { childList: true });
        node = node.parentNode;
      } else if (node.host) node = node.host;
      else break;
    }
  }
  function reconcileStatus(host) {
    const state = host.stateObj, attributes = state?.attributes;
    if (!host.isConnected || !host.shadowRoot ||
        typeof state?.entity_id !== "string" || !state.entity_id.startsWith("climate.") ||
        attributes?.hargassner_control_schema !== SCHEMA) { clearStatus(host); return; }
    const containers = host.shadowRoot.querySelectorAll("ha-more-info-control-select-container");
    if (containers.length !== 1 || !containers[0].parentNode) { clearStatus(host); return; }
    const anchor = containers[0], old = statusRecords.get(host);
    if (old && (old.entity !== state.entity_id || old.anchor !== anchor ||
        old.node.parentNode !== anchor.parentNode)) clearStatus(host);
    let record = statusRecords.get(host);
    if (!record) {
      const node = document.createElement("div");
      node.dataset.hargassnerOperatingState = VERSION;
      node.setAttribute("role", "status");
      node.setAttribute("aria-live", "polite");
      node.style.cssText = "margin:12px 0 0;text-align:center;line-height:1.5;overflow-wrap:anywhere;";
      const label = document.createElement("div"), value = document.createElement("div");
      label.textContent = "Heizkreisstatus";
      label.style.cssText = "color:var(--secondary-text-color);font-size:var(--ha-font-size-s,12px);";
      value.dataset.hargassnerOperatingStateValue = "";
      node.append(label, value);
      record = { host, node, value, anchor, entity: state.entity_id,
        observer: new MutationObserver(() => schedule(host)) };
      statusRecords.set(host, record);
      anchor.insertAdjacentElement("afterend", node);
    }
    const label = attributes["Heizkreisstatus"], code = attributes["Heizkreisstatus-Code"];
    const available = !["unavailable", "unknown"].includes(state.state) &&
      attributes["Heizkreisstatus verfügbar"] === true && Number.isSafeInteger(code) &&
      typeof label === "string" && label.trim().length > 0 && label.length <= 160;
    const text = available ? label : "Nicht verfügbar";
    if (record.value.textContent !== text) record.value.textContent = text;
    const title = available ? `Code ${code}` : "";
    if (record.node.title !== title) record.node.title = title;
    if (record.node.dataset.available !== String(available)) record.node.dataset.available = String(available);
    observe(record);
  }
  function reconcile(host) {
    reconcileTarget(host);
    reconcileStatus(host);
    if (!host.isConnected || !host.shadowRoot) { clear(host); return; }
    const context = eligible(host);
    if (!context) { clear(host); return; }
    const old = records.get(host);
    const menus = [...host.shadowRoot.querySelectorAll("ha-control-select-menu")]
      .filter(menu => menu !== old?.modus);
    if (menus.length < 2 || menus.length > 8) { clear(host); return; }
    const operation = menus.filter(menu => sameValues(optionValues(menu), context.attributes.hvac_modes));
    const presets = menus.filter(menu => sameValues(optionValues(menu), context.attributes.preset_modes));
    if (operation.length !== 1 || presets.length !== 1 || operation[0] === presets[0] ||
        operation[0].value !== context.state.state || presets[0].value !== context.attributes.preset_mode ||
        typeof presets[0].label !== "string" || operation[0].parentNode !== presets[0].parentNode ||
        operation[0].parentNode?.localName !== "ha-more-info-control-select-container") { clear(host); return; }
    const hvac = operation[0], preset = presets[0], parent = hvac.parentNode;
    if (old && (old.hvac !== hvac || old.preset !== preset || old.entity !== context.state.entity_id ||
                old.modus.parentNode !== parent)) clear(host);
    let record = records.get(host);
    if (!record) {
      const modus = document.createElement("ha-control-select-menu");
      modus.dataset.hargassnerModus = VERSION;
      modus.label = LABEL;
      modus.options = PRESETS.map(value => ({ value, label: value, iconPath: ICON_PATHS[MODE_ICONS[value]] }));
      record = { host, hvac, preset, modus, entity: context.state.entity_id,
        originals: [hvac, preset].map(menu => ({ menu,
          display: menu.style.getPropertyValue("display"), priority: menu.style.getPropertyPriority("display") })),
        observer: new MutationObserver(() => schedule(host)), select: null };
      record.select = event => {
        event.stopPropagation();
        const selected = event.detail?.item?.value;
        // Revalidate the current entity, schema, native controls and disabled
        // state at the user event. Detached/old menus cannot address a new entity.
        if (!running || records.get(host) !== record || !PRESETS.includes(selected)) return;
        reconcile(host);
        const current = eligible(host);
        if (records.get(host) !== record || !current || record.entity !== current.state.entity_id ||
            !modus.isConnected || modus.disabled || selected === current.attributes.preset_mode) return;
        const target = targetRecords.get(host);
        if (target) { target.presentation.intent = null; target.presentation.notify(); }
        // HA's existing listener performs climate.set_preset_mode and its
        // ordinary resync/error behavior. The HVAC and temperature paths are
        // untouched. Do not optimistically replace the reported current value.
        preset.dispatchEvent(new environment.CustomEvent("wa-select", {
          detail: { item: { value: selected } }, bubbles: false, composed: false,
        }));
      };
      modus.addEventListener("wa-select", record.select);
      records.set(host, record);
      parent.insertBefore(modus, hvac);
    }
    for (const menu of [hvac, preset]) {
      if (menu.style.getPropertyValue("display") !== "none" || menu.style.getPropertyPriority("display") !== "important")
        menu.style.setProperty("display", "none", "important");
    }
    if (record.modus.value !== context.attributes.preset_mode) record.modus.value = context.attributes.preset_mode;
    const disabled = !!(hvac.disabled || preset.disabled || !HVAC.includes(context.state.state) || !popupConnected(host));
    if (record.modus.disabled !== disabled) record.modus.disabled = disabled;
    observe(record);
  }
  function scanOnce() {
    const roots = [document];
    let elements = 0, rootCount = 0;
    while (roots.length && rootCount++ < 128 && elements < 12000) {
      const root = roots.pop();
      for (const node of root.querySelectorAll("*")) {
        if (++elements > 12000) break;
        if (node.localName === "more-info-climate") schedule(node);
        if (node.shadowRoot) roots.push(node.shadowRoot);
      }
    }
  }
  const controller = {
    version: VERSION,
    get running() { return running; },
    status: () => ({ running, activePopups: records.size, statusPopups: statusRecords.size, targetPopups: targetRecords.size, queued: queued.size }),
    stop() {
      if (!running) return;
      running = false;
      for (const record of [...records.values()]) { try { restore(record); } catch { /* Keep stopping other records. */ } }
      for (const host of [...statusRecords.keys()]) { try { clearStatus(host); } catch { /* Keep stopping other records. */ } }
      for (const host of [...targetRecords.keys()]) { try { clearTarget(host); } catch { /* Keep stopping other records. */ } }
      queued.clear();
      if (prototype && prototype.updated === wrapped) {
        if (originalDescriptor) Object.defineProperty(prototype, "updated", originalDescriptor);
        else delete prototype.updated;
      }
      environment.removeEventListener("pagehide", controller.stop);
    },
    ready: null,
  };
  environment[KEY] = controller;
  environment.hargassnerThermostatPopup = controller;
  environment.addEventListener("pagehide", controller.stop);
  controller.ready = customElements.whenDefined("more-info-climate").then(() => {
    if (!running) return;
    prototype = customElements.get("more-info-climate")?.prototype;
    if (!prototype || typeof prototype.updated !== "function") return;
    originalDescriptor = Object.getOwnPropertyDescriptor(prototype, "updated");
    const original = prototype.updated;
    wrapped = function (...args) {
      const result = original.apply(this, args);
      // Runs before the native child's queued Lit update, preventing the
      // interactive-to-readonly template flash without touching host state.
      try { reconcileTarget(this); } catch { try { clearTarget(this); } catch { /* Optional UI only. */ } }
      schedule(this);
      return result;
    };
    try {
      Object.defineProperty(prototype, "updated", { configurable: true, writable: true, value: wrapped });
      scanOnce();
    } catch { controller.stop(); }
  });
  return controller;
}

function scopedClimate(state) {
  return typeof state?.entity_id === "string" && state.entity_id.startsWith("climate.") &&
    state.attributes?.hargassner_control_schema === SCHEMA;
}
function operatingState(state, connected = true) {
  const a = state?.attributes, label = a?.["Heizkreisstatus"], code = a?.["Heizkreisstatus-Code"];
  return connected && !["unknown", "unavailable"].includes(state?.state) &&
    a?.["Heizkreisstatus verfügbar"] === true && Number.isSafeInteger(code) &&
    typeof label === "string" && label.trim() && label.length <= 160 ? label : "Nicht verfügbar";
}
function validTarget(state) {
  const a = state?.attributes;
  return scopedClimate(state) && ["auto", "heat"].includes(state.state) &&
    PRESETS.slice(1).includes(a.preset_mode) && state.state === (a.preset_mode === "Auto" ? "auto" : "heat") &&
    sameValues(a.hvac_modes, HVAC) &&
    a["Zieltemperatur verfügbar"] === true &&
    Number.isSafeInteger(a.supported_features) && (a.supported_features & 1) === 1 &&
    [a.temperature, a.min_temp, a.max_temp, a.target_temp_step].every(Number.isFinite) &&
    a.target_temp_step > 0 && a.max_temp > a.min_temp &&
    a.temperature >= a.min_temp && a.temperature <= a.max_temp;
}

/** Per-surface requested-value presentation. Never grants command authority. */
export class HargassnerTargetPresentation {
  constructor() { this.listeners = new Set(); this.retiredContexts = new Set(); this.reset(); }
  subscribe(listener) { this.listeners.add(listener); return () => this.listeners.delete(listener); }
  notify() {
    if (this.notifying) return; this.notifying = true;
    queueMicrotask(() => { this.notifying = false; for (const listener of [...this.listeners]) listener(); });
  }
  reset() { this.confirmed = null; this.intent = null; this.context = null; this.sequence = 0; this.revision = -1; this.serial = 0; this.entity = null; this.unit = null; this.failure = false; this.retiredContexts?.clear(); this.seenStates = new WeakSet(); this.actual = null; }
  envelope(state) {
    const value = state?.attributes?.Temperaturauftrag;
    if (!value || typeof value !== "object" || value.schemaVersion !== 1 ||
        typeof value.context !== "string" || !/^[a-f0-9]{32}$/.test(value.context) ||
        !Number.isSafeInteger(value.sequence) || value.sequence < 0 || !Number.isSafeInteger(value.revision) || value.revision < 0 || !Array.isArray(value.records) || value.records.length > 8) return null;
    if (value.temperatureUnit !== "°C" || !["°C", "°F"].includes(this.unit)) return null;
    const records = [];
    for (const row of value.records) {
      if (!row || typeof row !== "object" || !Number.isSafeInteger(row.sequence) || row.sequence < 1 || row.sequence > value.sequence ||
          typeof row.basis !== "string" || !/^[a-f0-9]{32}$/.test(row.basis) || !Number.isFinite(row.target) ||
          !["pending", "confirmed", "rejected", "sent_unconfirmed"].includes(row.status)) return null;
      records.push({ ...row, target: this.unit === "°F" ? Math.round((row.target * 9 / 5 + 32) * 1e8) / 1e8 : row.target });
    }
    if (new Set(records.map(row => row.sequence)).size !== records.length) return null;
    return { context: value.context, sequence: value.sequence, revision: value.revision, records: records.sort((a, b) => a.sequence - b.sequence) };
  }
  scope(state, unit) {
    if (this.entity !== state?.entity_id || this.unit !== unit || !scopedClimate(state)) {
      this.reset(); this.entity = state?.entity_id; this.unit = unit;
    }
  }
  coherent(state, unit = this.unit) {
    const echo = state?.attributes?.Temperaturauftrag;
    if (echo === undefined || echo === null) return true; // Exact old backend.
    if (!this.envelope(state)) return false;
    return echo.temperatureUnit === "°C" && Number.isFinite(echo.confirmedTarget) &&
      Math.abs(state.attributes.temperature - (unit === "°F" ? echo.confirmedTarget * 1.8 + 32 : echo.confirmedTarget)) < 1e-7 &&
      echo.confirmedBasis === state.attributes.Sollwertbasis && echo.confirmedPreset === state.attributes.preset_mode;
  }
  allows(state, unit = this.unit) {
    const envelope = this.envelope(state);
    return !(this.seenStates.has(state) && this.actual !== state) && !(envelope && (this.retiredContexts.has(envelope.context) ||
      this.context !== null && envelope.context !== this.context ||
      envelope.context === this.context && envelope.revision < this.revision)) && this.coherent(state, unit);
  }
  configuration(state, unit) {
    const a = state?.attributes || {}, correction = Number(a.Raumkorrektur);
    const base = Number.isFinite(a.temperature) && a.Raumkorrektur != null && Number.isFinite(correction)
      ? Math.round((a.temperature - correction * (unit === "°F" ? 1.8 : 1)) * 1e8) / 1e8 : null;
    return JSON.stringify([state?.state, a.preset_mode, a.Sollwertphase, a.Sollwertparameter, base]);
  }
  begin(state, unit, target) {
    this.scope(state, unit);
    if (!validTarget(state) || !this.allows(state, unit) || !Number.isFinite(target) || target < state.attributes.min_temp || target > state.attributes.max_temp) return null;
    const envelope = this.envelope(state);
    const intent = { serial: ++this.serial, target, basis: state.attributes.Sollwertbasis,
      context: envelope?.context ?? this.context, afterSequence: envelope?.sequence ?? this.sequence,
      boundSequence: null, status: "pending", local: true, submitted: false, createdAt: Date.now(), configuration: this.configuration(state, unit) };
    this.intent = intent; this.failure = false; this.notify(); return intent;
  }
  reject(intent) {
    if (intent && this.intent === intent) { this.intent = null; this.failure = true; this.notify(); }
  }
  unknown(intent) { if (intent && this.intent === intent) { intent.status = "sent_unconfirmed"; this.notify(); } }
  observe(state, unit, connected = true) {
    this.scope(state, unit);
    if (!scopedClimate(state)) return;
    const valid = connected && validTarget(state), envelope = this.envelope(state), previous = this.confirmed;
    if (envelope && (this.retiredContexts.has(envelope.context) || envelope.context === this.context && (envelope.sequence < this.sequence || envelope.revision < this.revision))) return;
    if (this.seenStates.has(state) && this.actual !== state) return;
    this.seenStates.add(state); this.actual = state;
    const before = JSON.stringify([this.intent?.serial, this.intent?.status, this.confirmed?.basis, this.confirmed?.state.attributes.temperature]);
    if (envelope) {
      const sameContext = this.context === envelope.context;
      if (!sameContext && this.context) { this.retiredContexts.add(this.context); if (this.retiredContexts.size > 16) this.retiredContexts.delete(this.retiredContexts.values().next().value); }
      if (!sameContext && this.intent) { this.intent.status = "sent_unconfirmed"; this.intent.boundSequence = null; }
      // A newly mounted surface does not adopt old saved intent from a receipt.
      if (sameContext && envelope.sequence >= this.sequence) {
        const intent = this.intent;
        if (intent?.local && intent.context === envelope.context) {
          const matching = envelope.records.filter(row => row.sequence > intent.afterSequence &&
            row.basis === intent.basis && row.target === intent.target);
          const newest = matching.at(-1);
          if (newest && ["pending", "sent_unconfirmed"].includes(newest.status)) {
            intent.boundSequence = newest.sequence; intent.status = newest.status;
          }
          const cutoff = Math.max(intent.afterSequence, intent.boundSequence || 0, ...matching.map(row => row.sequence));
          const later = cutoff > intent.afterSequence && envelope.records.filter(row => row.sequence > cutoff && row.status !== "rejected").at(-1);
          if (later && previous && later.target >= previous.state.attributes.min_temp && later.target <= previous.state.attributes.max_temp) {
            this.failure = false;
            this.intent = { serial: ++this.serial, target: later.target, basis: later.basis, context: envelope.context,
              afterSequence: cutoff, boundSequence: later.sequence, status: later.status === "confirmed" ? "pending" : later.status,
              local: false, submitted: true, createdAt: Date.now(), configuration: this.configuration(previous.state, unit) };
          }
          const bound = envelope.records.find(row => row.sequence === intent.boundSequence);
          if (this.intent === intent && bound?.status === "rejected") this.reject(intent);
          else if (bound?.status === "sent_unconfirmed") intent.status = "sent_unconfirmed";
          // Correction readback alone is not a qualified configured target.
        } else if (!intent) {
          const newest = envelope.records.filter(row => row.sequence > this.sequence).at(-1);
          if (newest && previous?.basis === newest.basis && ["pending", "sent_unconfirmed", "confirmed"].includes(newest.status)) {
            this.failure = false;
            this.intent = { serial: ++this.serial, target: newest.target, basis: newest.basis, context: envelope.context,
              afterSequence: this.sequence, boundSequence: newest.sequence, status: newest.status === "confirmed" ? "pending" : newest.status, local: false, submitted: true, createdAt: Date.now(), configuration: this.configuration(previous.state, unit) };
          }
        } else if (!intent.local && intent.context === envelope.context) {
          const newer = envelope.records.filter(row => row.sequence > intent.boundSequence).at(-1);
          if (newer && previous?.basis === newer.basis && ["pending", "sent_unconfirmed", "confirmed"].includes(newer.status)) {
            this.failure = false;
            this.intent = { serial: ++this.serial, target: newer.target, basis: newer.basis, context: envelope.context,
              afterSequence: this.sequence, boundSequence: newer.sequence, status: newer.status === "confirmed" ? "pending" : newer.status, local: false, submitted: true, createdAt: Date.now(), configuration: this.configuration(previous.state, unit) };
          } else {
            const bound = envelope.records.find(row => row.sequence === intent.boundSequence);
            if (bound?.status === "rejected") this.reject(intent);
            else if (bound?.status === "sent_unconfirmed") intent.status = "sent_unconfirmed";
          }
        }
      }
      this.context = envelope.context; this.sequence = envelope.sequence; this.revision = envelope.revision;
    }
    if (this.intent && (!connected || Date.now() - this.intent.createdAt >= 8000)) this.intent.status = "sent_unconfirmed";
    const echoed = this.coherent(state, unit);
    if (valid && echoed) {
      if (this.intent && this.intent.configuration !== this.configuration(state, unit)) this.intent = null;
      const basis = state.attributes.Sollwertbasis;
      if (typeof basis === "string" && /^[a-f0-9]{32}$/.test(basis))
        this.confirmed = { basis, state: { ...state, attributes: { ...state.attributes } }, unit };
      if (this.intent && state.attributes.temperature === this.intent.target) this.intent = null;
    }
    if (before !== JSON.stringify([this.intent?.serial, this.intent?.status, this.confirmed?.basis, this.confirmed?.state.attributes.temperature])) this.notify();
  }
  display(state, connected = true) {
    if (!this.confirmed || state?.state === "off" || !scopedClimate(state)) return { state, note: "", inert: !connected || !validTarget(state) };
    const intent = this.intent, valid = connected && validTarget(state) && this.allows(state);
    if (!intent && valid) return { state, note: this.failure ? "Änderung abgelehnt · bestätigter Sollwert" : "", inert: false };
    const old = this.confirmed.state.attributes, target = intent?.target ?? old.temperature;
    const attributes = { ...state.attributes, temperature: target, min_temp: old.min_temp, max_temp: old.max_temp,
      target_temp_step: old.target_temp_step, supported_features: (state.attributes.supported_features || 0) | (old.supported_features & 1) };
    // Requested/last-confirmed numbers exist only in this inert/private display.
    const status = intent ? intent.status === "pending" ? "Angefragt · Bestätigung ausstehend" : "Angefragt · Bestätigung unklar" :
      connected && !["unknown", "unavailable"].includes(state.state) ? "Letzter bestätigter Sollwert · Rückmeldung ausstehend" : "Letzter bestätigter Sollwert · nicht verfügbar";
    return { state: { ...state, attributes }, note: status, inert: !valid };
  }
}

// Memory only, isolated by authenticated HA connection/user/entity. This lets a
// later gesture in another surface supersede an older pending gesture without
// persisting intent or modifying Home Assistant's shared states.
const targetPresentationSessions = new WeakMap();
function sharedTargetPresentation(hass, state, fallback) {
  const connection = hass?.connection;
  if (!connection || typeof connection !== "object" || !scopedClimate(state)) return fallback || new HargassnerTargetPresentation();
  let entries = targetPresentationSessions.get(connection);
  if (!entries) { entries = new Map(); targetPresentationSessions.set(connection, entries); }
  const key = `${hass.user?.id || ""}::${state.entity_id}`;
  let presentation = entries.get(key);
  if (!presentation) {
    presentation = new HargassnerTargetPresentation(); entries.set(key, presentation);
    if (entries.size > 16) entries.delete(entries.keys().next().value);
  }
  return presentation;
}

function temperatureDescriptor(node, key) {
  for (let current = node; current; current = Object.getPrototypeOf(current)) {
    const value = Object.getOwnPropertyDescriptor(current, key); if (value) return value;
  }
}
function findTemperatureControl(native) {
  const found = [], seen = new Set();
  function visit(root, depth = 0) {
    if (!root || depth > 12 || seen.has(root)) return; seen.add(root);
    for (const node of root.querySelectorAll?.("*") || []) {
      if (node.localName === "ha-state-control-climate-temperature") found.push(node);
      if (node.shadowRoot) visit(node.shadowRoot, depth + 1);
    }
  }
  visit(native.shadowRoot); return found.length === 1 ? found[0] : null;
}
/** In-place native event/API leases; old references remain closed forever. */
function temperatureLease(node, options) {
  const api = temperatureDescriptor(node, "_api"), debounce = node._debouncedCallService;
  if (!api?.get || !api.set || typeof node._callService !== "function" || typeof debounce?.cancel !== "function" ||
      ["_api", "_callService", "_debouncedCallService"].some(key => Object.getOwnPropertyDescriptor(node, key)?.configurable === false)) return null;
  const record = { node, epoch: 0, closed: false, context: null, tickets: new WeakMap(), originalCall: node._callService,
    originalDebounce: debounce, originals: Object.fromEntries(["_api", "_callService", "_debouncedCallService"].map(key => [key, Object.getOwnPropertyDescriptor(node, key)])) };
  const current = epoch => !record.closed && record.epoch === epoch && options.current() && options.context() === record.context;
  record.get = function () {
    const raw = api.get.call(this), epoch = record.epoch;
    if (!raw || typeof raw.callService !== "function") return raw;
    if (record.cache?.raw === raw && record.cache.epoch === epoch) return record.cache.proxy;
    const guarded = (...args) => {
      if (!current(epoch) || api.get.call(node) !== raw || args[0] !== "climate" || args[1] !== "set_temperature" || !options.allowed(args[2])) return Promise.resolve();
      const intent = options.prepare(args[2].temperature, true);
      let result;
      try { result = raw.callService(...args); }
      catch (error) { options.rejected(intent); return Promise.resolve(); }
      return Promise.resolve(result).catch(() => { options.rejected(intent); });
    };
    const proxy = new Proxy(raw, { get: (value, key) => key === "callService" ? guarded : typeof value[key] === "function" ? value[key].bind(value) : value[key] });
    record.cache = { raw, epoch, proxy }; return proxy;
  };
  record.set = function (value) { api.set.call(this, value); record.cache = null; };
  record.rotate = function (context, freshApi) {
    record.originalDebounce.cancel(); options.clearTimeout(record.timer); record.timer = undefined; record.context = context; const epoch = ++record.epoch; record.cache = null;
    record.call = function (type) {
      if (this !== node || !current(epoch)) return;
      const ticket = type && typeof type === "object" ? record.tickets.get(type) : null;
      if (ticket && (ticket.epoch !== epoch || !options.intentCurrent(ticket.intent))) return;
      if (typeof type !== "string" && !ticket) return;
      if (!ticket) {
        options.prepare(node._targetTemperature?.value, false);
        record.rotate(options.context(), options.freshApi?.());
      }
      return record.originalCall.call(node, ticket ? ticket.type : type);
    };
    record.debounce = function (type) {
      if (this !== node || !current(epoch) || typeof type !== "string") return;
      const intent = options.prepare(node._targetTemperature?.value, false), ticket = {};
      record.rotate(options.context(), options.freshApi?.());
      record.tickets.set(ticket, { type, epoch: record.epoch, intent });
      const invoke = record.call;
      record.timer = options.setTimeout(() => { record.timer = undefined; invoke.call(node, ticket); }, 1000);
    };
    record.debounce.cancel = () => { record.originalDebounce.cancel(); options.clearTimeout(record.timer); record.timer = undefined; };
    Object.defineProperty(node, "_callService", { configurable: true, writable: true, value: record.call });
    Object.defineProperty(node, "_debouncedCallService", { configurable: true, writable: true, value: record.debounce });
    if (freshApi) record.set.call(node, freshApi);
  };
  record.dispose = function () {
    record.closed = true; record.epoch++; record.originalDebounce.cancel();
    for (const [key, expected] of [["_api", record.get], ["_callService", record.call], ["_debouncedCallService", record.debounce]]) {
      const own = Object.getOwnPropertyDescriptor(node, key); if ((key === "_api" ? own?.get : own?.value) !== expected) continue;
      const original = record.originals[key];
      if (key === "_callService") {
        const call = record.originalCall;
        Object.defineProperty(node, key, { configurable: true, writable: true, value: function (type) { if (typeof type === "string") return call.call(this, type); } });
      } else if (original) Object.defineProperty(node, key, original); else delete node[key];
    }
  };
  try {
    Object.defineProperty(node, "_api", { configurable: true, get: record.get, set: record.set }); record.rotate(options.context()); return record;
  } catch { record.dispose(); return null; }
}

export function installHargassnerThermostatCard(environment = window) {
  const { customElements, document, HTMLElement } = environment;
  if (!customElements || !document || !HTMLElement) return;
  const cardType = "hargassner-thermostat-card";
  if (!customElements.get(cardType)) {
    class HargassnerThermostatCard extends HTMLElement {
      constructor() {
        super(); this.attachShadow({ mode: "open" }); this._generation = 0; this._retiredNative = new WeakSet(); this._guardedNative = new WeakSet(); this._presentation = new HargassnerTargetPresentation(); this._apiEpoch = 0;
      }
      static getStubConfig(hass, entities = [], fallback = []) {
        const ids = [...entities, ...fallback, ...Object.keys(hass?.states || {})];
        return { entity: ids.find(id => scopedClimate(hass?.states?.[id])) || "" };
      }
      static getConfigForm() {
        return { schema: [
          { name: "entity", required: true, selector: { entity: { domain: "climate" } } },
          { name: "name", selector: { text: {} } },
        ], computeLabel: field => field.name === "entity" ? "Heizkreis" : "Name",
        computeHelper: field => field.name === "entity" ? "Hargassner-Heizkreis auswählen." : undefined };
      }
      setConfig(config) {
        if (typeof config?.entity !== "string" || !/^climate\.[a-z0-9_]+$/.test(config.entity))
          throw new Error("Bitte eine Heizkreis-Klimaentität auswählen.");
        if (config.name !== undefined && typeof config.name !== "string") throw new Error("Name muss Text sein.");
        this._dispose(); this._config = { entity: config.entity, name: config.name }; this._render();
      }
      set hass(value) { this._hass = value; this._render(); }
      get hass() { return this._hass; }
      getCardSize() { return 11; }
      getGridOptions() { return { columns: 12, rows: 9, min_columns: 6, min_rows: 9 }; }
      connectedCallback() { this._render(); }
      disconnectedCallback() { this._dispose(); }
      _cancelNative(force = false) {
        this._controlLease?.dispose(); this._controlLease = null; this._controlObserver?.disconnect(); this._controlObserver = null; this._apiEpoch = (this._apiEpoch || 0) + 1;
        if (this._nativeContext) {
          this._ui?.nativeHost.removeEventListener("context-request", this._nativeContext);
          this._nativeContext = null;
        }
        if (!force && !this._native && (!this._pending || !this._wantTarget)) return;
        this._generation++; this._cancelWait?.(); this._cancelWait = null;
        this._pending = false; this._native?.remove(); this._native = null;
      }
      _dispose() {
        this._cancelNative(true); this._presentationUnsubscribe?.(); this._presentationUnsubscribe = null; this._presentation = new HargassnerTargetPresentation();
        if (this._binding) this._binding.menu.removeEventListener("wa-select", this._binding.handler);
        this._binding = null; this._ui = null; this._failed = false; this._busy = false;
        this.shadowRoot.replaceChildren();
      }
      _connectedHA() { return this._hass?.connected !== false && this._hass?.connection?.connected !== false; }
      _build() {
        const style = document.createElement("style");
        style.textContent = `:host{display:block;height:100%}ha-card{height:100%;box-sizing:border-box;overflow:hidden}
          h3{font-size:var(--ha-font-size-l,20px);font-weight:500;margin:16px 16px 0;text-align:center;overflow-wrap:anywhere}
          .native{--ha-card-border-width:0;--ha-card-box-shadow:none;--ha-card-border-radius:0;--ha-card-background:transparent;display:block;height:320px}
          .placeholder{text-align:center;padding:28px 16px;color:var(--secondary-text-color)}
          .footer{padding:8px 16px 16px;display:grid;gap:12px;text-align:center}
          .readings{display:grid;gap:4px;font-size:var(--ha-font-size-s,12px);color:var(--secondary-text-color)}
          .status-label{font-size:var(--ha-font-size-s,12px);color:var(--secondary-text-color)}
          .status{overflow-wrap:anywhere}.error{color:var(--error-color)}[hidden]{display:none!important}
          button{font:inherit;color:var(--primary-color);background:none;border:1px solid var(--divider-color);border-radius:8px;padding:8px;cursor:pointer}`;
        const card = document.createElement("ha-card"), title = document.createElement("h3");
        const nativeHost = document.createElement("div"), placeholder = document.createElement("div");
        const updateNote = document.createElement("div"); updateNote.className = "placeholder"; updateNote.hidden = true;
        updateNote.setAttribute("role", "status"); updateNote.setAttribute("aria-live", "polite");
        updateNote.textContent = "Wird aktualisiert · letzter bestätigter Sollwert";
        placeholder.className = "placeholder";
        const retry = document.createElement("button"); retry.textContent = "Erneut versuchen";
        retry.addEventListener("click", () => { this._failed = false; this._render(); });
        const footer = document.createElement("div"); footer.className = "footer";
        const readings = document.createElement("div"); readings.className = "readings";
        const current = document.createElement("div"), correction = document.createElement("div");
        readings.append(current, correction);
        const menuHost = document.createElement("div"), status = document.createElement("div");
        status.className = "status"; status.setAttribute("role", "status"); status.setAttribute("aria-live", "polite");
        const statusLabel = document.createElement("div"), statusValue = document.createElement("div");
        statusLabel.className = "status-label"; statusLabel.textContent = "Heizkreisstatus";
        status.append(statusLabel, statusValue);
        const error = document.createElement("div"); error.className = "error"; error.setAttribute("role", "alert");
        footer.append(readings, menuHost, status, error); card.append(title, nativeHost, updateNote, placeholder, retry, footer);
        this.shadowRoot.append(style, card);
        this._ui = { card, title, nativeHost, updateNote, placeholder, retry, footer, current, correction, menuHost, statusValue, error };
      }
      _format(value, allowString = false, unit = this._hass?.config?.unit_system?.temperature || "°C") {
        if (allowString && typeof value === "string" && /^-?\d+(\.\d+)?$/.test(value)) value = Number(value);
        if (!Number.isFinite(value)) return "Nicht verfügbar";
        const language = this._hass?.locale?.language || "de";
        let text; try { text = new Intl.NumberFormat(language, { maximumFractionDigits: 1 }).format(value); }
        catch { text = String(value); }
        return `${text} ${unit}`;
      }
      _targetContext(state) {
        if (!scopedClimate(state)) return null;
        const a = state.attributes;
        // Only command-relevant confirmed context; sensor/status heartbeats do
        // not retire the native control or interrupt a current interaction.
        return JSON.stringify([state.entity_id, state.state, a.preset_mode,
          a.Sollwertbasis, a.temperature, a.min_temp, a.max_temp,
          a.target_temp_step, a.supported_features, a.hvac_modes,
          this._hass?.config?.unit_system?.temperature]);
      }
      _makeNativeApi(native, generation) {
        const epoch = this._apiEpoch || 0, targetContext = this._targetContext(this._hass?.states?.[this._config?.entity]);
        const current = () => this.isConnected && this._generation === generation && this._native === native &&
          (this._apiEpoch || 0) === epoch && this._connectedHA() && validTarget(this._hass?.states?.[this._config?.entity]) && this._presentation.allows(this._hass?.states?.[this._config?.entity]) &&
          targetContext === this._targetContext(this._hass?.states?.[this._config?.entity]);
        const guarded = (...args) => {
          if (!current() || args[0] !== "climate" || args[1] !== "set_temperature" ||
              args[2]?.entity_id !== this._config?.entity) return Promise.resolve();
          return this._hass.callService(...args);
        };
        return new Proxy({}, { get: (_, key) => key === "callService" ? guarded :
          typeof this._hass?.[key] === "function" ? this._hass[key].bind(this._hass) : this._hass?.[key] });
      }
      _guardNativeApi(native, generation) {
        this._nativeTargetContext = this._targetContext(this._hass?.states?.[this._config?.entity]);
        this._nativeContext = event => {
          if (event.context !== "hassApi" || typeof event.callback !== "function") return;
          event.stopPropagation(); this._guardedNative.add(native); event.callback(this._makeNativeApi(native, generation), () => {});
          environment.queueMicrotask?.(() => this._refreshControlLease());
        };
        this._ui.nativeHost.addEventListener("context-request", this._nativeContext);
      }
      _prepareTarget(target, submitted = false) {
        const state = this._hass?.states?.[this._config?.entity], unit = this._hass?.config?.unit_system?.temperature;
        if (!this._connectedHA() || !validTarget(state)) return null;
        let intent = this._presentation.intent;
        if (!submitted || !intent || intent.target !== target || intent.basis !== state.attributes.Sollwertbasis) {
          intent = this._presentation.begin(state, unit, target); if (this._ui) this._ui.error.textContent = "";
        }
        if (submitted && intent) { intent.status = "pending"; intent.submitted = true; }
        this._render(); return intent;
      }
      _refreshControlLease() {
        if (!this._native || !this._ui || !this.isConnected) return;
        const node = findTemperatureControl(this._native);
        if (this._controlLease && this._controlLease.node !== node) { this._controlLease.dispose(); this._controlLease = null; }
        if (!node) return;
        if (!this._controlLease) {
          const native = this._native, generation = this._generation;
          this._controlLease = temperatureLease(node, {
            current: () => this.isConnected && node.isConnected && findTemperatureControl(native) === node && this._native === native && this._generation === generation && this._connectedHA() &&
              validTarget(this._hass?.states?.[this._config?.entity]) && this._presentation.allows(this._hass?.states?.[this._config?.entity]),
            context: () => this._targetContext(this._hass?.states?.[this._config?.entity]),
            allowed: data => { const state = this._hass?.states?.[this._config?.entity]; return data?.entity_id === this._config?.entity &&
              Number.isFinite(data.temperature) && validTarget(state) && data.temperature >= state.attributes.min_temp && data.temperature <= state.attributes.max_temp; },
            setTimeout: environment.setTimeout.bind(environment), clearTimeout: environment.clearTimeout.bind(environment),
            freshApi: () => { this._apiEpoch = (this._apiEpoch || 0) + 1; return this._makeNativeApi(native, generation); },
            prepare: (target, submitted) => this._prepareTarget(target, submitted),
            intentCurrent: intent => !intent || this._presentation.intent === intent,
            rejected: intent => { if (this._presentation.intent !== intent) return; this._presentation.reject(intent); if (this._ui) this._ui.error.textContent = "Temperaturänderung fehlgeschlagen."; this._render(); },
          });
          if (this._controlLease) this._controlLease.rotate(this._nativeTargetContext, this._makeNativeApi(native, generation));
        }
        if (this._native.shadowRoot && typeof environment.MutationObserver === "function" && !this._controlObserver) {
          this._controlObserver = new environment.MutationObserver(() => this._refreshControlLease());
          this._controlObserver.observe(this._native.shadowRoot, { childList: true, subtree: true });
        }
      }
      _renderNative(state) {
        const context = this._targetContext(state);
        if (this._nativeTargetContext !== context) {
          if (this._presentation.intent && !this._presentation.intent.submitted) this._presentation.reject(this._presentation.intent);
          this._apiEpoch = (this._apiEpoch || 0) + 1; this._nativeTargetContext = context;
          this._controlLease?.rotate(context, this._makeNativeApi(this._native, this._generation));
        }
        const view = this._presentation.display(state, this._connectedHA());
        this._held = view.inert ? { id: "presentation_only" } : null;
        this._native.hass = view.state === state ? this._hass : { ...this._hass, states: { ...this._hass.states, [state.entity_id]: view.state } };
        this._native.inert = view.inert; if (this._native.style) this._native.style.opacity = view.inert ? "0.6" : "";
        if (view.inert) this._native.setAttribute("aria-disabled", "true"); else this._native.removeAttribute("aria-disabled");
        this._ui.updateNote.textContent = view.note; this._ui.updateNote.hidden = !view.note;
        this._ui.placeholder.hidden = true;
        this._refreshControlLease();
        Promise.resolve(this._native.updateComplete).then(() => this._refreshControlLease());
      }
      _render() {
        if (!this.isConnected || !this._config || !this._hass) return;
        if (!this._ui) this._build();
        const ui = this._ui, state = this._hass.states?.[this._config.entity];
        const scoped = scopedClimate(state) && state.entity_id === this._config.entity, connected = this._connectedHA();
        const presentation = sharedTargetPresentation(this._hass, state, this._presentation);
        if (presentation !== this._presentation || !this._presentationUnsubscribe) {
          this._presentationUnsubscribe?.(); this._presentation = presentation;
          this._presentationUnsubscribe = presentation.subscribe(() => {
            if (this.isConnected && this._hass?.config?.unit_system?.temperature === presentation.unit) this._render();
          });
        }
        ui.title.textContent = this._config.name || state?.attributes?.friendly_name || this._config.entity;
        ui.footer.hidden = !scoped;
        ui.retry.hidden = !this._failed || !scoped;
        if (!scoped) {
          this._presentation.scope(state, this._hass?.config?.unit_system?.temperature); this._cancelNative(true); ui.placeholder.hidden = false;
          ui.placeholder.textContent = !state || ["unknown", "unavailable"].includes(state.state)
            ? "Heizkreis nicht verfügbar." : "Diese Entität ist kein unterstützter Hargassner-Heizkreis.";
          if (this._binding) {
            this._binding.menu.removeEventListener("wa-select", this._binding.handler);
            this._binding.menu.remove(); this._binding = null;
          }
          return;
        }
        const available = connected && !["unknown", "unavailable"].includes(state.state);
        ui.current.textContent = `Ist-Temperatur: ${this._format(available ? state.attributes.current_temperature : null)}`;
        ui.correction.textContent = `Raumkorrektur: ${this._format(available ? state.attributes.Raumkorrektur : null, true, "°C")}`;
        ui.statusValue.textContent = operatingState(state, connected);
        if (customElements.get("ha-control-select-menu")) this._renderMenu(state);
        this._presentation.observe(state, this._hass?.config?.unit_system?.temperature, connected);
        if (this._native) {
          this._renderNative(state); this._renderMenu(state); return;
        }
        if (!connected || !validTarget(state)) {
          ui.placeholder.hidden = false; ui.placeholder.textContent = state.state === "off" ? "Aus · kein aktiver Sollwert." : "Zieltemperatur nicht verfügbar.";
          if (!this._failed && !customElements.get("ha-control-select-menu")) this._load(false);
          return;
        }
        ui.placeholder.hidden = false;
        ui.placeholder.textContent = this._failed ? "Thermostat derzeit nicht verfügbar." : "Thermostat wird geladen …";
        if (!this._failed) this._load(true);
      }
      _renderMenu(state) {
        if (!this._binding) {
          const menu = document.createElement("ha-control-select-menu");
          menu.label = "Modus"; menu.options = PRESETS.map(value => ({ value, label: value, iconPath: ICON_PATHS[MODE_ICONS[value]] }));
          const binding = { menu, entity: this._config.entity };
          binding.handler = event => this._selectMode(event, binding);
          menu.addEventListener("wa-select", binding.handler); this._binding = binding;
          this._ui.menuHost.replaceChildren(menu);
        }
        const context = eligible({ stateObj: state }), menu = this._binding.menu;
        menu.value = context ? state.attributes.preset_mode : undefined;
        menu.disabled = this._busy || !this._connectedHA() || !context || !HVAC.includes(state.state);
      }
      async _selectMode(event, binding) {
        event.stopPropagation();
        const value = event.detail?.item?.value, state = this._hass?.states?.[this._config?.entity];
        if (!this.isConnected || this._binding !== binding || event.currentTarget !== binding.menu ||
            !binding.menu.isConnected || binding.menu.disabled || binding.entity !== this._config?.entity || this._busy ||
            !this._connectedHA() || !eligible({ stateObj: state }) || !HVAC.includes(state.state) ||
            !PRESETS.includes(value) || value === state.attributes.preset_mode) return;
        this._presentation.intent = null; this._presentation.notify(); this._busy = true; this._ui.error.textContent = ""; this._render();
        try {
          await this._hass.callService("climate", "set_preset_mode", { entity_id: binding.entity, preset_mode: value });
        } catch {
          if (this._binding === binding && this._ui) this._ui.error.textContent = "Modus konnte nicht geändert werden.";
        } finally {
          if (this._binding === binding) { this._busy = false; this._render(); }
        }
      }
      _withTimeout(promise) {
        return new Promise((resolve, reject) => {
          const timer = environment.setTimeout(() => reject(new Error("dependency_timeout")), 8000);
          const cancel = () => { environment.clearTimeout(timer); reject(new Error("cancelled")); };
          this._cancelWait = cancel;
          Promise.resolve(promise).then(resolve, reject).finally(() => {
            environment.clearTimeout(timer); if (this._cancelWait === cancel) this._cancelWait = null;
          });
        });
      }
      _attachNative(helpers, generation) {
        const native = helpers.createCardElement({ type: "thermostat", entity: this._config.entity,
          name: "Zieltemperatur", show_current_as_primary: false, features: [] });
        if (native?.localName !== "hui-thermostat-card") throw new Error("native_card_unavailable");
        if (this._nativeContext) this._ui.nativeHost.removeEventListener("context-request", this._nativeContext);
        native.className = "native"; native.hass = this._hass; this._native = native;
        this._guardNativeApi(native, generation);
        // Initial mount only. Later context changes rotate event/API leases in place.
        this._ui.nativeHost.replaceChildren(native);
      }
      async _load(wantTarget) {
        if (this._pending) return;
        this._pending = true; this._wantTarget = wantTarget;
        const generation = this._generation, entity = this._config.entity;
        const current = () => this.isConnected && this._generation === generation && this._config.entity === entity;
        try {
          if (typeof environment.loadCardHelpers !== "function") throw new Error("helpers_unavailable");
          const helpers = await this._withTimeout(environment.loadCardHelpers());
          if (!current()) return;
          await this._withTimeout(customElements.whenDefined("ha-control-select-menu"));
          if (!current()) return;
          const state = this._hass.states?.[entity];
          if (wantTarget && this._connectedHA() && validTarget(state)) {
            this._helpers = helpers; this._attachNative(helpers, generation);
          }
          this._pending = false; this._render();
        } catch {
          if (current()) { this._pending = false; this._failed = true; this._render(); }
        }
      }
    }
    customElements.define(cardType, HargassnerThermostatCard);
  }
  environment.customCards = environment.customCards || [];
  if (!environment.customCards.some(card => card.type === cardType)) environment.customCards.push({
    type: cardType, name: "Hargassner Thermostat", preview: true,
    description: "Heizkreis mit Zieltemperatur, sechs Betriebsarten und Heizkreisstatus.",
    documentationURL: "https://github.com/lanabretterebner/harg-ha",
    getEntitySuggestion: (hass, entity) => scopedClimate(hass?.states?.[entity])
      ? { config: { type: `custom:${cardType}`, entity } } : null,
  });
  return customElements.get(cardType);
}

if (typeof window !== "undefined") {
  installHargassnerThermostatPopup(window);
  installHargassnerThermostatCard(window);
}

/* Logical sections inside the existing Hargassner HA device page.
 * Pinned native contract: frontend20260930.3 ha-device-entities-card.
 * No discovery, entity/device registry, data fetch or service changes.
 */
const DEVICE_GROUP_VERSION = "1.4.0";
const DEVICE_GROUP_KEY = Symbol.for("hargassner.device.groups.v1");
const DEVICE_GROUP_LABELS = Object.freeze({
  "heizkreis_1": "Heizkreis 1",
  "heizkreis_2": "Heizkreis 2",
  "puffer": "Puffer",
  "kessel": "Kessel",
  "solar": "Solar",
  "warmwasser": "Warmwasser",
  "pelletslager": "Pelletslager",
  "steuerung": "Steuerung"
});
const DEVICE_ENTITY_GROUPS = Object.freeze({
  "select.kesselprogramm": "kessel",
  "binary_sensor.boiler_1_pumpe": "warmwasser",
  "binary_sensor.heizkreis_1_pumpe": "heizkreis_1",
  "binary_sensor.heizkreis_2_pumpe": "heizkreis_2",
  "binary_sensor.ruecklauf_ladepumpe_ausgang": "kessel",
  "button.puffer_einmal_ladung_starten": "puffer",
  "button.boiler_1_einmal_ladung_starten": "warmwasser",
  "climate.heizkreis_1": "heizkreis_1",
  "climate.heizkreis_2": "heizkreis_2",
  "number.alle_heizkreise_aus_ueber_aussentemperatur": "steuerung",
  "number.alle_heizkreise_aus_bei_tagabsenkung": "steuerung",
  "number.alle_heizkreise_aus_bei_nachtabsenkung": "steuerung",
  "number.boiler_1_solltemperatur": "warmwasser",
  "number.heizkreis_1_tagtemperatur": "heizkreis_1",
  "number.heizkreis_1_absenktemperatur": "heizkreis_1",
  "number.heizkreis_2_tagtemperatur": "heizkreis_2",
  "number.heizkreis_2_absenktemperatur": "heizkreis_2",
  "number.heizkreis_2_steilheit_fbh": "heizkreis_2",
  "number.heizkreis_2_vorlauftemperatur_minimum_fbh": "heizkreis_2",
  "number.heizkreis_2_vorlauftemperatur_maximum_fbh": "heizkreis_2",
  "number.heizkreis_2_raumkorrektur": "heizkreis_2",
  "number.heizkreis_2_sommer_badheizen_puffer_mindesttemperatur": "heizkreis_2",
  "number.heizkreis_2_sommer_badheizen_vorlauf_soll": "heizkreis_2",
  "number.heizkreis_1_steilheit": "heizkreis_1",
  "number.heizkreis_1_vorlauftemperatur_minimum": "heizkreis_1",
  "number.heizkreis_1_vorlauftemperatur_maximum": "heizkreis_1",
  "number.heizkreis_1_raumkorrektur": "heizkreis_1",
  "number.heizkreis_1_sommer_badheizen_puffer_mindesttemperatur": "heizkreis_1",
  "number.heizkreis_1_sommer_badheizen_vorlauf_soll": "heizkreis_1",
  "number.boiler_1_boilertemperatur_schaltdifferenz": "warmwasser",
  "number.boiler_1_boilertemperatur_minimum": "warmwasser",
  "number.boiler_1_energiesparmodus_nach_der_dauer": "warmwasser",
  "number.boiler_1_maximale_pumpenlaufzeit": "warmwasser",
  "number.zentralpuffer_mindesttemperatur_oben": "puffer",
  "number.pufferzwangsladung_aussentemperaturgrenze": "puffer",
  "number.pelletvorrat_einstellen": "pelletslager",
  "select.urlaubsschaltung": "steuerung",
  "select.heizkreis_1_urlaubsschaltung": "heizkreis_1",
  "select.heizkreis_2_urlaubsschaltung": "heizkreis_2",
  "select.heizkreis_2_sommer_badheizen": "heizkreis_2",
  "select.heizkreis_1_sommer_badheizen": "heizkreis_1",
  "select.boiler_1_energiesparmodus": "warmwasser",
  "select.zentralpuffer_solar_fremdwaermebetrieb_aktivieren": "puffer",
  "sensor.boilerstatus": "warmwasser",
  "sensor.kesselstatus": "kessel",
  "sensor.solar_zieltemperatur": "solar",
  "sensor.solar_quellentemperatur": "solar",
  "sensor.heizkreis_1_status": "heizkreis_1",
  "sensor.heizkreis_2_status": "heizkreis_2",
  "sensor.lagerstand": "pelletslager",
  "sensor.gemeldete_leistung": "kessel",
  "sensor.restsauerstoff": "kessel",
  "sensor.restsauerstoff_sollwert": "kessel",
  "sensor.puffer_fuellgrad": "puffer",
  "sensor.puffer_hoechste_anforderung": "puffer",
  "sensor.puffer_solltemperatur_oben": "puffer",
  "sensor.puffer_solltemperatur_unten": "puffer",
  "sensor.pufferstatus": "puffer",
  "sensor.solar_gemeldete_leistung": "solar",
  "sensor.solarpumpe_1_ansteuerung": "solar",
  "sensor.solar_tagesenergie": "solar",
  "sensor.aussentemperatur_gemittelt": "steuerung",
  "sensor.boiler_1_boilertemperatur": "warmwasser",
  "sensor.kesseltemperatur": "kessel",
  "sensor.kessel_solltemperatur": "kessel",
  "sensor.pufferfuehler_oben": "puffer",
  "sensor.pufferfuehler_unten": "puffer",
  "sensor.heizkreis_1_raumtemperatur": "heizkreis_1",
  "sensor.rauchgastemperatur": "kessel",
  "sensor.ruecklauftemperatur": "kessel",
  "sensor.ruecklauf_solltemperatur": "kessel",
  "sensor.heizkreis_1_interner_regelsollwert": "heizkreis_1",
  "sensor.heizkreis_2_interner_regelsollwert": "heizkreis_2",
  "sensor.heizkreis_1_vorlauftemperatur": "heizkreis_1",
  "sensor.heizkreis_2_vorlauftemperatur": "heizkreis_2",
  "sensor.heizkreis_1_vorlauf_solltemperatur": "heizkreis_1",
  "sensor.heizkreis_2_vorlauf_solltemperatur": "heizkreis_2",
  "sensor.aussentemperatur": "steuerung",
  "sensor.kesselstatus_code": "kessel",
  "text.urlaubszeit_von": "steuerung",
  "text.heizkreis_1_urlaubszeit_von": "heizkreis_1",
  "text.heizkreis_2_urlaubszeit_von": "heizkreis_2",
  "text.urlaubszeit_bis": "steuerung",
  "text.heizkreis_1_urlaubszeit_bis": "heizkreis_1",
  "text.heizkreis_2_urlaubszeit_bis": "heizkreis_2",
  "text.name_heizkreis_1": "heizkreis_1",
  "text.name_heizkreis_2": "heizkreis_2",
  "text.name_boiler_1": "warmwasser",
  "text.name_zentralpuffer": "puffer",
  "text.name_pwm_diffreg_kreis_1": "solar"
});
const DEVICE_ENTITY_SUFFIXES = Object.entries(DEVICE_ENTITY_GROUPS).map(([id, group]) => {
  const [domain, slug] = id.split(".");
  return { domain, suffix: `_${domain}_${slug}`, group };
});

export function installHargassnerDeviceGroups(environment = window) {
  const prior = environment[DEVICE_GROUP_KEY];
  if (prior?.running && prior.version === DEVICE_GROUP_VERSION) return prior;
  if (prior?.running) prior.stop();
  const { document, customElements, MutationObserver } = environment;
  if (!document || !customElements || !MutationObserver) return null;
  let running = true, prototype, originalDescriptor, original, wrapped;
  const tracked = new Map();

  function forget(host) {
    tracked.get(host)?.disconnect();
    tracked.delete(host);
  }
  function parent(node) { return node?.parentNode || node?.host || null; }
  function page(host, deviceId) {
    let node = host;
    for (let depth = 0; node && depth < 64; depth++, node = parent(node)) {
      if (node.localName === "ha-config-device-page") return node.deviceId === deviceId;
    }
    return false;
  }
  function scoped(host, entries) {
    if (!host.isConnected || host.localName !== "ha-device-entities-card" ||
        !Array.isArray(host.entities) || !host.entities.length || host.entities.length > 512 ||
        !Array.isArray(entries) || entries.some(entry => !host.entities.includes(entry))) return false;
    const deviceId = host.entities[0]?.device_id;
    const device = host.hass?.devices?.[deviceId];
    return typeof deviceId === "string" && page(host, deviceId) &&
      device?.manufacturer === "Hargassner" && device.model === "Nano-PK" &&
      Array.isArray(device.identifiers) && device.identifiers.some(value =>
        Array.isArray(value) && value[0] === "mqtt" && typeof value[1] === "string" && value[1].startsWith("hargassner_")) &&
      host.entities.every(entry => entry && entry.device_id === deviceId && entry.platform === "mqtt" &&
        typeof entry.entity_id === "string" && typeof entry.unique_id === "string" && entry.unique_id.startsWith("hargassner_"));
  }
  function group(entry) {
    const domain = entry.entity_id.split(".")[0];
    const matches = DEVICE_ENTITY_SUFFIXES.filter(value => value.domain === domain && entry.unique_id.endsWith(value.suffix));
    return matches.length === 1 ? matches[0].group : "steuerung";
  }
  function track(host) {
    let observer = tracked.get(host);
    if (!observer) {
      observer = new MutationObserver(() => {
        if (!host.isConnected) forget(host);
      });
      tracked.set(host, observer);
    } else observer.disconnect();
    // Watch direct ancestor child lists only, including shadow boundaries.
    // No observer watches all document descendants or moves native row nodes.
    let node = host;
    for (let depth = 0; node && depth < 64; depth++, node = parent(node)) {
      if (node.parentNode) observer.observe(node.parentNode, { childList: true });
    }
  }
  function update(host) {
    if (host.isConnected && typeof host.requestUpdate === "function") host.requestUpdate();
  }
  function scanOnce() {
    const roots = [document];
    let elements = 0, rootCount = 0;
    while (roots.length && rootCount++ < 128 && elements < 12000) {
      const root = roots.pop();
      for (const node of root.querySelectorAll("*")) {
        if (++elements > 12000) break;
        if (node.localName === "ha-device-entities-card") update(node);
        if (node.shadowRoot) roots.push(node.shadowRoot);
      }
    }
  }
  const controller = {
    version: DEVICE_GROUP_VERSION,
    get running() { return running; },
    status: () => ({ running, activeCards: tracked.size }),
    stop() {
      if (!running) return;
      running = false;
      if (prototype && prototype._renderEntities === wrapped) {
        if (originalDescriptor) Object.defineProperty(prototype, "_renderEntities", originalDescriptor);
        else delete prototype._renderEntities;
      }
      for (const host of [...tracked.keys()]) { forget(host); update(host); }
      environment.removeEventListener("pagehide", controller.stop);
    },
    ready: null,
  };
  environment[DEVICE_GROUP_KEY] = controller;
  environment.hargassnerDeviceGroups = controller;
  environment.addEventListener("pagehide", controller.stop);
  controller.ready = customElements.whenDefined("ha-device-entities-card").then(() => {
    if (!running) return;
    prototype = customElements.get("ha-device-entities-card")?.prototype;
    original = prototype?._renderEntities;
    if (typeof original !== "function" || original.length !== 1 ||
        typeof prototype._renderEntity !== "function" || typeof prototype._renderUnavailableEntity !== "function") return;
    originalDescriptor = Object.getOwnPropertyDescriptor(prototype, "_renderEntities");
    wrapped = function (entries) {
      if (!running || !scoped(this, entries)) {
        forget(this);
        return original.call(this, entries);
      }
      if (!entries.length) return original.call(this, entries);
      const groups = new Map(Object.keys(DEVICE_GROUP_LABELS).map(key => [key, []]));
      for (const entry of entries) groups.get(group(entry)).push(entry);
      track(this);
      const output = [];
      for (const [key, members] of groups) {
        if (!members.length) continue;
        const heading = document.createElement("div");
        heading.dataset.hargassnerDeviceGroup = key;
        heading.setAttribute("role", "heading"); heading.setAttribute("aria-level", "3");
        heading.style.cssText = "font-weight:500;color:var(--secondary-text-color);padding:16px 8px 8px;";
        heading.textContent = DEVICE_GROUP_LABELS[key];
        output.push(heading, original.call(this, members));
      }
      return output;
    };
    try {
      Object.defineProperty(prototype, "_renderEntities", { configurable: true, writable: true, value: wrapped });
      scanOnce();
    } catch { controller.stop(); }
  });
  return controller;
}

if (typeof window !== "undefined") installHargassnerDeviceGroups(window);
