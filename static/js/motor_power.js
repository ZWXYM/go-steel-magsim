/* Derived display values only: no defaults, requests or changes to saved metrics. */
(function(root){
 'use strict';
 function fromExecution(record){
  const speed=record?.requested_protocol?.speed_rpm,torque=record?.result?.metrics?.T_avg_Nm;
  if(record?.result_available!==true||record.result?.native_configuration_verified!==true||record.result?.native_analyze_succeeded!==true||typeof speed!=='number'||!Number.isFinite(speed)||speed<=0||typeof torque!=='number'||!Number.isFinite(torque))return null;
  const power=torque*2*Math.PI*speed/60;return Number.isFinite(power)?{speed_rpm:speed,power_W:power}:null;
 }
 function fromBatchItem(item,record){
  const source=item?.source,m=item?.metrics,actual=record?.result?.metrics;
  if(item?.result_available!==true||!/^bhexec_[a-f0-9]{12}$/.test(item.execution_id)||record?.id!==item.execution_id||item.records?.execution?.id!==record.id||item.records?.analysis_plan?.id!==record.analysis_plan_id||!source||!['material_name','effective_bank_sha256'].every(k=>typeof source[k]==='string'&&source[k])||!Array.isArray(source.excluded_grades)||!Array.isArray(record.excluded_grades)||typeof item.measurement_protocol!=='string'||!item.measurement_protocol||typeof item.model_version!=='string'||!item.model_version||record.material_name!==source.material_name||record.result?.material_name!==source.material_name||record.effective_bank_sha256!==source.effective_bank_sha256||JSON.stringify(record.excluded_grades)!==JSON.stringify(source.excluded_grades)||record.measurement_protocol!==item.measurement_protocol||record.result?.model_version!==item.model_version||!m||!actual||typeof m.torque_csv_sha256!=='string'||!/^[a-f0-9]{64}$/.test(m.torque_csv_sha256)||m.torque_csv_sha256!==actual.torque_csv_sha256||m.T_avg_Nm!==actual.T_avg_Nm)return null;
  return fromExecution(record);
 }
 const api={fromExecution,fromBatchItem};if(typeof module==='object'&&module.exports)module.exports=api;else root.MotorPower=api;
})(typeof window==='object'?window:globalThis);
