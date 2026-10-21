/* Saved material deep links and refresh selection; no native or browser. */
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
class Node{
 constructor(tag='div'){this.tag=tag;this.children=[];this.textContent='';this.value='';this.dataset={};this.hidden=false;this.disabled=false}
 append(...nodes){this.children.push(...nodes);if(this.tag==='select'&&!this.value&&nodes.length)this.value=nodes[0].value}
 replaceChildren(...nodes){this.children=nodes;if(this.tag==='select')this.value=''} setAttribute(){}
}
const records=[{id:'cal_a',entries:[{key:'fit:B23R075',kind:'fit',label:'B23R075',available:true,excluded_grades:[]},{key:'prediction:pred_b',kind:'prediction',label:'pred_b',available:true,excluded_grades:['B30P105']}]}];
function harness(search){const nodes=new Map(),calls=[],rendered=[],location={pathname:'/material-library',search,hash:'#detail'};const c=vm.createContext({document:undefined,location,history:{replaceState(_state,_unused,url){const parsed=new URL(url,'http://localhost');location.pathname=parsed.pathname;location.search=parsed.search;location.hash=parsed.hash}},URLSearchParams,fetch:async url=>{calls.push(url);return {ok:true,json:async()=>url.endsWith('material-inputs')?{records}:{calibration_id:decodeURIComponent(url.split('/').at(-2)),key:decodeURIComponent(url.split('/').at(-1))}}}});
 vm.runInContext(fs.readFileSync('static/js/material_library.js','utf8'),c);c.document={getElementById:id=>{if(!nodes.has(id))nodes.set(id,new Node(id==='calibration'?'select':'div'));return nodes.get(id)},createElement:tag=>new Node(tag),querySelectorAll:()=>[]};
 c.mlRender=v=>rendered.push(v);return {c,nodes,calls,rendered};
}
const flush=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
 const h=harness('?calibration=cal_a&key=prediction%3Apred_b');await h.c.mlRefresh();await flush();assert.equal(h.rendered.at(-1).key,'prediction:pred_b');
 await h.c.mlRefresh();await flush();assert.equal(h.rendered.at(-1).key,'prediction:pred_b');assert.equal(h.nodes.get('calibration').value,'cal_a');
 await h.c.mlOpen('fit:B23R075');assert.equal(new URLSearchParams(h.c.location.search).get('key'),'fit:B23R075','material choice must update its saved address');assert.equal(h.c.location.hash,'#detail');
 const reopened=harness(h.c.location.search);await reopened.c.mlRefresh();await flush();assert.equal(reopened.rendered.at(-1).key,'fit:B23R075','browser reload must reopen the chosen material');
 h.c.location.search+='&view=source';await h.c.mlOpen('prediction:pred_b');assert.equal(new URLSearchParams(h.c.location.search).get('view'),'source','unrelated address options remain');
 h.nodes.get('calibration').value='cal_b';await h.c.mlOpen('holdout:B30P105');const newBank=harness(h.c.location.search);assert.equal(new URLSearchParams(newBank.c.location.search).get('calibration'),'cal_b','switching bank updates the address too');
 const mismatch=harness('?calibration=cal_a&key=fit%3AB23R075');mismatch.c.document.getElementById('calibration').value='cal_a';mismatch.c.fetch=async()=>({ok:true,json:async()=>({calibration_id:'cal_b',key:'fit:B23R075'})});await mismatch.c.mlOpen('fit:B23R075');assert.equal(mismatch.rendered.length,0);assert.match(mismatch.nodes.get('error').textContent,/不一致/);assert.equal(new URLSearchParams(mismatch.c.location.search).get('calibration'),'cal_a');
 let resolveDetail;const late=harness('');late.c.document.getElementById('calibration').value='cal_a';late.c.fetch=async url=>({ok:true,json:async()=>url.endsWith('fit%3AB23R075')?new Promise(resolve=>resolveDetail=resolve):({calibration_id:'cal_a',key:'prediction:pred_b'})});const slow=late.c.mlOpen('fit:B23R075');await flush();await late.c.mlOpen('prediction:pred_b');resolveDetail({calibration_id:'cal_a',key:'fit:B23R075'});await slow;assert.equal(late.rendered.length,1);assert.equal(new URLSearchParams(late.c.location.search).get('key'),'prediction:pred_b','late material reads cannot replace the current address');
 const failed=harness('?calibration=cal_a&key=prediction%3Apred_b');failed.c.document.getElementById('calibration').value='cal_a';failed.c.fetch=async()=>({ok:false,json:async()=>({error:'material file missing'})});await failed.c.mlOpen('fit:B23R075');assert.equal(failed.rendered.length,0);assert.equal(new URLSearchParams(failed.c.location.search).get('key'),'prediction:pred_b','failed reads do not claim to have opened a different material');assert.match(failed.nodes.get('error').textContent,/missing/);
 const bad=harness('?calibration=cal_missing&key=prediction%3Apred_b');await bad.c.mlRefresh();await flush();assert.equal(bad.rendered.length,0);assert.match(bad.nodes.get('error').textContent,/不存在/);assert.equal(bad.calls.length,1);
 const unknown=harness('?calibration=cal_a&key=prediction%3Aunknown');await unknown.c.mlRefresh();await flush();assert.equal(unknown.rendered.length,0);assert.match(unknown.nodes.get('error').textContent,/条目不存在/);
 let resolveOld,n=0;const old=new Promise(r=>resolveOld=r),stale=harness('');stale.c.fetch=async()=>({ok:true,json:async()=>++n===1?old:{records}});
 const first=stale.c.mlRefresh();await stale.c.mlRefresh();resolveOld({records:[]});await first;await flush();assert.equal(stale.nodes.get('calibration').children.length,1);assert.equal(stale.nodes.get('refresh').disabled,false);
 console.log('Material library selection: choice/reload URL, bank binding, response identity, failed/late reads, refresh and invalid targets: PASS');
})().catch(error=>{console.error(error);process.exitCode=1});
