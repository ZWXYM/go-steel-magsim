"""Audit frozen outer banks with paired grain draws; never solve or tune."""
import argparse
import json
import shutil
import sys
from pathlib import Path

import numpy as np

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from modules.calibration_sensitivity import (protocol, checked_ensembles, paired_draws,
    audit_fold, summarize)
from modules.calibration_transfer import TransferBank, digest
from modules.calibration_workbench import CalibrationWorkbench
from modules.material_calibration import file_hash, extract_loop_midpoint, guard_B
from tools.analyze_sampling_convergence import read_ensemble
from tools.run_calibration_pilot import H_GRID, write_json, write_csv


def source_hashes(pilot, artifact):
    return {name: {p.relative_to(root).as_posix(): file_hash(p)
        for p in sorted(root.rglob('*')) if p.is_file()}
        for name, root in (('pilot', pilot), ('frozen_artifact', artifact))}


def run(root, artifact, output):
    root, artifact, output = Path(root).resolve(), Path(artifact).resolve(), Path(output).resolve()
    pilot = root / 'calibration/pilot_20261003_n8'
    if pilot in output.parents or artifact in output.parents:
        raise ValueError('Diagnostic output must be outside the frozen evidence directories')
    if output.exists():
        raise ValueError('Use a new output directory; old evidence is never overwritten')
    # Freeze the diagnostic before inspecting outcomes; this is not a new fit.
    output.mkdir(parents=True)
    write_json(output / 'study_protocol.json', protocol())
    producers = ('modules/calibration_sensitivity.py', 'modules/calibration_transfer.py',
        'modules/calibration_workbench.py', 'modules/material_calibration.py',
        'tools/run_calibration_pilot.py', 'tools/analyze_sampling_convergence.py',
        'tools/audit_calibration_native_sensitivity.py')
    (output / 'frozen_producers').mkdir()
    for name in producers:
        shutil.copy2(PROJECT / name, output / 'frozen_producers' / Path(name).name)
    before = source_hashes(pilot, artifact)
    old_report = json.loads((artifact / 'report.json').read_text(encoding='utf-8'))
    if old_report['source_manifest_sha256'] != file_hash(pilot / 'manifest.json'):
        raise ValueError('Frozen calibration report and native manifest differ')
    samples = CalibrationWorkbench(root, output).samples()['samples']
    samples = sorted(samples, key=lambda s: s['grade'])
    grades = [s['grade'] for s in samples]
    if sorted(old_report['selected_grades']) != grades:
        raise ValueError('Frozen calibration report material membership differs')
    manifest = json.loads((pilot / 'manifest.json').read_text(encoding='utf-8'))
    ensembles, sources, grain_rows, native_rows = {}, {}, [], []
    for sample in samples:
        grade = sample['grade']
        ensembles[grade] = dict(H=H_GRID.tolist())
        for direction in ('RD', 'TD'):
            values, source = read_ensemble(pilot, grade, direction)
            sources[grade + '_' + direction] = source
            jobs = sorted([j for j in manifest['jobs'] if j['grade'] == grade
                and j['direction'] == direction], key=lambda j: j['grain_id'])
            midpoint, descending, ascending = [], [], []
            for job in jobs:
                c = extract_loop_midpoint(pilot / job['output'] / 'table.txt', H_GRID, angle_deg=job['angle'])
                midpoint.append(c['B_midpoint_before_guard'])
                descending.append(c['B_descending'])
                ascending.append(c['B_ascending'])
                for i, h in enumerate(H_GRID):
                    grain_rows.append(dict(grade=grade, direction=direction, grain_id=job['grain_id'],
                        H_A_per_m=h, guarded_B_T=c['B'][i], midpoint_before_guard_B_T=midpoint[-1][i],
                        descending_B_T=descending[-1][i], ascending_B_T=ascending[-1][i]))
            midpoint, descending, ascending = map(np.array, (midpoint, descending, ascending))
            ensembles[grade][direction] = dict(grain_ids=[j['grain_id'] for j in jobs],
                orientations_sha256=source['orientations_sha256'], guarded_B=values, midpoint_B=midpoint)
            guarded_mean, midpoint_mean = values.mean(axis=0), midpoint.mean(axis=0)
            mean_then_guard, _ = guard_B(H_GRID, midpoint_mean)
            native_rows.append(dict(grade=grade, direction=direction, n_grains=len(values),
                H=H_GRID.tolist(), guarded_mean_B_T=guarded_mean.tolist(), midpoint_mean_B_T=midpoint_mean.tolist(),
                mean_then_guard_B_T=mean_then_guard.tolist(),
                per_grain_guard_B800_change_T=float(np.interp(800, H_GRID, guarded_mean-midpoint_mean)),
                noncommuting_guard_B800_change_T=float(np.interp(800, H_GRID, guarded_mean-mean_then_guard)),
                max_grain_guard_change_T=float(np.max(np.abs(values-midpoint))),
                branch_separation_of_means_B800_T=float(np.interp(800, H_GRID, descending.mean(axis=0)-ascending.mean(axis=0))),
                mean_absolute_grain_branch_separation_B800_T=float(np.interp(800, H_GRID, np.abs(descending-ascending).mean(axis=0))),
                mean_descending_B_T=descending.mean(axis=0).tolist(), mean_ascending_B_T=ascending.mean(axis=0).tolist(),
                branch_values_are_not_normal_curve_targets=True))
    ensembles = checked_ensembles(ensembles)
    draws = paired_draws(ensembles)
    write_json(output / 'paired_grain_draws.json', {g: a.tolist() for g, a in draws.items()})
    rows, model_sources = [], {}
    for sample in samples:
        grade = sample['grade']
        path = artifact / 'holdout_banks' / (grade + '.json')
        bank = TransferBank(json.loads(path.read_text(encoding='utf-8')))
        eligible = [s for s in samples if s['grade'] != grade]
        if digest(eligible) != digest(bank.samples):
            raise ValueError('Frozen outer bank training data differs from current source')
        old_fold = next(f for f in old_report['nested_validation']['outer_folds'] if f['heldout'] == grade)
        if old_fold['bank_sha256'] != bank.bank_sha256:
            raise ValueError('Outer bank hash differs from the frozen calibration report')
        model_sources[grade] = dict(bank_sha256=bank.bank_sha256, file_sha256=file_hash(path),
            candidate=bank.candidate['id'], selection_sha256=digest(bank.payload['selection']))
        fold = audit_fold(sample, bank, ensembles, draws)
        for row in fold:
            old_curve = next(c for c in old_report['comparisons'] if c['grade'] == grade
                and c['direction'] == row['direction'])
            if not np.allclose(row['frozen_prediction_B_T'], old_curve['holdout_B'], atol=1e-12, rtol=0):
                raise ValueError('Audit baseline differs from the previous published prediction')
        rows.extend(fold)
    report = dict(protocol=protocol(), protocol_sha256=digest(protocol()),
        frozen_artifact_id=old_report['id'], frozen_bank_sha256=old_report['bank_sha256'],
        H_axis='physical_A_per_m', physics_version=old_report['physics_version'],
        texture_sampling_version=old_report['texture_sampling_version'],
        source_native_evidence=sources, frozen_outer_banks=model_sources,
        producer_sha256={name: file_hash(PROJECT / name) for name in producers},
        paired_draws_sha256=file_hash(output / 'paired_grain_draws.json'),
        native_diagnostics=native_rows, folds=rows, summary=summarize(rows),
        model_changed=False, independently_validated=False, calibrated_confidence_interval=False)
    write_json(output / 'report.json', report)
    write_csv(output / 'grain_curves.csv', grain_rows)
    write_csv(output / 'scores.csv', (dict(grade=r['grade'], direction=r['direction'], method=name, **m)
        for r in rows for name, m in r['scores'].items()))
    write_csv(output / 'sensitivity.csv', (dict(grade=r['grade'], direction=r['direction'], mode=mode,
        **{k: v for k, v in values.items() if not isinstance(v, dict)},
        B800_p025_T=values['conditional_B800_T']['p025'], B800_median_T=values['conditional_B800_T']['median'],
        B800_p975_T=values['conditional_B800_T']['p975']) for r in rows for mode, values in r['sensitivity'].items()))
    after = source_hashes(pilot, artifact)
    if before != after:
        raise ValueError('Frozen native/calibration evidence changed during the audit')
    write_json(output / 'source_integrity.json', dict(unchanged=True,
        source_file_counts={name: len(files) for name, files in before.items()}, sha256=before,
        new_MuMax_solves=0, new_Maxwell_solves=0))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=PROJECT.parent if PROJECT.name == 'magsim' else PROJECT)
    parser.add_argument('--artifact', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    report = run(args.root, args.artifact, args.output_dir)
    print(json.dumps(report['summary'], indent=2))


if __name__ == '__main__':
    main()
