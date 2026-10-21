/* A panel follows one script/job; closing its view never stops the worker. */
let _execJobId=null,_execScript=null,_execSource=null,_elapsedTimer=null,_execQueueTimer=null,_jobPollTimer=null;
let _execEpoch=0,_execLookupEpoch=0,_execPanelOpen=false;
const _execSubmissions=new Map(),_execStops=new Set();
function _execCurrent(epoch,job){return _execPanelOpen&&epoch===_execEpoch&&(job==null||job===_execJobId)}
function _detachExecView(){
  if(_execSource){_execSource.close();_execSource=null}
  if(_elapsedTimer){clearInterval(_elapsedTimer);_elapsedTimer=null}
  if(_execQueueTimer){clearTimeout(_execQueueTimer);_execQueueTimer=null}
}
function _execReconnectError(error,epoch,job){
  if(!_execCurrent(epoch,job))return;
  document.getElementById('exec-status-badge').textContent='状态暂不可读：'+error.message;
  const start=document.getElementById('exec-start-btn');start.disabled=false;start.style.display='';start.textContent='重新连接';
  document.getElementById('exec-stop-btn').disabled=true;
}
function openExecPanel(filename,totalTasks,existingJobId){
  _detachExecView();++_execEpoch;++_execLookupEpoch;_execPanelOpen=true;_execScript=filename;_execJobId=existingJobId||null;
  const epoch=_execEpoch,record=_execSubmissions.get(filename),start=document.getElementById('exec-start-btn');
  document.getElementById('exec-title').textContent=(existingJobId?'进度: ':'执行: ')+filename;
  start.disabled=!!existingJobId||!!record?.pending;start.style.display=existingJobId?'none':'';start.textContent=record&&!record.response?'核对原请求':'▶ 开始执行';
  document.getElementById('exec-stop-btn').disabled=true;
  for(const id of ['exec-status-badge','exec-log','exec-elapsed'])document.getElementById(id).textContent='';
  document.getElementById('exec-log').innerHTML='';updateExecProgress(0,0,totalTasks||0);
  document.getElementById('exec-current').textContent='等待任务状态';document.getElementById('exec-modal').style.display='flex';
  if(existingJobId)_reconnectStream(existingJobId,epoch);
  else if(record?.response){_execJobId=record.response.job_id;_reconnectStream(_execJobId,epoch)}
  else if(record?.pending)_observeExecSubmission(record,epoch,filename);
}
function closeExecModal(){_execPanelOpen=false;++_execEpoch;++_execLookupEpoch;_detachExecView();document.getElementById('exec-modal').style.display='none';loadTaskScripts()}
function clearExecLog(){document.getElementById('exec-log').innerHTML=''}
function _execFinished(jobId,status){
  const record=_execSubmissions.get(_execScript);if(record?.response?.job_id===jobId)_execSubmissions.delete(_execScript);
  _detachExecView();_updateExecBadge(status,true);document.getElementById('exec-stop-btn').disabled=true;
  const start=document.getElementById('exec-start-btn');start.disabled=false;start.style.display='';start.textContent='再次执行';
  _execJobId=null;
}
async function _reconnectStream(jobId,epoch=_execEpoch){
  if(!_execCurrent(epoch,jobId))return;
  try{
    const response=await api('/api/jobs/'+encodeURIComponent(jobId)+'/status');
    if(!_execCurrent(epoch,jobId))return;
    if(response.code==='job_not_found'){
      const record=_execSubmissions.get(_execScript);if(record?.response?.job_id===jobId)_execSubmissions.delete(_execScript);
      _detachExecView();_execJobId=null;document.getElementById('exec-status-badge').textContent=response.error;
      const start=document.getElementById('exec-start-btn');start.disabled=false;start.style.display='';start.textContent='新建执行';document.getElementById('exec-stop-btn').disabled=true;return;
    }
    if(response.error||response.id!==jobId||response.script!==_execScript)throw Error(response.error||'任务身份与当前脚本不一致');
    document.getElementById('exec-log').innerHTML='';(response.recent_lines||[]).forEach(appendExecLine);
    updateExecProgress(response.completed,response.failed,response.total);_updateExecBadge(response.status,response.done);
    if(response.done){_execFinished(jobId,response.status);return}
    document.getElementById('exec-start-btn').disabled=true;document.getElementById('exec-start-btn').style.display='none';
    document.getElementById('exec-stop-btn').disabled=_execStops.has(jobId)||response.status==='starting'||response.status==='stopping';
    if(response.status==='queued'||response.status==='starting')_pollUntilRunning(jobId,epoch);
    else {_startElapsedTimer(response.started,epoch,jobId);_openSSE(jobId,epoch)}
  }catch(error){_execReconnectError(error,epoch,jobId)}
}
function _openSSE(jobId,epoch=_execEpoch){
  if(!_execCurrent(epoch,jobId))return;
  if(_execSource)_execSource.close();const source=new EventSource('/api/jobs/'+encodeURIComponent(jobId)+'/stream');_execSource=source;
  source.onmessage=event=>{
    if(!_execCurrent(epoch,jobId)||_execSource!==source)return;
    try{
      const data=JSON.parse(event.data);(data.lines||[]).forEach(appendExecLine);updateExecProgress(data.completed,data.failed,data.total);
      if(data.done){_execFinished(jobId,data.status);loadTaskScripts()}
    }catch(error){_execReconnectError(error,epoch,jobId)}
  };
  source.onerror=()=>{
    if(!_execCurrent(epoch,jobId)||_execSource!==source)return;
    if(source.readyState===2){source.close();_execSource=null;_execReconnectError(Error('连接已中断；重新连接只查询当前任务'),epoch,jobId)}
  };
}
function _updateExecBadge(status,done){
  const node=document.getElementById('exec-status-badge');if(!node)return;
  node.textContent=done?(status==='completed'?'✓ 完成':status==='stopped'?'⏹ 已停止':'✗ '+status):status==='queued'?'⏳ 排队中':status==='starting'?'启动中':status==='stopping'?'停止中':'运行中';
}
function _startElapsedTimer(started,epoch=_execEpoch,jobId=_execJobId){
  if(_elapsedTimer)clearInterval(_elapsedTimer);const base=started?new Date(started).getTime():Date.now();
  _elapsedTimer=setInterval(()=>{if(!_execCurrent(epoch,jobId))return;const seconds=Math.floor((Date.now()-base)/1000);document.getElementById('exec-elapsed').textContent='用时: '+seconds+'s'},1000);
}
async function handleExecBtn(filename,totalTasks){
  const lookup=++_execLookupEpoch;
  try{const data=await api('/api/jobs');if(lookup!==_execLookupEpoch)return;if(data.error||!Array.isArray(data.jobs))throw Error(data.error||'任务列表不可读');const existing=data.jobs.find(job=>job.script===filename&&!job.done);openExecPanel(filename,totalTasks,existing?.id)}
  catch(error){if(lookup===_execLookupEpoch)toast('任务列表读取失败：'+error.message,'err')}
}
async function _observeExecSubmission(record,epoch,script){
  if(record.observers.has(epoch))return;record.observers.add(epoch);
  try{const response=await record.promise;if(!_execCurrent(epoch)||_execScript!==script)return;_execJobId=response.job_id;await _reconnectStream(_execJobId,epoch)}
  catch(error){if(_execCurrent(epoch)&&_execScript===script){document.getElementById('exec-start-btn').textContent='核对原请求';_execReconnectError(Error(error.message+'；重试沿用原请求ID'),epoch);document.getElementById('exec-start-btn').textContent='核对原请求'}}
  finally{if(_execCurrent(epoch)&&_execScript===script&&!_execJobId)document.getElementById('exec-start-btn').disabled=false}
}
async function startExec(){
  if(!_execPanelOpen||!_execScript)return;
  const epoch=_execEpoch,script=_execScript;
  if(_execJobId)return _reconnectStream(_execJobId,epoch);
  let record=_execSubmissions.get(script);if(record?.pending)return;
  if(record?.response){_execJobId=record.response.job_id;return _reconnectStream(_execJobId,epoch)}
  if(!record){record={request_id:typeof crypto!=='undefined'&&crypto.randomUUID?crypto.randomUUID():'script_'+Date.now()+'_'+Math.random().toString(16).slice(2),observers:new Set()};_execSubmissions.set(script,record)}
  record.pending=true;record.observers.clear();document.getElementById('exec-start-btn').disabled=true;document.getElementById('exec-status-badge').textContent='提交中';
  record.promise=(async()=>{try{const response=await api('/api/run-script',{method:'POST',body:{filename:script,request_id:record.request_id}});if(response.error||!(/^[A-Za-z0-9_-]{1,80}$/).test(response.job_id||''))throw Error(response.error||'未收到任务身份');record.response=response;return response}finally{record.pending=false}})();
  return _observeExecSubmission(record,epoch,script);
}
function _pollUntilRunning(jobId,epoch=_execEpoch){
  if(_execQueueTimer)clearTimeout(_execQueueTimer);_execQueueTimer=setTimeout(()=>{_execQueueTimer=null;if(_execCurrent(epoch,jobId))_reconnectStream(jobId,epoch)},3000);
}
async function stopExec(){
  const jobId=_execJobId,epoch=_execEpoch;if(!_execPanelOpen||!jobId||_execStops.has(jobId)||!confirm('停止或取消 '+_execScript+' 的任务 '+jobId+'？'))return;
  _execStops.add(jobId);document.getElementById('exec-stop-btn').disabled=true;
  try{const response=await api('/api/jobs/'+encodeURIComponent(jobId)+'/stop',{method:'POST'});if(response.error||response.success!==true)throw Error(response.error||'停止未获确认');if(_execCurrent(epoch,jobId)){toast(response.message||'停止请求已接收','inf');await _reconnectStream(jobId,epoch)}}
  catch(error){if(_execCurrent(epoch,jobId)){toast('停止失败：'+error.message,'err');document.getElementById('exec-stop-btn').disabled=false}}
  finally{_execStops.delete(jobId)}
}

