"""Expose branch and guard changes; do not repair or exclude native evidence."""
import argparse
import json
import sys
from pathlib import Path

import numpy as np

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from modules.material_calibration import extract_loop_midpoint, file_hash
from tools.analyze_sampling_convergence import read_ensemble
from tools.run_calibration_pilot import H_GRID, write_csv, write_json


def audit(run_dirs, grade, direction, output):
    rows, curves, ensembles = [], [], []
    for run in run_dirs:
        values, source = read_ensemble(run, grade, direction)
        manifest = json.loads((run/'manifest.json').read_text(encoding='utf-8-sig'))
        jobs = sorted((j for j in manifest['jobs'] if j['grade']==grade and j['direction']==direction),
                      key=lambda j: j['grain_id'])
        local, unguarded = [], []
        for job in jobs:
            result = extract_loop_midpoint(run/job['output']/'table.txt', H_GRID, angle_deg=job['angle'])
            before, after = np.array(result['B_midpoint_before_guard']), np.array(result['B'])
            descending, ascending = np.array(result['B_descending']), np.array(result['B_ascending'])
            delta = after-before
            peak = int(np.argmax(np.abs(delta)))
            row = dict(manifest_seed=source['seed'], material_seed=source['material_seed'],
                grain_id=job['grain_id'], max_guard_change_T=float(np.abs(delta).max()),
                H_at_max_guard_change_A_per_m=float(H_GRID[peak]),
                delta_B800_guard_T=float(np.interp(800,H_GRID,delta)),
                B800_unguarded_T=float(np.interp(800,H_GRID,before)),
                B800_guarded_T=float(np.interp(800,H_GRID,after)),
                minimum_midpoint_T=float(before.min()),
                largest_midpoint_drop_T=float(max(0, -np.diff(before).min())),
                min_descending_minus_ascending_T=float((descending-ascending).min()),
                table_sha256=result['table_sha256'], script_sha256=job['script_sha256'])
            rows.append(row)
            local.append(row)
            unguarded.append(before)
            for i,h in enumerate(H_GRID):
                curves.append(dict(manifest_seed=source['seed'], grain_id=job['grain_id'], H_A_per_m=float(h),
                    B_descending_T=float(descending[i]), B_ascending_T=float(ascending[i]),
                    B_midpoint_before_guard_T=float(before[i]), B_after_guard_T=float(after[i]),
                    imposed_change_T=float(delta[i])))
        mean = np.mean(unguarded,axis=0)
        np.testing.assert_allclose(mean,source['unguarded_ensemble_mean_B_T'],atol=1e-14)
        change = float(np.interp(800,H_GRID,values.mean(axis=0)-mean))
        np.testing.assert_allclose(change,np.mean([r['delta_B800_guard_T'] for r in local]),atol=1e-14)
        ensembles.append(dict(manifest_seed=source['seed'], source=source, mean_imposed_B800_change_T=change,
            largest_grain_change_T=max(r['max_guard_change_T'] for r in local),
            top_guard_changes=sorted(local,key=lambda r:r['max_guard_change_T'],reverse=True)[:5]))
    if output.exists():
        raise ValueError('Preserve earlier audit; use a new output directory')
    output.mkdir(parents=True)
    write_csv(output/'grain_guard_summary.csv',rows)
    write_csv(output/'branch_and_guard_curves.csv',curves)
    report=dict(status='posthoc_guard_and_branch_audit_no_exclusion_or_calibration',
        grade=grade,direction=direction,analysis_source_sha256=file_hash(Path(__file__)),
        ensembles=ensembles,all_grains_retained=True,source_tables_modified=False,
        limitations=['Guard is an imposed normal-curve prior, not observed magnetic response',
            'H0 midpoint is forced to zero by the extractor; branch values are saved separately',
            'Branch ordering/drop indicators locate cases for investigation, not proof of solver failure',
            'Do not interpret these native small-grid loop proxies as measured Hc or iron loss'])
    write_json(output/'summary.json',report)
    print(json.dumps([{k:v for k,v in e.items() if k!='source'} for e in ensembles],indent=2))
    return report


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir',type=Path,action='append',required=True)
    parser.add_argument('--grade',default='B30P105')
    parser.add_argument('--direction',choices=['RD','TD'],default='TD')
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args()
    audit(args.run_dir,args.grade,args.direction,args.output_dir)
