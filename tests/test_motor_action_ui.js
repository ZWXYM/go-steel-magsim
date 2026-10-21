// Run the shipped workbench against fake HTTP responses only. Never dispatch a solver.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const script=fs.readFileSync(path.join(__dirname,'../static/js/workbench.js'),'utf8');
const A='scan_aaaaaaaaaaaa',B='scan_bbbbbbbbbbbb',C='scan_cccccccccccc';
const reply=v=>({ok:true,json:async()=>structuredClone(v)});
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject}};
const job=(id,status='prepared')=>({id,status,measurement_protocol:'v8_2_cycle1',completed:0,total:1,cases:[{id:'case_01',material:'M6',status:'prepared'}]});
function harness(){
 const nodes=new Map(),calls=[],routes=new Map(),state={rows:[job(A)],queue:{active_ownership:[],queued:0,needs_attention:0},materials:['M6']};
 const el=id=>{if(!nodes.has(id))nodes.set(id,{value:id==='cores'?'2':id==='measurement'?'v8_2_cycle1':'',innerHTML:'',textContent:'',disabled:false,hidden:false,style:{}});return nodes.get(id)};
 const c=vm.createContext({document:undefined,setTimeout:()=>0,location:{hash:''},fetch:async(url,options={})=>{
  const route=url.replace('/api/workbench/',''),method=options.method||'GET';calls.push({route,method,body:options.body?JSON.parse(options.body):undefined});
  if(routes.has(method+' '+route))return routes.get(method+' '+route)(options);
  if(method==='GET'&&route==='motor/jobs')return reply(state.rows);
  if(method==='GET'&&route==='motor/queue')return reply(state.queue);
  if(method==='GET'&&route==='motor/archive')return reply([{case:'old',version:'V8_2',files:{}}]);
  throw Error('Unexpected fake request '+method+' '+route);
 }});
 vm.runInContext(script,c);c.document={getElementById:el,querySelectorAll:selector=>selector==='.material:checked'?state.materials.map(value=>({value})):[]};
 vm.runInContext('cpuOnly=false;motorReady=true',c);
 return {c,el,state,calls,routes,posts:route=>calls.filter(x=>x.method==='POST'&&(!route||x.route===route)),disabledSubmit:()=>/<button[^>]*disabled[^>]*onclick="submit\('/.test(el('jobs').innerHTML)};
}
(async()=>{
 // A polling redraw keeps the pending submit disabled. Queue recovery cannot race it.
 const h=harness();await h.c.jobs();const pending=deferred();h.routes.set('POST motor/jobs/'+A+'/submit',()=>pending.promise);
 const first=h.c.submit(A);assert.equal(h.disabledSubmit(),true);await h.c.jobs();assert.equal(h.disabledSubmit(),true);
 await h.c.submit(A);await h.c.recoverQueue();assert.equal(h.posts().length,1);
 h.state.rows[0].status='queued';pending.resolve(reply(h.state.rows[0]));await first;assert.doesNotMatch(h.el('jobs').innerHTML,/onclick="submit/);
 // Recovery stays single even when refreshed, including the busy acknowledgement.
 const q=harness();await q.c.jobs();const recovery=deferred();q.routes.set('POST motor/queue/recover',()=>recovery.promise);
 const qr=q.c.recoverQueue();await q.c.jobs();assert.equal(q.el('recoverQueue').disabled,true);assert.equal(q.disabledSubmit(),true);
 await q.c.recoverQueue();await q.c.submit(A);assert.equal(q.posts().length,1);recovery.resolve(reply({status:'busy',reason:'原活动进程保持等待'}));await qr;
 assert.match(q.el('queueNotice').textContent,/原活动进程保持等待/);assert.equal(q.el('recoverQueue').disabled,false);
 // Continue produces a separate prepared child and never automatically submits it.
 const next=harness();next.state.rows=[job(A,'needs_attention')];await next.c.jobs();const continuation=deferred();next.routes.set('POST motor/jobs/'+A+'/continue',()=>continuation.promise);
 const nr=next.c.continueMotor(A);await next.c.jobs();await next.c.continueMotor(A);assert.equal(next.posts().length,1);
 next.state.rows[0].continuation_job=B;const child={...job(B),parent_job:A};next.state.rows.push(child);continuation.resolve(reply(child));await nr;
 assert.match(next.el('queueNotice').textContent,new RegExp(B));assert.equal(next.posts().filter(x=>x.route.endsWith('/submit')).length,0);await next.c.continueMotor(A);assert.equal(next.posts().length,1);
 // Derived exports are single and their links must refer to the exact source job.
 const ex=harness();ex.state.rows=[{...job(A,'completed'),summary:{results:[]}}];await ex.c.jobs();const exportWait=deferred();ex.routes.set('POST motor/jobs/'+A+'/export',()=>exportWait.promise);
 const er=ex.c.exportMotor(A);await ex.c.jobs();await ex.c.exportMotor(A);assert.equal(ex.posts().length,1);
 exportWait.resolve(reply({id:'summary_test',job_id:A,files:{'reports_bundle.zip':'exports/summary_test/reports_bundle.zip','summary.csv':'exports/summary_test/summary.csv'}}));await er;
 assert.match(ex.el('jobs').innerHTML,new RegExp('/motor/'+A+'/exports/summary_test/summary.csv'));
 const wrong=harness();wrong.state.rows=structuredClone(ex.state.rows);await wrong.c.jobs();wrong.routes.set('POST motor/jobs/'+A+'/export',()=>reply({job_id:B,files:{'reports_bundle.zip':'wrong.zip','summary.csv':'wrong.csv'}}));await wrong.c.exportMotor(A);
 assert.doesNotMatch(wrong.el('jobs').innerHTML,/wrong\.zip|wrong\.csv/);assert.match(wrong.el('queueNotice').textContent,/来源任务不符/);
 // An older GET cannot overwrite the final post-submit read or the newest failure.
 const late=harness();await late.c.jobs();const stale=deferred();late.routes.set('GET motor/jobs',()=>stale.promise);const oldRead=late.c.jobs();late.routes.delete('GET motor/jobs');
 late.routes.set('POST motor/jobs/'+A+'/submit',()=>{late.state.rows[0].status='running';return reply(late.state.rows[0])});await late.c.submit(A);
 stale.resolve(reply([job(A)]));await oldRead;assert.match(late.el('jobs').innerHTML,/正在求解/);assert.doesNotMatch(late.el('jobs').innerHTML,/onclick="submit/);
 const oldError=deferred();late.routes.set('GET motor/jobs',()=>oldError.promise);const oldPoll=late.c.pollJobs();late.routes.delete('GET motor/jobs');await late.c.jobs();const latestInfo=late.el('queueInfo').textContent;
 oldError.reject(Error('旧读取失败'));await oldPoll;assert.equal(late.el('queueInfo').textContent,latestInfo);
 const newestError=deferred();const olderSuccess=deferred();late.routes.set('GET motor/jobs',()=>olderSuccess.promise);const earlier=late.c.jobs();late.routes.set('GET motor/jobs',()=>newestError.promise);const newest=late.c.jobs();newestError.reject(Error('最新读取失败'));await assert.rejects(newest);olderSuccess.resolve(reply([job(A)]));await earlier;
 assert.equal(late.el('recoverQueue').disabled,true);assert.match(late.el('queueInfo').textContent,/最新读取失败/);
 // Uncertain submission does not retry and remains disabled until a successful read.
 const fail=harness();await fail.c.jobs();fail.routes.set('POST motor/jobs/'+A+'/submit',()=>{throw Error('连接中断，状态未确认')});await fail.c.submit(A);await fail.c.submit(A);
 assert.equal(fail.posts().length,1);assert.equal(fail.disabledSubmit(),true);assert.match(fail.el('queueNotice').textContent,/不自动重试/);await fail.c.jobs();assert.equal(fail.disabledSubmit(),false);
 // CPU mode blocks native submit/continue/recovery but still permits CPU preparation.
 const cpu=harness();cpu.state.rows=[job(A),job(B,'needs_attention'),job(C,'failed')];vm.runInContext('cpuOnly=true',cpu.c);await cpu.c.jobs();await cpu.c.submit(A);await cpu.c.continueMotor(B);await cpu.c.recoverQueue();await cpu.c.submit(C);assert.equal(cpu.posts().length,0);
 const prep=deferred();cpu.routes.set('POST motor/jobs',()=>prep.promise);const pr=cpu.c.prepareMotor();cpu.state.materials=['changed'];cpu.el('measurement').value='periodic_cycle3_v1';await cpu.c.prepareMotor();assert.equal(cpu.posts().length,1);
 assert.deepEqual(cpu.posts()[0].body,{materials:['M6'],cores:2,measurement_protocol:'v8_2_cycle1'});
 const saved={...job(C),measurement_protocol:'v8_2_cycle1'};cpu.state.rows.push(saved);prep.resolve(reply(saved));await pr;assert.match(cpu.el('queueNotice').textContent,/M6 · v8_2_cycle1/);assert.equal(cpu.el('prepareMotor').disabled,false);
 // A mismatching saved recipe cannot be presented as the requested material scan.
 const bad=harness();bad.state.materials=['A','B'];bad.routes.set('POST motor/jobs',()=>reply({...job(A),cases:[{material:'A'},{material:'A'}]}));await bad.c.prepareMotor();assert.match(bad.el('queueNotice').textContent,/牌号或测量口径不符/);
 // Archive list refresh cannot re-enable a pending archive export.
 const archive=harness(),arWait=deferred();archive.routes.set('POST motor/archive/export',()=>arWait.promise);const ar=archive.c.exportArchive();await archive.c.loadArchive();assert.equal(archive.el('exportArchive').disabled,true);await archive.c.exportArchive();assert.equal(archive.posts().length,1);
 arWait.resolve(reply({files:{'reports_bundle.zip':'/bundle','summary.csv':'/csv','summary.md':'/md'},case_count:1,csv_verified_count:0}));await ar;assert.equal(archive.el('exportArchive').disabled,false);

 // Fractional or missing cores must not be silently rounded into a different request.
 for(const text of ['1.5','','0','33','2oops']){const invalid=harness();invalid.el('cores').value=text;await invalid.c.prepareMotor();assert.equal(invalid.posts().length,0,'invalid core input must not create a scan: '+text);assert.match(invalid.el('queueNotice').textContent,/核数/);assert.equal(invalid.el('prepareMotor').disabled,false)}
 const exponent=harness();exponent.el('cores').value='1e1';exponent.routes.set('POST motor/jobs',()=>reply(job(A)));await exponent.c.prepareMotor();assert.equal(exponent.posts()[0].body.cores,10,'scientific notation in a number input must keep its numeric value');

 const noMaterial=harness();noMaterial.state.materials=[];await noMaterial.c.prepareMotor();assert.equal(noMaterial.posts().length,0);assert.match(noMaterial.el('queueNotice').textContent,/至少选择/);
 const recoverInput=harness();recoverInput.el('cores').value='1.5';await recoverInput.c.prepareMotor();recoverInput.el('cores').value='2';recoverInput.routes.set('POST motor/jobs',()=>reply(job(A)));await recoverInput.c.prepareMotor();assert.equal(recoverInput.el('error').style.display,'none');assert.equal(recoverInput.posts().length,1);

 // Saved configuration is a read-only per-job view, kept through polling redraws.
 const manifest=(row,cores=2)=>({id:row.id,cores,model_version:'FROZEN_MODEL',design:'Motor-CAD 2',setup:'Setup1',aedt_version:'2025.1',measurement_protocol:row.measurement_protocol,cases:row.cases.map(c=>({id:c.id,material:c.material})),template_sha256:'a'.repeat(64)});
 const cfg=harness();await cfg.c.jobs();const waiting=deferred(),configRoute='files/motor/'+A+'/manifest.json';cfg.routes.set('GET '+configRoute,()=>waiting.promise);const openConfig=cfg.c.toggleMotorConfiguration(A);
 await cfg.c.jobs();await cfg.c.toggleMotorConfiguration(A);assert.match(cfg.el('jobs').innerHTML,/正在读取本任务保存配置/);assert.equal(cfg.calls.filter(c=>c.route===configRoute).length,1);
 waiting.resolve(reply(manifest(cfg.state.rows[0])));await openConfig;assert.match(cfg.el('jobs').innerHTML,/核数<\/th><td>2/);assert.match(cfg.el('jobs').innerHTML,/FROZEN_MODEL/);assert.match(cfg.el('jobs').innerHTML,new RegExp('/files/motor/'+A+'/manifest.json'));
 await cfg.c.jobs();assert.match(cfg.el('jobs').innerHTML,/核数<\/th><td>2/);await cfg.c.toggleMotorConfiguration(A);assert.doesNotMatch(cfg.el('jobs').innerHTML,/核数<\/th><td>/);await cfg.c.toggleMotorConfiguration(A);assert.match(cfg.el('jobs').innerHTML,/核数<\/th><td>2/);assert.equal(cfg.calls.filter(c=>c.route===configRoute).length,1);assert.equal(cfg.posts().length,0);
 cfg.routes.set('GET '+configRoute,()=>reply({...manifest(cfg.state.rows[0]),id:B,cores:31}));await cfg.c.readMotorConfiguration(A);assert.match(cfg.el('jobs').innerHTML,/配置暂不可读/);assert.doesNotMatch(cfg.el('jobs').innerHTML,/核数<\/th><td>31/);assert.equal(cfg.posts().length,0);
 cfg.routes.set('GET '+configRoute,()=>reply(manifest(cfg.state.rows[0],4)));await cfg.c.readMotorConfiguration(A);assert.match(cfg.el('jobs').innerHTML,/核数<\/th><td>4/);assert.equal(cfg.posts().length,0);
 cfg.state.rows[0].measurement_protocol='periodic_cycle3_v1';await cfg.c.jobs();assert.match(cfg.el('jobs').innerHTML,/协议不一致/);assert.doesNotMatch(cfg.el('jobs').innerHTML,/核数<\/th><td>4/);

 const cpuConfig=harness();vm.runInContext('cpuOnly=true',cpuConfig.c);await cpuConfig.c.jobs();cpuConfig.routes.set('GET '+configRoute,()=>reply(manifest(cpuConfig.state.rows[0])));await cpuConfig.c.toggleMotorConfiguration(A);assert.match(cpuConfig.el('jobs').innerHTML,/FROZEN_MODEL/);assert.equal(cpuConfig.posts().length,0);

 // A queue failure must not hide a successfully refreshed list of saved jobs.
 const partial=harness();await partial.c.jobs();partial.state.rows=[job(B)];partial.routes.set('GET motor/queue',()=>{throw Error('queue state unreadable')});await assert.rejects(partial.c.jobs());assert.match(partial.el('jobs').innerHTML,new RegExp(B),'readable new rows must remain visible when queue fails');assert.equal(partial.disabledSubmit(),true);
 partial.routes.set('GET files/motor/'+B+'/manifest.json',()=>reply(manifest(partial.state.rows[0])));await partial.c.toggleMotorConfiguration(B);assert.match(partial.el('jobs').innerHTML,/FROZEN_MODEL/);assert.equal(partial.posts().length,0);partial.routes.delete('GET motor/queue');await partial.c.jobs();assert.equal(partial.disabledSubmit(),false);

 const broken=harness();broken.state.rows=[job(A),{id:B,status:'completed',cases:[],completed:0,total:0,summary:{results:null}},job(C)];await broken.c.jobs();assert.match(broken.el('jobs').innerHTML,new RegExp(A));assert.match(broken.el('jobs').innerHTML,new RegExp(C));assert.match(broken.el('jobs').innerHTML,/此任务暂时无法显示/);assert.equal(broken.posts().length,0);
 const unknown=harness();unknown.state.rows=[job(A),null,job(C)];await assert.rejects(unknown.c.jobs());assert.match(unknown.el('jobs').innerHTML,new RegExp(C));assert.equal(unknown.disabledSubmit(),true);unknown.routes.set('GET files/motor/'+C+'/manifest.json',()=>reply(manifest(unknown.state.rows[2])));await unknown.c.toggleMotorConfiguration(C);assert.match(unknown.el('jobs').innerHTML,/FROZEN_MODEL/);assert.equal(unknown.posts().length,0);
 console.log('Motor actions UI: single submit/recovery/continue/export, pending redraw, read ordering, exact source, CPU preparation and no automatic native retry: PASS');
})().catch(e=>{console.error(e);process.exitCode=1});
