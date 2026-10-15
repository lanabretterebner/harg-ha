/** Independent source-level card transition/API matrix, no DOM/browser/network. */
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {pathToFileURL,fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
const sourcePath=path.resolve(process.argv[2]||fileURLToPath(new URL('../dist/hargassner-thermostat-popup.js',import.meta.url)));
const {installHargassnerThermostatCard}=await import(pathToFileURL(sourcePath));
const registry=new Map([['ha-control-select-menu',class{}]]),timers=new Map();let index=0;
const env={customElements:{get:name=>registry.get(name),define:(name,value)=>registry.set(name,value)},HTMLElement:class{},document:{},setTimeout(fn,delay){timers.set(++index,{fn,delay});return index;},clearTimeout(id){timers.delete(id)}};
const Card=installHargassnerThermostatCard(env),PRESETS=['Aus','Auto','Heizen','Absenken','1x Heizen','1x Absenken'],REASONS=['mode_report','parameter_report','phase_report','pending_change'],BASIS='a'.repeat(32),FRESH='b'.repeat(32);
const mode=preset=>preset==='Aus'?'off':preset==='Auto'?'auto':'heat';
function state(entity,preset,qualified=true,attrs={}){return{entity_id:entity,state:mode(preset),attributes:{hargassner_control_schema:'configured_room_setpoint_v1',supported_features:385,hvac_modes:['off','auto','heat'],preset_modes:['none',...PRESETS],preset_mode:preset,temperature:qualified&&preset!=='Aus'?19:null,min_temp:16,max_temp:22,target_temp_step:.5,'Zieltemperatur verfügbar':qualified&&preset!=='Aus',Sollwertbasis:qualified?BASIS:null,...attrs}};}
function marker(from,to,reason='mode_report',changes={}){return{id:'one_window',basis:BASIS,phase:'readback',version:2,reason,previousPreset:from,nextPreset:to,...changes};}
function native(){return{localName:'hui-thermostat-card',inert:false,setAttribute(key,value){this[key]=value},removeAttribute(key){delete this[key]},remove(){this.removed=true}};}
function fixture(from='Auto',entity='climate.synthetic_hc1'){
 const calls=[],card=Object.create(Card.prototype);Object.defineProperty(card,'isConnected',{value:true,configurable:true});
 card._generation=1;card._retiredNative=new WeakSet();card._guardedNative=new WeakSet();card._native=native();card._guardedNative.add(card._native);card._config={entity};
 card._hass={connected:true,connection:{connected:true},config:{unit_system:{temperature:'°C'}},states:{[entity]:state(entity,from)},callService:async(...args)=>calls.push(structuredClone(args))};
 card._lastConfirmed={basis:BASIS,unit:'°C',state:structuredClone(card._hass.states[entity])};
 const host=new EventTarget();host.replaceChildren=(...nodes)=>{host.nodes=nodes};
 card._ui=Object.fromEntries(['title','footer','retry','current','correction','statusValue','placeholder','updateNote','error'].map(key=>[key,{hidden:true}]));card._ui.nativeHost=host;
 card._helpers={createCardElement:native};card._renderMenu=()=>{};
 const api=()=>{let result;const event=new Event('context-request');event.context='hassApi';event.callback=value=>{result=value};host.dispatchEvent(event);return result;};
 card._guardNativeApi(card._native,card._generation);
 return{card,entity,calls,api};
}
const results=[];async function test(name,run){timers.clear();try{await run();results.push({name,passed:true})}catch(error){results.push({name,passed:false,error:error.stack})}}
for(const entity of ['climate.synthetic_hc1','climate.synthetic_hc2'])for(const from of PRESETS)for(const to of PRESETS)for(const reason of REASONS){
 await test(`${entity} ${from}->${to} ${reason}`,async()=>{
  const f=fixture(from,entity),old=f.card._native,api=f.api();
  const actual=state(entity,to,false,{Sollwertaktualisierung:marker(from,to,reason)}),before=JSON.stringify(actual);f.card._hass.states[entity]=actual;
  assert.equal(f.card._holdTarget(actual),from!=='Aus'&&to!=='Aus');assert.equal(JSON.stringify(actual),before);
  if(from!=='Aus'&&to!=='Aus'){
   assert.equal(f.card._native,old);assert.equal(old.inert,true);assert.equal(old.hass.states[entity].state,mode(to));assert.equal(old.hass.states[entity].attributes.preset_mode,to);assert.equal(old.hass.states[entity].attributes.temperature,19);
   await api.callService('climate','set_temperature',{entity_id:entity,temperature:20});assert.deepEqual(f.calls,[]);
   f.card._hass.states[entity]=state(entity,to,true,{Sollwertbasis:FRESH,temperature:20});f.card._render();assert.notEqual(f.card._native,old);assert.equal(f.card._native.inert,false);
   await api.callService('climate','set_temperature',{entity_id:entity,temperature:20});assert.deepEqual(f.calls,[]);
   await f.api().callService('climate','set_temperature',{entity_id:entity,temperature:20});assert.equal(f.calls.length,1);
  }
  f.card._clearHold(true);
 });
}
for(const entity of ['climate.synthetic_hc1','climate.synthetic_hc2'])for(const from of PRESETS.slice(1))for(const to of PRESETS.slice(1))await test(`qualified atomic context ${entity} ${from}->${to}, before/after render`,async()=>{
 const f=fixture(from,entity),old=f.card._native,api=f.api();
 f.card._hass.states[entity]=state(entity,to,true,{Sollwertbasis:FRESH,temperature:20});
 await api.callService('climate','set_temperature',{entity_id:entity,temperature:19.5});assert.deepEqual(f.calls,[]);
 f.card._render();assert.notEqual(f.card._native,old);await api.callService('climate','set_temperature',{entity_id:entity,temperature:19.5});assert.deepEqual(f.calls,[]);
 await f.api().callService('climate','set_temperature',{entity_id:entity,temperature:20});assert.equal(f.calls.length,1);
});
await test('ABA return to an old valid basis never reactivates its retired API',async()=>{
 const f=fixture(),api=f.api();f.card._hass.states[f.entity]=state(f.entity,'Heizen',true,{Sollwertbasis:FRESH});f.card._render();
 f.card._hass.states[f.entity]=state(f.entity,'Auto',true,{Sollwertbasis:BASIS});f.card._render();
 await api.callService('climate','set_temperature',{entity_id:f.entity,temperature:20});assert.deepEqual(f.calls,[]);
});
await test('atomic qualified basis/mode change retires old API with no target-null stage',async()=>{
 const f=fixture(),old=f.card._native,api=f.api();
 f.card._hass.states[f.entity]=state(f.entity,'Heizen',true,{Sollwertbasis:FRESH,temperature:22,min_temp:19,max_temp:25});f.card._render();
 assert.notEqual(f.card._native,old);await api.callService('climate','set_temperature',{entity_id:f.entity,temperature:19.5});assert.deepEqual(f.calls,[]);
 await f.api().callService('climate','set_temperature',{entity_id:f.entity,temperature:22.5});assert.equal(f.calls.length,1);
});
await test('old captured API cannot write a stale payload after atomic qualified context change',async()=>{
 const f=fixture(),api=f.api(); f.card._hass.states[f.entity]=state(f.entity,'Heizen',true,{Sollwertbasis:FRESH,temperature:22,min_temp:19,max_temp:25});f.card._render();
 await api.callService('climate','set_temperature',{entity_id:f.entity,temperature:19.5});assert.deepEqual(f.calls,[]);
});
await test('measurement/status-only qualified updates preserve current native API epoch',async()=>{
 const f=fixture(),old=f.card._native,api=f.api();f.card._hass.states[f.entity]=state(f.entity,'Auto',true,{current_temperature:21.2,'Heizkreisstatus':'Heizen'});f.card._render();
 assert.equal(f.card._native,old);await api.callService('climate','set_temperature',{entity_id:f.entity,temperature:20});assert.equal(f.calls.length,1);
});
await test('repeated mixed mode/offset/phase markers share original basis and deadline',()=>{
 const f=fixture();let first;
 for(const [to,reason]of[['Heizen','mode_report'],['Absenken','pending_change'],['1x Heizen','parameter_report'],['Auto','phase_report']]){
  const actual=state(f.entity,to,false,{Sollwertaktualisierung:marker('Auto',to,reason)});f.card._hass.states[f.entity]=actual;assert(f.card._holdTarget(actual));first??=f.card._holdTimer;assert.equal(f.card._holdTimer,first);
 }
 assert.equal(timers.size,1);timers.get(first).fn();assert.equal(f.card._native,null);assert.equal(f.card._lastConfirmed,null);
});
for(const failure of ['offline','unknown','unavailable','Off','unit','wrong-hvac','entity','schema','marker-id','legacy-mode','basis','metadata'])await test(`revoke unsafe hold: ${failure}`,()=>{
 const f=fixture();const actual=state(f.entity,'Heizen',false,{Sollwertaktualisierung:marker('Auto','Heizen')});
 if(failure==='offline')f.card._hass.connection.connected=false;
 if(failure==='unknown'||failure==='unavailable')actual.state=failure;
 if(failure==='Off')actual.state='off';if(failure==='unit')f.card._hass.config.unit_system.temperature='°F';
 if(failure==='wrong-hvac')actual.state='auto';if(failure==='entity')actual.entity_id='climate.other';
 if(failure==='schema')actual.attributes.hargassner_control_schema='other';
 if(failure==='legacy-mode')actual.attributes.Sollwertaktualisierung={id:'one_window',basis:BASIS,phase:'pending'};
 if(failure==='basis')actual.attributes.Sollwertaktualisierung.basis=FRESH;
 if(failure==='metadata')actual.attributes.Sollwertaktualisierung.extra=true;
 if(failure==='marker-id'){assert(f.card._holdTarget(actual));actual.attributes.Sollwertaktualisierung={...actual.attributes.Sollwertaktualisierung,id:'different_window'}}
 assert.equal(f.card._holdTarget(actual),false);f.card._clearHold(true);
});
await test('feature drop is inert presentation only and current capability gates recovery API',async()=>{
 const f=fixture(),api=f.api();const actual=state(f.entity,'Heizen',false,{supported_features:384,Sollwertaktualisierung:marker('Auto','Heizen')});f.card._hass.states[f.entity]=actual;
 assert(f.card._holdTarget(actual));assert.equal(f.card._native.hass.states[f.entity].attributes.supported_features,385);assert.equal(actual.attributes.supported_features,384);
 await api.callService('climate','set_temperature',{entity_id:f.entity,temperature:20});assert.deepEqual(f.calls,[]);
 f.card._hass.states[f.entity]=state(f.entity,'Heizen',true,{supported_features:384});await api.callService('climate','set_temperature',{entity_id:f.entity,temperature:20});assert.deepEqual(f.calls,[]);f.card._clearHold(true);
});
const report={schema_version:1,source_sha256:crypto.createHash('sha256').update(fs.readFileSync(sourcePath)).digest('hex'),scope:'Independent Node direct card-method/API/event/timer model; no browser/Lit/HA/network',mode_pair_scenarios:72,marker_reasons:4,passed:results.filter(x=>x.passed).length,failed:results.filter(x=>!x.passed).length,results};console.log(JSON.stringify(report,null,2));if(report.failed)process.exitCode=1;
