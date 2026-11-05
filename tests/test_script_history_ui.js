const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const code=fs.readFileSync('static/js/script_history.js','utf8'),tick=()=>new Promise(setImmediate);
function harness(){
 const nodes=new Map(),pending=[],calls=[];
 class Node{constructor(){this.textContent='';this.children=[];this.listeners={};this.hidden=false;this.disabled=false}append(...nodes){this.children.push(...nodes)}replaceChildren(...nodes){this.children=nodes}addEventListener(name,fn){this.listeners[name]=fn}removeAttribute(name){delete this[name]}}
 const get=id=>{if(!nodes.has(id))nodes.set(id,new Node());return nodes.get(id)};
 vm.runInNewContext(code,{document:{getElementById:get,createElement:()=>new Node()},fetch:(url,options)=>{calls.push({url,options});return new Promise(resolve=>pending.push(resolve))}});return{get,calls,pending};
}
const catalog=token=>({workspace_token:token||'a'.repeat(64),history_root:'owned history',records:[{id:'job_A',script:'A.bat',status:'completed'},{id:'job_B',script:'B.bat',status:'running'},{id:'bad',read_error:'original parse error'}]});
const detail=id=>({id,script:id+'.bat',status:'running',read_only_history:true,recent_lines:['original stdout'],log_files:['stdout.log','stdout.raw'],workspace_origin:{path:'original_A'},owner:{id:'old_owner'},revision:3});
function respond(h,data,ok=true){h.pending.shift()({ok,json:async()=>data})}
function button(h,index){return h.get('historyRows').children[index].children[4].children[0]}
(async()=>{
 const h=harness();respond(h,catalog());await tick();assert.equal(h.get('historyRows').children.length,3);assert.equal(button(h,2).disabled,true);
 const first=button(h,0).listeners.click(),a=h.pending.shift(),second=button(h,1).listeners.click(),b=h.pending.shift();a({ok:true,json:async()=>detail('job_A')});await first;assert.equal(h.get('historyTextLog').hidden,true);b({ok:true,json:async()=>detail('job_B')});await second;assert.match(h.get('historyTextLog').href,/job_B\/files\/stdout.log\?workspace_token=aaaa/);assert.equal(h.get('historyLog').textContent,'original stdout');assert.match(h.get('historyDetailStatus').textContent,/只读.*未核对/);
 const refresh=h.get('historyRefresh').listeners.click();assert.equal(h.get('historyTextLog').hidden,true);respond(h,catalog('b'.repeat(64)));await tick();assert.equal(h.calls.at(-1).options.headers['X-Magsim-Workspace'],'b'.repeat(64));respond(h,detail('job_B'));await refresh;assert.match(h.get('historyRawLog').href,/workspace_token=bbbb/);
 const invalid=button(h,0).listeners.click();respond(h,detail('wrong_ID'));await invalid;assert.match(h.get('historyDetailStatus').textContent,/身份不一致/);assert.equal(h.get('historyRawLog').hidden,true);
 const stale=button(h,1).listeners.click(),staleResponse=h.pending.shift(),changed=h.get('historyRefresh').listeners.click();respond(h,{error:'source unavailable'},false);await changed;staleResponse({ok:true,json:async()=>detail('job_B')});await stale;assert.equal(h.get('historyDetail').hidden,true);assert.equal(h.get('historyRows').children.length,0);assert.equal(h.get('historyRawLog').hidden,true);
 assert.ok(h.calls.every(call=>!call.options?.method));assert.ok(h.calls.every(call=>!call.url.endsWith('/stop')&&!call.url.includes('/run-script')));
 console.log('Read-only script history selection, pinned workspace log links, corrupt rows, late/error isolation and zero task actions passed');
})().catch(error=>{console.error(error);process.exitCode=1});
