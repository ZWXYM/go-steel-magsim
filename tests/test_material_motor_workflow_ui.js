/* Synthetic DOM/API checks; no browser/native or production records. */
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
class Node{
 constructor(){this.children=[];this.textContent='';this.value='';this.checked=false;this.hidden=false;this.disabled=false}
 append(...nodes){this.children.push(...nodes)} replaceChildren(...nodes){this.children=nodes}
 text(){return this.textContent+this.children.map(n=>n.text?n.text():String(n)).join(' ')}
}
function harness(api){const nodes=new Map(),calls=[],timers=[];const context=vm.createContext({document:undefined,URLSearchParams,setTimeout:(fn,ms)=>{timers.push(ms);return timers.length},clearTimeout:()=>{},fetch:async(url,options={})=>{calls.push({url,method:options.method||'GET'});const value=await api(url);return {ok:true,json:async()=>value}}});
 vm.runInContext(fs.readFileSync('static/js/material_motor_workflow.js','utf8'),context);context.document={getElementById:id=>{if(!nodes.has(id))nodes.set(id,new Node());return nodes.get(id)},createElement:()=>new Node()};
 return {context,nodes,calls,timers};
}
const row={calibration_id:'cal_aaa',material_key:'prediction:pred_bbb',material_name:'B30 holdout',label:'prediction',excluded_grades:['B30P105'],available:true,material_url:'/material-library?calibration=cal_aaa&key=prediction%3Apred_bbb',branches:[],next_step:{action:'native_start',label:'执行现有导入',href:'/material-library?key=prediction',blocked_reason:'许可未连接'}};
const snapshot={version:'v1',checked_utc:'now',resources:{cpu_only:false,license:{available:false,checked_servers:['1055@localhost']},pending_native_tasks:[]},materials:[row],totals:{materials:1,available_materials:1,linked_branches:0,actual_torque_results:0},issues:[]};
(async()=>{
 const h=harness(async()=>snapshot);await h.context.mwfLoad();
 assert.match(h.nodes.get('resource').text(),/1055@localhost/);assert.match(h.nodes.get('resource').text(),/不可连接/);
 assert.equal(h.nodes.get('materials').children.length,1);assert.ok(h.calls.every(c=>c.method==='GET'));assert.equal(h.timers.length,0);
 h.context.mwfDetail(row);assert.match(h.nodes.get('scope').text(),/B30P105/);assert.equal(h.nodes.get('detail').hidden,false);
 h.nodes.get('search').value='no-match';h.context.mwfRenderRows();assert.equal(h.nodes.get('empty').hidden,false);
 h.nodes.get('search').value='';h.nodes.get('preparedOnly').checked=true;h.context.mwfRenderRows();assert.equal(h.nodes.get('materials').children.length,0);

 const branch={records:{preparation:{id:'calmotor_1',status:'awaiting_native_material_import'},native_import:{id:'calimport_1',status:'imported_not_solved'},analysis_plan:{id:'bhanalysis_1',status:'model_snapshot_ready_not_submitted'},execution:{id:'bhexec_1',status:'completed'}},next_step:{action:'result',label:'查看官方转矩',href:'/calibrated-motor/analysis/bhanalysis_1'},result_available:true,torque:{T_avg_Nm:0,K_T_ripple_pct:null}};
 const ready={...row,branches:[branch]};h.context.mwfDetail(ready);assert.match(h.nodes.get('branches').text(),/0\.000000/);assert.match(h.nodes.get('branches').text(),/—/);assert.match(h.nodes.get('branches').text(),/官方转矩/);

 let oldResolve,n=0;const old=new Promise(r=>oldResolve=r),r=harness(async()=>++n===1?old:{...snapshot,resources:{...snapshot.resources,pending_native_tasks:[{status:'running'}]},materials:[{...row,branches:[{...branch,result_available:false,records:{execution:{id:'bhexec_1',status:'running',phase:'solving'}}}]}]});
 const first=r.context.mwfLoad();await r.context.mwfLoad();oldResolve(snapshot);await first;assert.ok(r.timers.includes(5000));assert.equal(r.nodes.get('refresh').disabled,false);
 r.context.fetch=async()=>{throw Error('read failure')};await r.context.mwfLoad();assert.match(r.nodes.get('error').text(),/保留上次快照/);assert.equal(r.nodes.get('materials').children.length,1);
 assert.ok(r.calls.every(c=>c.method==='GET'));
 console.log('Material-motor workflow UI: read-only, blocked license, source/zero metrics, filters, stale responses and retained snapshot: PASS');
})().catch(error=>{console.error(error);process.exitCode=1});
