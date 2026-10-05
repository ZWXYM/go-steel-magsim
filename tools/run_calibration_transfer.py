"""Reproduce the frozen nested whole-material transfer study; no native solve."""
import argparse
import json
import sys
from pathlib import Path

PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT))
from modules.calibration_workbench import CalibrationWorkbench,GRADES
from modules.calibration_transfer import VERSION,protocol
from modules.material_calibration import file_hash

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=PROJECT.parent if PROJECT.name=='magsim' else PROJECT)
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args()
    if args.output_dir.exists():
        raise ValueError('Use a new output directory; existing evidence is never overwritten')
    args.output_dir.mkdir(parents=True)
    # Freeze the candidate registry before loading targets or running selection.
    (args.output_dir/'study_protocol.json').write_text(json.dumps(protocol(),indent=2),encoding='utf-8')
    pilot=args.root/'calibration/pilot_20261003_n8'
    before={p.relative_to(pilot).as_posix():file_hash(p) for p in pilot.rglob('*') if p.is_file()}
    manager=CalibrationWorkbench(args.root,args.output_dir)
    result=manager.calibrate(list(GRADES),VERSION)
    after={p.relative_to(pilot).as_posix():file_hash(p) for p in pilot.rglob('*') if p.is_file()}
    assert before==after,'Pilot source changed'
    (args.output_dir/'source_integrity.json').write_text(json.dumps(dict(
        files_checked=len(before),unchanged=True,sha256=before,new_native_solves=0),indent=2),encoding='utf-8')
    print(json.dumps(dict(artifact=result['id'],directory=str(manager.get(result['id'])),
        legacy=result['legacy_material_holdout'],nested=result['material_holdout'],
        reference_only=result['reference_only_control'],deployment_candidate=result['nested_validation']['deployment_selection']['candidate'],
        outer_candidates={f['heldout']:f['selection']['candidate'] for f in result['nested_validation']['outer_folds']},
        sources_unchanged=len(before)),indent=2))

if __name__=='__main__':
    main()
