/* Batch page adapters: zero/null values, exact selections and no automatic dispatch. */
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
class Node{
 constructor(tag='div'){this.tag=tag;this.children=[];this.textContent='';this.value='';this.disabled=false;this.hidden=false;this.checked=false;this.style={};this.attrs={}}
 setAttribute(name,value){this.attrs[name]=String(value)}
 append(...items){this.children.push(...items);if(this.tag==='select'&&!this.value&&items.length)this.value=items[0].value}
 replaceChildren(...items){this.children=items;if(this.tag==='select')this.value=''}
 querySelectorAll(tag){return this.children.flatMap(n=>[...(n.tag===tag?[n]:[]),...n.querySelectorAll(tag)])}
 text(){return this.textContent+this.children.map(n=>n.text()).join(' ')}
}
const record={id:'bhbatch_aaaaaaaaaaaa',status:'prepared',dispatch_compatible:true,pause_requested:false,
 budget:{max_new_imports:1,max_new_motor_solves:1,cores:2,gpus:0},
 totals:{materials:1,actual_torque_results:1,new_completed_motor_solves:0},items:[{id:'item_01',status:'reused_result',result_available:true,
 selection:{calibration:'cal_a',key:'prediction:pred_a'},source:{material_name:'WB_a',excluded_grades:['B30P105']},
 analysis_url:'/calibrated-motor/analysis/plan_a',execution_id:'bhexec_existing',metrics:{T_avg_Nm:0,T_min_Nm:0,T_max_Nm:0,K_T_ripple_pct:null}}]};
