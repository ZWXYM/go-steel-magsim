/* Inventory and exact downloads only. No new plans, predictions or native work. */
(function(root){
 'use strict';
 const validDataset=id=>/^(v8_1|v8_2_archive|v8_2_bound_archive|scan_[a-f0-9]{12})$/.test(id),validCal=id=>/^cal_[a-f0-9]{12}$/.test(id),validPack=id=>/^pack_[a-f0-9]{16}$/.test(id);
 const numeric=value=>Number.isFinite(value)?value.toFixed(6):'未记录';
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
 function formDisabled(disabled){for(const id of ['packageCalibration','packageBatch','packageOptimization','packageDatasets','packageBuild'])$(id).disabled=disabled}
 function buildOptions(data){
  const previousCal=$('packageCalibration').value,previousBatch=$('packageBatch').value,previousOptimization=$('packageOptimization').value;
  const checked=new Set(Array.from($('packageDatasets').querySelectorAll('input:checked')).map(input=>input.value));
  const hadOptions=$('packageDatasets').querySelectorAll('input').length>0;
  $('packageCalibration').replaceChildren(element('option','不包含校准'));$('packageCalibration').children[0].value='';
  for(const row of data.calibrations.filter(row=>row.readable&&validCal(row.id))){const option=element('option',row.id+' · '+row.selected_grades.join('、'));option.value=row.id;$('packageCalibration').append(option)}
  $('packageCalibration').value=data.calibrations.some(row=>row.readable&&row.id===previousCal)?previousCal:'';
  $('packageBatch').replaceChildren(element('option','不包含批量'));$('packageBatch').children[0].value='';
  for(const row of (data.calibrated_batches||[]).filter(row=>row.available&&/^bhbatch_[a-f0-9]{12}$/.test(row.id))){const option=element('option',row.id+' · '+row.measurement_protocol+' · '+row.result_count+'/'+row.item_count+'结果');option.value=row.id;$('packageBatch').append(option)}
  $('packageBatch').value=(data.calibrated_batches||[]).some(row=>row.available&&row.id===previousBatch)?previousBatch:'';
  const optimizations=(data.optimizations||[]),readable=optimizations.filter(row=>row.readable&&/^opt_[a-f0-9]{12}$/.test(row.id));
  $('packageOptimization').replaceChildren(element('option','不包含原筛选'));$('packageOptimization').children[0].value='';
  for(const row of readable){const option=element('option',row.id+' · '+row.dataset+' · '+row.meets_limits_count+'案满足 · 基准'+row.reference);option.value=row.id;$('packageOptimization').append(option)}
  $('packageOptimization').value=readable.some(row=>row.id===previousOptimization)?previousOptimization:'';
  $('optimizationRecordStatus').textContent='已有原筛选 '+readable.length+'/'+optimizations.length+' 份可读取。保留原基准、权重、约束、数据口径和无改善候选；不重新评分。'+(readable.length<optimizations.length?' 其余记录文件待检查。':'');
  $('packageDatasets').replaceChildren(element('legend','电机数据集（至多16份）'));
  for(const row of data.datasets.filter(row=>row.readable&&validDataset(row.id))){const label=element('label'),input=element('input');input.type='checkbox';input.value=row.id;input.checked=hadOptions?checked.has(row.id):false;label.append(input,element('span',' '+row.title+' '));$('packageDatasets').append(label,element('br'))}
  formDisabled(!data.package_build_available);
 }
 async function build(){
  if(busy||building||!catalog)return;
  const cal=catalog.calibrations.find(row=>row.id===$('packageCalibration').value&&row.readable);
  const batch=(catalog.calibrated_batches||[]).find(row=>row.id===$('packageBatch').value&&row.available);
  const optimization=(catalog.optimizations||[]).find(row=>row.id===$('packageOptimization').value&&row.readable);
  const ids=Array.from($('packageDatasets').querySelectorAll('input:checked')).map(input=>input.value);
  if(!cal&&!batch&&!optimization&&!ids.length){$('packageBuildStatus').textContent='请至少选择一项已有数据。';return}
  if(ids.length>16){$('packageBuildStatus').textContent='每包最多16份电机数据集，请分开选择。';return}
  const selected=catalog.datasets.filter(row=>ids.includes(row.id)&&row.readable);
  if(selected.length!==ids.length){$('packageBuildStatus').textContent='所选记录已变化，请刷新后重新选择。';return}
  const payload={calibration:cal?{id:cal.id,report_sha256:cal.report_sha256}:null,
   datasets:selected.map(row=>({id:row.id,source_hashes_sha256:row.source_hashes_sha256})),
   batch:batch?{id:batch.id,source_binding_sha256:batch.source_binding_sha256}:null,
   optimization:optimization?{id:optimization.id,source_binding_sha256:optimization.source_binding_sha256}:null};
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
  if(busy||building)return;busy=true;formDisabled(true);$('dataRefresh').disabled=true;$('dataStatus').textContent='正在读取…';$('dataCounts').textContent='';
  for(const id of ['dataPackages','dataCalibrations','dataDatasets'])$(id).replaceChildren();
  try{
   const response=await fetch('/api/workbench/system-data',{method:'GET',cache:'no-store'}),data=await response.json();if(!response.ok)throw Error(data.error||'读取失败');
   if(data.schema!=='go_steel_system_data_v1'||!['packages','calibrations','datasets'].every(key=>Array.isArray(data[key])))throw Error('数据清单格式不符');
   packages(data.packages);calibrations(data.calibrations);datasets(data.datasets);
   buildOptions(data);catalog=data;
   const counts=data.counts;$('dataCounts').textContent=(counts.complete?'当前':'可读取部分')+'：'+counts.dataset_count+' 数据集、'+counts.row_count+' 保存记录、'+counts.original_accepted_count+' 原判据通过、'+counts.native_csv_accepted_count+' 原生CSV通过。记录包含重复材料及不同版本。';
   $('dataStatus').textContent='读取时间：'+data.created_utc+'。'+data.scope;
  }catch(error){catalog=null;formDisabled(true);for(const id of ['dataPackages','dataCalibrations','dataDatasets'])$(id).replaceChildren();$('dataCounts').textContent='';$('dataStatus').textContent='暂不可读取：'+error.message}
  finally{busy=false;$('dataRefresh').disabled=false}
 }
 $('dataRefresh').addEventListener('click',refresh);$('packageBuild').addEventListener('click',build);refresh();
})(typeof window==='object'?window:globalThis);
