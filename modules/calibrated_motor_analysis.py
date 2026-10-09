"""CPU plans and official torque output for BH-only materials; never solve."""
import io
import json
import re
import shutil
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from modules.material_library import sha
from modules.maxwell_material_transport import load_contract, verify_saved_material
from modules.motor_model_audit import audit_project
from modules.motor_workbench import numeric_csv, unit, write_json

VERSION='BH_only_torque_output_v1'
PROTOCOLS={
    'v8_2_cycle1':dict(cycles=1,source_samples=31,stop_s=.01,selected_window_ms=[0,10]),
    'periodic_cycle3_v1':dict(cycles=3,source_samples=91,stop_s=.03,selected_window_ms=[20,30])}


def protocol(name):
    if not isinstance(name,str) or name not in PROTOCOLS: raise ValueError('未知转矩测量协议')
    return dict(PROTOCOLS[name],id=name,speed_rpm=3000,poles=4,points_per_cycle=30,
                requested_time_step_s=1/3000,time_column_units=['s','ms','us','ns'],
                torque_expression='-Moving1.Torque',torque_unit='Nm',native_settings_applied=False)


def collect_torque(path,measurement='v8_2_cycle1'):
    """Same signed official columns/windows as the author scan, without losses."""
    path=Path(path);before=sha(path.read_bytes());p=protocol(measurement)
    headers,values=numeric_csv(path)
    tc=next((h for h in headers if h.split(' [',1)[0]=='Time'),None)
    yc=next((h for h in headers if h.split(' [',1)[0]=='-Moving1.Torque'),None)
    if tc is None or yc is None: raise ValueError('缺少官方 Time / -Moving1.Torque；不得替换转矩符号')
    time=values(tc)*unit(tc,{'s':1,'ms':1e-3,'us':1e-6,'ns':1e-9})
    raw=values(yc)*unit(yc,{'NewtonMeter':1,'Nm':1,'mNewtonMeter':1e-3})
    if len(time)!=p['source_samples'] or not np.allclose(time,np.linspace(0,p['stop_s'],p['source_samples']),atol=1e-10,rtol=1e-8):
        raise ValueError('转矩CSV时间网格不完整或与所选协议不符')
    start=60 if p['cycles']==3 else 0
    selected=raw[start:];mean=float(selected.mean())
    ripple=float((selected.max()-selected.min())/mean*100) if mean!=0 else None
    accepted=bool(mean>0 and selected.min()>0 and selected.max()<10 and ripple is not None and ripple<100)
    stability=None
    if start:
        previous=raw[30:61]
        peak=float(np.max(np.abs(selected-previous)))
        change=abs(mean-float(previous.mean()))/max(abs(mean),1e-12)
        stability=dict(cycles_compared=[2,3],max_phase_torque_delta_Nm=peak,
            relative_mean_torque_change=change,passed=bool(peak<=.1 and change<=.02),
            thresholds=dict(phase_torque_Nm=.1,mean_torque_relative=.02),core_loss_checked=False)
        accepted=accepted and stability['passed']
    if sha(path.read_bytes())!=before: raise ValueError('转矩CSV在读取期间改变')
    return dict(version=VERSION,measurement_protocol=measurement,source_n_samples=len(time),n_samples=len(selected),
        time_ms=(time*1000).tolist(),torque_Nm=raw.tolist(),selected_window_ms=p['selected_window_ms'],
        T_avg_Nm=mean,T_min_Nm=float(selected.min()),T_max_Nm=float(selected.max()),K_T_ripple_pct=ripple,
        torque_numerical_acceptance=bool(accepted),periodic_torque_stability=stability,torque_csv_sha256=before,
        eta_estimate_pct=None,P_Fe_W=None,P_Cu_W=None,efficiency_enabled=False,optimization_ranking_enabled=False,
        scope='Official signed torque only; no loss/efficiency calculation or final material ranking',new_native_solves=0)


