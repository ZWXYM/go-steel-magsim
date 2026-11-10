/* Frozen configuration is visible without changing inputs or starting work. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.resolve(__dirname,'../static/js/motor_optimization.js'),'utf8');
const plan={id:'plan_aaaaaaaaaaaa',status:'watching',job_id:'scan_bbbbbbbbbbbb',materials:['M6','GO'],reference_material:'M6',cores:2,measurement_protocol:'v8_2_cycle1',config:{min_ripple_reduction_pct:30,max_iron_loss_increase_pct:0,min_torque_change_pct:-.5,ripple_weight:.6,iron_loss_weight:.3,efficiency_weight:10}};
function harness(answer){let open=[],html='';const calls=[],c=vm.createContext({document:undefined,console,setTimeout:()=>0,fetch:async(url,options={})=>{calls.push({url,options});return {ok:true,json:async()=>answer()}}});vm.runInContext(source,c);c.document={querySelectorAll:s=>s==='#plans details[data-plan-config][open]'?open:[],getElementById:id=>{assert.equal(id,'plans');return {set innerHTML(v){html=v},get innerHTML(){return html}}}};return {c,calls,open:id=>open=[{dataset:{planConfig:id}}],close:()=>open=[],html:()=>html}}
(async()=>{
 const h=harness(()=>[plan]),initial=JSON.stringify(plan);const text=h.c.planSavedConfig(plan);
 assert.match(text,/<dt>计划核数<\/dt><dd>2<\/dd>/);assert.match(text,/<dt>转矩最小变化 %<\/dt><dd>-0.5<\/dd>/);assert.match(text,/<dt>铁损最大增幅 %<\/dt><dd>0<\/dd>/);assert.match(text,/<dd>v8_2_cycle1<\/dd>/);assert.equal(JSON.stringify(plan),initial);assert.equal(h.calls.length,0);
 const missing=h.c.planSavedConfig({id:plan.id,cores:2.5,measurement_protocol:'',config:{min_torque_change_pct:null,ripple_weight:NaN}});assert.equal((missing.match(/<dd>未保存<\/dd>/g)||[]).length,8);assert.doesNotMatch(missing,/<dd>0<\/dd>/);
 const hostile=h.c.planSavedConfig({...plan,id:'<id>',measurement_protocol:'<script>alert(1)</script>'});assert.doesNotMatch(hostile,/<script>/);assert.match(hostile,/&lt;script&gt;/);assert.match(hostile,/data-plan-config="&lt;id&gt;"/);
 h.open(plan.id);await h.c.planRecords();assert.match(h.html(),/data-plan-config="plan_aaaaaaaaaaaa" open/);h.close();await h.c.planRecords();assert.doesNotMatch(h.html(),/data-plan-config="plan_aaaaaaaaaaaa" open/);assert.ok(h.calls.every(c=>!c.options.method));
 h.open('plan_cccccccccccc');await h.c.planRecords();assert.doesNotMatch(h.html(),/data-plan-config="plan_aaaaaaaaaaaa" open/);
 let done;const pending=new Promise(r=>done=r),late=harness(()=>pending),read=late.c.planRecords();late.open(plan.id);done([plan]);await read;assert.match(late.html(),/data-plan-config="plan_aaaaaaaaaaaa" open/);
 let finishOld,n=0;const old=new Promise(r=>finishOld=r),race=harness(()=>++n===1?old:[{...plan,cores:3}]);const older=race.c.planRecords();await race.c.planRecords();finishOld([plan]);await older;assert.match(race.html(),/<dt>计划核数<\/dt><dd>3<\/dd>/);
 console.log('Saved plan configuration: exact frozen limits, missing values, escaped labels, stable open state and latest response; no writes: PASS');
})().catch(error=>{console.error(error);process.exitCode=1});
