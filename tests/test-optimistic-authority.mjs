/** Authority/atomic-context successor cases for the pre-optimistic suites. */
import assert from'node:assert/strict';
import{fixture,state,envelope,BASIS,FRESH,ENTITY}from'./optimistic-card-fixture.mjs';
const results=[];async function test(name,body){try{await body();results.push({name,passed:true})}catch(error){results.push({name,passed:false,error:error.stack})}}
const presets=['Auto','Heizen','Absenken','1x Heizen','1x Absenken'];
for(const entity of['climate.synthetic_hc1','climate.synthetic_hc2'])for(const from of presets)for(const to of presets)await test(`atomic ${entity} ${from}->${to} retires callbacks in place and survives ABA`,async()=>{
 const initial={...state(19,true,{preset_mode:from,Temperaturauftrag:envelope([],{confirmedPreset:from})}),entity_id:entity,state:from==='Auto'?'auto':'heat'},f=fixture(initial),api=f.node._api,call=f.node._callService;
 const next={...state(20,true,{preset_mode:to,Sollwertbasis:FRESH,Temperaturauftrag:envelope([],{revision:1,confirmedTarget:20,confirmedBasis:FRESH,confirmedPreset:to})}),entity_id:entity,state:to==='Auto'?'auto':'heat'};
 f.card._hass.states[entity]=next;await api.callService('climate','set_temperature',{entity_id:entity,temperature:19.5});assert.equal(f.calls.length,0);f.update(next);assert.equal(f.card._native,f.native);call.call(f.node,'value');assert.equal(f.calls.length,0);
 f.update({...initial,attributes:{...initial.attributes,Temperaturauftrag:envelope([],{revision:2,confirmedPreset:from})}});await api.callService('climate','set_temperature',{entity_id:entity,temperature:19.5});assert.equal(f.calls.length,0);f.input(19.5);f.flush();assert.equal(f.calls.length,1);
});
for(const kind of['offline','unknown','unavailable','off','wrong-hvac','schema','preset','feature','temperature','min','max','step','negative-step','out-of-range','invalid-echo','retired-revision','entity','unit'])await test(`actual ${kind} blocks old authority without remounting`,async()=>{
 const f=fixture(),api=f.node._api,call=f.node._callService,next=state();
 if(kind==='offline')f.card._hass.connection.connected=false;
 if(['unknown','unavailable','off'].includes(kind))next.state=kind;
 if(kind==='wrong-hvac')next.state='heat';
 if(kind==='schema')next.attributes.hargassner_control_schema='foreign';
 if(kind==='preset')next.attributes.preset_mode='none';
 if(kind==='feature')next.attributes.supported_features=384;
 if(kind==='temperature')next.attributes.temperature=null;
 if(kind==='min')next.attributes.min_temp=null;if(kind==='max')next.attributes.max_temp=null;
 if(kind==='step')next.attributes.target_temp_step=null;if(kind==='negative-step')next.attributes.target_temp_step=-.5;
 if(kind==='out-of-range')next.attributes.temperature=999;
 if(kind==='invalid-echo')next.attributes.Temperaturauftrag.confirmedTarget=20;
 if(kind==='retired-revision'){f.update(state(19,true,{Temperaturauftrag:envelope([],{revision:3})}));}
 if(kind==='entity')next.entity_id='climate.foreign';if(kind==='unit')f.card._hass.config.unit_system.temperature='°F';
 f.update(next);await api.callService('climate','set_temperature',{entity_id:ENTITY,temperature:19.5});call.call(f.node,'value');assert.equal(f.calls.length,0);
});
console.log(JSON.stringify({tests:results.length,passed:results.filter(x=>x.passed).length,failed:results.filter(x=>!x.passed).length,results},null,2));if(results.some(x=>!x.passed))process.exitCode=1;
