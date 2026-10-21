let current=null,samples=[],motorReady=false,jobExports={},cpuOnly=false,calibrationGeneration=0,calibrationListGeneration=0,fitPending=false,predictionPending=false,rawInputGeneration=0,lastErrorScope=null;const $=id=>document.getElementById(id),esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),num=(n,k=4)=>Number.isFinite(n)?n.toFixed(k):'—';
function error(e,scope='global'){lastErrorScope=scope;$('error').textContent=e.message||e;$('error').style.display='block'}
function clearActionError(scope){if(lastErrorScope===scope){$('error').textContent='';$('error').style.display='none';lastErrorScope=null}}
async function api(url,data){const r=await fetch('/api/workbench/'+url,data===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});const v=await r.json();if(!r.ok)throw Error(v.error||'请求失败');return v}
function tab(name,button,updateURL=true){document.querySelectorAll('.panel').forEach(x=>x.classList.remove('active'));$(name).classList.add('active');document.querySelectorAll('.tab').forEach(x=>x.classList.remove('active'));button.classList.add('active');if(updateURL)location.hash=name}
function updatePredictionButton(){$('predictButton').disabled=predictionPending||!current||($('predictionMode').value==='excluded'&&!current.selected_grades?.includes($('excludeGrade').value))}
function clearCalibration(){clearActionError('prediction');current=null;$('calReport').hidden=true;$('calID').textContent='';$('predResult').innerHTML='';$('predStatus').textContent=predictionPending?'先前预测仍在处理；新的校准尚未载入。':'';$('calBundle').hidden=true;$('qualityDownload').hidden=true;updatePredictionButton()}
function calibrationRecipe(){return JSON.stringify([[...document.querySelectorAll('.known:checked')].map(x=>x.value),$('calibrationMethod').value])}
async function calibrationHistory(autoLoad=false){
 const token=++calibrationListGeneration,requestGeneration=calibrationGeneration,list=await api('calibrations');if(token!==calibrationListGeneration)return;
 const selected=$('history').value||current?.id;$('history').innerHTML='<option value="">加载已有校准…</option>'+list.map(v=>`<option value="${esc(v.id)}">${esc(v.id)} · ${v.nested_validation?'跨材料泛化':'原锚点'} · ${esc(v.selected_grades.join(', '))}</option>`).join('');
 if(selected&&list.some(r=>r.id===selected)){$('history').value=selected;return}
 $('history').value='';if(autoLoad&&!current&&requestGeneration===calibrationGeneration){const preferred=new URLSearchParams(location.search).get('calibration');if(preferred&&!list.some(r=>r.id===preferred))throw Error('链接指定的校准库不存在，请重新选择');if(list.length)await loadCalibration(preferred||list[0].id)}
}
async function fit(){
 if(fitPending)return;clearActionError('calibration');const token=++calibrationGeneration,recipe=calibrationRecipe(),btn=$('fitButton');fitPending=true;btn.disabled=true;clearCalibration();$('history').value='';$('calActionStatus').textContent='正在保存所选样品与方法的校准…';
 try{const grades=[...document.querySelectorAll('.known:checked')].map(x=>x.value),v=await api('calibrate',{grades,calibration_version:$('calibrationMethod').value});
  if(!/^cal_[0-9a-f]{12}$/.test(v.id))throw Error('返回校准身份不正确');
  if(token===calibrationGeneration&&recipe===calibrationRecipe()){renderCalibration(v);$('calActionStatus').textContent='已保存并加载校准 '+v.id}
  else $('calActionStatus').textContent='较早选择的校准 '+v.id+' 已保存；当前选择已变更，可在记录中重开。';
  try{await calibrationHistory()}catch(e){$('calActionStatus').textContent+=' 校准列表刷新失败，可稍后重开保存记录。'}
 }catch(e){if(token===calibrationGeneration){clearCalibration();error(e,'calibration');$('calActionStatus').textContent='校准未完成，请检查错误；旧记录保留。'}else $('calActionStatus').textContent='较早校准请求未完成：'+e.message+'；当前选择保持不变。'}
 finally{fitPending=false;btn.disabled=false;updatePredictionButton()}
}
async function loadCalibration(id){
 clearActionError('calibration');const token=++calibrationGeneration;clearCalibration();$('history').value=id||'';$('calActionStatus').textContent=id?'正在加载校准 '+id:'尚未选择校准';if(!id)return;
 try{const v=await api('calibrations/'+encodeURIComponent(id));if(token!==calibrationGeneration)return;if(v.id!==id)throw Error('返回校准与当前选择不一致，未混用');renderCalibration(v);$('calActionStatus').textContent='已加载保存校准 '+v.id}
 catch(e){if(token===calibrationGeneration){clearCalibration();error(e,'calibration');$('calActionStatus').textContent='未加载校准 '+id+'；请选择有效记录。'}}
}
function qualitySummary(q){
 if(!q||!q.verified_native_source)return '原生质量未核实';
 const groups=Object.values(q.directions),count=key=>groups.reduce((n,d)=>n+(d[key]?.length||0),0);
 return '端点失败 '+count('endpoint_failed_grain_ids')+' · 反演失败 '+count('inversion_failed_grain_ids')+' · 收敛记录缺失 '+count('torque_unknown_grain_ids');
}
function renderQuality(v){
 const frozen=Boolean(v.native_quality),qs=v.native_quality||Object.fromEntries(samples.filter(s=>v.selected_grades.includes(s.grade)).map(s=>[s.grade,s.native_quality]));
 $('qualityScope').textContent=(frozen?'本记录已冻结原生来源、逐粒质量和生产代码哈希。':'旧记录未保存质量审计；下面是现存源数据的复查，不改写旧校准记录。')+' 端点与反演失败可重叠；缺收敛记录保持未知，H=0 数值不是实测剩磁。所有晶粒保留，筛查不等于独立材料验证。';
 $('qualityDownload').hidden=!v.native_quality_file;
 if(v.native_quality_file)$('qualityDownload').href=`/api/workbench/files/calibration/${v.id}/${v.native_quality_file}`;
 const ids=x=>x?.length?x.join(', '):'—';
 $('qualityTable').innerHTML='<table><tr><th>材料/方向</th><th>端点失败晶粒</th><th>反演失败晶粒</th><th>最大零场中点 / T</th><th>缺收敛记录晶粒</th></tr>'+Object.entries(qs).flatMap(([g,q])=>q?.directions?Object.entries(q.directions).map(([d,a])=>`<tr><td>${esc(g)} ${esc(d)}</td><td>${esc(ids(a.endpoint_failed_grain_ids))}</td><td>${esc(ids(a.inversion_failed_grain_ids))}</td><td>${num(a.max_abs_unforced_midpoint_H0_T,6)}</td><td>${esc(ids(a.torque_unknown_grain_ids))}</td></tr>`):[`<tr><td>${esc(g)}</td><td colspan="4">原生质量未核实</td></tr>`]).join('')+'</table>';
}
function renderCalibration(v){current=v;if(typeof history!=='undefined'&&typeof location!=='undefined'&&/^cal_[a-f0-9]{12}$/.test(v.id)){const query=new URLSearchParams(location.search);query.set('calibration',v.id);history.replaceState(null,'',(location.pathname||'/workbench')+'?'+query+(location.hash||'#cal'))}$('history').value=v.id;$('excludeGrade').innerHTML=v.selected_grades.map(g=>`<option>${esc(g)}</option>`).join('');if(v.selected_grades.includes('B30P105'))$('excludeGrade').value='B30P105';renderQuality(v);$('calReport').hidden=false;restoreCalibrationView(v);$('calID').textContent=v.id;$('calCSV').href=`/api/workbench/files/calibration/${v.id}/metrics.csv`;$('calBundle').hidden=!v.bundle_file;if(v.bundle_file)$('calBundle').href=`/api/workbench/files/calibration/${v.id}/${v.bundle_file}`;$('fitMetrics').innerHTML=[['原始仿真',v.raw],['已知材料标定',v.calibration_fit],['逐材料留出',v.material_holdout]].map(([name,m])=>`<div class="metric">${name}<strong>${num(m.mean_rmse_T,5)} T</strong><span class="muted">平均 RMSE（H≥100） · 最大 |B800误差| ${num(m.max_abs_B800_error_T,5)} T</span></div>`).join('');$('fitNote').textContent=v.calibration_fit.B800_0_05T_pass_all?'已知样品的 B800 标定误差均小于 0.05 T。其他材料的误差仍需看材料留出检验；'+(v.material_holdout.B800_0_05T_pass_all?'本次留出 B800 均通过门限，仍需独立来源验证。':'当前留出尚未全部达标。'):'部分样品标定误差仍未达到 0.05 T，请查看逐项误差。';$('calTable').innerHTML='<table><tr><th>材料/方向</th><th>原始 RMSE / T</th><th>标定 RMSE / T</th><th>标定 B800误差 / T</th><th>留出 RMSE / T</th><th>留出 B800误差 / T</th></tr>'+v.comparisons.map(c=>{const m=v.metrics.filter(r=>r.grade===c.grade&&r.direction===c.direction);const get=n=>m.find(x=>x.method===n);return `<tr><td>${esc(c.grade)} ${c.direction}</td><td>${num(get('raw').rmse_T)}</td><td>${num(get('calibration_fit').rmse_T)}</td><td>${num(get('calibration_fit').B800_error_T)}</td><td>${num(get(v.nested_validation?'nested_material_holdout':'material_holdout').rmse_T)}</td><td>${num(get(v.nested_validation?'nested_material_holdout':'material_holdout').B800_error_T)}</td></tr>`}).join('')+'</table>';$('transferNote').hidden=!v.nested_validation;if(v.nested_validation){$('transferNote').textContent='既有四牌号嵌套留出：原方法 RMSE '+num(v.legacy_material_holdout.mean_rmse_T,5)+' T；新方法 '+num(v.material_holdout.mean_rmse_T,5)+' T；仅参考对照 '+num(v.reference_only_control.mean_rmse_T,5)+' T。每个牌号的 RD/TD 一起留出，只用其余材料内部选参。当前是试验版，新增批次未验证；成分与织构独立作用尚未识别。'}showCurve();updatePredictionButton()}
function draw(svg,H,series){const W=Math.max(240,svg.clientWidth),Ht=340,L=50,R=28,T=20,D=46,maxH=Math.max(...H),maxB=Math.max(2.1,...series.flatMap(s=>s[1]));const x=h=>L+(W-L-R)*Math.log10(1+h)/Math.log10(1+maxH),y=b=>Ht-D-(Ht-T-D)*b/maxB;svg.setAttribute('viewBox',`0 0 ${W} ${Ht}`);let html='';[0,.5,1,1.5,2].forEach(v=>{html+=`<line x1="${L}" y1="${y(v)}" x2="${W-R}" y2="${y(v)}" stroke="#e3eaf2"/><text x="${L-10}" y="${y(v)+4}" text-anchor="end" font-size="12">${v}</text>`});(W>650?[0,10,100,800,10000,50000]:[0,100,800,50000]).forEach(h=>{html+=`<text x="${x(h)}" y="${Ht-D+22}" font-size="12" text-anchor="middle">${h}</text>`});series.forEach(([color,bs])=>{html+=`<polyline fill="none" stroke="${color}" stroke-width="2.2" points="${H.map((h,i)=>x(h)+','+y(bs[i])).join(' ')}"/>`});html+='<text x="18" y="22" font-size="12">B / T</text><text x="'+W/2+'" y="335" text-anchor="middle" font-size="11">物理 H / A·m⁻¹（log(1+H)）</text>';svg.innerHTML=html}
function restoreCalibrationView(record){
 const query=new URLSearchParams(typeof location!=='undefined'?location.search:'');
 const grade=query.has('curve_grade')?query.get('curve_grade'):record.selected_grades[0],direction=query.has('curve_direction')?query.get('curve_direction'):($('direction').value||'RD');
 const available=record.selected_grades.includes(grade);
 $('grade').innerHTML=(available?'':'<option value="" disabled>请选择材料…</option>')+record.selected_grades.map(g=>`<option>${esc(g)}</option>`).join('');
 $('grade').value=available?grade:'';$('direction').value=['RD','TD'].includes(direction)?direction:'';
}
function showCurve(){
 if(!current)return;const grade=$('grade').value,direction=$('direction').value,c=current.comparisons.find(v=>v.grade===grade&&v.direction===direction);
 const status=$('curveStatus');status.className=c?'muted':'notice';
 if(!c){$('curve').innerHTML='';$('curve').setAttribute('aria-label','未选择可用材料与方向曲线');status.textContent='所选曲线在当前校准中不可用，请选择有效材料和RD/TD方向。';return}
 status.textContent=grade+' · '+direction+' · '+current.id+'。地址可重开当前曲线。';$('curve').setAttribute('aria-label',grade+' '+direction+' 物理H与B曲线');
 draw($('curve'),c.H,[['#94a3b8',c.raw_B],['#162b43',c.reference_B],['#13816f',c.corrected_B],['#d68b27',c.holdout_B]]);
 if(typeof history!=='undefined'&&typeof location!=='undefined'&&/^cal_[a-f0-9]{12}$/.test(current.id)){
  const query=new URLSearchParams(location.search);query.set('calibration',current.id);query.set('curve_grade',grade);query.set('curve_direction',direction);
  history.replaceState(null,'',(location.pathname||'/workbench')+'?'+query+(location.hash||'#cal'));
 }
}
function predictionContext(){return JSON.stringify([calibrationGeneration,current?.id,$('history').value,rawInputGeneration,$('rawPair').value,$('predictionMode').value,$('excludeGrade').value])}
function predictionInputChanged(rawChanged=false){clearActionError('prediction');if(rawChanged)++rawInputGeneration;$('predResult').innerHTML='';$('predStatus').textContent=predictionPending?'输入或范围已变更；之前请求返回后可按当前选择预测。':'输入或预测范围已变更，请应用所选范围。';updatePredictionButton()}
function setPredictionInput(text){$('rawPair').value=text;predictionInputChanged(true)}
function beginPredictionInputRead(){return ++rawInputGeneration}
function applyPredictionInputRead(token,text){if(token!==rawInputGeneration)return false;setPredictionInput(text);return true}
async function example(){clearActionError('prediction');const token=++rawInputGeneration;try{const pair=await api('samples/B30P105/raw-pair');if(token===rawInputGeneration)setPredictionInput(JSON.stringify(pair,null,2))}catch(e){if(token===rawInputGeneration)error(e,'prediction')}}
async function readRaw(input){const file=input.files[0],token=++rawInputGeneration;if(!file)return;clearActionError('prediction');try{const text=await file.text();if(token===rawInputGeneration)setPredictionInput(text)}catch(e){if(token===rawInputGeneration)error(e,'prediction')}}
function parameterSupportSummary(s){if(!s||!s.joint_support_evaluated)return '参数组合覆盖范围未核验';if(s.matched_anchor_grades.length)return '与已知样品参数相同（标定条件）';return s.inside_joint_parameter_support?'参数组合位于样品覆盖范围内，独立准确性待验证':'参数组合超出样品覆盖范围，属于未标定组合'}
function predictionScopeChanged(){$('excludeChoice').hidden=$('predictionMode').value!=='excluded';predictionInputChanged()}
function renderPrediction(v,calibrationID){const base=`/api/workbench/files/calibration/${encodeURIComponent(calibrationID)}/${encodeURIComponent(v.id)}/`;$('predResult').innerHTML=`<p>${esc(v.material_id)} · 校准 ${esc(calibrationID)} · ${esc(v.id)}${v.prediction_scope?.excluded_grades.length?' · 完整排除 '+esc(v.prediction_scope.excluded_grades.join(', '))+(v.prediction_scope.selection_rerun_after_exclusion?'，仅使用剩余材料重新选参':'，仅使用剩余材料预测'):''} · ${esc(parameterSupportSummary(v.parameter_support))}${v.RD.transfer_candidate?(v.RD.prediction_mode==='known_anchor_calibration'?' · 参数命中已知样品，属于标定输出':' · 跨材料试验版，新增批次未验证'):''} · 铁损尚未校准</p><p class="notice">${esc(qualitySummary(v.native_quality))}。数值筛查不认证材料性能；此输出尚不能作正式代理标签。</p><div class="row"><a class="download" href="/material-library?calibration=${encodeURIComponent(calibrationID)}&amp;key=${encodeURIComponent('prediction:'+v.id)}">在材料库打开此预测</a><a class="download" href="${base}curves.csv">修正曲线 CSV</a><a class="download" href="${base}prediction.json">来源与结果 JSON</a><a class="download" href="${base}effective_bank.json">实际使用的校准库</a><a class="download" href="${base}${esc(encodeURIComponent(v.AMAT_file))}">B-H 材料 .amat</a><a class="download" href="${base}${esc(encodeURIComponent(v.AMAT_file.replace(/\.amat$/,'.metadata.json')))}">材料来源与质量记录</a></div>`}
async function predict(){
 if(predictionPending)return;clearActionError('prediction');if(!current){error(Error('请先建立或加载校准'),'calibration');return}
 const calibrationID=current.id,context=predictionContext();predictionPending=true;updatePredictionButton();$('predResult').innerHTML='';$('predStatus').textContent='正在校准 '+calibrationID+' 下保存当前输入的预测…';
 try{const raw=JSON.parse($('rawPair').value),excluded=$('predictionMode').value==='excluded',v=await api(`calibrations/${calibrationID}/${excluded?'predict-excluded':'predict'}`,excluded?{raw_pair:raw,exclude_grades:[$('excludeGrade').value]}:raw);
  if(v.calibration_id!==calibrationID||v.material_id!==raw.material_id||!/^pred_[0-9a-f]{12}$/.test(v.id))throw Error('预测来源与请求的校准或材料身份不符，未展示');
  if(context===predictionContext()){renderPrediction(v,calibrationID);$('predStatus').textContent='预测 '+v.id+' 已保存在校准 '+calibrationID+' 下。'}
  else{const href='/material-library?calibration='+encodeURIComponent(calibrationID)+'&key='+encodeURIComponent('prediction:'+v.id);$('predStatus').innerHTML=`较早输入的预测 ${esc(v.id)} 已保存到校准 ${esc(calibrationID)}；当前选择已变更，<a href="${href}">从原材料记录重开</a>。`}
 }catch(e){if(context===predictionContext()){error(e,'prediction');$('predStatus').textContent='预测未完成或未通过来源核对；已有记录保留。'}else $('predStatus').textContent='较早校准 '+calibrationID+' 的预测未完成：'+e.message+'；当前选择未改变。'}
 finally{predictionPending=false;updatePredictionButton()}
}
function selectMaterials(value){document.querySelectorAll('.material').forEach(x=>x.checked=value)}
let motorRows=[],motorQueueState=null,motorReadHealthy=false,motorJobsGeneration=0,motorPreparePending=false,motorRecoverPending=false,motorArchivePending=false;
const motorPendingActions=new Map();
const states={prepared:'已准备',queued:'排队中',starting:'启动中',running:'正在求解',completed:'完成',completed_with_failures:'完成，含异常项',failed:'失败',rejected:'未通过',needs_attention:'中断待处理',interrupted:'中断，现场保留'};
function motorControls(){$('prepareMotor').disabled=!motorReady||motorPreparePending;$('recoverQueue').disabled=cpuOnly||!motorReady||!motorReadHealthy||motorRecoverPending||motorPendingActions.size>0}
function motorActionDisabled(j,action){return !motorReadHealthy||motorPendingActions.has(j.id)||motorRecoverPending||(action!=='export'&&(cpuOnly||!motorReady))}
function motorButton(j,action,label,secondary=false){const pending=motorPendingActions.has(j.id);return `<button ${secondary?'class="secondary" ':''}${motorActionDisabled(j,action)?'disabled ':''}onclick="${action==='submit'?'submit':action==='continue'?'continueMotor':'exportMotor'}('${j.id}')">${pending?'正在处理本任务…':label}</button>`}
function exportLinks(j){const x=jobExports[j.id],f=x?.files;if(f)return `<a class="download" href="/api/workbench/files/motor/${j.id}/${f['reports_bundle.zip']}">下载完整汇总包</a><a class="download" href="/api/workbench/files/motor/${j.id}/${f['summary.csv']}">下载含验收原因的 CSV</a>`;return j.summary?.bundle_file?`<a class="download" href="/api/workbench/files/motor/${j.id}/${j.summary.bundle_file}">下载完整汇总包</a>`:''}
function renderMotorJobs(){
 motorControls();const q=motorQueueState;
 if(!motorReadHealthy)$('queueInfo').textContent='状态读取未确认；等待刷新后操作';
 else if(q)$('queueInfo').textContent=(q.active_ownership.length?'存在活动或未确认归属的进程；保持等待':'队列可检查')+` · 排队 ${q.queued} · 待处理 ${q.needs_attention}`;
 $('jobs').innerHTML=motorRows.length?motorRows.map(j=>`<div class="job"><div class="row"><strong>${esc(j.id)}</strong><span class="chip ${j.status.includes('fail')||j.status==='needs_attention'?'bad':''}">${esc(states[j.status]||j.status)}</span><span class="muted">${j.measurement_protocol==='periodic_cycle3_v1'?'三周期稳态检查':'单周期对照'}</span><span>${j.completed}/${j.total} · ${esc(j.current||'')}</span>${j.status==='prepared'?motorButton(j,'submit','提交求解队列'):''}${j.status==='needs_attention'&&!j.continuation_job?motorButton(j,'continue','仅继续未执行牌号'):''}${j.summary?`${['completed','completed_with_failures'].includes(j.status)?motorButton(j,'export','生成完整汇总包',true):''}<a class="download" href="/api/workbench/files/motor/${j.id}/summary.csv">下载原汇总 CSV</a><a class="download" href="/api/workbench/files/motor/${j.id}/summary.md">下载原总结</a>`:''}${exportLinks(j)}</div><p class="muted">${j.parent_job?'来源任务 '+esc(j.parent_job)+' · ':''}${j.continuation_job?'后续任务 '+esc(j.continuation_job)+' · ':''}${j.cases.map(c=>esc(c.material)+'：'+esc(states[c.status]||c.status)).join('　')}</p>${j.summary?resultTable(j.summary.results,j.id):resultTable(j.cases.filter(c=>c.metrics).map(c=>c.metrics),j.id)}${j.error?'<p>'+esc(j.error)+'</p>':''}${nativeFailures(j)}<details><summary>任务日志</summary><pre>${esc(j.log_tail||'')}</pre></details></div>`).join(''):'暂无扫描任务';
}
async function jobs(){
 const generation=++motorJobsGeneration;
 try{const [list,q]=await Promise.all([api('motor/jobs'),api('motor/queue')]);if(generation!==motorJobsGeneration)return false;
  if(!Array.isArray(list)||!Array.isArray(q?.active_ownership))throw Error('任务或队列响应格式不符');
  motorRows=list;motorQueueState=q;motorReadHealthy=true;renderMotorJobs();return true;
 }catch(e){if(generation===motorJobsGeneration){motorReadHealthy=false;renderMotorJobs();$('queueInfo').textContent='队列暂时不可访问：'+e.message}throw e}
}
async function prepareMotor(){
 if(motorPreparePending||!motorReady)return;motorPreparePending=true;++motorJobsGeneration;motorControls();
 const data={materials:[...document.querySelectorAll('.material:checked')].map(x=>x.value),cores:parseInt($('cores').value),measurement_protocol:$('measurement').value};
 try{const r=await api('motor/jobs',data);if(!/^scan_[0-9a-f]{12}$/.test(r.id)||r.measurement_protocol!==data.measurement_protocol||!Array.isArray(r.cases)||r.cases.length!==data.materials.length||JSON.stringify(r.cases.map(c=>c.material).sort())!==JSON.stringify([...data.materials].sort()))throw Error('保存任务与请求牌号或测量口径不符');
  $('queueNotice').textContent='已保存扫描任务 '+r.id+' · '+data.materials.join('、')+' · '+data.measurement_protocol+'；尚未提交求解。';await jobs();
 }catch(e){error(e);$('queueNotice').textContent='任务准备结果未确认：'+e.message+'；请先刷新记录，不自动重复生成。'}finally{motorPreparePending=false;motorControls()}
}
async function motorJobAction(id,action){
 const j=motorRows.find(r=>r.id===id);if(!j||motorActionDisabled(j,action))return;
 if(action==='submit'&&j.status!=='prepared'||action==='continue'&&(j.status!=='needs_attention'||j.continuation_job)||action==='export'&&(!j.summary||!['completed','completed_with_failures'].includes(j.status)))return;
 motorPendingActions.set(id,action);++motorJobsGeneration;renderMotorJobs();
 try{const r=await api('motor/jobs/'+encodeURIComponent(id)+'/'+action,{});
  if(action==='submit'){if(r.id!==id)throw Error('返回扫描任务身份不符');$('queueNotice').textContent='已提交任务 '+id+'；按原串行队列执行。'}
  else if(action==='continue'){if(r.parent_job!==id||!/^scan_[0-9a-f]{12}$/.test(r.id))throw Error('后续任务来源不符');$('queueNotice').textContent='已生成后续任务 '+r.id+'；来源 '+id+'，仅包含未尝试牌号，请提交新任务。'}
  else{if(r.job_id!==id||!r.files||['reports_bundle.zip','summary.csv'].some(k=>typeof r.files[k]!=='string'))throw Error('汇总包来源任务不符');jobExports[id]=r;$('queueNotice').textContent='已整理任务 '+id+' 的完整汇总包，原始结果保持。'}
  await jobs();
 }catch(e){motorReadHealthy=false;error(e);$('queueNotice').textContent='任务 '+id+' 操作未确认：'+e.message+'；请刷新或检查原记录，不自动重试。'}
 finally{motorPendingActions.delete(id);renderMotorJobs()}
}
async function submit(id){return motorJobAction(id,'submit')}
async function continueMotor(id){return motorJobAction(id,'continue')}
async function exportMotor(id){return motorJobAction(id,'export')}
async function recoverQueue(){
 motorControls();if($('recoverQueue').disabled)return;motorRecoverPending=true;++motorJobsGeneration;renderMotorJobs();
 try{const r=await api('motor/queue/recover',{});$('queueNotice').textContent=r.status==='busy'?r.reason:`检查完成：${r.needs_attention} 个任务需处理；${r.queued_resumed?'未执行的队列已继续。':'原始结果保留。'}`;await jobs()}
 catch(e){motorReadHealthy=false;error(e);$('queueNotice').textContent='队列检查结果未确认：'+e.message+'；先刷新记录，不自动重试。'}finally{motorRecoverPending=false;renderMotorJobs()}
}
function nativeFailures(j){return (j.native_failure_diagnostics||[]).map(d=>`<details><summary>${esc(d.material)}：${esc(d.reason)}</summary><pre>${esc(d.messages.join('\n'))}</pre><a href="/api/workbench/files/motor/${j.id}/${d.source_file.split('/').map(encodeURIComponent).join('/')}">下载原生错误记录</a><p class="muted">原状态与原生消息保留，未自动重试。</p></details>`).join('')}
function rejectReason(r){if(r.acceptance_reason)return r.acceptance_reason;if(r.periodic_stability&&!r.periodic_stability.passed){const v=r.periodic_stability,t=v.thresholds,parts=[];if(v.max_phase_torque_delta_Nm>t.phase_torque_Nm)parts.push('周期波形差 '+num(v.max_phase_torque_delta_Nm,3)+' N·m > '+t.phase_torque_Nm);if(v.relative_mean_torque_change>t.mean_torque_relative)parts.push('平均转矩未稳定');if(v.relative_core_loss_change>t.core_loss_relative)parts.push('铁损未稳定');return parts.join('；')}if(r.T_min_Nm<=0)return '转矩存在负值或零值';if(r.K_T_ripple_pct>=100)return '脉动超过 100%';return '数值门限未通过'}
function resultTable(rows,jobID=null){return '<div class="tablewrap"><table><tr><th>材料</th><th>数值筛查</th><th>结果范围</th><th>转矩 N·m</th><th>脉动 %</th><th>铁损 W</th><th>效率估计 %</th><th>官方证据</th></tr>'+rows.map(r=>`<tr><td>${esc(r.material||r.case)}</td><td>${r.accepted?'通过':esc(r.error||rejectReason(r))}</td><td>${r.solve_successful?(r.metric_protocol==='periodic_cycle3_v1'?'三周期结果':'单周期结果'):'未生成完整结果'}<details><summary>记录的验证范围</summary>${r.comparison_eligibility?.eligible?'已满足原记录比较门限':esc(r.comparison_eligibility?.reasons?.join('；')||'沿用当前数据来源和测量口径')}</details></td><td>${num(r.T_avg_Nm)}</td><td>${num(r.K_T_ripple_pct,2)}</td><td>${num(r.P_Fe_W,3)}</td><td>${num(r.eta_estimate_pct,2)}</td><td>${jobID&&r.solve_successful?`<a href="/api/workbench/files/motor/${jobID}/${r.case_id}/reports/Torque%20Plots.csv">转矩 CSV</a> · <a href="/api/workbench/files/motor/${jobID}/${r.case_id}/maxwell_reports/CoreLoss.csv">铁损 CSV</a>`:'—'}</td></tr>`).join('')+'</table></div>'}
function archiveTable(rows){return '<table><tr><th>材料 / 版本</th><th>指标来源</th><th>转矩 N·m</th><th>脉动 %</th><th>铁损 W</th><th>效率估计 %</th><th>状态 / 备注</th><th>下载原文件</th></tr>'+rows.map(r=>{const m=r.display_metrics||{},base=`/api/workbench/motor/archive/${encodeURIComponent(r.case)}/files/`,links=Object.keys(r.files||{}).map(name=>`<a href="${base}${name.split('/').map(encodeURIComponent).join('/')}">${esc(name.endsWith('.json')?'原摘要 JSON':name.split('/').pop())}</a>`).join(' · ');return `<tr><td>${esc(r.case)}<br><span class="muted">${esc(r.version)}</span></td><td>${r.csv_verified?'同版本 CSV 复核':'原保存摘要'}</td><td>${num(m.T_avg_Nm)}</td><td>${num(m.K_T_ripple_pct,2)}</td><td>${num(m.P_Fe_W,3)}</td><td>${num(m.eta_estimate_pct,2)}</td><td>${esc(r.reason||(r.metrics?.accepted?'已通过原单周期数值筛查':rejectReason(r.metrics||{})))}</td><td>${links||'—'}</td></tr>`}).join('')+'</table>'}
async function loadArchive(){const rows=await api('motor/archive');$('archive').innerHTML=rows.length?archiveTable(rows):'当前工作区没有 V8_2 final 历史结果';$('exportArchive').disabled=motorArchivePending||!rows.length}
async function exportArchive(){if(motorArchivePending)return;motorArchivePending=true;const btn=$('exportArchive');btn.disabled=true;try{const v=await api('motor/archive/export',{});$('archiveExports').innerHTML=`<a class="download" href="${esc(v.files['reports_bundle.zip'])}">完整汇总与原证据</a> <a class="download" href="${esc(v.files['summary.csv'])}">指标 CSV</a> <a class="download" href="${esc(v.files['summary.md'])}">整理说明</a> <span class="muted">${v.case_count} 案 / ${v.csv_verified_count} 案 CSV 复核</span>`}catch(e){error(e)}finally{motorArchivePending=false;btn.disabled=false}}
async function loadSamples(){const v=await api('samples');samples=v.samples;$('samples').innerHTML=samples.map(s=>`<label class="sample"><input type="checkbox" class="known" value="${esc(s.grade)}" checked> <strong>${esc(s.grade)}</strong><br>fGoss ${s.params.f_Goss} · θ ${s.params.theta_0_deg}°<br>半宽 ${s.params.halfwidth_deg}° · Si ${s.params.Si_content}%<br><span class="muted">报告参数 / 已处理参考</span><br><span class="chip bad">${esc(qualitySummary(s.native_quality))}</span></label>`).join('')}
async function loadMotorCatalog(){const cat=await api('motor/catalog');motorReady=cat.available;motorControls();$('templateInfo').textContent=cat.available?cat.template+' · 48 插片 / 48 局部坐标系'+(cat.license?.available?' · 许可服务可连接':' · 许可服务未连接，仍可准备任务和整理结果'):cat.reason;const physics=cat.model_physics_audit;$('motorPhysics').textContent=physics?`原模板插片铁损覆盖 ${physics.core_loss.insert_enabled_count}/48；新扫描副本启用全部插片铁损。当前库材料沿用标量 B-H；校准 RD/TD 材料接口与独立损耗另行记录。`:'需配置本机模型后才能生成扫描任务；已有校准和历史结果可独立使用';$('materials').innerHTML=cat.materials.map(s=>`<label><input type="checkbox" class="material" value="${esc(s)}" ${s.includes('23ZH90')||s.includes('M-6 Carlite')?'checked':''}> ${esc(s)}</label>`).join('')}
async function init(){try{const r=await api('resources');cpuOnly=r.cpu_only}catch(e){cpuOnly=true;error(e)}$('recoverQueue').disabled=true;$('prepareMotor').disabled=true;$('resourceHold').hidden=!cpuOnly;const tasks=[['样品','samples',loadSamples],['校准记录','calID',()=>calibrationHistory(true)],['电机目录','templateInfo',loadMotorCatalog],['旧电机结果','archive',loadArchive],['扫描队列','queueInfo',jobs]];const results=await Promise.allSettled(tasks.map(([, ,fn])=>fn()));results.forEach((r,i)=>{if(r.status==='rejected'){const [label,id]=tasks[i];$(id).textContent=label+'加载失败：'+r.reason.message;error(r.reason,label==='校准记录'?'calibration':'global')}})}
async function pollJobs(){try{await jobs()}catch{/* jobs reports only the latest read failure. */}finally{setTimeout(pollJobs,4000)}}
function panelFromHash(){const name=location.hash==='#motor'?'motor':'cal';tab(name,$(name+'Tab'),false)}
if(typeof document!=='undefined'){
addEventListener('hashchange',panelFromHash);panelFromHash();
init().then(()=>setTimeout(pollJobs,4000));
}
