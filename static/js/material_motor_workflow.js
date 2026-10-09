/* Read-only chain browsing. All native actions stay in their existing pages. */
const MWF={generation:0,snapshot:null,selected:null,timer:null};
const mwf$=id=>document.getElementById(id);
const mwfText=(tag,text,cls)=>{const node=document.createElement(tag);node.textContent=text;if(cls)node.className=cls;return node};
const mwfLabels={awaiting_native_material_import:'已准备，等待实际导入',ready:'等待手动导入',ready_source_changed:'程序变化，保留旧副本',starting:'正在启动',running:'正在执行',imported_not_solved:'导入与预检通过',ready_for_model_preparation:'可复制分析工程',model_snapshot_ready_not_submitted:'分析工程已复制',prepared:'执行任务已冻结',prepared_source_changed:'执行程序变化',completed:'官方转矩已整理',completed_after_budget:'超预算完成，需检查',failed:'失败，现场保留',needs_attention:'需要检查',invalid:'文件核验失败'};
function mwfLink(text,href){const a=mwfText('a',text);a.href=href;return a}
function mwfKey(row){return row.calibration_id+' / '+row.material_key}
function mwfDetail(row){
 MWF.selected=mwfKey(row);mwf$('detail').hidden=false;mwf$('detailTitle').textContent=row.material_name||row.label;
 mwf$('scope').textContent=`${row.calibration_id} · ${row.material_key} · ${row.excluded_grades?.length?'完整排除 '+row.excluded_grades.join('、'):'未排除训练样品'}。既有参考曲线不计入本材料求解结果。`;
 const out=mwf$('branches');out.replaceChildren();
 if(!row.branches.length)out.append(mwfText('p',row.error||'此材料尚无电机副本。','muted'));
 for(const branch of row.branches){const section=document.createElement('section'),stages=mwfText('div','','stages');
 for(const [name,label] of [['preparation','电机副本'],['native_import','原生材料导入'],['analysis_plan','转矩分析工程'],['execution','独立执行与结果']]){const r=branch.records[name],cell=mwfText('div','','stage');cell.append(mwfText('strong',label),mwfText('span',r?(mwfLabels[r.status]||r.status)+(r.phase?' · '+r.phase:''):'尚无记录'));if(r){cell.append(mwfText('span',r.id));if(r.error)cell.append(mwfText('span',r.error,'error'))}stages.append(cell)}section.append(stages);
 const next=branch.next_step;section.append(mwfLink(next.label,next.href));if(next.blocked_reason)section.append(mwfText('p',next.blocked_reason,'error'));
 if(branch.result_available){const metrics=mwfText('div','','metrics');for(const [label,key,unit] of [['平均转矩','T_avg_Nm','N·m'],['转矩脉动','K_T_ripple_pct','%']]){const cell=document.createElement('div'),v=branch.torque[key];cell.append(mwfText('span',label),mwfText('strong',v===null?'—':v.toFixed(6)+' '+unit));metrics.append(cell)}section.append(metrics,mwfText('p','仅来自此链路执行记录的官方转矩；效率和最终排名禁用。','muted'))}out.append(section)}
 const details=document.createElement('details'),source=mwfText('pre',JSON.stringify({parent_bank_sha256:row.parent_bank_sha256,effective_bank_sha256:row.effective_bank_sha256,training_grades:row.training_grades,excluded_grades:row.excluded_grades,H_axis:row.H_axis,H_scale:row.H_scale},null,2));details.append(mwfText('summary','材料来源'),source);out.append(details);
}
function mwfRenderRows(){
 if(!MWF.snapshot)return;const out=mwf$('materials');out.replaceChildren();let count=0;
 const query=mwf$('search').value.trim().toLowerCase(),prepared=mwf$('preparedOnly').checked;
 for(const row of MWF.snapshot.materials){if(prepared&&!row.branches.length)continue;if(query&&!JSON.stringify(row).toLowerCase().includes(query))continue;count++;
 const tr=document.createElement('tr'),name=document.createElement('td'),progress=document.createElement('td'),next=document.createElement('td'),open=mwfText('button',row.material_name||row.label);
 open.onclick=()=>mwfDetail(row);name.append(open,mwfText('p',mwfKey(row),'muted'));progress.append(mwfText('p',row.branches.length?`${row.branches.length} 条已保存链路 · ${row.branches.filter(b=>b.result_available).length} 份实际转矩`:'尚无电机副本','muted'));
 if(row.error)progress.append(mwfText('p',row.error,'error'));next.append(mwfLink(row.next_step.label,row.next_step.href));if(row.next_step.blocked_reason)next.append(mwfText('p',row.next_step.blocked_reason,'error'));tr.append(name,progress,next);out.append(tr)}mwf$('empty').hidden=count!==0;
}
function mwfRender(snapshot){
 MWF.snapshot=snapshot;const resource=snapshot.resources,available=resource.license.available===true;
 mwf$('resource').textContent=(resource.cpu_only?'CPU预览，可整理和准备；实际原生操作需普通实例。':'普通实例。')+(available?' 当前许可TCP可连接；尚不代表已取得实际求解许可。':' 当前许可服务不可连接，已有任务保留，导入和分析尚未下发。')+(resource.license.checked_servers?.length?' 配置：'+resource.license.checked_servers.join('；'):'');
 mwf$('totals').replaceChildren();for(const [label,key] of [['保存材料','materials'],['可读材料','available_materials'],['已关联链路','linked_branches'],['实际转矩结果','actual_torque_results']])mwf$('totals').append(mwfText('span',`${label} ${snapshot.totals[key]}`,'pill'));
 mwf$('checked').textContent='读取时间：'+snapshot.checked_utc+'。快照只读，不创建任务或改变旧记录。';
 const issues=mwf$('issues');issues.replaceChildren();mwf$('issuesPanel').hidden=!snapshot.issues.length;
 for(const issue of snapshot.issues)issues.append(mwfText('p',(issue.id||issue.stage)+' · '+issue.error,'error'));
 mwfRenderRows();if(!MWF.selected&&MWF.requested){const row=snapshot.materials.find(r=>r.branches.some(b=>Object.values(b.records).some(v=>v.id===MWF.requested)));if(row)MWF.selected=mwfKey(row)}
 const selected=snapshot.materials.find(row=>mwfKey(row)===MWF.selected);if(selected)mwfDetail(selected);else mwf$('detail').hidden=true;
}
async function mwfLoad(){
 const generation=++MWF.generation;if(MWF.timer)clearTimeout(MWF.timer);mwf$('refresh').disabled=true;mwf$('error').textContent='';
 try{const response=await fetch('/api/workbench/material-motor/workflows');const value=await response.json();if(!response.ok)throw Error(value.error||'读取失败');if(generation!==MWF.generation)return;mwfRender(value);
 if(value.resources.pending_native_tasks.some(r=>r.status==='starting'||r.status==='running'))MWF.timer=setTimeout(mwfLoad,5000);
 }catch(error){if(generation===MWF.generation){mwf$('error').textContent='进度暂不可读，保留上次快照：'+error.message;mwf$('checked').textContent='上次快照，当前读取失败；请刷新后确认最新状态。'}}finally{if(generation===MWF.generation)mwf$('refresh').disabled=false}
}
if(typeof document!=='undefined'){const query=typeof location!=='undefined'?new URLSearchParams(location.search):new URLSearchParams();MWF.requested=query.get('record');if(MWF.requested)mwf$('search').value=MWF.requested;mwf$('refresh').onclick=mwfLoad;mwf$('search').oninput=mwfRenderRows;mwf$('preparedOnly').onchange=mwfRenderRows;mwfLoad()}
