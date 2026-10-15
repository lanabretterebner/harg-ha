import { connectionFixture } from './connection-fixture.mjs';
import assert from 'node:assert/strict';
import{HargassnerTargetPresentation}from'../dist/hargassner-thermostat-popup.js';
import{fixture,state,envelope,operation,BASIS,FRESH,CONTEXT,ENTITY}from'./optimistic-card-fixture.mjs';
const results=[];async function test(name,run){try{await run();results.push({name,passed:true})}catch(error){results.push({name,passed:false,error:error.stack})}}
// A later request needs a genuinely qualified readback, even if the older
// service promise has not settled. Keep late-failure isolation coverage.
function confirmedState(entity=ENTITY){return{...state(19.5,true,{Sollwertbasis:FRESH,Raumkorrektur:.5,Temperaturauftrag:envelope([operation(1,'confirmed')],{confirmedTarget:19.5,confirmedBasis:FRESH})}),entity_id:entity}}
await test('button draft survives intervening old state and sends exact desired value',()=>{const f=fixture();f.input(19.5);assert.equal(f.node.stateObj.attributes.temperature,19.5);f.update(state());assert.equal(f.node.stateObj.attributes.temperature,19.5);f.flush();assert.equal(f.calls[0][2].temperature,19.5);assert.equal(f.card._native,f.native)});
await test('pending target never bounces to old and qualified recovery preserves both DOM objects',()=>{const f=fixture();f.input(19.5);f.flush();f.update(state(null,false,{Temperaturauftrag:envelope([operation()])}));assert.equal(f.node.stateObj.attributes.temperature,19.5);assert.equal(f.native.inert,true);f.update(state(19.5,true,{Sollwertbasis:FRESH,Raumkorrektur:.5,Temperaturauftrag:envelope([operation(1,'confirmed')],{confirmedTarget:19.5,confirmedBasis:FRESH})}));assert.equal(f.card._native,f.native);assert.equal(f.node.stateObj.attributes.temperature,19.5);assert.equal(f.native.inert,false)});
await test('unaffected circuit keeps last confirmed numeric display beyond old8s marker expiry',()=>{const f=fixture();f.update(state(null,false));assert.equal(f.node.stateObj.attributes.temperature,19);assert.equal(f.native.inert,true);assert.equal(f.card._native,f.native);assert.match(f.card._ui.updateNote.textContent,/bestätigter/);assert.equal(f.timers.size,0)});
await test('late failure of superseded user request cannot undo newest desired target',async()=>{const f=fixture();let reject;f.card._hass.callService=()=>new Promise((_,r)=>{reject=r});f.input(19.5);f.flush();f.update(confirmedState());f.card._hass.callService=async(...args)=>f.calls.push(args);f.input(20);f.flush();reject(Error('old'));await Promise.resolve();await Promise.resolve();assert.equal(f.node.stateObj.attributes.temperature,20)});
await test('bound definitive backend rejection alone permits rollback',()=>{const f=fixture();f.input(19.5);f.flush();f.update(state(19,true,{Temperaturauftrag:envelope([operation()])}));f.update(state(19,true,{Temperaturauftrag:envelope([operation(1,'rejected')])}));assert.equal(f.node.stateObj.attributes.temperature,19);assert.equal(f.card._presentation.intent,null)});
await test('unbound old rejection leaves current request uncertain rather than reverting',()=>{const f=fixture();f.input(19.5);f.flush();f.update(state(19,true,{Temperaturauftrag:envelope([operation(1,'rejected')])}));assert.equal(f.node.stateObj.attributes.temperature,19.5);assert(f.card._presentation.intent)});
await test('context epoch retires capturedAPI and cancelled callback without remount',()=>{const f=fixture();const api=f.node._api,call=f.node._callService;f.input(19.5);const callback=[...f.timers.values()][0];f.update({...state(22,true,{preset_mode:'Heizen',Sollwertbasis:FRESH,Raumkorrektur:0,Temperaturauftrag:envelope([],{confirmedTarget:22,confirmedBasis:FRESH,confirmedPreset:'Heizen'})}),state:'heat'});callback();call.call(f.node,'value');api.callService('climate','set_temperature',{entity_id:ENTITY,temperature:19.5});assert.equal(f.calls.length,0);assert.equal(f.card._native,f.native);assert.equal(f.node.stateObj.attributes.temperature,22)});
await test('two rapid drafts forward only newest target and old epoch stays closed',()=>{const f=fixture();f.input(19.5);const api=f.node._api,callback=[...f.timers.values()][0];f.input(20);callback();api.callService('climate','set_temperature',{entity_id:ENTITY,temperature:19.5});f.flush();assert.equal(f.calls.length,1);assert.equal(f.calls[0][2].temperature,20)});
await test('coherence gap old temperature/new correction does not settle or replace requested value',()=>{const f=fixture();f.input(19.5);f.flush();f.update(state(19,true,{Sollwertbasis:FRESH,Raumkorrektur:.5,Temperaturauftrag:envelope([operation(1,'confirmed')],{confirmedTarget:19.5,confirmedBasis:FRESH})}));assert.equal(f.node.stateObj.attributes.temperature,19.5);assert(f.card._presentation.intent)});
await test('genuine offline is inert, retains truthful reported state and cannot send',()=>{const f=fixture();f.input(19.5);f.flush();f.card._hass.connection.connected=false;f.update({...state(null,false),state:'unavailable'});assert.equal(f.node.stateObj.state,'unavailable');assert.equal(f.native.inert,true);const before=f.calls.length;f.node._callService('value');assert.equal(f.calls.length,before);assert.equal(f.card._native,f.native)});
await test('Fahrenheit backend Celsius request is converted only in private display',()=>{const t=new HargassnerTargetPresentation();const s=state(66.2,true,{min_temp:60.8,max_temp:71.6,Temperaturauftrag:envelope()});t.observe(s,'°F');const next=state(null,false,{min_temp:60.8,max_temp:71.6,Temperaturauftrag:envelope([operation()])});t.observe(next,'°F');assert.equal(t.display(next).state.attributes.temperature,67.1);assert.equal(next.attributes.temperature,null)});
await test('unit/entity replacement clears pending presentation without saved intent',()=>{const t=new HargassnerTargetPresentation();t.observe(state(),'°C');t.begin(state(),'°C',19.5);t.observe({...state(),entity_id:'climate.synthetic_hc2'},'°C');assert.equal(t.intent,null);t.begin({...state(),entity_id:'climate.synthetic_hc2'},'°C',20);t.observe({...state(),entity_id:'climate.synthetic_hc2'},'°F');assert.equal(t.intent,null)});
await test('newer authenticated external operation supersedes a bound local pending request',()=>{const f=fixture();f.input(19.5);f.flush();f.update(state(null,false,{Temperaturauftrag:envelope([operation()])}));f.update(state(null,false,{Temperaturauftrag:envelope([operation(1,'sent_unconfirmed'),{...operation(2,'pending',20),origin:'api_offset'}])}));assert.equal(f.node.stateObj.attributes.temperature,20);f.update(state(null,false,{Temperaturauftrag:envelope([operation(1,'rejected'),{...operation(2,'pending',20),origin:'api_offset'}])}));assert.equal(f.node.stateObj.attributes.temperature,20)});
await test('older envelope revision cannot overwrite a shared newer confirmed snapshot',()=>{const f=fixture(),old=state();f.update(old);f.input(19.5);f.flush();f.update(state(19.5,true,{Sollwertbasis:FRESH,Raumkorrektur:.5,Temperaturauftrag:envelope([operation(1,'confirmed')],{confirmedTarget:19.5,confirmedBasis:FRESH})}));f.update(old);assert.equal(f.node.stateObj.attributes.temperature,19.5);assert.equal(f.native.inert,true)});
await test('two cards on same connection share last intent, and late first failure stays retired',async()=>{const a=fixture(),b=fixture();b.card._hass.connection=a.card._hass.connection;b.card._hass.states=a.card._hass.states;b.card._render();let reject;a.card._hass.callService=()=>new Promise((_,r)=>{reject=r});a.input(19.5);a.flush();a.update(confirmedState());b.card._render();b.input(20);b.flush();reject(Error('old'));for(let i=0;i<8;i++)await Promise.resolve();assert.equal(a.node.stateObj.attributes.temperature,20);assert.equal(b.node.stateObj.attributes.temperature,20)});
await test('new external operation clears only a previous unrelated rejection note',()=>{const f=fixture();const p=f.card._presentation;p.reject(p.begin(f.card._hass.states[ENTITY],'°C',19.5));f.update(state(19,true,{Temperaturauftrag:envelope([{...operation(1,'pending',20),origin:'api_offset'}])}));assert.equal(p.failure,false);f.update(state(20,true,{Sollwertbasis:FRESH,Raumkorrektur:1,Temperaturauftrag:envelope([{...operation(1,'confirmed',20),origin:'api_offset'}],{confirmedTarget:20,confirmedBasis:FRESH})}));assert.equal(p.intent,null);assert.equal(p.display(f.card._hass.states[ENTITY]).note,'')});
await test('unqualified temperature leaves independently available exact six-mode menu usable',()=>{const f=fixture(),menu={isConnected:true,disabled:false};f.card._binding={menu,entity:ENTITY};const pending=state(null,false,{Temperaturauftrag:envelope([],{confirmedTarget:null,confirmedBasis:null})});f.update(pending);Object.getPrototypeOf(f.card)._renderMenu.call(f.card,pending);assert.equal(f.native.inert,true);assert.equal(menu.disabled,false);f.card._hass.connection.connected=false;Object.getPrototypeOf(f.card)._renderMenu.call(f.card,pending);assert.equal(menu.disabled,true)});
// The pinned native call body exposes only the callService promise, not an
// authenticated negative outcome receipt. Generic/structured errors remain
// uncertain until a bound backend operation proves rejection.
for (const shape of ['throw', 'error', 'connection_lost', 'service_validation_error']) await test(`service ${shape} preserves uncertain requested target`, async () => {
  const f = fixture();
  f.card._hass.callService = (...args) => {
    f.calls.push(structuredClone(args));
    if (shape === 'throw') throw Error('response lost after send');
    return Promise.reject(shape === 'error' ? Error('response lost after send') : { code: shape, message: 'service response unavailable' });
  };
  f.input(19.5); f.flush();
  for (let i = 0; i < 12; i++) await Promise.resolve();
  assert.equal(f.calls.length, 1);
  assert.equal(f.card._presentation.intent?.status, 'sent_unconfirmed');
  assert.equal(f.native.inert, true);
  assert.equal(f.node.stateObj.attributes.temperature, 19.5);
  assert.match(f.card._ui.updateNote.textContent, /Bestätigung unklar/);
  assert.doesNotMatch(f.card._ui.updateNote.textContent, /abgelehnt/);
  f.update(state(19, true, { Temperaturauftrag: envelope([operation()]) }));
  f.update(state(19, true, { Temperaturauftrag: envelope([operation(1, 'rejected')]) }));
  assert.equal(f.card._presentation.intent, null);
  assert.equal(f.node.stateObj.attributes.temperature, 19);
  assert.match(f.card._ui.updateNote.textContent, /abgelehnt/);
});
for (const change of ['connection', 'user']) for (const aba of [false, true]) await test(`${change}${aba ? ' ABA' : ''} replacement permanently retires card APIs and pending debounce`, async () => {
  const f = fixture();
  f.card.hass = { ...f.card.hass, user: { id: 'original_user' } };
  const original = f.card.hass, native = f.native, node = f.node;
  f.input(19.5);
  const api = node._api, raw = node.raw, call = node._callService, debounce = node._debouncedCallService;
  const callback = [...f.timers.values()][0];
  const replacement = { ...original, ...(change === 'connection' ? { connection: { connected: true } } : { user: { id: 'replacement_user' } }) };
  // A changed session cannot borrow authority even before a render callback.
  f.card._hass = replacement;
  await api.callService('climate', 'set_temperature', { entity_id: ENTITY, temperature: 19.5 });
  await raw.callService('climate', 'set_temperature', { entity_id: ENTITY, temperature: 19.5 });
  assert.equal(f.calls.length, 0);
  f.card.hass = replacement;
  assert.equal(f.timers.size, 0);
  if (aba) f.card.hass = original;
  callback(); call.call(node, 'value'); debounce.call(node, 'value');
  await api.callService('climate', 'set_temperature', { entity_id: ENTITY, temperature: 19.5 });
  await raw.callService('climate', 'set_temperature', { entity_id: ENTITY, temperature: 19.5 });
  assert.equal(f.calls.length, 0);
  assert.equal(f.card._native, native); assert.equal(f.node, node);
  assert.equal(node.stateObj.attributes.temperature, 19);
  assert.equal(f.card._presentation.intent, null);
  f.input(20); f.flush();
  assert.equal(f.calls.length, 1); assert.equal(f.calls[0][2].temperature, 20);
});

