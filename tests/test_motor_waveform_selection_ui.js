// Exercise the shipped waveform page with isolated DOM and fake HTTP only.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../static/js/motor_waveforms.js'),'utf8');
const clone=x=>structuredClone(x),decode=s=>s.replace(/&quot;/g,'"').replace(/&#39;/g,"'").replace(/&lt;/g,'<').replace(/&gt;/g,'>').replace(/&amp;/g,'&');
class Element{
 constructor(id){this.id=id;this.disabled=false;this.hidden=false;this.checked=id==='full';this.textContent='';this.dataset={};this.listeners={};this.inputs=[];this.options=[];this._value='';this._html='';this.clientWidth=560}
 set innerHTML(value){this._html=value;
  if(this.id==='frame'){this.options=[...value.matchAll(/<option(?:\s+value="([^"]*)")?[^>]*>([^<]*)<\/option>/g)].map(m=>decode(m[1]===undefined?m[2]:m[1]));this._value=this.options[0]||''}
  if(this.id==='cases')this.inputs=[...value.matchAll(/<input\s+([^>]+)>/g)].map(m=>{const e=new Element('input');e.dataset={index:m[1].match(/data-index="([^"]*)"/)[1],available:m[1].match(/data-available="([^"]*)"/)[1]};e.checked=/\bchecked\b/.test(m[1]);e.disabled=/\bdisabled\b/.test(m[1]);return e});
 }
 get innerHTML(){return this._html}
 set value(v){this._value=this.id==='frame'&&!this.options.includes(v)?'':v}
 get value(){return this._value}
 addEventListener(name,fn){this.listeners[name]=fn}
 fire(name){return this.listeners[name]?.({target:this})}
 querySelectorAll(selector){return selector==='input:checked'?this.inputs.filter(e=>e.checked):this.inputs}
 setAttribute(){}getBoundingClientRect(){return {left:0,width:560}}appendChild(){}remove(){}click(){}
}
const archiveFrame='OldModel | cycle',newFrame='NewModel | cycle';
const baseCatalog=()=>({errors:[],datasets:[
 {id:'v8_2_archive',version:'OldModel',measurement_protocol:'cycle',cases:['a','b','c'].map(id=>({id,material:'Old '+id,available:true,accepted:true}))},
 {id:'scan_aaaaaaaaaaaa',version:'NewModel',measurement_protocol:'cycle',cases:['a','b'].map(id=>({id,material:'New '+id,available:true,accepted:true}))}
]});
const comparison=selection=>({model_version:selection[0].dataset==='v8_2_archive'?'OldModel':'NewModel',measurement_protocol:'cycle',records:selection.map(s=>({...s,material:s.case,accepted:true,time_ms:[0,10],selected_window_ms:[0,10],series:{torque:[1,2],core_loss:[3,4],copper_loss:[5,6]},metrics:{T_avg_Nm:1.5,K_T_ripple_pct:66.6,P_Fe_W:3.5,P_Cu_W:5.5,eta_estimate_pct:90,efficiency_scope:'original'},files:{torque:'/raw.csv'},csv_sha256:{torque:'original-hash'}}))});
function harness(catalog=baseCatalog()){
 const nodes=new Map(),calls=[],handlers=new Map(),state={catalog};const el=id=>{if(!nodes.has(id))nodes.set(id,new Element(id));return nodes.get(id)};
 const document={getElementById:el,createElement:()=>new Element('a'),body:new Element('body')};
 const context=vm.createContext({document,window:{document,addEventListener:()=>{}},setTimeout:()=>0,URL:{createObjectURL:()=>'/mock.zip',revokeObjectURL:()=>{}},fetch:async(url,options={})=>{
  const route=url.replace('/api/workbench/waveforms/',''),body=options.body?JSON.parse(options.body):undefined;calls.push({route,method:options.method||'GET',body});
  let value=handlers.has(route)?await handlers.get(route)(body):route==='catalog'?clone(state.catalog):route==='compare'?comparison(body.selection):{};
  return {ok:value?.ok!==false,json:async()=>clone(value?.ok===false?value.data:value),blob:async()=>({})};
 }});
 vm.runInContext(source,context);const ready=()=>new Promise(resolve=>setImmediate(resolve));
 const choose=indices=>{const inputs=el('cases').inputs;for(const e of inputs)e.checked=indices.includes(Number(e.dataset.index));inputs[0]?.fire('change')};
 const selected=()=>el('cases').inputs.filter(e=>e.checked).map(e=>{const flat=state.catalog.datasets.flatMap(d=>d.cases.map(c=>({dataset:d.id,case:c.id})));return flat[Number(e.dataset.index)]});
 return {el,calls,handlers,state,ready,choose,selected,refresh:()=>el('refresh').fire('click'),frame:value=>{el('frame').value=value;return el('frame').fire('change')},compare:()=>el('compare').fire('click'),download:()=>el('download').fire('click')};
}
const defer=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject}};
(async()=>{
 const h=harness();await h.ready();assert.equal(h.el('frame').value,newFrame);assert.equal(h.selected().length,2);
 h.frame(archiveFrame);h.choose([0]);await h.refresh();assert.equal(h.el('frame').value,archiveFrame,'catalog refresh must retain chosen model and protocol');
 assert.deepEqual(h.selected(),[{dataset:'v8_2_archive',case:'a'}]);assert.match(h.el('selectionNotice').textContent,/保留 1/);
 // Stable source IDs survive dataset/case ordering changes; indices are not identities.
 h.state.catalog.datasets.reverse();h.state.catalog.datasets.find(d=>d.id==='v8_2_archive').cases.reverse();await h.refresh();
 assert.equal(h.el('frame').value,archiveFrame);assert.deepEqual(h.selected(),[{dataset:'v8_2_archive',case:'a'}]);
 await h.compare();assert.deepEqual(h.calls.at(-1).body.selection,[{dataset:'v8_2_archive',case:'a'}]);assert.match(h.el('metrics').innerHTML,/1\.5000/);assert.match(h.el('sources').innerHTML,/original-hash/);
 await h.download();assert.deepEqual(h.calls.at(-1).body.selection,[{dataset:'v8_2_archive',case:'a'}]);
 // An unavailable/removed chosen case is identified without selecting a replacement.
 h.state.catalog.datasets.find(d=>d.id==='v8_2_archive').cases.find(c=>c.id==='a').available=false;await h.refresh();
 assert.equal(h.selected().length,0);assert.equal(h.el('compare').disabled,true);assert.equal(h.el('download').disabled,true);assert.match(h.el('selectionNotice').textContent,/v8_2_archive \/ a/);
 assert.equal(h.el('cases').inputs.find(e=>e.dataset.available==='false').disabled,true);await h.refresh();assert.equal(h.selected().length,0);
 const removed=harness();await removed.ready();removed.frame(archiveFrame);removed.choose([0]);removed.state.catalog.datasets[0].cases.shift();await removed.refresh();assert.equal(removed.selected().length,0);assert.match(removed.el('selectionNotice').textContent,/v8_2_archive \/ a/);
 const partial=harness();await partial.ready();partial.frame(archiveFrame);partial.state.catalog.datasets[0].cases[0].available=false;await partial.refresh();assert.deepEqual(partial.selected(),[{dataset:'v8_2_archive',case:'b'}]);
 // Different scans can share case_01; retain the full dataset/case identity.
 const sameFrame=harness({errors:[],datasets:['scan_111111111111','scan_222222222222'].map(id=>({id,version:'NewModel',measurement_protocol:'cycle',cases:[{id:'case_01',material:id,available:true,accepted:true}]}))});
 await sameFrame.ready();sameFrame.choose([0]);sameFrame.state.catalog.datasets.reverse();await sameFrame.refresh();assert.deepEqual(sameFrame.selected(),[{dataset:'scan_111111111111',case:'case_01'}]);
 // Removing a whole frame requires explicit choice, even across subsequent refreshes.
 const gone=harness();await gone.ready();gone.frame(archiveFrame);gone.state.catalog.datasets.shift();await gone.refresh();assert.equal(gone.el('frame').value,'');assert.equal(gone.el('compare').disabled,true);assert.equal(gone.selected().length,0);assert.match(gone.el('selectionNotice').textContent,/OldModel/);
 await gone.refresh();assert.equal(gone.el('frame').value,'');gone.frame(newFrame);assert.equal(gone.selected().length,2);
 // Intentionally empty selection remains empty, and initial empty catalogs remain usable.
 const empty=harness();await empty.ready();empty.choose([]);await empty.refresh();assert.equal(empty.selected().length,0);assert.equal(empty.el('compare').disabled,true);
 const none=harness({errors:[],datasets:[]});await none.ready();assert.equal(none.el('compare').disabled,true);none.state.catalog=baseCatalog();await none.refresh();none.frame(archiveFrame);assert.equal(none.selected().length,2);
 // Failed reads retain the previous selection; a pending refresh/compare is single.
 const failure=harness();await failure.ready();failure.frame(archiveFrame);failure.choose([0]);failure.handlers.set('catalog',()=>{throw Error('fixture catalog unavailable')});await failure.refresh();assert.equal(failure.el('frame').value,archiveFrame);assert.deepEqual(failure.selected(),[{dataset:'v8_2_archive',case:'a'}]);assert.match(failure.el('error').textContent,/保留上次选择/);
 failure.handlers.set('catalog',()=>({datasets:{},errors:[]}));await failure.refresh();assert.equal(failure.el('frame').value,archiveFrame);assert.deepEqual(failure.selected(),[{dataset:'v8_2_archive',case:'a'}]);assert.match(failure.el('error').textContent,/格式不符/);
 const pending=harness();await pending.ready();const load=defer();pending.handlers.set('catalog',()=>load.promise);const first=pending.refresh();const count=pending.calls.length;await pending.refresh();await pending.compare();assert.equal(pending.calls.length,count);load.resolve(baseCatalog());await first;
 const waiting=defer();pending.handlers.set('compare',()=>waiting.promise);const firstCompare=pending.compare();const requests=pending.calls.length;await pending.compare();await pending.download();await pending.refresh();assert.equal(pending.calls.length,requests);waiting.resolve(comparison(pending.selected()));await firstCompare;
 assert.equal(pending.el('download').disabled,false);assert.equal(pending.el('result').hidden,false);
 console.log('Waveform selection UI: refresh preserves frame/source IDs/empty choice, handles missing data, keeps POST payloads exact and prevents overlap: PASS');
})().catch(e=>{console.error(e);process.exitCode=1});
