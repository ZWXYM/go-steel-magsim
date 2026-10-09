/* CPU-only plans and clearly identified existing official references. */
const BHA={generation:0,referenceGeneration:0,plan:null,references:[]};
const bha$=id=>document.getElementById(id);
const bhaText=(tag,text)=>{const node=document.createElement(tag);node.textContent=text;return node};
async function bhaAPI(url,body){const response=await fetch(url,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const data=await response.json();if(!response.ok)throw Error(data.error||'读取失败');return data}
function bhaRenderPlan(plan){
 BHA.plan=plan;const labels={awaiting_native_material_import:'计划已保存，等待原生材料导入',ready_for_model_preparation:'原生导入通过，可准备分析工程',model_snapshot_ready_not_submitted:'分析工程已复制，尚未提交求解'};
 bha$('planStatus').textContent=plan.id+' · '+(labels[plan.status]||plan.status);const dl=bha$('identity');dl.replaceChildren();
 for(const [label,value] of [['材料',plan.material_name],['导入记录',plan.native_import_id],['训练样品',plan.training_grades.join('、')],['排除样品',plan.excluded_grades.join('、')||'无'],['磁场轴','物理 H / A·m⁻¹，H_scale=1'],['材料求解结果','尚无；以下参考属于既有其他算例']])dl.append(bhaText('dt',label),bhaText('dd',value));
 bha$('prepareModel').disabled=plan.status!=='ready_for_model_preparation';bha$('planBundle').href='/api/workbench/calibrated-motor/analysis-plans/'+plan.id+'/bundle';
 const p=plan.requested_protocol;bha$('protocol').textContent=`计划：${p.speed_rpm} rpm · ${p.cycles}周期 · ${p.source_samples}点 · 取${p.selected_window_ms.join('–')} ms。请求配置尚未应用到AEDT，无新增求解。`;
 bha$('provenance').textContent=JSON.stringify({parent_bank_sha256:plan.parent_bank_sha256,effective_bank_sha256:plan.effective_bank_sha256,measurement_protocol:plan.measurement_protocol,requested_protocol:plan.requested_protocol,result_available:plan.result_available,efficiency_enabled:plan.efficiency_enabled,optimization_ranking_enabled:plan.optimization_ranking_enabled,input_hashes:plan.input_hashes},null,2);
}
async function bhaLoadPlan(){const generation=++BHA.generation;bha$('planError').textContent='';try{const plan=await bhaAPI('/api/workbench/calibrated-motor/analysis-plans/'+document.body.dataset.artifact);if(generation===BHA.generation)bhaRenderPlan(plan)}catch(error){if(generation===BHA.generation){bha$('planError').textContent=error.message;bha$('prepareModel').disabled=true}}}
async function bhaPrepareModel(){bha$('prepareModel').disabled=true;bha$('planError').textContent='';try{await bhaAPI('/api/workbench/calibrated-motor/analysis-plans/'+BHA.plan.id+'/prepare-model',{});await bhaLoadPlan()}catch(error){bha$('planError').textContent=error.message}}
function bhaPlot(metrics){
 const svg=bha$('torque');svg.replaceChildren();svg.setAttribute('viewBox','0 0 900 300');
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
if(typeof document!=='undefined'){bha$('refreshPlan').onclick=bhaLoadPlan;bha$('prepareModel').onclick=bhaPrepareModel;bha$('loadReference').onclick=bhaLoadReference;bha$('reference').onchange=bhaLoadReference;bhaLoadPlan();bhaCatalog()}
