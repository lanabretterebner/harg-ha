import assert from 'node:assert/strict';
import { resolveHargassnerCorrection } from '../dist/hargassner-thermostat-popup.js';
const id='hargassner_synthetic_number_heizkreis_1_raumkorrektur';
const climate={entity_id:'climate.renamed',state:'auto',attributes:{hargassner_control_schema:'configured_room_setpoint_v1',hargassner_correction_id:id,
 hvac_modes:['off','auto','heat'],preset_modes:['none','Aus','Auto','Heizen','Absenken','1x Heizen','1x Absenken'],preset_mode:'Auto'}};
const number=(entity='number.renamed',value='0')=>({entity_id:entity,state:value,attributes:{hargassner_control_schema:'room_correction_v1',hargassner_correction_id:id,min:-3,max:3,step:.5,unit_of_measurement:'°C'}});
const make=()=>({connected:true,connection:{connected:true},states:{[climate.entity_id]:climate,'number.renamed':number()}});
let count=0;function test(name,fn){fn();count++;console.log('PASS '+name)}
test('opaque commissioned identity resolves renamed entities without names/registry network',()=>{const h=make();assert.equal(resolveHargassnerCorrection(h,climate).entity,'number.renamed')});
test('duplicate identity refuses selection even when second entity unavailable',()=>{const h=make();h.states['number.duplicate']=number('number.duplicate','unavailable');assert.equal(resolveHargassnerCorrection(h,climate),null)});
for(const field of ['hargassner_correction_id','hargassner_control_schema','min','max','step','unit_of_measurement'])test('wrong '+field+' refuses',()=>{const h=make();h.states['number.renamed'].attributes[field]='wrong';assert.equal(resolveHargassnerCorrection(h,climate),null)});
test('missing identity never guesses known object names',()=>{const h=make();h.states={'number.heizkreis_1_raumkorrektur':number('number.heizkreis_1_raumkorrektur')};assert.equal(resolveHargassnerCorrection(h,{...climate,attributes:{...climate.attributes,hargassner_correction_id:null}}),null)});
for(const invalid of ['unknown','unavailable','NaN','Infinity','4','0.25',''])test('invalid/unavailable numeric state '+invalid+' never grants write',()=>{const h=make();h.states['number.renamed'].state=invalid;assert.equal(resolveHargassnerCorrection(h,climate).available,false)});
test('same-device registry accepted and foreign/missing device refused',()=>{const h=make();h.entities={'climate.renamed':{device_id:'one'},'number.renamed':{device_id:'one'}};assert.equal(resolveHargassnerCorrection(h,climate).available,true);h.entities['number.renamed'].device_id='other';assert.equal(resolveHargassnerCorrection(h,climate),null);delete h.entities['number.renamed'];assert.equal(resolveHargassnerCorrection(h,climate),null)});
for(const field of ['connected','connection'])test('disconnected '+field+' revokes writes',()=>{const h=make();if(field==='connection')h.connection.connected=false;else h.connected=false;assert.equal(resolveHargassnerCorrection(h,climate).available,false)});
console.log(JSON.stringify({scope:'synthetic resolver only; no service or network',tests:count,passed:true}));
