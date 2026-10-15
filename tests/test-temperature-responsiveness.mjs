/** Native gesture bodies + service/debounce fixtures, deterministic elapsed time.
 * This models event handling and leases; it does not claim browser/Lit rendering.
 */
import fs from 'node:fs';
import crypto from 'node:crypto';
import assert from 'node:assert/strict';
import { fixture, state, envelope, operation, FRESH, ENTITY } from './optimistic-card-fixture.mjs';
const bytes = fs.readFileSync(new URL('./ha-native-gesture-bodies.json', import.meta.url));
assert.equal(crypto.createHash('sha256').update(bytes).digest('hex'), '70fdd0475fa0e670cb9b351979c4c1b053ca2bac9b33ca49b59cf8c47e65cad6');
const native = JSON.parse(bytes);
assert.equal(native.source.sha256, 'c5aa68f9857b02ec9200f6f359ebc7d700e8c9b406648a1ae2e1f775f98e80f6');
const erase = body => body.replaceAll(' as Target', '').replaceAll(' as number', '');
const button = new Function('ev', 'clamp', erase(native.bodies._handleButton));
const changing = new Function('ev', erase(native.bodies._valueChanging));
const changed = new Function('ev', erase(native.bodies._valueChanged));
const clamp = (value, min, max) => Math.min(max, Math.max(min, value));
const results = [];
function test(name, run) { try { run(); results.push({name, passed:true}); } catch(error) { results.push({name, passed:false, error:error.stack}); } }
function control(initial=state()) {
  const f = fixture(initial);
  Object.defineProperties(f.node, {
    _min: {get() {return this.stateObj.attributes.min_temp;}},
    _max: {get() {return this.stateObj.attributes.max_temp;}},
  });
  return {...f,
    // Browser inert suppresses pointer/keyboard gestures before the native handler.
    button(step) {if(!f.native.inert)button.call(f.node, {currentTarget:{target:'value',step}}, clamp);},
    changing(value) {changing.call(f.node, {type:'value-changing',detail:{value}});},
    changed(value) {changed.call(f.node, {type:'value-changed',detail:{value}});},
  };
}
test('native button shows requested number synchronously and sends exactly at250ms', () => {
  const f = control(), actual = f.card.hass.states[ENTITY], before = JSON.stringify(actual);
  f.button(.5);
  assert.equal(f.node._targetTemperature.value, 19.5);
  assert.equal(f.node.stateObj.attributes.temperature, 19.5);
  assert.equal(f.card._presentation.intent.submitted, false);
  assert.equal(JSON.stringify(actual), before);
  assert.equal(f.card.hass.states[ENTITY], actual);
  f.advance(249); assert.equal(f.calls.length, 0);
  f.advance(1); assert.equal(f.calls.length, 1);
  assert.equal(f.calls[0][2].temperature, 19.5);
  assert.equal(f.card._presentation.intent.submitted, true);
  assert.equal(JSON.stringify(actual), before);
  f.advance(2000); assert.equal(f.calls.length, 1);
});
test('native +/- burst restarts250ms quiet period and sends only latest target', () => {
  const f = control();
  f.button(.5); f.advance(100); f.button(.5); f.advance(100); f.button(-.5);
  assert.equal(f.node._targetTemperature.value, 19.5);
  assert.equal(f.node.stateObj.attributes.temperature, 19.5);
  f.advance(249); assert.equal(f.calls.length, 0);
  f.advance(1); assert.equal(f.calls.length, 1);
  assert.equal(f.calls[0][2].temperature, 19.5);
  f.advance(2000); assert.equal(f.calls.length, 1);
});
test('native slider keeps live local feedback and sends release immediately', () => {
  const f = control(), actual = f.card.hass.states[ENTITY], before = JSON.stringify(actual);
  f.changing(19.5); assert.equal(f.node._targetTemperature.value, 19.5);
  f.changing(20); assert.equal(f.node._targetTemperature.value, 20);
  assert.equal(f.calls.length, 0); assert.equal(f.timers.size, 0);
  f.changed(20);
  assert.equal(f.node.stateObj.attributes.temperature, 20);
  assert.equal(f.calls.length, 1); assert.equal(f.calls[0][2].temperature, 20);
  assert.equal(JSON.stringify(actual), before);
  f.advance(2000); assert.equal(f.calls.length, 1);
});
test('slider release supersedes pending button timer without duplicate send', () => {
  const f = control();
  f.button(.5); const old = [...f.timers.values()][0]; f.advance(100);
  f.changing(20); f.changed(20);
  assert.equal(f.calls.length, 1); assert.equal(f.calls[0][2].temperature, 20);
  old(); f.advance(2000); assert.equal(f.calls.length, 1);
});
test('native button clamping still respects qualified correction range', () => {
  const f = control();
  for(let i=0;i<20;i++) f.button(.5);
  assert.equal(f.node._targetTemperature.value, 22);
  f.advance(250); assert.equal(f.calls.length, 1); assert.equal(f.calls[0][2].temperature, 22);
});
// This is deliberate serialization after submission, not a queued request.
for (const entity of ['climate.synthetic_hc1','climate.synthetic_hc2']) test(`${entity}250ms send locks350ms follow-on gesture before400ms receipt`, () => {
  const f = control({...state(),entity_id:entity});
  const node=f.node,native=f.native;
  f.button(.5); f.advance(250);
  assert.equal(f.calls.length,1); assert.equal(native.inert,true);
  assert.equal(node._targetTemperature.value,19.5);
  f.advance(100); f.button(.5);
  assert.equal(node._targetTemperature.value,19.5);
  assert.equal(node.stateObj.attributes.temperature,19.5);
  f.advance(50);
  f.card._hass.states[entity]={...state(null,false,{Temperaturauftrag:envelope([operation()])}),entity_id:entity};f.card._render();
  assert.equal(node.stateObj.attributes.temperature,19.5);
  assert.equal(f.card._presentation.failure,false);
  f.advance(2000);assert.equal(f.calls.length,1);
  f.card._hass.states[entity]={...state(19.5,true,{Sollwertbasis:FRESH,Raumkorrektur:.5,Temperaturauftrag:envelope([operation(1,'confirmed')],{confirmedTarget:19.5,confirmedBasis:FRESH})}),entity_id:entity};f.card._render();
  assert.equal(native.inert,false); assert.equal(node,f.node); assert.equal(native,f.card._native);
  f.advance(2000);assert.equal(f.calls.length,1); // no automatic queue/retry
  f.button(.5);f.advance(250);assert.equal(f.calls.length,2);assert.equal(f.calls[1][2].temperature,20);
});
test('submitted request closes current and captured native API and provider paths',()=>{
  const f=control(),raw=f.node.raw,api=f.node._api;
  f.button(.5);f.advance(250);
  const current=f.node._api,call=f.node._callService,debounce=f.node._debouncedCallService;
  for(const provider of [raw,api,current,f.node.raw])provider.callService('climate','set_temperature',{entity_id:ENTITY,temperature:20});
  call.call(f.node,'value');debounce.call(f.node,'value');f.advance(2000);
  assert.equal(f.calls.length,1);assert.equal(f.node.stateObj.attributes.temperature,19.5);
});
test('same-session second card closes admission synchronously before subscription rendering',()=>{
  const a=control(),b=control();b.card._hass.connection=a.card._hass.connection;b.card._hass.states=a.card._hass.states;b.card._render();
  const api=b.node._api,raw=b.node.raw;
  a.button(.5);a.advance(250);
  // Do not process the presentation subscription microtask before attempting.
  b.node._callService('value');b.node._debouncedCallService('value');
  api.callService('climate','set_temperature',{entity_id:ENTITY,temperature:20});
  raw.callService('climate','set_temperature',{entity_id:ENTITY,temperature:20});b.advance(2000);
  assert.equal(a.calls.length,1);assert.equal(b.calls.length,0);
  b.card._render();assert.equal(b.native.inert,true);assert.equal(b.node.stateObj.attributes.temperature,19.5);
});
test('repeated old qualified state and8s elapsed time cannot release submitted lock',()=>{
  const f=control();f.button(.5);f.advance(250);
  f.card._presentation.intent.createdAt-=9000;f.update(state());
  assert.equal(f.native.inert,true);assert.equal(f.card._presentation.intent.status,'sent_unconfirmed');
  f.advance(10000);f.button(.5);assert.equal(f.calls.length,1);
  assert.equal(f.node.stateObj.attributes.temperature,19.5);
});
test('unbound rejected receipt and repeated old confirmed basis cannot release submitted lock',()=>{
  const f=control();f.button(.5);f.advance(250);
  f.update(state(19,true,{Temperaturauftrag:envelope([operation(1,'rejected')])}));
  assert.equal(f.native.inert,true);assert.equal(f.card._presentation.intent.target,19.5);
  assert.equal(f.card._presentation.intent.boundSequence,null);
  f.button(.5);f.advance(2000);assert.equal(f.calls.length,1);assert.equal(f.node.stateObj.attributes.temperature,19.5);
});
test('bound rejection releases lock without automatic resend',()=>{
  const f=control();f.button(.5);f.advance(250);
  f.update(state(19,true,{Temperaturauftrag:envelope([operation()])}));
  assert.equal(f.native.inert,true);
  f.update(state(19,true,{Temperaturauftrag:envelope([operation(1,'rejected')])}));
  assert.equal(f.native.inert,false);assert.equal(f.node.stateObj.attributes.temperature,19);
  f.advance(2000);assert.equal(f.calls.length,1);
  f.button(.5);f.advance(250);assert.equal(f.calls.length,2);
});
test('qualified genuine configuration replacement releases lock with truthful target',()=>{
  const f=control();f.button(.5);f.advance(250);
  f.update({...state(22,true,{preset_mode:'Heizen',Sollwertbasis:FRESH,Raumkorrektur:0,Temperaturauftrag:envelope([],{confirmedTarget:22,confirmedBasis:FRESH,confirmedPreset:'Heizen'})}),state:'heat'});
  assert.equal(f.native.inert,false);assert.equal(f.node.stateObj.attributes.temperature,22);
  f.advance(2000);assert.equal(f.calls.length,1);
});
function uncertainRecovery(f, {entity=ENTITY,basis=FRESH,recordStatus='sent_unconfirmed',recordTarget=19.5,echoBasis=basis,qualified=true}={}) {
  f.card._hass.states[entity]={...state(19,true,{Sollwertbasis:basis,Temperaturauftrag:envelope([operation(1,recordStatus,recordTarget)],{confirmedTarget:19,confirmedBasis:echoBasis})}),entity_id:entity};
  if(!qualified) f.card._hass.connection.connected=false;
  f.card._render();
}
for(const entity of ['climate.synthetic_hc1','climate.synthetic_hc2'])test(`${entity} terminal uncertainty plus fresh coherent different actual target permits explicit new gesture`,()=>{
  const f=control({...state(),entity_id:entity});f.button(.5);f.advance(250);const oldApi=f.node._api;
  f.card._hass.states[entity]={...state(null,false,{Temperaturauftrag:envelope([operation()])}),entity_id:entity};f.card._render();
  uncertainRecovery(f,{entity});
  assert.equal(f.native.inert,false);assert.equal(f.node.stateObj.attributes.temperature,19.5);
  assert.equal(f.card._presentation.intent.target,19.5);assert.match(f.card._ui.updateNote.textContent,/Bestätigung unklar/);
  assert.equal(f.card._hass.states[entity].attributes.temperature,19);
  f.advance(2000);assert.equal(f.calls.length,1); // no automatic follow-up
  oldApi.callService('climate','set_temperature',{entity_id:entity,temperature:20});assert.equal(f.calls.length,1);
  f.button(.5);assert.equal(f.card._presentation.intent.basis,FRESH);assert.equal(f.node.stateObj.attributes.temperature,20);
  f.advance(250);assert.equal(f.calls.length,2);assert.equal(f.calls[1][2].temperature,20);
});
for(const invalid of ['original-basis','split-echo','pending-record','unbound-record','offline'])test(`${invalid} cannot release uncertain submission for another gesture`,()=>{
  const f=control();f.button(.5);f.advance(250);
  if(invalid!=='unbound-record')f.update(state(null,false,{Temperaturauftrag:envelope([operation()])}));
  // Local timeout/transport status must not masquerade as backend termination.
  f.card._presentation.intent.status='sent_unconfirmed';
  uncertainRecovery(f,{basis:invalid==='original-basis'?'a'.repeat(32):FRESH,
    echoBasis:invalid==='split-echo'?'a'.repeat(32):invalid==='original-basis'?'a'.repeat(32):FRESH,
    recordStatus:invalid==='pending-record'?'pending':'sent_unconfirmed',recordTarget:invalid==='unbound-record'?21:19.5,qualified:invalid!=='offline'});
  assert.equal(f.native.inert,true);assert.equal(f.node.stateObj.attributes.temperature,19.5);
  f.button(.5);f.advance(2000);assert.equal(f.calls.length,1);
});
test('a newer pending operation blocks recovered admission before the next render',()=>{
 const f=control();f.button(.5);f.advance(250);f.update(state(null,false,{Temperaturauftrag:envelope([operation()])}));uncertainRecovery(f);
 assert.equal(f.native.inert,false);
 f.card._hass.states[ENTITY]=state(19,true,{Sollwertbasis:FRESH,Temperaturauftrag:envelope([operation(1,'sent_unconfirmed'),{...operation(2,'pending',20),basis:FRESH}],{confirmedTarget:19,confirmedBasis:FRESH})});
 // Do not call render/observe: admission must inspect current backend receipt.
 f.node._api.callService('climate','set_temperature',{entity_id:ENTITY,temperature:20});f.node.raw.callService('climate','set_temperature',{entity_id:ENTITY,temperature:20});
 f.advance(2000);assert.equal(f.calls.length,1);assert.equal(f.card._presentation.intent.target,19.5);
 f.card._render();assert.equal(f.native.inert,true);assert.equal(f.node.stateObj.attributes.temperature,20);
});
test('submitted temperature lock leaves independent Modus path enabled',()=>{
  const f=control(),menu={isConnected:true,disabled:false};f.card._binding={menu,entity:ENTITY};
  f.button(.5);f.advance(250);Object.getPrototypeOf(f.card)._renderMenu.call(f.card,f.card.hass.states[ENTITY]);
  assert.equal(f.native.inert,true);assert.equal(menu.disabled,false);
});
console.log(JSON.stringify({tests:results.length,passed:results.filter(r=>r.passed).length,failed:results.filter(r=>!r.passed).length,results},null,2));
if(results.some(r=>!r.passed))process.exitCode=1;
