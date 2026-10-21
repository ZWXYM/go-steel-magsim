/* Saved materials and explicit, isolated native import; no solve submission. */
const ML={records:[],key:null,generation:0,requestUsed:false};
const mlLabels={fit:'已知样品标定',holdout:'整牌号留出',prediction:'保存的预测'};
const ml$=id=>document.getElementById(id);
async function mlApi(url){const r=await fetch(url);const v=await r.json();if(!r.ok)throw Error(v.error||'读取失败');return v}
function mlText(tag,text,cls){const e=document.createElement(tag);e.textContent=text;if(cls)e.className=cls;return e}
function mlPlot(id,c){
 const svg=ml$(id);svg.replaceChildren();svg.setAttribute('viewBox','0 0 410 250');
 const positive=c.H.filter(h=>h>0),lo=Math.log10(Math.min(...positive)),hi=Math.log10(Math.max(...positive));
 const ymax=Math.max(...c.B,...c.B_raw,...(c.B_reference||[]),.01)*1.05;
 const x=h=>h<=0?42:56+315*(Math.log10(h)-lo)/(hi-lo||1),y=b=>212-185*b/ymax;
 const add=(tag,attrs,text)=>{const e=document.createElementNS('http://www.w3.org/2000/svg',tag);Object.entries(attrs).forEach(([k,v])=>e.setAttribute(k,v));if(text!==undefined)e.textContent=text;svg.append(e);return e};
 add('path',{d:'M42 20V212H385',fill:'none',stroke:'#94a3b8'});
 [0,ymax/2,ymax].forEach(b=>{add('line',{x1:42,y1:y(b),x2:385,y2:y(b),stroke:'#e2e8f0'});add('text',{x:3,y:y(b)+4,'font-size':11},b.toFixed(2))});
 for(const h of [0,...positive.filter((_,i)=>i%Math.max(1,Math.floor(positive.length/4))===0)])add('text',{x:x(h),y:234,'font-size':10,'text-anchor':'middle'},String(h));
 for(const [field,color] of [['B_raw','#94a3b8'],['B_reference','#d68729'],['B','#2466ad']])if(c[field]){
  add('polyline',{points:c.H.map((h,i)=>`${x(h)},${y(c[field][i])}`).join(' '),fill:'none',stroke:color,'stroke-width':field==='B'?2:1.5});
  c.H.forEach((h,i)=>add('circle',{cx:x(h),cy:y(c[field][i]),r:2,fill:color}));
 }
}
function mlSyncActions(){ml$('prepareMotor').disabled=Boolean(ML.nativeBusy||ML.chainBusy);for(const item of ML.nativeButtons||[])item.button.disabled=Boolean(item.baseDisabled||ML.nativeBusy||ML.chainBusy)}
function mlBindNativeAction(button,url,data,selected,baseDisabled=false){ML.nativeButtons??=[];ML.nativeButtons.push({button,baseDisabled});button.onclick=()=>mlNativeAction(url,url.endsWith('/analysis-plans')?{...data,measurement_protocol:ml$('torqueProtocol').value}:data,button,selected);mlSyncActions()}
function mlRender(v){
 ML.nativeButtons=[];mlSyncActions();
 ML.detail=v;ml$('preparedMotor').replaceChildren();ml$('chainStatus').textContent='';ml$('chainOpen').hidden=true;mlLoadPreparations(v);
 ml$('detail').hidden=false;ml$('title').textContent=mlLabels[v.kind]+' · '+(v.display_name||v.material_name);
 ml$('scope').textContent=v.evidence_scope==='same_material_fit'?'同样品参考修正展示；其拟合误差不能作为泛化误差。':v.excluded_grades.length?'校准训练已完整排除所列牌号；此包保留原留出/预测结果，不代表新增外部实验验证。':'此预测使用全部训练样品，未执行材料留出；若输入为已知样品，结果仍属于同样品标定。';
 const dl=ml$('identity');dl.replaceChildren();
 for(const [label,value] of [['输入材料代号',v.source_material_id||'旧记录未提供'],['输入参数',v.input_params?`Goss ${v.input_params.f_Goss} · 偏角 ${v.input_params.theta_0_deg}° · 半宽 ${v.input_params.halfwidth_deg}° · Si ${v.input_params.Si_content}%`:'旧记录未提供'],['参数来源',v.input_parameter_source||'未提供'],['材料文件名',v.material_name],['校准记录',v.calibration_id],['父校准库',v.parent_bank_sha256],['实际有效库',v.effective_bank_sha256],['训练样品',v.training_grades.join('、')],['排除样品',v.excluded_grades.join('、')||'无'],['物理 H','A/m · H_scale=1'],['文件核验',v.integrity_scope==='saved_export_hash_and_curve'?'原导出哈希 + 保存曲线':'保存曲线 + 原生输入哈希（未保存原导出哈希）']]){dl.append(mlText('dt',label),mlText('dd',value))}
 for(const [id,url] of [['bundle',v.bundle_url],['amat',v.download_urls['material.amat']],['metadata',v.download_urls['metadata.json']],['csv',v.curves_url]])ml$(id).href=url;
 for(const d of ['RD','TD'])mlPlot(d,v.curves[d]);
 const table=document.createElement('table');const head=document.createElement('tr');['文件','大小 / B','SHA256'].forEach(t=>head.append(mlText('th',t)));table.append(head);
 for(const [name,f] of Object.entries(v.files)){const row=document.createElement('tr'),td=document.createElement('td'),a=mlText('a',name);a.href=v.download_urls[name];td.append(a);row.append(td,mlText('td',f.bytes),mlText('td',f.sha256,'hash'));table.append(row)}ml$('files').replaceChildren(table);
 ml$('provenance').textContent=JSON.stringify({prediction_scope:v.prediction_scope,loss_status:v.loss_status,independently_validated:v.independently_validated,new_predictions:v.new_predictions,new_native_solves:v.new_native_solves},null,2);
}
async function mlOpen(key){
 const generation=++ML.generation,calibration=ml$('calibration').value;ML.key=key;ML.detail=null;ml$('detail').hidden=true;ml$('error').textContent='';
 document.querySelectorAll('.entry').forEach(e=>e.setAttribute('aria-pressed',String(e.dataset.key===key)));
 try{const v=await mlApi('/api/workbench/material-inputs/'+encodeURIComponent(calibration)+'/'+encodeURIComponent(key));if(generation!==ML.generation)return;
 if(v.calibration_id!==calibration||v.key!==key)throw Error('返回材料与所选校准记录或条目不一致，请刷新目录。');
 mlRender(v);
 if(typeof history!=='undefined'&&typeof location!=='undefined'){const query=new URLSearchParams(location.search);query.set('calibration',calibration);query.set('key',key);history.replaceState(null,'',(location.pathname||'/material-library')+'?'+query+(location.hash||''))}
 }catch(e){if(generation===ML.generation)ml$('error').textContent=e.message}
}
function mlEntries(preferredKey){
 ++ML.generation;ML.key=null;ML.detail=null;ml$('detail').hidden=true;ml$('error').textContent='';const out=ml$('entries');out.replaceChildren();
 const r=ML.records.find(r=>r.id===ml$('calibration').value);if(!r){ml$('status').textContent='尚无保存材料，请先在校准工作台建立记录。';return}
 ml$('status').textContent=r.error||`${r.calibration_version||'旧版本'} · ${r.entries.length} 个保存条目 · 可打开 ${r.entries.filter(e=>e.available).length} 个`;
 for(const e of r.entries){const b=mlText('button',(mlLabels[e.kind]||e.kind)+' · '+e.label,'entry');b.dataset.key=e.key;b.disabled=!e.available;b.setAttribute('aria-pressed','false');b.append(mlText('small',e.available?(e.excluded_grades.length?'排除 '+e.excluded_grades.join('、'):'全库标定'):e.reason));b.onclick=()=>mlOpen(e.key);out.append(b)}
 if(typeof preferredKey==='string'){const selected=r.entries.find(e=>e.key===preferredKey);if(!selected||!selected.available){ml$('error').textContent=selected?.reason||'链接指定的材料条目不存在，请从列表选择。';return}mlOpen(selected.key);return}
 const first=r.entries.find(e=>e.available);if(first)mlOpen(first.key);
}
async function mlRefresh(){
 const generation=ML.catalogGeneration=(ML.catalogGeneration||0)+1;++ML.generation;ml$('detail').hidden=true;ml$('error').textContent='';ml$('refresh').disabled=true;
 try{const v=await mlApi('/api/workbench/material-inputs');if(generation!==ML.catalogGeneration)return;ML.records=v.records;const s=ml$('calibration'),old=s.value,oldKey=ML.key;
 const query=!ML.requestUsed&&typeof location!=='undefined'?new URLSearchParams(location.search):new URLSearchParams();ML.requestUsed=true;
 const requested=query.get('calibration'),key=query.get('key');s.replaceChildren();for(const r of v.records){const o=mlText('option',r.id+(r.error?' · 读取异常':''));o.value=r.id;s.append(o)}
 if(requested){if(!v.records.some(r=>r.id===requested)){ml$('entries').replaceChildren();ml$('error').textContent='链接指定的校准记录不存在，请选择现有记录。';return}s.value=requested;mlEntries(key||undefined)}
 else{if(v.records.some(r=>r.id===old))s.value=old;mlEntries(s.value===old&&oldKey?oldKey:undefined)}
 }catch(e){if(generation===ML.catalogGeneration)ml$('error').textContent=e.message}finally{if(generation===ML.catalogGeneration)ml$('refresh').disabled=false}
}
if(typeof document!=='undefined'){ml$('refresh').onclick=mlRefresh;ml$('calibration').onchange=()=>mlEntries();mlRefresh()}

