/* Execute the production prediction controller with delayed fetch and real form semantics. */
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const html=fs.readFileSync('templates/index.html','utf8'),code=fs.readFileSync('static/js/platform_prediction.js','utf8');
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject}};
class Element{
  constructor(tag='div'){this.tag=tag;this._value='';this.children=[];this.style={};this.textContent='';this.disabled=false}
  get value(){return this._value}set value(value){this._value=String(value)}
  replaceChildren(){this.children=[];this.value=''}
  append(child){this.children.push(child);if(this.tag==='select'&&!this.value)this.value=child.value}
}
function harness(){
  const nodes=new Map(),calls=[],pending=[],rendered=[],downloads=[],messages=[],clipboard=[],revoked=[];
  const get=id=>{if(!nodes.has(id))nodes.set(id,new Element(id==='pred-model'?'select':'div'));return nodes.get(id)};
  for(const [id,value] of Object.entries({'pred-model':'A','pred-fgoss':'.7','pred-theta':'15','pred-hw':'10','pred-si':'6','pred-mat-name':'SAVED_A','pred-thickness':'.35'}))get(id).value=value;
  const c=vm.createContext({document:{getElementById:get,createElement:tag=>tag==='a'?{click(){downloads.push({name:this.download,url:this.href})}}:new Element(tag)},
    fetch:(url,options={})=>{const request=deferred();calls.push({url,method:options.method||'GET',body:options.body?JSON.parse(options.body):null});pending.push(request);return request.promise},
    renderBHChart:data=>rendered.push(data.model_id),renderFullDirectionCharts(){},renderScalars(){},estimateCoreLossParams(){return{}},renderCoreLossCards(){},
    updatePredCoreLoss(){},navigator:{clipboard:{writeText:async text=>clipboard.push(text)}},alert:message=>messages.push(message),toast:(message,kind)=>messages.push({message,kind}),
    URL:{createObjectURL:()=>`blob:fixture-${downloads.length}`,revokeObjectURL:url=>revoked.push(url)},setTimeout:fn=>fn(),console});
  vm.runInContext('let _currentPredResult=null;'+code,c);return{c,get,calls,pending,rendered,downloads,messages,clipboard,revoked};
}
const result=(model='A',offset=0)=>({model_id:model,RD:{H:[0,20,800],B:[0,1+offset,1.8+offset]},TD:{H:[0,50,100,800],B:[0,.4+offset,.6+offset,1.2+offset]}});
function respond(request,data,status=200){request.resolve({ok:status>=200&&status<300,status,json:async()=>data,blob:async()=>({type:'application/octet-stream',size:5})})}
async function predict(h,model='A'){h.get('pred-model').value=model;const promise=h.c.runPrediction();respond(h.pending.shift(),result(model));await promise}
(async()=>{
  assert.match(html,/<script src="\/static\/js\/platform_prediction\.js"><\/script>/);
  for(const id of ['pred-fgoss','pred-theta','pred-hw','pred-si'])assert.match(html,new RegExp(`id="${id}" oninput="_predictionSelectionChanged\\(\\)"`));
  assert.match(html,/id="pred-model" onchange="_predictionSelectionChanged\(\)"/);
  assert.doesNotMatch(html,/function (?:runPrediction|loadModelListForPred|_fetchPredBH|doExportMaxwellFromPred|copyBHData)\(/);
  for(const [,script] of html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g))new vm.Script(script);

  const cached=harness();await predict(cached);const accepted=await cached.c._fetchPredBH();assert.equal(accepted.model_id,'A');assert.equal(cached.calls.length,1);
  await cached.c.copyBHData();assert.equal(cached.calls.length,1);assert.equal(cached.clipboard[0],'H_RD (A/m)\tB_RD (T)\tH_TD (A/m)\tB_TD (T)\n0\t0\t0\t0\n20\t1\t50\t0.4\n800\t1.8\t100\t0.6\n\t\t800\t1.2');
  cached.get('pred-model').value='B';cached.get('pred-fgoss').value='.8';cached.c._predictionSelectionChanged();assert.equal(cached.get('pred-result').style.display,'none');
  await cached.c.doExportMaxwellFromPred();assert.equal(cached.calls.length,1);assert.match(cached.get('pred-input-status').textContent,/重新预测/);

  const late=harness();const first=late.c.runPrediction(),old=late.pending.shift();late.get('pred-model').value='B';late.c._predictionSelectionChanged();const second=late.c.runPrediction();const newer=late.pending.shift();
  respond(newer,result('B',1));await second;respond(old,result('A'));await first;assert.deepEqual(late.rendered,['B']);assert.equal(late.c._predictionCurrentResult().model_id,'B');assert.match(late.get('pred-input-status').textContent,/B/);
  const oldFailure=harness();const failing=oldFailure.c.runPrediction(),bad=oldFailure.pending.shift();oldFailure.get('pred-model').value='B';oldFailure.c._predictionSelectionChanged();await predict(oldFailure,'B');bad.reject(Error('old backend failure'));await failing;assert.equal(oldFailure.messages.length,0);assert.equal(oldFailure.c._predictionCurrentResult().model_id,'B');
  const same=harness();const one=same.c.runPrediction(),two=same.c.runPrediction(),three=same.c._fetchPredBH();assert.equal(same.calls.length,1);assert.equal(same.get('pred-run').disabled,true);respond(same.pending.shift(),result());await Promise.all([one,two,three]);assert.equal(same.get('pred-run').disabled,false);
  const roundTrip=harness();const waiting=roundTrip.c.runPrediction(),stale=roundTrip.pending.shift();roundTrip.get('pred-theta').value='16';roundTrip.c._predictionSelectionChanged();roundTrip.get('pred-theta').value='15';roundTrip.c._predictionSelectionChanged();respond(stale,result());await waiting;assert.equal(roundTrip.c._predictionCurrentResult(),null);assert.equal(roundTrip.rendered.length,0);

  const list=harness();list.get('pred-model').value='B';let loading=list.c.loadModelListForPred();respond(list.pending.shift(),{models:[{model_id:'A'},{model_id:'B'},{model_id:'report',prediction_capable:false}]});await loading;assert.equal(list.get('pred-model').value,'B');assert.equal(list.get('pred-model').children.length,2);
  loading=list.c.loadModelListForPred();list.get('pred-model').value='A';respond(list.pending.shift(),{models:[{model_id:'B'},{model_id:'A'}]});await loading;assert.equal(list.get('pred-model').value,'A');
  const older=list.c.loadModelListForPred(),olderResponse=list.pending.shift(),latest=list.c.loadModelListForPred();respond(list.pending.shift(),{models:[{model_id:'A'},{model_id:'B'}]});await latest;respond(olderResponse,{models:[]});await older;assert.equal(list.get('pred-model').value,'A');
  loading=list.c.loadModelListForPred();respond(list.pending.shift(),{error:'unreadable'},500);await loading;assert.equal(list.get('pred-model').value,'A');assert.equal(list.get('pred-model').children.length,2);
  loading=list.c.loadModelListForPred();respond(list.pending.shift(),{models:[{model_id:'B'}]});await loading;assert.equal(list.get('pred-model').value,'');assert.equal(list.get('pred-model').children.at(-1).textContent,'所选模型已不在列表，请重新选择');

  const exportCase=harness();await predict(exportCase);exportCase.c.exportMaxwellFromPred();const download=exportCase.c.doExportMaxwellFromPred(),repeat=exportCase.c.doExportMaxwellFromPred();await repeat;assert.equal(exportCase.calls.length,2);assert.equal(exportCase.get('pred-download').disabled,true);assert.equal(exportCase.calls.at(-1).body.model_id,'A');assert.equal(exportCase.calls.at(-1).body.params.f_Goss,.7);
  exportCase.get('pred-model').value='B';exportCase.get('pred-fgoss').value='.8';exportCase.c._predictionSelectionChanged();respond(exportCase.pending.shift(),{});await download;assert.equal(exportCase.downloads[0].name,'SAVED_A.amat');assert.equal(exportCase.revoked.length,1);assert.match(exportCase.messages.at(-1).message,/模型 A/);assert.equal(exportCase.c._predictionCurrentResult(),null);
  const errorCase=harness();await predict(errorCase);let exportWaiting=errorCase.c.doExportMaxwellFromPred();respond(errorCase.pending.shift(),{error:'export failure'},500);await exportWaiting;assert.equal(errorCase.downloads.length,0);assert.equal(errorCase.get('pred-download').disabled,false);assert.match(errorCase.messages.at(-1).message,/export failure/);
  exportWaiting=errorCase.c.doExportMaxwellFromPred();respond(errorCase.pending.shift(),{});await exportWaiting;assert.equal(errorCase.downloads.length,1);
  const invalid=harness();for(const value of ['','bad','Infinity','0.1']){invalid.get('pred-fgoss').value=value;await invalid.c.runPrediction()}assert.equal(invalid.calls.length,0);assert.equal(invalid.c._predictionCurrentResult(),null);
  const mismatch=harness();const mismatchWaiting=mismatch.c.runPrediction();respond(mismatch.pending.shift(),result('wrong'));await mismatchWaiting;assert.equal(mismatch.c._predictionCurrentResult(),null);assert.match(mismatch.messages.at(-1),/不一致/);
  const malformed=harness();const malformedWaiting=malformed.c.runPrediction();respond(malformed.pending.shift(),{...result(),TD:{H:[0,1],B:[0]}});await malformedWaiting;assert.equal(malformed.c._predictionCurrentResult(),null);assert.equal(malformed.get('pred-run').disabled,false);
  console.log('Platform prediction: input/result/export binding, cache reuse, stale responses, preserved model selection, duplicate prevention, HTTP errors and separate physical RD/TD TSV axes: PASS');
})().catch(error=>{console.error(error);process.exitCode=1});
