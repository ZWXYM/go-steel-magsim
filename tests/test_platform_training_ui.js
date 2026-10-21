/* Production training controls: one request, frozen identity, terminal errors and GET-only recovery. */
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const code=fs.readFileSync('static/js/platform_training.js','utf8'),html=fs.readFileSync('templates/index.html','utf8');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const result=id=>({model_id:id,r2_avg:.9,mape_avg:.1,feature_importance:{}});
function harness(storage=new Map()){
  const nodes=new Map(),get=id=>{if(!nodes.has(id))nodes.set(id,{value:'',style:{},textContent:'',innerHTML:'',disabled:false});return nodes.get(id)},calls=[],pending=[],timers=new Map(),messages=[],rendered=[],listeners={};let sequence=0,timerID=0;
  for(const [id,value] of Object.entries({'train-dataset':'source_A.csv','train-test-size':'.2','ml-train-preset':'ridge_poly','paper-preset':'smoke','xgb-n-est':'2','xgb-depth':'2','xgb-lr':'.1','xgb-sub':'.8'}))get(id).value=value;
  const c=vm.createContext({document:{getElementById:get,addEventListener:(name,fn)=>listeners[name]=fn},
    _ML_TRAIN_PRESETS_META:{ridge_poly:{model_type:'ridge_poly'},lite:{model_type:'direct_xgb',xgb_fields:true}},selectedTrainingModels:()=>['extra_trees'],
    crypto:{randomUUID:()=>`request_training_${++sequence}_123`},localStorage:{setItem:(k,v)=>storage.set(k,v),getItem:k=>storage.get(k)??null,removeItem:k=>storage.delete(k)},
    fetch:(url,options={})=>{calls.push({url,method:options.method||'GET',body:options.body?JSON.parse(options.body):null});return new Promise((resolve,reject)=>pending.push({resolve,reject}))},
    setTimeout:fn=>{const id=++timerID;timers.set(id,fn);return id},clearTimeout:id=>timers.delete(id),renderFeatureImportance:()=>rendered.push(get('res-model-id').textContent),renderPaperTrainingResult:r=>{rendered.push(r.model_id);get('train-status').textContent='候选模型选择完成';get('res-model-id').textContent=r.model_id},loadModelList(){},loadModelListForPred(){},alert:message=>messages.push(message),console});
  vm.runInContext(code,c);return{c,get,calls,pending,timers,messages,rendered,storage,listeners};
}
function respond(wait,data,status=200){wait.resolve({ok:status>=200&&status<300,status,json:async()=>data})}
function task(h,id,status='running',kind='single',extra={}){return {id,name:kind==='single'?'model_train':'paper_surrogate_model_selection',training_request_id:h.calls.find(call=>call.method==='POST')?.body.request_id||JSON.parse([...h.storage.values()][0]).request.request_id,status,...extra}}
async function started(h,kind='single',id='task_A'){
  const waiting=kind==='single'?h.c.startSingleModelTraining():h.c.startTraining();const submission=h.pending.shift();respond(submission,{task_id:id});await tick();return {waiting,poll:h.pending.shift()};
}
(async()=>{
  assert.ok(html.includes('/static/js/platform_training.js'));assert.ok(!/function start(?:SingleModelTraining|Training)\(/.test(html));assert.ok(html.includes('id="train-check"'));
  for(const [,script] of html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g))new vm.Script(script);
  const shared=harness(),waiting=shared.c.startSingleModelTraining();await shared.c.startSingleModelTraining();await shared.c.startTraining();assert.equal(shared.calls.length,1);assert.equal(shared.get('train-single-start').disabled,true);assert.equal(shared.get('train-selection-start').disabled,true);
  shared.get('train-dataset').value='source_B.csv';respond(shared.pending.shift(),{task_id:'task_A'});await tick();assert.equal(shared.calls.at(-1).url,'/api/tasks/task_A');assert.match(shared.get('train-task-identity').textContent,/source_A.csv/);assert.doesNotMatch(shared.get('train-task-identity').textContent,/source_B.csv/);
  respond(shared.pending.shift(),task(shared,'task_A','completed','single',{result:result('model_A')}));await waiting;assert.deepEqual(shared.rendered,['model_A']);assert.equal(shared.get('train-single-start').disabled,false);assert.match(shared.get('train-status').textContent,/完成/);

  const failure=harness(),f=await started(failure);respond(f.poll,task(failure,'task_A','failed','single',{error:'dataset mismatch'}));await f.waiting;assert.match(failure.get('train-status').textContent,/失败.*dataset mismatch/);assert.equal(failure.get('train-selection-start').disabled,false);
  const rejected=harness(),rejecting=rejected.c.startTraining();respond(rejected.pending.shift(),{error:'invalid dataset'},400);await rejecting;assert.match(rejected.get('train-status').textContent,/未接受.*invalid dataset/);assert.equal(rejected.get('train-single-start').disabled,false);assert.equal(rejected.calls.length,1);

  const selection=harness(),s=await started(selection,'selection');respond(s.poll,task(selection,'task_A','running','selection'));await s.waiting;assert.equal(selection.timers.size,1);const manual=selection.c.refreshTrainingTask();await selection.c.refreshTrainingTask();assert.equal(selection.timers.size,0);assert.equal(selection.pending.length,1);respond(selection.pending.shift(),task(selection,'task_A','completed','selection',{result:{model_id:'selected_A'}}));await manual;assert.deepEqual(selection.rendered,['selected_A']);assert.equal(selection.calls.filter(call=>call.method==='POST').length,1);

  const unavailable=harness(),u=await started(unavailable);u.poll.reject(Error('offline'));await u.waiting;assert.match(unavailable.get('train-status').textContent,/无法确认.*offline/);assert.equal(unavailable.get('train-single-start').disabled,true);assert.equal(unavailable.timers.size,0);await unavailable.c.startTraining();assert.equal(unavailable.calls.filter(call=>call.method==='POST').length,1);
  const refresh=unavailable.c.refreshTrainingTask();respond(unavailable.pending.shift(),task(unavailable,'task_A','failed','single',{error:'real failure'}));await refresh;assert.equal(unavailable.get('train-single-start').disabled,false);

  const lost=harness(),lostSubmit=lost.c.startTraining();lost.pending.shift().reject(Error('response lost'));await lostSubmit;assert.equal(lost.get('train-single-start').disabled,true);const recover=lost.c.refreshTrainingTask();assert.equal(lost.calls.at(-1).url,'/api/tasks');respond(lost.pending.shift(),{tasks:[task(lost,'recovered','running','selection')]});await tick();respond(lost.pending.shift(),task(lost,'recovered','completed','selection',{result:{model_id:'recovered_model'}}));await recover;assert.deepEqual(lost.rendered,['recovered_model']);assert.equal(lost.calls.filter(call=>call.method==='POST').length,1);

  const reload=harness(selection.storage),restoring=reload.c.restorePlatformTraining();assert.equal(reload.calls[0].method,'GET');respond(reload.pending.shift(),task(reload,'task_A','completed','selection',{result:{model_id:'selected_A'}}));await restoring;assert.deepEqual(reload.rendered,['selected_A']);assert.equal(reload.calls.filter(call=>call.method==='POST').length,0);
  const gone=harness(selection.storage),restoringGone=gone.c.restorePlatformTraining();respond(gone.pending.shift(),{error:'Not found'},404);await restoringGone;assert.match(gone.get('train-status').textContent,/已不在当前进程/);assert.equal(gone.calls.filter(call=>call.method==='POST').length,0);assert.equal(gone.get('train-single-start').disabled,false);

  const wrong=harness(),w=await started(wrong);respond(w.poll,{...task(wrong,'task_A'),training_request_id:'another_request_123'});await w.waiting;assert.match(wrong.get('train-status').textContent,/身份.*不一致/);assert.equal(wrong.rendered.length,0);assert.equal(wrong.get('train-selection-start').disabled,true);
  const interrupted=harness(),pendingSubmission=interrupted.c.startSingleModelTraining(),post=interrupted.pending.shift(),readDuringPost=interrupted.c.refreshTrainingTask();respond(interrupted.pending.shift(),{tasks:[task(interrupted,'task_A')]});await tick();respond(interrupted.pending.shift(),task(interrupted,'task_A','completed','single',{result:result('early_complete')}));await readDuringPost;respond(post,{task_id:'task_A'});await pendingSubmission;assert.deepEqual(interrupted.rendered,['early_complete']);assert.equal(interrupted.timers.size,0);
  const invalid=harness();invalid.get('train-test-size').value='NaN';await invalid.c.startSingleModelTraining();assert.equal(invalid.calls.length,0);invalid.get('train-dataset').value='';await invalid.c.startTraining();assert.equal(invalid.calls.length,0);
  const corrupt=harness(new Map([['go_steel_training_task_v1',JSON.stringify({version:1,kind:'single',request:{dataset_path:'x',request_id:'bad/request/id'}})]]));await corrupt.c.restorePlatformTraining();assert.equal(corrupt.calls.length,0);assert.equal(corrupt.storage.size,0);
  console.log('Platform training: shared action lock, frozen dataset, status/failure recovery, nonoverlapping polls, request identity, lost response and reload recovered by GET without retraining: PASS');
})().catch(error=>{console.error(error);process.exitCode=1});
