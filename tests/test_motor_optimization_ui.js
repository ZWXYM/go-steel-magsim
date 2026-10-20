/* Real optimization page functions, fake responses only: no files or solvers. */
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const project=require('node:path').resolve(__dirname,'..');
const script=fs.readFileSync(project+'/static/js/motor_optimization.js','utf8');
const defaults={min_ripple_reduction_pct:'30',max_iron_loss_increase_pct:'0',min_torque_change_pct:'0',ripple_weight:'0.60',iron_loss_weight:'0.30',efficiency_weight:'10',planCores:'4',planProtocol:'v8_2_cycle1'};
const config=Object.fromEntries(Object.entries(defaults).filter(([k])=>!k.startsWith('plan')).map(([k,v])=>[k,Number(v)]));
const a={id:'v8_1',version:'VERSION_A',measurement_protocol:'original',scope:'A',references:[{case:'original_control',material:'M6'}],default_reference:'original_control'};
const b={...a,id:'v8_2_archive',version:'VERSION_B'};
const report=(dataset=a)=>({id:'opt_aaaaaaaaaaaa',dataset,reference:'original_control',torque_reference:'original_baseline',config:{...config},ranked:[{case:'case_zero',material:'M6',score:0,meets_limits:true,metrics:{T_avg_Nm:0,K_T_ripple_pct:0,P_Fe_W:null,eta_estimate_pct:0},reasons:[]}],files:{'ranking.csv':'/saved.csv'},meets_limits_count:1,best_case:'case_zero'});
function harness(api,materials=['M6']){
 const nodes=new Map(),calls=[];let planButtons=[];
 function node(id){if(!nodes.has(id))nodes.set(id,{value:defaults[id]||'',hidden:true,disabled:false,textContent:'',_html:'',
  set innerHTML(s){this._html=s;if(['dataset','reference','planReference'].includes(id)){const first=s.match(/<option(?: value="([^"]*)")?>([^<]*)<\/option>/);this.value=first?(first[1]??first[2]):''}if(id==='plans')planButtons=Array.from(s.matchAll(/<button data-plan-id="([^"]*)" ([^>]*)>/g),m=>({dataset:{planId:m[1]},disabled:m[2].includes('disabled')}))},
  get innerHTML(){return this._html}});return nodes.get(id)}
 const c=vm.createContext({document:undefined,setTimeout:()=>0,console,fetch:async(url,options={})=>{calls.push({url,method:options.method||'GET',body:options.body});return {ok:true,json:async()=>api(url,options)}}});
 vm.runInContext(script,c);c.document={getElementById:node,querySelectorAll:selector=>selector==='[data-plan-id]'?planButtons:selector==='.planMaterial:checked'?materials.map(value=>({value})):[]};
 node('dataset').value=a.id;return {c,node,calls,buttons:()=>planButtons};
}
const value=(h,expression)=>vm.runInContext(expression,h.c);
const posts=h=>h.calls.filter(c=>c.method==='POST');
async function loaded(h,dataset=a){h.node('dataset').value=dataset.id;await h.c.loadDataset()}
(async()=>{
 const html=fs.readFileSync(project+'/templates/motor_optimization.html','utf8');assert.match(html,/src="\/static\/js\/motor_optimization.js" defer/);assert.equal((html.match(/oninput="screeningChanged\(\)"/g)||[]).length,6);
 const valid=harness(async(url,options)=>url.endsWith('/analyze')?report():url.endsWith('/history')?[]:a);
 await loaded(valid);await valid.c.analyze();assert.equal(valid.node('result').hidden,false);
 assert.match(valid.node('result').innerHTML,/v8_1 \/ VERSION_A \/ original/);assert.match(valid.node('result').innerHTML,/转矩基准 original_baseline/);
 assert.match(valid.node('result').innerHTML,/0\.000/);assert.match(valid.node('result').innerHTML,/—/);
 assert.deepEqual(JSON.parse(posts(valid)[0].body),{dataset:a.id,reference:'original_control',config});assert.equal(posts(valid).length,1);
 const historyError=harness(async url=>url.endsWith('/analyze')?report():url.endsWith('/history')?Promise.reject(Error('history offline')):a);
 await loaded(historyError);await historyError.c.analyze();assert.equal(historyError.node('result').hidden,false);assert.match(historyError.node('analysisStatus').textContent,/历史列表刷新失败/);

 let finish;const delayed=new Promise(r=>finish=r),stale=harness(async url=>url.endsWith('/analyze')?delayed:url.endsWith('/history')?[]:url.endsWith('/v8_1')?a:b);
 await loaded(stale);const saving=stale.c.analyze();await stale.c.analyze();await loaded(stale,b);
 assert.equal(stale.node('analyze').disabled,true);finish(report());await saving;
 assert.equal(stale.node('result').hidden,true);assert.equal(value(stale,'current.id'),b.id);assert.equal(posts(stale).length,1);
 assert.match(stale.node('analysisStatus').textContent,/先前配置.*历史/);assert.equal(stale.node('analyze').disabled,false);

 let configFinish;const configWait=new Promise(r=>configFinish=r),changed=harness(async url=>url.endsWith('/analyze')?configWait:url.endsWith('/history')?[]:a);
 await loaded(changed);const configSave=changed.c.analyze();changed.node('ripple_weight').value='0.2';changed.c.screeningChanged();configFinish(report());await configSave;
 assert.equal(changed.node('result').hidden,true);assert.equal(changed.node('ripple_weight').value,'0.2');
 for(const wrong of [{dataset:b},{reference:'wrong'},{config:{...config,ripple_weight:99}}]){
  const mismatch=harness(async url=>url.endsWith('/analyze')?{...report(),...wrong}:url.endsWith('/history')?[]:a);
  await loaded(mismatch);await mismatch.c.analyze();assert.equal(mismatch.node('result').hidden,true);assert.match(mismatch.node('error').textContent,/来源、基准或配置/);
 }

 let listReady;const listWait=new Promise(r=>listReady=r),refresh=harness(async url=>url.endsWith('/datasets')?listWait:url.endsWith('/v8_1')?a:b);
 await loaded(refresh);const refreshing=refresh.c.refreshDatasets();await loaded(refresh,b);listReady([{id:a.id,title:'A'},{id:b.id,title:'B'}]);await refreshing;
 assert.equal(refresh.node('dataset').value,b.id);assert.equal(value(refresh,'current.id'),b.id);
 let oldDatasetReady;const oldDatasetWait=new Promise(r=>oldDatasetReady=r),dataRace=harness(async url=>url.endsWith('/v8_1')?oldDatasetWait:b);
 const oldRead=dataRace.c.loadDataset();await loaded(dataRace,b);oldDatasetReady(a);await oldRead;assert.equal(value(dataRace,'current.id'),b.id);
 const badData=harness(async()=>b);await loaded(badData);assert.equal(value(badData,'current'),null);assert.equal(badData.node('analyze').disabled,true);

 const plan={id:'plan_aaaaaaaaaaaa',status:'prepared',materials:['M6'],reference_material:'M6'};
 let submitReady;const submitWait=new Promise(r=>submitReady=r),pending=harness(async(url,options)=>options.method==='POST'?submitWait:[plan]);
 value(pending,'planCpuOnly=false');await pending.c.planRecords();const submitted=pending.c.submitPlan(plan.id);await pending.c.submitPlan(plan.id);await pending.c.planRecords();
 assert.equal(posts(pending).length,1);assert.equal(pending.buttons()[0].disabled,true);
 submitReady({});await submitted;assert.equal(pending.buttons()[0].disabled,false);assert.ok(posts(pending)[0].url.endsWith('/'+plan.id+'/submit'));
 const cpu=harness(async()=>{throw Error('CPU must not submit')});await cpu.c.submitPlan(plan.id);assert.equal(cpu.calls.length,0);

 let oldPlanReady,planCalls=0;const oldPlanWait=new Promise(r=>oldPlanReady=r),planRace=harness(async()=>++planCalls===1?oldPlanWait:[{...plan,status:'completed'}]);
 const oldPlans=planRace.c.planRecords();await planRace.c.planRecords();oldPlanReady([plan]);await oldPlans;
 assert.match(planRace.node('plans').innerHTML,/自动筛选完成/);assert.equal(planRace.buttons().length,0);
 let createReady;const createWait=new Promise(r=>createReady=r),create=harness(async(url,options)=>options.method==='POST'?createWait:url.endsWith('/plans')||url.endsWith('/history')?[]:a);
 await loaded(create);value(create,'planAvailable=true');create.c.updateScreeningButtons();create.node('planReference').value='M6';
 const creating=create.c.preparePlan(false);await create.c.preparePlan(false);assert.equal(posts(create).length,1);
 await loaded(create,b);assert.equal(create.node('preparePlan').disabled,true);createReady({});await creating;
 assert.equal(create.node('preparePlan').disabled,false);assert.deepEqual(JSON.parse(posts(create)[0].body),{config,materials:['M6'],reference_material:'M6',cores:4,measurement_protocol:'v8_2_cycle1'});

 let rejectOld;const oldErrorWait=new Promise((resolve,reject)=>rejectOld=reject),oldError=harness(async url=>url.endsWith('/analyze')?oldErrorWait:url.endsWith('/v8_1')?a:b);
 await loaded(oldError);const failed=oldError.c.analyze();await loaded(oldError,b);rejectOld(Error('old selection failed'));await failed;
 assert.equal(oldError.node('result').hidden,true);assert.equal(oldError.node('error').hidden,true);assert.equal(value(oldError,'current.id'),b.id);
 // Read saved rankings separately; reuse is an explicit source-checked GET action.
 const hashes={'source.csv':'a'.repeat(64),'other.csv':'b'.repeat(64)},restoreData={...a,source_hashes:hashes,references:[...a.references,{case:'original_baseline',material:'Baseline'}]},saved={...report(restoreData),config:{...config,min_ripple_reduction_pct:7,ripple_weight:.25}};
 const reopen=harness(async url=>url.includes('/files/analysis.json')?saved:restoreData);await loaded(reopen,restoreData);
 const inputsBefore=value(reopen,'configValues()');await reopen.c.loadSavedScreen(saved.id);
 assert.equal(reopen.node('savedScreenPanel').hidden,false);assert.match(reopen.node('savedScreenResult').innerHTML,/保存的筛选结果/);assert.match(reopen.node('savedScreenResult').innerHTML,/original_baseline/);
 assert.deepEqual(JSON.parse(JSON.stringify(value(reopen,'configValues()'))),JSON.parse(JSON.stringify(inputsBefore)));assert.equal(reopen.node('result').hidden,true);
 await reopen.c.reuseSavedScreen();assert.equal(reopen.node('ripple_weight').value,'0.25');assert.equal(reopen.node('min_ripple_reduction_pct').value,'7');assert.equal(reopen.node('reference').value,saved.reference);assert.match(reopen.node('analysisStatus').textContent,/尚未重新筛选/);assert.equal(posts(reopen).length,0);
 // Changed source/version/protocol/missing reference leave the current form untouched.
 for(const change of [{source_hashes:{...hashes,'source.csv':'c'.repeat(64)}},{version:'changed'},{measurement_protocol:'other'},{references:[]}]){
  const incompatible=harness(async url=>url.includes('/files/analysis.json')?saved:{...restoreData,...change});await incompatible.c.loadSavedScreen(saved.id);const before=incompatible.node('ripple_weight').value;
  await incompatible.c.reuseSavedScreen();assert.equal(incompatible.node('ripple_weight').value,before);assert.equal(incompatible.node('savedScreenPanel').hidden,false);assert.match(incompatible.node('savedScreenStatus').textContent,/未沿用配置/);assert.equal(posts(incompatible).length,0);
 }
 const order=harness(async url=>url.includes('/files/analysis.json')?saved:{...restoreData,source_hashes:{'other.csv':hashes['other.csv'],'source.csv':hashes['source.csv']}});await order.c.loadSavedScreen(saved.id);await order.c.reuseSavedScreen();assert.equal(order.node('ripple_weight').value,'0.25');
 let finishReuse;const reuseWait=new Promise(resolve=>finishReuse=resolve),duringReuse=harness(async url=>url.includes('/files/analysis.json')?saved:reuseWait);
 await duringReuse.c.loadSavedScreen(saved.id);const reusing=duringReuse.c.reuseSavedScreen();await duringReuse.c.reuseSavedScreen();assert.equal(duringReuse.calls.filter(c=>c.url.includes('/datasets/')).length,1);assert.equal(duringReuse.node('reuseSavedScreen').disabled,true);
 duringReuse.node('ripple_weight').value='0.99';duringReuse.c.screeningChanged();finishReuse(restoreData);await reusing;assert.equal(duringReuse.node('ripple_weight').value,'0.99');assert.match(duringReuse.node('savedScreenStatus').textContent,/未覆盖/);assert.equal(posts(duringReuse).length,0);
 // A late saved-record or reuse response never overwrites a newly chosen record.
 let finishSaved;const savedWait=new Promise(resolve=>finishSaved=resolve),otherSaved={...saved,id:'opt_bbbbbbbbbbbb'};
 const delayedSaved=harness(async url=>url.includes(saved.id)?savedWait:otherSaved);const oldSaved=delayedSaved.c.loadSavedScreen(saved.id);await delayedSaved.c.loadSavedScreen(otherSaved.id);finishSaved(saved);await oldSaved;assert.match(delayedSaved.node('savedScreenResult').innerHTML,/opt_bbbbbbbbbbbb/);
 let finishOldReuse;const oldReuseWait=new Promise(resolve=>finishOldReuse=resolve),newRecordDuringReuse=harness(async url=>url.includes('/datasets/')?oldReuseWait:url.includes(saved.id)?saved:otherSaved);
 await newRecordDuringReuse.c.loadSavedScreen(saved.id);const oldReuse=newRecordDuringReuse.c.reuseSavedScreen();await newRecordDuringReuse.c.loadSavedScreen(otherSaved.id);const newStatus=newRecordDuringReuse.node('savedScreenStatus').textContent;finishOldReuse(restoreData);await oldReuse;assert.equal(newRecordDuringReuse.node('savedScreenStatus').textContent,newStatus);assert.equal(newRecordDuringReuse.node('ripple_weight').value,defaults.ripple_weight);
 const badSaved=harness(async()=>({...saved,id:'wrong'}));await badSaved.c.loadSavedScreen(saved.id);assert.equal(badSaved.node('savedScreenPanel').hidden,true);assert.equal(badSaved.node('reuseSavedScreen').disabled,true);assert.match(badSaved.node('savedScreenStatus').textContent,/身份或配置/);const invalidCalls=badSaved.calls.length;await badSaved.c.loadSavedScreen('../private');assert.equal(badSaved.calls.length,invalidCalls);
 assert.ok([valid,stale,changed,refresh,pending,cpu,create,oldError].every(h=>h.calls.every(c=>!c.url.includes('native-material-runs'))));
 console.log('Motor optimization UI: source/config binding, delayed response isolation, latest selection, single plan submission, original score payloads: PASS');
})().catch(error=>{console.error(error);process.exitCode=1});
