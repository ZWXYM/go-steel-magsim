/* CPU export UI must not dispatch prediction/solve and must preserve verified inputs. */
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
assert.doesNotMatch(fs.readFileSync('templates/workbench.html','utf8'),/function\s+history\s*\(/,'Calibration history must not replace the browser history object');
assert.match(fs.readFileSync('templates/workbench.html','utf8'),/<select id="nativeGrade" onchange="npxControls\(\)">/,'Selecting a valid grade must re-enable export after a missing grade');
class Node{
 constructor(tag='div'){this.tag=tag;this.children=[];this.value='';this.textContent='';this.disabled=false;this.hidden=false;this.href=''}
 append(n){this.children.push(n);if(this.tag==='select'&&!this.value)this.value=n.value}
 replaceChildren(){this.children=[];this.value=''}
 scrollIntoView(){this.scrolled=true}
}
const source={id:'nativesrc_fixture',title:'pilot_20261003_n8',grades:['B30P105'],manifest_sha256:'frozen',n_grains:8,H_grid_points:26,recorded_successes:64,declared_jobs:64};
const record={id:'nativepair_aaaaaaaaaaaa',material_id:'B30P105',source_title:source.title,raw_pair:{material_id:'B30P105',RD:{H:[0,1],B:[0,1]}}};
function harness(url='http://local/workbench#cal'){
 const nodes=new Map(),calls=[],history=[];
 const context=vm.createContext({URL,console,Set,location:{href:url},window:{history:{replaceState:(_,__,v)=>history.push(v.toString())}},history:()=>{throw Error('global history is the calibration function')},
  document:{getElementById:id=>{if(!nodes.has(id))nodes.set(id,new Node(['nativeSource','nativeGrade','nativeHistory'].includes(id)?'select':'div'));return nodes.get(id)},createElement:tag=>new Node(tag)},
  api:async(path,data)=>{calls.push({path,data});if(path==='native-pair-sources')return {sources:[source],issues:[]};if(path==='native-pairs'&&!data)return [record];return record}});
 context.$=id=>context.document.getElementById(id);
 vm.runInContext(fs.readFileSync('static/js/native_pair_exports.js','utf8'),context);
 return {context,nodes,calls,history};
}
const flush=()=>new Promise(r=>setImmediate(r));
(async()=>{
 const h=harness();await flush();assert.equal(h.nodes.get('nativeExport').disabled,false);
 await h.context.npxExport();assert.equal(h.nodes.get('nativeResult').hidden,false);assert.match(h.history.at(-1),/native_pair=nativepair_aaaaaaaaaaaa/);
 assert.equal(h.calls.filter(c=>c.data).length,1);assert.equal(h.nodes.get('rawPair')?.value||'','');
 await h.context.npxLoad();assert.equal(JSON.parse(h.nodes.get('rawPair').value).material_id,'B30P105');assert(h.nodes.get('rawPair').scrolled);
 let hooked;h.context.setPredictionInput=text=>hooked=text;await h.context.npxLoad();assert.equal(JSON.parse(hooked).material_id,'B30P105');delete h.context.setPredictionInput;
 assert(h.calls.every(c=>!/(predict|start|submit|train)/.test(c.path)));
 const original=h.nodes.get('rawPair').value;h.context.api=async()=>{throw Error('保存文件发生变化')};await h.context.npxLoad();assert.equal(h.nodes.get('rawPair').value,original);assert.match(h.nodes.get('nativeActionInfo').textContent,/未载入/);
 const saved=harness('http://local/workbench?native_pair='+record.id+'#cal');await flush();await flush();assert(saved.calls.some(c=>c.path==='native-pairs/'+record.id));assert(saved.calls.every(c=>!c.data));
 const stale=harness();await flush();let finish;const first=new Promise(r=>finish=r);let n=0;stale.context.api=async path=>path==='native-pairs'?[]:(++n===1?first:{sources:[source],issues:[]});const old=stale.context.npxRefresh();await stale.context.npxRefresh();finish({sources:[],issues:[]});await old;assert.equal(stale.nodes.get('nativeSource').children.length,1);assert.equal(stale.nodes.get('nativeExport').disabled,false);
 // Refresh must not silently replace a four-sample grade or another source.
 const selected=harness();await flush();const multi={...source,grades:['B23R075','B27R090','B30P105']},second={...multi,id:'nativesrc_second',title:'second frozen run'};
 let sources=[multi,second];selected.context.api=async(path,data)=>{selected.calls.push({path,data});return path==='native-pair-sources'?{sources,issues:[]}:path==='native-pairs'&&!data?[]:record};
 await selected.context.npxRefresh();selected.nodes.get('nativeGrade').value='B30P105';await selected.context.npxRefresh();
 assert.equal(selected.nodes.get('nativeGrade').value,'B30P105','refresh must retain B30P105 instead of first grade');
 selected.nodes.get('nativeSource').value=second.id;selected.context.npxSourceChanged();selected.nodes.get('nativeGrade').value='B27R090';
 sources=[second,{...multi,grades:[...multi.grades].reverse()}];await selected.context.npxRefresh();assert.equal(selected.nodes.get('nativeSource').value,second.id);assert.equal(selected.nodes.get('nativeGrade').value,'B27R090');
 await selected.context.npxExport();assert.deepEqual(JSON.parse(JSON.stringify(selected.calls.find(c=>c.data).data)),{source_id:second.id,manifest_sha256:'frozen',grade:'B27R090'});
 // A removed grade/source requires explicit selection, with no replacement POST.
 selected.nodes.get('nativeSource').value=second.id;selected.context.npxSourceChanged();selected.nodes.get('nativeGrade').value='B27R090';sources=[{...second,grades:['B23R075']},multi];await selected.context.npxRefresh();
 assert.equal(selected.nodes.get('nativeGrade').value,'');assert.equal(selected.nodes.get('nativeExport').disabled,true);assert.match(selected.nodes.get('nativeSourceInfo').textContent,/B27R090/);
 const posts=selected.calls.filter(c=>c.data).length;await selected.context.npxExport();assert.equal(selected.calls.filter(c=>c.data).length,posts);await selected.context.npxRefresh();assert.equal(selected.nodes.get('nativeGrade').value,'');
 selected.nodes.get('nativeGrade').value='B23R075';selected.context.npxControls();assert.equal(selected.nodes.get('nativeExport').disabled,false);selected.nodes.get('nativeGrade').value='';selected.context.npxControls();assert.equal(selected.nodes.get('nativeExport').disabled,true);
 sources=[multi];await selected.context.npxRefresh();assert.equal(selected.nodes.get('nativeSource').value,'');assert.equal(selected.nodes.get('nativeExport').disabled,true);assert.match(selected.nodes.get('nativeSourceInfo').textContent,/重新选择/);
 await selected.context.npxRefresh();assert.equal(selected.nodes.get('nativeSource').value,'');selected.nodes.get('nativeSource').value=multi.id;selected.context.npxSourceChanged();assert.equal(selected.nodes.get('nativeExport').disabled,false);
 selected.nodes.get('nativeGrade').value='B30P105';selected.context.api=async()=>{throw Error('fixture offline')};await selected.context.npxRefresh();assert.equal(selected.nodes.get('nativeSource').value,multi.id);assert.equal(selected.nodes.get('nativeGrade').value,'B30P105');
 console.log('Native pair UI: export/reopen, explicit load, no auto prediction/native calls, failed load and stale refresh isolation: PASS');
})().catch(e=>{console.error(e);process.exitCode=1});
