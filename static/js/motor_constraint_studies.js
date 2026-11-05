(function(root){
 'use strict';if(!root.document)return;
 const $=id=>root.document.getElementById(id),base='/api/workbench/optimization/',valid=id=>/^csens_[a-f0-9]{12}$/.test(id);
 const filenames=['study_bundle.zip','configurations.csv','case_details.csv','study.json','source_dataset.json','manifest.json'];
 let input=null,loading=false,busy=false,generation=0;
 function node(tag,text){const value=root.document.createElement(tag);if(text!==undefined)value.textContent=String(text);return value}
 function actions(){
  $('sensitivityDataset').disabled=busy;$('sensitivityReference').disabled=busy||loading||!input;
  $('sensitivityRun').disabled=busy||loading||!input||!$('sensitivityReference').value;$('sensitivityRefresh').disabled=busy;
  for(const button of $('sensitivityHistory').querySelectorAll('button'))button.disabled=busy;
 }
 async function api(path,payload){const response=await fetch(base+path,payload===undefined?{method:'GET',cache:'no-store'}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}),data=await response.json();if(!response.ok)throw Error(data.error||'读取失败');return data}
 function render(report){
  if(report.schema!=='motor_constraint_study_v1'||!valid(report.id))throw Error('研究记录身份不符');
  $('sensitivityResult').hidden=false;$('sensitivityResultTitle').textContent=report.id+' · '+report.dataset;
  $('sensitivityResultScope').textContent=report.model_version+' / '+report.measurement_protocol+' · 比较基准 '+report.reference+' · 转矩基准 '+report.torque_reference+'。'+report.scope+' '+report.data_usage.note;
  $('sensitivitySummary').textContent=report.configuration_count+' 组配置 × '+report.source_case_count+' 条原案例，'+report.configurations_with_feasible_case+' 组有可行案；原默认约束下 '+report.original_default_candidate_count+' 案满足。没有新增独立材料或原生求解。';
  $('sensitivitySource').textContent='源数据哈希：'+report.source_hashes_sha256;
  $('sensitivityDownloads').replaceChildren();
  for(const name of filenames){const link=node('a',name);link.className='download';link.href=base+'sensitivity/'+report.id+'/files/'+name;$('sensitivityDownloads').append(link)}
  $('sensitivityRows').replaceChildren();
  for(const row of report.configurations){const tr=node('tr');for(const text of [row.id+(row.is_original_default?' · 原默认':''),row.config.min_ripple_reduction_pct,row.config.min_torque_change_pct,row.config.max_iron_loss_increase_pct,row.meets_limits_count,row.best_case||'无'])tr.append(node('td',text));$('sensitivityRows').append(tr)}
  const url=new URL(root.location.href);url.searchParams.set('study',report.id);root.history.replaceState(null,'',url.pathname+url.search);
 }
 async function history(){
  const rows=await api('sensitivity');$('sensitivityHistory').replaceChildren();
  if(!rows.length)$('sensitivityHistory').append(node('li','暂无保存研究。'));
  for(const row of rows){const li=node('li');$('sensitivityHistory').append(li);if(!row.readable||!valid(row.id)){li.append(node('span',row.id+' · 文件待检查：'+(row.error||'身份不符')));continue}
   const button=node('button','打开 '+row.id);button.addEventListener('click',()=>open(row.id));li.append(button,node('span',row.dataset+' · '+row.configurations_with_feasible_case+'/'+row.configuration_count+'组可行 · 原默认'+row.original_default_candidate_count+'案满足'))}
  actions();
 }
 async function open(id){if(busy||!valid(id))return;busy=true;actions();$('sensitivityActionStatus').textContent='正在读取保存研究…';$('sensitivityResult').hidden=true;
  try{const data=await api('sensitivity/'+id);if(data.id!==id)throw Error('返回了其他研究');render(data);$('sensitivityActionStatus').textContent='已重开原记录，没有重新评分。'}catch(error){$('sensitivityActionStatus').textContent='暂不可读取：'+error.message}finally{busy=false;actions()}}
 async function load(){
  const id=$('sensitivityDataset').value,token=++generation;input=null;loading=true;$('sensitivityReference').replaceChildren(node('option','请先选择有效数据集'));$('sensitivityReference').children[0].value='';actions();
  if(!id){loading=false;$('sensitivityInputStatus').textContent='请选择已有数据集。';actions();return}
  $('sensitivityInputStatus').textContent='正在读取所选来源…';
  try{const data=await api('sensitivity/inputs/'+encodeURIComponent(id));if(token!==generation||id!==$('sensitivityDataset').value)return;if(data.dataset.id!==id)throw Error('数据集身份不符');input=data;
   $('sensitivityReference').replaceChildren();for(const ref of data.dataset.references){const option=node('option',ref.material+' · '+ref.case);option.value=ref.case;$('sensitivityReference').append(option)}
   $('sensitivityReference').value=data.dataset.default_reference||'';$('sensitivityInputStatus').textContent=data.dataset.version+' / '+data.dataset.measurement_protocol+' · '+data.dataset.rows.length+'条保存案例。'+data.dataset.scope+' '+data.dataset.data_usage.note;
  }catch(error){if(token===generation){input=null;$('sensitivityInputStatus').textContent='暂不可读取：'+error.message}}finally{if(token===generation){loading=false;actions()}}
 }
 async function run(){
  if(busy||loading||!input||!$('sensitivityReference').value)return;
  const payload={dataset:input.dataset.id,reference:$('sensitivityReference').value,source_hashes_sha256:input.source_hashes_sha256};
  busy=true;actions();$('sensitivityResult').hidden=true;$('sensitivityActionStatus').textContent='正在按固定48组条件读取和计算…';
  try{const report=await api('sensitivity',payload);if(report.dataset!==payload.dataset||report.reference!==payload.reference||report.source_hashes_sha256!==payload.source_hashes_sha256)throw Error('研究来源或基准与请求不符');render(report);$('sensitivityActionStatus').textContent='研究已独立保存，原默认配置和旧结果保持。';try{await history()}catch(error){$('sensitivityActionStatus').textContent+=' 记录列表暂不可读取：'+error.message}}
  catch(error){$('sensitivityActionStatus').textContent='未确认保存：'+error.message+'；可先刷新记录，不自动重发。'}finally{busy=false;actions()}
 }
 $('sensitivityDataset').addEventListener('change',load);$('sensitivityReference').addEventListener('change',actions);$('sensitivityRun').addEventListener('click',run);
 $('sensitivityRefresh').addEventListener('click',async()=>{if(busy)return;busy=true;actions();try{await history()}catch(error){$('sensitivityActionStatus').textContent='记录列表暂不可读取：'+error.message}finally{busy=false;actions()}});
 (async()=>{
  try{const rows=await api('datasets');$('sensitivityDataset').replaceChildren(node('option','请选择数据集'));$('sensitivityDataset').children[0].value='';for(const row of rows){const option=node('option',row.title);option.value=row.id;$('sensitivityDataset').append(option)}$('sensitivityInputStatus').textContent='请选择已有数据集。'}catch(error){$('sensitivityInputStatus').textContent='数据目录不可读：'+error.message}
  try{await history()}catch(error){$('sensitivityActionStatus').textContent='记录列表不可读：'+error.message}
  actions();const id=new URL(root.location.href).searchParams.get('study');if(id){if(valid(id))await open(id);else $('sensitivityActionStatus').textContent='网址中的研究编号无效。'}
 })();
})(typeof window==='object'?window:globalThis);
