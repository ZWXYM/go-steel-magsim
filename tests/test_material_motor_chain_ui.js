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
 const linked=harness();linked.c.fetch=async(url,options)=>{linked.calls.push({url,options});return {ok:true,json:async()=>({records:{analysis_plan:{id:'bhanalysis_aaaaaaaaaaaa'},execution:{id:'bhexec_bbbbbbbbbbbb'}},reused:[1],created:[],next_step:'复用现有结果',href:'/calibrated-motor/analysis/bhanalysis_aaaaaaaaaaaa'})}};
 await linked.c.mlPrepareMotor();assert.equal(linked.nodes.get('chainOpen').href,'/calibrated-motor/analysis/bhanalysis_aaaaaaaaaaaa?execution=bhexec_bbbbbbbbbbbb');
 assert.equal(linked.calls.length,1);assert.ok(!linked.calls.some(c=>c.url.endsWith('/start')));
 let resolve;const pending=new Promise(r=>resolve=r),double=harness();double.c.fetch=async(url,options)=>{double.calls.push({url,options});return pending};
 const first=double.c.mlPrepareMotor();await double.c.mlPrepareMotor();assert.equal(double.calls.length,1);
 resolve({ok:true,json:async()=>({records:{},reused:[],created:[1],next_step:'准备完成',href:'/material-library'})});await first;
 assert.equal(double.nodes.get('prepareMotor').disabled,false);
 const bad=harness();bad.c.fetch=async()=>({ok:false,json:async()=>({error:'现有记录需检查'})});await bad.c.mlPrepareMotor();
 assert.match(bad.nodes.get('error').textContent,/需检查/);assert.equal(bad.nodes.get('prepareMotor').disabled,false);assert.equal(bad.nodes.get('chainOpen').hidden,true);
 const stale=harness();stale.c.fetch=async()=>({ok:true,json:async()=>{vm.runInContext('ML.detail={key:"another"}',stale.c);return {records:{},reused:[],created:[],next_step:'old',href:'/old'}}});
 await stale.c.mlPrepareMotor();assert.equal(stale.nodes.get('chainOpen').hidden,true);assert.notEqual(stale.nodes.get('chainStatus').textContent,'old');

 // A recreated native-action button shares the pending state of the original operation.
 let finishImport;const importWait=new Promise(resolve=>finishImport=resolve),native=harness();
 native.c.fetch=async(url,options)=>{native.calls.push({url,options});return importWait};
 const firstButton={disabled:false},newButton={disabled:false},cpuButton={disabled:false},url='/api/workbench/calibrated-motor/native-imports',data={preparation:'bhprep_test'};
 native.c.mlBindNativeAction(firstButton,url,data,native.selected);const importing=native.c.mlNativeAction(url,data,firstButton,native.selected);
 native.c.mlBindNativeAction(newButton,url,data,native.selected);native.c.mlBindNativeAction(cpuButton,url+'/calimport_test/start',{},native.selected,true);
 assert.equal(newButton.disabled,true);await native.c.mlNativeAction(url,data,newButton,native.selected);await native.c.mlPrepareMotor();assert.equal(native.calls.length,1);
 const changed={calibration_id:'cal_b',key:'prediction:other'};native.c.reselected=changed;vm.runInContext('ML.detail=reselected',native.c);let refreshed=null;native.c.mlLoadPreparations=async selected=>{refreshed=selected};
 finishImport({ok:true,json:async()=>({id:'calimport_test'})});await importing;assert.equal(refreshed,changed);assert.equal(cpuButton.disabled,true);assert.equal(newButton.disabled,false);
 // An old material's failure cannot replace the new material's error display.
 let rejectImport;const failWait=new Promise((_,reject)=>rejectImport=reject),nf=harness();nf.c.fetch=async()=>failWait;
 const failedNative=nf.c.mlNativeAction(url,data,{disabled:false},nf.selected);nf.c.other={key:'another'};vm.runInContext('ML.detail=other',nf.c);rejectImport(Error('old material failure'));await failedNative;
 assert.notEqual(nf.nodes.get('error').textContent,'old material failure');
 // Keep the shared lock until the post-action read finishes, including freshly rendered buttons.
 const refreshCase=harness();let announceRefresh,finishRefresh;
 const refreshStarted=new Promise(resolve=>announceRefresh=resolve),refreshPending=new Promise(resolve=>finishRefresh=resolve);
 refreshCase.c.mlLoadPreparations=async()=>{announceRefresh();await refreshPending};
 const original={disabled:false},replacement={disabled:false};refreshCase.c.mlBindNativeAction(original,url,data,refreshCase.selected);
 const pendingAction=refreshCase.c.mlNativeAction(url,data,original,refreshCase.selected);await refreshStarted;
 refreshCase.c.mlBindNativeAction(replacement,url,data,refreshCase.selected);
 assert.equal(replacement.disabled,true,'post-action refresh must retain native-action lock');
 assert.equal(refreshCase.nodes.get('prepareMotor').disabled,true);
 const duplicateAction=refreshCase.c.mlNativeAction(url,data,replacement,refreshCase.selected);
 assert.equal(refreshCase.calls.length,1);finishRefresh();await Promise.all([pendingAction,duplicateAction]);
 assert.equal(replacement.disabled,false);assert.equal(refreshCase.nodes.get('prepareMotor').disabled,false);
 console.log('Material chain UI: exact source/protocol, no dispatch, double-click protection, stale/error recovery: PASS');
})().catch(error=>{console.error(error);process.exitCode=1});
