/* Explicit study choices, cached results and late responses do not dispatch native work. */
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const script=fs.readFileSync(path.join(__dirname,'../static/js/motor_study_application.js'),'utf8');
class Node{
 constructor(tag){this.tag=tag;this.children=[];this.textContent='';this.value='';this.disabled=false;this.listeners={}}
 append(...children){this.children.push(...children)}replaceChildren(...children){this.children=children}
 addEventListener(name,fn){this.listeners[name]=fn}
 set innerHTML(_){throw Error('Use safe text')}
}
const study='csens_aaaaaaaaaaaa',other='csens_bbbbbbbbbbbb';
const preview=id=>({study_id:id,dataset:'bound_source',reference:'M6',source_matches:true,source_binding_sha256:'a'.repeat(64),configurations:[
 {id:'c30',config:{min_ripple_reduction_pct:30,min_torque_change_pct:-.5,max_iron_loss_increase_pct:0},meets_limits_count:1},
 {id:'c33',config:{min_ripple_reduction_pct:30,min_torque_change_pct:0,max_iron_loss_increase_pct:0},meets_limits_count:0,is_original_default:true}]});
const record=(id=study,configuration='c30')=>({id:'sapply_0123456789abcdef',analysis_id:'opt_123456abcdef',study_id:id,configuration_id:configuration,source_binding_sha256:'a'.repeat(64),created:true,meets_limits_count:configuration==='c30'?1:0,best_case:configuration==='c30'?'candidate':null,original_default_candidate_count:0});
function flatten(node){return [node,...node.children.flatMap(flatten)]}
async function flush(){for(let i=0;i<20;i++)await Promise.resolve()}
(async()=>{
 const nodes=new Map(),events={},calls=[];let get=id=>preview(id),post=body=>record(study,body.configuration_id);
 const node=id=>{if(!nodes.has(id))nodes.set(id,new Node('root'));return nodes.get(id)};
 const context={document:{getElementById:node,createElement:tag=>new Node(tag)},URL,location:{href:'http://local/motor-sensitivity'},addEventListener:(name,fn)=>events[name]=fn,
  fetch:async(url,options)=>{calls.push({url,options});const id=url.split('/').at(-2);return {ok:true,json:async()=>options.method==='POST'?post(JSON.parse(options.body)):get(id)}}};
 context.window=context;vm.runInNewContext(script,context);assert.equal(node('applyStudyConfig').disabled,true);
 await events['go-steel-study-selected']({detail:{id:study}});assert.equal(node('studyConfiguration').value,'');assert.equal(node('applyStudyConfig').disabled,true);
 node('studyConfiguration').value='c30';node('studyConfiguration').listeners.change();assert.equal(node('applyStudyConfig').disabled,false);
 let finish;post=()=>new Promise(resolve=>finish=resolve);const pending=node('applyStudyConfig').listeners.click();await flush();const before=calls.length;
 await node('applyStudyConfig').listeners.click();await node('refreshStudyApplication').listeners.click();assert.equal(calls.length,before);assert.equal(node('studyConfiguration').disabled,true);
 await events['go-steel-study-selected']({detail:{id:other}});const newStatus=node('studyApplicationStatus').textContent;finish(record());await pending;
 assert.equal(node('studyApplicationStatus').textContent,newStatus);assert.equal(node('studyApplicationResult').children.length,0);
 await events['go-steel-study-selected']({detail:{id:study}});node('studyConfiguration').value='c33';node('studyConfiguration').listeners.change();post=body=>record(study,body.configuration_id);
 await node('applyStudyConfig').listeners.click();assert.match(node('studyApplicationStatus').textContent,/0案满足.*首选无.*原默认仍为0/);
 const links=flatten(node('studyApplicationResult')).filter(n=>n.tag==='a').map(n=>n.href);assert(links.includes('/motor-optimization?analysis=opt_123456abcdef'));assert(links.some(href=>href.endsWith('/application_bundle.zip')));
 const body=JSON.parse(calls.filter(row=>row.options.method==='POST').at(-1).options.body);assert.deepEqual(body,{configuration_id:'c33',source_binding_sha256:'a'.repeat(64)});
 post=()=>({...record(study,'c33'),created:false});await node('applyStudyConfig').listeners.click();assert.match(node('studyApplicationStatus').textContent,/已重开已有记录/);
 get=id=>({...preview(id),source_matches:false,source_error:'changed source'});await node('refreshStudyApplication').listeners.click();assert.equal(node('applyStudyConfig').disabled,true);assert.match(node('studyApplicationStatus').textContent,/changed source/);const count=calls.length;await node('applyStudyConfig').listeners.click();assert.equal(calls.length,count);
 get=id=>preview(id);await node('refreshStudyApplication').listeners.click();node('studyConfiguration').value='c30';post=()=>record(other);await node('applyStudyConfig').listeners.click();assert.match(node('studyApplicationStatus').textContent,/返回筛选与所选研究配置不符.*不自动重发/);assert.equal(node('studyApplicationResult').children.length,0);
 assert(calls.every(row=>/^\/api\/workbench\/optimization\/sensitivity\/csens_[a-f0-9]{12}\/applications$/.test(row.url)));
 console.log('Study applications: explicit config/source binding, default-zero result, one pending POST, cached reopen, late study response isolation and no native dispatch: PASS');
})().catch(error=>{console.error(error);process.exitCode=1});
