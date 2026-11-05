/* Exact saved dataset links, immutable package downloads and failed refreshes. */
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const script=fs.readFileSync(path.join(__dirname,'../static/js/system_data.js'),'utf8');
const {validDataset,validCal,validPack}=require('../static/js/system_data.js');
assert(validDataset('scan_aaaaaaaaaaaa'));assert(validDataset('v8_2_bound_archive'));assert(!validDataset('../v8_1'));assert(!validCal('javascript:bad'));assert(validPack('pack_0123456789abcdef'));
const fixture={schema:'go_steel_system_data_v1',created_utc:'2026-11-06',scope:'saved inventory',
 counts:{complete:true,dataset_count:2,row_count:4,original_accepted_count:2,native_csv_accepted_count:1},package_build_available:true,
 packages:[{id:'pack_0123456789abcdef',readable:true,title:'<script>原包</script>',created_utc:'2026-11-06',member_count:54,bytes:1000,sha256:'a'.repeat(64),description:'private snapshot'}],
 calibrations:[{id:'cal_bbbbbbbbbbbb',readable:true,report_sha256:'c'.repeat(64),selected_grades:['B23R075'],calibration_version:'fit_v2',H_axis:'physical_A_per_m',calibration_fit:{mean_rmse_T:.01},material_holdout:{mean_rmse_T:.04},bank_sha256:'b'.repeat(64)}],
 constraint_studies:[{id:'csens_ffffffffffff',readable:true,source_binding_sha256:'b'.repeat(64),dataset:'scan_aaaaaaaaaaaa',configuration_count:48,original_default_candidate_count:0}],
 calibrated_batches:[{id:'bhbatch_dddddddddddd',available:true,source_binding_sha256:'d'.repeat(64),result_count:1,item_count:2,measurement_protocol:'v8_2_cycle1'}],
 optimizations:[{id:'opt_eeeeeeeeeeee',readable:true,source_binding_sha256:'a'.repeat(64),dataset:'scan_aaaaaaaaaaaa',meets_limits_count:0,reference:'case_01'}],
 datasets:[{id:'v8_1',title:'original',version:'V8_1_original_saved',readable:true,source_hashes_sha256:'e'.repeat(64),measurement_protocol:'original_processed_metrics',row_count:2,original_accepted_count:1,data_usage:{kind:'historical_processed_estimate',native_csv_accepted_count:0,note:'historical estimated loss'}},
 {id:'scan_aaaaaaaaaaaa',title:'scan',readable:true,source_hashes_sha256:'f'.repeat(64),version:'V8_2_object_CS_loss_scope_v2',measurement_protocol:'v8_2_cycle1',row_count:2,original_accepted_count:1,data_usage:{kind:'per_row_official_source',native_csv_accepted_count:1,note:'official CSV'}}]};
