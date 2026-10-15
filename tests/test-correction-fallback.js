import { installHargassnerThermostatPopup } from '../dist/hargassner-thermostat-popup.js';
const fixtures=document.querySelector('#fixtures'), results=[], presets=['Aus','Auto','Heizen','Absenken','1x Heizen','1x Absenken'];
const assert=(v,m='Assertion failed')=>{if(!v)throw Error(m)};
const equal=(a,b)=>assert(JSON.stringify(a)===JSON.stringify(b),JSON.stringify(a)+' != '+JSON.stringify(b));
const settle=async()=>{for(let i=0;i<5;i++)await new Promise(r=>setTimeout(r,0))};
const delay=()=>new Promise(r=>setTimeout(r,280));
class Menu extends HTMLElement{constructor(){super();this.options=[]}}
class Temperature extends HTMLElement{
 constructor(){super();this._debouncedCallService=()=>this._callService('value')}
 get _api(){return this.raw} set _api(v){this.raw=v}
 get stateObj(){return this.obj} set stateObj(v){this.obj=v;this._targetTemperature={value:v.attributes.temperature}}
 _callService(type){if(type==='value')return this._api.callService('climate','set_temperature',{entity_id:this.stateObj.entity_id,temperature:this._targetTemperature.value})}
}
class Native extends HTMLElement{
 constructor(){super();this.attachShadow({mode:'open'});this.control=document.createElement('ha-state-control-climate-temperature');this.shadowRoot.append(this.control);this.updateComplete=Promise.resolve()}
 set hass(v){this.control.stateObj=v.states[this.entity]}
 connectedCallback(){const e=new Event('context-request',{bubbles:true,composed:true});e.context='hassApi';e.callback=api=>{this.control._api=api};this.dispatchEvent(e)}
}
class Popup extends HTMLElement{
 constructor(){super();this.attachShadow({mode:'open'});this.controls=document.createElement('div');this.controls.className='controls';this.temperature=document.createElement('ha-state-control-climate-temperature');this.controls.append(this.temperature);
 this.container=document.createElement('ha-more-info-control-select-container');this.hvac=document.createElement('ha-control-select-menu');this.preset=document.createElement('ha-control-select-menu');this.container.append(this.hvac,this.preset);this.shadowRoot.append(this.controls,this.container)}
 updated(){}
 render(){const s=this.stateObj;this.temperature._api=this.hass;this.temperature.stateObj=s;this.hvac.options=s.attributes.hvac_modes.map(value=>({value}));this.hvac.value=s.state;
  this.preset.options=s.attributes.preset_modes.map(value=>({value}));this.preset.value=s.attributes.preset_mode;this.preset.label='Preset';this.updated()}
}
customElements.define('ha-control-select-menu',Menu);customElements.define('ha-state-control-climate-temperature',Temperature);customElements.define('hui-thermostat-card',Native);customElements.define('more-info-climate',Popup);
window.loadCardHelpers=async()=>({createCardElement(config){const n=document.createElement('hui-thermostat-card');n.entity=config.entity;return n}});
const controller=installHargassnerThermostatPopup(window);await controller.ready;
function climate(circuit=1,known=false){return {entity_id:`climate.renamed_${circuit}`,state:'auto',attributes:{hargassner_control_schema:'configured_room_setpoint_v1',hargassner_correction_id:`synthetic_correction_${circuit}`,
 hvac_modes:['off','heat','auto'],preset_modes:['none',...presets],preset_mode:'Auto',supported_features:known?385:384,temperature:known?20:null,min_temp:17,max_temp:23,target_temp_step:.5,
 'Zieltemperatur verfügbar':known,Sollwertbasis:known?'a'.repeat(32):null,Raumkorrektur:0,'Heizkreisstatus':'Sommerabschaltung','Heizkreisstatus-Code':5,'Heizkreisstatus verfügbar':true}}}
