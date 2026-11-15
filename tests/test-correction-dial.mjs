/** Synthetic stub-DOM pointer/format checks for the correction dial and the card mode lock; no HA, browser or network. */
import assert from 'node:assert/strict';
import { createHargassnerCorrectionControl } from '../dist/hargassner-thermostat-popup.js';
import { fixture, state, ENTITY } from './optimistic-card-fixture.mjs';

class Element {
  constructor(tag) { this.localName = tag; this.attributes = new Map(); this.children = []; this.listeners = new Map(); this.style = { cssText: '' }; this.dataset = {}; this.textContent = ''; this.hidden = false; this.disabled = false; this.tabIndex = -1; this.captured = []; this.connected = true; this.rect = { left: 0, top: 0, width: 180, height: 155 } }
  setAttribute(k, v) { this.attributes.set(k, String(v)) }
  getAttribute(k) { return this.attributes.has(k) ? this.attributes.get(k) : null }
  removeAttribute(k) { this.attributes.delete(k) }
  append(...nodes) { this.children.push(...nodes) }
  remove() {}
  get isConnected() { return this.connected }
  addEventListener(type, fn) { this.listeners.set(type, [...(this.listeners.get(type) || []), fn]) }
  dispatch(type, event) { for (const fn of this.listeners.get(type) || []) fn({ preventDefault() {}, ...event }) }
  setPointerCapture(id) { this.captured.push(id) }
  getBoundingClientRect() { return this.rect }
}
const ID = 'synthetic_correction_1', NUMBER = 'number.synthetic_hk1_rk';
const climate = () => ({ entity_id: 'climate.synthetic_hc1', state: 'auto', attributes: { hargassner_control_schema: 'configured_room_setpoint_v1', hargassner_correction_id: ID,
  hvac_modes: ['off', 'auto', 'heat'], preset_modes: ['none', 'Aus', 'Auto', 'Heizen', 'Absenken', '1x Heizen', '1x Absenken'], preset_mode: 'Auto',
  supported_features: 384, temperature: null, min_temp: 16, max_temp: 22, target_temp_step: .5, 'Zieltemperatur verfügbar': false } });
const number = value => ({ entity_id: NUMBER, state: value, last_updated: new Date(Date.now() - 10000).toISOString(),
  attributes: { hargassner_control_schema: 'room_correction_v1', hargassner_correction_id: ID, min: -3, max: 3, step: .5, unit_of_measurement: '°C' } });
// One synthetic HA session; every surface() is a further control for the same entity and shares its request model.
function world(value) {
  const timers = new Map(), calls = []; let timer = 0;
  const environment = { document: { createElement: tag => new Element(tag), createElementNS: (_, tag) => new Element(tag) },
    setTimeout(fn) { timers.set(++timer, fn); return timer }, clearTimeout(id) { timers.delete(id) } };
  const c = climate(), hass = { connected: true, connection: { connected: true }, user: { id: 'synthetic' }, states: { [c.entity_id]: c, [NUMBER]: number(value) },
    callService: (...args) => { calls.push(args); return Promise.resolve() } };
  // Runs the timers due now; a timer started meanwhile (the 8 s confirmation limit) stays queued.
  const flush = () => { const pending = [...timers.values()]; timers.clear(); pending.forEach(fn => fn()) };
  // Client coordinates of a dial viewBox point (centre 110,100; ring radius 85).
  const at = (x, y) => ({ button: 0, pointerId: 1, clientX: x / 220 * 180, clientY: y / 190 * 155 });
  const polar = (degrees, radius = 85) => at(110 + radius * Math.cos(degrees * Math.PI / 180), 100 + radius * Math.sin(degrees * Math.PI / 180));
  const surface = () => {
    const control = createHargassnerCorrectionControl(environment, () => ({ hass, state: c, connected: true }));
    control.render();
    const slider = control.node.children.find(node => node.getAttribute('role') === 'slider');
    const [minus, plus] = control.node.children.find(node => node.className === 'correction-stepper').children;
    const tap = point => { slider.dispatch('pointerdown', point); slider.dispatch('pointerup', point); flush() };
    return { control, slider, minus, plus, note: control.node.children.at(-1), tap, text: () => slider.getAttribute('aria-valuetext') };
  };
  const readback = next => { hass.states[NUMBER] = { ...number(next), last_updated: new Date(Date.now() + 1000).toISOString() } };
  return { calls, flush, at, polar, surface, readback, sent: () => calls.map(call => call[2].value) };
}
function dial(value = '-2.5') { const w = world(value); return { ...w, ...w.surface() } }
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
// A drag that leaves the arc through an end keeps that end across the whole gap, also past straight-down.
for (const [start, press, moves, end] of [['2', 22.5, [35, 45, 60, 80, 89, 91, 100, 120, 134], 3], ['-2', 157.5, [145, 135, 120, 100, 91, 89, 80, 60, 46], -3]])
  await test(`drag overshooting ${end} across the bottom centre stays at ${end}`, () => {
    const d = dial(start), label = `${end > 0 ? '+' : ''}${end} °C`; d.slider.dispatch('pointerdown', d.polar(press));
    for (const degrees of moves) { d.slider.dispatch('pointermove', d.polar(degrees)); if (degrees > 45 && degrees < 135) assert.equal(d.text(), label, `${degrees}°`) }
    d.slider.dispatch('pointerup', d.polar(moves.at(-1))); d.flush();
    assert.deepEqual(d.calls, [['number', 'set_value', { entity_id: NUMBER, value: end }]]);
  });
