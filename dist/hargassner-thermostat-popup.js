/* Hargassner thermostat popup, v1.1.0.
 * One native-styled Modus menu replaces both native HVAC/preset menus.
 * Only an explicit user selection forwards a manufacturer mode to HA's
 * existing preset handler. No data fetch, temperature command or mode guess.
 * See POPUP-INSTALL.md for the supported HA DOM contract and manual install.
 */
const VERSION = "1.1.0";
const SCHEMA = "configured_room_setpoint_v1";
const PRESETS = Object.freeze(["Aus", "Auto", "Heizen", "Absenken", "1x Heizen", "1x Absenken"]);
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
  function schedule(host) {
    if (!running || queued.has(host)) return;
    queued.add(host);
    environment.queueMicrotask(() => {
      queued.delete(host);
      if (!running) return;
      try { reconcile(host); } catch { try { clear(host); } catch { /* Optional UI only. */ } }
    });
  }
  function observe(record) {
    record.observer.disconnect();
    record.observer.observe(record.host.shadowRoot, { childList: true, subtree: true, attributes: true });
    for (const menu of [record.hvac, record.preset, record.modus]) {
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
  function reconcile(host) {
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
      modus.options = PRESETS.map(value => ({ value, label: value }));
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
    status: () => ({ running, activePopups: records.size, queued: queued.size }),
    stop() {
      if (!running) return;
      running = false;
      for (const record of [...records.values()]) { try { restore(record); } catch { /* Keep stopping other records. */ } }
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
