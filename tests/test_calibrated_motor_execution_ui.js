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
 const context=vm.createContext({document:undefined,console,Error,Promise,JSON,Number,Math,
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

 console.log('Torque execution UI: separate actual/reference results, disabled resource failure, no automatic submit, stale response isolation: PASS');
})().catch(error=>{console.error(error);process.exitCode=1});
