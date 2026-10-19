/* Preparation/reuse button: no native dispatch, stale results or double clicks. */
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
function harness(){
 const nodes=new Map(),calls=[],selected={calibration_id:'cal_test',key:'prediction:pred_test'};
 const c=vm.createContext({document:undefined,selected,fetch:async(url,options)=>{
  calls.push({url,options});return {ok:true,json:async()=>({records:{analysis_plan:{id:'plan_test'}},reused:[1,2,3],created:[],next_step:'复用现有记录',href:'/calibrated-motor/analysis/plan_test'})};
 }});
 vm.runInContext(fs.readFileSync('static/js/material_library.js','utf8'),c);
 c.document={getElementById:id=>{if(!nodes.has(id))nodes.set(id,{disabled:false,hidden:false,textContent:'',value:id==='motorCores'?'2':'v8_2_cycle1'});return nodes.get(id)}};
 vm.runInContext('ML.detail=selected',c);c.mlLoadPreparations=async()=>{};
 return {c,nodes,calls,selected};
}
(async()=>{
 const h=harness();await h.c.mlPrepareMotor();assert.equal(h.calls.length,1);
 assert.equal(h.calls[0].url,'/api/workbench/material-motor/prepare-or-reuse');
 assert.deepEqual(JSON.parse(h.calls[0].options.body),{calibration:'cal_test',key:'prediction:pred_test',cores:2,measurement_protocol:'v8_2_cycle1'});
 assert.equal(h.nodes.get('chainOpen').href,'/calibrated-motor/analysis/plan_test');assert.equal(h.nodes.get('chainOpen').hidden,false);
 assert.match(h.nodes.get('chainStatus').textContent,/复用 3 项/);assert.match(h.nodes.get('chainStatus').textContent,/未下发/);
 let resolve;const pending=new Promise(r=>resolve=r),double=harness();double.c.fetch=async(url,options)=>{double.calls.push({url,options});return pending};
 const first=double.c.mlPrepareMotor();await double.c.mlPrepareMotor();assert.equal(double.calls.length,1);
 resolve({ok:true,json:async()=>({records:{},reused:[],created:[1],next_step:'准备完成',href:'/material-library'})});await first;
 assert.equal(double.nodes.get('prepareMotor').disabled,false);
 const bad=harness();bad.c.fetch=async()=>({ok:false,json:async()=>({error:'现有记录需检查'})});await bad.c.mlPrepareMotor();
 assert.match(bad.nodes.get('error').textContent,/需检查/);assert.equal(bad.nodes.get('prepareMotor').disabled,false);assert.equal(bad.nodes.get('chainOpen').hidden,true);
 const stale=harness();stale.c.fetch=async()=>({ok:true,json:async()=>{vm.runInContext('ML.detail={key:"another"}',stale.c);return {records:{},reused:[],created:[],next_step:'old',href:'/old'}}});
 await stale.c.mlPrepareMotor();assert.equal(stale.nodes.get('chainOpen').hidden,true);assert.notEqual(stale.nodes.get('chainStatus').textContent,'old');
 console.log('Material chain UI: exact source/protocol, no dispatch, double-click protection, stale/error recovery: PASS');
})().catch(error=>{console.error(error);process.exitCode=1});
