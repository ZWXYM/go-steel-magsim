(function(){
 let accepted=null,epoch=0,pending=false;
 const $=id=>document.getElementById(id);
 function node(tag,text){const n=document.createElement(tag);if(text!==undefined)n.textContent=String(text);return n}
 function field(container,label,value){container.append(node('dt',label),node('dd',value??'—'))}
 function render(report){
  const runtime=$('reportRuntime');runtime.replaceChildren();const info=report.runtime?.value||{};
  for(const [label,value] of [['报告时间',report.created_utc],['当前地址',report.web_address],['程序目录',info.project_path],['资源目录',info.root_path],['任务存储',report.storage_root],['主平台目录',report.workspace_at_capture],['Python',report.python?.executable],['启动版本',info.source_fingerprint],['磁盘版本',info.current_source_fingerprint]])field(runtime,label,value);
  field(runtime,'源码状态',!report.runtime?.ok?report.runtime?.error:info.restart_required?'源码已变化，请重新运行启动器':info.source_matches_startup===true?'与启动版本一致':'未提供版本核对');
  field(runtime,'运行模式',info.cpu_only===true?'该Web为CPU预览':info.cpu_only===false?'普通模式，可按任务配置使用GPU与原生求解':'未知');
  field(runtime,'许可TCP',!report.license_TCP?.ok?report.license_TCP?.error:report.license_TCP.value?.available===true?'可连接；具体求解仍由AEDT申请许可':'不可连接；仍可查看和整理已有结果');
  const groups=$('reportGroups');groups.replaceChildren();
  for(const group of report.record_groups){
   const section=node('section');section.append(node('h3',group.label),node('p',`${group.record_count} 条记录 · ${group.readable_count} 条可读${group.limit_reached?' · 仅列出前1000条':''}`));
   if(group.read_error)section.append(node('p','读取失败：'+group.read_error));
   const table=node('table'),head=node('tr');for(const label of ['记录','保存状态','状态文件','读取/任务错误'])head.append(node('th',label));table.append(head);
   for(const row of group.records){const tr=node('tr');tr.append(node('td',row.id||row.directory_id),node('td',row.status||'未记录状态'),node('td',row.state_file),node('td',row.read_error||row.error||'—'));table.append(tr)}
   const scroll=node('div');scroll.className='scroll';scroll.append(table);section.append(scroll);groups.append(section);
  }
  $('reportIdentity').textContent='当前报告摘要：'+report.report_sha256;
 }
 async function refresh(){
  const current=++epoch;pending=true;$('reportRefresh').disabled=true;$('reportDownload').disabled=true;$('reportStatus').textContent='正在读取当前实例与保存状态…';
  try{
   const response=await fetch('/api/workbench/system-report'),report=await response.json();if(current!==epoch)return;
   if(!response.ok||report.schema!=='go_steel_system_report_v1'||!Array.isArray(report.record_groups)||!report.record_groups.every(group=>group&&Array.isArray(group.records)&&group.records.every(row=>row&&typeof row==='object'))||!(/^[a-f0-9]{64}$/).test(report.report_sha256||''))throw Error(report.error||'报告格式不可读');
   render(report);accepted=JSON.parse(JSON.stringify(report));$('reportStatus').textContent='报告已读取，下载将保存当前显示的快照。';
  }catch(error){if(current===epoch)$('reportStatus').textContent='读取失败：'+error.message+(accepted?'；保留上一次报告。':'')}
  finally{if(current===epoch){pending=false;$('reportRefresh').disabled=false;$('reportDownload').disabled=!accepted}}
 }
 function download(){
  if(pending||!accepted)return;
  const url=URL.createObjectURL(new Blob([JSON.stringify(accepted,null,2)],{type:'application/json;charset=utf-8'})),link=node('a');link.href=url;link.download='go_steel_system_report_'+accepted.report_sha256.slice(0,12)+'.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
 }
 $('reportRefresh').addEventListener('click',refresh);$('reportDownload').addEventListener('click',download);refresh();
})();
