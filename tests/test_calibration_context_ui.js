/* Calibration/prediction request identity; fake fetch only, no fits or files. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const project=path.resolve(__dirname,'..'),source=fs.readFileSync(project+'/static/js/workbench.js','utf8');
const A='cal_aaaaaaaaaaaa',B='cal_bbbbbbbbbbbb',C='cal_cccccccccccc';
function calibration(id){
 const summary={mean_rmse_T:.01,max_abs_B800_error_T:0,B800_0_05T_pass_all:true},H=[0,100,800],curves=['RD','TD'].map(direction=>({grade:'S0',direction,H,raw_B:[0,.5,1],reference_B:[0,.6,1.2],corrected_B:[0,.6,1.2],holdout_B:[0,.55,1.1]}));
 return {id,selected_grades:['S0'],nested_validation:false,raw:{...summary},calibration_fit:{...summary},material_holdout:{...summary},comparisons:curves,metrics:curves.flatMap(c=>['raw','calibration_fit','material_holdout'].map(method=>({grade:c.grade,direction:c.direction,method,rmse_T:.01,B800_error_T:0})))};
}
const raw={material_id:'TEST'},prediction={id:'pred_aaaaaaaaaaaa',calibration_id:A,material_id:'TEST',AMAT_file:'WB_test.amat',RD:{transfer_candidate:false},prediction_scope:{excluded_grades:[]}};
class Node{
 constructor(id){this.id=id;this.value=({predictionMode:'all',direction:'RD',calibrationMethod:'fixed_h_delta_v1',rawPair:JSON.stringify(raw)})[id]||'';this._text='';this._html='';this.style={display:'none'};this.hidden=true;this.disabled=false;this.clientWidth=900;this.attrs={};this.classList={add(){},remove(){}}}
 set textContent(value){this._text=String(value);this._html=''}get textContent(){return this._text}
 set innerHTML(html){this._html=html;this._text=html.replace(/<[^>]*>/g,'');if(['history','grade','excludeGrade'].includes(this.id)){const first=html.match(/<option(?: value="([^"]*)")?>([^<]*)<\/option>/);this.value=first?(first[1]??first[2]):''}}get innerHTML(){return this._html}
 setAttribute(name,value){this.attrs[name]=value}
}
function harness(api,script=source){
 const nodes=new Map(),calls=[],urls=[],location={search:'',hash:'#cal',pathname:'/workbench'};const node=id=>{if(!nodes.has(id))nodes.set(id,new Node(id));return nodes.get(id)};
 const c=vm.createContext({document:undefined,URLSearchParams,location,history:{replaceState:(state,title,url)=>{urls.push(url);location.search=url.includes('?')?'?'+url.split('?')[1].split('#')[0]:''}},setTimeout:()=>0,addEventListener(){},
  fetch:async(url,options={})=>{calls.push({url,method:options.method||'GET',body:options.body});return {ok:true,json:async()=>api(url,options)}}});
 vm.runInContext(script,c);c.document={getElementById:node,querySelectorAll:selector=>selector==='.known:checked'?[{value:'S0'}]:[]};return {c,node,calls,urls};
}
const posts=h=>h.calls.filter(c=>c.method==='POST'),value=(h,s)=>vm.runInContext(s,h.c);
async function loaded(h,id=A){await h.c.loadCalibration(id)}
(async()=>{
 const html=fs.readFileSync(project+'/templates/workbench.html','utf8'),scripts=Array.from(html.matchAll(/<script src="([^"]+)" defer>/g),m=>m[1]);
 assert.deepEqual(scripts,['/static/js/workbench.js','/static/js/native_pair_exports.js','/static/js/beta_trial_preview.js']);assert.match(html,/oninput="predictionInputChanged\(true\)"/);
 const valid=harness(async url=>url.endsWith('/predict')?prediction:calibration(A));await loaded(valid);assert.equal(valid.node('predictButton').disabled,false);await valid.c.predict();
 assert.equal(posts(valid).length,1);assert.ok(posts(valid)[0].url.endsWith('/'+A+'/predict'));assert.deepEqual(JSON.parse(posts(valid)[0].body),raw);
 assert.match(valid.node('predResult').innerHTML,new RegExp('/'+A+'/'+prediction.id+'/curves.csv'));assert.match(valid.node('predResult').innerHTML,/校准 cal_aaaaaaaaaaaa/);
 assert.ok(valid.node('predResult').innerHTML.includes('/material-library?calibration='+A+'&amp;key=prediction%3A'+prediction.id));
 valid.c.setPredictionInput(JSON.stringify({material_id:'NEXT'}));assert.equal(valid.node('predResult').innerHTML,'');

 let finish;const delay=new Promise(r=>finish=r),switching=harness(async url=>url.endsWith('/predict')?delay:calibration(url.endsWith('/'+B)?B:A));
 await loaded(switching);const saving=switching.c.predict();await switching.c.predict();await loaded(switching,B);assert.equal(switching.node('predictButton').disabled,true);
 finish(prediction);await saving;assert.equal(posts(switching).length,1);assert.equal(value(switching,'current.id'),B);assert.equal(switching.node('predResult').innerHTML,'');
 assert.ok(switching.node('predStatus').innerHTML.includes('calibration='+A+'&key=prediction%3A'+prediction.id));assert.equal(switching.node('predictButton').disabled,false);
 for(const wrong of [{calibration_id:B},{material_id:'WRONG'},{id:'not_a_prediction'}]){
  const mismatch=harness(async url=>url.endsWith('/predict')?{...prediction,...wrong}:calibration(A));await loaded(mismatch);await mismatch.c.predict();assert.equal(mismatch.node('predResult').innerHTML,'');assert.match(mismatch.node('error').textContent,/身份不符/);
 }
 const named=harness(async url=>url.endsWith('/predict')?{...prediction,AMAT_file:'材料 test.amat'}:calibration(A));await loaded(named);await named.c.predict();assert.ok(named.node('predResult').innerHTML.includes(encodeURIComponent('材料 test.amat')));
 const excluded=harness(async url=>url.endsWith('/predict-excluded')?{...prediction,prediction_scope:{excluded_grades:['S0']}}:calibration(A));await loaded(excluded);excluded.node('predictionMode').value='excluded';excluded.c.predictionScopeChanged();await excluded.c.predict();
 assert.deepEqual(JSON.parse(posts(excluded)[0].body),{raw_pair:raw,exclude_grades:['S0']});

 let oldReady;const oldWait=new Promise(r=>oldReady=r),race=harness(async url=>url.endsWith('/'+A)?oldWait:calibration(B));
 const old=race.c.loadCalibration(A);assert.equal(race.node('predictButton').disabled,true);assert.equal(value(race,'current'),null);
 await loaded(race,B);oldReady(calibration(A));await old;assert.equal(value(race,'current.id'),B);assert.equal(race.node('calID').textContent,B);
 const bad=harness(async()=>calibration(B));await loaded(bad);assert.equal(value(bad,'current'),null);assert.equal(bad.node('predictButton').disabled,true);assert.match(bad.node('error').textContent,/不一致/);
 let badRead=false;const failed=harness(async()=>badRead?Promise.reject(Error('read failed')):calibration(A));await loaded(failed);badRead=true;await loaded(failed,B);assert.equal(value(failed,'current'),null);assert.equal(failed.node('calReport').hidden,true);

 let rawReady;const rawWait=new Promise(r=>rawReady=r),files=harness(async()=>calibration(A));
 const reading=files.c.readRaw({files:[{text:()=>rawWait}]});await files.c.readRaw({files:[{text:async()=>'{"material_id":"LATEST"}'}]});rawReady('{"material_id":"OLD"}');await reading;assert.equal(JSON.parse(files.node('rawPair').value).material_id,'LATEST');
 let exampleReady;const exampleWait=new Promise(r=>exampleReady=r),example=harness(async()=>exampleWait);const filling=example.c.example();example.c.setPredictionInput('{"material_id":"NATIVE_PAIR"}');exampleReady(raw);await filling;assert.equal(JSON.parse(example.node('rawPair').value).material_id,'NATIVE_PAIR');

 let fitReady;const fitWait=new Promise(r=>fitReady=r),fit=harness(async(url,options)=>url.endsWith('/calibrate')?fitWait:url.endsWith('/calibrations')?[{id:A,selected_grades:['S0']},{id:C,selected_grades:['S0']}]:calibration(A));
 const fitting=fit.c.fit();await fit.c.fit();await loaded(fit);fitReady(calibration(C));await fitting;
 assert.equal(posts(fit).length,1);assert.equal(value(fit,'current.id'),A);assert.match(fit.node('calActionStatus').textContent,/较早选择.*已保存/);
 let changedReady;const changedWait=new Promise(r=>changedReady=r),changed=harness(async url=>url.endsWith('/calibrate')?changedWait:[]);const changing=changed.c.fit();changed.node('calibrationMethod').value='other_method';changedReady(calibration(C));await changing;
 assert.equal(value(changed,'current'),null);assert.match(changed.node('calActionStatus').textContent,/当前选择已变更/);
 let listReady;const listWait=new Promise(r=>listReady=r),list=harness(async url=>url.endsWith('/calibrations')?listWait:calibration(B));const listing=list.c.calibrationHistory(true);await loaded(list,B);listReady([{id:A,selected_grades:['S0']},{id:B,selected_grades:['S0']}]);await listing;assert.equal(value(list,'current.id'),B);assert.equal(list.node('history').value,B);
 // Successful load updates a shareable address; a full page reopen uses that library.
 const choose=harness(async()=>calibration(B));choose.c.location.search='?pair=nativepair_aaaaaaaaaaaa';choose.c.location.hash='#motor';await loaded(choose,B);
 assert.match(choose.urls.at(-1),new RegExp('calibration='+B));assert.match(choose.urls.at(-1),/pair=nativepair_aaaaaaaaaaaa/);assert.match(choose.urls.at(-1),/#motor$/);
 const reopening=harness(async url=>url.endsWith('/calibrations')?[{id:A,selected_grades:['S0']},{id:B,selected_grades:['S0']}]:calibration(url.endsWith('/'+B)?B:A));
 reopening.c.location.search=choose.c.location.search;await reopening.c.calibrationHistory(true);assert.equal(value(reopening,'current.id'),B);assert.ok(reopening.calls.every(c=>c.method==='GET'));
 const absent=harness(async()=>[]);absent.c.location.search='?calibration='+B;await assert.rejects(absent.c.calibrationHistory(true),/指定的校准库不存在/);assert.equal(value(absent,'current'),null);
 const recovered=harness(async url=>url.endsWith('/'+A)?Promise.reject(Error('library missing')):calibration(B));await loaded(recovered,A);assert.equal(recovered.node('error').style.display,'block');
 await loaded(recovered,B);assert.equal(value(recovered,'current.id'),B);assert.equal(recovered.node('error').style.display,'none');assert.equal(recovered.node('error').textContent,'');
 recovered.c.error(Error('motor unavailable'));await loaded(recovered,B);assert.match(recovered.node('error').textContent,/motor unavailable/);assert.equal(recovered.node('error').style.display,'block');
 const corrected=harness(async url=>url.endsWith('/predict')?prediction:calibration(A));await loaded(corrected);corrected.node('rawPair').value='{';await corrected.c.predict();
 assert.equal(posts(corrected).length,0);assert.equal(corrected.node('error').style.display,'block');
 corrected.c.setPredictionInput(JSON.stringify(raw));assert.equal(corrected.node('error').style.display,'none');await corrected.c.predict();assert.equal(posts(corrected).length,1);assert.equal(corrected.node('error').textContent,'');
 corrected.c.error(Error('separate motor error'));corrected.c.predictionInputChanged();await corrected.c.predict();assert.match(corrected.node('error').textContent,/separate motor error/);
 const noBank=harness(async()=>calibration(A));await noBank.c.predict();assert.match(noBank.node('error').textContent,/先建立或加载校准/);await loaded(noBank);assert.equal(noBank.node('error').style.display,'none');
 const fileError=harness(async()=>raw);await fileError.c.readRaw({files:[{text:async()=>{throw Error('file read failure')}}]});assert.equal(fileError.node('error').style.display,'block');
 await fileError.c.readRaw({files:[{text:async()=>JSON.stringify(raw)}]});assert.equal(fileError.node('error').style.display,'none');assert.equal(JSON.parse(fileError.node('rawPair').value).material_id,raw.material_id);
 assert.ok([valid,switching,excluded,race,files,example,fit].every(h=>h.calls.every(c=>!/(\/start|\/submit|\/train)/.test(c.url))));
 console.log('Workbench UI: calibration generations, frozen prediction URLs, no duplicate fit/prediction, raw-input ordering and script integration: PASS');
})().catch(error=>{console.error(error);process.exitCode=1});
