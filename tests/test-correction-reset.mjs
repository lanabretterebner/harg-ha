/** "Korrektur ±0" for a qualified target (card and shared helper); synthetic model, no HA, browser or network. */
import assert from 'node:assert/strict';
import { correctionResetTarget, requestCorrectionReset, HargassnerTargetPresentation } from '../dist/hargassner-thermostat-popup.js';
import { fixture, state, envelope, operation, ENTITY } from './optimistic-card-fixture.mjs';
const results = [];
async function test(name, body) { try { await body(); results.push({ name, passed: true }) } catch (error) { results.push({ name, passed: false, error: error.stack }) } }
// Displayed target 19.5 = configured 18 + correction 1.5.
const corrected = (correction = 1.5, temp = 18 + correction, extra = {}) =>
  state(temp, true, { Raumkorrektur: correction, Temperaturauftrag: envelope([], { confirmedTarget: temp }), ...extra });

await test('the reset target is the configured base, only in °C and only on the half-degree grid', () => {
  assert.equal(correctionResetTarget(corrected(1.5), '°C'), 18);
  assert.equal(correctionResetTarget(corrected(-2), '°C'), 18);
  for (const [value, unit] of [[0, '°C'], [1.5, '°F'], [0.3, '°C'], [3.5, '°C']]) assert.equal(correctionResetTarget(corrected(value), unit), null, `${value} ${unit}`);
  assert.equal(correctionResetTarget(state(19, true, { Raumkorrektur: '1.5' }), '°C'), null);
  assert.equal(correctionResetTarget(state(19, false, { Raumkorrektur: 1 }), '°C'), null); // no qualified target
  assert.equal(correctionResetTarget(corrected(1.5, 17.5), '°C'), 16); // the minimum itself is allowed
  assert.equal(correctionResetTarget(corrected(1.5, 17), '°C'), null); // a base below the minimum is never sent
});
await test('the helper writes set_temperature(base) once through the shared intent and refuses while one exists', async () => {
  const presentation = new HargassnerTargetPresentation(), calls = [], s = corrected(1.5);
  const hass = { connected: true, connection: { connected: true }, callService: (...args) => { calls.push(args); return Promise.resolve() } };
  presentation.observe(s, '°C', true);
  const intent = requestCorrectionReset({ hass, state: s, unit: '°C', presentation });
  assert.deepEqual(calls, [['climate', 'set_temperature', { entity_id: ENTITY, temperature: 18 }]]);
  assert.equal(intent.status, 'pending'); assert.equal(intent.submitted, true); assert.equal(presentation.intent, intent);
  assert.equal(requestCorrectionReset({ hass, state: s, unit: '°C', presentation }), null); assert.equal(calls.length, 1);
  // The native display is inert and shows the requested target until a readback.
  const view = presentation.display(s, true); assert.equal(view.inert, true); assert.equal(view.state.attributes.temperature, 18);
  assert.equal(view.note, 'Angefragt · Bestätigung ausstehend');
});
await test('a transport error leaves the reset requested and uncertain, never released', async () => {
  const presentation = new HargassnerTargetPresentation(), s = corrected(1);
  const hass = { connected: true, connection: { connected: true }, callService: () => Promise.reject(new Error('lost')) };
  presentation.observe(s, '°C', true);
  const intent = requestCorrectionReset({ hass, state: s, unit: '°C', presentation }); await Promise.resolve(); await Promise.resolve();
  assert.equal(presentation.intent, intent); assert.equal(intent.status, 'sent_unconfirmed');
  assert.equal(presentation.display(s, true).note, 'Angefragt · Bestätigung unklar');
});
await test('the card offers Korrektur ±0, writes once, stays inert until the coherent readback', async () => {
  const f = fixture(corrected(1.5));
  f.card._ui.reset = { hidden: true }; f.card._ui.error = { textContent: '' };
  f.card._render(); assert.equal(f.card._ui.reset.hidden, false);
  f.card._requestCorrectionReset(); f.card._requestCorrectionReset();
  assert.deepEqual(f.calls, [['climate', 'set_temperature', { entity_id: ENTITY, temperature: 18 }]]);
  assert.equal(f.card._native.inert, true); assert.equal(f.card._ui.reset.hidden, true);
  // A native gesture cannot cross the lock.
  await f.node._api.callService('climate', 'set_temperature', { entity_id: ENTITY, temperature: 20 }); assert.equal(f.calls.length, 1);
  // The same old state again releases nothing.
  f.update(corrected(1.5)); assert.equal(f.card._native.inert, true);
  f.update(state(18, true, { Raumkorrektur: 0, Temperaturauftrag: envelope([operation(1, 'confirmed', 18)], { confirmedTarget: 18 }) }));
  assert.equal(f.card._native.inert, false); assert.equal(f.card._ui.reset.hidden, true); assert.equal(f.calls.length, 1);
});
await test('the card hides Korrektur ±0 for a zero correction, °F, no qualified target or a disconnected HA', () => {
  for (const [initial, change] of [[corrected(0, 18), null], [corrected(1), 'unit'], [state(19, false, { Raumkorrektur: 1 }), null], [corrected(1), 'offline']]) {
    const f = fixture(initial); f.card._ui.reset = { hidden: true }; f.card._ui.error = { textContent: '' };
    if (change === 'unit') f.card._hass.config.unit_system.temperature = '°F';
    if (change === 'offline') f.card._hass.connection.connected = false;
    f.card._render(); assert.equal(f.card._ui.reset.hidden, true, String(change)); f.card._requestCorrectionReset(); assert.equal(f.calls.length, 0);
  }
});
console.log(JSON.stringify({ scope: 'synthetic Korrektur ±0 target path; no HA, browser or network', tests: results.length, passed: results.filter(x => x.passed).length, results: results.filter(x => !x.passed) }, null, 2));
if (results.some(x => !x.passed)) process.exitCode = 1;