function number(circuit=1,value='0',stamp=Date.now()-10000,entity=`number.renamed_${circuit}`){return {entity_id:entity,state:value,last_updated:new Date(stamp).toISOString(),attributes:{hargassner_control_schema:'room_correction_v1',hargassner_correction_id:`synthetic_correction_${circuit}`,min:-3,max:3,step:.5,unit_of_measurement:'°C'}}}
async function fixture(kind,circuit=1){
 const c=climate(circuit),n=number(circuit),calls=[],connection=new EventTarget();connection.connected=true;
 const h={connected:true,connection,user:{id:'synthetic'},states:{[c.entity_id]:c,[n.entity_id]:n},entities:{[c.entity_id]:{device_id:'device'},[n.entity_id]:{device_id:'device'}},config:{unit_system:{temperature:'°C'}},callService:async(...args)=>calls.push(structuredClone(args))};
 const host=document.createElement(kind==='card'?'hargassner-thermostat-card':'more-info-climate');
 if(kind==='card')host.setConfig({entity:c.entity_id});host.hass=h;host.stateObj=c;fixtures.append(host);
 const refresh=()=>{if(kind==='card')host.hass=host.hass;else{host.stateObj=host.hass.states[c.entity_id];host.render()}};refresh();await settle();
 return {host,h,c,n,calls,refresh,control:()=>host.shadowRoot.querySelector('[data-hargassner-correction]'),update(value){host.hass.states[value.entity_id]=value;refresh()}};
}
const plus=f=>f.control().querySelectorAll('button')[1],minus=f=>f.control().querySelectorAll('button')[0],dial=f=>f.control().querySelector('[role=slider]');
async function test(name,fn){fixtures.replaceChildren();try{await fn();results.push({name,passed:true})}catch(e){results.push({name,passed:false,error:e.stack})}}
for(const kind of ['card','popup'])for(const circuit of [1,2]){
 await test(`${kind} HC${circuit} unknown target exposes correction dial, pure render sends nothing`,async()=>{const f=await fixture(kind,circuit);assert(f.control()&&!f.control().hidden);equal(dial(f).getAttribute('aria-valuenow'),'0');assert(f.host.shadowRoot.textContent.includes('Aktive Zieltemperatur unbekannt'));if(kind==='card')assert(f.host._ui.correction.hidden);equal(f.calls,[]);equal(f.h.states[f.c.entity_id].attributes.temperature,null)});
 await test(`${kind} HC${circuit} rapid correction clicks coalesce and stale echoes never flash rollback`,async()=>{const f=await fixture(kind,circuit),node=f.control();plus(f).click();plus(f).click();equal(dial(f).getAttribute('aria-valuenow'),'1');await delay();equal(f.calls,[['number','set_value',{entity_id:f.n.entity_id,value:1}]]);assert(plus(f).disabled);f.update(number(circuit));await settle();equal(f.control(),node);equal(dial(f).getAttribute('aria-valuenow'),'1');assert(node.textContent.includes('Bestätigung ausstehend'));
  f.update(number(circuit,'1',Date.now()-5000));await settle();assert(plus(f).disabled);equal(dial(f).getAttribute('aria-valuenow'),'1');f.update(number(circuit,'1',Date.now()+1));await settle();assert(!plus(f).disabled);equal(f.calls.length,1);equal(f.h.states[f.c.entity_id].attributes.temperature,null)});
 await test(`${kind} HC${circuit} keyboard changes exact half-degree correction and bounds`,async()=>{const f=await fixture(kind,circuit);dial(f).dispatchEvent(new KeyboardEvent('keydown',{key:'End',bubbles:true}));await delay();equal(f.calls[0],['number','set_value',{entity_id:f.n.entity_id,value:3}]);f.update(number(circuit,'3',Date.now()+1));await settle();assert(plus(f).disabled);assert(!minus(f).disabled)});
}
for(const kind of ['card','popup']){
 await test(`${kind} known target keeps native control and no fallback`,async()=>{const f=await fixture(kind);f.update(climate(1,true));await settle();assert(!f.control()||f.control().hidden);equal(f.h.states[f.c.entity_id].attributes.temperature,20);equal(f.calls,[]);if(kind==='popup')assert(!f.host.temperature.hidden);else {assert(!f.host.shadowRoot.querySelector('.native')?.parentNode.hidden);assert(!f.host._ui.correction.hidden)}});
 await test(`${kind} renaming number resolves exact identity, wrong-device and duplicates refuse`,async()=>{const f=await fixture(kind);delete f.h.states[f.n.entity_id];delete f.h.entities[f.n.entity_id];const renamed=number(1,'0',Date.now(),'number.custom_user_name');f.h.entities[renamed.entity_id]={device_id:'device'};f.update(renamed);await settle();plus(f).click();await delay();equal(f.calls[0][2].entity_id,renamed.entity_id);f.h.entities[renamed.entity_id].device_id='foreign';f.refresh();await settle();assert(!f.control()||f.control().hidden);equal(f.calls.length,1)});
 await test(`${kind} disconnect before debounce sends nothing and preserves requested pending after service error`,async()=>{const f=await fixture(kind);plus(f).click();f.h.connection.connected=false;f.h.connection.dispatchEvent(new Event('disconnected'));f.refresh();await delay();equal(f.calls,[]);f.h.connection.connected=true;f.h.connection.dispatchEvent(new Event('ready'));f.refresh();await settle();f.h.callService=(...args)=>{f.calls.push(structuredClone(args));return Promise.reject(Error('transport unavailable'))};plus(f).click();await delay();await settle();equal(dial(f).getAttribute('aria-valuenow'),'0.5');assert(f.control().textContent.includes('Bestätigung unklar'));f.update(number());await settle();equal(dial(f).getAttribute('aria-valuenow'),'0.5');equal(f.calls.length,1)});
 await test(`${kind} definite service validation rejection alone rolls back draft`,async()=>{const f=await fixture(kind);f.h.callService=()=>Promise.reject({code:'service_validation_error'});plus(f).click();await delay();await settle();equal(dial(f).getAttribute('aria-valuenow'),'0');assert(f.control().textContent.includes('abgelehnt'))});
 for(const change of ['connection','user'])await test(`${kind} ${change} ABA retires queued correction`,async()=>{const f=await fixture(kind);plus(f).click();const original=f.host.hass;f.host.hass={...original,...(change==='connection'?{connection:{connected:true}}:{user:{id:'other'}})};f.refresh();f.host.hass=original;f.refresh();await delay();equal(f.calls,[]);equal(dial(f).getAttribute('aria-valuenow'),'0')});
}
await test('card/popup share one requested correction and do not mutate global number or target',async()=>{const f=await fixture('card'),p=document.createElement('more-info-climate');p.hass=f.h;p.stateObj=f.c;fixtures.append(p);p.render();await settle();plus(f).click();await delay();await settle();equal(p.shadowRoot.querySelector('[role=slider]').getAttribute('aria-valuenow'),'0.5');equal(f.calls.length,1);equal(f.h.states[f.n.entity_id].state,'0');equal(f.c.attributes.temperature,null)});
await test('pending correction retains exact node across unrelated room/status updates',async()=>{const f=await fixture('card'),node=f.control();plus(f).click();await delay();f.update({...f.c,attributes:{...f.c.attributes,current_temperature:21,'Heizkreisstatus':'Frostschutz'}});await settle();equal(f.control(),node);equal(dial(f).getAttribute('aria-valuenow'),'0.5');equal(f.calls.length,1)});
const report={tests:results.length,passed:results.every(r=>r.passed),results,scope:'Loopback browser synthetic number services only; no HA/controller/network calls'};
window.correctionFallbackTests=report;document.querySelector('#results').textContent=JSON.stringify(report,null,2);

window.prepareCorrectionPointerProbe=async kind=>{fixtures.replaceChildren();window.correctionPointerProbe=await fixture(kind)};
window.readCorrectionPointerProbe=()=>({calls:window.correctionPointerProbe.calls,correction:window.correctionPointerProbe.control().querySelector('[role=slider]').getAttribute('aria-valuenow'),target:window.correctionPointerProbe.c.attributes.temperature});
