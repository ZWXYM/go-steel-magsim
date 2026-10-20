/* Reopen saved records. Only existing read endpoints are requested here. */
(function(root){
 'use strict';
 const labels={ready:'已准备，尚未开始',prepared:'已准备，尚未开始',preparing:'正在准备',starting:'正在启动',running:'正在计算',paused:'已暂停',completed:'已完成',completed_with_failures:'已结束，含失败或跳过项',failed:'失败，现场保留',preparation_failed:'准备失败',needs_attention:'待检查',invalid:'记录不可读取',prepared_source_changed:'程序已更新，原计划保留'};
 const valid=(id,prefix)=>typeof id==='string'&&new RegExp('^'+prefix+'_[a-f0-9]{12}$').test(id);
 const status=v=>labels[v]||v||'状态未登记';
 const ordered=rows=>[...rows].sort((a,b)=>(Date.parse(b.created_utc)||0)-(Date.parse(a.created_utc)||0));
 function when(value){const t=Date.parse(value);return Number.isFinite(t)?new Date(t).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}):'时间未登记'}
 function nativeRows(records){
  if(!Array.isArray(records))throw Error('原生任务目录格式不符');
  return {count:`保存 ${records.length} 个任务`,rows:ordered(records).slice(0,3).map(r=>({title:(r.recipe?.material_id||r.id)+' · '+status(r.status),href:valid(r.id,'nativerun')?'/native-material-runs?run='+encodeURIComponent(r.id):null,detail:r.id+' · '+when(r.created_utc)+(r.error?' · '+r.error:'')}))};
 }
 function resultSummary(items){
  const results=(items||[]).filter(i=>i.result_available),passed=results.filter(i=>i.metrics?.torque_numerical_acceptance===true).length,failed=results.filter(i=>i.metrics?.torque_numerical_acceptance===false).length;
  return `已整理 ${results.length}/${(items||[]).length} 份结果 · 原数值筛查：通过 ${passed}，未通过 ${failed}，未报告 ${results.length-passed-failed}`;
 }
 function batchRows(records){
  if(!Array.isArray(records))throw Error('电机批量目录格式不符');
  return {count:`保存 ${records.length} 个校准材料批量`,rows:ordered(records).slice(0,3).map(r=>({title:r.id+' · '+status(r.status),href:valid(r.id,'bhbatch')?'/calibrated-motor/batches?batch='+encodeURIComponent(r.id):null,detail:when(r.created_utc)+' · '+resultSummary(r.items)+(r.error?' · '+r.error:'')}))};
 }
 function materialRows(catalog){
  if(!Array.isArray(catalog?.records))throw Error('材料目录格式不符');
  const count=catalog.records.reduce((n,r)=>n+(r.entries||[]).length,0),rows=[];
  for(const r of ordered(catalog.records).slice(0,3)){
   const entries=r.entries||[],base=valid(r.id,'cal')?'/material-library?calibration='+encodeURIComponent(r.id):null;
   rows.push({title:r.id+' · '+entries.length+' 份材料',href:base,detail:when(r.created_utc)+(r.error?' · '+r.error:' · 可读取 '+entries.filter(e=>e.available).length+' 份')});
   const prediction=entries.find(e=>e.available&&e.kind==='prediction'&&/^prediction:pred_[a-f0-9]{12}$/.test(e.key));
   if(prediction)rows.push({title:'保存预测：'+prediction.label,href:base?base+'&key='+encodeURIComponent(prediction.key):null,detail:'来源校准 '+r.id});
  }
  return {count:`保存 ${catalog.records.length} 份校准 · ${count} 份材料`,rows};
 }
 if(typeof module==='object'&&module.exports)module.exports={nativeRows,batchRows,materialRows,resultSummary};
 if(!root.document)return;
 const $=id=>root.document.getElementById(id),sections=[['Native','native-material-runs',nativeRows],['Materials','material-inputs',materialRows],['Batches','calibrated-motor/batches',batchRows]];
 let busy=false;
 function render(key,data){
  $('recent'+key+'Count').textContent=data.count;const list=$('recent'+key);list.replaceChildren();
  if(!data.rows.length){const item=root.document.createElement('li');item.textContent='暂无保存记录，可从下方入口开始。';list.append(item);return}
  for(const row of data.rows){const item=root.document.createElement('li'),title=root.document.createElement(row.href?'a':'span');title.textContent=row.title;if(row.href)title.href=row.href;const detail=root.document.createElement('small');detail.textContent=row.detail;item.append(title,detail);list.append(item)}
 }
 async function refresh(){
  if(busy)return;busy=true;$('recentRefresh').disabled=true;
  try{await Promise.all(sections.map(async([key,path,build])=>{
   $('recent'+key+'Count').textContent='正在读取保存记录…';$('recent'+key).replaceChildren();
   try{const response=await fetch('/api/workbench/'+path,{method:'GET'}),data=await response.json();if(!response.ok)throw Error(data.error||'读取失败');render(key,build(data))}
   catch(error){$('recent'+key).replaceChildren();$('recent'+key+'Count').textContent='暂不可读取：'+error.message}
  }))}finally{busy=false;$('recentRefresh').disabled=false}
 }
 $('recentRefresh').addEventListener('click',refresh);refresh();
})(typeof window==='object'?window:globalThis);
