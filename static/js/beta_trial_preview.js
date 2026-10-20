/* Frozen retrospective curves only. Opening this panel never fits or deploys. */
const BTP={generation:0,data:null};
const btp$=id=>document.getElementById(id),btpText=(tag,value)=>{const n=document.createElement(tag);n.textContent=value;return n};
function btpActive(){return btp$('calID').textContent.trim()}
function btpReset(){BTP.generation++;BTP.data=null;btp$('betaTrialPanel').hidden=true;btp$('betaTrialError').textContent='';btp$('betaTrialLoad').disabled=false}
async function btpLoad(){
 const artifact=btpActive(),token=++BTP.generation,button=btp$('betaTrialLoad');button.disabled=true;btp$('betaTrialPanel').hidden=true;btp$('betaTrialError').textContent='';
 try{if(!/^cal_[0-9a-f]{12}$/.test(artifact))throw Error('请先加载四样品跨材料校准记录');
 const response=await fetch('/api/workbench/calibrations/'+artifact+'/beta-trial-preview'),data=await response.json();
 if(token!==BTP.generation||btpActive()!==artifact)return;if(!response.ok)throw Error(data.error||'候选对照读取失败');
 if(data.calibration_id!==artifact||data.read_only!==true||data.default_prediction_changed!==false||data.automatic_deployment!==false)throw Error('对照记录的身份或只读范围不符');
 BTP.data=data;btpRender(data);
 }catch(error){if(token===BTP.generation){BTP.data=null;btp$('betaTrialPanel').hidden=true;btp$('betaTrialError').textContent=error.message}}
 finally{if(token===BTP.generation)button.disabled=false}
}
function btpRender(data){
 btp$('betaTrialPanel').hidden=false;const s=data.summary;
 btp$('betaTrialNotice').textContent='回顾性材料留出开发对照 · 未部署，当前校准库和材料文件不变。'+(s.all_acceptance_gates_passed?'保存的研发接受条件均满足；仍不是新增批次独立验证。':'保存的研发接受条件未全部满足。')+` B800误差<0.05 T：${s.B800_passed_directions}/${s.B800_total_directions}方向。`;
 const table=document.createElement('table'),header=document.createElement('tr');for(const text of ['材料级留出方法','平均RMSE / T','最大|B800误差| / T','最坏方向RMSE / T'])header.append(btpText('th',text));table.append(header);
 for(const [key,label] of [['baseline_saved_nested','原协议材料留出'],['trial_nested','候选材料留出（未部署）'],['reference_only_control','仅参考内插对照']]){const row=document.createElement('tr'),m=s.summaries[key];row.append(btpText('td',label));for(const name of ['mean_rmse_T','max_abs_B800_error_T','max_direction_RMSE_T'])row.append(btpText('td',Number(m[name]).toFixed(6)));table.append(row)}btp$('betaTrialStats').replaceChildren(table);
 const gates=btp$('betaTrialGates');gates.replaceChildren();const labels={mean_RMSE_improvement_at_least_10pct:'平均RMSE下降至少10%',worst_B800_not_increased:'最大B800误差不增加',worst_direction_RMSE_not_increased:'最坏方向RMSE不增加',max_single_direction_regression_at_most_0_01T:'单方向RMSE回退不超过0.01 T',all_B800_below_0_05T:'全部方向B800误差<0.05 T'};
 for(const [name,passed] of Object.entries(s.gates))gates.append(btpText('p',(labels[name]||name)+'：'+(passed?'通过':'未通过')));
 const grades=btp$('betaTrialGrade');grades.replaceChildren();for(const grade of [...new Set(data.curves.map(c=>c.grade))]){const option=btpText('option',grade);option.value=grade;grades.append(option)}
 btp$('betaTrialDirection').value='RD';btp$('betaTrialDownload').href='/api/workbench/calibrations/'+data.calibration_id+'/beta-trial-preview/download';btp$('betaTrialCurvesCSV').href='/api/workbench/calibrations/'+data.calibration_id+'/beta-trial-preview/curves.csv';btp$('betaTrialMetricsCSV').href='/api/workbench/calibrations/'+data.calibration_id+'/beta-trial-preview/metrics.csv';btp$('betaTrialBundle').href='/api/workbench/calibrations/'+data.calibration_id+'/beta-trial-preview/bundle';btpPlot();
}
function btpPlot(){
 if(!BTP.data||BTP.data.calibration_id!==btpActive()){btpReset();return}
 const c=BTP.data.curves.find(c=>c.grade===btp$('betaTrialGrade').value&&c.direction===btp$('betaTrialDirection').value);if(!c)return;
 const svg=btp$('betaTrialCurve');svg.replaceChildren();svg.setAttribute('viewBox','0 0 920 340');
 const series=[['B_baseline','#2466ad'],['B_trial','#c87521'],['B_reference','#21836b']],values=series.flatMap(([key])=>c[key]),lo=Math.min(0,...values),hi=Math.max(lo+.01,...values),maxH=Math.max(...c.H);
 const x=h=>80+790*Math.log1p(h)/Math.log1p(maxH),y=b=>285-250*(b-lo)/(hi-lo);
 const add=(tag,attrs,text)=>{const n=document.createElementNS('http://www.w3.org/2000/svg',tag);for(const [key,value] of Object.entries(attrs))n.setAttribute(key,value);if(text!==undefined)n.textContent=text;svg.append(n)};
 add('path',{d:'M80 25V285H870',stroke:'#64748b',fill:'none'});
 for(const h of [0,10,100,800,10000,50000].filter(h=>h<=maxH))add('text',{x:x(h),y:310,'text-anchor':'middle','font-size':12},String(h));
 for(const b of [lo,(lo+hi)/2,hi])add('text',{x:4,y:y(b)+4,'font-size':12},b.toFixed(3)+' T');
 add('text',{x:870,y:335,'text-anchor':'end','font-size':12},'物理 H / A·m⁻¹（log(1+H)）');
 for(const [key,color] of series){add('polyline',{points:c.H.map((h,i)=>`${x(h)},${y(c[key][i])}`).join(' '),fill:'none',stroke:color,'stroke-width':2});c.H.forEach((h,i)=>add('circle',{cx:x(h),cy:y(c[key][i]),r:1.5,fill:color}))}
 const metric=BTP.data.metrics.find(m=>m.grade===c.grade&&m.direction===c.direction&&m.method==='trial_nested');
 btp$('betaTrialCurveInfo').textContent=`${c.grade} · ${c.direction} · ${c.H.length}个原H节点 · 只用其余材料内层选定 ${c.candidate}。候选RMSE ${Number(metric.rmse_T).toFixed(6)} T，B800误差 ${Number(metric.B800_error_T).toFixed(6)} T。`;
}
if(typeof document!=='undefined'){
 btp$('betaTrialLoad').onclick=btpLoad;btp$('betaTrialGrade').onchange=btpPlot;btp$('betaTrialDirection').onchange=btpPlot;
 if(typeof MutationObserver!=='undefined')new MutationObserver(btpReset).observe(btp$('calID'),{childList:true,characterData:true,subtree:true});
}
