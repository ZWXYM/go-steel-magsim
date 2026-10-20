/* Isolated DOM/API adapters: no browser, native worker or production data. */
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
class Node {
 constructor(tag='div'){this.tag=tag;this.children=[];this.textContent='';this.hidden=false;this.disabled=false;this.attrs={}}
 append(...nodes){this.children.push(...nodes)}
 replaceChildren(...nodes){this.children=nodes}
 setAttribute(name,value){this.attrs[name]=String(value)}
 text(){return this.textContent+this.children.map(n=>n.text?n.text():String(n)).join(' ')}
}
function harness(api){
 const nodes=new Map(),calls=[],timers=[];
 const context=vm.createContext({document:undefined,MotorPower:require('../static/js/motor_power.js'),console,Error,Promise,JSON,Number,Math,
  setTimeout:(fn,ms)=>{timers.push(ms);return timers.length},clearTimeout:()=>{},
  fetch:async(url,options={})=>{calls.push({url,method:options.method||'GET'});return {ok:true,json:async()=>api(url,options)}}});
 vm.runInContext(fs.readFileSync(require.resolve('../static/js/calibrated_motor_analysis.js'),'utf8'),context);
 context.document={body:{dataset:{artifact:'bhanalysis_aaaaaaaaaaaa'}},
  getElementById:id=>{if(!nodes.has(id))nodes.set(id,new Node());return nodes.get(id)},
  createElement:tag=>new Node(tag),createElementNS:(ns,tag)=>new Node(tag)};
 const plan={id:'bhanalysis_aaaaaaaaaaaa',status:'model_snapshot_ready_not_submitted',material_name:'WB_current',
  native_import_id:'calimport_test',training_grades:['B23R075'],excluded_grades:['B30P105'],
  requested_protocol:{speed_rpm:3000,cycles:1,source_samples:31,selected_window_ms:[0,10]}};
 context.bhaRenderPlan(plan);return {context,nodes,calls,timers};
}
const metrics={time_ms:[0,10],torque_Nm:[1,2],selected_window_ms:[0,10],T_avg_Nm:1.5,T_min_Nm:1,T_max_Nm:2,K_T_ripple_pct:null};
const current={id:'bhexec_aaaaaaaaaaaa',analysis_plan_id:'bhanalysis_aaaaaaaaaaaa',status:'completed',phase:'finished',result_available:true,
 result:{material_name:'WB_current',model_version:'V8_2_BH_only',measurement_protocol:'v8_2_cycle1',metrics}};