async function mlPrepareMotor(){
 const selected=ML.detail,button=ml$('prepareMotor');if(!selected||button.disabled||ML.nativeBusy||ML.chainBusy)return;ML.chainBusy=true;mlSyncActions();ml$('error').textContent='';ml$('chainStatus').textContent='正在核对并接续已有记录…';ml$('chainOpen').hidden=true;
 try{const r=await fetch('/api/workbench/material-motor/prepare-or-reuse',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({calibration:selected.calibration_id,key:selected.key,cores:Number(ml$('motorCores').value),measurement_protocol:ml$('torqueProtocol').value})});const v=await r.json();if(!r.ok)throw Error(v.error||'准备失败');
 if(ML.detail!==selected)return;ml$('chainStatus').textContent=`${v.next_step} · 复用 ${v.reused.length} 项，新增 ${v.created.length} 项准备 · 未下发原生任务`;
 ml$('chainOpen').href=v.href;
 if(/^\/calibrated-motor\/analysis\/bhanalysis_[a-f0-9]{12}$/.test(v.href)&&/^bhexec_[a-f0-9]{12}$/.test(v.records.execution?.id))ml$('chainOpen').href+='?execution='+encodeURIComponent(v.records.execution.id);
 ml$('chainOpen').textContent=v.records.analysis_plan?'打开现有分析计划':'查看当前材料记录';ml$('chainOpen').hidden=false;await mlLoadPreparations(selected);
 }catch(e){if(ML.detail===selected){ml$('error').textContent=e.message;ml$('chainStatus').textContent='接续未完成，已保存步骤保留；可检查后重新打开。'}}finally{ML.chainBusy=false;mlSyncActions()}
}
if(typeof document!=='undefined')ml$('prepareMotor').onclick=mlPrepareMotor;