class CalibratedMotorAnalysis:
    def __init__(self,native,waveforms,storage):
        self.native,self.waveforms=native,waveforms
        self.storage=Path(storage).resolve();self.storage.mkdir(parents=True,exist_ok=True)

    def path(self,artifact):
        if not isinstance(artifact,str) or not re.fullmatch(r'bhanalysis_[0-9a-f]{12}',artifact):
            raise ValueError('无效B-H转矩分析计划')
        p=(self.storage/artifact).resolve()
        if not p.is_relative_to(self.storage): raise ValueError('分析计划超出目录')
        return p

    def prepare(self,data):
        if not isinstance(data,dict) or set(data)-{'native_import','measurement_protocol','cores'} or 'native_import' not in data:
            raise ValueError('请选择原生导入记录和测量协议')
        source=self.native.get(data['native_import'])
        if source['status'] not in ('ready','imported_not_solved'):
            raise ValueError('请选择当前程序绑定的ready或已成功导入记录')
        measurement=data.get('measurement_protocol','v8_2_cycle1');p=protocol(measurement)
        cores=data.get('cores',source['cores'])
        if type(cores) is not int or not 1<=cores<=32: raise ValueError('核数需为1–32整数')
        origin=self.native.path(source['id']);artifact='bhanalysis_'+uuid.uuid4().hex[:12]
        folder=self.path(artifact);inputs=folder/'inputs';inputs.mkdir(parents=True)
        for name,relative in (('native_import_manifest.json','manifest.json'),('material.amat','inputs/material.amat'),
                              ('material.metadata.json','inputs/material.metadata.json'),('source_package.zip','inputs/source_package.zip')):
            shutil.copy2(origin/relative,inputs/name)
        contract=load_contract(inputs/'material.amat')
        if contract!=source['material_contract']: raise ValueError('分析包与导入材料合同不符')
        manifest=dict(id=artifact,version=VERSION,created_utc=datetime.now(timezone.utc).isoformat(),
            native_import_id=source['id'],preparation_id=source['preparation_id'],material_name=source['material_name'],
            parent_bank_sha256=source['parent_bank_sha256'],effective_bank_sha256=source['effective_bank_sha256'],
            training_grades=source['training_grades'],excluded_grades=source['excluded_grades'],H_axis='physical_A_per_m',H_scale=1,
            cores=cores,measurement_protocol=measurement,requested_protocol=p,material_contract=contract,
            native_submission_allowed=False,efficiency_enabled=False,optimization_ranking_enabled=False,new_native_solves=0,
            input_hashes={f.relative_to(folder).as_posix():sha(f.read_bytes()) for f in inputs.iterdir()})
        write_json(folder/'manifest.json',manifest)
        write_json(folder/'state.json',dict(id=artifact,status='planned_not_submitted'))
        return self.get(artifact)

    def get(self,artifact):
        folder=self.path(artifact);manifest=json.loads((folder/'manifest.json').read_text(encoding='utf8'))
        state=json.loads((folder/'state.json').read_text(encoding='utf8'))
        if (manifest.get('id')!=artifact or state.get('id')!=artifact or manifest.get('version')!=VERSION or
                any(manifest.get(k) is not False for k in ('native_submission_allowed','efficiency_enabled','optimization_ranking_enabled')) or
                manifest.get('new_native_solves')!=0 or manifest.get('requested_protocol')!=protocol(manifest['measurement_protocol']) or
                state.get('status') not in ('planned_not_submitted','model_snapshot_ready_not_submitted')):
            raise ValueError('分析计划身份/协议/执行范围不符')
        for name,digest in manifest['input_hashes'].items():
            p=(folder/name).resolve()
            if not p.is_relative_to(folder/'inputs') or sha(p.read_bytes())!=digest: raise ValueError('冻结分析输入改变：'+name)
        if load_contract(folder/'inputs/material.amat')!=manifest['material_contract']: raise ValueError('冻结分析材料不符')
        result=dict(manifest,**state)
        if state['status']=='model_snapshot_ready_not_submitted':
            binding=json.loads((folder/'model_binding.json').read_text(encoding='utf8'))
            if set(binding['files'])!={'model/motor.aedt'} or binding.get('native_import_id')!=manifest['native_import_id']:
                raise ValueError('分析工程绑定身份或文件清单不符')
            for name,digest in binding['files'].items():
                p=(folder/name).resolve()
                if not p.is_relative_to(folder/'model') or sha(p.read_bytes())!=digest: raise ValueError('已准备工程改变：'+name)
            verify_saved_material((folder/'model/motor.aedt').read_text(encoding='utf-8-sig'),manifest['material_contract'])
            result.update(model_binding=binding,status='model_snapshot_ready_not_submitted')
        else:
            source=self.native.get(manifest['native_import_id'])
            if source['material_contract']!=manifest['material_contract'] or source['effective_bank_sha256']!=manifest['effective_bank_sha256']:
                raise ValueError('导入来源与分析计划不符')
            result.update(native_import_status=source['status'],status='ready_for_model_preparation' if source['status']=='imported_not_solved' else 'awaiting_native_material_import')
        result.update(result_available=False,calibrated_material_has_been_solved=False,
            native_submission_allowed=False,efficiency_enabled=False,optimization_ranking_enabled=False,new_native_solves=0)
        return result

    def prepare_model(self,artifact):
        folder=self.path(artifact);plan=self.get(artifact)
        if plan['status']!='ready_for_model_preparation': raise ValueError('实际原生材料导入与保存预检通过后，才能准备分析工程')
        if (folder/'model').exists() or (folder/'model_binding.json').exists(): raise ValueError('已有模型准备痕迹；保留现场，不覆盖')
        source=self.native.get(plan['native_import_id']);origin=self.native.path(source['id'])
        if sha((origin/'manifest.json').read_bytes())!=plan['input_hashes']['inputs/native_import_manifest.json']:
            raise ValueError('原生导入manifest在计划后改变')
        if source['summary'].get('native_validation')!=1: raise ValueError('原生模型预检未通过')
        text=(origin/'motor.aedt').read_text(encoding='utf-8-sig');verify_saved_material(text,plan['material_contract'])
        audit=audit_project(origin/'motor.aedt')
        if (not audit['local_CS_verified'] or audit['moving_insert_count']!=48 or not audit['core_loss']['complete_insert_coverage'] or
                any(r['material'].casefold()!=plan['material_name'].casefold() for r in audit['inserts'])):
            raise ValueError('实际保存工程的48插片/CS/损耗选择不完整')
        model=folder/'model';model.mkdir()
        shutil.copy2(origin/'motor.aedt',model/'motor.aedt')
        if sha((model/'motor.aedt').read_bytes())!=source['summary']['saved_project_sha256']: raise ValueError('原生工程在复制期间改变')
        binding=dict(native_import_id=source['id'],source_native_summary_sha256=sha((origin/'summary.json').read_bytes()),
            source_native_validation=source['summary']['native_validation'],actual_snapshot_audit=audit,
            requested_protocol=plan['requested_protocol'],native_settings_applied=False,new_native_solves=0,
            files={'model/motor.aedt':sha((model/'motor.aedt').read_bytes())})
        write_json(folder/'model_binding.json',binding)
        write_json(folder/'state.json',dict(id=artifact,status='model_snapshot_ready_not_submitted'))
        return self.get(artifact)

    def records(self):
        rows=[]
        for folder in self.storage.glob('bhanalysis_*'):
            try: rows.append(self.get(folder.name))
            except (OSError,KeyError,ValueError,TypeError) as error: rows.append(dict(id=folder.name,status='invalid',error=str(error)))
        return sorted(rows,key=lambda r:r.get('created_utc',''),reverse=True)

    def bundle(self,artifact):
        record=self.get(artifact);folder=self.path(artifact);out=io.BytesIO()
        with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('plan.json',json.dumps(record,ensure_ascii=False,indent=2))
            for name in record['input_hashes']: archive.write(folder/name,name)
            if record['status']=='model_snapshot_ready_not_submitted':
                archive.write(folder/'model_binding.json','model_binding.json');archive.write(folder/'model/motor.aedt','model/motor.aedt')
        out.seek(0);return out

    def reference(self,dataset,case):
        paths,record=self.waveforms._paths(dataset,case)
        metrics=collect_torque(paths['torque'],record['measurement_protocol'])
        expected=record['metrics']['csv_hashes'].get(str(Path('reports/Torque Plots.csv')))
        if metrics['torque_csv_sha256']!=expected: raise ValueError('参考转矩原CSV与保存案哈希不符')
        for name in ('T_avg_Nm','T_min_Nm','T_max_Nm','K_T_ripple_pct'):
            a,b=metrics[name],record['metrics'][name]
            if a!=b and (a is None or b is None or not np.isclose(a,b,rtol=1e-10,atol=1e-10,equal_nan=False)):
                raise ValueError('参考转矩与原扫描指标不符：'+name)
        return dict(dataset=dataset,case=case,material=record['material'],model_version=record['model_version'],
            metrics=metrics,source_scope='existing_library_reference_not_calibrated_material_result',
            calibrated_material_has_been_solved=False,efficiency_enabled=False,optimization_ranking_enabled=False,
            torque_csv_url='/api/workbench/calibrated-motor/torque-reference/'+dataset+'/'+case+'/csv')

    def reference_csv(self,dataset,case):
        record=self.reference(dataset,case);paths,_=self.waveforms._paths(dataset,case)
        blob=paths['torque'].read_bytes()
        if sha(blob)!=record['metrics']['torque_csv_sha256']: raise ValueError('参考转矩原CSV在下载前改变')
        return io.BytesIO(blob)
