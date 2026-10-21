const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('static/js/platform_workspace.js','utf8');
function harness(){
 const calls=[],pending=[],messages=[],prompts=[],node={textContent:''};let reloads=0;
 const root={location:{origin:'http://local',href:'http://local/',reload(){reloads++}},fetch(input,options){calls.push({input,options});return new Promise(resolve=>pending.push(resolve))}};
 const context=vm.createContext({window:root,document:{getElementById:()=>node},URL,Headers,toast:(s)=>messages.push(s),prompt:()=>prompts.shift()});vm.runInContext(source,context);
 return {root,calls,pending,messages,prompts,node,reloads:()=>reloads};
}
const A='a'.repeat(64),B='b'.repeat(64),reply=(resolve,data,ok=true)=>resolve({ok,json:async()=>data});
(async()=>{
 const h=harness();let read=h.root.loadWorkspace();reply(h.pending.shift(),{workspace:'中文A',workspace_token:A});await read;
 assert.equal(h.node.textContent,'中文A');let call=h.root.fetch('/api/ml/models',{headers:{Existing:'yes'}});assert.equal(h.calls.at(-1).options.headers.get('X-Magsim-Workspace'),A);assert.equal(h.calls.at(-1).options.headers.get('Existing'),'yes');reply(h.pending.shift(),{});await call;
 for(const url of ['https://external/api/x','/api/workbench/x']){call=h.root.fetch(url);assert.equal(h.calls.at(-1).options,undefined);reply(h.pending.shift(),{});await call}
 h.prompts.push('中文A');let change=h.root.changeWorkspace();assert.equal(await h.root.changeWorkspace(),false);reply(h.pending.shift(),{workspace:'中文A',workspace_token:A});await new Promise(setImmediate);assert.equal(h.calls.at(-1).options.method,'POST');reply(h.pending.shift(),{success:true,changed:false});await change;assert.equal(h.reloads(),0);
 h.prompts.push('B');change=h.root.changeWorkspace();reply(h.pending.shift(),{workspace:'中文A',workspace_token:A});await new Promise(setImmediate);reply(h.pending.shift(),{error:'活动任务'},false);assert.equal(await change,false);assert.match(h.messages.at(-1),/活动任务/);assert.equal(h.reloads(),0);
 h.prompts.push('B');change=h.root.changeWorkspace();reply(h.pending.shift(),{workspace:'中文A',workspace_token:A});await new Promise(setImmediate);reply(h.pending.shift(),{success:true,changed:true});await change;assert.equal(h.reloads(),1);
 const other=harness();const first=other.root.loadWorkspace(),second=other.root.loadWorkspace();const r1=other.pending.shift(),r2=other.pending.shift();reply(r2,{workspace:'A',workspace_token:A});await second;reply(r1,{workspace:'B',workspace_token:B});await first;assert.equal(other.node.textContent,'A');assert.equal(other.reloads(),0);
 read=other.root.loadWorkspace();reply(other.pending.shift(),{workspace:'B',workspace_token:B});await read;assert.equal(other.reloads(),1);
 const old=harness();read=old.root.loadWorkspace();reply(old.pending.shift(),{workspace:'A'});assert.equal(await read,null);assert.match(old.messages.at(-1),/最新启动器/);
 const shared=harness();read=shared.root.loadWorkspace();const context=shared.root.getPlatformWorkspace();assert.equal(shared.calls.length,1);reply(shared.pending.shift(),{workspace:'中文A',workspace_token:A});await read;assert.equal((await context).workspace_token,A);assert.equal((await shared.root.getPlatformWorkspace()).workspace,'中文A');assert.equal(shared.calls.length,1);
 assert.equal(await other.root.getPlatformWorkspace(),null);
 console.log('Workspace token headers, request preservation, duplicate/late/error guards, idle reload and external/workbench exclusions passed');
})().catch(error=>{console.error(error);process.exitCode=1});
