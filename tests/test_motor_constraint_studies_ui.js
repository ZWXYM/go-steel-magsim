/* Source selection, fixed-study creation, persistence and response isolation. */
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const script=fs.readFileSync(path.join(__dirname,'../static/js/motor_constraint_studies.js'),'utf8');
class Node{
 constructor(tag){this.tag=tag;this.children=[];this.textContent='';this.value='';this.disabled=false;this.hidden=false;this.listeners={}}
 append(...children){this.children.push(...children)}replaceChildren(...children){this.children=children}addEventListener(name,fn){this.listeners[name]=fn}
 querySelectorAll(selector){return flatten(this).filter(node=>node.tag===selector)}
 set innerHTML(_){throw Error('Untrusted source names must be text')}
}
function flatten(node){return [node,...node.children.flatMap(flatten)]}
async function flush(){for(let i=0;i<20;i++)await Promise.resolve()}
const report={schema:'motor_constraint_study_v1',id:'csens_aaaaaaaaaaaa',dataset:'v8_2_bound_archive',model_version:'bound_v1',measurement_protocol:'v8_2_cycle1',
 reference:'reference',torque_reference:'reference',scope:'synthetic fixture',data_usage:{note:'saved data only'},source_hashes_sha256:'a'.repeat(64),
 configuration_count:48,source_case_count:6,configurations_with_feasible_case:36,original_default_candidate_count:0,
 configurations:[{id:'c00',config:{min_ripple_reduction_pct:10,min_torque_change_pct:-2,max_iron_loss_increase_pct:0},meets_limits_count:1,best_case:'<script>candidate</script>'},
 {id:'c33',config:{min_ripple_reduction_pct:30,min_torque_change_pct:0,max_iron_loss_increase_pct:0},meets_limits_count:0,best_case:null,is_original_default:true}]};
function input(id){return {source_hashes_sha256:'a'.repeat(64),dataset:{id,version:id==='v8_1'?'old_v1':'bound_v1',measurement_protocol:'v8_2_cycle1',rows:[{},{},{},{},{},{}],scope:'source scope',data_usage:{note:'source note'},default_reference:'reference',references:[{case:'reference',material:'M6'}]}}}
(async()=>{
 const nodes=new Map(),calls=[];let studies=[],post=()=>{studies=[{...report,readable:true}];return structuredClone(report)},getInput=id=>input(id);
 const document={getElementById:id=>{if(!nodes.has(id))nodes.set(id,new Node('root'));return nodes.get(id)},createElement:tag=>new Node(tag)};
 const context={document,URL,location:{href:'http://local/motor-sensitivity'},history:{replaceState:(_,__,value)=>{context.location.href='http://local'+value}},
  fetch:async(url,options)=>{calls.push({url,options});const suffix=url.replace('/api/workbench/optimization/','');let data;
   if(suffix==='datasets')data=[{id:'v8_1',title:'old source'},{id:'v8_2_bound_archive',title:'bound source'}];
   else if(suffix.startsWith('sensitivity/inputs/'))data=()=>getInput(suffix.split('/').pop());
   else if(suffix==='sensitivity')data=options.method==='POST'?post:()=>structuredClone(studies);
   else if(suffix==='sensitivity/'+report.id)data=structuredClone(report);
   else throw Error('Unexpected request '+url);
   return {ok:true,json:async()=>typeof data==='function'?data():data};}};
 context.window=context;vm.runInNewContext(script,context);await flush();
 assert.equal(nodes.get('sensitivityRun').disabled,true);assert.match(nodes.get('sensitivityInputStatus').textContent,/请选择/);
 let first;getInput=id=>id==='v8_1'?new Promise(resolve=>first=resolve):input(id);
 nodes.get('sensitivityDataset').value='v8_1';const pendingSource=nodes.get('sensitivityDataset').listeners.change();await flush();
 nodes.get('sensitivityDataset').value='v8_2_bound_archive';await nodes.get('sensitivityDataset').listeners.change();
 first(input('v8_1'));await pendingSource;assert.match(nodes.get('sensitivityInputStatus').textContent,/bound_v1/);assert.equal(nodes.get('sensitivityRun').disabled,false);
 let finish;post=()=>new Promise(resolve=>finish=()=>{studies=[{...report,readable:true}];resolve(structuredClone(report))});
 const running=nodes.get('sensitivityRun').listeners.click();await flush();const count=calls.length;
 await nodes.get('sensitivityRun').listeners.click();await nodes.get('sensitivityRefresh').listeners.click();assert.equal(calls.length,count);assert.equal(nodes.get('sensitivityDataset').disabled,true);
 const write=calls.find(call=>call.options.method==='POST');assert.deepEqual(JSON.parse(write.options.body),{dataset:'v8_2_bound_archive',reference:'reference',source_hashes_sha256:'a'.repeat(64)});
 finish();await running;assert.equal(nodes.get('sensitivityResult').hidden,false);assert.equal(nodes.get('sensitivityRows').children.length,2);
 assert.match(nodes.get('sensitivitySummary').textContent,/48 组配置 × 6 条原案例，36 组有可行案；原默认约束下 0/);
 assert(context.location.href.endsWith('?study=csens_aaaaaaaaaaaa'));assert.equal(nodes.get('sensitivityDataset').disabled,false);
 const links=flatten(nodes.get('sensitivityDownloads')).filter(node=>node.tag==='a').map(node=>node.href);
 assert(links.includes('/api/workbench/optimization/sensitivity/csens_aaaaaaaaaaaa/files/case_details.csv'));
 assert(flatten(nodes.get('sensitivityRows')).some(node=>node.textContent==='<script>candidate</script>'));
 await nodes.get('sensitivityHistory').querySelectorAll('button')[0].listeners.click();assert.match(nodes.get('sensitivityActionStatus').textContent,/没有重新评分/);
 post=()=>({...structuredClone(report),source_hashes_sha256:'wrong source'});await nodes.get('sensitivityRun').listeners.click();
 assert.equal(nodes.get('sensitivityResult').hidden,true);assert.match(nodes.get('sensitivityActionStatus').textContent,/来源或基准与请求不符.*不自动重发/);
 assert.equal(calls.filter(call=>call.options.method==='POST').length,2);
 console.log('Constraint studies: last source wins, source-bound fixed POST, one pending action, original-default explanation, exact saved downloads/reopen, text safety and mismatched response isolation: PASS');
})().catch(error=>{console.error(error);process.exitCode=1});
