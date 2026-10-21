/* Explicit GPU start, saved reopen and pause/inspection; all API calls are CPU mocks. */
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
class Node{
 constructor(tag='div'){this.tag=tag;this.children=[];this.value='';this.textContent='';this.disabled=false;this.hidden=false}
 append(n){this.children.push(n);if(this.tag==='select'&&!this.value)this.value=n.value}
 replaceChildren(){this.children=[];this.value=''}
}
const id='nativerun_aaaaaaaaaaaa';
const base={id,status:'ready',producer_compatible:true,elapsed_seconds:0,total_jobs:2,completed_jobs:0,texture_sampling_version:'fixture',
 recipe:{calibration_id:'cal_aaaaaaaaaaaa',material_id:'CPU_FIXTURE',params:{f_Goss:.855,theta_0_deg:4.5,halfwidth_deg:6.5,Si_content:3.1},n_grains:1,thickness_mm:.27,seed:42}};
function harness(allowed=true,url='http://local/native-material-runs'){
 const nodes=new Map(),calls=[],timers=[],ctx=vm.createContext({URL,console,location:{href:url},window:{history:{replaceState:()=>{}}},
  document:{getElementById:id=>{if(!nodes.has(id))nodes.set(id,new Node(['nmrCalibration','nmrHistory'].includes(id)?'select':'div'));return nodes.get(id)},createElement:tag=>new Node(tag)},
  setTimeout:fn=>{timers.push(fn);return timers.length},fetch:async(url,opts={})=>{calls.push({url,...opts});let result=base;
    if(url.endsWith('/resources'))result={native_submission_allowed:allowed};else if(url.endsWith('/calibrations'))result=[{id:'cal_aaaaaaaaaaaa'}];else if(url.endsWith('/native-material-runs')&&!opts.method)result=[];
    else if(url.endsWith('/start'))result={...base,status:'running'};else if(url.endsWith('/pause'))result={message:'当前晶粒结束后暂停'};
    return {ok:true,json:async()=>result}}});
 vm.runInContext(fs.readFileSync('static/js/native_material_runs.js','utf8'),ctx);return {ctx,nodes,calls,timers};
}
const flush=()=>new Promise(r=>setImmediate(r));
(async()=>{
 const h=harness();await flush();for(const [name,value] of Object.entries({nmrMaterial:'CPU_FIXTURE',nmrGoss:.855,nmrTheta:4.5,nmrWidth:6.5,nmrSi:3.1,nmrThickness:.27,nmrGrains:1,nmrSeed:42}))h.nodes.set(name,Object.assign(new Node(),{value:String(value)}));
 await h.ctx.nmrPrepare();assert.equal(h.calls.filter(c=>c.method==='POST').length,1);assert(!h.calls.some(c=>c.url.endsWith('/start')));assert.equal(h.nodes.get('nmrStart').disabled,false);
 await h.ctx.nmrStart();assert.equal(h.calls.filter(c=>c.url.endsWith('/start')).length,1);assert.equal(h.nodes.get('nmrPause').disabled,false);
 await h.ctx.nmrPause();assert.match(h.nodes.get('nmrMessage').textContent,/当前晶粒/);
 h.ctx.nmrRender({...base,status:'paused',completed_jobs:1,elapsed_seconds:10});assert.equal(h.nodes.get('nmrStart').textContent,'接续剩余晶粒');assert.equal(h.nodes.get('nmrStart').disabled,false);
 h.ctx.nmrRender({...base,status:'paused',elapsed_seconds:120});assert.equal(h.nodes.get('nmrStart').disabled,true);
 const cpu=harness(false,'http://local/native-material-runs?run='+id);await flush();await flush();assert.equal(cpu.nodes.get('nmrStart').disabled,true);assert(cpu.calls.every(c=>c.method!=='POST'));
 const old=harness();await flush();let finish;const pending=new Promise(r=>finish=r);let n=0;old.ctx.fetch=async()=>({ok:true,json:async()=>++n===1?pending:{...base,id:'nativerun_bbbbbbbbbbbb'}});const first=old.ctx.nmrOpen(id);await old.ctx.nmrOpen('nativerun_bbbbbbbbbbbb');finish(base);await first;assert.match(old.nodes.get('nmrStatus').textContent,/bbbbbbbbbbbb/);

 // A task switch is a barrier for every native action and old download.
 const switching=harness();await flush();switching.ctx.nmrRender(base);const next='nativerun_bbbbbbbbbbbb';let opened;
 const openingWait=new Promise(r=>opened=r),switchCalls=[];
 switching.ctx.fetch=async(url,options={})=>{switchCalls.push({url,...options});return {ok:true,json:async()=>url.endsWith('/'+next)?openingWait:{...base,id:next,status:'running'}}};
 switching.nodes.get('nmrHistory').value=next;const opening=switching.ctx.nmrOpen(next);
 assert.equal(switching.nodes.get('nmrStart').disabled,true);assert.equal(switching.nodes.get('nmrBundle').hidden,true);assert.equal(switching.nodes.get('nmrPrepare').disabled,true);
 await switching.ctx.nmrStart();assert.equal(switchCalls.filter(c=>c.method==='POST').length,0);
 opened({...base,id:next});await opening;assert.equal(switching.nodes.get('nmrStart').disabled,false);await switching.ctx.nmrStart();
 assert.equal(switchCalls.filter(c=>c.method==='POST').length,1);assert.ok(switchCalls.find(c=>c.method==='POST').url.endsWith('/'+next+'/start'));

 // A poll begun before another task opens cannot reselect its old task.
 const polling=harness();await flush();polling.ctx.nmrRender(base);let oldPollReady,newOpenReady;
 const oldPoll=new Promise(r=>oldPollReady=r),newOpen=new Promise(r=>newOpenReady=r);
 polling.ctx.fetch=async url=>({ok:true,json:async()=>url.endsWith('/resources')?{native_submission_allowed:true}:url.endsWith('/'+next)?newOpen:oldPoll});
 const readOld=polling.ctx.nmrPoll();polling.nodes.get('nmrHistory').value=next;const readNew=polling.ctx.nmrOpen(next);
 oldPollReady({...base,status:'running'});await readOld;assert.equal(polling.nodes.get('nmrHistory').value,next);assert.equal(vm.runInContext('nmrCurrent',polling.ctx),null);
 newOpenReady({...base,id:next});await readNew;assert.match(polling.nodes.get('nmrStatus').textContent,/bbbbbbbbbbbb/);

 // Reads from before a successful action cannot roll running back to ready.
 const rollback=harness();await flush();rollback.ctx.nmrRender(base);let staleReady;const staleRead=new Promise(r=>staleReady=r);
 rollback.ctx.fetch=async(url,options={})=>({ok:true,json:async()=>options.method==='POST'?{...base,status:'running'}:url.endsWith('/resources')?{native_submission_allowed:true}:staleRead});
 const stalePoll=rollback.ctx.nmrPoll();await rollback.ctx.nmrStart();staleReady(base);await stalePoll;
 assert.equal(vm.runInContext('nmrCurrent.status',rollback.ctx),'running');assert.equal(rollback.nodes.get('nmrStart').disabled,true);

 const pause=harness();await flush();pause.ctx.nmrRender({...base,status:'running'});let pauseReady;const pauseWait=new Promise(r=>pauseReady=r),pauseCalls=[];
 pause.ctx.fetch=async(url,options={})=>{pauseCalls.push({url,...options});return {ok:true,json:async()=>pauseWait}};
 const pausing=pause.ctx.nmrPause();await pause.ctx.nmrPause();assert.equal(pause.nodes.get('nmrHistory').disabled,true);assert.equal(pauseCalls.length,1);
 pauseReady({message:'当前晶粒结束后暂停'});await pausing;assert.equal(pause.nodes.get('nmrHistory').disabled,false);
 const inspecting=harness();await flush();inspecting.ctx.nmrRender({...base,status:'needs_attention'});let inspectedReady;const inspectedWait=new Promise(r=>inspectedReady=r),inspectCalls=[];
 inspecting.ctx.fetch=async(url,options={})=>{inspectCalls.push({url,...options});return {ok:true,json:async()=>inspectedWait}};const checking=inspecting.ctx.nmrInspect();await inspecting.ctx.nmrInspect();
 assert.equal(inspecting.nodes.get('nmrHistory').disabled,true);assert.equal(inspectCalls.length,1);assert.ok(inspectCalls[0].url.endsWith('/'+id+'/reconcile'));
 inspectedReady({...base,status:'paused',elapsed_seconds:10});await checking;assert.equal(inspecting.nodes.get('nmrStart').disabled,false);assert.ok(!inspectCalls.some(c=>c.url.endsWith('/start')));
 for(const current of [{...base,elapsed_seconds:120},{...base,producer_compatible:false},{...base,status:'failed'},{...base,status:'completed'}]){
  const blocked=harness();await flush();blocked.ctx.nmrRender(current);const before=blocked.calls.length;await blocked.ctx.nmrStart();assert.equal(blocked.calls.length,before);
 }
 const noGPU=harness(false);await flush();noGPU.ctx.nmrRender(base);const noCalls=noGPU.calls.length;await noGPU.ctx.nmrStart();assert.equal(noGPU.calls.length,noCalls);

 const invalid=harness();await flush();invalid.ctx.nmrRender(base);invalid.ctx.fetch=async()=>({ok:true,json:async()=>({...base,id:next})});await invalid.ctx.nmrOpen(id);
 assert.equal(vm.runInContext('nmrCurrent',invalid.ctx),null);assert.equal(invalid.nodes.get('nmrStart').disabled,true);assert.equal(invalid.nodes.get('nmrBundle').hidden,true);assert.match(invalid.nodes.get('nmrMessage').textContent,/身份/);
 const clear=harness();await flush();clear.ctx.nmrRender(base);let lastURL;clear.ctx.window.history.replaceState=(a,b,url)=>lastURL=url.toString();await clear.ctx.nmrOpen('');
 assert.equal(vm.runInContext('nmrCurrent',clear.ctx),null);assert.equal(new URL(lastURL).searchParams.has('run'),false);assert.equal(clear.nodes.get('nmrPredict').hidden,true);

 const failed=harness();await flush();failed.ctx.nmrRender(base);failed.ctx.fetch=async()=>{throw Error('offline')};await failed.ctx.nmrRefresh();assert.equal(failed.nodes.get('nmrStart').disabled,true);
 const beforeFailureCalls=failed.calls.length;await failed.ctx.nmrStart();assert.equal(failed.calls.length,beforeFailureCalls);
 const uncertain=harness();await flush();uncertain.ctx.nmrRender(base);uncertain.ctx.fetch=async()=>{throw Error('response lost')};await uncertain.ctx.nmrStart();
 assert.match(uncertain.nodes.get('nmrMessage').textContent,/操作未确认/);assert.equal(uncertain.nodes.get('nmrStart').disabled,true);

 // A CPU preparation response with another material is not made startable.
 const wrongRecipe=harness();await flush();wrongRecipe.ctx.nmrRender(base);
 for(const [name,val] of Object.entries({nmrMaterial:'NEW_RECIPE',nmrGoss:.855,nmrTheta:4.5,nmrWidth:6.5,nmrSi:3.1,nmrThickness:.27,nmrGrains:1,nmrSeed:42}))wrongRecipe.nodes.set(name,Object.assign(new Node(),{value:String(val)}));
 wrongRecipe.ctx.fetch=async()=>({ok:true,json:async()=>base});await wrongRecipe.ctx.nmrPrepare();
 assert.equal(vm.runInContext('nmrCurrent',wrongRecipe.ctx),null);assert.equal(wrongRecipe.nodes.get('nmrStart').disabled,true);assert.match(wrongRecipe.nodes.get('nmrMessage').textContent,/参数与请求不符/);

 // An empty parameter is missing input, even when zero is a valid physical value.
 const form={nmrMaterial:'CPU_FIXTURE',nmrGoss:.855,nmrTheta:4.5,nmrWidth:6.5,nmrSi:3.1,nmrThickness:.27,nmrGrains:1,nmrSeed:42};
 for(const field of ['nmrMaterial','nmrGoss','nmrTheta','nmrWidth','nmrSi','nmrThickness','nmrGrains','nmrSeed']){
  const missing=harness();await flush();missing.ctx.nmrRender(base);for(const [name,value] of Object.entries(form))missing.nodes.set(name,Object.assign(new Node(),{value:String(value)}));missing.nodes.get(field).value='';
  const before=missing.calls.length;await missing.ctx.nmrPrepare();assert.equal(missing.calls.slice(before).filter(c=>c.method==='POST').length,0,field+' must not be silently submitted as zero');assert.equal(vm.runInContext('nmrCurrent.id',missing.ctx),id,'invalid input must preserve the already-open task');assert.match(missing.nodes.get('nmrMessage').textContent,/填写/);
 }
 for(const [field,value] of [['nmrGoss','NaN'],['nmrTheta','Infinity'],['nmrGoss','1.1'],['nmrGrains','1.5'],['nmrSeed','2147483648'],['nmrThickness','0'],['nmrMaterial','bad/name']]){
  const invalidForm=harness();await flush();invalidForm.ctx.nmrRender(base);for(const [name,initial] of Object.entries(form))invalidForm.nodes.set(name,Object.assign(new Node(),{value:String(initial)}));invalidForm.nodes.get(field).value=value;
  const before=invalidForm.calls.length;await invalidForm.ctx.nmrPrepare();assert.equal(invalidForm.calls.slice(before).filter(c=>c.method==='POST').length,0,field+' invalid value must not create a task');assert.equal(vm.runInContext('nmrCurrent.id',invalidForm.ctx),id);
 }
 const zero=harness();await flush();for(const [name,value] of Object.entries({...form,nmrGoss:0,nmrTheta:0,nmrSi:0,nmrSeed:0}))zero.nodes.set(name,Object.assign(new Node(),{value:String(value)}));
 let savedData;zero.ctx.fetch=async(url,options={})=>{if(options.method==='POST'){savedData=JSON.parse(options.body);return {ok:true,json:async()=>({...base,recipe:savedData})}}return {ok:true,json:async()=>url.endsWith('/resources')?{native_submission_allowed:true}:url.endsWith('/calibrations')?[{id:base.recipe.calibration_id}]:[]}};
 await zero.ctx.nmrPrepare();assert.deepEqual(savedData.params,{f_Goss:0,theta_0_deg:0,halfwidth_deg:6.5,Si_content:0});assert.equal(savedData.seed,0);assert.equal(vm.runInContext('nmrCurrent.status',zero.ctx),'ready','explicit zero is allowed');assert.equal(zero.nodes.get('nmrStart').disabled,false);

 // Editing the form while CPU preparation waits must not select the old recipe for GPU start.
 const edited=harness();await flush();for(const [name,value] of Object.entries(form))edited.nodes.set(name,Object.assign(new Node(),{value:String(value)}));let prepared;
 edited.ctx.fetch=async(url,options={})=>({ok:true,json:async()=>options.method==='POST'?new Promise(resolve=>prepared=resolve):url.endsWith('/resources')?{native_submission_allowed:true}:url.endsWith('/calibrations')?[{id:base.recipe.calibration_id}]:[base]});
 const making=edited.ctx.nmrPrepare();await flush();edited.nodes.get('nmrGoss').value='.9';prepared(base);await making;
 assert.equal(vm.runInContext('nmrCurrent',edited.ctx),null,'a saved older recipe must not become the current startable task after form edits');assert.equal(edited.nodes.get('nmrStart').disabled,true);assert.equal(edited.nodes.get('nmrGoss').value,'.9');assert.match(edited.nodes.get('nmrMessage').textContent,/输入已变化/);assert.ok(edited.nodes.get('nmrMessage').children.some(node=>node.href==='/native-material-runs?run='+id),'old saved task remains explicitly reopenable');

 const copy=harness();await flush();copy.ctx.nmrRender({...base,status:'completed'});for(const [name,value] of Object.entries({...form,nmrMaterial:'NEW_INPUT',nmrGoss:.4}))copy.nodes.set(name,Object.assign(new Node(),{value:String(value)}));const beforeCopy=copy.calls.length;copy.ctx.nmrLoadRecipe();
 assert.equal(copy.nodes.get('nmrMaterial').value,'CPU_FIXTURE');assert.equal(copy.nodes.get('nmrGoss').value,'0.855');assert.equal(copy.nodes.get('nmrGrains').value,'1');assert.deepEqual(JSON.parse(JSON.stringify(copy.ctx.nmrReadRecipe())),base.recipe);assert.equal(copy.calls.length,beforeCopy,'copying a saved recipe is read-only and creates no task');assert.equal(vm.runInContext('nmrCurrent.status',copy.ctx),'completed');assert.match(copy.nodes.get('nmrMessage').textContent,/全部参数/);
 const noBank=harness();await flush();noBank.ctx.nmrRender({...base,recipe:{...base.recipe,calibration_id:'cal_bbbbbbbbbbbb'}});for(const [name,value] of Object.entries(form))noBank.nodes.set(name,Object.assign(new Node(),{value:String(value)}));noBank.nodes.get('nmrMaterial').value='KEEP_INPUT';noBank.ctx.nmrLoadRecipe();assert.equal(noBank.nodes.get('nmrMaterial').value,'KEEP_INPUT');assert.equal(noBank.nodes.get('nmrCalibration').value,base.recipe.calibration_id);assert.match(noBank.nodes.get('nmrMessage').textContent,/校准库不在当前目录/);

 console.log('Native material UI: CPU prepare, separate GPU start, pause/resume budget, CPU mode and stale open isolation: PASS');
})().catch(e=>{console.error(e);process.exitCode=1});