(async()=>{
 // Power comes from this execution's verified operating point, never a page default.
 const p=harness(async()=>[]),verified={...current,requested_protocol:{speed_rpm:3000},result:{...current.result,native_configuration_verified:true,native_analyze_succeeded:true}};
 const expected=1.5*2*Math.PI*3000/60;assert.equal(p.context.bhaPower(verified).power_W,expected);p.context.bhaActual(verified);assert.match(p.nodes.get('actualMetrics').text(),/电磁机械功率.*471\.239.*3000/);
 for(const [torque,power] of [[0,0],[-1.5,-expected]])assert.equal(p.context.bhaPower({...verified,result:{...verified.result,metrics:{...metrics,T_avg_Nm:torque}}}).power_W,power);
 for(const rpm of [undefined,null,NaN,Infinity,-1,0,'3000'])assert.equal(p.context.bhaPower({...verified,requested_protocol:{speed_rpm:rpm}}),null);
 assert.equal(p.context.bhaPower({...verified,result:{...verified.result,native_configuration_verified:false}}),null);assert.equal(p.context.bhaPower({...verified,result_available:false}),null);
 assert.equal(p.context.bhaPower({...verified,result:{...verified.result,metrics:{...metrics,T_avg_Nm:null}}}),null);
 assert.equal(p.context.bhaPower({...verified,requested_protocol:{speed_rpm:1e308},result:{...verified.result,metrics:{...metrics,T_avg_Nm:1e308}}}),null);
 p.context.bhaActual({...verified,requested_protocol:{speed_rpm:1500}});assert.match(p.nodes.get('actualMetrics').text(),/235\.619.*1500/);assert.equal(p.calls.length,0);
 const a=harness(async url=>url.endsWith('/resources')?{cpu_only:false}:[current,{...current,id:'other',analysis_plan_id:'another'}]);
 a.context.bhaRenderReference({dataset:'original_M6',case:'case_01',material:'M6',model_version:'original_V8_2',metrics,torque_csv_url:'/old.csv'});
 const reference=a.nodes.get('referenceStatus').text(),plot=a.nodes.get('torque').children;
 await a.context.bhaLoadExecutions();
 assert.equal(a.nodes.get('actualPanel').hidden,false);assert.match(a.nodes.get('actualStatus').text(),/WB_current/);
 assert.match(a.nodes.get('actualMetrics').text(),/—/);assert.match(a.nodes.get('actualLinks').children[0].href,/bhexec_aaaaaaaaaaaa\/files\/case\/reports/);
 assert.equal(a.nodes.get('referenceStatus').text(),reference);assert.equal(a.nodes.get('torque').children,plot);
 assert.ok(!a.nodes.get('executions').text().includes('other'));assert.ok(a.calls.every(c=>c.method==='GET'));

 const b=harness(async url=>{if(url.endsWith('/resources'))throw Error('fixture resource failure');return [{...current,status:'prepared',result_available:false}]});
 await b.context.bhaLoadExecutions();
 assert.equal(b.nodes.get('executions').children[0].children.find(n=>n.tag==='button').disabled,true);
 assert.match(b.nodes.get('executionError').text(),/资源状态暂不可读/);assert.equal(b.nodes.get('prepareExecution').disabled,false);
 assert.equal(b.nodes.get('actualPanel').hidden,true);assert.ok(b.calls.every(c=>c.method==='GET'));

 let resolveOld,requests=0;const oldResponse=new Promise(resolve=>resolveOld=resolve);
 const c=harness(async url=>url.endsWith('/resources')?{cpu_only:true}:(++requests===1?oldResponse:[{...current,status:'running',result_available:false,phase:'solving'}]));
 const old=c.context.bhaLoadExecutions();await c.context.bhaLoadExecutions();resolveOld([current]);await old;
 assert.equal(c.nodes.get('actualPanel').hidden,true);assert.match(c.nodes.get('executions').text(),/正在分析/);
 assert.match(c.nodes.get('planNotice').text(),/正在执行/);assert.ok(c.timers.includes(3000));assert.ok(c.calls.every(x=>x.method==='GET'));
 const report={torque_numerical_acceptance:false,findings:['存在非正转矩点'],nonpositive_points:[{time_ms:3.333,torque_Nm:-4.557,within_metric_window:true,previous:{torque_Nm:1.48},next:{torque_Nm:1.95},native_profile_row:{final_residual_raw:.201423,matrix_calls:40}}],saved_setup:{UseNonLinearIterNum:'false'},saved_report_expressions:['-Moving1.Torque'],source_hashes:{},warnings:[],nonlinear_convergence_certified:false};
 const d=harness(async()=>report);d.context.bhaActual(current);await d.context.bhaLoadReview(current.id);
 assert.equal(d.nodes.get('savedReviewPanel').hidden,false);assert.match(d.nodes.get('savedReviewStatus').text(),/未通过/);
 assert.match(d.nodes.get('savedReviewPoints').text(),/-4\.557000/);assert.equal(d.nodes.get('savedReviewPoints').children[0].className,'error');assert.match(d.nodes.get('savedReviewPoints').text(),/矩阵调用数 40/);
 assert.match(d.nodes.get('savedReviewSettings').text(),/UseNonLinearIterNum/);assert.ok(d.calls.every(c=>c.method==='GET'));
 let finishReview;const waiting=new Promise(resolve=>finishReview=resolve);const e=harness(async()=>waiting);
 e.context.bhaActual(current);const pending=e.context.bhaLoadReview(current.id);e.context.bhaActual({...current,id:'bhexec_bbbbbbbbbbbb'});
 finishReview(report);await pending;assert.equal(e.nodes.get('savedReviewPanel').hidden,true);assert.equal(e.nodes.get('savedReviewPoints').text(),'');
 const f=harness(async()=>{throw Error('fixture missing profile')});f.context.bhaActual(current);await f.context.bhaLoadReview(current.id);
 assert.match(f.nodes.get('savedReviewError').text(),/missing profile/);assert.equal(f.nodes.get('actualPanel').hidden,false);assert.match(f.nodes.get('actualMetrics').text(),/1.500000/);

 const g=harness(async url=>url.endsWith('/resources')?{cpu_only:false,native_submission_allowed:false,source_restart_required:true}:[{...current,status:'prepared',result_available:false}]);
 await g.context.bhaLoadExecutions();assert.equal(g.nodes.get('executions').children[0].children.find(n=>n.tag==='button').disabled,true);
 assert.match(g.nodes.get('executionError').text(),/源码已更新/);assert.ok(g.calls.every(c=>c.method==='GET'));

 // Refresh preserves an explicitly selected result, its exact links, and an open review.
 const second={...current,id:'bhexec_bbbbbbbbbbbb',result:{...current.result,metrics:{...metrics,T_avg_Nm:2.5}}};
 let selectionRows=[current,second];
 const keep=harness(async url=>url.endsWith('/resources')?{cpu_only:false}:url.endsWith('/review')?report:selectionRows);
 await keep.context.bhaLoadExecutions();keep.context.bhaActual(second);await keep.context.bhaLoadReview(second.id);
 const savedReviewNodes=keep.nodes.get('savedReviewPoints').children;
 await keep.context.bhaLoadExecutions();assert.match(keep.nodes.get('actualStatus').text(),new RegExp(second.id));assert.match(keep.nodes.get('actualMetrics').text(),/2.500000/);
 assert.equal(keep.nodes.get('savedReviewPanel').hidden,false);assert.equal(keep.nodes.get('savedReviewPoints').children,savedReviewNodes);
 assert.match(keep.nodes.get('actualLinks').children[0].href,new RegExp(second.id+'/files/'));
 // New records and reordering do not change the selected result.
 selectionRows=[{...current,id:'bhexec_cccccccccccc'},second,current];await keep.context.bhaLoadExecutions();assert.match(keep.nodes.get('actualStatus').text(),new RegExp(second.id));
 // A temporarily unreadable selected result is explained; no other material/run is substituted.
 selectionRows=[current];await keep.context.bhaLoadExecutions();assert.equal(keep.nodes.get('actualPanel').hidden,true);assert.match(keep.nodes.get('planNotice').text(),new RegExp(second.id));assert.match(keep.nodes.get('planNotice').text(),/暂无可读结果/);
 selectionRows=[current,second];await keep.context.bhaLoadExecutions();assert.equal(keep.nodes.get('actualPanel').hidden,false);assert.match(keep.nodes.get('actualStatus').text(),new RegExp(second.id));assert.equal(keep.nodes.get('savedReviewPanel').hidden,true);
 // A changed result invalidates its previous review rather than carrying stale findings across values.
 await keep.context.bhaLoadReview(second.id);selectionRows=[current,{...second,result:{...second.result,metrics:{...second.result.metrics,T_avg_Nm:3.5}}}];await keep.context.bhaLoadExecutions();assert.equal(keep.nodes.get('savedReviewPanel').hidden,true);assert.match(keep.nodes.get('actualMetrics').text(),/3.500000/);
 // An in-flight read-only review may finish across an unchanged background refresh.
 let finishSameReview;const sameReview=new Promise(resolve=>finishSameReview=resolve);
 const during=harness(async url=>url.endsWith('/resources')?{cpu_only:false}:url.endsWith('/review')?sameReview:[current]);
 await during.context.bhaLoadExecutions();const reviewDuringRefresh=during.context.bhaLoadReview(current.id);await during.context.bhaLoadExecutions();finishSameReview(report);await reviewDuringRefresh;
 assert.equal(during.nodes.get('savedReviewPanel').hidden,false);assert.match(during.nodes.get('savedReviewStatus').text(),/未通过/);
 assert.ok(keep.calls.every(c=>c.method==='GET'));assert.ok(during.calls.every(c=>c.method==='GET'));

 // CPU task preparation remains single even when the execution list is refreshed while waiting.
 let finishPrepare;const waitingPrepare=new Promise(resolve=>finishPrepare=resolve);
 const pendingPrepare=harness(async(url,options)=>options.method==='POST'?waitingPrepare:url.endsWith('/resources')?{cpu_only:true}:[]);
 await pendingPrepare.context.bhaLoadExecutions();const prepareBody={analysis_plan:current.analysis_plan_id};
 const prepFirst=pendingPrepare.context.bhaExecutionAction('/api/workbench/calibrated-motor/executions',prepareBody);
 await pendingPrepare.context.bhaLoadExecutions();assert.equal(pendingPrepare.nodes.get('prepareExecution').disabled,true);
 const prepSecond=pendingPrepare.context.bhaExecutionAction('/api/workbench/calibrated-motor/executions',prepareBody);assert.equal(pendingPrepare.calls.filter(c=>c.method==='POST').length,1);
 finishPrepare({...current,status:'prepared',result_available:false});await Promise.all([prepFirst,prepSecond]);assert.equal(pendingPrepare.nodes.get('prepareExecution').disabled,false);
 // Native start buttons recreated by refresh stay disabled until the single explicit action ends.
 let finishStart;const waitingStart=new Promise(resolve=>finishStart=resolve);let startRows=[{...current,status:'prepared',result_available:false}];
 const starting=harness(async(url,options)=>options.method==='POST'?waitingStart:url.endsWith('/resources')?{cpu_only:false}:startRows);
 await starting.context.bhaLoadExecutions();const startURL='/api/workbench/calibrated-motor/executions/'+current.id+'/start';
 const startFirst=starting.context.bhaExecutionAction(startURL,{});await starting.context.bhaLoadExecutions();
 assert.equal(starting.nodes.get('executions').children[0].children.find(n=>n.tag==='button').disabled,true);
 await starting.context.bhaExecutionAction(startURL,{});assert.equal(starting.calls.filter(c=>c.method==='POST').length,1);
 startRows=[{...current,status:'running',result_available:false}];finishStart(startRows[0]);await startFirst;
 assert.match(starting.nodes.get('executions').text(),/正在分析/);assert.equal(starting.nodes.get('executions').children[0].children.filter(n=>n.tag==='button').length,0);
 // CPU/resource gates also apply to action calls after a previously visible button was replaced.
 const cpuStart=harness(async url=>url.endsWith('/resources')?{cpu_only:true}:[{...current,status:'prepared',result_available:false}]);await cpuStart.context.bhaLoadExecutions();await cpuStart.context.bhaExecutionAction(startURL,{});assert.ok(cpuStart.calls.every(c=>c.method==='GET'));
 const wrongAction=harness(async(url,opt)=>opt.method==='POST'?{...current,analysis_plan_id:'another'}:url.endsWith('/resources')?{cpu_only:false}:[]);await wrongAction.context.bhaLoadExecutions();await wrongAction.context.bhaExecutionAction('/api/workbench/calibrated-motor/executions',prepareBody);assert.match(wrongAction.nodes.get('executionError').text(),/身份与请求不符/);assert.equal(wrongAction.nodes.get('prepareExecution').disabled,true);
 await wrongAction.context.bhaExecutionAction('/api/workbench/calibrated-motor/executions',prepareBody);assert.equal(wrongAction.calls.filter(c=>c.method==='POST').length,1);
 // A plan refresh must not re-enable the pending analysis-model preparation button.
 let finishModel;const waitingModel=new Promise(resolve=>finishModel=resolve);const readyPlan={...pendingPrepare.context.document.body.dataset,id:current.analysis_plan_id,status:'ready_for_model_preparation',material_name:'WB',native_import_id:'calimport_test',training_grades:[],excluded_grades:[],requested_protocol:{speed_rpm:3000,cycles:1,source_samples:31,selected_window_ms:[0,10]}};
 const model=harness(async(url,opt)=>opt.method==='POST'?waitingModel:url.endsWith('/resources')?{cpu_only:true}:url.includes('/analysis-plans/')?readyPlan:[]);model.context.bhaRenderPlan(readyPlan);
 const modelFirst=model.context.bhaPrepareModel();await model.context.bhaLoadPlan();assert.equal(model.nodes.get('prepareModel').disabled,true);await model.context.bhaPrepareModel();assert.equal(model.calls.filter(c=>c.method==='POST').length,1);
 finishModel(readyPlan);await modelFirst;assert.equal(model.nodes.get('prepareModel').disabled,false);
 console.log('Torque execution UI: separate actual/reference results, disabled resource failure, no automatic submit, stale response isolation: PASS');
})().catch(error=>{console.error(error);process.exitCode=1});