for (const [press, moves, end] of [[60, [80, 89, 91, 110, 134], 3], [120, [100, 91, 89, 70, 46], -3]])
  await test(`drag begun by a gap press at ${press}° keeps ${end} on the other half of the gap`, () => {
    const d = dial('0'); d.slider.dispatch('pointerdown', d.polar(press));
    for (const degrees of moves) { d.slider.dispatch('pointermove', d.polar(degrees)); assert.equal(d.slider.getAttribute('aria-valuenow'), String(end), `${degrees}°`) }
    d.slider.dispatch('pointerup', d.polar(moves.at(-1))); d.flush();
    assert.deepEqual(d.sent(), [end]);
  });
await test('one coarse move from an inner value into the gap takes the end on its shorter way', () => {
  for (const [start, degrees, end] of [[22.5, 100, 3], [157.5, 80, -3], [-67.5, 105, 3], [-112.5, 75, -3]]) {
    const d = dial('0'); d.slider.dispatch('pointerdown', d.polar(start)); d.slider.dispatch('pointermove', d.polar(degrees));
    assert.equal(d.slider.getAttribute('aria-valuenow'), String(end), `${start}° → ${degrees}°`); d.slider.dispatch('pointercancel', {});
    assert.deepEqual(d.calls, []);
  }
});
// The dial does not wrap: back on the ring beyond the gap, the drag keeps its end until the pointer is nearer to it along the ring.
for (const [start, press, moves, end] of [['2', 22.5, [45, 90, 134, 140, 170, 220], 3], ['-2', 157.5, [135, 90, 46, 40, 10, -40], -3]])
  await test(`drag crossing the whole gap onto the far ring stays at ${end}`, () => {
    const d = dial(start), label = `${end > 0 ? '+' : ''}${end} °C`; d.slider.dispatch('pointerdown', d.polar(press));
    for (const degrees of moves) { d.slider.dispatch('pointermove', d.polar(degrees)); assert.equal(d.text(), label, `${degrees}°`) }
    d.slider.dispatch('pointerup', d.polar(moves.at(-1))); d.flush();
    assert.deepEqual(d.sent(), [end]);
  });
