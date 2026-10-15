/** Real DOM/context events with synthetic native handlers only. No HA/service/network. */
import {installHargassnerThermostatPopup} from '../dist/hargassner-thermostat-popup.js';
const presets=['none','Aus','Auto','Heizen','Absenken','1x Heizen','1x Absenken'];
const results=[], wait=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const settle=async()=>{for(let i=0;i<8;i++)await Promise.resolve();await wait(0)};
const assert=(value,message='Assertion failed')=>{if(!value)throw Error(message)};
async function fixture(nativeContext){
 window.hargassnerThermostatPopup?.stop();document.querySelector('#fixtures').replaceChildren();
 const calls=[],callbacks={},unsubscribed={};
 const connection={connected:true},user={id:'synthetic_user'};
 const value={entity_id:'climate.synthetic_popup_scope',state:'auto',attributes:{hargassner_control_schema:'configured_room_setpoint_v1',hvac_modes:['off','heat','auto'],preset_modes:presets,preset_mode:'Auto',supported_features:385,temperature:19,min_temp:16,max_temp:22,target_temp_step:.5,'Zieltemperatur verfügbar':true,Sollwertbasis:'a'.repeat(32),Sollwertphase:'night',Sollwertparameter:'5',Raumkorrektur:0}};
 const hass={connected:true,connection,user,config:{unit_system:{temperature:'°C'}},states:{[value.entity_id]:value},callService:async(...args)=>{calls.push(args)}};
 const wrapper=document.createElement('div');wrapper.hass=hass;
 wrapper.addEventListener('context-request',event=>{
  if(!['hassConnection','hassConfig'].includes(event.context))return;
  event.stopPropagation();callbacks[event.context]=event.callback;
  const dispose=()=>{unsubscribed[event.context]=(unsubscribed[event.context]||0)+1};
  event.callback(event.context==='hassConnection'?{connection,connected:true}:{user,config:hass.config},dispose);
 });
 const p=document.createElement('more-info-climate');p.stateObj=value;p._api={callService:hass.callService};
 p.hass=hass; // Initial fixture render needs its legacy helper only before installation.
 wrapper.append(p);document.querySelector('#fixtures').append(wrapper);
 const controls=document.createElement('div');controls.className='controls';p.shadowRoot.insertBefore(controls,p.temperatureControl);controls.append(p.temperatureControl);p.temperatureControl._api=p._api;
 if(nativeContext)delete p.hass;
 const controller=installHargassnerThermostatPopup(window);await controller.ready;p.updated();await settle();
 return{p,node:p.temperatureControl,hass,calls,callbacks,unsubscribed,controller,wrapper};
}
async function test(name,run){let f;try{await run(async mode=>(f=await fixture(mode)));results.push({name,passed:true})}catch(error){results.push({name,passed:false,error:error.message})}finally{f?.controller.stop();f?.wrapper.remove()}}
for(const nativeContext of[false,true])for(const change of['connection','user'])await test(`${nativeContext?'native context':'existing hass'} ${change} coalesced ABA closes old native leases`,async make=>{
 const f=await make(nativeContext),n=f.node,original=f.hass;n._targetTemperature.value=19.5;n._debouncedCallService('value');const api=n._api,call=n._callService,debounce=n._debouncedCallService;
 if(nativeContext){const key=change==='connection'?'hassConnection':'hassConfig';f.callbacks[key](change==='connection'?{connection:{connected:true},connected:true}:{user:{id:'other'},config:original.config});f.callbacks[key](change==='connection'?{connection:original.connection,connected:true}:{user:original.user,config:original.config})}
 else{f.p.hass={...original,...(change==='connection'?{connection:{connected:true}}:{user:{id:'other'}})};f.p.hass=original}
 await api.callService('climate','set_temperature',{entity_id:f.p.stateObj.entity_id,temperature:19.5});call.call(n,'value');debounce.call(n,'value');await wait(320);
 assert(f.calls.length===0,'Old scope emitted a synthetic call');assert(f.node===n&&f.p.temperatureControl===n,'Native node replaced');
 n._targetTemperature.value=20;n._debouncedCallService('value');await wait(320);assert(f.calls.length===1&&f.calls[0][2].temperature===20,'New gesture blocked/duplicated');
});
await test('unchanged context identities preserve queued250ms gesture',async make=>{
 const f=await make(true),n=f.node;n._targetTemperature.value=19.5;n._debouncedCallService('value');f.callbacks.hassConnection({connection:f.hass.connection,connected:true});f.callbacks.hassConfig({user:{...f.hass.user},config:{...f.hass.config}});await wait(320);assert(f.calls.length===1&&f.calls[0][2].temperature===19.5,'Same identity canceled draft');assert(f.p.temperatureControl===n,'Native replaced');
});
await test('owned native context subscriptions release once on stop and late callbacks stay inert',async make=>{
 const f=await make(true),n=f.node,api=n._api,callback=f.callbacks.hassConnection;f.controller.stop();assert(f.unsubscribed.hassConnection===1&&f.unsubscribed.hassConfig===1,'Subscriptions not released once');callback({connection:{connected:true},connected:true});await api.callService('climate','set_temperature',{entity_id:f.p.stateObj.entity_id,temperature:19.5});await wait(10);assert(f.calls.length===0,'Disposed API forwarded');assert(!Object.hasOwn(f.p,'hass'),'Invented native hass property');
});

for(const flag of ['hass','connection'])await test(`same identity ${flag} false-ready before render closes old native leases`,async make=>{
 const f=await make(false),n=f.node,original=f.p.hass;n._targetTemperature.value=19.5;n._debouncedCallService('value');const api=n._api,call=n._callService,debounce=n._debouncedCallService;
 if(flag==='hass'){f.p.hass={...original,connected:false};f.p.hass={...original,connected:true}}
 else{original.connection.connected=false;f.p.hass={...original};original.connection.connected=true;f.p.hass={...original}}
 await api.callService('climate','set_temperature',{entity_id:f.p.stateObj.entity_id,temperature:19.5});call.call(n,'value');debounce.call(n,'value');await wait(320);assert(f.calls.length===0,'Disconnected draft forwarded');
 n._targetTemperature.value=20;n._debouncedCallService('value');await wait(320);assert(f.calls.length===1&&f.calls[0][2].temperature===20,'Fresh gesture blocked/duplicated');assert(f.p.temperatureControl===n,'Native node replaced');
});
window.popupScopeBrowserResults={passed:results.every(r=>r.passed),tests:results.length,results,scope:'Local synthetic native handlers with actual DOM/context events; not installed HA'};
document.querySelector('#scope-results').textContent=JSON.stringify(window.popupScopeBrowserResults,null,2);
