/* Hargassner thermostat popup, v1.2.0.
 * One native-styled Modus menu replaces both native HVAC/preset menus.
 * Only an explicit user selection forwards a manufacturer mode to HA's
 * existing preset handler. No data fetch, temperature command or mode guess.
 * See POPUP-INSTALL.md for the supported HA DOM contract and manual install.
 */
const VERSION = "1.2.0";
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
    const disabled = !!(hvac.disabled || preset.disabled || context.state.state === "unavailable");
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
    status: () => ({ running, activePopups: records.size, statusPopups: statusRecords.size, queued: queued.size }),
    stop() {
      if (!running) return;
      running = false;
      for (const record of [...records.values()]) { try { restore(record); } catch { /* Keep stopping other records. */ } }
      for (const host of [...statusRecords.keys()]) { try { clearStatus(host); } catch { /* Keep stopping other records. */ } }
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

if (typeof window !== "undefined") installHargassnerThermostatPopup(window);
