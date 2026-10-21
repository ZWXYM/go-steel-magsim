/* One visible training task, recoverable by GET after refresh or a lost response. */
let _platformTraining=null;
const _trainingStorageKey='go_steel_training_task_v1';
function _trainingTerminal(flow){return ['completed','failed','missing','history'].includes(flow?.phase)}
function _trainingMessage(message){const node=document.getElementById('train-status');if(node)node.textContent=message}
function _trainingValidOrigin(origin){return origin&&origin.schema==='workspace_origin_v1'&&typeof origin.path==='string'&&origin.path.trim()&&(/^[a-f0-9]{64}$/).test(origin.token||'')}
function _trainingKey(origin){return 'go_steel_training_task_v2_'+origin.token}
function _trainingRemember(flow){if(!_trainingValidOrigin(flow.workspace_origin))return;try{localStorage.setItem(_trainingKey(flow.workspace_origin),JSON.stringify({version:2,kind:flow.kind,request:flow.request,task_id:flow.task_id,phase:flow.phase,workspace_origin:flow.workspace_origin}))}catch{}}
async function _trainingWorkspace(){
  const current=typeof getPlatformWorkspace==='function'?await getPlatformWorkspace():null;
  if(!current||typeof current.workspace!=='string'||!(/^[a-f0-9]{64}$/).test(current.workspace_token||''))throw Error('工作目录尚未确认，请刷新目录或用最新启动器打开系统');
  return {schema:'workspace_origin_v1',path:current.workspace,token:current.workspace_token};
}
function _trainingButtons(){
  const busy=!!_platformTraining&&!_trainingTerminal(_platformTraining);
  for(const id of ['train-single-start','train-selection-start']){const node=document.getElementById(id);if(node)node.disabled=busy}
  const check=document.getElementById('train-check');if(check)check.disabled=!_platformTraining||!!_platformTraining.pollPending;
}
function _trainingIdentity(flow){
  const node=document.getElementById('train-task-identity');
  if(node)node.textContent=`${flow.kind==='single'?'单模型训练':'候选模型选择'} · 来源目录：${flow.workspace_origin?.path||'未知（旧记录）'}${flow.phase==='history'?' · 历史记录':''} · 数据集：${flow.request.dataset_path} · 配置：${flow.request.model_type||flow.request.preset} · 任务：${flow.task_id||'等待确认'} · 请求：${flow.request.request_id}`;
}
function _singleTrainingResult(result){
  _trainingMessage(result.metric_reliability==='exploratory_only'?'训练完成：探索模型，样本不足，未发布正式验证指标。':'训练完成！');
  document.getElementById('res-r2').textContent=result.r2_avg!=null?Number(result.r2_avg).toFixed(4):result.train_r2_avg!=null?'训练 '+Number(result.train_r2_avg).toFixed(4):'—';
  document.getElementById('res-mape').textContent=result.mape_avg!=null?(Number(result.mape_avg)*100).toFixed(2)+'%':result.train_mape_avg!=null?'训练 '+(Number(result.train_mape_avg)*100).toFixed(2)+'%':'—';
  document.getElementById('res-model-id').textContent=result.model_id||'—';document.getElementById('train-results').style.display='block';
  const canvas=document.getElementById('fi-chart');if(canvas)canvas.style.display='block';renderFeatureImportance(result.feature_importance||{});
}
function _trainingName(flow){return flow.kind==='single'?'model_train':'paper_surrogate_model_selection'}
async function _trainingRead(url){
  const response=await fetch(url),data=await response.json();
  if(!response.ok){const error=Error(data.error||`任务读取失败（${response.status}）`);error.status=response.status;throw error}
  return data;
}
async function _trainingFind(flow){
  const listing=await _trainingRead('/api/tasks');
  const matches=(listing.tasks||[]).filter(task=>task.training_request_id===flow.request.request_id&&task.name===_trainingName(flow));
  if(matches.length!==1)throw Error(matches.length?'请求对应多个任务，请核对任务列表':'尚未找到原请求对应的任务，请稍后再次刷新或核对已保存模型；不会自动重训');
  flow.task_id=matches[0].id;_trainingRemember(flow);_trainingIdentity(flow);
}
async function _trainingPoll(flow){
  if(_platformTraining!==flow||flow.pollPending||!flow.current_workspace)return;
  if(flow.timer){clearTimeout(flow.timer);flow.timer=null}
  flow.pollPending=true;_trainingButtons();
  try{
    if(!flow.task_id)await _trainingFind(flow);
    let task;
    try{task=await _trainingRead('/api/tasks/'+encodeURIComponent(flow.task_id))}
    catch(error){if(error.status===404){flow.phase='missing';_trainingMessage('原任务已不在当前进程中。请刷新已保存模型核对完成产物；不会自动重新提交。');return}throw error}
    if(_platformTraining!==flow)return;
    if(task.id!==flow.task_id||task.training_request_id!==flow.request.request_id||task.name!==_trainingName(flow))throw Error('任务身份与原请求不一致，请核对记录');
    const origin=_trainingValidOrigin(task.workspace_origin)?task.workspace_origin:null;
    if(origin&&flow.workspace_origin&&origin.token!==flow.workspace_origin.token)throw Error('任务目录与原请求来源不一致，请核对历史记录');
    if(origin)flow.workspace_origin=origin;
    if(!origin||origin.token!==flow.current_workspace.token){
      if(!origin)flow.workspace_origin=null;
      flow.phase='history';document.getElementById('train-results').style.display='none';document.getElementById('paper-ranking').innerHTML='';
      _trainingMessage(origin?'历史任务来自其他目录，未显示为当前目录的训练结果。原任务和模型保留，不会自动重训。':'旧任务来源目录未知，未显示为当前训练结果。请核对已保存模型与历史任务；不会用当前目录补写来源或自动重训。');return;
    }
    if(task.status==='completed'){
      flow.phase='completed';
      try{if(flow.kind==='single')_singleTrainingResult(task.result||{});else renderPaperTrainingResult(task.result||{});loadModelList();loadModelListForPred()}
      catch(error){_trainingMessage('训练已完成，但结果暂未完整显示：'+error.message+'。可刷新当前任务或查看已保存模型。')}
    }else if(task.status==='failed'){
      flow.phase='failed';_trainingMessage('训练失败：'+(task.error||'未提供错误原因'));
    }else if(task.status==='running'){
      flow.phase='running';_trainingMessage('训练运行中；显示的是下方记录的固定数据集和配置，可继续查看其他页面。');
    }else throw Error('当前任务状态暂不可识别：'+task.status);
  }catch(error){if(_platformTraining===flow){flow.phase='unconfirmed';_trainingMessage('暂时无法确认训练状态：'+error.message+'。可刷新当前任务，未重复提交。')}}
  finally{
    flow.pollPending=false;
    if(_platformTraining===flow){_trainingRemember(flow);_trainingIdentity(flow);_trainingButtons();if(flow.phase==='running')flow.timer=setTimeout(()=>_trainingPoll(flow),2000)}
  }
}
async function refreshTrainingTask(){if(_platformTraining)return _trainingPoll(_platformTraining)}
async function _beginPlatformTraining(kind,body){
  if(_platformTraining&&!_trainingTerminal(_platformTraining))return;
  const request_id=typeof crypto!=='undefined'&&crypto.randomUUID?crypto.randomUUID():'train_'+Date.now()+'_'+Math.random().toString(16).slice(2);
  const flow={kind,request:JSON.parse(JSON.stringify({...body,request_id})),task_id:null,phase:'submitting',pollPending:false,timer:null};
  _platformTraining=flow;_trainingRemember(flow);_trainingIdentity(flow);_trainingButtons();
  document.getElementById('train-results').style.display='none';document.getElementById('paper-ranking').innerHTML='';
  _trainingMessage('正在提交训练请求…');
  try{
    flow.current_workspace=await _trainingWorkspace();flow.workspace_origin=flow.current_workspace;_trainingRemember(flow);_trainingIdentity(flow);
    flow.submitAttempted=true;
    const response=await fetch(kind==='single'?'/api/ml/train':'/api/ml/paper-train',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(flow.request)});
    const data=await response.json();if(_platformTraining!==flow||_trainingTerminal(flow))return;
    if(!response.ok){if(response.status>=400&&response.status<500)flow.phase='failed';throw Error(data.error||`提交失败（${response.status}）`)}
    if(typeof data.task_id!=='string'||!(/^[A-Za-z0-9_-]{1,80}$/).test(data.task_id))throw Error('服务器未返回可核对的任务ID');
    if(flow.task_id&&flow.task_id!==data.task_id)throw Error('提交返回的任务与已恢复记录不一致');
    flow.task_id=data.task_id;flow.phase='running';_trainingRemember(flow);_trainingIdentity(flow);await _trainingPoll(flow);
  }catch(error){if(_platformTraining===flow&&!flow.submitAttempted){flow.phase='failed';_trainingMessage('训练请求未接受：'+error.message)}else if(_platformTraining===flow&&!_trainingTerminal(flow)){flow.phase='unconfirmed';_trainingMessage('提交结果尚未确认：'+error.message+'。请刷新当前任务，只查询原请求，不会重新训练。')}else if(_platformTraining===flow)_trainingMessage('训练请求未接受：'+error.message)}
  finally{if(_platformTraining===flow){_trainingRemember(flow);_trainingButtons()}}
}
async function startSingleModelTraining(){
  if(_platformTraining&&!_trainingTerminal(_platformTraining))return;
  try{
    const dataset_path=document.getElementById('train-dataset').value;if(!dataset_path)throw Error('请先选择数据集');
    const key=document.getElementById('ml-train-preset')?.value||'gaussian_process',preset=_ML_TRAIN_PRESETS_META[key]||{model_type:'gaussian_process'};
    const test_size=Number(document.getElementById('train-test-size').value);if(!Number.isFinite(test_size)||test_size<=0||test_size>=1)throw Error('测试集比例需在0与1之间');
    const body={dataset_path,model_type:preset.model_type,test_size};
    if(preset.xgb_fields){body.xgb_params={};for(const [key,id] of [['n_estimators','xgb-n-est'],['max_depth','xgb-depth'],['learning_rate','xgb-lr'],['subsample','xgb-sub']]){const value=Number(document.getElementById(id).value);if(!Number.isFinite(value)||value<=0)throw Error(key+' 需为正数');body.xgb_params[key]=value}}
    return await _beginPlatformTraining('single',body);
  }catch(error){_trainingMessage('训练输入有误：'+error.message);alert(error.message)}
}
async function startTraining(){
  if(_platformTraining&&!_trainingTerminal(_platformTraining))return;
  try{
    const dataset_path=document.getElementById('train-dataset').value;if(!dataset_path)throw Error('请先选择或生成数据集');
    const candidate_models=[...selectedTrainingModels()];if(!candidate_models.length)throw Error('请至少勾选一个候选模型');
    const preset=document.getElementById('paper-preset')?.value||'lite';return await _beginPlatformTraining('selection',{dataset_path,preset,candidate_models});
  }catch(error){_trainingMessage('训练输入有误：'+error.message);alert(error.message)}
}
async function restorePlatformTraining(){
  if(_platformTraining)return;
  let current,stored,key;
  try{current=await _trainingWorkspace();key=_trainingKey(current);let raw=localStorage.getItem(key);if(raw===null){key=_trainingStorageKey;raw=localStorage.getItem(key)}stored=JSON.parse(raw)}catch(error){_trainingMessage('训练记录暂不可恢复：'+error.message);return}
  if(_platformTraining||!stored)return;
  if(![1,2].includes(stored.version)||!['single','selection'].includes(stored.kind)||typeof stored.request?.dataset_path!=='string'||!(/^[A-Za-z0-9_-]{12,80}$/).test(stored.request?.request_id||'')||(stored.task_id!=null&&!(/^[A-Za-z0-9_-]{1,80}$/).test(stored.task_id))||(stored.version===2&&(!_trainingValidOrigin(stored.workspace_origin)||key!==_trainingKey(stored.workspace_origin)))){_trainingMessage('旧训练记录格式不可确认，原记录保留；请核对已保存模型。');return}
  const flow={...stored,workspace_origin:stored.version===2?stored.workspace_origin:null,current_workspace:current,phase:'unconfirmed',pollPending:false,timer:null};_platformTraining=flow;_trainingIdentity(flow);_trainingButtons();return _trainingPoll(flow);
}
if(typeof document!=='undefined')document.addEventListener('DOMContentLoaded',restorePlatformTraining);
