/* Saved catalog/estimate viewer. Loading, selection and downloads never submit a solve. */
const LR={generation:0,data:null,initial:true},lr$=id=>document.getElementById(id),lrText=(tag,text)=>{const n=document.createElement(tag);n.textContent=text;return n};
function lrClear(){for(const id of ['lossPoints','lossAlternates','lossCoefficients','lossSources'])lr$(id).replaceChildren();lr$('lossDetail').hidden=true;lr$('lossError').textContent='';for(const id of ['lossJSON','lossPointsCSV','lossEstimatesCSV','lossZIP']){lr$(id).hidden=true;lr$(id).href=''}}
function lrShow(){
 const row=LR.data?.records.find(r=>r.grade===lr$('lossGrade').value);lr$('lossDetail').hidden=!row;if(!row)return;
 lr$('lossTitle').textContent=row.grade+' · 厂家典型值与原程序估计';lr$('lossInputs').textContent=`公称厚度 ${row.nominal_thickness_mm} mm；旧程序假定密度 ${row.assumed_density_kg_m3} kg/m³，电导率 ${row.assumed_conductivity_S_m} S/m。`;
 const addPoints=(target,points,defaultDoc)=>{const out=lr$(target);out.replaceChildren();for(const point of points){const doc=LR.data.documents[point.document||defaultDoc],tr=document.createElement('tr');tr.append(lrText('td',point.B_T),lrText('td',point.f_Hz),lrText('td',point.P_W_kg),lrText('td',doc.filename+' · PDF第'+doc.pdf_page+'页（印刷页'+doc.printed_page+'）'));out.append(tr)}};
 addPoints('lossPoints',row.points,row.document);addPoints('lossAlternates',row.alternate_catalog_points,row.document);lr$('lossAlternatePanel').hidden=!row.alternate_catalog_points.length;
 const estimate=row.saved_estimate,coeff=lr$('lossCoefficients');coeff.replaceChildren();for(const [name,mass,volume] of [['Kh','Kh_fit_mass','Kh_fit_volumetric'],['Kc','Kc_classical_mass','Kc_classical_volumetric'],['Ke','Ke_fit_mass','Ke_fit_volumetric']]){const tr=document.createElement('tr');tr.append(lrText('td',name),lrText('td',estimate[mass]),lrText('td',LR.data.mass_units[name]),lrText('td',estimate[volume]),lrText('td',LR.data.volumetric_units[name]));coeff.append(tr)}
 lr$('lossFit').textContent=`原拟合RMSE ${estimate.fit_rmse_w_kg} W/kg；原最大绝对误差 ${estimate.fit_max_abs_error_w_kg} W/kg。这是对上表原输入的拟合误差。`;
 const notes=['旧汇总中的AMAT系数 Kh/Kc/Ke：'+['Kh','Kc','Ke'].map(k=>row.original_amat_coefficients[k]).join(' / ')+'。本页不改写材料。'];
 if(new Set(row.points.map(p=>p.f_Hz)).size<2)notes.push('只有一个频率，Kh/Ke分离较弱。');if(estimate.Ke_fit_mass===0)notes.push('旧非负拟合得到Ke=0，原值保留。');if(estimate.warning)notes.push(estimate.warning);
 lr$('lossWarnings').textContent=notes.join(' ');lr$('lossFormula').textContent=LR.data.formula;
 const sources=lr$('lossSources');sources.replaceChildren();for(const [key,doc] of Object.entries(LR.data.documents)){if(key===row.document||row.alternate_catalog_points.some(p=>p.document===key))sources.append(lrText('p',doc.filename+' · SHA256 '+doc.sha256))}
 sources.append(lrText('p','旧汇总 '+LR.data.source_csv.filename+' · SHA256 '+LR.data.source_csv.sha256),lrText('p','当前快照 SHA256 '+LR.data.snapshot_sha256));
 if(typeof history!=='undefined'&&typeof location!=='undefined')history.replaceState(null,'',location.pathname+'?grade='+encodeURIComponent(row.grade));
}
async function lrLoad(){
 const generation=++LR.generation,selected=lr$('lossGrade').value;LR.data=null;lrClear();lr$('lossRefresh').disabled=lr$('lossGrade').disabled=true;lr$('lossStatus').textContent='正在读取已有参考数据…';
 try{const response=await fetch('/api/workbench/material-loss-reference');const data=await response.json();if(generation!==LR.generation)return;if(!response.ok)throw Error(data.error||'读取失败');
  if(data.version!=='four_sample_loss_reference_v1'||!Array.isArray(data.records)||data.efficiency_enabled!==false||data.motor_assignment_performed!==false)throw Error('返回的参考版本或来源身份不符');
  LR.data=data;const select=lr$('lossGrade');select.replaceChildren();const empty=lrText('option','选择样品');empty.value='';select.append(empty);for(const row of data.records){const option=lrText('option',row.grade);option.value=row.grade;select.append(option)}
  let choice=data.records.some(r=>r.grade===selected)?selected:LR.initial?data.records[0]?.grade||'':'';
  if(LR.initial&&typeof location!=='undefined'){const query=new URLSearchParams(location.search),ids=query.getAll('grade');if(ids.length){choice=ids.length===1&&data.records.some(r=>r.grade===ids[0])?ids[0]:'';if(!choice)lr$('lossError').textContent='链接牌号不可用或重复，请从列表选择。'}}
  LR.initial=false;select.value=choice;lrShow();lr$('lossStatus').textContent=`${data.records.length}种样品 · ${data.records.reduce((n,r)=>n+r.points.length,0)}个原参考点；厂家典型值，不是本批样品实测。`;
  for(const [id,format] of [['lossJSON','json'],['lossPointsCSV','points_csv'],['lossEstimatesCSV','estimates_csv'],['lossZIP','zip']]){lr$(id).href='/api/workbench/material-loss-reference?format='+format;lr$(id).hidden=false}
 }catch(error){if(generation===LR.generation){LR.data=null;lrClear();lr$('lossError').textContent=error.message;lr$('lossStatus').textContent='参考数据暂不可读；校准和电机任务保持。'}}
 finally{if(generation===LR.generation)lr$('lossRefresh').disabled=lr$('lossGrade').disabled=false}
}
if(typeof document!=='undefined'){lr$('lossRefresh').onclick=lrLoad;lr$('lossGrade').onchange=()=>{lr$('lossError').textContent='';lrShow()};lrLoad()}
