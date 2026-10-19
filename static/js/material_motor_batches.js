/* Saved calibrated-material batches; polling never starts or retries a task. */
const MBB={catalog:[],selected:new Set(),record:null,generation:0,historyGeneration:0,timer:null};
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
async function mbCatalog(){mb$('batchCatalogRefresh').disabled=true;mb$('batchPrepareError').textContent='';try{const data=await mbAPI('/api/workbench/material-library');MBB.catalog=data.records;const select=mb$('batchCalibration'),old=select.value;select.replaceChildren();for(const r of data.records){const o=mbText('option',r.id);o.value=r.id;select.append(o)}if(data.records.some(r=>r.id===old))select.value=old;mbEntries()}catch(error){mb$('batchPrepareError').textContent=error.message}finally{mb$('batchCatalogRefresh').disabled=false}}
async function mbPrepare(){
 const button=mb$('batchPrepare');if(button.disabled)return;button.disabled=true;mb$('batchPrepareError').textContent='';
 try{if(!MBB.selected.size||MBB.selected.size>8)throw Error('请选择1–8种材料');const calibration=mb$('batchCalibration').value;
 const data=await mbAPI('/api/workbench/calibrated-motor/batches',{materials:[...MBB.selected].map(key=>({calibration,key})),cores:Number(mb$('batchCores').value),measurement_protocol:mb$('batchProtocol').value});
 await mbHistory(data.id);mb$('batchPrepareError').textContent='';
 }catch(error){mb$('batchPrepareError').textContent=error.message}finally{button.disabled=false}
}
function mbRender(record,canStart){
 if(typeof history!=='undefined'&&typeof location!=='undefined')history.replaceState(null,'',location.pathname+'?batch='+encodeURIComponent(record.id));
 MBB.record=record;mb$('batchStatus').textContent=record.id+' · '+(mbLabels[record.status]||record.status);
 const b=record.budget;mb$('batchBudget').textContent=`最多新增 ${b.max_new_imports} 次材料导入、${b.max_new_motor_solves} 次电机分析；${b.cores}核 / ${b.gpus} GPU。每次导入240秒、每次电机1200秒；已有结果直接复用。`;
 mb$('batchError').textContent=record.error||'';mb$('batchStart').disabled=!(canStart&&record.status==='prepared'&&record.dispatch_compatible);
 mb$('batchResume').disabled=!(canStart&&['paused','needs_attention'].includes(record.status)&&record.dispatch_compatible);
 mb$('batchPause').disabled=!['prepared','starting','running'].includes(record.status)||record.pause_requested;
 mb$('batchBundle').hidden=false;mb$('batchBundle').href='/api/workbench/calibrated-motor/batches/'+record.id+'/bundle';
 const anomalous=record.items.filter(r=>r.result_available&&r.metrics?.torque_numerical_acceptance===false).length;
 mb$('batchTotals').textContent=`${record.totals.materials}种材料 · 可读取实际转矩 ${record.totals.actual_torque_results}项 · 本批新增完成 ${record.totals.new_completed_motor_solves}项 · 原数值判据未通过 ${anomalous}项`+(record.pause_requested?(record.status==='paused'?' · 已暂停，可显式接续':' · 已请求暂停，等待当前阶段结束'):'');
 const rows=mb$('batchRows');rows.replaceChildren();const number=v=>v===null||v===undefined?'—':Number(v).toFixed(6);
 for(const item of record.items){const row=document.createElement('tr'),name=document.createElement('td'),m=item.metrics||{};name.append(mbText('strong',item.source.material_name),mbText('small',' · '+item.selection.key));
 name.append(mbText('p','排除：'+(item.source.excluded_grades.join('、')||'无')));const link=mbText('a','查看独立分析与原始结果');link.href=item.analysis_url;name.append(link);
 const bad=item.result_available&&m.torque_numerical_acceptance===false,phase=mbText('td',bad?'结果含数值异常 · 原值保留':mbItemLabels[item.status]||item.status);if(bad)phase.className='error';
 row.append(name,phase,mbText('td',number(m.T_avg_Nm)),mbText('td',number(m.T_min_Nm)+' / '+number(m.T_max_Nm)),mbText('td',number(m.K_T_ripple_pct)),mbText('td',item.error||item.execution_id||'尚无实际转矩结果'));rows.append(row)}
 mbPlot(record);
}
function mbPlot(record){
 const svg=mb$('batchWaveforms'),legend=mb$('batchPlotLegend'),panel=mb$('batchPlotPanel');if(!svg||!legend||!panel)return;
 svg.replaceChildren();legend.replaceChildren();const items=record.items.filter(r=>r.result_available&&Array.isArray(r.metrics?.time_ms)&&r.metrics.time_ms.length&&r.metrics.time_ms.length===r.metrics.torque_Nm?.length);
 panel.hidden=!items.length;if(!items.length)return;
 const times=items.flatMap(r=>r.metrics.time_ms),values=items.flatMap(r=>r.metrics.torque_Nm),x0=Math.min(...times),x1=Math.max(...times),lo=Math.min(0,...values),hi=Math.max(...values,lo+.01),colors=['#2466ad','#c36a21','#219178','#9151b0','#a54260','#527326','#6c68cc','#6e7177'];
 const x=v=>105+760*(v-x0)/(x1-x0||1),y=v=>260-225*(v-lo)/(hi-lo);svg.setAttribute('viewBox','0 0 920 320');
 const add=(tag,attrs,text)=>{const e=document.createElementNS('http://www.w3.org/2000/svg',tag);Object.entries(attrs).forEach(([k,v])=>e.setAttribute(k,v));if(text!==undefined)e.textContent=text;svg.append(e);return e};
 const window=items[0].metrics.selected_window_ms;if(window)add('rect',{x:x(window[0]),y:25,width:x(window[1])-x(window[0]),height:235,fill:'#edf2f7'});
 add('path',{d:'M105 25V260H865',fill:'none',stroke:'#64748b'});
 for(const v of [lo,(lo+hi)/2,hi])add('text',{x:4,y:y(v)+4,'font-size':12},v.toFixed(3)+' N·m');
 for(const v of [x0,(x0+x1)/2,x1])add('text',{x:x(v),y:286,'font-size':12,'text-anchor':'middle'},v.toFixed(2)+' ms');
 items.forEach((item,i)=>{const m=item.metrics,color=colors[i%colors.length];add('polyline',{points:m.time_ms.map((v,k)=>`${x(v)},${y(m.torque_Nm[k])}`).join(' '),fill:'none',stroke:color,'stroke-width':2});
 m.time_ms.forEach((v,k)=>add('circle',{cx:x(v),cy:y(m.torque_Nm[k]),r:1.8,fill:color}));const label=mbText('span',item.source.material_name);label.style.color=color;legend.append(label)});
}
async function mbLoad(){
 const artifact=mb$('batchHistory').value;if(!artifact)return;const generation=++MBB.generation;if(MBB.timer)clearTimeout(MBB.timer);mb$('batchError').textContent='';
 const settled=await Promise.allSettled([mbAPI('/api/workbench/calibrated-motor/batches/'+artifact),mbAPI('/api/workbench/resources')]);if(generation!==MBB.generation)return;
 if(settled[0].status!=='fulfilled'){mb$('batchError').textContent='批量读取失败：'+settled[0].reason.message;for(const id of ['batchStart','batchResume','batchPause'])mb$(id).disabled=true;return}
 mbRender(settled[0].value,settled[1].status==='fulfilled'&&!settled[1].value.cpu_only);
 if(settled[1].status!=='fulfilled')mb$('batchError').textContent='资源状态不可读，原生开始与接续暂不可用。';
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
 mb$('batchCatalogRefresh').onclick=mbCatalog;mb$('batchCalibration').onchange=mbEntries;mb$('batchSelectFit').onclick=()=>mbSelectFits();mb$('batchClearSelection').onclick=()=>mbSelectFits(true);
 mb$('batchPrepare').onclick=mbPrepare;mb$('batchRefresh').onclick=()=>mbHistory();mb$('batchHistory').onchange=mbLoad;
 mb$('batchStart').onclick=()=>mbAction('start');mb$('batchResume').onclick=()=>mbAction('resume');mb$('batchPause').onclick=()=>mbAction('pause');mbCatalog();mbHistory();
}
