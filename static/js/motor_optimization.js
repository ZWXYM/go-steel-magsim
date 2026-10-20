const $=id=>document.getElementById(id),esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const keys=['min_ripple_reduction_pct','max_iron_loss_increase_pct','min_torque_change_pct','ripple_weight','iron_loss_weight','efficiency_weight'];
let current=null,loadGeneration=0,listGeneration=0,analysisPending=false,historyGeneration=0,planGeneration=0,preparingPlan=false,planAvailable=false;
let savedScreen=null,savedScreenGeneration=0,savedScreenLoading=false,reusingScreen=false;
const pendingPlanIds=new Set();
const num=(v,d=3)=>typeof v==='number'&&Number.isFinite(v)?v.toFixed(d):'—';
async function api(path,data){const r=await fetch('/api/workbench/optimization/'+path,data===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});const v=await r.json();if(!r.ok)throw Error(v.error||'读取失败');return v}
function error(e){$('error').hidden=false;$('error').textContent=e.message}
function metricsTable(rows,saved=false){return '<div class="tablewrap"><table><tr><th>牌号 / 案例</th><th>状态</th><th>分数</th><th>平均转矩 N·m</th><th>脉动 %</th><th>铁损 W</th><th>效率估计 %</th><th>说明</th></tr>'+rows.map(r=>{const m=r.metrics||r;return `<tr><td>${esc(r.material||r.material_assignment||r.case)}</td><td>${saved?esc(r.status||r.group):(r.meets_limits?'满足配置':'未满足')}</td><td>${num(saved?r.V8_1_score:r.score,3)}</td><td>${num(m.T_avg_Nm,4)}</td><td>${num(m.K_T_ripple_pct,2)}</td><td>${num(m.P_Fe_W,3)}</td><td>${num(m.eta_estimate_pct??m.eta_pct,2)}</td><td>${esc(saved?r.error||'':r.reasons.join('；'))}</td></tr>`}).join('')+'</table></div>'}
async function workflow(){const v=await api('workflow');$('rule').textContent=v.original_criteria+'；'+v.score_definition;$('workflow').innerHTML=v.stages.map(s=>`<div class="stage"><h3>${esc(s.title)}</h3>${s.available?`<p class="muted">原生成时间 ${esc(s.generated||'未登记')} · ${s.rows.length} 行 · ${s.ranked.length} 个原排序项</p>${Object.keys(s.files).map(n=>`<a href="/api/workbench/optimization/sources/${s.id}/${encodeURIComponent(n)}">${esc(n)}</a>`).join(' · ')}<details><summary>查看原保存指标</summary>${metricsTable(s.ranked.length?s.ranked:s.rows,true)}</details>`:'此资源目录未附带原输出'}</div>`).join('');$('scripts').innerHTML=v.scripts.length?v.scripts.map(s=>`<p><code>${esc(s.relative_path)}</code><br><span class="muted">${esc(s.sha256)}</span></p>`).join(''):'尚未配置原电机程序目录'}
function screeningContext(){return JSON.stringify([loadGeneration,$('dataset').value,current?.id,$('reference').value,...keys.map(k=>$(k).value)])}
function updateScreeningButtons(){
 $('analyze').disabled=reusingScreen||analysisPending||!current?.references.length||current.id!==$('dataset').value;
 $('bindPlan').disabled=reusingScreen||preparingPlan||!current?.id.startsWith('scan_')||!current?.references.length||current.id!==$('dataset').value;
 $('preparePlan').disabled=reusingScreen||preparingPlan||!planAvailable;updateSavedScreenControls();
}
function screeningChanged(){
 $('result').hidden=true;$('analysisStatus').textContent=analysisPending?'当前选择或配置已变更；此前请求返回后可重新保存。':'配置已变更；请重新保存筛选与排序。';
}
function referenceChanged(){screeningChanged();$('referenceNote').textContent=current?.id==='v8_1'&&$('reference').value==='original_control'?'脉动/铁损/效率采用原 Control；平均转矩采用原 Baseline。':'全部相对指标采用所选同数据集基准。'}
async function loadDataset(){
 const token=++loadGeneration,id=$('dataset').value;current=null;updateScreeningButtons();$('reference').innerHTML='';$('referenceNote').textContent='';$('datasetScope').textContent='正在读取…';$('result').hidden=true;$('analysisStatus').textContent=analysisPending?'先前配置的筛选仍在保存，当前结果区等待重新选择。':'';
 try{if(!id){$('datasetScope').textContent='尚无可用结果；可先准备并完成一个新扫描';return}const v=await api('datasets/'+encodeURIComponent(id));if(token!==loadGeneration)return;if(v.id!==id)throw Error('返回数据集与当前选择不一致，未混用');current=v;$('datasetScope').textContent=v.version+' · '+v.measurement_protocol+'；'+v.scope;$('reference').innerHTML=v.references.map(r=>`<option value="${esc(r.case)}">${esc(r.material||r.case)} · ${esc(r.case)}</option>`).join('');$('reference').value=v.default_reference||'';referenceChanged();updateScreeningButtons()}
 catch(e){if(token===loadGeneration){current=null;updateScreeningButtons();$('datasetScope').textContent='数据集读取失败';error(e)}}
}
async function refreshDatasets(){
 const token=++listGeneration,btn=$('refresh');btn.disabled=true;
 try{const values=await api('datasets');if(token!==listGeneration)return;const selected=$('dataset').value;$('dataset').innerHTML=values.map(v=>`<option value="${esc(v.id)}">${esc(v.title)}</option>`).join('');if(values.some(v=>v.id===selected))$('dataset').value=selected;await loadDataset()}
 catch(e){if(token===listGeneration)error(e)}finally{if(token===listGeneration)btn.disabled=false}
}
async function history(){const token=++historyGeneration,values=await api('history');if(token!==historyGeneration)return;$('history').innerHTML=values.length?'<div class="tablewrap"><table><tr><th>记录 / 时间</th><th>数据集</th><th>满足配置</th><th>首选</th><th>查看 / 下载</th></tr>'+values.map(v=>`<tr><td>${esc(v.id)}<br>${esc(v.created_utc)}</td><td>${esc(v.dataset)}</td><td>${v.meets_limits_count}</td><td>${esc(v.best_case||'无')}</td><td>${/^opt_[a-f0-9]{12}$/.test(v.id)?`<button onclick="loadSavedScreen('${v.id}')">查看保存结果</button> `:''}<a href="/api/workbench/optimization/${encodeURIComponent(v.id)}/files/analysis_bundle.zip">完整筛选包</a> · <a href="/api/workbench/optimization/${encodeURIComponent(v.id)}/files/ranking.csv">CSV</a></td></tr>`).join('')+'</table></div>':'暂无记录'}
function updateSavedScreenControls(){$('reuseSavedScreen').disabled=!savedScreen||savedScreenLoading||reusingScreen||analysisPending||preparingPlan}
function validSavedScreen(v,id){
 return v?.id===id&&typeof v.dataset?.id==='string'&&typeof v.reference==='string'&&Array.isArray(v.ranked)&&v.ranked.every(r=>Array.isArray(r.reasons))&&v.config&&Object.keys(v.config).length===keys.length&&keys.every(k=>typeof v.config[k]==='number'&&Number.isFinite(v.config[k])&&Math.abs(v.config[k])<=1000&&(!k.endsWith('_weight')||v.config[k]>=0))&&keys.filter(k=>k.endsWith('_weight')).some(k=>v.config[k]>0);
}
async function loadSavedScreen(id){
 if(!/^opt_[a-f0-9]{12}$/.test(id))return;const token=++savedScreenGeneration;savedScreenLoading=true;savedScreen=null;$('savedScreenPanel').hidden=true;$('savedScreenStatus').textContent='正在读取保存结果…';updateSavedScreenControls();
 try{const v=await api(id+'/files/analysis.json');if(token!==savedScreenGeneration)return;if(!validSavedScreen(v,id))throw Error('保存记录身份或配置格式不符，未载入');savedScreen=v;
  const names=['脉动最小降幅 %','铁损最大增幅 %','转矩最小变化 %','脉动权重','铁损权重','效率变化权重'];
  $('savedScreenResult').innerHTML=`<h3>${esc(v.id)} · 保存的筛选结果</h3><p>${esc(v.dataset.id)} / ${esc(v.dataset.version)} / ${esc(v.dataset.measurement_protocol)} · 基准 ${esc(v.reference)} · 转矩基准 ${esc(v.torque_reference||v.reference)}</p><p>${esc(v.created_utc||'时间未登记')} · ${v.meets_limits_count}/${v.ranked.length} 满足保存配置 · 首选 ${esc(v.best_case||'无')}</p><p class="muted">${esc(v.dataset.scope||'')}</p><dl>${keys.map((k,i)=>`<dt>${names[i]}</dt><dd>${esc(v.config[k])}</dd>`).join('')}</dl>${['analysis.json','ranking.csv','analysis.md','analysis_bundle.zip'].map(n=>`<a class="download" href="/api/workbench/optimization/${v.id}/files/${n}">${n}</a>`).join(' ')}${metricsTable(v.ranked)}`;
  $('savedScreenPanel').hidden=false;$('savedScreenStatus').textContent='只读保存结果；上方当前配置保持不变，未新增筛选或求解。';
 }catch(e){if(token===savedScreenGeneration)$('savedScreenStatus').textContent='保存结果暂不可读取：'+e.message}
 finally{if(token===savedScreenGeneration){savedScreenLoading=false;updateSavedScreenControls()}}
}
function savedSourceKey(value){return value&&typeof value==='object'&&!Array.isArray(value)&&Object.keys(value).length?JSON.stringify(Object.entries(value).sort(([a],[b])=>a.localeCompare(b))):null}
async function reuseSavedScreen(){
 if(!savedScreen||savedScreenLoading||reusingScreen||analysisPending||preparingPlan)return;
 const saved=savedScreen,token=savedScreenGeneration,context=screeningContext();reusingScreen=true;updateScreeningButtons();$('savedScreenStatus').textContent='正在核对保存配置的数据集、基准与来源…';
 try{const fresh=await api('datasets/'+encodeURIComponent(saved.dataset.id));
  if(token!==savedScreenGeneration)return;
  if(context!==screeningContext()){$('savedScreenStatus').textContent='读取期间选择或配置已变更，未覆盖当前输入。';return}
  if(fresh.id!==saved.dataset.id||fresh.version!==saved.dataset.version||fresh.measurement_protocol!==saved.dataset.measurement_protocol||!savedSourceKey(saved.dataset.source_hashes)||savedSourceKey(saved.dataset.source_hashes)!==savedSourceKey(fresh.source_hashes)||!fresh.references?.some(r=>r.case===saved.reference)||!fresh.references.some(r=>r.case===(saved.torque_reference||saved.reference)))throw Error('当前数据集、协议、来源或基准与保存记录不符；保留旧预览，请重新选择数据集');
  if(!Array.from($('dataset').options||[]).some(o=>o.value===fresh.id))$('dataset').innerHTML+=`<option value="${esc(fresh.id)}">${esc(fresh.id)} · 保存记录</option>`;
  ++loadGeneration;++listGeneration;current=fresh;$('dataset').value=fresh.id;$('refresh').disabled=false;$('datasetScope').textContent=fresh.version+' · '+fresh.measurement_protocol+'；'+fresh.scope;
  $('reference').innerHTML=fresh.references.map(r=>`<option value="${esc(r.case)}">${esc(r.material||r.case)} · ${esc(r.case)}</option>`).join('');$('reference').value=saved.reference;for(const k of keys)$(k).value=String(saved.config[k]);referenceChanged();$('error').hidden=true;
  $('analysisStatus').textContent='已载入 '+saved.id+' 的配置；尚未重新筛选或计算。';$('savedScreenStatus').textContent='已沿用此配置；下方仍是原保存结果。需要新的筛选时请点击上方“保存筛选与排序”。';
 }catch(e){if(token===savedScreenGeneration)$('savedScreenStatus').textContent='未沿用配置：'+e.message}
 finally{reusingScreen=false;updateScreeningButtons()}
}
async function analyze(){
 if(reusingScreen||analysisPending||!current?.references.length||current.id!==$('dataset').value)return;
 const context=screeningContext();analysisPending=true;updateScreeningButtons();$('error').hidden=true;$('analysisStatus').textContent='正在保存当前数据集、基准和配置的筛选…';
 try{const data={dataset:current.id,reference:$('reference').value,config:configValues()},v=await api('analyze',data);
  if(v.dataset?.id!==data.dataset||v.reference!==data.reference||keys.some(k=>v.config?.[k]!==data.config[k]))throw Error('保存筛选的来源、基准或配置与请求不符，未展示');
  if(context===screeningContext()){$('result').hidden=false;$('result').innerHTML=`<h3>${v.meets_limits_count}/${v.ranked.length} 满足配置 · 首选 ${esc(v.best_case||'无')}</h3><p class="muted">${esc(v.id)} · ${esc(v.dataset.id)} / ${esc(v.dataset.version)} / ${esc(v.dataset.measurement_protocol)} · 比较基准 ${esc(v.reference)} · 转矩基准 ${esc(v.torque_reference||v.reference)} · 原结果保留，新增求解 0</p>${Object.entries(v.files).map(([n,url])=>`<a class="download" href="${esc(url)}">${esc(n)}</a>`).join(' ')}${metricsTable(v.ranked)}`;$('analysisStatus').textContent='当前选择与配置的筛选已保存。'}
  else $('analysisStatus').textContent='先前配置的筛选已保存到历史；当前选择已改变，未混入当前结果区。';
  try{await history()}catch(e){$('analysisStatus').textContent+=' 历史列表刷新失败，可稍后重开已有记录。'}
 }catch(e){if(context===screeningContext()){error(e);$('analysisStatus').textContent='本次筛选未展示，请检查错误。'}else $('analysisStatus').textContent='先前配置的筛选请求未完成：'+e.message+'；当前选择保持不变。'}
 finally{analysisPending=false;updateScreeningButtons()}
}
let planCpuOnly=true;
function configValues(){const config=Object.fromEntries(keys.map(k=>[k,Number($(k).value)]));if(keys.some(k=>$(k).value.trim()===''||!Number.isFinite(config[k])))throw Error('请填写完整的有限数值配置');return config}
async function planCatalog(){const c=await api('plan-catalog');planCpuOnly=c.cpu_only;planAvailable=c.available;updateScreeningButtons();updatePlanButtons();$('planMaterials').innerHTML=c.materials.map(m=>`<label><input class="planMaterial" type="checkbox" value="${esc(m)}" ${m.includes('M-6 Carlite')||m.includes('23ZDKH75')?'checked':''} onchange="planReferenceChoices()"> ${esc(m)}</label>`).join('')||esc(c.reason);planReferenceChoices()}
function planReferenceChoices(){const old=$('planReference').value,materials=[...document.querySelectorAll('.planMaterial:checked')].map(e=>e.value);$('planReference').innerHTML=materials.map(m=>`<option>${esc(m)}</option>`).join('');if(materials.includes(old))$('planReference').value=old}
async function planRecords(){const token=++planGeneration,v=await api('plans');if(token!==planGeneration)return;const names={prepared:'已准备，尚未提交',watching:'等待任务完成与自动筛选',completed:'自动筛选完成',needs_attention:'需要检查'};$('plans').innerHTML=v.length?v.map(p=>`<div class="stage"><strong>${esc(p.id)} · ${esc(names[p.status]||p.status)}</strong><p>${esc(p.job_id||'')} · ${esc((p.materials||[]).join(' / '))} · 基准 ${esc(p.reference_material||'')}</p>${p.error?`<p class="notice">${esc(p.error)}</p>`:''}${p.status==='prepared'?`<button data-plan-id="${esc(p.id)}" ${planCpuOnly||pendingPlanIds.has(p.id)?'disabled':''} onclick="submitPlan('${p.id}')">提交此计划的求解</button>`:''}${p.analysis_id?`<a class="download" href="/api/workbench/optimization/${p.analysis_id}/files/analysis_bundle.zip">下载自动筛选包</a> · ${p.meets_limits_count} 案满足配置 · 首选 ${esc(p.best_case||'无')}`:''}</div>`).join(''):'暂无扫描计划'}
async function preparePlan(bind){
 const btn=$(bind?'bindPlan':'preparePlan');if(preparingPlan||btn.disabled)return;preparingPlan=true;updateScreeningButtons();
 try{const data={config:configValues()};if(bind){if(!current?.id.startsWith('scan_'))throw Error('请先在上方选择一份已完成的扫描任务');data.job_id=current.id;data.reference_material=current.references.find(r=>r.case===$('reference').value)?.material}else{data.materials=[...document.querySelectorAll('.planMaterial:checked')].map(e=>e.value);data.reference_material=$('planReference').value;data.cores=Number($('planCores').value);data.measurement_protocol=$('planProtocol').value}await api('plans',data);await planRecords();await history()}
 catch(e){error(e)}finally{preparingPlan=false;updateScreeningButtons()}
}
function updatePlanButtons(){for(const button of document.querySelectorAll('[data-plan-id]'))button.disabled=planCpuOnly||pendingPlanIds.has(button.dataset.planId)}
async function submitPlan(id){
 if(planCpuOnly||pendingPlanIds.has(id))return;pendingPlanIds.add(id);updatePlanButtons();
 try{await api('plans/'+id+'/submit',{});await planRecords()}catch(e){error(e)}finally{pendingPlanIds.delete(id);updatePlanButtons()}
}
async function pollPlans(){try{await planRecords()}catch(e){$('plans').textContent='计划读取暂时失败：'+e.message}finally{setTimeout(pollPlans,5000)}}
if(typeof document!=='undefined'){
Promise.allSettled([planCatalog(),planRecords()]).then(results=>{results.forEach(r=>{if(r.status==='rejected')error(r.reason)});setTimeout(pollPlans,5000)});
Promise.allSettled([workflow(),refreshDatasets(),history()]).then(results=>results.forEach((r,i)=>{if(r.status==='rejected'){ $(['workflow','datasetScope','history'][i]).textContent='暂时不可读取：'+r.reason.message;error(r.reason)}}));
}
