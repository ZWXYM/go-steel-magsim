/* Exact saved dataset links, immutable package downloads and failed refreshes. */
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const script=fs.readFileSync(path.join(__dirname,'../static/js/system_data.js'),'utf8');
const {validDataset,validCal,validPack}=require('../static/js/system_data.js');
assert(validDataset('scan_aaaaaaaaaaaa'));assert(validDataset('v8_2_bound_archive'));assert(!validDataset('../v8_1'));assert(!validCal('javascript:bad'));assert(validPack('pack_0123456789abcdef'));
const fixture={schema:'go_steel_system_data_v1',created_utc:'2026-11-06',scope:'saved inventory',
 counts:{complete:true,dataset_count:2,row_count:4,original_accepted_count:2,native_csv_accepted_count:1},package_build_available:true,
 packages:[{id:'pack_0123456789abcdef',readable:true,title:'<script>原包</script>',created_utc:'2026-11-06',member_count:54,bytes:1000,sha256:'a'.repeat(64),description:'private snapshot'},
 {id:'pack_2468ace02468ace0',readable:true,title:'频率留出复核',created_utc:'2026-11-12',member_count:4,bytes:2000,sha256:'e'.repeat(64),description:'同试样表内检验',source_filename:'frequency_loss_validation.zip'}],
 calibrations:[{id:'cal_bbbbbbbbbbbb',readable:true,report_sha256:'c'.repeat(64),selected_grades:['B23R075'],calibration_version:'fit_v2',H_axis:'physical_A_per_m',calibration_fit:{mean_rmse_T:.01},material_holdout:{mean_rmse_T:.04},bank_sha256:'b'.repeat(64)}],
 saved_predictions:[{id:'pred_123456abcdef',calibration_id:'cal_bbbbbbbbbbbb',readable:true,source_binding_sha256:'9'.repeat(64),material_name:'B30 heldout',excluded_grades:['B30P105']}],
 constraint_studies:[{id:'csens_ffffffffffff',readable:true,source_binding_sha256:'b'.repeat(64),dataset:'scan_aaaaaaaaaaaa',configuration_count:48,original_default_candidate_count:0}],
 calibrated_batches:[{id:'bhbatch_dddddddddddd',available:true,source_binding_sha256:'d'.repeat(64),result_count:1,item_count:2,measurement_protocol:'v8_2_cycle1'},
 {id:'bhbatch_111111111111',available:true,source_binding_sha256:'1'.repeat(64),result_count:1,item_count:1,measurement_protocol:'fine101'},
 {id:'bhbatch_222222222222',available:true,source_binding_sha256:'2'.repeat(64),result_count:1,item_count:1,measurement_protocol:'other'},
 {id:'bhbatch_333333333333',available:true,source_binding_sha256:'3'.repeat(64),result_count:1,item_count:1,measurement_protocol:'other'}],
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
 const nodes=new Map(),calls=[];let handler=()=>structuredClone(fixture),buildHandler=()=>({id:'pack_1111111111111111',member_count:20,sha256:'1'.repeat(64),title:'new snapshot'}),compareHandler;
 const document={getElementById:id=>{if(!nodes.has(id))nodes.set(id,new Node('root'));return nodes.get(id)},createElement:tag=>new Node(tag)};
 const context={document,fetch:async(url,options)=>{calls.push({url,options});return {ok:true,json:async()=>options.method==='POST'?buildHandler():url.includes('/calibration-comparison?')?compareHandler():handler()}},Number};
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
 assert(links().includes('/frequency-loss-validation/pack_2468ace02468ace0'));
 assert.equal(nodes.get('packagePredictions').querySelectorAll('input')[0].checked,false);
 nodes.get('packagePredictions').querySelectorAll('input')[0].checked=true;
 await nodes.get('dataRefresh').listeners.click();assert.equal(nodes.get('packagePredictions').querySelectorAll('input')[0].checked,true);
 await nodes.get('packageBuild').listeners.click();const withPredictions=JSON.parse(calls.filter(call=>call.options.method==='POST').at(-1).options.body);
 assert.deepEqual(withPredictions.predictions,[{id:'pred_123456abcdef',calibration_id:'cal_bbbbbbbbbbbb',source_binding_sha256:'9'.repeat(64)}]);
 const extra=()=>nodes.get('packageMoreBatches').querySelectorAll('input');extra()[0].checked=true;
 const writesBeforeDuplicate=calls.filter(call=>call.options.method==='POST').length;await nodes.get('packageBuild').listeners.click();assert.match(nodes.get('packageBuildStatus').textContent,/不能重复选择/);assert.equal(calls.filter(call=>call.options.method==='POST').length,writesBeforeDuplicate);
 extra().forEach(input=>input.checked=true);await nodes.get('packageBuild').listeners.click();assert.match(nodes.get('packageBuildStatus').textContent,/超过3份/);assert.equal(calls.filter(call=>call.options.method==='POST').length,writesBeforeDuplicate);
 extra().forEach(input=>input.checked=false);extra()[1].checked=true;
 await nodes.get('dataRefresh').listeners.click();assert.equal(extra()[1].checked,true);
 await nodes.get('packageBuild').listeners.click();const multi=JSON.parse(calls.filter(call=>call.options.method==='POST').at(-1).options.body);
 assert.deepEqual(multi.additional_batches,[{id:'bhbatch_111111111111',source_binding_sha256:'1'.repeat(64)}]);assert.equal(multi.batch.id,'bhbatch_dddddddddddd');
 for(const id of ['packageCalibration','packageBatch','packageOptimization','packageStudy'])nodes.get(id).value='';
 for(const id of ['packagePredictions','packageDatasets'])nodes.get(id).querySelectorAll('input').forEach(input=>input.checked=false);
 await nodes.get('packageBuild').listeners.click();const onlyExtra=JSON.parse(calls.filter(call=>call.options.method==='POST').at(-1).options.body);assert.equal(onlyExtra.batch,null);assert.equal(onlyExtra.additional_batches.length,1);assert.equal(onlyExtra.calibration,null);
 extra().forEach(input=>input.checked=false);
 const refs=()=>nodes.get('packageReferenceEvidence').querySelectorAll('input');assert.equal(refs().length,1);refs()[0].checked=true;
 await nodes.get('dataRefresh').listeners.click();assert.equal(refs()[0].checked,true);
 await nodes.get('packageBuild').listeners.click();const onlyReference=JSON.parse(calls.filter(call=>call.options.method==='POST').at(-1).options.body);
 assert.deepEqual(onlyReference,{calibration:null,batch:null,datasets:[],optimization:null,constraint_study:null,reference_evidence:[{id:'pack_2468ace02468ace0',sha256:'e'.repeat(64)}]});
 let completeBuild;buildHandler=()=>new Promise(resolve=>completeBuild=resolve);const building=nodes.get('packageBuild').listeners.click();await flush();const beforePending=calls.length;await nodes.get('packageBuild').listeners.click();await nodes.get('dataRefresh').listeners.click();assert.equal(calls.length,beforePending);assert.equal(nodes.get('dataRefresh').disabled,true);completeBuild({id:'pack_2222222222222222',title:'second',member_count:2,sha256:'2'.repeat(64)});await building;
 buildHandler=()=>{throw Error('response lost')};await nodes.get('packageBuild').listeners.click();assert.match(nodes.get('packageBuildStatus').textContent,/response lost.*不自动重复生成/);assert.equal(nodes.get('packageBuildResult').children.length,0);
 handler=()=>({...structuredClone(fixture),packages:[{id:'pack_0123456789abcdef',readable:false,error:'changed bytes'}],datasets:[{id:'scan_aaaaaaaaaaaa',readable:false,error:'broken source'}],counts:{...fixture.counts,complete:false,dataset_count:0,row_count:0,original_accepted_count:0,native_csv_accepted_count:0}});
 await nodes.get('dataRefresh').listeners.click();assert.match(text(nodes.get('dataPackages')),/changed bytes/);assert.match(text(nodes.get('dataDatasets')),/broken source/);
 assert(!links().some(link=>link.includes('/packages/')||link.includes('/datasets/')));assert.match(nodes.get('dataCounts').textContent,/可读取部分/);
 handler=()=>{throw Error('unavailable')};await nodes.get('dataRefresh').listeners.click();assert.match(nodes.get('dataStatus').textContent,/unavailable/);assert.equal(nodes.get('dataCounts').textContent,'');assert.equal(links().length,0);
 let finish;handler=()=>new Promise(resolve=>finish=resolve);const pending=nodes.get('dataRefresh').listeners.click();await flush();const count=calls.length;await nodes.get('dataRefresh').listeners.click();assert.equal(calls.length,count);assert.equal(nodes.get('dataRefresh').disabled,true);finish(structuredClone(fixture));await pending;assert.equal(nodes.get('dataRefresh').disabled,false);
 assert(calls.every(call=>call.url==='/api/workbench/system-data'&&call.options.method==='GET'&&call.options.cache==='no-store'||call.url==='/api/workbench/system-data/packages'&&call.options.method==='POST'));
 const first=fixture.calibrations[0],second={...first,id:'cal_cccccccccccc',report_sha256:'d'.repeat(64),calibration_version:'fit_v3'};
 handler=()=>({...structuredClone(fixture),calibrations:[first,second]});await nodes.get('dataRefresh').listeners.click();
 nodes.get('compareBaseline').value=first.id;nodes.get('compareCandidate').value=second.id;nodes.get('compareCandidate').listeners.change();
 const payload={version:'saved_calibration_comparison_v1',baseline:{...first,material_holdout_mean_rmse_T:.04,material_holdout_max_abs_B800_error_T:.1},candidate:{...second,material_holdout_mean_rmse_T:.03,material_holdout_max_abs_B800_error_T:.06},improved_holdout_directions:1,worsened_holdout_directions:0,rows:[{grade:'B23R075',direction:'RD',holdout_delta_rmse_T:-.01}]};
 compareHandler=()=>payload;await nodes.get('compareCalibrations').listeners.click();
 assert.match(nodes.get('calibrationComparisonStatus').textContent,/0.040000 → 0.030000/);
 assert.match(text(nodes.get('calibrationComparisonRows')),/-0.010000/);
 assert(links().some(href=>href.includes(first.report_sha256)&&href.endsWith('&format=zip')));
 let finishCompare;compareHandler=()=>new Promise(resolve=>finishCompare=resolve);const earlier=nodes.get('compareCalibrations').listeners.click();await flush();
 nodes.get('compareCandidate').value='';nodes.get('compareCandidate').listeners.change();finishCompare(payload);await earlier;
 assert.equal(nodes.get('calibrationComparisonResults').hidden,true);
 assert(calls.filter(call=>call.url.includes('/calibration-comparison?')).every(call=>call.options.method==='GET'));
 console.log('System data: version-bound links, separate fit/holdout and estimate/native counts, source-bound explicit builds, no duplicate or automatic retries, safe text and refresh recovery: PASS');
})().catch(error=>{console.error(error);process.exitCode=1});
