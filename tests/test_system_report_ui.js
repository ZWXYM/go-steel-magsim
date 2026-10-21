const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const code=fs.readFileSync('static/js/system_report.js','utf8'),tick=()=>new Promise(setImmediate);
function harness(){
 const nodes=new Map(),get=id=>{if(!nodes.has(id))nodes.set(id,new Node());return nodes.get(id)},pending=[],calls=[],downloads=[],blobs=[],timers=[];
 class Node{constructor(){this.children=[];this.listeners={};this.textContent='';this.disabled=false}append(...nodes){this.children.push(...nodes)}replaceChildren(...nodes){this.children=nodes}addEventListener(name,fn){this.listeners[name]=fn}click(){downloads.push({href:this.href,name:this.download})}}
 class Blob{constructor(parts){this.parts=parts;blobs.push(this)}}
 const c=vm.createContext({document:{getElementById:get,createElement:()=>new Node()},fetch:url=>{calls.push(url);return new Promise(resolve=>pending.push(resolve))},Blob,URL:{createObjectURL:()=>`blob:${blobs.length}`,revokeObjectURL(){}},setTimeout:fn=>timers.push(fn)});vm.runInContext(code,c);return{get,pending,calls,downloads,blobs,timers};
}
function report(id='a'){return {schema:'go_steel_system_report_v1',created_utc:id,web_address:'http://local',runtime:{ok:true,value:{source_matches_startup:true,cpu_only:false}},license_TCP:{ok:true,value:{available:true}},record_groups:[{label:'示例',record_count:1,readable_count:1,records:[{id:'case_A',status:'failed',state_file:'motor/scan_a/state.json',error:'original error'}]}],report_sha256:id.repeat(64)}}
function respond(h,data,ok=true){h.pending.shift()({ok,json:async()=>data})}
(async()=>{
 const h=harness();assert.equal(h.calls.length,1);assert.equal(h.get('reportDownload').disabled,true);respond(h,report());await tick();assert.equal(h.get('reportDownload').disabled,false);h.get('reportDownload').listeners.click();assert.deepEqual(JSON.parse(h.blobs[0].parts[0]),report());assert.match(h.downloads[0].name,/aaaaaaaaaaaa/);
 const refresh=h.get('reportRefresh').listeners.click();assert.equal(h.get('reportDownload').disabled,true);h.get('reportDownload').listeners.click();assert.equal(h.downloads.length,1);respond(h,{error:'offline'},false);await refresh;assert.match(h.get('reportStatus').textContent,/保留上一次/);h.get('reportDownload').listeners.click();assert.deepEqual(JSON.parse(h.blobs.at(-1).parts[0]),report());
 const first=h.get('reportRefresh').listeners.click(),second=h.get('reportRefresh').listeners.click(),old=h.pending.shift(),latest=h.pending.shift();latest({ok:true,json:async()=>report('b')});await second;old({ok:true,json:async()=>report('c')});await first;h.get('reportDownload').listeners.click();assert.deepEqual(JSON.parse(h.blobs.at(-1).parts[0]),report('b'));
 const invalid=h.get('reportRefresh').listeners.click();respond(h,{...report('d'),record_groups:[{label:'bad',records:null}]});await invalid;assert.match(h.get('reportStatus').textContent,/格式不可读/);h.get('reportDownload').listeners.click();assert.deepEqual(JSON.parse(h.blobs.at(-1).parts[0]),report('b'));
 assert.ok(h.calls.every(url=>url==='/api/workbench/system-report'));assert.ok(h.get('reportGroups').children[0].children.length>0);
 console.log('System report GET-only reading, exact displayed JSON download, stale/error guards and malformed snapshot preservation passed');
})().catch(error=>{console.error(error);process.exitCode=1});
