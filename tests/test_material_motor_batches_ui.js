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
const record={id:'bhbatch_aaaaaaaaaaaa',status:'prepared',dispatch_compatible:true,pause_requested:false,cores:2,measurement_protocol:'v8_2_cycle1',
 budget:{max_new_imports:1,max_new_motor_solves:1,cores:2,gpus:0},
 totals:{materials:1,actual_torque_results:1,new_completed_motor_solves:0},items:[{id:'item_01',status:'reused_result',result_available:true,
 selection:{calibration:'cal_a',key:'prediction:pred_a'},source:{material_name:'WB_a',excluded_grades:['B30P105']},
 analysis_url:'/calibrated-motor/analysis/plan_a',execution_id:'bhexec_existing',metrics:{T_avg_Nm:0,T_min_Nm:0,T_max_Nm:0,K_T_ripple_pct:null}}]};
function harness(api){
 const nodes=new Map(),calls=[],timers=[],urls=[];
 const c=vm.createContext({document:undefined,MotorPower:require('../static/js/motor_power.js'),URLSearchParams,location:{search:'',pathname:'/calibrated-motor/batches'},history:{replaceState:(state,title,url)=>urls.push(url)},setTimeout:(fn,ms)=>{timers.push(ms);return timers.length},clearTimeout:()=>{},
 fetch:async(url,options={})=>{calls.push({url,method:options.method||'GET',body:options.body});return {ok:true,json:async()=>api(url,options)}}});
 vm.runInContext(fs.readFileSync('static/js/material_motor_batches.js','utf8'),c);
 c.document={getElementById:id=>{if(!nodes.has(id))nodes.set(id,new Node(['batchHistory','batchCalibration','batchBaseline'].includes(id)?'select':'div'));return nodes.get(id)},createElement:tag=>new Node(tag),createElementNS:(ns,tag)=>new Node(tag)};
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
 const linked=structuredClone(record);linked.items[0].analysis_url='/calibrated-motor/analysis/bhanalysis_aaaaaaaaaaaa';linked.items[0].execution_id='bhexec_bbbbbbbbbbbb';
 const exact=harness(async()=>linked);exact.c.mbRender(linked,true);
 assert.ok(exact.nodes.get('batchRows').querySelectorAll('a').some(a=>a.href==='/calibrated-motor/analysis/bhanalysis_aaaaaaaaaaaa?execution=bhexec_bbbbbbbbbbbb'));
 assert.equal(exact.calls.length,0);
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

 // Refresh keeps only available keys under the same calibration identity.
 const entry=(key,available=true)=>({key,available,kind:'fit',label:key,excluded_grades:[],reason:'unavailable'});
 let catalogs={records:[{id:'cal_a',entries:[entry('fit:B23R075'),entry('fit:B27R090')]},{id:'cal_b',entries:[entry('fit:B23R075')]}]};
 const checked=harness(async()=>catalogs);await checked.c.mbCatalog();checked.c.mbSelectFits();
 assert.equal(vm.runInContext('MBB.selected.size',checked.c),2);await checked.c.mbCatalog();
 assert.equal(vm.runInContext('MBB.selected.size',checked.c),2);assert.ok(checked.nodes.get('batchMaterials').querySelectorAll('input').every(n=>n.checked));
 catalogs.records[0].entries[1].available=false;await checked.c.mbCatalog();
 assert.deepEqual(Array.from(vm.runInContext('MBB.selected',checked.c)),['fit:B23R075']);
 assert.equal(checked.nodes.get('batchMaterials').querySelectorAll('input')[1].checked,false);
 checked.nodes.get('batchCalibration').value='cal_b';checked.c.mbEntries();assert.equal(vm.runInContext('MBB.selected.size',checked.c),0);
 checked.c.mbSelectFits();await checked.c.mbCatalog();assert.equal(vm.runInContext('MBB.selected.size',checked.c),1);

 // Switching is a load barrier: no action can submit to the previous batch.
 let ready;const wait=new Promise(r=>ready=r),next='bhbatch_bbbbbbbbbbbb';
 const switching=harness(async(url,options)=>url.endsWith('/resources')?{cpu_only:false}:url.endsWith('/'+next)?wait:record);
 await switching.c.mbLoad();switching.nodes.get('batchHistory').value=next;const loading=switching.c.mbLoad();
 for(const id of ['batchStart','batchResume','batchPause'])assert.equal(switching.nodes.get(id).disabled,true);
 assert.equal(switching.nodes.get('batchBundle').hidden,true);assert.equal(switching.nodes.get('batchRows').children.length,0);
 await switching.c.mbAction('start');assert.equal(switching.calls.filter(c=>c.method==='POST').length,0);
 ready({...record,id:next});await loading;assert.equal(switching.nodes.get('batchStart').disabled,false);
 await switching.c.mbAction('start');assert.equal(switching.calls.filter(c=>c.method==='POST').length,1);
 assert.ok(switching.calls.find(c=>c.method==='POST').url.endsWith('/'+next+'/start'));

 // Late reads from another batch cannot replace the selected record.
 let oldReady;const oldWait=new Promise(r=>oldReady=r);
 const cross=harness(async url=>url.endsWith('/resources')?{cpu_only:false}:url.endsWith('/'+record.id)?oldWait:{...record,id:next});
 const oldRead=cross.c.mbLoad();cross.nodes.get('batchHistory').value=next;await cross.c.mbLoad();oldReady(record);await oldRead;
 assert.equal(vm.runInContext('MBB.record.id',cross.c),next);assert.match(cross.nodes.get('batchStatus').textContent,/bbbbbbbbbbbb/);

 // A missing or failed history clears old data, bundle and executable binding.
 const missing=harness(async url=>url.endsWith('/resources')?{cpu_only:false}:url.endsWith('/batches')?[record]:record);
 await missing.c.mbLoad();await missing.c.mbHistory('bhbatch_missing');await missing.c.mbAction('start');
 assert.equal(missing.nodes.get('batchHistory').value,'');assert.equal(vm.runInContext('MBB.record',missing.c),null);
 assert.equal(missing.nodes.get('batchBundle').hidden,true);assert.equal(missing.nodes.get('batchRows').children.length,0);
 assert.equal(missing.calls.filter(c=>c.method==='POST').length,0);
 const badID=harness(async url=>url.endsWith('/resources')?{cpu_only:false}:record);badID.nodes.get('batchHistory').value=next;await badID.c.mbLoad();
 assert.equal(vm.runInContext('MBB.record',badID.c),null);assert.match(badID.nodes.get('batchError').textContent,/身份/);
 let readFails=false;const readError=harness(async url=>url.endsWith('/resources')?{cpu_only:false}:readFails?Promise.reject(Error('read failed')):record);
 await readError.c.mbLoad();readFails=true;await readError.c.mbLoad();await readError.c.mbAction('start');
 assert.equal(readError.nodes.get('batchBundle').hidden,true);assert.equal(readError.nodes.get('batchRows').children.length,0);
 assert.equal(readError.calls.filter(c=>c.method==='POST').length,0);

 // A slow action remains single-submit through catalog refresh and a batch switch.
 let actionReject;const actionWait=new Promise((resolve,reject)=>actionReject=reject);
 const pending=harness(async(url,options)=>url.endsWith('/material-inputs')?catalogs:url.endsWith('/resources')?{cpu_only:false}:options.method==='POST'?actionWait:url.endsWith('/'+next)?{...record,id:next}:record);
 await pending.c.mbLoad();const pendingAction=pending.c.mbAction('start');await pending.c.mbAction('start');await pending.c.mbCatalog();
 assert.equal(pending.nodes.get('batchStart').disabled,true);assert.equal(pending.calls.filter(c=>c.method==='POST').length,1);
 pending.nodes.get('batchHistory').value=next;await pending.c.mbLoad();assert.equal(pending.nodes.get('batchStart').disabled,true);
 actionReject(Error('previous batch action failed'));await pendingAction;
 assert.equal(vm.runInContext('MBB.record.id',pending.c),next);assert.doesNotMatch(pending.nodes.get('batchError').textContent,/previous batch/);
 assert.equal(pending.nodes.get('batchStart').disabled,false);assert.equal(pending.calls.filter(c=>c.method==='POST').length,1);

 // Stale catalog responses cannot restore a superseded calibration selection.
 let catalogReady,catalogCalls=0;const catalogWait=new Promise(r=>catalogReady=r);
 const catalogRace=harness(async()=>++catalogCalls===1?catalogWait:{records:[{id:'cal_b',entries:[entry('fit:B23R075')]}]});
 const firstCatalog=catalogRace.c.mbCatalog();await catalogRace.c.mbCatalog();catalogReady(catalogs);await firstCatalog;
 assert.equal(catalogRace.nodes.get('batchCalibration').value,'cal_b');assert.equal(vm.runInContext('MBB.catalog[0].id',catalogRace.c),'cal_b');

 // Read the matching saved operating point independently; no start, plan or result writes.
 const powerMetrics={...metrics,T_avg_Nm:0,torque_csv_sha256:'a'.repeat(64)},powerItem={...record.items[0],execution_id:'bhexec_111111111111',measurement_protocol:'cycle',model_version:'model',metrics:powerMetrics,records:{execution:{id:'bhexec_111111111111'},analysis_plan:{id:'bhanalysis_222222222222'}},source:{...record.items[0].source,effective_bank_sha256:'bank'}};
 const powerRun={id:powerItem.execution_id,analysis_plan_id:powerItem.records.analysis_plan.id,result_available:true,material_name:'WB_a',effective_bank_sha256:'bank',excluded_grades:['B30P105'],measurement_protocol:'cycle',requested_protocol:{speed_rpm:3000},result:{metrics:powerMetrics,model_version:'model',material_name:'WB_a',native_configuration_verified:true,native_analyze_succeeded:true}},powerBatch={...record,status:'completed',items:[powerItem]};
 const flush=()=>new Promise(resolve=>setImmediate(resolve)),powerUI=harness(async()=>powerRun);powerUI.c.mbRender(powerBatch,true);await flush();assert.match(powerUI.nodes.get('batchRows').children[0].children[4].text(),/0\.000.*3000/);assert.equal(powerUI.calls.length,1);
 powerUI.c.mbRender(powerBatch,true);await flush();assert.equal(powerUI.calls.length,1,'unchanged saved execution shares its read');assert.ok(powerUI.calls.every(c=>c.method==='GET'));
 let finishPower;const pendingPower=new Promise(resolve=>finishPower=resolve),powerRace=harness(async()=>pendingPower);powerRace.c.mbRender(powerBatch,true);const oldCell=powerRace.nodes.get('batchRows').children[0].children[4];powerRace.c.mbRender(powerBatch,true);const currentCell=powerRace.nodes.get('batchRows').children[0].children[4];finishPower(powerRun);await flush();assert.match(currentCell.text(),/0\.000.*3000/);assert.match(oldCell.text(),/读取工况/);assert.equal(powerRace.calls.length,1);
 let finishSwitched;const switchedPower=new Promise(resolve=>finishSwitched=resolve),powerSwitch=harness(async()=>switchedPower);powerSwitch.c.mbRender(powerBatch,true);powerSwitch.c.mbRender({...powerBatch,id:'bhbatch_333333333333',items:[]},true);finishSwitched(powerRun);await flush();assert.equal(powerSwitch.nodes.get('batchRows').children.length,0);assert.match(powerSwitch.nodes.get('batchStatus').textContent,/bhbatch_333333333333/);
 let powerFailures=0;const powerFailure=harness(async()=>{if(++powerFailures===1)throw Error('saved speed unreadable');return powerRun});powerFailure.c.mbRender(powerBatch,true);await flush();assert.match(powerFailure.nodes.get('batchRows').children[0].children[4].text(),/工况未核对/);powerFailure.c.mbRender(powerBatch,true);await flush();assert.match(powerFailure.nodes.get('batchRows').children[0].children[4].text(),/0\.000.*3000/);assert.equal(powerFailures,2);
 const powerMismatch=harness(async()=>({...powerRun,id:'bhexec_444444444444'}));powerMismatch.c.mbRender(powerBatch,true);await flush();assert.match(powerMismatch.nodes.get('batchRows').children[0].children[4].text(),/工况未核对/);assert.match(powerMismatch.nodes.get('batchRows').text(),/0\.000000/);
 const missingPower=harness(async()=>{throw Error('must not fetch without helper')});missingPower.c.MotorPower=undefined;missingPower.c.mbRender(powerBatch,true);await flush();assert.equal(missingPower.calls.length,0);assert.match(missingPower.nodes.get('batchRows').text(),/工况未核对/);
 assert.equal(JSON.stringify(powerMetrics),JSON.stringify(powerRun.result.metrics));
 // A delayed CPU plan must not replace the batch the user switched to.
 let prepareReady;const prepareWait=new Promise(resolve=>prepareReady=resolve);
 const delayedPrepare=harness(async(url,options)=>options.method==='POST'?prepareWait:url.endsWith('/resources')?{cpu_only:false}:url.endsWith('/batches')?[record,{...record,id:next}]:url.endsWith('/'+next)?{...record,id:next}:record);
 vm.runInContext('MBB.selected=new Set(["prediction:pred_a"])',delayedPrepare.c);
 await delayedPrepare.c.mbLoad();const preparing=delayedPrepare.c.mbPrepare();
 delayedPrepare.nodes.get('batchHistory').value=next;await delayedPrepare.c.mbLoad();
 prepareReady(record);await preparing;
 assert.equal(delayedPrepare.nodes.get('batchHistory').value,next,'late preparation must retain the newly viewed batch');
 assert.equal(vm.runInContext('MBB.record.id',delayedPrepare.c),next);
 assert.ok(delayedPrepare.nodes.get('batchPrepareStatus').querySelectorAll('a').some(a=>a.href==='/calibrated-motor/batches?batch='+record.id));
 assert.equal(delayedPrepare.calls.filter(c=>c.method==='POST').length,1);
 // Configuration edits preserve the form and expose the earlier saved plan.
 for(const field of ['batchCores','batchProtocol','batchCalibration','selection']){
  let finish;const wait=new Promise(resolve=>finish=resolve),race=harness(async(url,options)=>options.method==='POST'?wait:record);
  vm.runInContext('MBB.selected=new Set(["prediction:pred_a"])',race.c);const pending=race.c.mbPrepare();
  if(field==='selection')vm.runInContext('MBB.selected=new Set(["fit:B23R075"])',race.c);
  else race.nodes.get(field).value={batchCores:'1',batchProtocol:'periodic_cycle3_v1',batchCalibration:'cal_b'}[field];
  await race.c.mbPrepare();finish(record);await pending;
  assert.equal(race.calls.length,1);assert.equal(race.calls[0].method,'POST');
  assert.match(race.nodes.get('batchPrepareStatus').text(),/已保存/);assert.equal(race.nodes.get('batchPrepare').disabled,false);
 }
 // A second late window exists while the saved-history GET is pending.
 for(const reject of [false,true]){
  let finishHistory;const historyWait=new Promise((resolve,fail)=>finishHistory=reject?fail:resolve);
  const race=harness(async(url,options)=>options.method==='POST'?record:url.endsWith('/resources')?{cpu_only:false}:url.endsWith('/batches')?historyWait:url.endsWith('/'+next)?{...record,id:next}:record);
  vm.runInContext('MBB.selected=new Set(["prediction:pred_a"])',race.c);await race.c.mbLoad();const pending=race.c.mbPrepare();await flush();
  race.nodes.get('batchHistory').value=next;await race.c.mbLoad();
  finishHistory(reject?Error('earlier history failed'):[record,{...record,id:next}]);await pending;
  assert.equal(vm.runInContext('MBB.record.id',race.c),next);assert.doesNotMatch(race.nodes.get('batchError').textContent,/earlier history/);
  assert.match(race.nodes.get('batchPrepareStatus').text(),/已保存/);
 }
 // A failed earlier POST cannot overwrite errors belonging to a newly edited form.
 let rejectPrepare;const failedWait=new Promise((resolve,reject)=>rejectPrepare=reject),failedPrepare=harness(async()=>failedWait);
 vm.runInContext('MBB.selected=new Set(["prediction:pred_a"])',failedPrepare.c);const failedPending=failedPrepare.c.mbPrepare();
 failedPrepare.nodes.get('batchCores').value='1';failedPrepare.nodes.get('batchPrepareError').textContent='current form message';
 rejectPrepare(Error('older preparation failed'));await failedPending;
 assert.equal(failedPrepare.nodes.get('batchPrepareError').textContent,'current form message');assert.match(failedPrepare.nodes.get('batchPrepareStatus').text(),/先前请求未确认/);
 // Unexpected identity/configuration never navigates or automatically retries.
 for(const bad of [{...record,id:'invalid'},{...record,cores:1},{...record,measurement_protocol:'other'},{...record,items:[]}]){
  const mismatch=harness(async()=>bad);vm.runInContext('MBB.selected=new Set(["prediction:pred_a"])',mismatch.c);await mismatch.c.mbPrepare();
  assert.match(mismatch.nodes.get('batchPrepareError').textContent,/不一致/);assert.equal(mismatch.calls.length,1);
 }
 // Starting an explicitly viewed batch also takes precedence over a pending plan.
 let finishPrepared;const waitPrepared=new Promise(resolve=>finishPrepared=resolve),newPlan={...record,id:'bhbatch_cccccccccccc'};
 const actDuringPrepare=harness(async(url,options)=>url.endsWith('/batches')&&options.method==='POST'?waitPrepared:url.endsWith('/resources')?{cpu_only:false}:record);
 vm.runInContext('MBB.selected=new Set(["prediction:pred_a"])',actDuringPrepare.c);await actDuringPrepare.c.mbLoad();const waitingPrepare=actDuringPrepare.c.mbPrepare();
 await actDuringPrepare.c.mbAction('start');finishPrepared(newPlan);await waitingPrepare;
 assert.equal(vm.runInContext('MBB.record.id',actDuringPrepare.c),record.id);assert.equal(actDuringPrepare.calls.filter(c=>c.method==='POST').length,2);
 assert.equal(actDuringPrepare.calls.filter(c=>c.url.endsWith('/start')).length,1);assert.match(actDuringPrepare.nodes.get('batchPrepareStatus').text(),/已保存/);
 // Normal unchanged preparation still opens exactly its returned plan.
 assert.equal(vm.runInContext('MBB.record.id',prepare.c),record.id);assert.match(prepare.nodes.get('batchPrepareStatus').text(),/尚未开始/);
 // Baseline comparison is explicit, GET-only and bound to both the viewed batch and selected item.
 const compareRecord=structuredClone(mixed);compareRecord.items[0].metrics.torque_numerical_acceptance=true;
 const comparison=(batch=compareRecord.id,baseline='item_01')=>({version:'batch_baseline_comparison_v1',batch,baseline_item:baseline,entries:compareRecord.items.map((item,i)=>({item:item.id,comparison_status:'same_saved_setup',delta_T_avg_Nm:i?-1:0,delta_T_avg_pct:i?-50:0,delta_power_W:i?-314.159:0,delta_power_pct:i?-50:0,torque_numerical_acceptance:i?false:true,notes:i?['标定来源不同，差值同时包含校准库/留出范围差异']:[],comparison_reasons:[]}))});
 const compared=harness(async()=>comparison());compared.c.mbRender(compareRecord,false);
 assert.equal(compared.calls.length,0);assert.equal(compared.nodes.get('batchBaseline').value,'');assert.equal(compared.nodes.get('batchCompareRead').disabled,true);
 compared.nodes.get('batchBaseline').value='item_01';compared.c.mbComparisonClear();await compared.c.mbCompare();
 assert.equal(compared.calls.length,1);assert.equal(compared.calls[0].method,'GET');assert.match(compared.calls[0].url,/comparison\?baseline=item_01$/);
 assert.match(compared.nodes.get('batchCompareRows').text(),/-50\.000000/);assert.match(compared.nodes.get('batchCompareRows').text(),/数值异常/);assert.match(compared.nodes.get('batchCompareRows').text(),/标定来源不同/);
 assert.match(compared.nodes.get('batchCompareCSV').href,/baseline=item_01&format=csv$/);assert.equal(compared.nodes.get('batchCompareCSV').hidden,false);
 assert.match(compared.nodes.get('batchCompareZIP').href,/baseline=item_01&format=zip$/);assert.equal(compared.nodes.get('batchCompareZIP').download,compareRecord.id+'_item_01_comparison.zip');
 const displayed=compared.nodes.get('batchCompareRows').text();compared.c.mbRender(compareRecord,false);assert.equal(compared.nodes.get('batchCompareRows').text(),displayed);assert.equal(compared.calls.length,1);
 compared.nodes.get('batchBaseline').value='item_02';compared.c.mbComparisonClear();assert.equal(compared.nodes.get('batchCompareRows').children.length,0);assert.equal(compared.nodes.get('batchCompareCSV').hidden,true);
 // Switching the baseline during an earlier read makes the earlier response inert.
 let resolveCompare;const waitCompare=new Promise(resolve=>resolveCompare=resolve),late=harness(async()=>waitCompare);late.c.mbRender(compareRecord,false);
 late.nodes.get('batchBaseline').value='item_01';late.c.mbComparisonClear();const oldCompare=late.c.mbCompare();
 await late.c.mbCompare();assert.equal(late.calls.length,1);late.nodes.get('batchBaseline').value='item_02';late.c.mbComparisonClear();
 resolveCompare(comparison());await oldCompare;assert.equal(late.nodes.get('batchCompareRows').children.length,0);assert.equal(late.nodes.get('batchCompareCSV').hidden,true);assert.equal(late.nodes.get('batchCompareRead').disabled,false);
 // A new batch, malformed response or unavailable result never gains a downloadable comparison.
 let finishCompare;const swapWait=new Promise(resolve=>finishCompare=resolve),another=harness(async()=>swapWait);another.c.mbRender(compareRecord,false);
 another.nodes.get('batchBaseline').value='item_01';another.c.mbComparisonClear();const pendingComparison=another.c.mbCompare();
 another.c.mbRender({...compareRecord,id:'bhbatch_bbbbbbbbbbbb'},false);finishCompare(comparison());await pendingComparison;
 assert.equal(another.nodes.get('batchCompareCSV').hidden,true);assert.equal(another.nodes.get('batchBaseline').value,'');
 for(const response of [comparison('wrong'),comparison(compareRecord.id,'item_02'),{...comparison(),entries:[]},(()=>{const r=comparison();r.entries[1].delta_T_avg_Nm=NaN;return r})()]){
  const invalidCompare=harness(async()=>response);invalidCompare.c.mbRender(compareRecord,false);invalidCompare.nodes.get('batchBaseline').value='item_01';invalidCompare.c.mbComparisonClear();await invalidCompare.c.mbCompare();
  assert.ok(invalidCompare.nodes.get('batchCompareError').textContent);assert.equal(invalidCompare.nodes.get('batchCompareCSV').hidden,true);assert.equal(invalidCompare.nodes.get('batchCompareRows').children.length,0);
 }
 const pendingOnly=harness(async()=>{throw Error('must not fetch')});pendingOnly.c.mbRender({...compareRecord,items:compareRecord.items.map(i=>({...i,result_available:false}))},false);
 pendingOnly.nodes.get('batchBaseline').value='item_01';await pendingOnly.c.mbCompare();assert.equal(pendingOnly.calls.length,0);
 // A transient record=null during a same-batch refresh is not a change of viewed result.
 let finishComparison,finishRefresh;const duringComparison=new Promise(resolve=>finishComparison=resolve),duringRefresh=new Promise(resolve=>finishRefresh=resolve);
 const refreshingComparison=harness(async url=>url.includes('/comparison?')?duringComparison:url.endsWith('/resources')?{cpu_only:false}:duringRefresh);
 refreshingComparison.c.mbRender(compareRecord,false);refreshingComparison.nodes.get('batchBaseline').value='item_01';refreshingComparison.c.mbComparisonClear();
 const pendingBaseline=refreshingComparison.c.mbCompare(),sameBatchRefresh=refreshingComparison.c.mbLoad();
 finishComparison(comparison());await pendingBaseline;
 assert.equal(refreshingComparison.nodes.get('batchCompareRows').children.length,2,'same-batch status refresh must preserve the pending comparison response');
 assert.equal(refreshingComparison.nodes.get('batchCompareRead').disabled,true,'new comparison reads wait until the batch refresh finishes');
 finishRefresh(compareRecord);await sameBatchRefresh;
 assert.equal(refreshingComparison.nodes.get('batchCompareRows').children.length,2);assert.equal(refreshingComparison.nodes.get('batchCompareCSV').hidden,false);
 assert.doesNotMatch(refreshingComparison.nodes.get('batchCompareStatus').textContent,/正在读取/);assert.equal(refreshingComparison.nodes.get('batchCompareRead').disabled,false);
 assert.ok(refreshingComparison.calls.every(c=>c.method==='GET'));assert.equal(refreshingComparison.calls.filter(c=>c.url.includes('/comparison?')).length,1);
 compared.c.mbReset('reset');assert.equal(compared.nodes.get('batchComparePanel').hidden,true);assert.equal(compared.nodes.get('batchCompareCSV').hidden,true);
 assert.equal(compared.nodes.get('batchCompareZIP').hidden,true);assert.equal(compared.nodes.get('batchCompareZIP').href,'');
 console.log('Calibrated batch UI: explicit GET-only baseline deltas, exact CPU preparation, stale/resource isolation, pause/resume, bound power and cached reads: PASS');
})().catch(error=>{console.error(error);process.exitCode=1});
