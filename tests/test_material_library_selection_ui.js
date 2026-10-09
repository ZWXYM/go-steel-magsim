/* Saved material deep links and refresh selection; no native or browser. */
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
class Node{
 constructor(tag='div'){this.tag=tag;this.children=[];this.textContent='';this.value='';this.dataset={};this.hidden=false;this.disabled=false}
 append(...nodes){this.children.push(...nodes);if(this.tag==='select'&&!this.value&&nodes.length)this.value=nodes[0].value}
 replaceChildren(...nodes){this.children=nodes;if(this.tag==='select')this.value=''} setAttribute(){}
}
const records=[{id:'cal_a',entries:[{key:'fit:B23R075',kind:'fit',label:'B23R075',available:true,excluded_grades:[]},{key:'prediction:pred_b',kind:'prediction',label:'pred_b',available:true,excluded_grades:['B30P105']}]}];
function harness(search){const nodes=new Map(),calls=[],rendered=[];const c=vm.createContext({document:undefined,location:{search},URLSearchParams,fetch:async url=>{calls.push(url);return {ok:true,json:async()=>url.endsWith('material-library')?{records}:{key:decodeURIComponent(url.split('/').at(-1))}}}});
 vm.runInContext(fs.readFileSync('static/js/material_library.js','utf8'),c);c.document={getElementById:id=>{if(!nodes.has(id))nodes.set(id,new Node(id==='calibration'?'select':'div'));return nodes.get(id)},createElement:tag=>new Node(tag),querySelectorAll:()=>[]};
 c.mlRender=v=>rendered.push(v);return {c,nodes,calls,rendered};
}
const flush=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
 const h=harness('?calibration=cal_a&key=prediction%3Apred_b');await h.c.mlRefresh();await flush();assert.equal(h.rendered.at(-1).key,'prediction:pred_b');
 await h.c.mlRefresh();await flush();assert.equal(h.rendered.at(-1).key,'prediction:pred_b');assert.equal(h.nodes.get('calibration').value,'cal_a');
 const bad=harness('?calibration=cal_missing&key=prediction%3Apred_b');await bad.c.mlRefresh();await flush();assert.equal(bad.rendered.length,0);assert.match(bad.nodes.get('error').textContent,/不存在/);assert.equal(bad.calls.length,1);
 const unknown=harness('?calibration=cal_a&key=prediction%3Aunknown');await unknown.c.mlRefresh();await flush();assert.equal(unknown.rendered.length,0);assert.match(unknown.nodes.get('error').textContent,/条目不存在/);
 let resolveOld,n=0;const old=new Promise(r=>resolveOld=r),stale=harness('');stale.c.fetch=async()=>({ok:true,json:async()=>++n===1?old:{records}});
 const first=stale.c.mlRefresh();await stale.c.mlRefresh();resolveOld({records:[]});await first;await flush();assert.equal(stale.nodes.get('calibration').children.length,1);assert.equal(stale.nodes.get('refresh').disabled,false);
 console.log('Material library selection: exact deep link, refresh preservation, invalid targets and stale catalog isolation: PASS');
})().catch(error=>{console.error(error);process.exitCode=1});
