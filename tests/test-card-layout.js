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
window.layoutFixture={calls,hass};
window.layoutReady=true;
