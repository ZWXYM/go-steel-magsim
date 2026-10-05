"""Freeze reproducible CPU quality evidence without changing models or solving."""
import argparse
import json
import shutil
import sys
from pathlib import Path

import numpy as np

PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT))
from modules.calibration_workbench import CalibrationWorkbench,GRADES
from modules.calibration_transfer import load_bank
from modules.material_calibration import file_hash
from modules.native_quality import VERSION,digest
from tools.run_calibration_pilot import write_json,write_csv


def run(root,output):
    root,output=Path(root).resolve(),Path(output).resolve()
    pilot=root/'calibration/pilot_20261003_n8'
    if output.exists() or output==pilot or pilot in output.parents:
        raise ValueError('Use a new evidence directory outside the immutable pilot')
    frozen=root/'calibration/generalization_20261005'
    if (frozen/'final').is_dir():
        frozen=frozen/'final'
    frozen=frozen/'cal_65e506f6e207'
    def inventory():
        return {name:{p.relative_to(base).as_posix():file_hash(p) for p in sorted(base.rglob('*')) if p.is_file()}
                for name,base in (('pilot',pilot),('frozen_calibration',frozen))}
    before=inventory()
    if len(before['pilot'])!=525 or len(before['frozen_calibration'])!=26:
        raise ValueError('Expected complete frozen sources')
    output.mkdir(parents=True)
    producers=('modules/native_quality.py','modules/calibration_workbench.py','modules/maxwell_exporter.py',
        'modules/dataset_contract.py','modules/workbench_routes.py','modules/workbench_cpu_policy.py',
        'tools/start_workbench.py','tools/audit_workbench_native_quality.py','templates/workbench.html')
    producer_sha256={name:file_hash(PROJECT/name) for name in producers}
    (output/'frozen_producers').mkdir()
    for name in producers:
        shutil.copy2(PROJECT/name,output/'frozen_producers'/Path(name).name)
    write_json(output/'study_protocol.json',dict(contract_version=VERSION,
        scope='all four existing n8 materials, both directions, all 64 grains; no selection or correction',
        native_execution_allowed=False,new_native_solves=0,model_changed=False,
        GPU_resumption_requires_explicit_user_authorization=True))
    samples=CalibrationWorkbench(root,output/'unused_storage').samples(include_quality=True)['samples']
    quality={s['grade']:s['native_quality'] for s in samples}
    write_json(output/'native_quality.json',quality)
    rows=[dict(grade=grade,direction=direction,**grain) for grade,q in quality.items()
          for direction,d in q['directions'].items() for grain in d['grain_details']]
    write_csv(output/'grain_quality.csv',rows)
    previous=json.loads((frozen/'report.json').read_text(encoding='utf-8'))
    full=load_bank(frozen/'bank.json')
    identity=[]
    for c in previous['comparisons']:
        sample=next(s for s in samples if s['grade']==c['grade'])
        curve=sample['curves'][c['direction']]
        fold=load_bank(frozen/'holdout_banks'/(c['grade']+'.json'))
        outputs={}
        for kind,bank,expected in (('calibration_fit',full,c['corrected_B']),('material_holdout',fold,c['holdout_B'])):
            prediction=bank.correct(curve['H'],curve['raw_B'],sample['params'],direction=c['direction'],
                texture_sampling_version=bank.texture_sampling_version)
            delta=float(np.max(np.abs(np.asarray(prediction['B'])-expected)))
            if delta>1e-12 or (kind=='material_holdout' and c['grade'] in prediction['anchor_weights']):
                raise ValueError('Quality annotations changed numerical prediction or material exclusion')
            outputs[kind+'_max_abs_delta_T']=delta
        identity.append(dict(grade=c['grade'],direction=c['direction'],**outputs))
    write_json(output/'curve_identity.json',identity)
    after=inventory()
    if before!=after:
        raise ValueError('Frozen sources changed')
    write_json(output/'source_integrity.json',dict(unchanged=True,
        file_counts={name:len(v) for name,v in before.items()},sha256=before))
    groups=[d for q in quality.values() for d in q['directions'].values()]
    report=dict(contract_version=VERSION,role='source_quality_and_transport_diagnostics_not_material_validation',
        native_quality_sha256=digest(quality),producer_sha256=producer_sha256,
        grains=len(rows),endpoint_screen_failures=sum(len(d['endpoint_failed_grain_ids']) for d in groups),
        inversion_screen_failures=sum(len(d['inversion_failed_grain_ids']) for d in groups),
        torque_unknown_grains=sum(len(d['torque_unknown_grain_ids']) for d in groups),
        strict_training_eligible=False,existing_bank_sha256=previous['bank_sha256'],
        existing_material_holdout=previous['material_holdout'],curve_identity=identity,
        new_MuMax_solves=0,new_Maxwell_solves=0,GPU_used=False,model_changed=False,
        no_grain_removed=True,no_offset_correction_applied=True,independently_validated=False)
    write_json(output/'report.json',report)
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=PROJECT.parent if PROJECT.name=='magsim' else PROJECT)
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args()
    result=run(args.root,args.output_dir)
    print(json.dumps({k:result[k] for k in ('grains','endpoint_screen_failures','inversion_screen_failures',
        'torque_unknown_grains','strict_training_eligible','new_MuMax_solves','new_Maxwell_solves','GPU_used')}))