class Node{
 constructor(tag){this.tag=tag;this.children=[];this.textContent='';this.disabled=false;this.listeners={};this.value=''}
 append(...nodes){this.children.push(...nodes)}replaceChildren(...nodes){this.children=nodes}addEventListener(name,fn){this.listeners[name]=fn}
 querySelectorAll(selector){return all(this).filter(node=>node.tag==='input'&&(selector!=='input:checked'||node.checked))}
 set innerHTML(_){throw Error('Untrusted data must use textContent')}
}
function all(node){return [node,...node.children.flatMap(all)]}
function text(node){return all(node).map(n=>n.textContent).join(' ')}
async function flush(){for(let i=0;i<12;i++)await Promise.resolve()}
(async()=>{
 const nodes=new Map(),calls=[];let handler=()=>structuredClone(fixture),buildHandler=()=>({id:'pack_1111111111111111',member_count:20,sha256:'1'.repeat(64),title:'new snapshot'});
 const document={getElementById:id=>{if(!nodes.has(id))nodes.set(id,new Node('root'));return nodes.get(id)},createElement:tag=>new Node(tag)};
 const context={document,fetch:async(url,options)=>{calls.push({url,options});return {ok:true,json:async()=>options.method==='POST'?buildHandler():handler()}},Number};
 context.window=context;vm.runInNewContext(script,context);await flush();
 const links=()=>[...nodes.values()].flatMap(all).filter(n=>n.tag==='a').map(n=>n.href);
 assert(links().includes('/api/workbench/system-data/packages/pack_0123456789abcdef/download'));
 assert(links().includes('/api/workbench/optimization/datasets/scan_aaaaaaaaaaaa/bundle'));
 assert(links().includes('/material-library?calibration=cal_bbbbbbbbbbbb'));
 assert.match(text(nodes.get('dataCalibrations')),/拟合平均RMSE 0.010000 T；整材料留出平均RMSE 0.040000/);
 assert.match(text(nodes.get('dataDatasets')),/历史处理 \/ 估算/);
 assert.match(text(nodes.get('dataPackages')),/<script>原包<\/script>/);
 assert.match(nodes.get('dataCounts').textContent,/2 数据集、4 保存记录、2 原判据通过、1 原生CSV通过/);
 await nodes.get('packageBuild').listeners.click();assert.match(nodes.get('packageBuildStatus').textContent,/至少选择/);assert.equal(calls.filter(call=>call.options.method==='POST').length,0);
 nodes.get('packageCalibration').value='cal_bbbbbbbbbbbb';nodes.get('packageBatch').value='bhbatch_dddddddddddd';nodes.get('packageOptimization').value='opt_eeeeeeeeeeee';nodes.get('packageStudy').value='csens_ffffffffffff';nodes.get('packageDatasets').querySelectorAll('input')[1].checked=true;
 await nodes.get('packageBuild').listeners.click();const write=calls.find(call=>call.options.method==='POST');assert.equal(write.url,'/api/workbench/system-data/packages');
 assert.deepEqual(JSON.parse(write.options.body),{calibration:{id:'cal_bbbbbbbbbbbb',report_sha256:'c'.repeat(64)},batch:{id:'bhbatch_dddddddddddd',source_binding_sha256:'d'.repeat(64)},datasets:[{id:'scan_aaaaaaaaaaaa',source_hashes_sha256:'f'.repeat(64)}],optimization:{id:'opt_eeeeeeeeeeee',source_binding_sha256:'a'.repeat(64)},constraint_study:{id:'csens_ffffffffffff',source_binding_sha256:'b'.repeat(64)}});
 assert.match(nodes.get('optimizationRecordStatus').textContent,/1\/1.*无改善候选/);
 assert(links().includes('/api/workbench/system-data/packages/pack_1111111111111111/download'));assert.match(nodes.get('packageBuildStatus').textContent,/已保存 pack_1111111111111111/);
 let completeBuild;buildHandler=()=>new Promise(resolve=>completeBuild=resolve);const building=nodes.get('packageBuild').listeners.click();await flush();const beforePending=calls.length;await nodes.get('packageBuild').listeners.click();await nodes.get('dataRefresh').listeners.click();assert.equal(calls.length,beforePending);assert.equal(nodes.get('dataRefresh').disabled,true);completeBuild({id:'pack_2222222222222222',title:'second',member_count:2,sha256:'2'.repeat(64)});await building;
 buildHandler=()=>{throw Error('response lost')};await nodes.get('packageBuild').listeners.click();assert.match(nodes.get('packageBuildStatus').textContent,/response lost.*不自动重复生成/);assert.equal(nodes.get('packageBuildResult').children.length,0);
 handler=()=>({...structuredClone(fixture),packages:[{id:'pack_0123456789abcdef',readable:false,error:'changed bytes'}],datasets:[{id:'scan_aaaaaaaaaaaa',readable:false,error:'broken source'}],counts:{...fixture.counts,complete:false,dataset_count:0,row_count:0,original_accepted_count:0,native_csv_accepted_count:0}});
 await nodes.get('dataRefresh').listeners.click();assert.match(text(nodes.get('dataPackages')),/changed bytes/);assert.match(text(nodes.get('dataDatasets')),/broken source/);
 assert(!links().some(link=>link.includes('/packages/')||link.includes('/datasets/')));assert.match(nodes.get('dataCounts').textContent,/可读取部分/);
 handler=()=>{throw Error('unavailable')};await nodes.get('dataRefresh').listeners.click();assert.match(nodes.get('dataStatus').textContent,/unavailable/);assert.equal(nodes.get('dataCounts').textContent,'');assert.equal(links().length,0);
 let finish;handler=()=>new Promise(resolve=>finish=resolve);const pending=nodes.get('dataRefresh').listeners.click();await flush();const count=calls.length;await nodes.get('dataRefresh').listeners.click();assert.equal(calls.length,count);assert.equal(nodes.get('dataRefresh').disabled,true);finish(structuredClone(fixture));await pending;assert.equal(nodes.get('dataRefresh').disabled,false);
 assert(calls.every(call=>call.url==='/api/workbench/system-data'&&call.options.method==='GET'&&call.options.cache==='no-store'||call.url==='/api/workbench/system-data/packages'&&call.options.method==='POST'));
 console.log('System data: version-bound links, separate fit/holdout and estimate/native counts, source-bound explicit builds, no duplicate or automatic retries, safe text and refresh recovery: PASS');
})().catch(error=>{console.error(error);process.exitCode=1});
