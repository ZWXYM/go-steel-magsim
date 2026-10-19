/* Explicit GPU start, saved reopen and pause/inspection; all API calls are CPU mocks. */
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
class Node{
 constructor(tag='div'){this.tag=tag;this.children=[];this.value='';this.textContent='';this.disabled=false;this.hidden=false}
 append(n){this.children.push(n);if(this.tag==='select'&&!this.value)this.value=n.value}
 replaceChildren(){this.children=[];this.value=''}
}
const id='nativerun_aaaaaaaaaaaa';
const base={id,status:'ready',producer_compatible:true,elapsed_seconds:0,total_jobs:2,completed_jobs:0,texture_sampling_version:'fixture',
 recipe:{calibration_id:'cal_aaaaaaaaaaaa',material_id:'CPU_FIXTURE',params:{f_Goss:.855,theta_0_deg:4.5,halfwidth_deg:6.5,Si_content:3.1},n_grains:1}};
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
 console.log('Native material UI: CPU prepare, separate GPU start, pause/resume budget, CPU mode and stale open isolation: PASS');
})().catch(e=>{console.error(e);process.exitCode=1});
