/** Timer/ownership/context protocol model only; no browser or native HA test. */
import fs from "node:fs";
import vm from "node:vm";
import assert from "node:assert/strict";
const source = fs.readFileSync(new URL("./installed-native-probe.js", import.meta.url), "utf8");
const bodies = JSON.parse(fs.readFileSync(new URL("./ha-native-bodies.json", import.meta.url), "utf8"));
const nativeDebounce = new Function("func", "wait", "immediate", "window", bodies.native_debounce_body
  .replace("let timeout: number | undefined;", "let timeout;").replace("let trailingArgs: T | undefined;", "let trailingArgs;")
  .replace("(...args: T): void =>", "(...args) =>"));
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
function fixture({ slider = true, extraContext, callDelayMs = 0, dropCall = false } = {}) {
  const contexts = {}, denied = [], observers = [];
  class Element {
    constructor(name) { this.localName = name; this.children = []; this.style = {}; this.dataset = {}; this.events = {}; this.parentNode = null; this.isConnected = false;
      if (name === "ha-state-control-climate-temperature") {
        this.shadowRoot = new Element("shadow");
        this._callService = () => {
          if (!dropCall) this._api.callService("climate", "set_temperature", { entity_id: this.stateObj.entity_id, temperature: this._targetTemperature.value });
        };
        this._debouncedCallService = nativeDebounce(type => this._callService(type), 1000, false, { setTimeout, clearTimeout });
        if (slider) {
          const node = new Element("ha-control-circular-slider");
          node.dispatchEvent = event => { this._targetTemperature.value = event.detail.value;
            if (callDelayMs) setTimeout(() => this._callService("value"), callDelayMs); else this._callService("value"); };
          this.shadowRoot.append(node);
        }
      }
    }
    set stateObj(state) { this._state = state; this._targetTemperature = { value: state.attributes.temperature }; }
    get stateObj() { return this._state; }
    addEventListener(name, fn) { this.events[name] = fn; }
    append(child) { this.children.push(child); child.parentNode = this; if (this.isConnected) connect(child); }
    remove() { if (this.parentNode) this.parentNode.children = this.parentNode.children.filter(node => node !== this); this.parentNode = null; this.isConnected = false; }
    querySelectorAll(selector) {
      const found = [];
      for (const child of this.children) {
        if (selector === "*" || selector.split(",").includes(child.localName)) found.push(child);
        found.push(...child.querySelectorAll(selector));
      }
      return found;
    }
  }
  function connect(element) {
    element.isConnected = true;
    if (element.localName === "ha-state-control-climate-temperature") {
      for (const name of ["hassConfig", "hassInternationalization", "hassFormatters", ...(extraContext ? [extraContext] : [])]) {
        let parent = element.parentNode;
        while (parent && !parent.events["context-request"]) parent = parent.parentNode;
        parent?.events["context-request"]({ context: name, subscribe: true, stopPropagation() {}, callback(value) { contexts[name] = value; } });
      }
    }
    for (const child of [...element.children]) connect(child);
  }
  class Observer { constructor(fn) { this.fn = fn; this.disconnected = false; observers.push(this); } observe() {} disconnect() { this.disconnected = true; } }
  const body = new Element("body"); body.isConnected = true;
  const document = { body, createElement: name => new Element(name), querySelector: () => { denied.push("real-document-query"); throw Error("No real HA lookup allowed"); } };
  const window = { hargassnerThermostatPopup: { version: "1.4.3", running: true } };
  const context = vm.createContext({ window, document, customElements: { get: () => class {} }, MutationObserver: Observer,
    CustomEvent: class { constructor(type, options) { this.type = type; Object.assign(this, options); } },
    setTimeout, clearTimeout, Date, Symbol, WeakSet, Map, Set, Object, Number, String, Promise,
    requestAnimationFrame() { throw Error("RAF is forbidden"); } });
  vm.runInContext(source, context);
  const start = options => context.startHargassnerNativeProbeV2({ installedVersion: "1.4.3", verifiedResourceSha256: "d".repeat(64), timeoutMs: 300, ...options });
  return { start, window, body, contexts, denied, observers };
}
const results = [];
async function test(name, run) { try { await run(); results.push({ name, passed: true }); } catch (error) { results.push({ name, passed: false, error: error.message }); } }
await test("start returns synchronously; mount-only never invokes service", async () => {
  const f = fixture(), result = f.start(); assert.equal(typeof result?.then, "undefined"); assert.equal(result.running, true);
  await wait(100); const r = f.window.hargassnerNativeProbeV2; assert.equal(r.status, "passed"); assert.equal(r.fakeServiceCalls.length, 0);
  assert.equal(f.body.children.length, 0); assert(f.observers.every(row => row.disconnected)); assert.equal(f.denied.length, 0);
});
await test("exact config and internationalization context shapes are supplied", async () => {
  const f = fixture(); f.start(); await wait(100);
  assert.equal(f.contexts.hassConfig.config.unit_system.temperature, "°C");
  assert.equal(f.contexts.hassInternationalization.locale.language, "de"); assert.equal(typeof f.contexts.hassInternationalization.localize, "function");
  assert.equal(typeof f.contexts.hassFormatters.formatEntityStateToParts, "function");
});
await test("unsupported context blocks and cleans up without losing final stage", async () => {
  const f = fixture({ extraContext: "notARealContext" }); f.start(); await wait(30);
  const r = f.window.hargassnerNativeProbeV2; assert.equal(r.status, "blocked"); assert.equal(r.stage, "finished"); assert.equal(f.body.children.length, 0);
});
await test("missing slider is bounded by watchdog instead of awaiting Lit or RAF", async () => {
  const f = fixture({ slider: false }); f.start({ timeoutMs: 200 }); await wait(260);
  const r = f.window.hargassnerNativeProbeV2; assert.equal(r.status, "timeout"); assert.equal(r.running, false); assert.equal(f.body.children.length, 0);
});
await test("explicit stop cancels owned polling and removes synthetic component", async () => {
  const f = fixture(); f.start(); f.window.hargassnerNativeProbeV2.stop(); await wait(100);
  assert.equal(f.window.hargassnerNativeProbeV2.status, "stopped"); assert.equal(f.window.hargassnerNativeProbeV2.fakeServiceCalls.length, 0); assert.equal(f.body.children.length, 0);
});
await test("second start refuses to displace an owned running probe", () => {
  const f = fixture(); f.start(); assert.throws(() => f.start(), /already running/); f.window.hargassnerNativeProbeV2.stop();
});
await test("one native gesture reaches only literal synthetic API", async () => {
  const f = fixture(); f.start({ exercise: true }); await wait(220);
  const r = f.window.hargassnerNativeProbeV2; assert.equal(r.status, "passed"); assert.equal(r.fakeServiceCalls.length, 1); assert.equal(r.fakeServiceCalls[0].temperature, 22.5); assert.equal(r.realServiceCalls, 0);
});
await test("unverified resource is refused before attaching anything", () => {
  const f = fixture(); assert.throws(() => f.start({ verifiedResourceSha256: "bad" }), /Verified installed/); assert.equal(f.body.children.length, 0);
});
await test("owned mutation budget stops before a callback flood can keep the component mounted", () => {
  const f = fixture(); f.start(); f.observers[0].fn(Array(501).fill({}));
  assert.equal(f.window.hargassnerNativeProbeV2.status, "blocked"); assert.match(f.window.hargassnerNativeProbeV2.reason, /mutation budget/); assert.equal(f.body.children.length, 0);
});
await test("repeated context request budget refuses unbounded resubscription", () => {
  const f = fixture(); f.start(); const listener = f.body.children[0].events["context-request"];
  for (let i = 0; i < 70; i++) listener({ context: "hassConfig", subscribe: false, stopPropagation() {}, callback() {} });
  assert.equal(f.window.hargassnerNativeProbeV2.status, "blocked"); assert.match(f.window.hargassnerNativeProbeV2.reason, /Repeated context/); assert.equal(f.body.children.length, 0);
});
await test("native receipt delayed beyond100ms is polled rather than falsely rejected", async () => {
  const f = fixture({ callDelayMs: 250 }); f.start({ exercise: true, timeoutMs: 1000 }); await wait(420);
  assert.equal(f.window.hargassnerNativeProbeV2.status, "passed"); assert.equal(f.window.hargassnerNativeProbeV2.fakeServiceCalls.length, 1);
});
await test("exact pinned native1000ms debounce completes within bounded receipt allowance", async () => {
  const f = fixture(); f.start({ exercise: true, gesture: "debounced", timeoutMs: 2200 }); await wait(1250);
  const r = f.window.hargassnerNativeProbeV2; assert.equal(r.status, "passed"); assert.equal(r.fakeServiceCalls.length, 1);
  assert(r.progress.find(row => row.stage === "fake_receipt").ms >= 1000);
});
await test("short overall deadline cancels the owned native debounce before its fake callback", async () => {
  const f = fixture(); f.start({ exercise: true, gesture: "debounced", timeoutMs: 200 }); await wait(260);
  const r = f.window.hargassnerNativeProbeV2; assert.equal(r.status, "timeout"); await wait(950); assert.equal(r.fakeServiceCalls.length, 0);
});
await test("missing fake receipt remains a bounded timeout", async () => {
  const f = fixture({ dropCall: true }); f.start({ exercise: true, timeoutMs: 300 }); await wait(360);
  const r = f.window.hargassnerNativeProbeV2; assert.equal(r.status, "timeout"); assert.equal(r.fakeServiceCalls.length, 0); assert.equal(f.body.children.length, 0);
});
await test("unsupported gesture is refused before attaching a component", () => {
  const f = fixture(); assert.throws(() => f.start({ gesture: "unknown" }), /gesture path/); assert.equal(f.body.children.length, 0);
});
console.log(JSON.stringify({ scope: "portable protocol/cleanup model only", tests: results.length, passed: results.filter(row => row.passed).length,
  failed: results.filter(row => !row.passed).length, results }, null, 2));
process.exitCode = results.some(row => !row.passed) ? 1 : 0;
