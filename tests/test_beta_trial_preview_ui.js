/* Read-only preview, source selection, unchanged physical points and stale loads. */
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
class Node{
 constructor(tag='div'){this.tag=tag;this.children=[];this.textContent='';this.value='';this.hidden=false;this.disabled=false;this.attrs={}}
 append(...nodes){this.children.push(...nodes);if(this.tag==='select'&&!this.value&&nodes.length)this.value=nodes[0].value}
 replaceChildren(...nodes){this.children=nodes;if(this.tag==='select')this.value=''}
 setAttribute(key,value){this.attrs[key]=String(value)}
 text(){return this.textContent+this.children.map(n=>n.text()).join(' ')}
}
const id='cal_aaaaaaaaaaaa',summaries={baseline_saved_nested:{mean_rmse_T:.044,max_abs_B800_error_T:.106,max_direction_RMSE_T:.094},trial_nested:{mean_rmse_T:.031,max_abs_B800_error_T:.06,max_direction_RMSE_T:.059},reference_only_control:{mean_rmse_T:.029,max_abs_B800_error_T:.074,max_direction_RMSE_T:.064}};
const curves=['S0','S1'].flatMap(grade=>['RD','TD'].map(direction=>({grade,direction,H:[0,100,800],B_baseline:[0,.7,1.3],B_trial:[0,.8,1.4],B_reference:[0,.9,1.45],candidate:'beta_quarter'})));
const data={calibration_id:id,read_only:true,default_prediction_changed:false,automatic_deployment:false,
 summary:{all_acceptance_gates_passed:false,B800_passed_directions:6,B800_total_directions:8,summaries,gates:{all_B800_below_0_05T:false}},curves,
 metrics:curves.map(c=>({grade:c.grade,direction:c.direction,method:'trial_nested',rmse_T:.031,B800_error_T:.06}))};
function harness(api){const nodes=new Map(),calls=[];
 const c=vm.createContext({document:undefined,fetch:async(url,options={})=>{calls.push({url,method:options.method||'GET'});return {ok:true,json:async()=>api(url,options)}}});
 vm.runInContext(fs.readFileSync('static/js/beta_trial_preview.js','utf8'),c);
 c.document={getElementById:key=>{if(!nodes.has(key))nodes.set(key,new Node(['betaTrialGrade','betaTrialDirection'].includes(key)?'select':'div'));return nodes.get(key)},createElement:tag=>new Node(tag),createElementNS:(ns,tag)=>new Node(tag)};
 c.document.getElementById('calID').textContent=id;return {c,nodes,calls};
}
(async()=>{
 const h=harness(async()=>data);assert.equal(h.calls.length,0);const original=JSON.stringify(data);await h.c.btpLoad();
 assert.equal(h.calls.length,1);assert.ok(h.calls.every(c=>c.method==='GET'));
 assert.equal(h.nodes.get('betaTrialPanel').hidden,false);assert.match(h.nodes.get('betaTrialNotice').text(),/未部署/);
 assert.match(h.nodes.get('betaTrialStats').text(),/原协议材料留出/);assert.match(h.nodes.get('betaTrialStats').text(),/0\.031000/);
 assert.match(h.nodes.get('betaTrialGates').text(),/未通过/);assert.equal(h.nodes.get('betaTrialCurve').children.filter(n=>n.tag==='circle').length,9);
 assert.equal(h.nodes.get('betaTrialDownload').href,'/api/workbench/calibrations/'+id+'/beta-trial-preview/download');
 h.nodes.get('betaTrialGrade').value='S1';h.nodes.get('betaTrialDirection').value='TD';h.c.btpPlot();
 assert.match(h.nodes.get('betaTrialCurveInfo').text(),/S1 · TD/);assert.match(h.nodes.get('betaTrialCurveInfo').text(),/0\.060000 T/);assert.equal(JSON.stringify(data),original);
 h.c.btpReset();assert.equal(h.nodes.get('betaTrialPanel').hidden,true);assert.equal(h.calls.length,1);
 const noCal=harness(async()=>{throw Error('must not fetch')});noCal.nodes.get('calID').textContent='';await noCal.c.btpLoad();assert.equal(noCal.calls.length,0);assert.match(noCal.nodes.get('betaTrialError').text(),/先加载/);
 const denied=harness(async()=>({...data,default_prediction_changed:true}));await denied.c.btpLoad();assert.match(denied.nodes.get('betaTrialError').text(),/只读范围/);assert.equal(denied.nodes.get('betaTrialPanel').hidden,true);
 const missing=harness(async()=>{throw Error('冻结来源不同')});await missing.c.btpLoad();assert.match(missing.nodes.get('betaTrialError').text(),/来源不同/);assert.equal(missing.nodes.get('calID').text(),id);
 const malformed=harness(async()=>({...data,metrics:[]}));await malformed.c.btpLoad();assert.equal(malformed.nodes.get('betaTrialPanel').hidden,true);assert.ok(malformed.nodes.get('betaTrialError').text());
 let resolveOld,count=0;const old=new Promise(r=>resolveOld=r),stale=harness(async()=>++count===1?old:data);
 const first=stale.c.btpLoad();await stale.c.btpLoad();resolveOld({...data,summary:{...data.summary,summaries:{...summaries,trial_nested:{...summaries.trial_nested,mean_rmse_T:.99}}}});await first;
 assert.match(stale.nodes.get('betaTrialStats').text(),/0\.031000/);assert.doesNotMatch(stale.nodes.get('betaTrialStats').text(),/0\.990000/);
 let resolveChanged;const pending=new Promise(r=>resolveChanged=r),changed=harness(async()=>pending);const waiting=changed.c.btpLoad();changed.nodes.get('calID').textContent='cal_bbbbbbbbbbbb';resolveChanged(data);await waiting;assert.equal(changed.nodes.get('betaTrialPanel').hidden,true);assert.equal(changed.nodes.get('betaTrialLoad').disabled,false);
 assert.ok(stale.calls.every(c=>c.method==='GET'));
 console.log('Frozen beta preview: explicit GET only, source identity, all points, failed gates, stale/calibration-change isolation: PASS');
})().catch(error=>{console.error(error);process.exitCode=1});
