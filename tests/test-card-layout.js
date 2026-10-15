import '../dist/hargassner-thermostat-popup.js';
class Frame extends HTMLElement{constructor(){super();this.attachShadow({mode:'open'});this.shadowRoot.innerHTML='<style>:host{display:block;background:var(--ha-card-background);border:1px solid var(--divider-color);border-radius:12px;box-sizing:border-box}</style><slot></slot>'}}
class Menu extends HTMLElement{
 constructor(){super();this.attachShadow({mode:'open'});this.options=[];this._label='';this._value=''}
 set label(v){this._label=v;this.paint()}get label(){return this._label}
 set value(v){this._value=v;this.paint()}get value(){return this._value}
 set disabled(v){this._disabled=v;this.paint()}get disabled(){return this._disabled}
 paint(){this.shadowRoot.innerHTML='<style>:host{display:block;min-width:0}.label{font-size:11px;line-height:16px;color:var(--secondary-text-color);margin-bottom:4px}button{display:flex;align-items:center;justify-content:space-between;gap:20px;min-height:36px;min-width:120px;width:100%;padding:6px 10px;box-sizing:border-box;border:0;border-radius:8px;background:var(--secondary-background-color);color:var(--primary-text-color);font:inherit;text-align:left}button span:last-child{color:var(--secondary-text-color);font-size:12px}</style><div class="label"></div><button><span></span><span>⌄</span></button>';this.shadowRoot.querySelector('.label').textContent=this._label;this.shadowRoot.querySelector('button span').textContent=this._value;this.shadowRoot.querySelector('button').disabled=this._disabled}
}
customElements.define('ha-card',Frame);customElements.define('ha-control-select-menu',Menu);
class Temperature extends HTMLElement{
 constructor(){super();this.attachShadow({mode:'open'});this.shadowRoot.innerHTML='<style>:host{display:block;width:180px;height:180px}.container{position:relative;width:180px;height:180px;border:8px solid var(--divider-color);border-radius:50%;box-sizing:border-box}.info{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:2px}.info>*{margin:0}.primary{font-size:30px;line-height:38px}.container.xs .label{display:none}</style><div class="container xs"><div class="info"><p class="label">Auto</p><ha-big-number class="primary"></ha-big-number><p class="label secondary">native secondary</p></div></div>';this._debouncedCallService=()=>this._callService('value')}
 set stateObj(value){this.state=value;this._targetTemperature={value:value.attributes.temperature};this.shadowRoot.querySelector('.primary').textContent=value.attributes.temperature==null?'—':`${value.attributes.temperature} °C`}
 get stateObj(){return this.state}set _api(value){this.api=value}get _api(){return this.api}
 _callService(type){if(type==='value')return this._api.callService('climate','set_temperature',{entity_id:this.stateObj.entity_id,temperature:this._targetTemperature.value})}
}
class Native extends HTMLElement{
 constructor(){super();this.attachShadow({mode:'open'});this.control=document.createElement('ha-state-control-climate-temperature');this.shadowRoot.innerHTML='<style>:host{display:block;width:220px;height:220px}.title{height:24px;text-align:center}.container{display:flex;justify-content:center;align-items:center}</style><div class="title">Zieltemperatur</div><div class="container"></div>';this.shadowRoot.querySelector('.container').append(this.control);this.updateComplete=Promise.resolve()}
 set hass(value){this._hass=value;this.control.stateObj=value.states[this.entity]}get hass(){return this._hass}
 connectedCallback(){const event=new Event('context-request',{bubbles:true,composed:true});event.context='hassApi';event.callback=api=>{this.control._api=api};this.dispatchEvent(event)}
}
customElements.define('ha-state-control-climate-temperature',Temperature);customElements.define('hui-thermostat-card',Native);
window.loadCardHelpers=async()=>({createCardElement(config){const native=document.createElement('hui-thermostat-card');native.entity=config.entity;return native}});

const presets=['Aus','Auto','Heizen','Absenken','1x Heizen','1x Absenken'],calls=[];
const connection={connected:true},states={},entities={},hass={connected:true,connection,user:{id:'layout_synthetic'},states,entities,config:{unit_system:{temperature:'°C'}},locale:{language:'de'},callService:async(...args)=>{calls.push(structuredClone(args))}};
for(let i=1;i<=3;i++){
 const climate=`climate.layout_${i}`,number=`number.layout_${i}`;
 states[climate]={entity_id:climate,state:'auto',attributes:{friendly_name:i===3?'Heizkreis · angefragt':`Heizkreis ${i}`,hargassner_control_schema:'configured_room_setpoint_v1',hargassner_correction_id:`layout_${i}`,hvac_modes:['off','heat','auto'],preset_modes:['none',...presets],preset_mode:'Auto',supported_features:384,temperature:null,min_temp:17,max_temp:23,target_temp_step:.5,'Zieltemperatur verfügbar':false,Raumkorrektur:i===2?-.5:0,current_temperature:i===2?null:21.5,'Heizkreisstatus':'Sommerabschaltung','Heizkreisstatus-Code':5,'Heizkreisstatus verfügbar':true}};
 states[number]={entity_id:number,state:i===2?'-.5':'0',last_updated:new Date(Date.now()-10000).toISOString(),attributes:{hargassner_control_schema:'room_correction_v1',hargassner_correction_id:`layout_${i}`,min:-3,max:3,step:.5,unit_of_measurement:'°C'}};
 // Use ordinary decimal representation, as HA does.
 if(i===2)states[number].state='-0.5';
 entities[climate]=entities[number]={device_id:'layout_device'};
 const card=document.createElement('hargassner-thermostat-card');card.setConfig({entity:climate});card.hass=hass;document.querySelector('#cards').append(card);
}
window.layoutFixture={calls,hass,update(index,attributes={},value){const entity=`climate.layout_${index+1}`,before=states[entity];states[entity]={...before,...(value?{state:value}:{}),attributes:{...before.attributes,...attributes}};document.querySelectorAll('hargassner-thermostat-card')[index].hass=hass}};
window.layoutReady=true;
