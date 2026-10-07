// Exercise the actual workbench startup with one backend module unavailable.
// No browser, native process or network request is launched by this check.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const html = fs.readFileSync(path.join(__dirname, '../templates/workbench.html'), 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
const elements = new Map();
const element = id => {
  if (!elements.has(id)) elements.set(id, {innerHTML:'', textContent:'', style:{}, disabled:false, hidden:false});
  return elements.get(id);
};
let catalogFails = true;
const source = {
  resources: {cpu_only:true},
  samples: {samples: ['B23R075','B27R090','B27R095','B30P105'].map(grade => ({grade,
    params:{f_Goss:.9,theta_0_deg:3,halfwidth_deg:5,Si_content:3.1}}))},
  calibrations: [],
  'motor/jobs': [],
  'motor/queue': {active_ownership:[],queued:0,needs_attention:0},
  'motor/catalog': {available:true,materials:['AK Steel - M-6 Carlite'],template:'V8_2',license:{available:false}},
  'motor/archive': [{case:'nippon_steel_23zdkh75',version:'V8_2/final_resolved',csv_verified:false,
    display_metrics:{T_avg_Nm:1.8673,P_Fe_W:17.041,eta_estimate_pct:89.86},
    reason:'final CSV 缺失',files:{'final_resolved_official_report.json':{}}}]
};
const context = vm.createContext({
  document: {getElementById:element,querySelectorAll:()=>[]},
  setTimeout:()=>0,
  fetch:async url=>{
    const route=url.replace('/api/workbench/','');
    if(route==='motor/catalog'&&catalogFails) return {ok:false,json:async()=>({error:'测试目录不可访问'})};
    assert.ok(Object.hasOwn(source,route), 'Unexpected startup request: '+route);
    return {ok:true,json:async()=>source[route]};
  }
});
vm.runInContext(script,context);
(async()=>{
  // A complete startup call, not an arbitrary timer delay.
  await vm.runInContext('init()',context);
  for(const grade of ['B23R075','B27R090','B27R095','B30P105']) assert.match(element('samples').innerHTML,new RegExp(grade));
  assert.match(element('archive').innerHTML,/1\.8673/);
  assert.match(element('archive').innerHTML,/原保存摘要/);
  assert.match(element('archive').innerHTML,/final_resolved_official_report\.json/);
  assert.match(element('templateInfo').textContent,/电机目录加载失败/);
  assert.match(element('queueInfo').textContent,/排队 0/);
  assert.equal(element('prepareMotor').disabled,true);
  assert.equal(element('recoverQueue').disabled,true);
  assert.equal(element('exportArchive').disabled,false);
  catalogFails=false;
  await vm.runInContext('init()',context);
  assert.match(element('materials').innerHTML,/M-6 Carlite/);
  assert.match(element('templateInfo').textContent,/仍可准备任务和整理结果/);
  assert.equal(element('prepareMotor').disabled,false);
  assert.equal(element('recoverQueue').disabled,true);
  console.log('Workbench module failure isolation and recovery: PASS');
})().catch(error=>{console.error(error);process.exitCode=1});