for (const entity of ['climate.synthetic_hc1', 'climate.synthetic_hc2']) for (const path of ['hass-flag', 'connection-flag', 'events-before-render']) await test(`${entity} card reconnect ${path} retires draft and all captured authority`, async () => {
  const connection = connectionFixture(), f = fixture({ ...state(), entity_id: entity }, connection), node = f.node;
  f.input(19.5);
  const api = node._api, raw = node.raw, call = node._callService, debounce = node._debouncedCallService;
  assert.equal(f.timers.size, 1);
  if (path === 'events-before-render') {
    // Both socket events occur in this task, without a hass setter/render or
    // fixture timer invocation. Retirement must already cancel the live timer.
    connection.disconnect(); assert.equal(f.timers.size, 0); connection.reconnect();
  } else {
    const owner = path === 'hass-flag' ? f.card.hass : connection;
    owner.connected = false; f.card._render(); assert.equal(f.native.inert, true);
    assert.equal(f.timers.size, 0); owner.connected = true; f.card._render();
  }
  await api.callService('climate', 'set_temperature', { entity_id: entity, temperature: 20 });
  await raw.callService('climate', 'set_temperature', { entity_id: entity, temperature: 20 });
  call.call(node, 'value'); debounce.call(node, 'value'); f.flush();
  assert.equal(f.calls.length, 0); assert.equal(f.card._native, f.native);
  assert.equal(node.stateObj.attributes.temperature, 19); assert.equal(f.card._presentation.intent, null);
  f.input(20); f.flush(); assert.equal(f.calls.length, 1); assert.equal(f.calls[0][2].temperature, 20);
});
for (const entity of ['climate.synthetic_hc1', 'climate.synthetic_hc2']) await test(`${entity} card reconnect retains submitted lock until qualified readback`, async () => {
  const connection = connectionFixture(), f = fixture({ ...state(), entity_id: entity }, connection);
  f.input(19.5); f.flush(); const api = f.node._api;
  connection.disconnect(); connection.reconnect();
  for (let i = 0; i < 12; i++) await Promise.resolve();
  assert.equal(f.card._presentation.intent?.status, 'sent_unconfirmed');
  assert.equal(f.node.stateObj.attributes.temperature, 19.5); assert.match(f.card._ui.updateNote.textContent, /Bestätigung unklar/);
  await api.callService('climate', 'set_temperature', { entity_id: entity, temperature: 20 }); assert.equal(f.calls.length, 1);
  assert.equal(f.native.inert, true);
  f.input(20); f.flush(); assert.equal(f.calls.length, 1);
  f.update(confirmedState(entity)); assert.equal(f.native.inert, false);
  f.input(20); f.flush(); assert.equal(f.calls.length, 2); assert.equal(f.calls[1][2].temperature, 20);
  assert.equal(f.card._native, f.native);
});
await test('card lifecycle subscriptions follow session replacement and disposal', () => {
  const connection = connectionFixture(), f = fixture(state(), connection), second = connectionFixture();
  assert.equal(connection.listenerCount(), 2);
  f.card.hass = { ...f.card.hass, connection: second }; assert.equal(connection.listenerCount(), 0); assert.equal(second.listenerCount(), 2);
  f.card.shadowRoot = { replaceChildren() {} }; f.card.disconnectedCallback(); assert.equal(second.listenerCount(), 0);
});

