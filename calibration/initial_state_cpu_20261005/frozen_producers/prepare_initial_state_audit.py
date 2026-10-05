"""CPU-only signed-loop audit and state-control preparation; no native runner."""
import argparse
import json
import shutil
import sys
from pathlib import Path

import numpy as np

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from modules.material_calibration import file_hash
from modules.native_loop_diagnostics import audit_table, initial_state_script, VERSION
from tools.analyze_sampling_convergence import read_ensemble
from tools.run_calibration_pilot import H_GRID, write_json, write_csv

GRADES = ('B23R075', 'B27R090', 'B27R095', 'B30P105')


def study_protocol():
    return dict(diagnostic_version=VERSION, H_axis='physical_A_per_m',
        selection='all four confirmed materials and RD/TD; fixed grain_id=1 for prepared controls',
        audit_scope='all 64 complete legacy n8 major loops; no result-based exclusions',
        conditions=['strict_major_loop', 'zero_transverse_pair'],
        matched_solver=dict(MinimizerStop=1e-7, MinimizerSamples=20, hmax_A_per_m=50000),
        planned_native_jobs=16, actually_executed_native_jobs=0, native_execution_allowed=False,
        resource_constraint='User GPU restriction: preparation and CPU analysis only; explicit user resumption required',
        controls_are_not_material_curves=True, no_reference_curve_used=True,
        gates=dict(min_signed_endpoint_m_projection=.95, max_branch_inversion_error_T=.02,
            max_residual_torque_T=1e-5),
        limitations=['initial transverse pair is not certified demagnetization or a virgin curve',
            'single fixed grain is a state/path diagnostic, not a material ensemble',
            'strict solver alone does not establish torque convergence',
            'no calibration promotion, material export, Hc, Br or loss inference'])


def prepare(root, output):
    root, output = Path(root).resolve(), Path(output).resolve()
    pilot = root / 'calibration/pilot_20261003_n8'
    if output.exists() or pilot in output.parents:
        raise ValueError('Use a new output directory outside frozen pilot evidence')
    output.mkdir(parents=True)
    p = study_protocol()
    write_json(output / 'study_protocol.json', p)
    producers = ('modules/native_loop_diagnostics.py', 'modules/material_calibration.py',
        'tools/prepare_initial_state_audit.py', 'tools/analyze_sampling_convergence.py',
        'tools/run_calibration_pilot.py')
    (output / 'frozen_producers').mkdir()
    for name in producers:
        shutil.copy2(PROJECT / name, output / 'frozen_producers' / Path(name).name)
    before = {f.relative_to(pilot).as_posix(): file_hash(f) for f in sorted(pilot.rglob('*')) if f.is_file()}
    manifest = json.loads((pilot / 'manifest.json').read_text(encoding='utf-8'))
    sources, rows, details, jobs, files = {}, [], [], [], []
    for grade in GRADES:
        for direction in ('RD', 'TD'):
            ensemble, source = read_ensemble(pilot, grade, direction)
            sources[grade + '_' + direction] = source
            native = sorted([j for j in manifest['jobs'] if j['grade'] == grade and j['direction'] == direction],
                key=lambda j: j['grain_id'])
            for i, job in enumerate(native):
                metric = audit_table(pilot / job['output'] / 'table.txt', H_GRID, angle_deg=job['angle'])
                if not np.allclose(ensemble[i], metric['guarded_midpoint_B_T'], atol=1e-12, rtol=0):
                    raise ValueError('Directional audit differs from the existing native extractor')
                row = dict(grade=grade, direction=direction, grain_id=job['grain_id'],
                    **{k:v for k,v in metric.items() if not isinstance(v, list)})
                row['endpoint_screen_failed'] = metric['min_signed_endpoint_m_projection'] < .95
                row['inversion_screen_failed'] = metric['max_branch_inversion_error_T'] > .02
                row['torque_gate_evaluable'] = metric['max_residual_torque_T'] is not None
                rows.append(row)
                details.append(dict(grade=grade, direction=direction, grain_id=job['grain_id'], **metric))
            first = native[0]
            if first['grain_id'] != 1:
                raise ValueError('Fixed case grain_id=1 absent')
            case = grade + '_' + direction + '_g001'
            origin = output / 'source_cases' / case
            origin.mkdir(parents=True)
            for source_path, name in ((pilot / first['script'], 'original.mx3'),
                                      (pilot / first['output'] / 'table.txt', 'original_table.txt')):
                shutil.copy2(source_path, origin / name)
                files.append(dict(path=(origin/name).relative_to(output).as_posix(), sha256=file_hash(origin/name)))
            original = (origin/'original.mx3').read_text(encoding='utf-8')
            for condition in p['conditions']:
                script = output / 'prepared_scripts' / case / (condition + '.mx3')
                script.parent.mkdir(parents=True, exist_ok=True)
                script.write_text(initial_state_script(original, condition, H_GRID), encoding='utf-8')
                jobs.append(dict(case_id=case, grade=grade, direction=direction, grain_id=1, condition=condition,
                    script=script.relative_to(output).as_posix(), script_sha256=file_hash(script),
                    source_script_sha256=first['script_sha256'], source_table_sha256=file_hash(origin/'original_table.txt'),
                    status='prepared_not_executed'))
    summaries = []
    for grade in GRADES:
        for direction in ('RD', 'TD'):
            group = [r for r in rows if r['grade'] == grade and r['direction'] == direction]
            summaries.append(dict(grade=grade, direction=direction, grains=len(group),
                endpoint_screen_failures=sum(r['endpoint_screen_failed'] for r in group),
                inversion_screen_failures=sum(r['inversion_screen_failed'] for r in group),
                unknown_torque_grains=sum(not r['torque_gate_evaluable'] for r in group),
                max_abs_unforced_midpoint_H0_T=max(abs(r['native_unforced_midpoint_H0_T']) for r in group),
                mean_B800_midpoint_T=float(np.mean([r['B800_after_guard_T'] for r in group])),
                mean_H0_branch_separation_T=float(np.mean([r['native_branch_separation_H0_T'] for r in group]))))
    after = {f.relative_to(pilot).as_posix(): file_hash(f) for f in sorted(pilot.rglob('*')) if f.is_file()}
    if before != after:
        raise ValueError('Original native evidence changed')
    write_json(output/'source_integrity.json', dict(files_checked=len(before), unchanged=True, sha256=before))
    write_csv(output/'native_state_metrics.csv', rows)
    report = dict(protocol=p, physics_version=manifest['physics_version'],
        texture_sampling_version=manifest.get('texture_sampling_version', 'legacy_multi_peak_importance_v1'),
        source_manifest_sha256=file_hash(pilot/'manifest.json'), source_native_evidence=sources,
        producer_sha256={name:file_hash(PROJECT/name) for name in producers},
        summaries=summaries, grain_details=details, jobs=jobs, frozen_source_files=files,
        new_MuMax_solves=0, new_Maxwell_solves=0, model_changed=False, all_grains_retained=True)
    write_json(output/'report.json', report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=PROJECT.parent if PROJECT.name == 'magsim' else PROJECT)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    report = prepare(args.root, args.output_dir)
    print(json.dumps(dict(summaries=report['summaries'], prepared_jobs=len(report['jobs']),
        executed_jobs=0, GPU_used=False), indent=2))
