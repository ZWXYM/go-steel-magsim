/* Real window controller: source/interval binding, original plots and GET-only exports. */
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const {plotData,chartMarkup}=require('../static/js/torque_windows.js');
const VERSION='calibrated_batch_torque_windows_v1',A='bhbatch_aaaaaaaaaaaa',B='bhbatch_bbbbbbbbbbbb';
const catalog={version:VERSION,batches:[A,B].map(id=>({id,status:'completed',items:[{id:'item_01',material_name:id===A?'material_A':'material_B',selection:{key:'fit:known'},available:true,original_acceptance:false,original_window_ms:[0,2],time_range_ms:[0,2]},{id:'item_02',material_name:'unavailable',selection:{key:'prediction:pending'},available:false,error:'no original result'}]}))};
const payload=(batch=A,window=[0,2])=>{const indices=[0,1,2].filter(i=>i>=window[0]&&i<=window[1]);return {version:VERSION,batch,snapshot_sha256:'c'.repeat(64),requested_window_ms:window,records:[{item:'item_01',material_name:batch===A?'material_A':'material_B',selection:{key:'fit:known'},execution_id:'bhexec_aaaaaaaaaaaa',source:{effective_bank_sha256:'bank',excluded_grades:[]},model_version:'V8_2',measurement_protocol:'v8_2_cycle1',time_ms:[0,1,2],torque_Nm:[0,2,177],original_metrics:{T_avg_Nm:59.666,torque_numerical_acceptance:false},original_findings:['spike preserved'],analysis_url:'/calibrated-motor/analysis/plan',raw_csv_url:'/source.csv',torque_csv_sha256:'d'.repeat(64),window_statistics:{sample_indices:indices,n_samples:indices.length,requested_window_ms:window,sampled_window_ms:[indices[0],indices.at(-1)],sample_mean_torque_Nm:59.666,min_torque_Nm:0,max_torque_Nm:177,peak_to_peak_over_abs_mean_pct:300,speed_rpm:3000,electromagnetic_mechanical_power_W:18744}}]}};
class Node{
 constructor(tag='div'){this.tag=tag;this.children=[];this.style={};this.dataset={};this.listeners={};this._value='';this.textContent='';this.innerHTML='';this.disabled=false;this.hidden=false;this.checked=false;this.clientWidth=560;this.attrs={}}
 get value(){return this._value}set value(value){this._value=String(value)}
 append(...nodes){this.children.push(...nodes);if(this.tag==='select'&&!this.value&&nodes.length)this.value=nodes[0].value}
 replaceChildren(...nodes){this.children=nodes;if(this.tag==='select')this.value=''}
 addEventListener(name,fn){this.listeners[name]=fn}fire(name,event={}){return this.listeners[name]?.({target:this,...event})}
 querySelectorAll(query){return [...(query==='input'&&this.tag==='input'||query==='input:checked'&&this.tag==='input'&&this.checked?[this]:[]),...this.children.flatMap(child=>child.querySelectorAll(query))]}
 setAttribute(name,value){this.attrs[name]=value}getBoundingClientRect(){return {left:0,width:560}}
}
function harness(url='http://127.0.0.1:5076/calibrated-motor/torque-windows'){
 const nodes=new Map(),calls=[],pending=[],downloads=[],revoked=[],get=id=>{if(!nodes.has(id))nodes.set(id,new Node(id==='twBatch'?'select':'div'));return nodes.get(id)};
 get('twResult').hidden=true;const location={href:url},history={replaceState:(state,title,url)=>location.href=url};
 class LocalURL extends URL{static createObjectURL(){return 'blob:window_fixture'}static revokeObjectURL(value){revoked.push(value)}}
 const document={getElementById:get,createElement:tag=>tag==='a'?{click(){downloads.push({name:this.download,href:this.href})}}:new Node(tag)};
 const c=vm.createContext({window:{document,location,history,addEventListener(){}},document,URL:LocalURL,URLSearchParams,setTimeout:fn=>fn(),console,fetch:(url,options={})=>{calls.push({url,method:options.method||'GET'});return new Promise((resolve,reject)=>pending.push({resolve,reject}))}});
 vm.runInContext(fs.readFileSync('static/js/torque_windows.js','utf8'),c);return {c,get,calls,pending,downloads,revoked,location};
}
function respond(request,data,status=200){request.resolve({ok:status>=200&&status<300,status,json:async()=>data,blob:async()=>({size:100})})}
const tick=()=>new Promise(resolve=>setImmediate(resolve));
async function ready(h,data=catalog){respond(h.pending.shift(),data);await tick()}
(async()=>{
 const data=payload(),before=JSON.stringify(data),full=plotData(data.records,false),selected=plotData(payload(A,[1,2]).records,true);assert.equal(full.lines[0].points.length,3);assert.equal(full.lines[0].points[0].v,0);assert.equal(full.lines[0].points.at(-1).v,177);assert.equal(selected.lines[0].points.length,2);assert.equal(JSON.stringify(data),before);assert.equal((chartMarkup(full).match(/<polyline /g)||[]).length,1);
 const h=harness();await ready(h);assert.equal(h.get('twBatch').value,A);assert.equal(h.get('twItems').querySelectorAll('input')[1].disabled,true);assert.equal(h.get('twStart').value,'0');
 const request=h.get('twApply').fire('click'),repeat=h.get('twApply').fire('click');assert.equal(h.calls.length,2);assert.equal(h.get('twApply').disabled,true);respond(h.pending.shift(),payload());await Promise.all([request,repeat]);assert.equal(h.get('twResult').hidden,false);assert.ok(h.get('twMetrics').innerHTML.includes('177.000000'));assert.ok(h.get('twMetrics').innerHTML.includes('未通过'));assert.ok(h.get('twFull').innerHTML.includes('polyline'));h.get('twFull').onmousemove({clientX:80});assert.match(h.get('twFullProbe').textContent,/N·m/);assert.match(h.get('twBookmark').href,/items=item_01/);
 let download=h.get('twDownload').fire('click');h.get('twDownload').fire('click');assert.equal(h.calls.length,3);assert.match(h.calls.at(-1).url,/snapshot=cccc/);respond(h.pending.shift(),{});await download;assert.equal(h.downloads.length,1);assert.equal(h.revoked.length,1);
 h.get('twStart').value='1';h.get('twStart').fire('input');assert.equal(h.get('twResult').hidden,true);assert.equal(h.get('twDownload').disabled,true);let apply=h.get('twApply').fire('click');respond(h.pending.shift(),payload(A,[1,2]));await apply;assert.equal((h.get('twZoom').innerHTML.match(/<polyline /g)||[]).length,1);
 const refresh=h.get('twRefresh').fire('click');assert.equal(h.get('twItems').querySelectorAll('input')[0].disabled,true);respond(h.pending.shift(),catalog);await refresh;assert.equal(h.get('twStart').value,'1');assert.equal(h.get('twItems').querySelectorAll('input')[0].checked,true);

 const late=harness();await ready(late);const first=late.get('twApply').fire('click'),old=late.pending.shift();late.get('twBatch').value=B;late.get('twBatch').fire('change');const second=late.get('twApply').fire('click');respond(late.pending.shift(),payload(B));await second;respond(old,payload(A));await first;assert.match(late.get('twIdentity').textContent,/bhbatch_bbbbbbbbbbbb/);assert.doesNotMatch(late.get('twMetrics').innerHTML,/material_A/);
 const failed=late.get('twApply').fire('click');respond(late.pending.shift(),{error:'source file changed'},400);await failed;assert.match(late.get('twError').textContent,/source file changed/);assert.equal(late.get('twDownload').disabled,true);assert.equal(late.get('twBatch').value,B);
 const retry=late.get('twApply').fire('click');respond(late.pending.shift(),payload(B));await retry;download=late.get('twDownload').fire('click');respond(late.pending.shift(),{error:'snapshot changed'},400);await download;assert.match(late.get('twError').textContent,/snapshot changed/);assert.equal(late.downloads.length,0);

 const restored=harness('http://127.0.0.1:5076/calibrated-motor/torque-windows?batch='+B+'&items=item_01&start_ms=1&stop_ms=2');await ready(restored);assert.equal(restored.get('twBatch').value,B);assert.equal(restored.get('twStart').value,'1');assert.equal(restored.calls.length,1);assert.equal(restored.get('twResult').hidden,true);
 const missing=harness('http://127.0.0.1:5076/calibrated-motor/torque-windows?batch=bhbatch_cccccccccccc');await ready(missing);assert.equal(missing.get('twBatch').value,'');assert.equal(missing.get('twApply').disabled,true);
 const noevent=harness();await ready(noevent);apply=noevent.get('twApply').fire('click');noevent.get('twStop').value='1';respond(noevent.pending.shift(),payload());await apply;assert.equal(noevent.get('twResult').hidden,true);
 const wrong=harness();await ready(wrong);apply=wrong.get('twApply').fire('click');respond(wrong.pending.shift(),payload(B));await apply;assert.match(wrong.get('twError').textContent,/不一致/);assert.equal(wrong.get('twResult').hidden,true);
 const invalid=harness();await ready(invalid);invalid.get('twStart').value='';await invalid.get('twApply').fire('click');assert.equal(invalid.calls.length,1);assert.match(invalid.get('twError').textContent,/有效/);
 assert.ok([h,late,restored,missing,noevent,wrong,invalid].every(h=>h.calls.every(call=>call.method==='GET')));
 const html=fs.readFileSync('templates/torque_windows.html','utf8');assert.ok(html.includes('/static/js/torque_windows.js'));assert.ok(html.includes('全段原始转矩'));assert.ok(html.includes('铭牌额定轴功率'));
 console.log('Torque windows: all original points, separate zoom, frozen input/download, single actions, stale/error isolation, bookmarks and GET-only access: PASS');
})().catch(error=>{console.error(error);process.exitCode=1});
