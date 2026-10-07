"""CPU reproduction of four whole-material prediction scopes, with no solves."""
import argparse
import json
import shutil
import sys
from pathlib import Path
PROJECT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(PROJECT));sys.path.insert(0,str(PROJECT/'modules'))
import numpy as np
from modules.calibration_prediction import predict_pair
from modules.calibration_transfer import load_bank,error_metrics,TransferBank
from modules.maxwell_exporter import export_calibrated_pair
from modules.maxwell_material_transport import digest,directional_curves
from modules.motor_workbench import write_json
from tools.run_calibration_pilot import write_csv

PRODUCERS=('modules/calibration_prediction.py','modules/calibration_transfer.py',
    'modules/material_calibration.py','modules/calibration_domain.py','modules/calibration_workbench.py',
    'modules/maxwell_exporter.py','modules/native_quality.py','modules/texture_sampling.py',
    'modules/workbench_routes.py','modules/workbench_cpu_policy.py',
    'tools/predict_calibrated_material.py','tools/audit_prediction_scope.py','templates/workbench.html')


def audit(root,output):
    if output.exists():raise ValueError('Preserve previous audits; output directory must be new')
    base=root/'calibration/generalization_20261005/final/cal_65e506f6e207'
    if not base.exists():base=root/'calibration/generalization_20261005/cal_65e506f6e207'
    pilot=root/'calibration/pilot_20261003_n8';bank=load_bank(base/'bank.json')
    before={p.relative_to(root).as_posix():digest(p) for p in base.rglob('*') if p.is_file()}
    before.update({p.relative_to(root).as_posix():digest(p) for p in pilot.rglob('*') if p.is_file()})
    output.mkdir(parents=True);write_json(output/'protocol.json',dict(protocol='prediction_scope_reproduction_v1',
        source_bank_sha256=bank.bank_sha256,scope='Provenance fix and identical frozen retrospective holdouts; not improved error or prospective validation',
        grades=[s['grade'] for s in bank.samples],new_native_solves=0,source_sha256=before,
        producer_sha256={p:digest(PROJECT/p) for p in PRODUCERS}))
    for name in PRODUCERS:
        dest=output/'frozen_producers'/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(PROJECT/name,dest)
    cases=[];metrics=[]
    for sample in bank.samples:
        grade=sample['grade'];raw=json.loads((pilot/grade/'raw_pair.json').read_text())
        result=predict_pair(raw,bank,[grade],include_bank=True);effective=result.pop('_effective_bank_payload')
        actual=TransferBank(effective);folder=output/'cases'/grade;folder.mkdir(parents=True)
        frozen=load_bank(base/'holdout_banks'/(grade+'.json'))
        if actual.bank_sha256!=frozen.bank_sha256:raise ValueError('Frozen holdout selection/identity changed')
        write_json(folder/'effective_bank.json',effective);write_json(folder/'prediction.json',result)
        material=Path(export_calibrated_pair(result['RD'],result['TD'],'SCOPE_'+grade,thickness_mm=raw['thickness_mm'],export_dir=str(folder)))
        saved=directional_curves(material.read_text(),material.stem)
        metadata=json.loads(material.with_suffix('.metadata.json').read_text())
        if metadata['prediction_scope']!=result['prediction_scope']:raise ValueError('Material scope differs from prediction')
        for direction in ('RD','TD'):
            c=raw[direction];old=frozen.correct(c['H'],c['B'],raw['params'],direction=direction)
            if result[direction]['B']!=old['B'] or result[direction]['H']!=old['H']:
                raise ValueError('Frozen held-out curve changed')
            if grade in result[direction]['anchor_weights'] or grade in result['parameter_support']['training_grades']:
                raise ValueError('Held-out material remained in prediction training')
            if not np.allclose(saved[direction]['B'],result[direction]['B'],rtol=1e-10,atol=2e-7):
                raise ValueError('Serialized BH changed')
            metrics.append(dict(grade=grade,direction=direction,**error_metrics(c['H'],old['B'],sample['curves'][direction]['reference_B'])))
        cases.append(dict(grade=grade,source_calibration_sha256=result['source_calibration_sha256'],
            effective_calibration_sha256=result['calibration_sha256'],training_grades=result['prediction_scope']['effective_training_grades'],
            frozen_holdout_bank_identical=True,RD_TD_and_top_level_identity_match=True,
            selection_candidate=result['RD']['transfer_candidate'],parameter_support=result['parameter_support']))
    if any(digest(root/p)!=h for p,h in before.items()):raise ValueError('Original evidence changed')
    write_csv(output/'metrics.csv',metrics)
    summary=dict(status='CPU_scope_verified_frozen_holdouts_unchanged',cases=cases,
        material_holdout_mean_RMSE_T=float(np.mean([r['rmse_T'] for r in metrics])),
        worst_B800_error_T=max(abs(r['B800_error_T']) for r in metrics),
        predictive_accuracy_improvement_claimed=False,prospective_external_validation=False,
        real_material_physics_verified=False,new_field_solves=0,new_motor_solves=0,new_MuMax_solves=0,
        cumulative_Maxwell_completed=39,cumulative_MuMax_success=680,source_files_checked=len(before))
    write_json(output/'summary.json',summary)
    write_json(output/'evidence_sha256.json',{p.relative_to(output).as_posix():digest(p) for p in output.rglob('*') if p.is_file()})
    return summary


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True);p.add_argument('--output-dir',type=Path,required=True)
    a=p.parse_args();r=audit(a.root.resolve(),a.output_dir.resolve());print(json.dumps({k:v for k,v in r.items() if k!='cases'},indent=2))
