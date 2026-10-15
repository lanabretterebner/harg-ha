/** Both-circuit/all-mode current-authority and stable-DOM matrix. No live I/O. */
import assert from'node:assert/strict';
import{fixture,state,envelope,BASIS,FRESH}from'./optimistic-card-fixture.mjs';
const PRESETS=['Aus','Auto','Heizen','Absenken','1x Heizen','1x Absenken'],REASONS=['mode_report','parameter_report','phase_report','pending_change'];
const mode=p=>p==='Aus'?'off':p==='Auto'?'auto':'heat';
function make(entity,preset,qualified=true,attrs={}){return{...state(19,qualified,{preset_mode:preset,Temperaturauftrag:envelope([],{revision:qualified?1:0,confirmedTarget:qualified&&preset!=='Aus'?19:null,confirmedBasis:qualified&&preset!=='Aus'?BASIS:null,confirmedPreset:qualified?preset:null}),...attrs}),entity_id:entity,state:mode(preset)}}
const results=[];
for(const entity of['climate.synthetic_hc1','climate.synthetic_hc2'])for(const from of PRESETS)for(const to of PRESETS)for(const reason of REASONS){
 const name=`${entity}:${from}->${to}:${reason}`;try{
  const f=fixture(make(entity,from,from!=='Aus')),native=f.native,node=f.node,api=node._api,call=node._callService;
  if(from!=='Aus')f.input(19.5);const timer=[...f.timers.values()][0];
  const actual=make(entity,to,false,{temperature:null,'Zieltemperatur verfügbar':false,Sollwertbasis:null,Sollwertaktualisierung:{version:2,id:'source_transition',basis:BASIS,phase:'readback',reason,previousPreset:from,nextPreset:to}}),unchanged=JSON.stringify(actual);
  f.update(actual);assert.equal(f.card._native,native);assert.equal(f.native.shadowRoot.querySelectorAll()[0],node);assert.equal(JSON.stringify(actual),unchanged);assert(native.inert);
  if(from!=='Aus'&&to!=='Aus')assert.equal(node.stateObj.attributes.temperature,19);else assert.equal(node.stateObj.attributes.temperature,null);
  timer?.();call.call(node,'value');await api.callService('climate','set_temperature',{entity_id:entity,temperature:19.5});assert.equal(f.calls.length,0);
  const recovered=make(entity,to,to!=='Aus',{temperature:to==='Aus'?null:20,Sollwertbasis:to==='Aus'?null:FRESH,Temperaturauftrag:envelope([],{revision:2,confirmedTarget:to==='Aus'?null:20,confirmedBasis:to==='Aus'?null:FRESH,confirmedPreset:to})});f.update(recovered);assert.equal(f.card._native,native);
  await api.callService('climate','set_temperature',{entity_id:entity,temperature:19.5});assert.equal(f.calls.length,0);
  if(to!=='Aus'){node._targetTemperature.value=20.5;node._callService('value');assert.equal(f.calls.length,1);assert.equal(f.calls[0][2].temperature,20.5)}else{assert.equal(node.stateObj.state,'off');assert.equal(node.stateObj.attributes.temperature,null)}
  results.push({name,passed:true});
 }catch(error){results.push({name,passed:false,error:error.stack})}
}
console.log(JSON.stringify({tests:results.length,passed:results.filter(r=>r.passed).length,failed:results.filter(r=>!r.passed).length,failures:results.filter(r=>!r.passed)},null,2));if(results.some(r=>!r.passed))process.exitCode=1;
