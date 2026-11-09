/* An explicit study choice creates an original screening; it never submits a scan. */
(function(root){
 'use strict';if(!root.document)return;
 const $=id=>root.document.getElementById(id),base='/api/workbench/optimization/';
 const validStudy=id=>/^csens_[a-f0-9]{12}$/.test(id);
 let activeId=null,view=null,loading=false,pending=false,generation=0;
 function node(tag,text){const value=root.document.createElement(tag);if(text!==undefined)value.textContent=String(text);return value}
 function controls(){const blocked=loading||pending||!view?.source_matches;$('studyConfiguration').disabled=blocked;$('applyStudyConfig').disabled=blocked||!$('studyConfiguration').value;$('refreshStudyApplication').disabled=pending||loading||!activeId}
 async function api(path,payload){const response=await fetch(base+path,payload===undefined?{method:'GET',cache:'no-store'}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}),data=await response.json();if(!response.ok)throw Error(data.error||'读取失败');return data}
 function link(text,href){const value=node('a',text);value.href=href;value.className='download';return value}
 async function open(id){
  const token=++generation,selected=view?.study_id===id?$('studyConfiguration').value:'';activeId=validStudy(id)?id:null;view=null;loading=true;controls();$('studyApplicationResult').replaceChildren();$('studyConfiguration').replaceChildren(node('option','请先选择研究配置'));$('studyConfiguration').children[0].value='';
  if(!activeId){loading=false;$('studyApplicationStatus').textContent='先打开一份保存研究。';controls();return}
  $('studyApplicationStatus').textContent='正在核对保存研究与当前来源…';
  try{const data=await api('sensitivity/'+id+'/applications');if(token!==generation)return;
   if(data.study_id!==id||!Array.isArray(data.configurations)||!/^([a-f0-9]{64})$/.test(data.source_binding_sha256))throw Error('研究配置来源身份不符');
   view=data;
   for(const row of data.configurations){if(!/^c\d{2}$/.test(row.id))throw Error('研究配置编号不符');const option=node('option',row.id+(row.is_original_default?' · 原默认':'')+' · 脉动至少降'+row.config.min_ripple_reduction_pct+'% / 转矩变化至少'+row.config.min_torque_change_pct+'% / 铁损最多增'+row.config.max_iron_loss_increase_pct+'% · '+row.meets_limits_count+'案满足');option.value=row.id;$('studyConfiguration').append(option)}
   $('studyConfiguration').value=data.configurations.some(row=>row.id===selected)?selected:'';
   $('studyApplicationStatus').textContent=data.source_matches?data.study_id+' · '+data.dataset+' · 基准'+data.reference+'；请选择配置，保存独立筛选。':'来源待检查：'+(data.source_error||'当前来源与研究不符')+'。原研究和已保存结果仍可下载。';
  }catch(error){if(token===generation){view=null;$('studyApplicationStatus').textContent='暂不可应用：'+error.message}}
  finally{if(token===generation){loading=false;controls()}}
 }
 async function apply(){
  if(loading||pending||!view?.source_matches||!$('studyConfiguration').value)return;
  const source=view,configuration=$('studyConfiguration').value,token=generation;
  if(!source.configurations.some(row=>row.id===configuration))return;
  pending=true;controls();$('studyApplicationResult').replaceChildren();$('studyApplicationStatus').textContent='正在用所选条件保存原程序筛选…';
  if(typeof root.CustomEvent==='function')root.dispatchEvent(new root.CustomEvent('go-steel-study-application-selected',{detail:{id:null}}));
  try{const record=await api('sensitivity/'+source.study_id+'/applications',{configuration_id:configuration,source_binding_sha256:source.source_binding_sha256});if(token!==generation)return;
   if(record.study_id!==source.study_id||record.configuration_id!==configuration||record.source_binding_sha256!==source.source_binding_sha256||!/^sapply_[a-f0-9]{16}$/.test(record.id)||!/^opt_[a-f0-9]{12}$/.test(record.analysis_id))throw Error('返回筛选与所选研究配置不符');
   $('studyApplicationStatus').textContent=(record.created?'已保存 ':'已重开已有记录 ')+record.analysis_id+' · 配置'+configuration+' · '+record.meets_limits_count+'案满足 · 首选'+(record.best_case||'无')+'；原默认仍为'+record.original_default_candidate_count+'候选。未启动扫描。';
   const path=base+'study-applications/'+record.id+'/files/';
   $('studyApplicationResult').append(link('查看原优化结果','/motor-optimization?analysis='+record.analysis_id),link('完整配置筛选包',path+'application_bundle.zip'),link('排序 CSV',path+'ranking.csv'),link('来源 JSON',path+'application.json'));
   if(typeof root.CustomEvent==='function')root.dispatchEvent(new root.CustomEvent('go-steel-study-application-selected',{detail:{id:record.id}}));
  }catch(error){if(token===generation)$('studyApplicationStatus').textContent='未确认保存：'+error.message+'。可查看原优化历史，不自动重发。'}
  finally{pending=false;controls()}
 }
 $('studyConfiguration').addEventListener('change',controls);$('applyStudyConfig').addEventListener('click',apply);$('refreshStudyApplication').addEventListener('click',()=>{if(!pending&&!loading&&activeId)return open(activeId)});
 root.addEventListener('go-steel-study-selected',event=>open(event.detail?.id));
 controls();const id=new URL(root.location.href).searchParams.get('study');if(id)open(id);
})(typeof window==='object'?window:globalThis);
