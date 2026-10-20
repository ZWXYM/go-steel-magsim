/* CPU source selection and saved export; prediction is always a separate action. */
let npxSources=[],npxCurrent=null,npxBusy=false,npxRequest=0,npxCatalogLoaded=false;
function npxOptions(node,rows){node.replaceChildren();for(const [value,text] of rows){const o=document.createElement('option');o.value=value;o.textContent=text;node.append(o)}}
function npxControls(){const source=npxSources.find(s=>s.id===$('nativeSource').value),available=source?.grades.includes($('nativeGrade').value);$('nativeExport').disabled=npxBusy||!available;$('nativeSource').disabled=npxBusy;$('nativeGrade').disabled=npxBusy;$('nativeRefresh').disabled=npxBusy;$('nativeHistory').disabled=npxBusy;$('nativeLoad').disabled=npxBusy||!npxCurrent}
function npxSourceChanged(options={}){
 const source=npxSources.find(s=>s.id===$('nativeSource').value),retaining=Object.prototype.hasOwnProperty.call(options,'keepGrade'),missing=retaining&&!source?.grades.includes(options.keepGrade);
 npxOptions($('nativeGrade'),(missing?[['','请重新选择材料…']]:[]).concat(source?source.grades.map(g=>[g,g]):[]));
 if(retaining)$('nativeGrade').value=missing?'':options.keepGrade;
 $('nativeSourceInfo').textContent=source?`${source.n_grains}晶粒/方向 · ${source.H_grid_points}个H节点 · 采样${source.texture_sampling_version||'未登记'} · 已记录成功${source.recorded_successes}/${source.declared_jobs}项。导出时逐项核对脚本、方向与table；数量不代表质量通过。`+(missing?(options.keepGrade?' 原材料 '+options.keepGrade+' 已不在此来源，请重新选择。':' 请重新选择材料后导出。'):''):(npxSources.length?'原选择的仿真来源已不可用，请重新选择来源和材料。':'当前资源目录没有支持的冻结仿真来源。');
 npxControls()
}
async function npxRefresh(){
 const generation=++npxRequest,previous=$('nativeSource').value,grade=$('nativeGrade').value,retaining=npxCatalogLoaded;npxBusy=true;npxControls();
 try{const [catalog,records]=await Promise.all([api('native-pair-sources'),api('native-pairs')]);if(generation!==npxRequest)return;
  if(!Array.isArray(catalog.sources)||!Array.isArray(catalog.issues)||!Array.isArray(records)||catalog.sources.some(s=>!Array.isArray(s.grades)))throw Error('来源目录响应格式不符');
  npxSources=catalog.sources;const missing=retaining&&!npxSources.some(s=>s.id===previous);npxOptions($('nativeSource'),(missing?[['','请重新选择仿真来源…']]:[]).concat(npxSources.map(s=>[s.id,s.title])));
  if(retaining)$('nativeSource').value=missing?'':previous;else{const pilot=npxSources.find(s=>s.title==='pilot_20261003_n8');if(pilot)$('nativeSource').value=pilot.id}
  npxCatalogLoaded=true;npxOptions($('nativeHistory'),[['','重开保存导出…'],...records.map(r=>[r.id,r.id+' · '+(r.error?'文件待检查':r.material_id+' · '+r.source_title)])]);if(npxCurrent)$('nativeHistory').value=npxCurrent.id;
  $('nativeCatalogInfo').textContent=`${npxSources.length}份仿真来源 · ${new Set(npxSources.flatMap(s=>s.grades)).size}种材料。`+(catalog.issues.length?` ${catalog.issues.length}份来源暂不可读。`:'');npxSourceChanged(retaining?{keepGrade:missing?'':grade}:{})
 }catch(e){$('nativeCatalogInfo').textContent='来源读取失败，保留上次选择：'+e.message}finally{if(generation===npxRequest){npxBusy=false;npxControls()}}
}
function npxShow(record){npxCurrent=record;if(npxSources.some(s=>s.id===record.source_id)){$('nativeSource').value=record.source_id;npxSourceChanged();$('nativeGrade').value=record.material_id}$('nativeResult').hidden=false;$('nativeHistory').value=record.id;$('nativeExportInfo').textContent=record.id+' · '+record.material_id+' · '+record.source_title+'。已核对原始文件；未应用参考修正，质量仍为未评定。';const base='/api/workbench/native-pairs/'+record.id;$('nativeRawDownload').href=base+'/files/raw_pair.json';$('nativeSourceDownload').href=base+'/files/source_manifest.json';$('nativeBundleDownload').href=base+'/bundle';const url=new URL(location.href);url.searchParams.set('native_pair',record.id);window.history.replaceState(null,'',url);npxControls()}
async function npxExport(){if(npxBusy)return;const source=npxSources.find(s=>s.id===$('nativeSource').value);if(!source||!source.grades.includes($('nativeGrade').value))return;npxBusy=true;npxControls();$('nativeActionInfo').textContent='正在读取原table并导出；不会启动仿真。';try{const record=await api('native-pairs',{source_id:source.id,manifest_sha256:source.manifest_sha256,grade:$('nativeGrade').value});npxShow(record);$('nativeActionInfo').textContent='导出完成。可下载或载入下方预测输入；预测需单独点击应用。';await npxRefresh()}catch(e){$('nativeActionInfo').textContent='未导出：'+e.message}finally{npxBusy=false;npxControls()}}
async function npxReopen(id){if(!id||npxBusy)return;npxBusy=true;npxControls();try{npxShow(await api('native-pairs/'+encodeURIComponent(id)));$('nativeActionInfo').textContent='已重开保存导出，未重新提取或预测。'}catch(e){$('nativeActionInfo').textContent='无法重开：'+e.message;npxCurrent=null;$('nativeResult').hidden=true}finally{npxBusy=false;npxControls()}}
async function npxLoad(){if(!npxCurrent||npxBusy)return;npxBusy=true;npxControls();try{const record=await api('native-pairs/'+npxCurrent.id);if(typeof setPredictionInput==='function')setPredictionInput(JSON.stringify(record.raw_pair,null,2));else $('rawPair').value=JSON.stringify(record.raw_pair,null,2);$('nativeActionInfo').textContent='已载入下方原始材料JSON。请确认校准记录和预测范围，再点击“应用所选范围”。';$('rawPair').scrollIntoView({block:'center',behavior:'smooth'})}catch(e){$('nativeActionInfo').textContent='未载入：'+e.message}finally{npxBusy=false;npxControls()}}
async function npxInit(){await npxRefresh();const saved=new URL(location.href).searchParams.get('native_pair');if(saved)await npxReopen(saved)}
npxInit();
