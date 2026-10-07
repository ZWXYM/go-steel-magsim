"""User-facing four-sample calibration and source-bound transfer artifacts."""
from __future__ import annotations

import copy
import csv
import json
import sys
import uuid
import zipfile
from datetime import datetime,timezone
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parent))

import numpy as np

from modules.material_calibration import (CalibrationBank, PHYSICS_VERSION, VERSION,
    file_hash, features, validated_curve)
from modules.calibration_transfer import (VERSION as TRANSFER_VERSION, TransferBank,
    load_bank, nested_validation, protocol, digest, select_candidate)
from modules.maxwell_exporter import export_calibrated_pair
from modules.native_quality import audit_pilot, unassessed_quality
from modules.calibration_domain import bank_parameter_support
from modules.calibration_prediction import predict_pair
from tools.analyze_sampling_convergence import read_ensemble
from tools.run_calibration_pilot import H_GRID, metrics,write_json,write_csv

GRADES=('B23R075','B27R090','B27R095','B30P105')


class CalibrationWorkbench:
    def __init__(self,root,storage):
        self.root=Path(root).resolve()
        self.storage=Path(storage).resolve()
        self.pilot=self.root/'calibration/pilot_20261003_n8'

    def source(self):
        payload=json.loads((self.pilot/'calibration_bank.json').read_text(encoding='utf-8'))
        manifest=json.loads((self.pilot/'manifest.json').read_text(encoding='utf-8'))
        if payload['manifest_sha256']!=file_hash(self.pilot/'manifest.json'):
            raise ValueError('标定基准 manifest 已改变')
        CalibrationBank(payload)
        return payload,manifest

    def samples(self,*,include_quality=False):
        payload,manifest=self.source()
        quality=audit_pilot(self.pilot,GRADES)
        result=[]
        for m in manifest['materials']:
            pair=self._bound_raw_pair(m,quality[m['grade']])
            curves={}
            for d in ('RD','TD'):
                raw=pair[d]
                a=next(a for a in payload['anchors'] if a['grade']==m['grade'] and a['direction']==d)
                p=self.pilot/a['reference']['path']
                if file_hash(p)!=a['reference']['sha256']:
                    raise ValueError('参考曲线已改变')
                ref=np.loadtxt(p,delimiter=',',comments='#')
                curves[d]=dict(H=H_GRID.tolist(),raw_B=raw['B'],
                    reference_B=np.interp(H_GRID,ref[:,0],ref[:,1]).tolist())
            sample=dict(grade=m['grade'],params=m['params'],thickness_mm=m['thickness_mm'],
                composition_status=m['Si_status'],texture_status=m['ODF_status'],
                reference_status='现有已处理参考曲线',curves=curves)
            if include_quality:
                sample['native_quality']=quality[m['grade']]
            result.append(sample)
        return dict(samples=result,physics_version=payload['physics_version'],
            texture_sampling_version=payload.get('texture_sampling_version','legacy_multi_peak_importance_v1'),
            calibration_version=VERSION,H_axis='physical_A_per_m')

    def _bound_raw_pair(self,material,quality):
        pair=json.loads((self.pilot/material['grade']/'raw_pair.json').read_text(encoding='utf-8'))
        if (pair['material_id']!=material['grade'] or pair['params']!=material['params']
                or pair['manifest_sha256']!=quality['source_manifest_sha256']
                or pair['simulation_physics_version']!=PHYSICS_VERSION
                or pair['thickness_mm']!=material['thickness_mm']):
            raise ValueError('原生材料对与已核实来源参数不符')
        for d in ('RD','TD'):
            if (not np.array_equal(pair[d]['H'],H_GRID)
                    or not np.allclose(pair[d]['B'],quality['directions'][d]['guarded_mean_B_T'],atol=1e-12,rtol=0)):
                raise ValueError('原生材料对与已核实 table 聚合不符')
        return pair

    def _attach_quality(self,directory,report,fits,samples,grades):
        report['native_quality']={g:samples[g]['native_quality'] for g in grades}
        report['native_quality_sha256']=digest(report['native_quality'])
        report['native_quality_file']='native_quality.json'
        report['strict_training_eligible']=all(q['strict_training_eligible'] for q in report['native_quality'].values())
        write_json(directory/report['native_quality_file'],report['native_quality'])
        for (grade,direction),curve in fits.items():
            curve['native_quality']=samples[grade]['native_quality']
            curve['calibration_native_quality_sha256']=report['native_quality_sha256']

    def calibrate(self,grades,calibration_version=VERSION):
        if calibration_version not in (VERSION,TRANSFER_VERSION):
            raise ValueError('未知校准协议')
        if not isinstance(grades,list) or len(grades)<2 or len(set(grades))!=len(grades) or not set(grades)<=set(GRADES):
            raise ValueError('请至少选择两个不同的已知材料')
        if calibration_version==TRANSFER_VERSION and len(grades)<4:
            raise ValueError('嵌套材料留出至少需要四个材料；少材料请使用原协议')
        payload,manifest=self.source()
        samples={s['grade']:s for s in self.samples(include_quality=True)['samples']}
        # Confirm executed bytes and actual aggregates, not merely a saved fit.
        sources={}
        for grade in grades:
            for d in ('RD','TD'):
                values,source=read_ensemble(self.pilot,grade,d)
                raw=np.asarray(samples[grade]['curves'][d]['raw_B'])
                np.testing.assert_allclose(raw,values.mean(axis=0),atol=1e-12)
                sources[grade+'_'+d]=source
        if calibration_version==TRANSFER_VERSION:
            return self._generalize(grades,samples,sources,payload)
        payload=copy.deepcopy(payload)
        payload['anchors']=[a for a in payload['anchors'] if a['grade'] in grades]
        bank=CalibrationBank(payload)
        rows,comparisons,fits=[],[],{}
        for grade in grades:
            sample=samples[grade]
            fold=CalibrationBank({**payload,'anchors':[a for a in payload['anchors'] if a['grade']!=grade]})
            for d in ('RD','TD'):
                raw=np.array(sample['curves'][d]['raw_B'])
                reference=np.array(sample['curves'][d]['reference_B'])
                fit=bank.correct(H_GRID,raw,sample['params'],direction=d,texture_sampling_version=bank.texture_sampling_version)
                fits[(grade,d)]=fit
                hold=fold.correct(H_GRID,raw,sample['params'],direction=d,exclude_grades=[grade],texture_sampling_version=bank.texture_sampling_version)
                assert grade not in hold['anchor_weights']
                for name,curve in [('raw',raw),('calibration_fit',fit['B']),('material_holdout',hold['B'])]:
                    rows.append(dict(grade=grade,direction=d,method=name,**metrics(H_GRID,curve,reference)))
                comparisons.append(dict(grade=grade,direction=d,H=H_GRID.tolist(),raw_B=raw.tolist(),
                    reference_B=reference.tolist(),corrected_B=fit['B'],holdout_B=hold['B'],
                    fit_weights=fit['anchor_weights'],holdout_weights=hold['anchor_weights']))
        artifact='cal_'+uuid.uuid4().hex[:12]
        directory=self.storage/artifact
        directory.mkdir(parents=True,exist_ok=False)
        write_json(directory/'bank.json',payload)
        report=dict(id=artifact,created_utc=datetime.now(timezone.utc).isoformat(),selected_grades=grades,
            bank_sha256=bank.bank_sha256,source_bank_sha256=file_hash(self.pilot/'calibration_bank.json'),
            source_manifest_sha256=file_hash(self.pilot/'manifest.json'),
            calibration_version=VERSION,physics_version=PHYSICS_VERSION,
            texture_sampling_version=bank.texture_sampling_version,H_axis='physical_A_per_m',
            evidence_status='existing_processed_references_and_report_parameters',
            independently_validated=False,source_native_evidence=sources,metrics=rows,comparisons=comparisons)
        for method in ('raw','calibration_fit','material_holdout'):
            selected=[r for r in rows if r['method']==method]
            report[method]=dict(mean_rmse_T=float(np.mean([r['rmse_T'] for r in selected])),
                max_abs_B800_error_T=max(abs(r['B800_error_T']) for r in selected),
                B800_0_05T_pass_all=all(abs(r['B800_error_T'])<.05 for r in selected))
        write_csv(directory/'metrics.csv',rows)
        write_csv(directory/'curves.csv',(dict(grade=c['grade'],direction=c['direction'],H_A_per_m=h,
            B_raw_T=raw,B_reference_T=ref,B_corrected_T=fit,B_holdout_T=hold)
            for c in comparisons for h,raw,ref,fit,hold in zip(c['H'],c['raw_B'],c['reference_B'],c['corrected_B'],c['holdout_B'])))
        self._attach_quality(directory,report,fits,samples,grades)
        report['known_material_exports']={}
        for grade in grades:
            path=export_calibrated_pair(fits[(grade,'RD')],fits[(grade,'TD')],
                'WB_'+grade+'_'+artifact[4:],thickness_mm=samples[grade]['thickness_mm'],export_dir=str(directory/'materials'))
            report['known_material_exports'][grade]=dict(path=Path(path).relative_to(directory).as_posix(),sha256=file_hash(path),loss_status='uncalibrated_BH_only')
        report['bundle_file']='calibration_bundle.zip'
        write_json(directory/'report.json',report)
        with zipfile.ZipFile(directory/report['bundle_file'],'x',compression=zipfile.ZIP_DEFLATED) as bundle:
            for file in sorted(directory.rglob('*')):
                if file.is_file() and file.name!=report['bundle_file']:
                    bundle.write(file,file.relative_to(directory).as_posix())
        return report

    def _generalize(self,grades,samples,sources,source_payload):
        # Audit annotations must not enter model selection/source identity.
        selected=[{k:v for k,v in samples[g].items() if k!='native_quality'} for g in sorted(grades)]
        validation=nested_validation(selected,source_payload.get('texture_sampling_version','legacy_multi_peak_importance_v1'))
        bank=TransferBank.fit(selected,source_payload.get('texture_sampling_version','legacy_multi_peak_importance_v1'))
        artifact='cal_'+uuid.uuid4().hex[:12]
        directory=self.storage/artifact
        directory.mkdir(parents=True,exist_ok=False)
        write_json(directory/'protocol.json',protocol())
        write_json(directory/'bank.json',bank.payload)
        fits,rows={},validation['metrics']
        for sample in selected:
            for d in ('RD','TD'):
                c=sample['curves'][d]
                fit=bank.correct(c['H'],c['raw_B'],sample['params'],direction=d,
                    texture_sampling_version=bank.texture_sampling_version)
                fits[(sample['grade'],d)]=fit
                rows.append(dict(grade=sample['grade'],direction=d,method='calibration_fit',
                    selected_candidate=bank.candidate['id'],**metrics(np.asarray(c['H']),fit['B'],c['reference_B'])))
        comparisons=validation['comparisons']
        for c in comparisons:
            c['corrected_B']=fits[(c['grade'],c['direction'])]['B']
            c['fit_weights']=fits[(c['grade'],c['direction'])]['anchor_weights']
        fit_rows=[r for r in rows if r['method']=='calibration_fit']
        report=dict(id=artifact,created_utc=datetime.now(timezone.utc).isoformat(),selected_grades=grades,
            bank_sha256=bank.bank_sha256,source_bank_sha256=file_hash(self.pilot/'calibration_bank.json'),
            source_manifest_sha256=file_hash(self.pilot/'manifest.json'),
            calibration_version=TRANSFER_VERSION,physics_version=PHYSICS_VERSION,
            texture_sampling_version=bank.texture_sampling_version,H_axis='physical_A_per_m',
            evidence_status='existing_processed_references_and_report_parameters',
            independently_validated=False,prospective_external_validation=False,
            source_native_evidence=sources,metrics=rows,comparisons=comparisons,
            raw=validation['summaries']['raw'],material_holdout=validation['summaries']['nested_material_holdout'],
            legacy_material_holdout=validation['summaries']['legacy_material_holdout'],
            reference_only_control=validation['summaries']['reference_only_control'],
            calibration_fit=dict(mean_rmse_T=float(np.mean([r['rmse_T'] for r in fit_rows])),
                max_abs_B800_error_T=max(abs(r['B800_error_T']) for r in fit_rows),
                B800_0_05T_pass_all=all(abs(r['B800_error_T'])<.05 for r in fit_rows)),
            nested_validation=dict(protocol=protocol(),protocol_sha256=digest(protocol()),
                outer_folds=validation['outer_folds'],deployment_selection=bank.payload['selection']),
            producer_sha256={p:file_hash(Path(__file__).parent.parent/p) for p in
                ('modules/calibration_transfer.py','modules/calibration_workbench.py','modules/maxwell_exporter.py')},
            new_native_solves=0,known_material_exports={},holdout_material_exports={})
        self._attach_quality(directory,report,fits,samples,grades)
        write_csv(directory/'metrics.csv',rows)
        write_csv(directory/'curves.csv',(dict(grade=c['grade'],direction=c['direction'],H_A_per_m=h,
            B_raw_T=raw,B_reference_T=ref,B_corrected_T=fit,B_holdout_T=hold,
            B_legacy_holdout_T=old,B_reference_only_T=control)
            for c in comparisons for h,raw,ref,fit,hold,old,control in zip(c['H'],c['raw_B'],c['reference_B'],
                c['corrected_B'],c['holdout_B'],c['legacy_holdout_B'],c['reference_only_B'])))
        holdout_dir=directory/'holdout_banks'
        holdout_dir.mkdir()
        for grade in grades:
            fullpath=export_calibrated_pair(fits[(grade,'RD')],fits[(grade,'TD')],
                'WB_'+grade+'_'+artifact[4:],thickness_mm=samples[grade]['thickness_mm'],
                export_dir=str(directory/'materials'))
            report['known_material_exports'][grade]=dict(path=Path(fullpath).relative_to(directory).as_posix(),
                sha256=file_hash(fullpath),loss_status='uncalibrated_BH_only')
            fold=TransferBank(validation['holdout_banks'][grade])
            write_json(holdout_dir/(grade+'.json'),fold.payload)
            held={}
            for d in ('RD','TD'):
                c=samples[grade]['curves'][d]
                held[d]=fold.correct(c['H'],c['raw_B'],samples[grade]['params'],direction=d,
                    texture_sampling_version=fold.texture_sampling_version)
                held[d]['excluded_grades']=[grade]
                held[d]['native_quality']=samples[grade]['native_quality']
                held[d]['calibration_native_quality_sha256']=digest({g:q for g,q in report['native_quality'].items() if g!=grade})
            path=export_calibrated_pair(held['RD'],held['TD'],'WB_'+grade+'_holdout_'+artifact[4:],
                thickness_mm=samples[grade]['thickness_mm'],export_dir=str(directory/'holdout_materials'))
            report['holdout_material_exports'][grade]=dict(path=Path(path).relative_to(directory).as_posix(),
                sha256=file_hash(path),bank_sha256=fold.bank_sha256,excluded_grade=grade,
                loss_status='uncalibrated_BH_only')
        report['bundle_file']='calibration_bundle.zip'
        write_json(directory/'report.json',report)
        with zipfile.ZipFile(directory/report['bundle_file'],'x',compression=zipfile.ZIP_DEFLATED) as bundle:
            for file in sorted(directory.rglob('*')):
                if file.is_file() and file.name!=report['bundle_file']:
                    bundle.write(file,file.relative_to(directory).as_posix())
        return report

    def get(self,artifact):
        if not isinstance(artifact,str) or not artifact.startswith('cal_') or len(artifact)!=16 or not all(c in '0123456789abcdef' for c in artifact[4:]):
            raise ValueError('无效标定 ID')
        return self.storage/artifact

    def compatible_pair(self,pair):
        """Hydrate only field-for-field historical payloads, never new inputs."""
        if not isinstance(pair,dict):
            raise ValueError('原生材料对必须是 JSON 对象')
        pair=copy.deepcopy(pair)
        # User-supplied pass labels are never trusted. Rebind exact known inputs.
        pair.pop('native_quality',None)
        payload,manifest=self.source()
        comparable={k:v for k,v in pair.items() if k not in ('texture_sampling_version','historical_source')}
        for grade in GRADES:
            source=self.pilot/grade/'raw_pair.json'
            original=json.loads(source.read_text(encoding='utf-8'))
            expected={k:v for k,v in original.items() if k not in ('texture_sampling_version','historical_source')}
            if comparable==expected:
                if 'texture_sampling_version' not in pair:
                    pair['texture_sampling_version']=payload.get('texture_sampling_version','legacy_multi_peak_importance_v1')
                pair['historical_source']=dict(grade=grade,raw_pair_sha256=file_hash(source),
                    manifest_sha256=file_hash(self.pilot/'manifest.json'))
                quality=audit_pilot(self.pilot,[grade])[grade]
                self._bound_raw_pair(next(m for m in manifest['materials'] if m['grade']==grade),quality)
                pair['native_quality']=quality
                break
        if 'native_quality' not in pair:
            pair['native_quality']=unassessed_quality(pair.get('material_id','unspecified'))
        return pair

    def predict(self,artifact,pair,exclude_grades=()):
        directory=self.get(artifact)
        bank=load_bank(directory/'bank.json')
        calibration_report=json.loads((directory/'report.json').read_text(encoding='utf-8'))
        if bank.bank_sha256!=calibration_report['bank_sha256']:
            raise ValueError('该标定 bank 在创建后改变，请重新建立校准记录')
        pair=self.compatible_pair(pair)
        if pair.get('simulation_physics_version')!=PHYSICS_VERSION:
            raise ValueError('原生曲线物理版本不符，请使用 cubic_sample_frame_v2 raw_pair.json')
        if pair.get('texture_sampling_version')!=bank.texture_sampling_version:
            raise ValueError('原生曲线取向采样协议与此标定不同，请勿混合新旧采样器')
        if not isinstance(pair.get('material_id'),str) or not pair['material_id'].strip():
            raise ValueError('缺少材料 ID')
        features(pair['params'])
        thickness=float(pair.get('thickness_mm',.3))
        if not np.isfinite(thickness) or thickness<=0:
            raise ValueError('厚度必须为有限正值')
        result=predict_pair(pair,bank,exclude_grades,include_bank=True)
        effective_bank=result.pop('_effective_bank_payload')
        parameter_support=result['parameter_support']
        qualities=calibration_report.get('native_quality')
        training_quality_sha=digest({g:q for g,q in qualities.items() if g in result['prediction_scope']['effective_training_grades']}) if qualities else 'legacy_unassessed'
        for d in ('RD','TD'):
            h,b=validated_curve(pair[d]['H'],pair[d]['B'])
            if not np.all(np.isfinite(b)):
                raise ValueError('非有限曲线')
            result[d]['native_quality']=pair['native_quality']
            result[d]['calibration_native_quality_sha256']=training_quality_sha
            result[d]['parameter_support']=parameter_support
        pred_id='pred_'+uuid.uuid4().hex[:12]
        out=directory/pred_id
        out.mkdir()
        write_json(out/'effective_bank.json',effective_bank)
        write_json(out/'input_raw_pair.json',pair)
        name='WB_'+pred_id
        path=export_calibrated_pair(result['RD'],result['TD'],name,
            thickness_mm=thickness,export_dir=str(out))
        result.update(id=pred_id,calibration_id=artifact,material_id=pair['material_id'],
            params=pair['params'],AMAT_file=Path(path).name,loss_status='uncalibrated_BH_only',
            source_input_sha256=file_hash(out/'input_raw_pair.json'),native_quality=pair['native_quality'],
            strict_training_eligible=False)
        result['parameter_support']=parameter_support
        write_json(out/'prediction.json',result)
        write_csv(out/'curves.csv',(dict(direction=d,H_A_per_m=h,B_raw_T=raw,B_corrected_T=b)
            for d in ('RD','TD') for h,raw,b in zip(result[d]['H'],result[d]['B_raw'],result[d]['B'])))
        return result