function harness(api){
 const nodes=new Map(),calls=[],timers=[],urls=[];
 const c=vm.createContext({document:undefined,URLSearchParams,location:{search:'',pathname:'/calibrated-motor/batches'},history:{replaceState:(state,title,url)=>urls.push(url)},setTimeout:(fn,ms)=>{timers.push(ms);return timers.length},clearTimeout:()=>{},
 fetch:async(url,options={})=>{calls.push({url,method:options.method||'GET',body:options.body});return {ok:true,json:async()=>api(url,options)}}});
 vm.runInContext(fs.readFileSync('static/js/material_motor_batches.js','utf8'),c);
 c.document={getElementById:id=>{if(!nodes.has(id))nodes.set(id,new Node(['batchHistory','batchCalibration'].includes(id)?'select':'div'));return nodes.get(id)},createElement:tag=>new Node(tag),createElementNS:(ns,tag)=>new Node(tag)};
 c.document.getElementById('batchHistory').value=record.id;c.document.getElementById('batchCalibration').value='cal_a';
 c.document.getElementById('batchCores').value='2';c.document.getElementById('batchProtocol').value='v8_2_cycle1';
 return {c,nodes,calls,timers,urls};
}
(async()=>{
 const h=harness(async url=>url.endsWith('/resources')?{cpu_only:false}:record);await h.c.mbLoad();
 assert.equal(h.nodes.get('batchStart').disabled,false);assert.equal(h.nodes.get('batchResume').disabled,true);
 assert.match(h.nodes.get('batchRows').text(),/0\.000000/);assert.match(h.nodes.get('batchRows').text(),/—/);assert.match(h.nodes.get('batchRows').text(),/B30P105/);
 assert.ok(h.calls.every(c=>c.method==='GET'));assert.equal(h.timers.length,0);
 assert.equal(h.urls.at(-1),'/calibrated-motor/batches?batch=bhbatch_aaaaaaaaaaaa');
 const cpu=harness(async url=>url.endsWith('/resources')?{cpu_only:true}:record);await cpu.c.mbLoad();assert.equal(cpu.nodes.get('batchStart').disabled,true);assert.equal(cpu.nodes.get('batchPause').disabled,false);
 const unavailable=harness(async url=>{if(url.endsWith('/resources'))throw Error('offline');return record});await unavailable.c.mbLoad();assert.equal(unavailable.nodes.get('batchStart').disabled,true);assert.match(unavailable.nodes.get('batchError').textContent,/资源状态不可读/);
 const running=harness(async url=>url.endsWith('/resources')?{cpu_only:false}:{...record,status:'running'});await running.c.mbLoad();assert.ok(running.timers.includes(3000));assert.ok(running.calls.every(c=>c.method==='GET'));
 let resolveOld,count=0;const stalePromise=new Promise(r=>resolveOld=r),stale=harness(async url=>url.endsWith('/resources')?{cpu_only:false}:(++count===1?stalePromise:{...record,status:'completed'}));
 const first=stale.c.mbLoad();await stale.c.mbLoad();resolveOld({...record,status:'running'});await first;assert.match(stale.nodes.get('batchStatus').textContent,/批量已完成/);assert.equal(stale.timers.length,0);
 const prepare=harness(async(url,options)=>url.endsWith('/resources')?{cpu_only:false}:url.endsWith('/batches')&&options.method!=='POST'?[record]:record);
 vm.runInContext('MBB.selected=new Set(["prediction:pred_a"])',prepare.c);await prepare.c.mbPrepare();
 const posts=prepare.calls.filter(c=>c.method==='POST');assert.equal(posts.length,1);assert.equal(posts[0].url,'/api/workbench/calibrated-motor/batches');
 assert.deepEqual(JSON.parse(posts[0].body),{materials:[{calibration:'cal_a',key:'prediction:pred_a'}],cores:2,measurement_protocol:'v8_2_cycle1'});
 assert.ok(!prepare.calls.some(c=>c.url.endsWith('/start')||c.url.endsWith('/resume')));
 const empty=harness(async()=>{throw Error('must not fetch')});await empty.c.mbPrepare();assert.match(empty.nodes.get('batchPrepareError').textContent,/请选择/);assert.equal(empty.calls.length,0);
 const paused=harness(async url=>url.endsWith('/resources')?{cpu_only:false}:{...record,status:'paused',pause_requested:true});await paused.c.mbLoad();assert.equal(paused.nodes.get('batchResume').disabled,false);assert.equal(paused.nodes.get('batchStart').disabled,true);assert.ok(paused.calls.every(c=>c.method==='GET'));
 assert.match(paused.nodes.get('batchTotals').textContent,/已暂停，可显式接续/);
 const plot=harness(async()=>record),metrics={...record.items[0].metrics,time_ms:[0,5,10],torque_Nm:[0,1,2],selected_window_ms:[0,10]},before=JSON.stringify(metrics);
 plot.c.mbPlot({...record,items:[{...record.items[0],result_available:true,metrics}]});
 assert.equal(plot.nodes.get('batchPlotPanel').hidden,false);assert.equal(plot.nodes.get('batchWaveforms').children.find(n=>n.tag==='polyline').attrs.points.split(' ').length,3);
 assert.equal(plot.nodes.get('batchWaveforms').children.filter(n=>n.tag==='circle').length,3);assert.equal(JSON.stringify(metrics),before);
 plot.c.mbRender({...record,items:[{...record.items[0],result_available:true,metrics:{...metrics,T_min_Nm:-4.5,K_T_ripple_pct:427,torque_numerical_acceptance:false}}]},true);
 assert.match(plot.nodes.get('batchRows').text(),/数值异常/);assert.match(plot.nodes.get('batchRows').text(),/-4\.500000/);assert.match(plot.nodes.get('batchTotals').textContent,/未通过 1项/);
 const identity=harness(async url=>url.endsWith('/material-inputs')?{records:[{id:'cal_a',entries:[{key:'prediction:pred_a',label:'CUSTOM_INPUT · pred_a',available:true,kind:'prediction',excluded_grades:[]}]}]}:record);
 identity.c.mbRender(record,true);await identity.c.mbCatalog();assert.match(identity.nodes.get('batchRows').text(),/CUSTOM_INPUT/);assert.equal(identity.nodes.get('batchStart').disabled,false);assert.ok(identity.calls.every(c=>c.method==='GET'));
 const mixed=structuredClone(record);mixed.status='completed';mixed.totals={materials:2,actual_torque_results:2,new_completed_motor_solves:1};
 mixed.items=[{...mixed.items[0],metrics:{...metrics,torque_numerical_acceptance:true}},
 {...mixed.items[0],id:'item_02',status:'completed',metrics:{...metrics,torque_Nm:[-10,1,2],torque_numerical_acceptance:false}}];
 const select=harness(async()=>mixed);select.c.mbRender(mixed,true);const raw=JSON.stringify(mixed);
 assert.equal(select.nodes.get('batchWaveforms').querySelectorAll('circle').length,6);
 assert.match(select.nodes.get('batchRows').text(),/新增结果已完成/);assert.match(select.nodes.get('batchRows').text(),/复用已有结果/);
 assert.match(select.nodes.get('batchTotals').textContent,/复用 1项/);assert.match(select.nodes.get('batchTotals').textContent,/通过 1项 \/ 未通过 1项 \/ 未报告 0项/);
 const colors=select.nodes.get('batchWaveforms').querySelectorAll('polyline').map(n=>n.attrs.stroke);
 select.c.mbPlotMode('accepted');assert.equal(select.nodes.get('batchWaveforms').querySelectorAll('circle').length,3);
 assert.match(select.urls.at(-1),/plot=accepted/);assert.match(select.nodes.get('batchPlotCount').textContent,/显示 1 \/ 2/);
 select.c.mbRender(mixed,true);assert.equal(select.nodes.get('batchWaveforms').querySelectorAll('circle').length,3);
 select.c.mbPlotMode('none');assert.equal(select.nodes.get('batchWaveforms').children.length,0);assert.equal(select.nodes.get('batchPlotPanel').hidden,false);assert.equal(select.nodes.get('batchPlotEmpty').hidden,false);
 select.c.mbRender(mixed,true);assert.equal(select.nodes.get('batchWaveforms').children.length,0);
 select.c.mbPlotMode('all');const boxes=select.nodes.get('batchPlotChoices').querySelectorAll('input');boxes[0].checked=false;boxes[0].onchange();
 assert.equal(select.nodes.get('batchWaveforms').querySelectorAll('polyline')[0].attrs.stroke,colors[1]);assert.match(select.urls.at(-1),/plot=item_02/);
 select.c.mbRender(mixed,true);assert.equal(select.nodes.get('batchWaveforms').querySelectorAll('circle').length,3);
 const restored=harness(async()=>mixed);restored.c.location.search='?batch='+mixed.id+'&plot=item_02';restored.c.mbRender(mixed,true);
 assert.equal(restored.nodes.get('batchWaveforms').querySelectorAll('circle').length,3);assert.equal(restored.nodes.get('batchPlotChoices').querySelectorAll('input')[0].checked,false);
 restored.c.mbRender({...mixed,id:'bhbatch_bbbbbbbbbbbb'},true);assert.equal(restored.nodes.get('batchWaveforms').querySelectorAll('circle').length,6);
 const invalid=harness(async()=>mixed);invalid.c.location.search='?batch='+mixed.id+'&plot=unknown';invalid.c.mbRender(mixed,true);assert.equal(invalid.nodes.get('batchWaveforms').querySelectorAll('circle').length,6);
 const noResult=harness(async()=>record);noResult.c.mbRender({...record,items:[{...record.items[0],result_available:false,metrics:{T_avg_Nm:999}}]},true);assert.doesNotMatch(noResult.nodes.get('batchRows').text(),/999/);
 assert.equal(JSON.stringify(mixed),raw);assert.equal(select.calls.length,0);assert.equal(restored.calls.length,0);
 console.log('Calibrated batch UI: exact CPU preparation, no auto dispatch, pause/resume gates, zero/null, stale/resource isolation: PASS');
})().catch(error=>{console.error(error);process.exitCode=1});
