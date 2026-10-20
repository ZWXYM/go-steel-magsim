/* Saved calibrated-material batches; polling never starts or retries a task. */
const MBB={catalog:[],selected:new Set(),record:null,generation:0,historyGeneration:0,timer:null,plotBatch:null,plotMode:'all',plotSelected:new Set()};
const mb$=id=>document.getElementById(id),mbText=(tag,text)=>{const n=document.createElement(tag);n.textContent=text;return n};
const mbLabels={prepared:'计划已准备，尚未开始',prepared_source_changed:'准备后程序已更新，请保留旧计划',starting:'正在启动串行控制',running:'正在串行推进',completed:'批量已完成',completed_with_failures:'批量结束，包含失败或跳过项',needs_attention:'需要检查后显式接续',failed:'失败，现场保留',paused:'已暂停，阶段与结果保留'};
const mbItemLabels={pending:'待执行',importing:'导入与预检',preparing_model:'准备分析工程',analyzing:'转矩分析',completed:'新增结果已完成',reused_result:'复用已有结果',failed:'失败，现场保留',blocked:'原记录需检查'};
async function mbAPI(url,body){const response=await fetch(url,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const data=await response.json();if(!response.ok)throw Error(data.error||'请求失败');return data}
function mbEntries(){
 MBB.selected.clear();const out=mb$('batchMaterials');out.replaceChildren();const record=MBB.catalog.find(r=>r.id===mb$('batchCalibration').value);
 for(const entry of record?.entries||[]){const label=mbText('label','');label.className='choice';const input=document.createElement('input');input.type='checkbox';input.value=entry.key;input.disabled=!entry.available;
 const text=mbText('span',({fit:'标定',holdout:'整牌号留出',prediction:'保存的预测'}[entry.kind]||entry.kind)+' · '+entry.label);text.append(mbText('small',entry.available?(entry.excluded_grades.length?'排除 '+entry.excluded_grades.join('、'):'使用全库校准'):entry.reason));input.onchange=()=>{if(input.checked)MBB.selected.add(entry.key);else MBB.selected.delete(entry.key);mbSelection()};label.append(input,text);out.append(label)}mbSelection();
}
function mbSelection(){mb$('batchSelectionStatus').textContent=`已选择 ${MBB.selected.size} 种材料；准备只补CPU步骤，实际分析需单独开始。`}
function mbSelectFits(clear=false){MBB.selected.clear();for(const input of mb$('batchMaterials').querySelectorAll('input')){input.checked=!clear&&!input.disabled&&input.value.startsWith('fit:');if(input.checked)MBB.selected.add(input.value)}mbSelection()}
async function mbCatalog(){mb$('batchCatalogRefresh').disabled=true;mb$('batchPrepareError').textContent='';try{const data=await mbAPI('/api/workbench/material-inputs');MBB.catalog=data.records;const select=mb$('batchCalibration'),old=select.value;select.replaceChildren();for(const r of data.records){const o=mbText('option',r.id);o.value=r.id;select.append(o)}if(data.records.some(r=>r.id===old))select.value=old;mbEntries();if(MBB.record)mbRender(MBB.record,MBB.canStart)}catch(error){mb$('batchPrepareError').textContent=error.message}finally{mb$('batchCatalogRefresh').disabled=false}}
async function mbPrepare(){
 const button=mb$('batchPrepare');if(button.disabled)return;button.disabled=true;mb$('batchPrepareError').textContent='';
 try{if(!MBB.selected.size||MBB.selected.size>8)throw Error('请选择1–8种材料');const calibration=mb$('batchCalibration').value;
 const data=await mbAPI('/api/workbench/calibrated-motor/batches',{materials:[...MBB.selected].map(key=>({calibration,key})),cores:Number(mb$('batchCores').value),measurement_protocol:mb$('batchProtocol').value});
 await mbHistory(data.id);mb$('batchPrepareError').textContent='';
 }catch(error){mb$('batchPrepareError').textContent=error.message}finally{button.disabled=false}
}
function mbName(item){const entry=MBB.catalog.find(r=>r.id===item.selection?.calibration)?.entries.find(e=>e.key===item.selection?.key);return entry?.available?entry.label:item.source.material_name}
function mbCounts(record){const results=record.items.filter(r=>r.result_available);return {reused:results.filter(r=>r.status==='reused_result').length,passed:results.filter(r=>r.metrics?.torque_numerical_acceptance===true).length,bad:results.filter(r=>r.metrics?.torque_numerical_acceptance===false).length,unknown:results.filter(r=>![true,false].includes(r.metrics?.torque_numerical_acceptance)).length}}
function mbWaveforms(record){return record.items.filter(r=>r.result_available&&Array.isArray(r.metrics?.time_ms)&&r.metrics.time_ms.length&&r.metrics.time_ms.length===r.metrics.torque_Nm?.length)}
function mbPlotSelection(record){
 const available=mbWaveforms(record);
 if(MBB.plotBatch!==record.id){MBB.plotBatch=record.id;MBB.plotMode='all';MBB.plotSelected.clear();
  const query=typeof location!=='undefined'?new URLSearchParams(location.search):null,choice=query?.get('batch')===record.id?query.get('plot'):null;
  if(['accepted','none'].includes(choice))MBB.plotMode=choice;
  else if(choice&&choice!=='all'){const ids=choice.split(',');if(ids.every(id=>record.items.some(r=>r.id===id))){MBB.plotMode='manual';MBB.plotSelected=new Set(ids)}}
 }
 if(MBB.plotMode==='all')MBB.plotSelected=new Set(available.map(r=>r.id));
 else if(MBB.plotMode==='accepted')MBB.plotSelected=new Set(available.filter(r=>r.metrics.torque_numerical_acceptance===true).map(r=>r.id));
 else if(MBB.plotMode==='none')MBB.plotSelected.clear();
 return available;
}
function mbPlotURL(record){if(typeof history==='undefined'||typeof location==='undefined')return;const query=new URLSearchParams();query.set('batch',record.id);
 if(MBB.plotMode!=='all')query.set('plot',MBB.plotMode==='manual'?[...MBB.plotSelected].join(','):MBB.plotMode);
 history.replaceState(null,'',location.pathname+'?'+query.toString());
}
function mbPlotMode(mode){if(!MBB.record)return;MBB.plotMode=mode;mbPlot(MBB.record)}
function mbRender(record,canStart){
 MBB.canStart=canStart;
 const counts=mbCounts(record);MBB.record=record;mb$('batchStatus').textContent=record.id+' · '+(mbLabels[record.status]||record.status)+(counts.bad?' · 含数值异常':'');
 const b=record.budget;mb$('batchBudget').textContent=`最多新增 ${b.max_new_imports} 次材料导入、${b.max_new_motor_solves} 次电机分析；${b.cores}核 / ${b.gpus} GPU。每次导入240秒、每次电机1200秒；已有结果直接复用。`;
 mb$('batchError').textContent=record.error||'';mb$('batchStart').disabled=!(canStart&&record.status==='prepared'&&record.dispatch_compatible);
 mb$('batchResume').disabled=!(canStart&&['paused','needs_attention'].includes(record.status)&&record.dispatch_compatible);
 mb$('batchPause').disabled=!['prepared','starting','running'].includes(record.status)||record.pause_requested;
 mb$('batchBundle').hidden=false;mb$('batchBundle').href='/api/workbench/calibrated-motor/batches/'+record.id+'/bundle';
 mb$('batchTotals').textContent=`${record.totals.materials}种材料 · 可读取实际转矩 ${record.totals.actual_torque_results}项 · 本批新增完成 ${record.totals.new_completed_motor_solves}项 · 复用 ${counts.reused}项 · 原数值判据：通过 ${counts.passed}项 / 未通过 ${counts.bad}项 / 未报告 ${counts.unknown}项`+(record.pause_requested?(record.status==='paused'?' · 已暂停，可显式接续':' · 已请求暂停，等待当前阶段结束'):'');
 const rows=mb$('batchRows');rows.replaceChildren();const number=v=>v===null||v===undefined?'—':Number(v).toFixed(6);
 for(const item of record.items){const row=document.createElement('tr'),name=document.createElement('td'),m=item.result_available?item.metrics||{}:{};name.append(mbText('strong',mbName(item)),mbText('small',' · '+item.selection.key));
 name.append(mbText('p','排除：'+(item.source.excluded_grades.join('、')||'无')));const link=mbText('a','查看独立分析与原始结果');link.href=item.analysis_url;name.append(link);
 const bad=item.result_available&&m.torque_numerical_acceptance===false,quality=mbText('td',!item.result_available?'尚无结果':bad?'数值异常 · 原值保留':m.torque_numerical_acceptance===true?'通过原判据':'未报告');if(bad)quality.className='error';
 const phase=mbText('td',mbItemLabels[item.status]||item.status);
 row.append(name,phase,quality,mbText('td',number(m.T_avg_Nm)),mbText('td',number(m.T_min_Nm)+' / '+number(m.T_max_Nm)),mbText('td',number(m.K_T_ripple_pct)),mbText('td',item.error||item.execution_id||'尚无实际转矩结果'));rows.append(row)}
 mbPlot(record);
}
function mbPlot(record){
 const svg=mb$('batchWaveforms'),legend=mb$('batchPlotLegend'),panel=mb$('batchPlotPanel');if(!svg||!legend||!panel)return;
 svg.replaceChildren();legend.replaceChildren();const available=mbPlotSelection(record),items=available.filter(r=>MBB.plotSelected.has(r.id)),choices=mb$('batchPlotChoices');
 panel.hidden=!available.length;svg.hidden=legend.hidden=!items.length;mb$('batchPlotEmpty').hidden=!!items.length;mb$('batchPlotCount').textContent=`显示 ${items.length} / ${available.length} 条原始波形；筛选仅改变显示，下载包保留全部材料和原始点。`;
 choices.replaceChildren();for(const item of available){const label=mbText('label',''),input=document.createElement('input');label.className='choice';input.type='checkbox';input.checked=MBB.plotSelected.has(item.id);input.value=item.id;
 input.onchange=()=>{if(input.checked)MBB.plotSelected.add(item.id);else MBB.plotSelected.delete(item.id);MBB.plotMode=MBB.plotSelected.size?'manual':'none';mbPlot(record)};
 const quality=item.metrics.torque_numerical_acceptance;label.append(input,mbText('span',mbName(item)+' · '+(quality===true?'通过原判据':quality===false?'数值异常':'判据未报告')));choices.append(label)}
 mbPlotURL(record);if(!items.length)return;
 const times=items.flatMap(r=>r.metrics.time_ms),values=items.flatMap(r=>r.metrics.torque_Nm),x0=Math.min(...times),x1=Math.max(...times),lo=Math.min(0,...values),hi=Math.max(...values,lo+.01),colors=['#2466ad','#c36a21','#219178','#9151b0','#a54260','#527326','#6c68cc','#6e7177'];
 const x=v=>105+760*(v-x0)/(x1-x0||1),y=v=>260-225*(v-lo)/(hi-lo);svg.setAttribute('viewBox','0 0 920 320');
 const add=(tag,attrs,text)=>{const e=document.createElementNS('http://www.w3.org/2000/svg',tag);Object.entries(attrs).forEach(([k,v])=>e.setAttribute(k,v));if(text!==undefined)e.textContent=text;svg.append(e);return e};
 const window=items[0].metrics.selected_window_ms;if(window)add('rect',{x:x(window[0]),y:25,width:x(window[1])-x(window[0]),height:235,fill:'#edf2f7'});
 add('path',{d:'M105 25V260H865',fill:'none',stroke:'#64748b'});
 for(const v of [lo,(lo+hi)/2,hi])add('text',{x:4,y:y(v)+4,'font-size':12},v.toFixed(3)+' N·m');
 for(const v of [x0,(x0+x1)/2,x1])add('text',{x:x(v),y:286,'font-size':12,'text-anchor':'middle'},v.toFixed(2)+' ms');
 items.forEach((item,i)=>{const m=item.metrics,color=colors[record.items.indexOf(item)%colors.length];add('polyline',{points:m.time_ms.map((v,k)=>`${x(v)},${y(m.torque_Nm[k])}`).join(' '),fill:'none',stroke:color,'stroke-width':2});
 m.time_ms.forEach((v,k)=>add('circle',{cx:x(v),cy:y(m.torque_Nm[k]),r:1.8,fill:color}));const label=mbText('span',mbName(item));label.style.color=color;legend.append(label)});
}
async function mbLoad(){
 const artifact=mb$('batchHistory').value;if(!artifact)return;const generation=++MBB.generation;if(MBB.timer)clearTimeout(MBB.timer);mb$('batchError').textContent='';
 const settled=await Promise.allSettled([mbAPI('/api/workbench/calibrated-motor/batches/'+artifact),mbAPI('/api/workbench/resources')]);if(generation!==MBB.generation)return;
 if(settled[0].status!=='fulfilled'){mb$('batchError').textContent='批量读取失败：'+settled[0].reason.message;for(const id of ['batchStart','batchResume','batchPause'])mb$(id).disabled=true;return}
 mbRender(settled[0].value,settled[1].status==='fulfilled'&&!settled[1].value.cpu_only&&settled[1].value.native_submission_allowed!==false);
 if(settled[1].status!=='fulfilled')mb$('batchError').textContent='资源状态不可读，原生开始与接续暂不可用。';
 if(settled[1].status==='fulfilled'&&settled[1].value.source_restart_required)mb$('batchError').textContent='源码已更新，请重新运行启动器打开匹配版本；仍可查看结果或请求阶段结束后暂停。';
 if(['starting','running'].includes(settled[0].value.status))MBB.timer=setTimeout(mbLoad,3000);
}
async function mbHistory(preferred){
 const generation=++MBB.historyGeneration;mb$('batchError').textContent='';try{const records=await mbAPI('/api/workbench/calibrated-motor/batches');if(generation!==MBB.historyGeneration)return;
 const select=mb$('batchHistory'),old=preferred||select.value||(typeof location!=='undefined'?new URLSearchParams(location.search).get('batch'):null);select.replaceChildren();
 for(const r of records){const o=mbText('option',r.id+' · '+(mbLabels[r.status]||r.status));o.value=r.id;select.append(o)}
 if(old){if(!records.some(r=>r.id===old)){mb$('batchError').textContent='指定的批量记录不存在，请选择现有记录。';return}select.value=old}
 if(records.length)await mbLoad();else mb$('batchStatus').textContent='尚无批量计划，请先选择保存材料。';
 }catch(error){if(generation===MBB.historyGeneration)mb$('batchError').textContent=error.message}
}
async function mbAction(action){
 if(!MBB.record)return;for(const id of ['batchStart','batchResume','batchPause'])mb$(id).disabled=true;
 try{await mbAPI('/api/workbench/calibrated-motor/batches/'+MBB.record.id+'/'+action,{});await mbLoad()}
 catch(error){mb$('batchError').textContent=error.message;await mbLoad();mb$('batchError').textContent=error.message}
}
if(typeof document!=='undefined'){
 mb$('batchPlotAll').onclick=()=>mbPlotMode('all');mb$('batchPlotAccepted').onclick=()=>mbPlotMode('accepted');mb$('batchPlotNone').onclick=()=>mbPlotMode('none');
 mb$('batchCatalogRefresh').onclick=mbCatalog;mb$('batchCalibration').onchange=mbEntries;mb$('batchSelectFit').onclick=()=>mbSelectFits();mb$('batchClearSelection').onclick=()=>mbSelectFits(true);
 mb$('batchPrepare').onclick=mbPrepare;mb$('batchRefresh').onclick=()=>mbHistory();mb$('batchHistory').onchange=mbLoad;
 mb$('batchStart').onclick=()=>mbAction('start');mb$('batchResume').onclick=()=>mbAction('resume');mb$('batchPause').onclick=()=>mbAction('pause');mbCatalog();mbHistory();
}
