/* Home navigation uses real record identities and GET only, with independent failures. */
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const script=fs.readFileSync(path.join(__dirname,'../static/js/system_recent.js'),'utf8');
const {nativeRows,batchRows,materialRows,resultSummary}=require('../static/js/system_recent.js');
const native={id:'nativerun_aaaaaaaaaaaa',status:'completed',recipe:{material_id:'DEMO <sample>'},created_utc:'2026-10-19T16:11:00Z'};
const batch={id:'bhbatch_bbbbbbbbbbbb',status:'completed',created_utc:'2026-10-19T16:22:00Z',items:[{result_available:true,metrics:{torque_numerical_acceptance:false}},{result_available:true,metrics:{torque_numerical_acceptance:true}}]};
const catalog={records:[{id:'cal_cccccccccccc',created_utc:'2026-10-07T12:00:00Z',entries:[{kind:'fit',available:true,key:'fit:B30P105',label:'B30P105'},{kind:'prediction',available:true,key:'prediction:pred_dddddddddddd',label:'DEMO · pred_dddddddddddd'}]}]};
assert.equal(nativeRows([native]).rows[0].href,'/native-material-runs?run=nativerun_aaaaaaaaaaaa');
assert.equal(nativeRows([{...native,id:'../../private'}]).rows[0].href,null);
assert.equal(batchRows([batch]).rows[0].href,'/calibrated-motor/batches?batch=bhbatch_bbbbbbbbbbbb');
assert.match(batchRows([batch]).rows[0].detail,/通过 1，未通过 1/);assert.match(resultSummary([{result_available:true,metrics:{}}]),/未报告 1/);assert.match(resultSummary([{result_available:false}]),/已整理 0\/1/);
assert.match(nativeRows([{id:'nativerun_eeeeeeeeeeee',status:'needs_attention',error:'原文件待检查'}]).rows[0].detail,/时间未登记.*原文件待检查/);
assert.equal(nativeRows([{...native,id:'nativerun_111111111111',created_utc:'2026-10-01'},native]).rows[0].title,'DEMO <sample> · 已完成');
assert.match(materialRows(catalog).count,/1 份校准 · 2 份材料/);assert.equal(materialRows(catalog).rows[1].href,'/material-library?calibration=cal_cccccccccccc&key=prediction%3Apred_dddddddddddd');
assert.equal(materialRows({records:[{...catalog.records[0],id:'javascript:bad'}]}).rows[1].href,null);
assert.throws(()=>materialRows({records:{}}),/格式不符/);assert.equal(nativeRows([]).rows.length,0);
class Node{
 constructor(){this.children=[];this.textContent='';this.disabled=false;this.listeners={}}
 append(...items){this.children.push(...items)}replaceChildren(){this.children=[]}addEventListener(name,fn){this.listeners[name]=fn}
}
function harness(overrides={}){
 const nodes=new Map(),calls=[],handlers={...overrides},document={getElementById:id=>{if(!nodes.has(id))nodes.set(id,new Node());return nodes.get(id)},createElement:()=>new Node()};
 const fixtures={'native-material-runs':[native],'material-inputs':catalog,'calibrated-motor/batches':[batch]};
 vm.runInNewContext(script,{window:{document},Date,fetch:async(url,options)=>{const route=url.replace('/api/workbench/','');calls.push({route,options});const data=handlers[route]?await handlers[route]():fixtures[route];return {ok:true,json:async()=>data}}});
 return {nodes,calls,handlers,refresh:()=>nodes.get('recentRefresh').listeners.click()};
}
const flush=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
 const h=harness();await flush();assert.equal(h.nodes.get('recentRefresh').disabled,false);assert.match(h.nodes.get('recentBatchesCount').textContent,/保存 1/);
 const first=h.nodes.get('recentNative').children[0];assert.equal(first.children[0].textContent,'DEMO <sample> · 已完成');assert.equal(first.children[0].href,'/native-material-runs?run=nativerun_aaaaaaaaaaaa');assert(!first.children[0].innerHTML);
 assert.equal(h.nodes.get('recentMaterials').children[1].children[0].href,'/material-library?calibration=cal_cccccccccccc&key=prediction%3Apred_dddddddddddd');
 assert.equal(h.calls.length,3);assert(h.calls.every(c=>c.options.method==='GET'));assert.deepEqual(h.calls.map(c=>c.route).sort(),['calibrated-motor/batches','material-inputs','native-material-runs']);
 h.handlers['native-material-runs']=()=>{throw Error('fixture unreadable')};await h.refresh();assert.match(h.nodes.get('recentNativeCount').textContent,/fixture unreadable/);assert.equal(h.nodes.get('recentNative').children.length,0);assert.equal(h.nodes.get('recentBatches').children.length,1);assert.equal(h.nodes.get('recentMaterials').children.length,2);
 h.handlers['native-material-runs']=()=>[];h.handlers['material-inputs']=()=>({records:[]});await h.refresh();assert.match(h.nodes.get('recentNative').children[0].textContent,/暂无/);assert.match(h.nodes.get('recentMaterialsCount').textContent,/0 份材料/);
 let finish;h.handlers['native-material-runs']=()=>new Promise(resolve=>finish=resolve);const pending=h.refresh(),count=h.calls.length;await h.refresh();assert.equal(h.calls.length,count);assert.equal(h.nodes.get('recentRefresh').disabled,true);finish([native]);await pending;assert.equal(h.nodes.get('recentRefresh').disabled,false);
 assert(h.calls.every(c=>c.options.method==='GET'));
 console.log('System recent work: exact reopening links, status vs numerical outcomes, independent read failures, empty records and single GET refresh: PASS');
})().catch(e=>{console.error(e);process.exitCode=1});
