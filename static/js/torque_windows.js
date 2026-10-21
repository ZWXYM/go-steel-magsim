(function(root){
 'use strict';
 const VERSION='calibrated_batch_torque_windows_v1',colors=['#2563b8','#008674','#d08222','#b44b71','#7752ad','#374151'];
 const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const number=value=>value==null?'—':Number(value).toFixed(6);
 function plotData(records,selectedOnly){
  const lines=records.map((record,index)=>({record,color:colors[index],points:(selectedOnly?record.window_statistics.sample_indices:record.time_ms.map((_,i)=>i)).map(i=>({t:record.time_ms[i],v:record.torque_Nm[i],index:i}))}));
  const points=lines.flatMap(line=>line.points);if(!points.length)return {lines:[]};
  let min=Math.min(0,...points.map(p=>p.v)),max=Math.max(0,...points.map(p=>p.v));const pad=Math.max((max-min)*.06,1e-6);min-=pad;max+=pad;
  return {lines,min,max,start:Math.min(...points.map(p=>p.t)),stop:Math.max(...points.map(p=>p.t))};
 }
 function chartMarkup(plot,width=560){
  if(!plot.lines.length)return '';
  const L=65,R=16,T=15,B=44,H=300,x=t=>L+(width-L-R)*(t-plot.start)/Math.max(plot.stop-plot.start,1e-12),y=v=>H-B-(H-T-B)*(v-plot.min)/(plot.max-plot.min),window=plot.lines[0].record.window_statistics.requested_window_ms;
  let html=`<rect x="${x(Math.max(window[0],plot.start))}" y="${T}" width="${Math.max(0,x(Math.min(window[1],plot.stop))-x(Math.max(window[0],plot.start)))}" height="${H-B-T}" fill="#eef6fc"/>`;
  for(let i=0;i<=4;i++){const value=plot.min+(plot.max-plot.min)*i/4,time=plot.start+(plot.stop-plot.start)*i/4;html+=`<line x1="${L}" y1="${y(value)}" x2="${width-R}" y2="${y(value)}" stroke="#dce5ef"/><text x="${L-8}" y="${y(value)+4}" text-anchor="end" font-size="11">${Number(value.toPrecision(4))}</text><text x="${x(time)}" y="${H-B+22}" text-anchor="middle" font-size="11">${Number(time.toPrecision(4))}</text>`}
  for(const line of plot.lines)html+=`<polyline fill="none" stroke="${line.color}" stroke-width="2" points="${line.points.map(point=>x(point.t)+','+y(point.v)).join(' ')}"/>`;
  return html+`<text x="${width/2}" y="296" font-size="12" text-anchor="middle">时间 / ms</text>`;
 }
 if(typeof module==='object'&&module.exports)module.exports={plotData,chartMarkup};
 if(!root.document)return;
 const $=id=>root.document.getElementById(id);let catalog=null,selected=[],current=null,revision=0,pending=null,catalogBusy=false,downloadBusy=false;
 const initial=new URL(root.location.href).searchParams;
 function selection(){return selected.slice()}
 function invalidate(){++revision;current=null;$('twResult').hidden=true;$('twError').textContent='';pending=null;controls()}
 function controls(){const count=selected.length;$('twApply').disabled=catalogBusy||!!pending||count<1||count>6;$('twRefresh').disabled=catalogBusy;$('twDownload').disabled=downloadBusy||!current;$('twBatch').disabled=catalogBusy;$('twReset').disabled=catalogBusy||!count;$('twStart').disabled=catalogBusy;$('twStop').disabled=catalogBusy;$('twItems').querySelectorAll('input').forEach(input=>input.disabled=catalogBusy||input.dataset.available!=='true')}
 function batch(){return catalog?.batches.find(row=>row.id===$('twBatch').value)}
 function resetWindow(){const first=batch()?.items.find(row=>selected.includes(row.id)&&row.available);$('twStart').value=first?.original_window_ms?.[0]??'';$('twStop').value=first?.original_window_ms?.[1]??'';invalidate()}
 function fillItems(keep=null){
  const record=batch();selected=[];const container=$('twItems');container.replaceChildren();
  for(const item of record?.items||[]){const label=document.createElement('label');label.className='item';const checkbox=document.createElement('input');checkbox.type='checkbox';checkbox.disabled=!item.available;checkbox.dataset.available=String(item.available);checkbox.value=item.id;checkbox.checked=item.available&&(keep?keep.includes(item.id):selected.length<4);if(checkbox.checked)selected.push(item.id);
   const text=document.createElement('span');text.textContent=item.material_name;const detail=document.createElement('small');detail.className='muted';detail.textContent=item.id+' · '+item.selection.key+(item.available?' · 有原结果 · 原数值'+(item.original_acceptance?'通过':'未通过'):' · 无完整转矩结果')+(item.error?' · '+item.error:'');label.append(checkbox,text,detail);container.append(label);
   checkbox.addEventListener('change',()=>{selected=Array.from(container.querySelectorAll('input:checked')).map(input=>input.value);invalidate();$('twSelection').textContent=`已选择 ${selected.length} 项；最多6项，修改后请重新统计。`});
  }
  $('twSelection').textContent=record?`${record.id} · ${record.status} · 已选 ${selected.length} 项`+(record.error?' · '+record.error:''):'所选批量不在可读目录，请重新选择。';invalidate();
 }
 async function read(url){const response=await fetch(url,{cache:'no-store'});if(!response.ok){const data=await response.json();throw Error(data.error||'读取失败')}return response.json()}
 async function refresh(){
  if(catalogBusy)return;catalogBusy=true;controls();const had=!!catalog,previous=$('twBatch').value,keep=selection();
  try{const data=await read('/api/workbench/torque-windows');if(data.version!==VERSION||!Array.isArray(data.batches))throw Error('批量目录格式不符');catalog=data;const chosen=had?previous:(initial.get('batch')||data.batches[0]?.id||'');$('twBatch').replaceChildren();
   if(!data.batches.some(row=>row.id===chosen)){const option=document.createElement('option');option.value='';option.textContent='请选择可读批量';$('twBatch').append(option)}
   for(const row of data.batches){const option=document.createElement('option');option.value=row.id;option.textContent=row.id+' · '+row.status+' · '+row.items.filter(item=>item.available).length+' 项已有转矩';$('twBatch').append(option)}
   $('twBatch').value=data.batches.some(row=>row.id===chosen)?chosen:'';fillItems(had?keep:initial.has('items')?initial.get('items').split(','):null);
   if(!had){resetWindow();if(initial.has('start_ms')||initial.has('stop_ms')){$('twStart').value=initial.get('start_ms')??'';$('twStop').value=initial.get('stop_ms')??''}}
  }catch(error){$('twError').textContent='目录暂不可更新：'+error.message+'；已有选择保留。'}finally{catalogBusy=false;controls()}
 }
 function form(){const artifact=$('twBatch').value,items=selection(),start=$('twStart').value,stop=$('twStop').value,window=[Number(start),Number(stop)];if(!(/^bhbatch_[0-9a-f]{12}$/).test(artifact)||!items.length||items.length>6)throw Error('请选择可读批量中的1至6项');if(!start.trim()||!stop.trim()||!window.every(Number.isFinite)||window[0]>=window[1])throw Error('请输入递增的有效开始和结束时间');return {artifact,items,window}}
 function query(data,snapshot){const values=new URLSearchParams({items:data.items.join(','),start_ms:String(data.window[0]),stop_ms:String(data.window[1])});if(snapshot)values.set('snapshot',snapshot);return values.toString()}
 function matches(input){try{return JSON.stringify(form())===JSON.stringify(input)}catch{return false}}
 function checked(data,input){
  if(data.version!==VERSION||data.batch!==input.artifact||!(/^[0-9a-f]{64}$/).test(data.snapshot_sha256)||data.records?.length!==input.items.length||JSON.stringify(data.requested_window_ms)!==JSON.stringify(input.window))throw Error('区间结果与所选请求不一致');
  data.records.forEach((row,index)=>{const n=row.time_ms?.length,indices=row.window_statistics?.sample_indices;if(row.item!==input.items[index]||!n||row.torque_Nm?.length!==n||![...row.time_ms,...row.torque_Nm].every(Number.isFinite)||!Array.isArray(indices)||indices.length<2||indices.some(i=>!Number.isInteger(i)||i<0||i>=n)||row.window_statistics.n_samples!==indices.length)throw Error('返回原始点或选择身份不完整')});return data;
 }
 function draw(){if(!current)return;for(const [id,zoom] of [['twFull',false],['twZoom',true]]){const svg=$(id),plot=plotData(current.data.records,zoom),width=Math.max(300,svg.clientWidth||560);svg.setAttribute('viewBox',`0 0 ${width} 300`);svg.innerHTML=chartMarkup(plot,width);svg.onmousemove=event=>{const rect=svg.getBoundingClientRect(),time=plot.start+(event.clientX-rect.left-65)*(plot.stop-plot.start)/Math.max(rect.width-81,1);$(id+'Probe').textContent=plot.lines.map(line=>{const point=line.points.reduce((a,b)=>Math.abs(b.t-time)<Math.abs(a.t-time)?b:a);return line.record.material_name+'：'+number(point.v)+' N·m @ '+number(point.t)+' ms'}).join(' · ')};svg.onmouseleave=()=>{$(id+'Probe').textContent=''}}}
 function render(){const data=current.data;$('twResult').hidden=false;$('twIdentity').textContent=data.batch+' · 请求区间 '+data.requested_window_ms.map(number).join('–')+' ms · 来源快照 '+data.snapshot_sha256;$('twLegend').innerHTML=data.records.map((row,index)=>`<span><i class="dot" style="background:${colors[index]}"></i>${esc(row.material_name)} (${esc(row.item)})</span>`).join('');
  $('twMetrics').innerHTML='<table><tr><th>材料 / 来源</th><th>原平均转矩 N·m</th><th>区间均值 N·m</th><th>最小 / 最大 N·m</th><th>峰峰 / |均值| %</th><th>采样点 / 区间 ms</th><th>转速 rpm</th><th>区间折算功率 W</th><th>原数值判据</th></tr>'+data.records.map(row=>{const stats=row.window_statistics;return `<tr><td>${esc(row.material_name)}<br><small>${esc(row.selection.key)}</small></td><td>${number(row.original_metrics.T_avg_Nm)}</td><td>${number(stats.sample_mean_torque_Nm)}</td><td>${number(stats.min_torque_Nm)} / ${number(stats.max_torque_Nm)}</td><td>${number(stats.peak_to_peak_over_abs_mean_pct)}</td><td>${stats.n_samples} / ${stats.sampled_window_ms.map(number).join('–')}</td><td>${number(stats.speed_rpm)}</td><td>${number(stats.electromagnetic_mechanical_power_W)}</td><td>${row.original_metrics.torque_numerical_acceptance?'通过':'未通过，原值保留'}</td></tr>`}).join('')+'</table>';
  $('twSources').innerHTML=data.records.map(row=>`<p><strong>${esc(row.material_name)}</strong> · <a href="${esc(row.analysis_url)}">原执行与诊断</a> · <a href="${esc(row.raw_csv_url)}">完整原CSV</a></p><pre>${esc(row.execution_id)}\n${esc(row.torque_csv_sha256)}\n模型：${esc(row.model_version)} / ${esc(row.measurement_protocol)}\n有效校准库：${esc(row.source.effective_bank_sha256)}\n排除材料：${esc(JSON.stringify(row.source.excluded_grades))}\n原异常：${esc(row.original_findings.join('、')||'无原异常说明')}</pre>`).join('');
  const bookmark=new URL(root.location.href);bookmark.search='batch='+encodeURIComponent(current.input.artifact)+'&'+query(current.input);$('twBookmark').href=bookmark.href;root.history?.replaceState(null,'',bookmark.href);draw();
 }
 async function analyze(){
  if(pending)return pending.promise;let input;try{input=form()}catch(error){$('twError').textContent=error.message;return}
  const request={revision,input,promise:null};pending=request;$('twError').textContent='';controls();
  request.promise=(async()=>{try{const data=await read('/api/workbench/torque-windows/'+input.artifact+'/analyze?'+query(input));if(pending!==request||revision!==request.revision||!matches(input))return;current={input,data:checked(data,input)};render()}catch(error){if(pending===request&&revision===request.revision&&matches(input)){current=null;$('twResult').hidden=true;$('twError').textContent=error.message}}finally{if(pending===request){pending=null;controls()}}})();return request.promise;
 }
 async function download(){if(downloadBusy||!current)return;const saved=current;downloadBusy=true;controls();try{const response=await fetch('/api/workbench/torque-windows/'+saved.input.artifact+'/export?'+query(saved.input,saved.data.snapshot_sha256),{cache:'no-store'});if(!response.ok){const data=await response.json();throw Error(data.error||'下载失败')}const url=URL.createObjectURL(await response.blob()),link=document.createElement('a');link.href=url;link.download=saved.input.artifact+'_torque_window.zip';try{link.click()}finally{setTimeout(()=>URL.revokeObjectURL(url),1000)}}catch(error){if(current===saved)$('twError').textContent=error.message}finally{downloadBusy=false;controls()}}
 $('twRefresh').addEventListener('click',refresh);$('twBatch').addEventListener('change',()=>{fillItems();resetWindow()});$('twReset').addEventListener('click',resetWindow);$('twStart').addEventListener('input',invalidate);$('twStop').addEventListener('input',invalidate);$('twApply').addEventListener('click',analyze);$('twDownload').addEventListener('click',download);root.addEventListener('resize',draw);refresh();
})(typeof window==='object'?window:globalThis);
