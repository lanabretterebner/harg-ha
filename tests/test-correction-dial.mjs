/** Synthetic stub-DOM pointer/format checks for the correction dial and the card mode lock; no HA, browser or network. */
import assert from 'node:assert/strict';
import { createHargassnerCorrectionControl } from '../dist/hargassner-thermostat-popup.js';
import { fixture, state, ENTITY } from './optimistic-card-fixture.mjs';

class Element {
  constructor(tag) { this.localName = tag; this.attributes = new Map(); this.children = []; this.listeners = new Map(); this.style = { cssText: '' }; this.dataset = {}; this.textContent = ''; this.hidden = false; this.disabled = false; this.tabIndex = -1; this.captured = [] }
  setAttribute(k, v) { this.attributes.set(k, String(v)) }
  getAttribute(k) { return this.attributes.has(k) ? this.attributes.get(k) : null }
  removeAttribute(k) { this.attributes.delete(k) }
  append(...nodes) { this.children.push(...nodes) }
  remove() {}
  get isConnected() { return true }
  addEventListener(type, fn) { this.listeners.set(type, [...(this.listeners.get(type) || []), fn]) }
  dispatch(type, event) { for (const fn of this.listeners.get(type) || []) fn({ preventDefault() {}, ...event }) }
  setPointerCapture(id) { this.captured.push(id) }
  getBoundingClientRect() { return { left: 0, top: 0, width: 180, height: 155 } }
}
const ID = 'synthetic_correction_1', NUMBER = 'number.synthetic_hk1_rk';
const climate = () => ({ entity_id: 'climate.synthetic_hc1', state: 'auto', attributes: { hargassner_control_schema: 'configured_room_setpoint_v1', hargassner_correction_id: ID,
  hvac_modes: ['off', 'auto', 'heat'], preset_modes: ['none', 'Aus', 'Auto', 'Heizen', 'Absenken', '1x Heizen', '1x Absenken'], preset_mode: 'Auto',
  supported_features: 384, temperature: null, min_temp: 16, max_temp: 22, target_temp_step: .5, 'Zieltemperatur verfügbar': false } });
const number = value => ({ entity_id: NUMBER, state: value, last_updated: new Date(Date.now() - 10000).toISOString(),
  attributes: { hargassner_control_schema: 'room_correction_v1', hargassner_correction_id: ID, min: -3, max: 3, step: .5, unit_of_measurement: '°C' } });
function dial(value = '-2.5') {
  const timers = new Map(), calls = []; let timer = 0;
  const environment = { document: { createElement: tag => new Element(tag), createElementNS: (_, tag) => new Element(tag) },
    setTimeout(fn) { timers.set(++timer, fn); return timer }, clearTimeout(id) { timers.delete(id) } };
  const c = climate(), hass = { connected: true, connection: { connected: true }, user: { id: 'synthetic' }, states: { [c.entity_id]: c, [NUMBER]: number(value) },
    callService: (...args) => { calls.push(args); return Promise.resolve() } };
  const control = createHargassnerCorrectionControl(environment, () => ({ hass, state: c, connected: true }));
  control.render();
  const slider = control.node.children.find(node => node.getAttribute('role') === 'slider');
  const flush = () => { const pending = [...timers.values()]; timers.clear(); pending.forEach(fn => fn()) };
  // Client coordinates of a dial viewBox point (centre 110,100; ring radius 85).
  const at = (x, y) => ({ button: 0, pointerId: 1, clientX: x / 220 * 180, clientY: y / 190 * 155 });
  const polar = (degrees, radius = 85) => at(110 + radius * Math.cos(degrees * Math.PI / 180), 100 + radius * Math.sin(degrees * Math.PI / 180));
  const tap = point => { slider.dispatch('pointerdown', point); slider.dispatch('pointerup', point); flush() };
  return { control, slider, calls, flush, at, polar, tap, text: () => slider.getAttribute('aria-valuetext') };
}
let count = 0;
async function test(name, fn) { await fn(); count++; console.log('PASS ' + name) }

// Screen angles: 90° is straight down (centre of the open gap), 135° the -3 end, 45° the +3 end.
await test('tap just below the dial centre (value text) writes nothing', () => {
  const d = dial(); d.tap({ button: 0, pointerId: 1, clientX: 90, clientY: 95 });
  assert.deepEqual(d.calls, []); assert.deepEqual(d.slider.captured, []); assert.equal(d.text(), '-2,5 °C');
});
for (const [degrees, expected] of [[134, -3], [100, -3], [91, -3], [89, 3], [60, 3], [46, 3]])
  await test(`gap tap at ${degrees}° snaps to nearer end ${expected}`, () => {
    const d = dial(); d.tap(d.polar(degrees));
    assert.deepEqual(d.calls, [['number', 'set_value', { entity_id: NUMBER, value: expected }]]);
  });