async function mlLoadPreparations(selected){
 const generation=ML.preparationGeneration=(ML.preparationGeneration||0)+1;
 try{const [rows,imports,resources,plans]=await Promise.all([mlApi('/api/workbench/calibrated-motor'),mlApi('/api/workbench/calibrated-motor/native-imports'),mlApi('/api/workbench/resources'),mlApi('/api/workbench/calibrated-motor/analysis-plans')]);if(ML.detail!==selected||generation!==ML.preparationGeneration)return;const out=ml$('preparedMotor');out.replaceChildren();ML.nativeButtons=[];
 for(const r of rows){if(r.status==='invalid'){out.append(mlText('p',r.id+' · '+r.error,'error'));continue}
 if(r.calibration_id!==selected.calibration_id||r.material_key!==selected.key)continue;
 const row=document.createElement('p');row.append(mlText('span',r.id+' · '+r.created_utc+' · 准备副本已冻结 '));
 for(const name of ['motor.aedt','package.zip','manifest.json']){const a=mlText('a',name+' ');a.href='/api/workbench/calibrated-motor/'+r.id+'/files/'+name;row.append(a)}out.append(row);
 const children=imports.filter(c=>c.preparation_id===r.id),ready=mlText('button','准备原生导入');mlBindNativeAction(ready,'/api/workbench/calibrated-motor/native-imports',{preparation:r.id},selected);out.append(ready);
 for(const c of children){const p=document.createElement('p');const labels={ready:'已冻结，等待手动执行',ready_source_changed:'准备后程序已更新，需新副本',starting:'正在启动专用会话',running:'正在导入与预检',imported_not_solved:'导入与预检通过，未求解',failed:'导入失败，保留现场',needs_attention:'需检查现场，不自动重试',invalid:'文件核验失败'};
 p.append(mlText('span',c.id+' · '+(labels[c.status]||c.status)+' '));if(c.error)p.append(mlText('span',c.error,'error'));
 if(c.status==='ready'){const start=mlText('button','执行原生导入与预检');start.disabled=resources.cpu_only||resources.native_submission_allowed===false;start.title=resources.source_restart_required?'源码已更新，请从启动器打开匹配版本':resources.cpu_only?'CPU预览窗口禁止原生执行':'最多1专用会话，0磁场/电机求解';mlBindNativeAction(start,'/api/workbench/calibrated-motor/native-imports/'+c.id+'/start',{},selected,start.disabled);p.append(start)}
 if(c.status==='imported_not_solved'){p.append(mlText('span','RD/TD '+c.summary.material.curve_points.RD+'/'+c.summary.material.curve_points.TD+' 点 · 插片赋值48/48 · 损耗选择48/48 · 效率禁用 '));for(const name of ['motor.aedt','summary.json']){const a=mlText('a',name+' ');a.href='/api/workbench/calibrated-motor/native-imports/'+c.id+'/files/'+name;p.append(a)}}out.append(p);
 const existing=plans.filter(v=>v.native_import_id===c.id);
 if(c.status==='ready'||c.status==='imported_not_solved'){const make=mlText('button','准备转矩分析计划');mlBindNativeAction(make,'/api/workbench/calibrated-motor/analysis-plans',{native_import:c.id,measurement_protocol:ml$('torqueProtocol').value},selected);out.append(make)}
 for(const v of existing){const link=mlText('a','打开转矩分析计划 '+v.id);link.href='/calibrated-motor/analysis/'+v.id;out.append(document.createElement('br'),link)}
 }
 }const refresh=mlText('button','刷新导入状态');refresh.onclick=()=>mlLoadPreparations(selected);out.append(refresh);
 for(const c of imports.filter(c=>c.status==='invalid'))out.append(mlText('p',c.id+' · '+c.error,'error'));
 }catch(e){if(ML.detail===selected&&generation===ML.preparationGeneration)ml$('preparedMotor').textContent='准备记录暂时不可读：'+e.message}
}

async function mlNativeAction(url,data,button,selected){
 if(ML.nativeBusy||ML.chainBusy||button.disabled)return;ML.nativeBusy=true;button.disabled=true;mlSyncActions();ml$('error').textContent='';
 try{const response=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}),result=await response.json();if(!response.ok)throw Error(result.error||'操作失败')}
 catch(error){if(ML.detail===selected)ml$('error').textContent='操作结果未确认：'+error.message+'；请检查原记录，不自动重试。'}
 finally{try{if(ML.detail&&!ml$('detail').hidden)await mlLoadPreparations(ML.detail)}finally{ML.nativeBusy=false;mlSyncActions()}}
}
