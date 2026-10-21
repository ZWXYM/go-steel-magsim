/* Keep displayed curves, copied data and downloads bound to one accepted input. */
let _predAcceptedInput=null, _predRequest=null, _predInputEpoch=0;
let _predModelsEpoch=0, _predExportPending=false;
const _predFields=[
  ['f_Goss','pred-fgoss','f_Goss',.4,.9],
  ['theta_0_deg','pred-theta','θ₀',1,30],
  ['halfwidth_deg','pred-hw','半宽 σ',5,15],
  ['Si_content','pred-si','Si 含量',2.5,6]
];
function _predictionSnapshot(){
  const model_id=document.getElementById('pred-model')?.value?.trim();
  if(!model_id)throw Error('请先选择可预测模型');
  const params={};
  for(const [key,id,label,min,max] of _predFields){
    const raw=document.getElementById(id)?.value??'',value=Number(raw);
    if(!raw.trim()||!Number.isFinite(value)||value<min||value>max)throw Error(`${label} 需为 ${min}–${max} 范围内的有效数值`);
    params[key]=value;
  }
  return {model_id,params};
}
function _predictionKey(snapshot){return JSON.stringify(snapshot)}
function _predictionMatches(snapshot){try{return !!snapshot&&_predictionKey(snapshot)===_predictionKey(_predictionSnapshot())}catch{return false}}
function _predictionCurrentResult(){return _predictionMatches(_predAcceptedInput)?_currentPredResult:null}
function _predictionNotice(message){const el=document.getElementById('pred-input-status');if(el)el.textContent=message}
function _predictionButtons(){
  const pending=!!_predRequest&&_predictionMatches(_predRequest.snapshot),button=document.getElementById('pred-run');
  if(button){button.disabled=pending;button.textContent=pending?'⏳ 预测中…':'⚡ 立即预测'}
  const download=document.getElementById('pred-download');
  if(download){download.disabled=_predExportPending||!_predictionCurrentResult();download.textContent=_predExportPending?'正在导出…':'⬇️ 下载 .amat'}
}
function _predictionHideResult(){
  for(const id of ['pred-result','pred-full-wrap','pred-core-loss','pred-analysis-result','maxwell-pred-dialog']){
    const el=document.getElementById(id);if(el)el.style.display='none';
  }
}
function _predictionSelectionChanged(){
  ++_predInputEpoch;_predRequest=null;_predAcceptedInput=null;_currentPredResult=null;
  _predictionHideResult();_predictionNotice('输入已改变，请重新预测。');_predictionButtons();
}
function _predictionStale(){const error=Error('输入已改变，已忽略此前的预测响应');error.stale=true;return error}
function _predictionActive(request){return _predRequest===request&&request.epoch===_predInputEpoch&&_predictionMatches(request.snapshot)}
function _predictionRender(data,snapshot){
  document.getElementById('pred-result').style.display='block';
  renderBHChart(data);renderScalars(data.scalars||{},data.RD);
  if(data.full_direction)renderFullDirectionCharts(data.full_direction,'pred-full-');
  if(data.RD.H.length>2){
    const wrap=document.getElementById('pred-core-loss');if(wrap)wrap.style.display='block';
    renderCoreLossCards(estimateCoreLossParams(data.RD.H,data.RD.B,.35),'pred-cl-grid');
  }
  const axis=data.H_axis==='physical_A_per_m'?'物理H（A/m）':data.H_axis==='legacy_unspecified'?'旧模型未声明H轴口径':data.H_axis||'H轴口径未声明';
  _predictionNotice(`当前结果：${snapshot.model_id} · f_Goss=${snapshot.params.f_Goss} · θ₀=${snapshot.params.theta_0_deg}° · σ=${snapshot.params.halfwidth_deg}° · Si=${snapshot.params.Si_content}% · ${axis}`);
}
async function _fetchPredBH(force=false){
  const snapshot=_predictionSnapshot(),key=_predictionKey(snapshot);
  if(_predRequest&&_predRequest.key===key&&_predRequest.epoch===_predInputEpoch)return _predRequest.promise;
  if(!force&&_predictionCurrentResult())return _currentPredResult;
  const current={snapshot,key,epoch:++_predInputEpoch,promise:null};_predRequest=current;
  _currentPredResult=null;_predAcceptedInput=null;_predictionHideResult();
  _predictionNotice('正在预测所选模型与参数…');_predictionButtons();
  current.promise=(async()=>{
    const response=await fetch('/api/ml/predict',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(snapshot)});
    const data=await response.json();
    if(!_predictionActive(current))throw _predictionStale();
    if(!response.ok||data.error)throw Error(data.error||`预测读取失败（${response.status}）`);
    if(data.model_id!==snapshot.model_id)throw Error('返回模型与所选模型不一致，请重新预测');
    for(const direction of ['RD','TD']){
      const curve=data[direction];
      if(!Array.isArray(curve?.H)||!Array.isArray(curve?.B)||curve.H.length<2||curve.H.length!==curve.B.length||![...curve.H,...curve.B].every(Number.isFinite))throw Error(`${direction} 曲线数据不完整`);
    }
    _currentPredResult=data;_predAcceptedInput=snapshot;_predictionRender(data,snapshot);return data;
  })().catch(error=>{if(!_predictionActive(current)&&!error.stale)throw _predictionStale();throw error}).finally(()=>{
    if(_predRequest===current){_predRequest=null;_predictionButtons()}
  });
  return current.promise;
}
async function runPrediction(){
  try{return await _fetchPredBH(true)}catch(error){if(!error.stale){_predictionNotice('预测失败：'+error.message);alert('预测失败：'+error.message)}return null}
}
async function loadModelListForPred(){
  const epoch=++_predModelsEpoch;
  try{
    const response=await fetch('/api/ml/models'),data=await response.json();
    if(epoch!==_predModelsEpoch)return;
    if(!response.ok||data.error||data.warning)throw Error(data.error||data.warning||`模型列表读取失败（${response.status}）`);
    const select=document.getElementById('pred-model'),selected=select.value;
    const models=(data.models||[]).filter(model=>model.prediction_capable!==false);
    select.replaceChildren();
    for(const model of models){
      const option=document.createElement('option');option.value=model.model_id;
      const metric=model.r2_avg!=null?'验证R²='+Number(model.r2_avg).toFixed(3):model.train_r2_avg!=null?'训练R²='+Number(model.train_r2_avg).toFixed(3):'指标待补充';
      option.textContent=`${model.model_id} (${model.model_type||'direct_xgb'}) · ${metric}`;select.append(option);
    }
    if(selected&&models.some(model=>model.model_id===selected))select.value=selected;
    else if(selected||!models.length){
      const missing=document.createElement('option');missing.value='';missing.textContent=selected?'所选模型已不在列表，请重新选择':'暂无可预测模型';select.append(missing);select.value='';
    }
    if(select.value!==selected)_predictionSelectionChanged();
  }catch(error){if(epoch===_predModelsEpoch)_predictionNotice('模型列表暂不可更新：'+error.message+'；已有选择保留。')}
}
function exportMaxwellFromPred(){
  if(!_predictionCurrentResult()){_predictionNotice('请先按当前模型和参数重新预测，再导出材料。');return}
  document.getElementById('maxwell-pred-dialog').style.display='block';updatePredCoreLoss();_predictionButtons();
}
async function doExportMaxwellFromPred(){
  if(_predExportPending)return;
  if(!_predictionCurrentResult()){_predictionNotice('输入已改变，请重新预测后导出。');return}
  const snapshot=_predAcceptedInput,matName=document.getElementById('pred-mat-name').value.trim()||'GO_Pred';
  const thickness=Number(document.getElementById('pred-thickness').value);
  if(!Number.isFinite(thickness)||thickness<=0){toast('厚度需为大于0的有效数值','err');return}
  const payload={...snapshot,params:{...snapshot.params},mat_name:matName,thickness_mm:thickness};
  _predExportPending=true;_predictionButtons();
  try{
    const response=await fetch('/api/analyze/export-maxwell-from-prediction',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    if(!response.ok){let data={};try{data=await response.json()}catch{}throw Error(data.error||`材料导出失败（${response.status}）`)}
    const blob=await response.blob(),url=URL.createObjectURL(blob),link=document.createElement('a');
    try{link.href=url;link.download=matName+'.amat';link.click()}finally{setTimeout(()=>URL.revokeObjectURL(url),1000)}
    if(_predictionMatches(snapshot))document.getElementById('maxwell-pred-dialog').style.display='none';
    toast(`已下载 ${matName}.amat（模型 ${snapshot.model_id}）`,'ok');
  }catch(error){toast('导出失败：'+error.message,'err')}
  finally{_predExportPending=false;_predictionButtons()}
}
async function copyBHData(){
  try{
    const data=await _fetchPredBH(),rd=data.RD,td=data.TD;
    const lines=['H_RD (A/m)\tB_RD (T)\tH_TD (A/m)\tB_TD (T)'];
    for(let i=0;i<Math.max(rd.H.length,td.H.length);i++)lines.push([rd.H[i]??'',rd.B[i]??'',td.H[i]??'',td.B[i]??''].join('\t'));
    await navigator.clipboard.writeText(lines.join('\n'));toast('B-H 数据已复制，RD/TD 各自保留原始H轴','ok');
  }catch(error){if(!error.stale)toast('复制失败：'+error.message,'err')}
}