await test('dragging slightly past -3 stays at -3 instead of jumping to +3', () => {
  const d = dial(); d.slider.dispatch('pointerdown', d.polar(150));
  for (const degrees of [140, 135, 130, 120]) d.slider.dispatch('pointermove', d.polar(degrees));
  assert.equal(d.text(), '-3 °C'); d.slider.dispatch('pointerup', d.polar(120)); d.flush();
  assert.deepEqual(d.calls, [['number', 'set_value', { entity_id: NUMBER, value: -3 }]]);
});
await test('drag release over the centre keeps the last ring value', () => {
  const d = dial(); d.slider.dispatch('pointerdown', d.polar(-90));
  d.slider.dispatch('pointermove', d.at(110, 100)); assert.equal(d.text(), '0 °C');
  d.slider.dispatch('pointerup', d.at(110, 100)); d.flush();
  assert.deepEqual(d.calls, [['number', 'set_value', { entity_id: NUMBER, value: 0 }]]);
});
await test('arc values remain exact half-degree steps from -3 to +3', () => {
  const d = dial('0');
  for (const [degrees, expected] of [[135, -3], [180, -2], [270, 0], [360, 2], [405, 3]]) {
    d.slider.dispatch('pointerdown', d.polar(degrees)); assert.equal(d.slider.getAttribute('aria-valuenow'), String(expected)); d.slider.dispatch('pointercancel', {});
  }
  assert.deepEqual(d.calls, []);
});
await test('near-zero pointer value and -0 readback display 0 °C, never -0', () => {
  assert.equal(dial('-0').text(), '0 °C');
  const d = dial('-0.5'); d.slider.dispatch('pointerdown', d.polar(268)); assert.equal(d.text(), '0 °C'); assert.equal(d.slider.getAttribute('aria-valuenow'), '0');
  d.slider.dispatch('pointerup', d.polar(268)); d.flush();
  assert.equal(Object.is(d.calls[0][2].value, -0), false); assert.equal(d.calls[0][2].value, 0);
});

await test('card mode lock clears when the climate becomes unavailable during set_preset_mode', async () => {
  const f = fixture(); let settle;
  f.card._hass.callService = (...args) => { f.calls.push(args); return new Promise(resolve => { settle = resolve }) };
  const menu = (() => { const m = { isConnected: true, disabled: false, removeEventListener() {}, remove() {} }; return m })();
  const binding = { menu, entity: ENTITY, handler() {} }; f.card._binding = binding;
  const select = (b, value) => f.card._selectMode({ stopPropagation() {}, detail: { item: { value } }, currentTarget: b.menu }, b);
  const inflight = select(binding, 'Heizen'); assert.ok(f.card._busy);
  f.update({ entity_id: ENTITY, state: 'unavailable', attributes: { friendly_name: 'HK1' } });
  assert.equal(f.card._binding, null);
  settle(); await inflight;
  assert.equal(!!f.card._busy, false, 'six-mode lock stuck after unavailable state');
  f.card._hass.states[ENTITY] = state();
  const next = { menu: { isConnected: true, disabled: false, removeEventListener() {}, remove() {} }, entity: ENTITY, handler() {} };
  f.card._binding = next; Object.getPrototypeOf(f.card)._renderMenu.call(f.card, state());
  assert.equal(next.menu.disabled, false);
  f.card._hass.callService = async (...args) => { f.calls.push(args) };
  f.card._renderMenu = () => {}; await select(next, 'Absenken');
  assert.deepEqual(f.calls.map(call => call[2].preset_mode), ['Heizen', 'Absenken']);
});
await test('a stale call cannot clear the lock of a newer in-flight call after remount', async () => {
  const f = fixture(), pending = [];
  f.card._hass.callService = (...args) => { f.calls.push(args); return new Promise(resolve => pending.push(resolve)) };
  const make = () => ({ menu: { isConnected: true, disabled: false, removeEventListener() {}, remove() {} }, entity: ENTITY, handler() {} });
  const select = (b, value) => f.card._selectMode({ stopPropagation() {}, detail: { item: { value } }, currentTarget: b.menu }, b);
  const first = make(); f.card._binding = first; const one = select(first, 'Heizen');
  f.card._busy = false; const second = make(); f.card._binding = second; const two = select(second, 'Absenken'); // models _dispose/rebuild reset
  pending[0](); await one; assert.ok(f.card._busy, 'old call released newer lock');
  pending[1](); await two; assert.equal(!!f.card._busy, false);
});
console.log(JSON.stringify({ scope: 'synthetic stub DOM correction dial pointer/format and card mode lock; no service or network', tests: count, passed: true }));
