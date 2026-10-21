/* Original AMAT history. Every action reads existing data, without prediction or dispatch. */
const PX={records:[],selected:null,generation:0,detailGeneration:0,initial:true,folder:null},px$=id=>document.getElementById(id),pxText=(tag,text)=>{const n=document.createElement(tag);n.textContent=text;return n};
const pxKinds={paired:'原配对metadata',directory_record:'原目录增强记录',missing:'未附metadata',unreadable:'metadata不可读'};
async function pxAPI(url){const r=await fetch(url),v=await r.json();if(!r.ok)throw Error(v.error||'读取失败');return v}
function pxClear(){++PX.detailGeneration;px$('exportDetail').hidden=true;px$('exportFiles').replaceChildren();px$('exportMetadata').textContent='';px$('exportBundle').hidden=true;px$('exportBundle').href=''}
function pxList(){
 const out=px$('exportEntries');out.replaceChildren();const q=px$('exportFilter').value.trim().toLowerCase();
 for(const row of PX.records){if(![row.material_name,row.relative_file,row.model_id||''].join(' ').toLowerCase().includes(q))continue;
  const button=pxText('button',row.material_name+' · '+row.relative_file);button.className='entry';button.setAttribute('aria-pressed',String(PX.selected===row.id));button.append(pxText('small',(row.available?'材料已保存':'材料缺失，仅元数据')+' · '+(pxKinds[row.metadata_kind]||'来源未标注')));button.onclick=()=>pxOpen(row.id);out.append(button)}
 px$('exportFilterCount').textContent=`显示 ${out.children.length} / ${PX.records.length} 条记录`;
}
async function pxOpen(id){
 const row=PX.records.find(r=>r.id===id);if(!row)return;pxClear();PX.selected=id;pxList();px$('exportError').textContent='';
 const generation=PX.detailGeneration,catalog=PX.generation,current=()=>generation===PX.detailGeneration&&catalog===PX.generation&&PX.selected===id;
 try{const data=await pxAPI('/api/workbench/platform-material-exports/'+id);if(!current())return;
  if(data.id!==id||data.folder!==PX.folder||JSON.stringify(data.files)!==JSON.stringify(row.files))throw Error('原文件或目录与当前条目不一致，请刷新目录。');
  px$('exportTitle').textContent=data.material_name;px$('exportIdentity').textContent=JSON.stringify({file:data.relative_file,model_id:data.model_id||'未记录',prediction_params:data.prediction_params??'未记录',H_axis:data.H_axis||'未标注',metadata:pxKinds[data.metadata_kind],augmentation_source:data.augmentation_source,folder:data.folder},null,2);
  px$('exportMetadata').textContent=data.metadata===null?'未附可读的原始metadata。':JSON.stringify(data.metadata,null,2);px$('exportDetailError').textContent=data.error||'';
  for(const [key,file] of Object.entries(data.files)){if(!['material.amat','metadata.json'].includes(key))throw Error('未知导出文件类型');const tr=document.createElement('tr'),cell=document.createElement('td'),link=pxText('a',file.name);link.href='/api/workbench/platform-material-exports/'+id+'/files/'+key;cell.append(link);tr.append(cell,pxText('td',file.bytes),pxText('td',file.sha256));px$('exportFiles').append(tr)}
  const bundle=px$('exportBundle');bundle.href='/api/workbench/platform-material-exports/'+id+'/bundle';bundle.textContent=data.available?'下载材料与来源包':'下载现有元数据包';bundle.hidden=!Object.keys(data.files).length;px$('exportDetail').hidden=false;
  if(typeof history!=='undefined'&&typeof location!=='undefined')history.replaceState(null,'',location.pathname+'?export='+encodeURIComponent(id));
 }catch(error){if(current()){pxClear();px$('exportError').textContent=error.message}}
}
async function pxLoad(){
 const selected=PX.selected,generation=++PX.generation;pxClear();PX.selected=null;PX.records=[];pxList();px$('exportRefresh').disabled=true;px$('exportCSV').hidden=true;px$('exportCSV').href='';px$('exportError').textContent='';px$('exportStatus').textContent='正在读取保存的导出…';
 try{const data=await pxAPI('/api/workbench/platform-material-exports');if(generation!==PX.generation)return;
  if(!Array.isArray(data.records)||!/^[a-f0-9]{64}$/.test(data.snapshot_sha256)||data.records.some(r=>!/^export_[a-f0-9]{24}$/.test(r.id))||new Set(data.records.map(r=>r.id)).size!==data.records.length)throw Error('返回的导出目录身份不符');
  PX.records=data.records;PX.folder=data.folder;px$('exportFolder').textContent=data.folder;px$('exportStatus').textContent=`${data.records.length} 条保存记录；浏览和下载不新增材料、预测或求解。`;pxList();px$('exportCSV').href='/api/workbench/platform-material-exports/catalog.csv?snapshot='+data.snapshot_sha256;px$('exportCSV').hidden=false;
  let chosen=selected;if(PX.initial&&typeof location!=='undefined'){const query=new URLSearchParams(location.search),ids=query.getAll('export');if(ids.length){chosen=ids.length===1?ids[0]:null;if(ids.length!==1)px$('exportError').textContent='链接包含重复记录，请从列表选择。'}}PX.initial=false;
  if(chosen&&PX.records.some(r=>r.id===chosen))void pxOpen(chosen);else if(chosen)px$('exportError').textContent='所选导出或目录已变化，请重新选择；未自动切换材料。';
 }catch(error){if(generation===PX.generation){px$('exportError').textContent=error.message;px$('exportStatus').textContent='导出目录暂不可读；原文件保持。'}}
 finally{if(generation===PX.generation)px$('exportRefresh').disabled=false}
}
if(typeof document!=='undefined'){px$('exportRefresh').onclick=pxLoad;px$('exportFilter').oninput=pxList;pxLoad()}
