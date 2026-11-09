/* Exact case choices and one pending preparation; no native submit route exists here. */
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const script=fs.readFileSync(path.join(__dirname,'../static/js/motor_study_plans.js'),'utf8');
class Node{
 constructor(tag){this.tag=tag;this.children=[];this.value='';this.textContent='';this.disabled=false;this.checked=false;this.listeners={}}
 append(...values){this.children.push(...values)}replaceChildren(...values){this.children=values}addEventListener(name,fn){this.listeners[name]=fn}
 querySelectorAll(selector){return flatten(this).filter(node=>node.tag==='input'&&(selector!=='input:checked'||node.checked))}
 set innerHTML(_){throw Error('Use source names as text')}
}
function flatten(n){return [n,...n.children.flatMap(flatten)]}
async function flush(){for(let i=0;i<20;i++)await Promise.resolve()}
const application='sapply_0123456789abcdef',other='sapply_fedcba9876543210';
const preview=id=>({application_id:id,available:true,source_binding_sha256:'a'.repeat(64),recipe:{reference:{material:'M6'},configuration_id:'c30',measurement_protocol:'v8_2_cycle1',template:'SYNTHETIC original template',candidates:[{case_id:'source_candidate',material:'<script>GOES</script>'}]}});
const result=id=>({id:'splan_123456789abcdef0',application_id:id,plan_id:'plan_aaaaaaaaaaaa',job_id:'scan_bbbbbbbbbbbb',source_analysis_id:'opt_cccccccccccc',package_id:'pack_dddddddddddddddd',materials:['M6','GOES'],current_plan_status:'prepared',created:true});
(async()=>{
 const nodes=new Map(),events={},calls=[];let read=id=>preview(id),write=()=>result(application);
 const node=id=>{if(!nodes.has(id))nodes.set(id,new Node('root'));return nodes.get(id)};node('studyPlanCores').value='2';
 const context={document:{getElementById:node,createElement:tag=>new Node(tag)},addEventListener:(name,fn)=>events[name]=fn,
  fetch:async(url,options)=>{calls.push({url,options});const id=url.split('/').at(-2);return {ok:true,json:async()=>options.method==='POST'?write(JSON.parse(options.body)):read(id)}}};context.window=context;vm.runInNewContext(script,context);
 assert.equal(node('prepareStudyPlan').disabled,true);await events['go-steel-study-application-selected']({detail:{id:application}});
 const choice=()=>node('studyPlanCases').querySelectorAll('input')[0];assert.equal(choice().checked,false);assert.equal(node('prepareStudyPlan').disabled,true);
 assert(flatten(node('studyPlanCases')).some(row=>row.textContent.includes('<script>GOES</script>')));
 choice().checked=true;choice().listeners.change();await node('refreshStudyPlan').listeners.click();assert.equal(choice().checked,true);
 node('studyPlanCores').value='';const before=calls.length;await node('prepareStudyPlan').listeners.click();assert.equal(calls.length,before);assert.match(node('studyPlanStatus').textContent,/1–32/);node('studyPlanCores').value='2';
 let finish;write=()=>new Promise(resolve=>finish=resolve);const pending=node('prepareStudyPlan').listeners.click();await flush();const count=calls.length;await node('prepareStudyPlan').listeners.click();await node('refreshStudyPlan').listeners.click();assert.equal(calls.length,count);
 await events['go-steel-study-application-selected']({detail:{id:other}});const status=node('studyPlanStatus').textContent;finish(result(application));await pending;assert.equal(node('studyPlanStatus').textContent,status);assert.equal(node('studyPlanResult').children.length,0);
 await events['go-steel-study-application-selected']({detail:{id:application}});choice().checked=true;write=()=>result(application);await node('prepareStudyPlan').listeners.click();
 assert.match(node('studyPlanStatus').textContent,/已准备 plan_aaaaaaaaaaaa.*创建时未下发/);const payload=JSON.parse(calls.filter(row=>row.options.method==='POST').at(-1).options.body);assert.deepEqual(payload,{source_binding_sha256:'a'.repeat(64),cases:['source_candidate'],cores:2});
 const links=flatten(node('studyPlanResult')).filter(row=>row.tag==='a').map(row=>row.href);assert(links.includes('/motor-optimization?analysis=opt_cccccccccccc#plans'));assert(links.includes('/api/workbench/system-data/packages/pack_dddddddddddddddd/download'));
 write=()=>({...result(application),created:false});await node('prepareStudyPlan').listeners.click();assert.match(node('studyPlanStatus').textContent,/已重开原计划/);
 read=id=>({application_id:id,available:false,reason:'no candidate'});await node('refreshStudyPlan').listeners.click();assert.equal(node('prepareStudyPlan').disabled,true);assert.match(node('studyPlanStatus').textContent,/no candidate/);
 await events['go-steel-study-selected']({detail:{id:'csens_aaaaaaaaaaaa'}});assert.equal(node('studyPlanCases').querySelectorAll('input').length,0);assert.equal(node('prepareStudyPlan').disabled,true);
 assert(calls.every(row=>/^\/api\/workbench\/optimization\/study-applications\/sapply_[a-f0-9]{16}\/scan-plan$/.test(row.url)));
 console.log('Study scan plans: explicit candidates/cores, exact source POST, no duplicate pending work, late result isolation, cached plan links and no native submission: PASS');
})().catch(error=>{console.error(error);process.exitCode=1});
