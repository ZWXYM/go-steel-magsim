/* A prepared recipe never starts a GPU process without the separate start action. */
let nmrCurrent=null,nmrAllowed=false,nmrBusy=false,nmrOpening=false,nmrGeneration=0,nmrOpenGeneration=0;
const nmr$=id=>document.getElementById(id);
const nmrNames={preparing:'正在生成脚本',preparation_failed:'准备失败，现场保留',ready:'已准备，未开始仿真',starting:'正在启动',running:'正在串行仿真',paused:'已暂停，结果保留',completed:'原生对已导出',failed:'失败，现场保留',needs_attention:'需要检查记录'};
async function nmrApi(path,data){const r=await fetch('/api/workbench/'+path,data===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});const v=await r.json();if(!r.ok)throw Error(v.error||'请求失败');return v}
function nmrOptions(node,items){const value=node.value;node.replaceChildren();for(const [id,label] of items){const o=document.createElement('option');o.value=id;o.textContent=label;node.append(o)}if(items.some(([id])=>id===value))node.value=value}
function nmrReadRecipe(values){
 const read=id=>values?values[id]:nmr$(id).value,number=(id,label,low,high,integer=false)=>{const text=String(read(id)??'').trim();if(!text)throw Error('请填写'+label);const value=Number(text);if(!Number.isFinite(value)||value<low||value>high||integer&&!Number.isInteger(value))throw Error(label+'需为'+low+'–'+high+'范围内的'+(integer?'整数':'有限数值'));return value};
 const calibration_id=String(read('nmrCalibration')??''),material_id=String(read('nmrMaterial')??'').trim();
 if(!/^cal_[a-f0-9]{12}$/.test(calibration_id))throw Error('请选择保存的校准库');
 if(!material_id)throw Error('请填写材料代号');if(!/^[A-Za-z0-9_-]{1,40}$/.test(material_id))throw Error('材料代号限1–40个英文字母、数字、下划线或连字符');
 return {calibration_id,material_id,params:{f_Goss:number('nmrGoss','Goss比例',0,1),theta_0_deg:number('nmrTheta','偏角',0,45),halfwidth_deg:number('nmrWidth','半宽',.1,60),Si_content:number('nmrSi','Si含量',0,6.5)},thickness_mm:number('nmrThickness','厚度',.05,1),n_grains:number('nmrGrains','每方向晶粒数',1,8,true),seed:number('nmrSeed','随机种子',0,2147483647,true)};
}
function nmrLoadRecipe(){
 if(nmrBusy||nmrOpening||!nmrBound())return;const recipe=nmrCurrent.recipe,p=recipe.params||{},values={nmrCalibration:recipe.calibration_id,nmrMaterial:recipe.material_id,nmrGoss:p.f_Goss,nmrTheta:p.theta_0_deg,nmrWidth:p.halfwidth_deg,nmrSi:p.Si_content,nmrThickness:recipe.thickness_mm,nmrGrains:recipe.n_grains,nmrSeed:recipe.seed};
 try{nmrReadRecipe(values);if(!Array.from(nmr$('nmrCalibration').children).some(o=>o.value===recipe.calibration_id))throw Error('本任务的校准库不在当前目录中，请先恢复对应校准记录');for(const [id,value] of Object.entries(values))nmr$(id).value=String(value);nmr$('nmrMessage').textContent='已载入 '+nmrCurrent.id+' 保存的全部参数；可修改后生成独立新任务，当前任务保持。'}catch(error){nmr$('nmrMessage').textContent='未载入参数：'+error.message+'；当前表单与任务保持。'}
}
function nmrBound(){return nmrCurrent&&nmrCurrent.id===nmr$('nmrHistory').value}
function nmrControls(){const r=nmrCurrent,bound=nmrBound();nmr$('nmrHistory').disabled=nmrBusy;nmr$('nmrLoadRecipe').disabled=nmrBusy||nmrOpening||!bound;nmr$('nmrPrepare').disabled=nmrBusy||nmrOpening||!nmr$('nmrCalibration').value;nmr$('nmrStart').disabled=nmrBusy||nmrOpening||!bound||!nmrAllowed||!r.producer_compatible||!['ready','paused'].includes(r.status)||(r.elapsed_seconds||0)>=120;nmr$('nmrStart').textContent=r?.status==='paused'?'接续剩余晶粒':'开始本任务仿真';nmr$('nmrPause').disabled=nmrBusy||nmrOpening||!bound||!['starting','running'].includes(r.status);nmr$('nmrInspect').disabled=nmrBusy||nmrOpening||!bound||!['starting','running','needs_attention'].includes(r.status)}
function nmrRunURL(id){const url=new URL(location.href);if(id)url.searchParams.set('run',id);else url.searchParams.delete('run');window.history.replaceState(null,'',url)}
function nmrClear(message){nmrCurrent=null;nmr$('nmrStatus').textContent=message;nmr$('nmrRecipe').textContent='';nmr$('nmrIdentity').textContent='';nmr$('nmrProgress').max=1;nmr$('nmrProgress').value=0;nmr$('nmrBundle').hidden=true;nmr$('nmrBundle').href='';nmr$('nmrPredict').hidden=true;nmr$('nmrPredict').href='';nmr$('nmrError').hidden=true;nmrControls()}
function nmrRender(r){nmrCurrent=r;nmr$('nmrStatus').textContent=r.id+' · '+r.recipe.material_id+' · '+(nmrNames[r.status]||r.status)+` · ${r.completed_jobs||0}/${r.total_jobs||2*r.recipe.n_grains}`;nmr$('nmrProgress').max=r.total_jobs||2*r.recipe.n_grains;nmr$('nmrProgress').value=r.completed_jobs||0;const p=r.recipe.params;nmr$('nmrRecipe').textContent=`Goss ${p.f_Goss} · 偏角${p.theta_0_deg}° · 半宽${p.halfwidth_deg}° · Si ${p.Si_content}% · ${r.recipe.n_grains}晶粒/方向 · 已用${(r.elapsed_seconds||0).toFixed(1)}秒`;nmr$('nmrIdentity').textContent=JSON.stringify({calibration:r.recipe.calibration_id,bank:r.selected_bank_sha256,sampling:r.texture_sampling_version,physics:r.physics_version,input_role:r.input_role},null,2);const needsStart=['ready','paused'].includes(r.status);nmr$('nmrError').hidden=!r.error&&(!needsStart||(r.producer_compatible&&nmrAllowed));nmr$('nmrError').textContent=r.error||(!r.producer_compatible?'程序版本已更新，旧任务保留，请新建任务。':!nmrAllowed?'当前实例不允许启动原生求解；可继续准备和查看记录，请通过启动器打开匹配的普通实例。':'');nmr$('nmrBundle').hidden=false;nmr$('nmrBundle').href='/api/workbench/native-material-runs/'+r.id+'/bundle';nmr$('nmrPredict').hidden=!r.prediction_url;if(r.prediction_url)nmr$('nmrPredict').href=r.prediction_url;if(!Array.from(nmr$('nmrHistory').children).some(o=>o.value===r.id)){const option=document.createElement('option');option.value=r.id;nmr$('nmrHistory').append(option)}for(const option of nmr$('nmrHistory').children){if(option.value===r.id)option.textContent=r.id+' · '+r.recipe.material_id+' · '+(nmrNames[r.status]||r.status)}nmr$('nmrHistory').value=r.id;nmrRunURL(r.id);nmrControls()}
async function nmrRefresh(){const g=++nmrGeneration;nmrControls();try{const [resources,cals,runs]=await Promise.all([nmrApi('resources'),nmrApi('calibrations'),nmrApi('native-material-runs')]);if(g!==nmrGeneration)return;nmrAllowed=resources.native_submission_allowed===true;nmrOptions(nmr$('nmrCalibration'),cals.map(c=>[c.id,c.id]));nmrOptions(nmr$('nmrHistory'),[['','重开已保存任务…'],...runs.map(r=>[r.id,r.id+' · '+(r.recipe?.material_id||'记录异常')+' · '+(nmrNames[r.status]||r.status)])]);if(nmrCurrent&&!nmrOpening)nmr$('nmrHistory').value=nmrCurrent.id;nmrControls()}catch(e){if(g===nmrGeneration){nmrAllowed=false;nmrControls();nmr$('nmrMessage').textContent='刷新失败，暂不开始原生任务：'+e.message}}}
async function nmrPrepare(){
 if(nmrBusy||nmrOpening)return;let data;try{data=nmrReadRecipe()}catch(e){nmr$('nmrMessage').textContent='未准备：'+e.message+'；已有任务保持。';return}
 nmrBusy=true;++nmrOpenGeneration;nmr$('nmrHistory').value='';nmrRunURL(null);nmrClear('正在准备新任务；旧记录保留');nmr$('nmrMessage').textContent='正在使用CPU生成独立取向和脚本，不启动GPU…';
 try{
  const r=await nmrApi('native-material-runs',data);if(!/^nativerun_[0-9a-f]{12}$/.test(r.id)||['calibration_id','material_id','thickness_mm','n_grains','seed'].some(k=>r.recipe?.[k]!==data[k])||Object.keys(data.params).some(k=>r.recipe?.params?.[k]!==data.params[k]))throw Error('保存任务参数与请求不符，请从记录核查');
  await nmrRefresh();let sameRecipe=false;try{sameRecipe=JSON.stringify(data)===JSON.stringify(nmrReadRecipe())}catch{/* Changed or incomplete inputs do not change the saved recipe. */}
  if(sameRecipe){nmrRender(r);nmr$('nmrMessage').textContent=r.status==='ready'?'脚本已准备。确认预算后，点击下方开始才执行仿真。':'任务记录已保存，请查看状态与原因。'}
  else{nmr$('nmrMessage').textContent='已按先前参数保存任务 '+r.id+'；当前输入已变化，未切换到该任务。';const link=document.createElement('a');link.href='/native-material-runs?run='+encodeURIComponent(r.id);link.textContent='重开保存的原任务';nmr$('nmrMessage').append(link)}
 }catch(e){nmrClear('新任务准备未完成；旧记录保留');nmr$('nmrMessage').textContent='未准备：'+e.message}finally{nmrBusy=false;nmrControls()}
}
async function nmrOpen(id){
 if(nmrBusy)return;const generation=++nmrOpenGeneration;nmrOpening=!!id;nmr$('nmrHistory').value=id||'';nmrRunURL(id);nmrClear(id?'正在读取任务 '+id:'尚未选择已保存任务');nmr$('nmrMessage').textContent='';if(!id)return;
 try{const r=await nmrApi('native-material-runs/'+encodeURIComponent(id));if(generation!==nmrOpenGeneration)return;if(r.id!==id)throw Error('返回任务身份与选择不同，未混用');nmrOpening=false;nmrRender(r)}
 catch(e){if(generation===nmrOpenGeneration){nmrClear('无法读取任务 '+id);nmr$('nmrMessage').textContent='无法重开：'+e.message}}
 finally{if(generation===nmrOpenGeneration){nmrOpening=false;nmrControls()}}
}
async function nmrTaskAction(action){
 if(nmrBusy||nmrOpening||!nmrBound())return;nmrControls();const button={start:'nmrStart',pause:'nmrPause',reconcile:'nmrInspect'}[action];if(!button||nmr$(button).disabled)return;
 const id=nmrCurrent.id,generation=++nmrOpenGeneration;nmrBusy=true;nmrControls();
 try{const r=await nmrApi('native-material-runs/'+id+'/'+action,{});if(generation!==nmrOpenGeneration||!nmrBound()||nmrCurrent.id!==id)return;
  if(action==='pause'){if(r.id&&r.id!==id)throw Error('返回暂停任务身份不符');nmr$('nmrMessage').textContent=r.message||'已请求当前晶粒结束后暂停'}
  else{if(r.id!==id)throw Error('返回任务身份不符，原现场保留');nmrRender(r);nmr$('nmrMessage').textContent=action==='start'?'已显式下发本任务，按固定预算串行执行。':'现场检查完成，未重试或关闭任何进程。'}
 }catch(e){if(generation===nmrOpenGeneration&&nmrCurrent?.id===id){nmrAllowed=false;nmr$('nmrMessage').textContent='任务操作未确认：'+e.message+'；请刷新或检查原任务，不自动重试。'}}
 finally{nmrBusy=false;nmrControls()}
}
async function nmrStart(){return nmrTaskAction('start')}
async function nmrPause(){return nmrTaskAction('pause')}
async function nmrInspect(){return nmrTaskAction('reconcile')}
async function nmrPoll(){const id=nmrCurrent?.id,generation=nmrOpenGeneration;try{if(id&&!nmrBusy&&!nmrOpening){const [r,resources]=await Promise.all([nmrApi('native-material-runs/'+id),nmrApi('resources')]);if(generation===nmrOpenGeneration&&nmrBound()&&nmrCurrent.id===id&&!nmrBusy&&!nmrOpening){if(r.id!==id)throw Error('返回任务身份不符');nmrAllowed=resources.native_submission_allowed===true;nmrRender(r)}}}catch(e){if(generation===nmrOpenGeneration&&nmrCurrent?.id===id&&!nmrOpening){nmrAllowed=false;nmrControls();nmr$('nmrMessage').textContent='状态暂不可读，暂不开始任务：'+e.message}}finally{setTimeout(nmrPoll,2000)}}
async function nmrInit(){await nmrRefresh();const id=new URL(location.href).searchParams.get('run');if(id)await nmrOpen(id);setTimeout(nmrPoll,2000)}
nmrInit();
