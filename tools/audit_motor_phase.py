"""Freeze official CSV from a completed scan and audit phase differences on CPU."""
import argparse
import json
import shutil
import sys
from pathlib import Path

PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT))
from modules.material_calibration import file_hash
from modules.motor_phase_diagnostics import audit_case,VERSION
from tools.run_calibration_pilot import write_json

PRODUCERS=('modules/motor_phase_diagnostics.py','modules/motor_workbench.py','tools/audit_motor_phase.py')
FILES=('reports/Torque Plots.csv','reports/Drive Current Plots.csv',
    'maxwell_reports/CoreLoss.csv','maxwell_reports/StrandedLoss.csv','maxwell_reports/SolidLoss.csv','result.json')


def audit(source,output):
    source,output=Path(source).resolve(),Path(output).resolve()
    if output.exists() or source in output.parents or output==source:
        raise ValueError('Use a new directory outside the original scan')
    manifest=json.loads((source/'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('measurement_protocol')!='periodic_cycle3_v1' or len(manifest['cases'])!=2:
        raise ValueError('This frozen diagnostic requires the two-case three-cycle scan')
    before={name:file_hash(source/name) for name in ('manifest.json','state.json')}
    for case in manifest['cases']:
        for name in FILES:
            before[case['id']+'/'+name]=file_hash(source/case['id']/name)
        result=json.loads((source/case['id']/'result.json').read_text(encoding='utf-8'))
        hashes={name.replace('\\','/'):digest for name,digest in result['csv_hashes'].items()}
        if len(hashes)!=len(result['csv_hashes']):
            raise ValueError('Conflicting normalized CSV source paths')
        for name in FILES[:-1]:
            if hashes[name]!=file_hash(source/case['id']/name):
                raise ValueError('Official CSV differs from saved native result')
    output.mkdir(parents=True)
    for name in ('manifest.json','state.json'):
        shutil.copy2(source/name,output/('source_'+name))
    rows=[]
    for case in manifest['cases']:
        folder=output/'source_cases'/case['id']
        for name in FILES:
            (folder/name).parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(source/case['id']/name,folder/name)
        row=audit_case(folder)
        saved=json.loads((folder/'result.json').read_text(encoding='utf-8'))
        if (row['existing_periodic_stability']!=saved['periodic_stability']
                or row['existing_acceptance']!=saved['accepted']):
            raise ValueError('Official recomputation differs from stored result')
        rows.append(dict(case_id=case['id'],material=case['material'],**row))
    frozen={}
    for name in PRODUCERS:
        folder=output/'frozen_producers'
        folder.mkdir(exist_ok=True)
        shutil.copy2(PROJECT/name,folder/Path(name).name)
        frozen[name]=file_hash(PROJECT/name)
    after={name:file_hash(source/name) for name in before}
    if before!=after:
        raise ValueError('Original scan files changed')
    report=dict(protocol=VERSION,source_job_id=manifest['id'],source_manifest_sha256=before['manifest.json'],
        source_files_sha256=before,producer_sha256=frozen,cases=rows,source_unchanged=True,
        new_Maxwell_solves=0,metrics_replaced=False,ranking_created=False)
    write_json(output/'analysis.json',report)
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-job',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args()
    report=audit(args.source_job,args.output_dir)
    print(json.dumps({k:v for k,v in report.items() if k not in ('cases','source_files_sha256')},indent=2))
