/* Saved model records retain their source and cannot be replaced by an older read. */
let _modelRows=[],_modelRowsLoaded=false,_modelReadEpoch=0;
const _modelPendingDeletions=new Set();
function _validSavedModelID(value){return typeof value==='string'&&(/^[A-Za-z0-9_-][A-Za-z0-9_.-]{0,127}$/).test(value)}
function _modelNotice(message){const node=document.getElementById('model-list-status');if(node)node.textContent=message}
function _modelElement(tag,text,className){const node=document.createElement(tag);if(text!==undefined)node.textContent=String(text);if(className)node.className=className;return node}
function _modelMetric(value,digits=4){return value==null||!Number.isFinite(Number(value))?'—':Number(value).toFixed(digits)}
function _savedModelCard(model){
  const card=_modelElement('div');card.style.padding='10px 12px';card.style.borderBottom='1px solid var(--border)';card.style.fontSize='12px';
  if(!model||typeof model.model_id!=='string'){card.append(_modelElement('p','此模型记录暂不可读，其他记录保留。'));return card}
  card.dataset.modelId=model.model_id;
  const heading=_modelElement('div');Object.assign(heading.style,{display:'flex',gap:'8px',alignItems:'center',flexWrap:'wrap'});
  const identity=_modelElement('code',model.model_id);identity.style.color='var(--cyan)';heading.append(identity,
    _modelElement('span',model.prediction_capable===false?'CV REPORT':'PREDICT','badge '+(model.prediction_capable===false?'b-amber':'b-green')),
    _modelElement('span','类型: '+(model.model_type||'—')),_modelElement('span','数据集: '+(model.dataset_name||'—')),_modelElement('span',(model.n_samples??0)+' samples'));
  const button=_modelElement('button',_modelPendingDeletions.has(model.model_id)?'正在删除…':'删除','btn btn-danger');Object.assign(button.style,{marginLeft:'auto',padding:'3px 10px',fontSize:'12px'});
  button.disabled=_modelPendingDeletions.has(model.model_id)||!_validSavedModelID(model.model_id);button.onclick=()=>deleteModel(model.model_id);heading.append(button);
  const metrics=_modelElement('div');Object.assign(metrics.style,{display:'flex',gap:'12px',flexWrap:'wrap',marginTop:'6px',color:'var(--t2)'});
  for(const text of ['CV B-H RMSE: '+_modelMetric(model.cv_bh_rmse_T_mean,5)+' T','Holdout B-H RMSE: '+_modelMetric(model.holdout_bh_rmse_T,5)+' T','R²: '+_modelMetric(model.r2_avg),'选择方式: '+(model.selection_metric||model.metric_reliability||'—')])metrics.append(_modelElement('span',text));
  if(!_validSavedModelID(model.model_id))metrics.append(_modelElement('span','记录ID格式异常，未提供删除操作。'));
  card.append(heading,metrics);return card;
}
function _renderSavedModels(){
  if(!_modelRowsLoaded)return;
  const container=document.getElementById('model-list');if(!container)return;
  if(!_modelRows.length){container.replaceChildren(_modelElement('p','暂无已训练模型'));return}
  container.replaceChildren(_modelElement('div','CV B-H RMSE选模型只在同一数据集、同一fold策略下比较；跨数据集模型不能直接排名。','info-note'),..._modelRows.map(_savedModelCard));
}
async function loadModelList(){
  const epoch=++_modelReadEpoch;_modelNotice('正在读取已保存模型…');
  try{
    const response=await fetch('/api/ml/models'),data=await response.json();if(epoch!==_modelReadEpoch)return false;
    if(!response.ok||data.error||data.warning||!Array.isArray(data.models))throw Error(data.error||data.warning||'模型列表暂不可读');
    _modelRows=data.models;_modelRowsLoaded=true;_renderSavedModels();_modelNotice(`已读取 ${_modelRows.length} 份模型或报告记录`);return true;
  }catch(error){if(epoch===_modelReadEpoch)_modelNotice('模型列表暂不可更新：'+error.message+'；保留已读记录。');return false}
}
async function deleteModel(model_id){
  if(!_validSavedModelID(model_id)){_modelNotice('模型ID格式无效，未删除任何文件。');return false}
  if(_modelPendingDeletions.has(model_id)||!confirm('确定删除模型 '+model_id+' ?'))return false;
  _modelPendingDeletions.add(model_id);++_modelReadEpoch;_renderSavedModels();_modelNotice('正在删除模型 '+model_id+'…');
  try{
    const response=await fetch('/api/ml/models/'+encodeURIComponent(model_id),{method:'DELETE'}),data=await response.json();
    if(!response.ok||data.error||typeof data.success!=='boolean')throw Error(data.error||`删除未获确认（${response.status}）`);
    _modelRows=_modelRows.filter(model=>model?.model_id!==model_id);_renderSavedModels();
    if(document.getElementById('pred-model')?.value===model_id)_predictionSelectionChanged();
    const [updated]=await Promise.all([loadModelList(),loadModelListForPred()]);
    if(updated)_modelNotice(data.success?'已删除模型 '+model_id:'模型 '+model_id+' 已不在库中');return true;
  }catch(error){_modelNotice('删除失败：'+error.message+'；请核对记录后再操作。');return false}
  finally{_modelPendingDeletions.delete(model_id);_renderSavedModels()}
}
