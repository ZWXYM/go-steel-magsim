/* Inventory and exact downloads only. No new plans, predictions or native work. */
(function(root){
 'use strict';
 const validDataset=id=>/^(v8_1|v8_2_archive|v8_2_bound_archive|scan_[a-f0-9]{12})$/.test(id),validCal=id=>/^cal_[a-f0-9]{12}$/.test(id),validPack=id=>/^pack_[a-f0-9]{16}$/.test(id);
 const numeric=value=>Number.isFinite(value)?value.toFixed(6):'未记录';
 const predictionKey=row=>row.calibration_id+'/'+row.id;
 const packageViews=new Map([
  ['external_bh_validation.zip',['查看原表核对','/external-bh-validation/']],
  ['measured_loop_loss.zip',['查看回线核算','/measured-loop-loss/']],
  ['frequency_loss_validation.zip',['查看多频复核','/frequency-loss-validation/']],
 ]);
 const referencePackages=rows=>rows.filter(row=>row.readable&&validPack(row.id)&&/^[a-f0-9]{64}$/.test(row.sha256)&&packageViews.has(row.source_filename));
 if(typeof module==='object'&&module.exports)module.exports={validDataset,validCal,validPack,numeric};
 if(!root.document)return;
 const $=id=>root.document.getElementById(id);
 function element(tag,text,className){const node=root.document.createElement(tag);if(text!==undefined)node.textContent=String(text);if(className)node.className=className;return node}
 function link(text,href){const node=element('a',text,'download');node.href=href;return node}
 function empty(node,text){node.replaceChildren(element('li',text,'muted'))}
 function packages(rows){
  const list=$('dataPackages');list.replaceChildren();if(!rows.length){empty(list,'尚未注册完整工作包；下方已保存数据集仍可直接下载。');return}
  for(const row of rows){
   const item=element('li');list.append(item);
   if(!row.readable||!validPack(row.id)){item.append(element('strong',row.id),element('p','数据包暂不可读取：'+(row.error||'编号不符'),'error'));continue}
   item.append(element('strong',row.title),element('small',row.created_utc+' · '+row.member_count+' 文件 · '+row.bytes+' 字节 · 冻结快照'));
   item.append(element('p',row.description),element('span','SHA-256：'+row.sha256,'sha'));
   item.append(link('下载完整工作包','/api/workbench/system-data/packages/'+row.id+'/download'));
   const view=packageViews.get(row.source_filename);
   if(view)item.append(link(view[0],view[1]+row.id));
  }
 }
 function calibrations(rows){
  const list=$('dataCalibrations');list.replaceChildren();if(!rows.length){empty(list,'暂无保存校准，可从材料校准页建立或加载。');return}
  for(const row of rows){
   const item=element('li');list.append(item);
   if(!row.readable||!validCal(row.id)){item.append(element('strong',row.id),element('p',row.error||'记录编号不符','error'));continue}
   item.append(element('strong',row.id+' · '+row.selected_grades.join('、')),element('small',row.calibration_version+' · H：'+row.H_axis));
   item.append(element('p','拟合平均RMSE '+numeric(row.calibration_fit?.mean_rmse_T)+' T；整材料留出平均RMSE '+numeric(row.material_holdout?.mean_rmse_T)+' T。'));
   item.append(element('span','校准库 SHA-256：'+row.bank_sha256,'sha'));
   item.append(link('查看材料与来源','/material-library?calibration='+row.id),link('下载原报告','/api/workbench/files/calibration/'+row.id+'/report.json'));
  }
 }
 let compareGeneration=0, comparing=false;
 function compareSelection(){
  const reports=catalog?.calibrations||[];
  return ['compareBaseline','compareCandidate'].map(id=>reports.find(row=>row.id===$(id).value&&row.readable));
 }
 function compareControls(){
  if(!$('compareCalibrations'))return;
  const [a,b]=compareSelection();
  $('compareCalibrations').disabled=busy||comparing||!a||!b||a.id===b.id;
 }
 function clearComparison(){
  if(!$('compareCalibrations'))return;
  compareGeneration++;comparing=false;
  $('calibrationComparisonStatus').textContent='';$('calibrationComparisonResults').hidden=true;
  $('calibrationComparisonLinks').replaceChildren();$('calibrationComparisonRows').replaceChildren();compareControls();
 }
 function comparisonOptions(rows){
  if(!$('compareCalibrations'))return;
  const reports=rows.filter(row=>row.readable&&validCal(row.id)&&/^[a-f0-9]{64}$/.test(row.report_sha256));
  for(const id of ['compareBaseline','compareCandidate']){
   const select=$(id),previous=select.value,blank=element('option','请选择');blank.value='';select.replaceChildren(blank);
   for(const row of reports){const option=element('option',(row.display_label||row.calibration_version)+' · '+row.id);option.value=row.id;select.append(option)}
   select.value=reports.some(row=>row.id===previous)?previous:'';select.disabled=reports.length<2;
  }
  clearComparison();
 }
 async function compareSavedCalibrations(){
  if(comparing||$('compareCalibrations').disabled)return;
  const [a,b]=compareSelection();if(!a||!b||a.id===b.id)return;
  clearComparison();comparing=true;compareControls();const generation=compareGeneration;
  const url='/api/workbench/system-data/calibration-comparison?'+Object.entries({baseline:a.id,candidate:b.id,baseline_sha256:a.report_sha256,candidate_sha256:b.report_sha256}).map(([key,value])=>key+'='+encodeURIComponent(value)).join('&');
  $('calibrationComparisonStatus').textContent='正在核对同源曲线与整牌号留出记录…';
  try{
   const response=await fetch(url,{method:'GET',cache:'no-store'}),result=await response.json();
   if(generation!==compareGeneration)return;
   if(!response.ok)throw Error(result.error||'比较失败');
   if(result.version!=='saved_calibration_comparison_v1'||result.baseline?.id!==a.id||result.candidate?.id!==b.id||result.baseline.report_sha256!==a.report_sha256||result.candidate.report_sha256!==b.report_sha256||!Array.isArray(result.rows))throw Error('比较响应与所选报告不符');
   const rows=$('calibrationComparisonRows');
   for(const row of result.rows){const tr=element('tr');tr.append(element('td',row.grade+' / '+row.direction));
    for(const key of ['fit_baseline_rmse_T','fit_candidate_rmse_T','holdout_baseline_rmse_T','holdout_candidate_rmse_T','holdout_delta_rmse_T','holdout_baseline_B800_error_T','holdout_candidate_B800_error_T'])tr.append(element('td',numeric(row[key])));
    rows.append(tr);
   }
   $('calibrationComparisonStatus').textContent=`整牌号留出平均 RMSE ${numeric(result.baseline.material_holdout_mean_rmse_T)} → ${numeric(result.candidate.material_holdout_mean_rmse_T)} T。${result.improved_holdout_directions} 个方向改善，${result.worsened_holdout_directions} 个方向增大。最大 |B800 误差| ${numeric(result.baseline.material_holdout_max_abs_B800_error_T)} → ${numeric(result.candidate.material_holdout_max_abs_B800_error_T)} T。`;
   $('calibrationComparisonResults').hidden=false;
   $('calibrationComparisonLinks').append(link('下载逐方向 CSV',url+'&format=csv'),link('下载对照及原报告',url+'&format=zip'));
  }catch(error){if(generation===compareGeneration)$('calibrationComparisonStatus').textContent=error.message}
  finally{if(generation===compareGeneration){comparing=false;compareControls()}}
 }
 function datasets(rows){
  const body=$('dataDatasets');body.replaceChildren();
  if(!rows.length){const tr=element('tr'),td=element('td','暂无保存电机数据。');td.colSpan=5;tr.append(td);body.append(tr);return}
  for(const row of rows){
   const tr=element('tr'),name=element('td');body.append(tr);name.append(element('strong',row.title||row.id),element('small',row.id));tr.append(name);
   if(!row.readable||!validDataset(row.id)){const td=element('td',row.error||'数据集编号不符','error');td.colSpan=4;tr.append(td);continue}
   name.append(element('small',row.version));tr.append(element('td',row.measurement_protocol),element('td',row.row_count+' / '+row.original_accepted_count+' / '+row.data_usage.native_csv_accepted_count));
   const usage=element('td',row.data_usage.kind==='historical_processed_estimate'?'历史处理 / 估算':'逐行官方来源检查');usage.append(element('small',row.data_usage.note));tr.append(usage);
   const files=element('td');files.append(link('下载数据集','/api/workbench/optimization/datasets/'+row.id+'/bundle'));tr.append(files);
  }
 }
 let busy=false,building=false,catalog=null;
 function formDisabled(disabled){for(const id of ['packageCalibration','packageBatch','packageMoreBatches','packageOptimization','packageStudy','packageDatasets','packagePredictions','packageReferenceEvidence','packageBuild'])if($(id))$(id).disabled=disabled}
 function buildOptions(data){
  const previousCal=$('packageCalibration').value,previousBatch=$('packageBatch').value,previousOptimization=$('packageOptimization').value;
  const checked=new Set(Array.from($('packageDatasets').querySelectorAll('input:checked')).map(input=>input.value));
  const hadOptions=$('packageDatasets').querySelectorAll('input').length>0;
  const selectedPredictions=new Set(Array.from($('packagePredictions').querySelectorAll('input:checked')).map(input=>input.value));
  const moreBatches=$('packageMoreBatches'),checkedBatches=new Set(moreBatches?Array.from(moreBatches.querySelectorAll('input:checked')).map(input=>input.value):[]);
  const references=$('packageReferenceEvidence'),checkedReferences=new Set(references?Array.from(references.querySelectorAll('input:checked')).map(input=>input.value):[]);
  $('packageCalibration').replaceChildren(element('option','不包含校准'));$('packageCalibration').children[0].value='';
  for(const row of data.calibrations.filter(row=>row.readable&&validCal(row.id))){const option=element('option',row.id+' · '+(row.display_label?row.display_label+' · ':'')+row.selected_grades.join('、'));option.value=row.id;$('packageCalibration').append(option)}
  $('packageCalibration').value=data.calibrations.some(row=>row.readable&&row.id===previousCal)?previousCal:'';
  $('packageBatch').replaceChildren(element('option','不包含批量'));$('packageBatch').children[0].value='';
  for(const row of (data.calibrated_batches||[]).filter(row=>row.available&&/^bhbatch_[a-f0-9]{12}$/.test(row.id))){const option=element('option',row.id+' · '+row.measurement_protocol+' · '+row.result_count+'/'+row.item_count+'结果 · 校准'+(row.source_calibration_ids?.join('、')||'未记录'));option.value=row.id;$('packageBatch').append(option)}
  $('packageBatch').value=(data.calibrated_batches||[]).some(row=>row.available&&row.id===previousBatch)?previousBatch:'';
  if(moreBatches){moreBatches.replaceChildren(element('legend','另外收集的已完成电机批量（最多3份）'));
   for(const row of (data.calibrated_batches||[]).filter(row=>row.available&&/^bhbatch_[a-f0-9]{12}$/.test(row.id))){const label=element('label'),input=element('input');input.type='checkbox';input.value=row.id;input.checked=checkedBatches.has(row.id);label.append(input,element('span',' '+row.id+' · '+row.measurement_protocol+' · '+row.result_count+'/'+row.item_count+'结果'));moreBatches.append(label,element('br'))}}
  const optimizations=(data.optimizations||[]),readable=optimizations.filter(row=>row.readable&&/^opt_[a-f0-9]{12}$/.test(row.id));
  $('packageOptimization').replaceChildren(element('option','不包含原筛选'));$('packageOptimization').children[0].value='';
  for(const row of readable){const option=element('option',row.id+' · '+row.dataset+' · '+row.meets_limits_count+'案满足 · 基准'+row.reference);option.value=row.id;$('packageOptimization').append(option)}
  $('packageOptimization').value=readable.some(row=>row.id===previousOptimization)?previousOptimization:'';
  const previousStudy=$('packageStudy').value,studies=(data.constraint_studies||[]).filter(row=>row.readable&&/^csens_[a-f0-9]{12}$/.test(row.id));
  $('packageStudy').replaceChildren(element('option','不包含约束研究'));$('packageStudy').children[0].value='';
  for(const row of studies){const option=element('option',row.id+' · '+row.dataset+' · '+row.configuration_count+'组 · 原默认'+row.original_default_candidate_count+'候选');option.value=row.id;$('packageStudy').append(option)}
  $('packageStudy').value=studies.some(row=>row.id===previousStudy)?previousStudy:'';
  $('optimizationRecordStatus').textContent='已有原筛选 '+readable.length+'/'+optimizations.length+' 份可读取。保留原基准、权重、约束、数据口径和无改善候选；不重新评分。'+(readable.length<optimizations.length?' 其余记录文件待检查。':'');
  $('packageDatasets').replaceChildren(element('legend','电机数据集（至多16份）'));
  for(const row of data.datasets.filter(row=>row.readable&&validDataset(row.id))){const label=element('label'),input=element('input');input.type='checkbox';input.value=row.id;input.checked=hadOptions?checked.has(row.id):false;label.append(input,element('span',' '+row.title+' '));$('packageDatasets').append(label,element('br'))}
  $('packagePredictions').replaceChildren(element('legend','已保存的材料预测（至多16份）'));
  for(const row of (data.saved_predictions||[]).filter(row=>row.readable&&validCal(row.calibration_id)&&/^pred_[a-f0-9]{12}$/.test(row.id))){const label=element('label'),input=element('input');input.type='checkbox';input.value=predictionKey(row);input.checked=selectedPredictions.has(input.value);label.append(input,element('span',' '+row.material_name+' · '+row.calibration_id+' · 排除'+(row.excluded_grades?.join('、')||'无')));$('packagePredictions').append(label,element('br'))}
  if(references){
   const choices=referencePackages(data.packages);references.replaceChildren(element('legend','已保存曲线与损耗核对（最多12份）'));
   for(const row of choices){const label=element('label'),input=element('input');input.type='checkbox';input.value=row.id;input.checked=checkedReferences.has(row.id);label.append(input,element('span',' '+row.title+' · '+row.id));references.append(label,element('br'))}
   if(!choices.length)references.append(element('p','保存原表核对、回线或多频复核后可选择。','muted'));
  }
  formDisabled(!data.package_build_available);
 }
 async function build(){
  if(busy||building||!catalog)return;
  const cal=catalog.calibrations.find(row=>row.id===$('packageCalibration').value&&row.readable);
  const batch=(catalog.calibrated_batches||[]).find(row=>row.id===$('packageBatch').value&&row.available);
  const extraIds=$('packageMoreBatches')?Array.from($('packageMoreBatches').querySelectorAll('input:checked')).map(input=>input.value):[];
  const extraBatches=(catalog.calibrated_batches||[]).filter(row=>row.available&&extraIds.includes(row.id));
  if(extraBatches.length!==extraIds.length||extraIds.length>3){$('packageBuildStatus').textContent='另外选择的批量已变化或超过3份，请刷新后重新选择。';return}
  if(batch&&extraIds.includes(batch.id)){$('packageBuildStatus').textContent='同一电机批量不能重复选择，请取消主批量或其他批量中的重复项。';return}
  const optimization=(catalog.optimizations||[]).find(row=>row.id===$('packageOptimization').value&&row.readable);
  const study=(catalog.constraint_studies||[]).find(row=>row.id===$('packageStudy').value&&row.readable);
  const ids=Array.from($('packageDatasets').querySelectorAll('input:checked')).map(input=>input.value);
  const predKeys=Array.from($('packagePredictions').querySelectorAll('input:checked')).map(input=>input.value);
  const predictions=(catalog.saved_predictions||[]).filter(row=>row.readable&&predKeys.includes(predictionKey(row)));
  if(predictions.length!==predKeys.length||predictions.length>16){$('packageBuildStatus').textContent='保存预测选择已变化或超过16份，请刷新后重新选择。';return}
  const references=$('packageReferenceEvidence'),referenceIds=references?Array.from(references.querySelectorAll('input:checked')).map(input=>input.value):[];
  const referenceEvidence=referencePackages(catalog.packages).filter(row=>referenceIds.includes(row.id));
  if(referenceEvidence.length!==referenceIds.length||referenceIds.length>12){$('packageBuildStatus').textContent='曲线与损耗记录已变化或超过12份，请刷新后重新选择。';return}
  if(!cal&&!batch&&!extraBatches.length&&!optimization&&!study&&!ids.length&&!predictions.length&&!referenceEvidence.length){$('packageBuildStatus').textContent='请至少选择一项已有数据。';return}
  if(ids.length>16){$('packageBuildStatus').textContent='每包最多16份电机数据集，请分开选择。';return}
  const selected=catalog.datasets.filter(row=>ids.includes(row.id)&&row.readable);
  if(selected.length!==ids.length){$('packageBuildStatus').textContent='所选记录已变化，请刷新后重新选择。';return}
  const payload={calibration:cal?{id:cal.id,report_sha256:cal.report_sha256}:null,
   datasets:selected.map(row=>({id:row.id,source_hashes_sha256:row.source_hashes_sha256})),
   batch:batch?{id:batch.id,source_binding_sha256:batch.source_binding_sha256}:null,
   optimization:optimization?{id:optimization.id,source_binding_sha256:optimization.source_binding_sha256}:null,
   constraint_study:study?{id:study.id,source_binding_sha256:study.source_binding_sha256}:null};
  if(predictions.length)payload.predictions=predictions.map(row=>({calibration_id:row.calibration_id,id:row.id,source_binding_sha256:row.source_binding_sha256}));
  if(referenceEvidence.length)payload.reference_evidence=referenceEvidence.map(row=>({id:row.id,sha256:row.sha256}));
  if(extraBatches.length)payload.additional_batches=extraBatches.map(row=>({id:row.id,source_binding_sha256:row.source_binding_sha256}));
  building=true;formDisabled(true);$('dataRefresh').disabled=true;$('packageBuildResult').replaceChildren();$('packageBuildStatus').textContent='正在收集所选原文件和核对来源，请等待…';
  try{
   const response=await fetch('/api/workbench/system-data/packages',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}),record=await response.json();if(!response.ok)throw Error(record.error||'生成失败');
   if(!validPack(record.id))throw Error('生成的数据包编号不符');
   $('packageBuildResult').append(link('下载本次工作包','/api/workbench/system-data/packages/'+record.id+'/download'));
   $('packageBuildStatus').textContent='已保存 '+record.id+'，'+record.member_count+' 文件。SHA-256：'+record.sha256+'。旧数据和原包保持。';
   const rows=[record,...catalog.packages.filter(row=>row.id!==record.id)];catalog.packages=rows;packages(rows.map(row=>({...row,readable:row.readable!==false})));
  }catch(error){$('packageBuildStatus').textContent='未确认生成：'+error.message+'。可先刷新完整工作包目录检查，不自动重复生成。'}
  finally{building=false;formDisabled(!catalog.package_build_available);$('dataRefresh').disabled=false}
 }
 async function refresh(){
  if(busy||building)return;busy=true;clearComparison();formDisabled(true);$('dataRefresh').disabled=true;$('dataStatus').textContent='正在读取…';$('dataCounts').textContent='';
  for(const id of ['dataPackages','dataCalibrations','dataDatasets'])$(id).replaceChildren();
  try{
   const response=await fetch('/api/workbench/system-data',{method:'GET',cache:'no-store'}),data=await response.json();if(!response.ok)throw Error(data.error||'读取失败');
   if(data.schema!=='go_steel_system_data_v1'||!['packages','calibrations','datasets'].every(key=>Array.isArray(data[key])))throw Error('数据清单格式不符');
   packages(data.packages);calibrations(data.calibrations);datasets(data.datasets);
   buildOptions(data);catalog=data;comparisonOptions(data.calibrations);
   const counts=data.counts;$('dataCounts').textContent=(counts.complete?'当前':'可读取部分')+'：'+counts.dataset_count+' 数据集、'+counts.row_count+' 保存记录、'+counts.original_accepted_count+' 原判据通过、'+counts.native_csv_accepted_count+' 原生CSV通过。记录包含重复材料及不同版本。';
   $('dataStatus').textContent='读取时间：'+data.created_utc+'。'+data.scope;
  }catch(error){catalog=null;formDisabled(true);comparisonOptions([]);for(const id of ['dataPackages','dataCalibrations','dataDatasets','packageMoreBatches'])if($(id))$(id).replaceChildren();$('dataCounts').textContent='';$('dataStatus').textContent='暂不可读取：'+error.message}
  finally{busy=false;$('dataRefresh').disabled=false;compareControls()}
 }
 $('dataRefresh').addEventListener('click',refresh);$('packageBuild').addEventListener('click',build);
 if($('compareCalibrations')){
  $('compareCalibrations').addEventListener('click',compareSavedCalibrations);
  $('compareBaseline').addEventListener('change',clearComparison);$('compareCandidate').addEventListener('change',clearComparison);
 }
 refresh();
})(typeof window==='object'?window:globalThis);
