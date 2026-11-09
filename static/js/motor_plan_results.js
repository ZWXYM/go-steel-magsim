/* One explicit click collects a completed original plan; no solver route. */
let planResultPending=false;
const planResultNode=id=>document.getElementById(id);
const planResultEsc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function updatePlanResultButtons(){for(const b of document.querySelectorAll('[data-plan-result]'))b.disabled=planResultPending}
async function planResultApi(url,body){
 const r=await fetch(url,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
 const v=await r.json();if(!r.ok)throw Error(v.error||'计划结果读取失败');return v;
}
async function collectPlanResult(id){
 if(planResultPending||!/^plan_[a-f0-9]{12}$/.test(id))return;
 planResultPending=true;updatePlanResultButtons();planResultNode('planResultPanel').hidden=false;
 planResultNode('planResultStatus').textContent='正在整理 '+id+' 的原计划、官方CSV及保存筛选…';planResultNode('planResultContent').innerHTML='';
 try{
  const base='/api/workbench/optimization/plans/'+id+'/result-package',p=await planResultApi(base),s=p.summary;
  if(!p.available)throw Error(p.reason||'该计划尚无可整理的完成结果');
  if(!/^[a-f0-9]{64}$/.test(p.source_binding_sha256)||s?.plan_id!==id||!/^scan_[a-f0-9]{12}$/.test(s.dataset)||!/^opt_[a-f0-9]{12}$/.test(s.analysis_id))throw Error('返回的计划或结果来源身份不符');
  const v=await planResultApi(base,{source_binding_sha256:p.source_binding_sha256});
  if(v.plan_id!==id||v.id!=='presult_'+p.source_binding_sha256.slice(0,16)||v.analysis_id!==s.analysis_id||v.dataset!==s.dataset||v.summary?.binding?.plan_id!==id||!/^pack_[a-f0-9]{16}$/.test(v.package_id)||v.package_url!=='/api/workbench/system-data/packages/'+v.package_id+'/download'||!/^[a-f0-9]{64}$/.test(v.package_sha256))throw Error('工作包与所选计划或结果来源不符，未展示下载');
  const e=planResultEsc,r=v.summary;
  planResultNode('planResultContent').innerHTML=`<h3>${e(id)}</h3><p>${e(r.dataset)} / ${e(r.model_version)} / ${e(r.measurement_protocol)}</p><p>基准 ${e(r.reference_material)} · ${e(r.reference_case)}；${e(r.row_count)} 案保存结果，${e(r.saved_accepted_count)} 案通过原判据；原筛选 ${e(r.meets_limits_count)} 案满足配置，首选 ${e(r.best_case||'无')}。</p><p class="muted">${e(r.scope)} ${e(r.data_usage?.note||'')}</p><p><a class="download" href="${e(v.package_url)}">下载完整官方结果包</a> <a href="/system-data">在数据中心重开</a></p><p class="muted">${e(v.package_members)} 文件；SHA-256 ${e(v.package_sha256)}</p>`;
  planResultNode('planResultStatus').textContent=(v.created?'已整理并登记到数据中心。':'已重开同一来源的工作包。')+'原计划、CSV及筛选保持；新增求解和分析均为0。';
 }catch(e){planResultNode('planResultStatus').textContent='本次未展示工作包：'+e.message}
 finally{planResultPending=false;updatePlanResultButtons()}
}
