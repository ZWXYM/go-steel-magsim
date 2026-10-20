"""Read the frozen residual trial without fitting or changing a saved material."""
import copy
import csv
import io
import math
import zipfile
import json
from pathlib import Path

from modules.calibration_transfer import TransferBank, digest, load_bank
from modules.material_calibration import validated_curve
from modules.material_library import sha

EXPECTED_BANK_SHA256 = '3cffed42ece56052a0518983b2d628dad376cbd266adfd224cc7b8b284caae33'
EXPECTED_FILES = {'curves.json': 'c42a5866e2efba25052019454df72180d31a9ca5107a3c3fc4dbdbc50fb488eb', 'frozen_protocol.json': '70d20ecb3d5c1999b2cdd0de2bb70c14964590dcac728cb1ce64bc3b5e6e9114', 'metrics.csv': '95bedcb35345e13eb53f23fc24173de70af6cb505d43482a253d96f978183492', 'outer_selection.json': 'fc6278e8cdd34ae5092859d6d0f58d872392c9d077708cd653c518f29346f5bc', 'summary.json': 'c841337cb41509315f3b517575ea1dd422c8e5a51f4b261fa61e9af695278e44'}


class CalibrationBetaPreview:
    def __init__(self,calibration):
        self.calibration=calibration

    def _study(self):
        root=Path(self.calibration.root)
        candidates=(root/'calibration/diagnostics/low_beta_trial_20261020/public_evidence',
                    root/'calibration/low_beta_trial_20261020')
        folder=next((p for p in candidates if p.is_dir()),None)
        if folder is None:raise ValueError('当前资源副本没有已冻结的CPU候选对照，未重新计算')
        files={name:(folder/name).read_bytes() for name in EXPECTED_FILES}
        if any(sha(blob)!=EXPECTED_FILES[name] for name,blob in files.items()):
            raise ValueError('冻结CPU候选对照文件已改变，未展示或重新计算')
        return files

    def snapshot(self,artifact):
        bank=load_bank(self.calibration.get(artifact)/'bank.json')
        if not isinstance(bank,TransferBank) or bank.bank_sha256!=EXPECTED_BANK_SHA256:
            raise ValueError('当前校准库与冻结四样品候选对照来源不同，未混用；原材料仍可正常使用')
        files=self._study()
        summary=json.loads(files['summary.json']);plan=json.loads(files['frozen_protocol.json'])
        if summary['version']!=plan['version'] or digest(plan)!=summary['frozen_protocol_sha256']:
            raise ValueError('CPU候选协议与保存摘要不符')
        curves=json.loads(files['curves.json']);selections=json.loads(files['outer_selection.json'])
        expected={(s['grade'],d) for s in bank.samples for d in ('RD','TD')}
        if len(curves)!=len(expected) or {(c['grade'],c['direction']) for c in curves}!=expected:
            raise ValueError('候选材料/方向组合缺失或重复')
        for curve in curves:
            sample=next(s for s in bank.samples if s['grade']==curve['grade'])
            reference=sample['curves'][curve['direction']]
            if curve['H']!=reference['H'] or curve['B_reference']!=reference['reference_B']:
                raise ValueError('候选的物理H或参考曲线与当前库不同')
            for name in ('B_trial','B_baseline','B_reference'):
                validated_curve(curve['H'],curve[name])
            if set(curve['weights'])!={s['grade'] for s in bank.samples if s['grade']!=curve['grade']}:
                raise ValueError('候选训练材料与留出身份不符')
        metrics=[]
        for row in csv.DictReader(io.StringIO(files['metrics.csv'].decode('utf-8-sig'))):
            parsed={key:row[key] for key in ('grade','direction','method')}
            for key,value in row.items():
                if key in parsed:continue
                parsed[key]=float(value) if value else None
                if parsed[key] is not None and not math.isfinite(parsed[key]):raise ValueError('候选指标不是有限数值')
            metrics.append(parsed)
        metric_keys={(g,d,method) for g,d in expected for method in ('baseline_saved_nested','trial_nested','reference_only_control')}
        if len(metrics)!=len(metric_keys) or {(m['grade'],m['direction'],m['method']) for m in metrics}!=metric_keys:
            raise ValueError('候选逐项指标材料/方向组合缺失或重复')
        if summary['all_acceptance_gates_passed']!=all(summary['gates'].values()):
            raise ValueError('候选接受状态与逐项门限不符')
        return dict(version='calibration_beta_preview_v1',calibration_id=artifact,
            bank_sha256=bank.bank_sha256,summary=copy.deepcopy(summary),protocol=plan,
            curves=curves,metrics=metrics,outer_selection=selections,
            source_files_sha256=dict(EXPECTED_FILES),read_only=True,
            default_prediction_changed=False,material_exports_changed=False,
            new_candidate_evaluations=0,new_saved_predictions=0,new_native_solves=0,new_ML_training=0,
            automatic_deployment=False,prospective_external_validation=False,
            scope='Frozen retrospective whole-material holdout comparison; not current anchor-fit curves')

    def csv(self,artifact,kind):
        if kind not in ('curves','metrics'):raise ValueError('仅支持全部曲线或逐项误差CSV')
        return self._csv(self.snapshot(artifact),kind)

    @staticmethod
    def _csv(data,kind):
        meta=dict(calibration_id=data['calibration_id'],trial_version=data['summary']['version'],
            source_bank_sha256=data['bank_sha256'],deployment_status='not_deployed',
            comparison_scope='retrospective_whole_material_holdout')
        rows=[]
        if kind=='curves':
            for c in data['curves']:
                for i,h in enumerate(c['H']):
                    rows.append(dict(meta,material_id=c['grade'],direction=c['direction'],H_A_per_m=h,
                        B_original_holdout_T=c['B_baseline'][i],B_candidate_holdout_T=c['B_trial'][i],
                        B_target_reference_T=c['B_reference'][i],selected_candidate=c['candidate']))
        else:
            rows=[dict(meta,**row) for row in data['metrics']]
        stream=io.StringIO(newline='');writer=csv.DictWriter(stream,fieldnames=list(rows[0]))
        writer.writeheader();writer.writerows(rows)
        return stream.getvalue().encode('utf-8-sig')

    def bundle(self,artifact):
        data=self.snapshot(artifact)
        files={'preview.json':json.dumps(data,ensure_ascii=False,indent=2).encode('utf8'),
            'curves.csv':self._csv(data,'curves'),'metrics.csv':self._csv(data,'metrics'),
            'README.md':('冻结的材料级留出开发对照；未部署。\n'
                'curves.csv保留全部8组曲线原H节点；metrics.csv保留24项误差。\n'
                '目标参考与仅参考内插对照分别标记。当前bank和材料文件不变。\n'
                '保存的整体接受条件：'+str(data['summary']['all_acceptance_gates_passed'])+'。\n'
                '该回顾性开发对照不能作为新增批次独立验证。\n').encode('utf8')}
        manifest=dict(version='beta_trial_preview_bundle_v1',calibration_id=artifact,
            source_bank_sha256=data['bank_sha256'],read_only=True,default_prediction_changed=False,
            automatic_deployment=False,new_native_solves=0,new_candidate_evaluations=0,
            files={name:sha(blob) for name,blob in files.items()})
        files['manifest.json']=json.dumps(manifest,ensure_ascii=False,indent=2).encode('utf8')
        out=io.BytesIO()
        with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as archive:
            for name,blob in files.items():archive.writestr(name,blob)
        out.seek(0);return out
