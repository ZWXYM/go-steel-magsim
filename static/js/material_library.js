/* Saved data only: no calibration, prediction or native submission. */
const ML={records:[],key:null,generation:0};
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
function mlRender(v){
 ML.detail=v;ml$('preparedMotor').replaceChildren();mlLoadPreparations(v);
 ml$('detail').hidden=false;ml$('title').textContent=mlLabels[v.kind]+' · '+v.material_name;
 ml$('scope').textContent=v.evidence_scope==='same_material_fit'?'同样品参考修正展示；其拟合误差不能作为泛化误差。':v.excluded_grades.length?'校准训练已完整排除所列牌号；此包保留原留出/预测结果，不代表新增外部实验验证。':'此预测使用全部训练样品，未执行材料留出；若输入为已知样品，结果仍属于同样品标定。';
 const dl=ml$('identity');dl.replaceChildren();
 for(const [label,value] of [['校准记录',v.calibration_id],['父校准库',v.parent_bank_sha256],['实际有效库',v.effective_bank_sha256],['训练样品',v.training_grades.join('、')],['排除样品',v.excluded_grades.join('、')||'无'],['物理 H','A/m · H_scale=1'],['文件核验',v.integrity_scope==='saved_export_hash_and_curve'?'原导出哈希 + 保存曲线':'保存曲线 + 原生输入哈希（未保存原导出哈希）']]){dl.append(mlText('dt',label),mlText('dd',value))}
 for(const [id,url] of [['bundle',v.bundle_url],['amat',v.download_urls['material.amat']],['metadata',v.download_urls['metadata.json']],['csv',v.curves_url]])ml$(id).href=url;
 for(const d of ['RD','TD'])mlPlot(d,v.curves[d]);
 const table=document.createElement('table');const head=document.createElement('tr');['文件','大小 / B','SHA256'].forEach(t=>head.append(mlText('th',t)));table.append(head);
 for(const [name,f] of Object.entries(v.files)){const row=document.createElement('tr'),td=document.createElement('td'),a=mlText('a',name);a.href=v.download_urls[name];td.append(a);row.append(td,mlText('td',f.bytes),mlText('td',f.sha256,'hash'));table.append(row)}ml$('files').replaceChildren(table);
 ml$('provenance').textContent=JSON.stringify({prediction_scope:v.prediction_scope,loss_status:v.loss_status,independently_validated:v.independently_validated,new_predictions:v.new_predictions,new_native_solves:v.new_native_solves},null,2);
}
async function mlOpen(key){
 const generation=++ML.generation;ML.key=key;ml$('detail').hidden=true;ml$('error').textContent='';
 document.querySelectorAll('.entry').forEach(e=>e.setAttribute('aria-pressed',String(e.dataset.key===key)));
 try{const v=await mlApi('/api/workbench/material-library/'+ml$('calibration').value+'/'+encodeURIComponent(key));if(generation===ML.generation)mlRender(v)}catch(e){if(generation===ML.generation)ml$('error').textContent=e.message}
}
function mlEntries(){
 ++ML.generation;ML.key=null;ml$('detail').hidden=true;ml$('error').textContent='';const out=ml$('entries');out.replaceChildren();
 const r=ML.records.find(r=>r.id===ml$('calibration').value);if(!r){ml$('status').textContent='尚无保存材料，请先在校准工作台建立记录。';return}
 ml$('status').textContent=r.error||`${r.calibration_version||'旧版本'} · ${r.entries.length} 个保存条目 · 可打开 ${r.entries.filter(e=>e.available).length} 个`;
 for(const e of r.entries){const b=mlText('button',(mlLabels[e.kind]||e.kind)+' · '+e.label,'entry');b.dataset.key=e.key;b.disabled=!e.available;b.setAttribute('aria-pressed','false');b.append(mlText('small',e.available?(e.excluded_grades.length?'排除 '+e.excluded_grades.join('、'):'全库标定'):e.reason));b.onclick=()=>mlOpen(e.key);out.append(b)}
 const first=r.entries.find(e=>e.available);if(first)mlOpen(first.key);
}
async function mlRefresh(){
 ++ML.generation;ml$('detail').hidden=true;ml$('error').textContent='';ml$('refresh').disabled=true;
 try{const v=await mlApi('/api/workbench/material-library');ML.records=v.records;const s=ml$('calibration'),old=s.value;s.replaceChildren();for(const r of v.records){const o=mlText('option',r.id+(r.error?' · 读取异常':''));o.value=r.id;s.append(o)}if(v.records.some(r=>r.id===old))s.value=old;mlEntries()}catch(e){ml$('error').textContent=e.message}finally{ml$('refresh').disabled=false}
}
if(typeof document!=='undefined'){ml$('refresh').onclick=mlRefresh;ml$('calibration').onchange=mlEntries;mlRefresh()}

async function mlPrepareMotor(){
 const selected=ML.detail,button=ml$('prepareMotor');button.disabled=true;ml$('error').textContent='';
 try{const r=await fetch('/api/workbench/calibrated-motor',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({calibration:selected.calibration_id,key:selected.key,cores:Number(ml$('motorCores').value)})});const v=await r.json();if(!r.ok)throw Error(v.error||'准备失败');
 if(ML.detail!==selected)return; const out=ml$('preparedMotor');out.replaceChildren(mlText('p',v.id+' · 已冻结材料与工程副本 · 待原生导入'));
 for(const name of ['motor.aedt','package.zip','manifest.json']){const a=mlText('a',name+' ');a.href='/api/workbench/calibrated-motor/'+v.id+'/files/'+name;out.append(a)}
 }catch(e){ml$('error').textContent=e.message}finally{button.disabled=false}
}
if(typeof document!=='undefined')ml$('prepareMotor').onclick=mlPrepareMotor;

async function mlLoadPreparations(selected){
 try{const rows=await mlApi('/api/workbench/calibrated-motor');if(ML.detail!==selected)return;const out=ml$('preparedMotor');out.replaceChildren();
 for(const r of rows){if(r.status==='invalid'){out.append(mlText('p',r.id+' · '+r.error,'error'));continue}
 if(r.calibration_id!==selected.calibration_id||r.material_key!==selected.key)continue;
 const row=document.createElement('p');row.append(mlText('span',r.id+' · '+r.created_utc+' · 待原生导入 '));
 for(const name of ['motor.aedt','package.zip','manifest.json']){const a=mlText('a',name+' ');a.href='/api/workbench/calibrated-motor/'+r.id+'/files/'+name;row.append(a)}out.append(row)}
 }catch(e){if(ML.detail===selected)ml$('preparedMotor').textContent='准备记录暂时不可读：'+e.message}
}