await test('late transport error cannot poison newer explicit gesture after terminal uncertainty and fresh differing actual target',async()=>{
 const f=fixture();let reject;f.card._hass.callService=(...args)=>{f.calls.push(args);return new Promise((_,r)=>{reject=r})};
 f.input(19.5);f.flush();f.update(state(null,false,{Temperaturauftrag:envelope([operation()])}));
 f.update(state(19,true,{Sollwertbasis:FRESH,Temperaturauftrag:envelope([operation(1,'sent_unconfirmed')],{confirmedTarget:19,confirmedBasis:FRESH})}));
 assert.equal(f.native.inert,false);assert.equal(f.node.stateObj.attributes.temperature,19.5);
 f.card._hass.callService=async(...args)=>f.calls.push(args);f.input(20);f.flush();assert.equal(f.calls.length,2);
 const current=f.card._presentation.intent;reject(Error('old response lost'));for(let i=0;i<12;i++)await Promise.resolve();
 assert.equal(f.card._presentation.intent,current);assert.equal(current.status,'pending');assert.equal(current.target,20);assert.equal(current.basis,FRESH);
 assert.equal(f.native.inert,true);assert.equal(f.node.stateObj.attributes.temperature,20);
});

for(const entity of ['climate.synthetic_hc1','climate.synthetic_hc2'])await test(`${entity} current reading changes retain requested target and native leases through missing/offline telemetry`,()=>{
 const f=fixture({...state(19,true,{current_temperature:21.5}),entity_id:entity}),native=f.native,node=f.node;
 f.input(19.5);f.flush();f.update({...state(19,true,{current_temperature:0}),entity_id:entity});
 assert.equal(f.card._ui.current.textContent,'Ist: 0 °C');assert.equal(f.card._ui.current.hidden,false);assert.equal(node.stateObj.attributes.temperature,19.5);assert.equal(f.card._native,native);assert.equal(f.calls.length,1);
 f.update({...state(null,false,{current_temperature:null}),entity_id:entity});assert.equal(f.card._ui.current.textContent,'Ist: Nicht verfügbar');assert.equal(f.card._ui.current.hidden,false);assert.equal(node.stateObj.attributes.temperature,19.5);
 f.card._hass.connected=false;f.card._render();assert.equal(f.card._ui.current.textContent,'Ist: Nicht verfügbar');assert.equal(f.card._native,native);assert.equal(f.node,node);assert.equal(f.calls.length,1);
});
console.log(JSON.stringify({tests:results.length,passed:results.filter(r=>r.passed).length,failed:results.filter(r=>!r.passed).length,results},null,2));if(results.some(r=>!r.passed))process.exitCode=1;