function appendExecLine(line){
  const log = document.getElementById('exec-log');
  if(line === undefined || line === null) return;
  let color = '';
  if(line.includes('[OK]')) color = 'color:var(--green)';
  else if(line.includes('[FAIL]') || line.includes('[ERROR]') || /error/i.test(line)) color = 'color:var(--red)';
  else if(line.includes('completed!') || line.includes('All configurations')) color = 'color:var(--green);font-weight:600';
  else if(line.includes('Global Progress')) color = 'color:var(--cyan)';
  const gm = line.match(/Grain (\d+)\/(\d+) at (\d+)/);
  if(gm){
    document.getElementById('exec-current').innerHTML =
      `<span style="color:var(--amber)">⚙</span> <span style="color:var(--t1)">${esc(line.trim())}</span>`;
  }
  const div = document.createElement('div');
  div.style.cssText = (color || '') + ';padding:1px 0';
  div.textContent = line;
  log.appendChild(div);
  if(document.getElementById('exec-autoscroll')?.checked)
    log.scrollTop = log.scrollHeight;
}

function updateExecProgress(completed, failed, total){
  total = total || 0;
  const pct = total > 0 ? Math.min(100, Math.round(completed*100/total)) : 0;
  document.getElementById('exec-progress-bar').style.width = pct + '%';
  document.getElementById('exec-progress-text').textContent = `${completed} / ${total}${total?` (${pct}%)`:''}`;
  document.getElementById('exec-done-cnt').textContent = completed;
  document.getElementById('exec-fail-cnt').textContent = failed;
}

