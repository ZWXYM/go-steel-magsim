/* CPU-only plans and clearly identified existing official references. */
const BHA={generation:0,referenceGeneration:0,plan:null,references:[]};
const bha$=id=>document.getElementById(id);
const bhaText=(tag,text)=>{const node=document.createElement(tag);node.textContent=text;return node};
async function bhaAPI(url,body){const response=await fetch(url,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const data=await response.json();if(!response.ok)throw Error(data.error||'读取失败');return data}
function bhaRenderPlan(plan){
 BHA.plan=plan;const labels={awaiting_native_material_import:'计划已保存，等待原生材料导入',ready_for_model_preparation:'原生导入通过，可准备分析工程',model_snapshot_ready_not_submitted:'分析工程已复制，尚未提交求解'};
 bha$('planStatus').textContent=plan.id+' · '+(labels[plan.status]||plan.status);const dl=bha$('identity');dl.replaceChildren();
 for(const [label,value] of [['材料',plan.material_name],['导入记录',plan.native_import_id],['训练样品',plan.training_grades.join('、')],['排除样品',plan.excluded_grades.join('、')||'无'],['磁场轴','物理 H / A·m⁻¹，H_scale=1'],['材料求解结果','见下方独立执行任务；既有参考单独显示']])dl.append(bhaText('dt',label),bhaText('dd',value));
 bha$('prepareModel').disabled=plan.status!=='ready_for_model_preparation';bha$('planBundle').href='/api/workbench/calibrated-motor/analysis-plans/'+plan.id+'/bundle';
 const p=plan.requested_protocol;bha$('protocol').textContent=`计划：${p.speed_rpm} rpm · ${p.cycles}周期 · ${p.source_samples}点 · 取${p.selected_window_ms.join('–')} ms。请求配置尚未应用到AEDT，无新增求解。`;
 bha$('provenance').textContent=JSON.stringify({parent_bank_sha256:plan.parent_bank_sha256,effective_bank_sha256:plan.effective_bank_sha256,measurement_protocol:plan.measurement_protocol,requested_protocol:plan.requested_protocol,result_available:plan.result_available,efficiency_enabled:plan.efficiency_enabled,optimization_ranking_enabled:plan.optimization_ranking_enabled,input_hashes:plan.input_hashes},null,2);
}
async function bhaLoadPlan(){const generation=++BHA.generation;bha$('planError').textContent='';try{const plan=await bhaAPI('/api/workbench/calibrated-motor/analysis-plans/'+document.body.dataset.artifact);if(generation===BHA.generation){bhaRenderPlan(plan);bhaLoadExecutions()}}catch(error){if(generation===BHA.generation){bha$('planError').textContent=error.message;bha$('prepareModel').disabled=true;bha$('prepareExecution').disabled=true}}}
async function bhaPrepareModel(){bha$('prepareModel').disabled=true;bha$('planError').textContent='';try{await bhaAPI('/api/workbench/calibrated-motor/analysis-plans/'+BHA.plan.id+'/prepare-model',{});await bhaLoadPlan()}catch(error){bha$('planError').textContent=error.message}}
function bhaPlot(metrics,elementId='torque'){
 const svg=bha$(elementId);svg.replaceChildren();svg.setAttribute('viewBox','0 0 900 300');
 const t=metrics.time_ms,y=metrics.torque_Nm,x0=Math.min(...t),x1=Math.max(...t),lo=Math.min(0,...y),hi=Math.max(...y,lo+.01);
 const x=v=>60+810*(v-x0)/(x1-x0||1),yy=v=>250-215*(v-lo)/(hi-lo);
 const add=(tag,attrs,text)=>{const node=document.createElementNS('http://www.w3.org/2000/svg',tag);Object.entries(attrs).forEach(([key,value])=>node.setAttribute(key,value));if(text!==undefined)node.textContent=text;svg.append(node)};
 add('rect',{x:x(metrics.selected_window_ms[0]),y:25,width:x(metrics.selected_window_ms[1])-x(metrics.selected_window_ms[0]),height:225,fill:'#edf2f7'});
 add('path',{d:'M60 25V250H870',stroke:'#64748b',fill:'none'});
 for(const v of [lo,(lo+hi)/2,hi])add('text',{x:5,y:yy(v)+4,'font-size':12},v.toFixed(3));
 for(const v of [x0,(x0+x1)/2,x1])add('text',{x:x(v),y:272,'font-size':12,'text-anchor':'middle'},v.toFixed(2)+' ms');
 add('polyline',{points:t.map((v,i)=>`${x(v)},${yy(y[i])}`).join(' '),stroke:'#2466ad','stroke-width':2,fill:'none'});
 for(let i=0;i<t.length;i++)add('circle',{cx:x(t[i]),cy:yy(y[i]),r:2,fill:'#2466ad'});
}
function bhaRenderReference(record){
 const m=record.metrics;bha$('referenceStatus').textContent=`既有参考：${record.dataset} / ${record.case} / ${record.material} · ${record.model_version} · ${m.measurement_protocol}。不是当前校准材料结果。`;
 const out=bha$('metrics');out.replaceChildren();for(const [label,value,suffix] of [['平均转矩',m.T_avg_Nm,'N·m'],['最低转矩',m.T_min_Nm,'N·m'],['最高转矩',m.T_max_Nm,'N·m'],['转矩脉动',m.K_T_ripple_pct,'%']]){const cell=document.createElement('div');cell.append(bhaText('span',label),bhaText('strong',value===null?'—':value.toFixed(6)),bhaText('small',suffix));out.append(cell)}
 bha$('rawCSV').hidden=false;bha$('rawCSV').href=record.torque_csv_url;bhaPlot(m);
}
async function bhaLoadReference(){const generation=++BHA.referenceGeneration,selected=BHA.references[Number(bha$('reference').value)];if(!selected)return;bha$('referenceError').textContent='';bha$('metrics').replaceChildren();bha$('torque').replaceChildren();bha$('rawCSV').hidden=true;
 try{const record=await bhaAPI('/api/workbench/calibrated-motor/torque-reference/'+selected.dataset+'/'+selected.case);if(generation===BHA.referenceGeneration)bhaRenderReference(record)}catch(error){if(generation===BHA.referenceGeneration)bha$('referenceError').textContent=error.message}
}
async function bhaCatalog(){try{const data=await bhaAPI('/api/workbench/waveforms/catalog');const select=bha$('reference');select.replaceChildren();BHA.references=[];for(const d of data.datasets)for(const c of d.cases)if(c.available){const option=bhaText('option',d.id+' / '+c.id+' / '+c.material);option.value=String(BHA.references.length);select.append(option);BHA.references.push({dataset:d.id,case:c.id})}if(BHA.references.length)bhaLoadReference();else bha$('referenceStatus').textContent='当前没有可读取的完整官方CSV。'}catch(error){bha$('referenceError').textContent=error.message}}
async function bhaExecutionAction(url,body){bha$('executionError').textContent='';try{await bhaAPI(url,body);await bhaLoadExecutions()}catch(error){bha$('executionError').textContent=error.message}}
function bhaActual(record){
 const saved=record.result,m=saved.metrics;bha$('actualPanel').hidden=false;bha$('actualStatus').textContent=`${record.id} / ${saved.material_name} · ${saved.model_version} · ${saved.measurement_protocol}`;
 const out=bha$('actualMetrics');out.replaceChildren();for(const [label,key,suffix] of [['平均转矩','T_avg_Nm','N·m'],['最低转矩','T_min_Nm','N·m'],['最高转矩','T_max_Nm','N·m'],['转矩脉动','K_T_ripple_pct','%']]){const cell=document.createElement('div');const value=m[key];cell.append(bhaText('span',label),bhaText('strong',value===null?'—':value.toFixed(6)),bhaText('small',suffix));out.append(cell)}
 bhaPlot(m,'actualTorque');const links=bha$('actualLinks');links.replaceChildren();
 for(const [label,path] of [['原转矩CSV','case/reports/Torque Plots.csv'],['转矩汇总CSV','summary.csv'],['结果JSON','result.json']]){const link=bhaText('a',label);link.href='/api/workbench/calibrated-motor/executions/'+record.id+'/files/'+path;links.append(link)}
}
async function bhaLoadExecutions(){
 const generation=BHA.executionGeneration=(BHA.executionGeneration||0)+1;if(BHA.executionTimer)clearTimeout(BHA.executionTimer);
 bha$('prepareExecution').disabled=true;bha$('executionError').textContent='';
 const results=await Promise.allSettled([bhaAPI('/api/workbench/calibrated-motor/executions'),bhaAPI('/api/workbench/resources')]);if(generation!==BHA.executionGeneration)return;
 const [rowsResult,resourceResult]=results;
 if(rowsResult.status!=='fulfilled'){bha$('executionError').textContent=rowsResult.reason.message;return}
 const canStart=resourceResult.status==='fulfilled'&&!resourceResult.value.cpu_only;
 if(resourceResult.status!=='fulfilled')bha$('executionError').textContent='执行资源状态暂不可读；任务仍可查看。';
 const rows=rowsResult.value.filter(r=>r.analysis_plan_id===document.body.dataset.artifact||r.status==='invalid'),out=bha$('executions');out.replaceChildren();
 bha$('prepareExecution').disabled=!(BHA.plan&&BHA.plan.status==='model_snapshot_ready_not_submitted');
 const labels={prepared:'已冻结，尚未执行',prepared_source_changed:'执行程序已更新，保留此记录',starting:'正在启动',running:'正在分析',completed:'官方转矩已整理',completed_after_budget:'超预算完成，需检查',failed:'失败，现场保留',needs_attention:'需检查现场，不自动重试',invalid:'文件核验失败'};
 for(const r of rows){const row=document.createElement('p');row.append(bhaText('span',r.id+' · '+(labels[r.status]||r.status)+(r.phase?' · '+r.phase:'')+' '));if(r.error)row.append(bhaText('span',r.error,'error'));
 if(r.status==='prepared'){const start=bhaText('button','执行一次转矩分析');start.disabled=!canStart;start.onclick=()=>{start.disabled=true;bhaExecutionAction('/api/workbench/calibrated-motor/executions/'+r.id+'/start',{})};row.append(start)}
 if(r.status!=='invalid'){const pack=bhaText('a','下载执行包');pack.href='/api/workbench/calibrated-motor/executions/'+r.id+'/bundle';row.append(pack)}
 if(r.result_available){const view=bhaText('button','查看本材料转矩');view.onclick=()=>bhaActual(r);row.append(view)}out.append(row)}
 if(!rows.length)out.append(bhaText('p','当前计划没有执行任务；需先完成原生材料导入与分析工程准备。','muted'));
 const available=rows.find(r=>r.result_available);
 if(available){bha$('planNotice').textContent='本材料已有独立执行任务的官方转矩结果。仅整理转矩，效率和最终优化排名保持禁用。';bhaActual(available)}
 else{bha$('actualPanel').hidden=true;bha$('planNotice').textContent='当前校准材料尚无可用电机转矩结果。先完成原生导入和分析工程准备，再手动执行；本页不自动启动求解。'}
 if(rows.some(r=>r.status==='starting'||r.status==='running'))BHA.executionTimer=setTimeout(bhaLoadExecutions,3000);
}
if(typeof document!=='undefined'){bha$('refreshPlan').onclick=bhaLoadPlan;bha$('prepareModel').onclick=bhaPrepareModel;bha$('loadReference').onclick=bhaLoadReference;bha$('reference').onchange=bhaLoadReference;
 bha$('prepareExecution').onclick=()=>bhaExecutionAction('/api/workbench/calibrated-motor/executions',{analysis_plan:document.body.dataset.artifact});bha$('refreshExecutions').onclick=bhaLoadExecutions;bhaLoadPlan();bhaCatalog()}