await test('a drag held at an end beyond the gap follows the pointer again once it is nearer along the ring', () => {
  for (const [start, press, far, near, value] of [['2', 22.5, 170, 240, '-0,5 °C'], ['-2', 157.5, 10, -60, '+0,5 °C']]) {
    const d = dial(start); d.slider.dispatch('pointerdown', d.polar(press));
    for (const degrees of [90, far]) d.slider.dispatch('pointermove', d.polar(degrees));
    d.slider.dispatch('pointermove', d.polar(near)); assert.equal(d.text(), value, `${press}° → ${near}°`);
    d.slider.dispatch('pointercancel', {}); assert.deepEqual(d.calls, []);
  }
});
await test('a press on the ring selects its own value whatever the shown value', () => {
  for (const [start, degrees, expected] of [['3', 140, -3], ['-3', 40, 3], ['3', 180, -2], ['-3', 360, 2]]) {
    const d = dial(start); d.tap(d.polar(degrees)); assert.deepEqual(d.sent(), [expected], `${start} → ${degrees}°`);
  }
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
await test('zero-size or non-finite dial rect selects nothing during a captured drag', () => {
  for (const rect of [{ left: 0, top: 0, width: 0, height: 0 }, { left: 0, top: 0, width: 180, height: 0 }, { left: NaN, top: NaN, width: NaN, height: NaN }, { left: 0, top: 0, width: Infinity, height: Infinity }]) {
    const d = dial('0'); d.slider.dispatch('pointerdown', d.polar(0)); assert.equal(d.text(), '+2 °C');
    d.slider.rect = rect;
    for (const point of [{ clientX: 40, clientY: 40 }, { clientX: 0, clientY: 0 }, { clientX: 170, clientY: 150 }]) { d.slider.dispatch('pointermove', { pointerId: 1, ...point }); assert.equal(d.text(), '+2 °C') }
    d.slider.dispatch('pointerup', { pointerId: 1, clientX: 40, clientY: 40 }); d.flush();
    assert.deepEqual(d.sent(), [2], JSON.stringify(rect));
    const fresh = dial('0'); fresh.slider.rect = rect; fresh.tap({ button: 0, pointerId: 1, clientX: 40, clientY: 40 });
    assert.deepEqual(fresh.calls, []); assert.deepEqual(fresh.slider.captured, []); assert.equal(fresh.text(), '0 °C');
  }
  const d = dial('0'); d.slider.dispatch('pointerdown', d.polar(0));
  for (const point of [{ clientX: NaN, clientY: 40 }, { clientX: 40, clientY: Infinity }]) d.slider.dispatch('pointermove', { pointerId: 1, ...point });
  assert.equal(d.text(), '+2 °C'); d.slider.dispatch('pointercancel', {}); assert.deepEqual(d.calls, []);
});

await test("a second surface's pending request discards this surface's unsent drag draft", async () => {
  const w = world('0'), a = w.surface(), b = w.surface();
  a.slider.dispatch('pointerdown', w.polar(0)); assert.equal(a.text(), '+2 °C');
  b.plus.dispatch('click', {}); w.flush(); assert.deepEqual(w.sent(), [.5]);
  assert.equal(a.text(), '+0,5 °C'); assert.equal(a.note.textContent, 'Angefragt · Bestätigung ausstehend'); assert.equal(a.plus.disabled, true);
  a.slider.dispatch('pointermove', w.polar(22.5)); a.slider.dispatch('pointerup', w.polar(22.5)); w.flush();
  assert.equal(a.text(), '+0,5 °C'); assert.equal(b.text(), '+0,5 °C'); assert.deepEqual(w.sent(), [.5]);
  w.readback('0.5'); a.control.render(); b.control.render();
  assert.equal(a.text(), '+0,5 °C'); assert.equal(a.note.textContent, '');
  a.plus.dispatch('click', {}); w.flush();
  assert.deepEqual(w.sent(), [.5, 1]);
});
await test('a release that cannot be sent discards its draft instead of leaving it displayed', () => {
  const d = dial('0'); d.slider.dispatch('pointerdown', d.polar(0)); assert.equal(d.text(), '+2 °C');
  d.control.node.connected = false; d.slider.dispatch('pointerup', d.polar(0)); d.flush();
  assert.deepEqual(d.calls, []); assert.equal(d.text(), '0 °C');
  d.control.node.connected = true; d.control.render(); assert.equal(d.text(), '0 °C');
  d.plus.dispatch('click', {}); d.flush();
  assert.deepEqual(d.sent(), [.5]);
});
await test('plus and minus step from the real or pending value, never from an unscheduled drag draft', () => {
  const d = dial('0'); d.slider.dispatch('pointerdown', d.polar(0)); assert.equal(d.text(), '+2 °C');
  d.plus.dispatch('click', {}); assert.equal(d.text(), '+0,5 °C');
  d.plus.dispatch('click', {}); assert.equal(d.text(), '+1 °C'); // scheduled steps still coalesce
  d.flush(); assert.deepEqual(d.sent(), [1]);
  d.slider.dispatch('pointermove', d.polar(22.5)); d.slider.dispatch('pointerup', d.polar(22.5)); d.flush();
  assert.equal(d.text(), '+1 °C'); assert.deepEqual(d.sent(), [1]); // the sent step ended the drag
  const w = world('1'), a = w.surface(); a.minus.dispatch('click', {}); a.minus.dispatch('click', {}); a.minus.dispatch('click', {}); w.flush();
  assert.deepEqual(w.sent(), [-.5]);
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
