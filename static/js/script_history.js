(function(){
 let catalog=null,selected=null,generation=0,detailEpoch=0;
 const $=id=>document.getElementById(id),valid=id=>typeof id==='string'&&(/^[A-Za-z0-9_-]{1,80}$/).test(id);
 function node(tag,text){const n=document.createElement(tag);if(text!==undefined)n.textContent=String(text);return n}
 function hideLinks(){for(const id of ['historyTextLog','historyRawLog']){$(id).hidden=true;$(id).removeAttribute('href')}}
 async function api(url,token){const response=await fetch(url,token?{headers:{'X-Magsim-Workspace':token}}:undefined),data=await response.json();if(!response.ok||data.error)throw Error(data.error||'历史记录不可读');return data}
 function render(){
  const table=$('historyRows');table.replaceChildren();
  for(const row of catalog.records){
   const tr=node('tr'),cell=node('td'),button=node('button','查看保存记录');button.disabled=!!row.read_error||!valid(row.id);button.addEventListener('click',()=>view(row.id));
   cell.append(button);tr.append(node('td',row.id),node('td',row.script||'—'),node('td',row.read_error?'读取失败：'+row.read_error:row.status),node('td',row.queued_at||'—'),cell);table.append(tr);
  }
 }
 async function view(id){
  if(!catalog||!valid(id))return;
  selected=id;const current=++detailEpoch,listGeneration=generation,token=catalog.workspace_token;$('historyDetail').hidden=false;$('historyDetailStatus').textContent='正在读取 '+id+'…';hideLinks();
  try{
   const data=await api('/api/jobs/'+encodeURIComponent(id)+'/status?history=1',token);
   if(current!==detailEpoch||listGeneration!==generation||selected!==id||!catalog)return;
   if(data.id!==id||data.read_only_history!==true||!Array.isArray(data.recent_lines||[])||!Array.isArray(data.log_files||[]))throw Error('历史记录身份不一致');
   const fields=$('historyFields');fields.replaceChildren();
   for(const [label,value] of [['作业',data.id],['脚本',data.script],['原工作目录',data.workspace_origin?.path||'未知'],['保存状态',data.status],['所属实例',data.owner?.id||'未知'],['保存时间',data.saved_utc||'未记录'],['修订号',data.revision],['进度',`${data.completed??0} / ${data.total??0}`],['执行错误数',data.failed??0],['历史写入提示',data.journal_error||'—']])fields.append(node('dt',label),node('dd',value??'—'));
   $('historyLog').textContent=(data.recent_lines||[]).join('\n');
   for(const [key,name] of [['historyTextLog','stdout.log'],['historyRawLog','stdout.raw']])if((data.log_files||[]).includes(name)){$(key).href='/api/jobs/'+encodeURIComponent(id)+'/files/'+name+'?workspace_token='+encodeURIComponent(token);$(key).hidden=false}
   $('historyDetailStatus').textContent='只读保存记录，当前进程状态未核对。';
  }catch(error){if(current===detailEpoch&&listGeneration===generation){$('historyDetailStatus').textContent='读取失败：'+error.message;hideLinks()}}
 }
 async function refresh(){
  const current=++generation;++detailEpoch;catalog=null;hideLinks();$('historyRefresh').disabled=true;$('historyStatus').textContent='正在读取当前工作目录…';
  try{
   const data=await api('/api/jobs/history');if(current!==generation)return;
   if(!Array.isArray(data.records)||!(/^[a-f0-9]{64}$/).test(data.workspace_token||''))throw Error('历史列表格式不可读');
   catalog=data;render();$('historyRoot').textContent='历史目录：'+data.history_root;$('historyStatus').textContent=`已读取 ${data.records.length} 条保存记录${data.limit_reached?'（前1000条）':''}，不会自动恢复任务。`;
   if(selected&&data.records.some(r=>r.id===selected&&!r.read_error))await view(selected);else{selected=null;$('historyDetail').hidden=true}
  }catch(error){if(current===generation){$('historyStatus').textContent='读取失败：'+error.message;$('historyRows').replaceChildren();$('historyDetail').hidden=true}}
  finally{if(current===generation)$('historyRefresh').disabled=false}
 }
 $('historyRefresh').addEventListener('click',refresh);refresh();
})();
