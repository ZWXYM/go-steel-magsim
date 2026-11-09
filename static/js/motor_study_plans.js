/* Prepare only: the existing optimization page owns explicit native submission. */
(function(root){
 'use strict';if(!root.document)return;
 const $=id=>root.document.getElementById(id),base='/api/workbench/optimization/';
 let active=null,current=null,loading=false,pending=false,generation=0;
 const inputs=()=>Array.from($('studyPlanCases').querySelectorAll('input:checked'));
 function node(tag,text){const value=root.document.createElement(tag);if(text!==undefined)value.textContent=String(text);return value}
 function link(text,url){const value=node('a',text);value.className='download';value.href=url;return value}
 function controls(){const blocked=loading||pending||!current?.available;$('studyPlanCases').disabled=blocked;$('studyPlanCores').disabled=blocked;$('prepareStudyPlan').disabled=blocked||!inputs().length;$('refreshStudyPlan').disabled=loading||pending||!active}
 async function api(path,payload){const response=await fetch(base+path,payload===undefined?{method:'GET',cache:'no-store'}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}),data=await response.json();if(!response.ok)throw Error(data.error||'读取失败');return data}
 async function open(id){
  const token=++generation,checked=new Set(active===id?inputs().map(input=>input.value):[]);active=/^sapply_[a-f0-9]{16}$/.test(id)?id:null;current=null;loading=true;controls();$('studyPlanCases').replaceChildren(node('legend','复算候选（基准自动加入）'));$('studyPlanResult').replaceChildren();
  if(!active){loading=false;$('studyPlanStatus').textContent='先按所选研究配置保存或重开一份筛选。';controls();return}
  $('studyPlanStatus').textContent='正在读取原库牌号映射与当前模板…';
  try{const data=await api('study-applications/'+id+'/scan-plan');if(token!==generation)return;
   if(data.application_id!==id)throw Error('扫描来源与当前筛选不符');current=data;
   if(!data.available){$('studyPlanStatus').textContent='暂不可准备：'+(data.reason||'当前没有可行候选');return}
   if(!/^[a-f0-9]{64}$/.test(data.source_binding_sha256)||!Array.isArray(data.recipe?.candidates))throw Error('原扫描来源格式不符');
   for(const row of data.recipe.candidates){const label=node('label'),input=node('input');input.type='checkbox';input.value=row.case_id;input.checked=checked.has(row.case_id);input.addEventListener('change',controls);label.append(input,node('span',' '+row.material+' · '+row.case_id));$('studyPlanCases').append(label,node('br'))}
   $('studyPlanStatus').textContent='基准 '+data.recipe.reference.material+'；沿用配置 '+data.recipe.configuration_id+'，协议 '+data.recipe.measurement_protocol+'。当前模板：'+data.recipe.template+'。请选择复算候选。';
  }catch(error){if(token===generation){current=null;$('studyPlanStatus').textContent='暂不可准备：'+error.message}}
  finally{if(token===generation){loading=false;controls()}}
 }
 async function prepare(){
  if(loading||pending||!current?.available)return;
  const cases=inputs().map(input=>input.value),cores=Number($('studyPlanCores').value);
  if(!cases.length||cases.length>15||!Number.isInteger(cores)||cores<1||cores>32){$('studyPlanStatus').textContent='请选择1–15个候选及1–32个计算核。';return}
  const id=active,source=current,token=generation;pending=true;controls();$('studyPlanResult').replaceChildren();$('studyPlanStatus').textContent='正在准备原扫描计划与来源工作包…';
  try{const record=await api('study-applications/'+id+'/scan-plan',{source_binding_sha256:source.source_binding_sha256,cases,cores});if(token!==generation)return;
   if(record.application_id!==id||!/^splan_[a-f0-9]{16}$/.test(record.id)||!/^plan_[a-f0-9]{12}$/.test(record.plan_id)||!/^scan_[a-f0-9]{12}$/.test(record.job_id)||!/^opt_[a-f0-9]{12}$/.test(record.source_analysis_id)||!/^pack_[a-f0-9]{16}$/.test(record.package_id))throw Error('返回计划与当前筛选身份不符');
   $('studyPlanStatus').textContent=(record.created?'已准备 ':'已重开原计划 ')+record.plan_id+' · '+record.materials.join('、')+' · 当前状态 '+record.current_plan_status+'。创建时未下发求解，请在原优化页明确开始。';
   $('studyPlanResult').append(link('查看原计划与开始入口','/motor-optimization?analysis='+record.source_analysis_id+'#plans'),link('下载计划与来源工作包','/api/workbench/system-data/packages/'+record.package_id+'/download'),link('在数据中心查看','/system-data'));
  }catch(error){if(token===generation)$('studyPlanStatus').textContent='未确认准备：'+error.message+'。可先查看原计划列表与数据中心，不自动重发。'}
  finally{pending=false;controls()}
 }
 $('prepareStudyPlan').addEventListener('click',prepare);$('studyPlanCores').addEventListener('input',controls);$('refreshStudyPlan').addEventListener('click',()=>{if(!loading&&!pending&&active)return open(active)});
 root.addEventListener('go-steel-study-selected',()=>open(null));root.addEventListener('go-steel-study-application-selected',event=>open(event.detail?.id));controls();
})(typeof window==='object'?window:globalThis);
