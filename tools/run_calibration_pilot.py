"""Prepare/run/resume/analyze an isolated four-material MuMax3 pilot.

Uses report-labelled ODF and nominal Si, frozen existing reference CSVs,
explicit major-loop branches, and material-level exclusion. No production
model/cache is overwritten; an engineering pilot is not experimental validation.
"""
from __future__ import annotations

import argparse
import contextlib
import csv
import io
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

PROJECT = Path(__file__).resolve().parents[1]
# Support both the consolidated thesis workspace and a standalone public clone.
ROOT = PROJECT if (PROJECT / 'calibration').is_dir() else PROJECT.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / 'modules'))

from modules.material_calibration import (CalibrationBank, VERSION, PHYSICS_VERSION,
    MU0, extract_loop_midpoint, file_hash, guard_B)
from modules.mx3_generator import SimulationConfig, generate_single_mode_script
from modules.reference_corrector import load_reference_bh
from modules.texture_sampling import (LEGACY_SAMPLING_VERSION, SAMPLING_VERSION,
                                      sample_texture)

H_GRID = np.array([0, .1, .2, .5, 1, 2, 5, 10, 20, 50, 100, 200, 500, 800,
                  1000, 1500, 2000, 3000, 5000, 7500, 10000, 15000, 20000,
                  30000, 40000, 50000], dtype=float)
GRADES = ['B23R075', 'B27R090', 'B27R095', 'B30P105']


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2,
                         allow_nan=False), encoding='utf-8')


def write_csv(path, rows):
    rows = list(rows)
    with Path(path).open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def pilot_script(euler, angle):
    script = generate_single_mode_script(1, *euler, angle)
    # Keep exactly the newly versioned material/geometry setup. Replace the
    # uniform loop by a grid resolving 100/200/500/800 A/m explicitly.
    prefix = script.split('tableautosave(')[0]
    signed_grid = np.concatenate([-H_GRID[:0:-1], H_GRID])
    # MuMax3 3.11.1 does not support Go composite array literals. Emit the
    # frozen field schedule explicitly instead of []float64{...}.
    body = '\nbranch := -1.0\ntableaddvar(branch, "branch", "")\nH := 0.0\n'
    for label, schedule in [(-1, signed_grid[::-1]), (1, signed_grid)]:
        body += f'branch = {label}.0\n'
        for h in schedule:
            body += (f'H = {h:.12g}\n'
                     'B_ext = vector(mu0*H*Hx_dir, mu0*H*Hy_dir, mu0*H*Hz_dir)\n'
                     'minimize()\ntablesave()\n')
    return prefix + body


def prepare(run_dir, n_grains, seed, texture_sampling_version=None):
    if (run_dir / 'manifest.json').exists():
        manifest = json.loads((run_dir / 'manifest.json').read_text(encoding='utf-8'))
        if manifest['n_grains'] != n_grains or manifest['seed'] != seed:
            raise ValueError('Existing run has different grain count/seed; use a new --run-dir')
        frozen_sampling = manifest.get('texture_sampling_version', LEGACY_SAMPLING_VERSION)
        if texture_sampling_version is not None and texture_sampling_version != frozen_sampling:
            raise ValueError('Existing run has different texture sampling version; use a new run')
        for material in manifest['materials']:
            if file_hash(run_dir / material['grade'] / 'orientations.csv') != material['orientations_sha256']:
                raise ValueError('Frozen orientation samples changed')
            if 'grain_samples' in material:
                samples = material['grain_samples']
                if file_hash(run_dir / samples['path']) != samples['sha256']:
                    raise ValueError('Frozen grain provenance changed')
            for reference in material['references'].values():
                if file_hash(run_dir / reference['path']) != reference['sha256']:
                    raise ValueError('Frozen reference changed')
        for job in manifest['jobs']:
            if file_hash(run_dir / job['script']) != job['script_sha256']:
                raise ValueError('Existing script changed; use a new run directory')
        return manifest
    texture_sampling_version = texture_sampling_version or SAMPLING_VERSION
    if texture_sampling_version not in (SAMPLING_VERSION, LEGACY_SAMPLING_VERSION):
        raise ValueError('Unsupported texture sampling version')
    run_dir.mkdir(parents=True, exist_ok=True)
    registry_path = ROOT / 'calibration/material_registry.csv'
    with registry_path.open(encoding='utf-8-sig') as registry_stream:
        registry = {r['grade']: r for r in csv.DictReader(registry_stream)}
    manifest = {'created_utc': datetime.now(timezone.utc).isoformat(),
                'calibration_version': VERSION, 'physics_version': PHYSICS_VERSION,
                'status': 'experimental_pilot', 'H_axis': 'physical_A_per_m',
                'n_grains': n_grains, 'seed': seed, 'H_grid': H_GRID.tolist(),
                'registry_sha256': file_hash(registry_path), 'materials': [], 'jobs': []}
    manifest['texture_sampling_version'] = texture_sampling_version
    manifest['texture_module_sha256'] = file_hash(PROJECT / 'modules' /
        ('texture_sampling.py' if texture_sampling_version == SAMPLING_VERSION else 'odf_texture.py'))
    original_si = SimulationConfig.SI_CONTENT
    try:
        for idx, grade in enumerate(GRADES):
            record = registry[grade]
            params = {'f_Goss': float(record['report_f_Goss']),
                      'theta_0_deg': float(record['report_theta_deg']),
                      'halfwidth_deg': float(record['report_halfwidth_deg']),
                      'Si_content': float(record['report_Si_wt_percent'])}
            material_dir = run_dir / grade
            material_dir.mkdir()
            sampling_metadata, grain_samples = None, None
            if texture_sampling_version == SAMPLING_VERSION:
                eulers, grain_samples, sampling_metadata = sample_texture(params['f_Goss'],
                    params['theta_0_deg'], n_grains, params['halfwidth_deg'], seed + idx)
            else:
                from modules.odf_texture import generate_texture_with_odf
                np.random.seed(seed + idx)
                with contextlib.redirect_stdout(io.StringIO()):
                    eulers = generate_texture_with_odf(params['f_Goss'], params['theta_0_deg'],
                        n_grains, params['halfwidth_deg'], plot_odf=False,
                        output_dir=str(material_dir / 'texture'))
            if len(eulers) != n_grains:
                raise ValueError('Texture generator returned a different grain count')
            # Freeze Euler samples explicitly; reproducibility uses these exact
            # values even if a future orix random generator changes.
            euler_path = material_dir / 'orientations.csv'
            np.savetxt(euler_path, eulers, delimiter=',', header='phi1,Phi,phi2', comments='')
            material = {'grade': grade, 'params': params, 'seed': seed + idx,
                        'thickness_mm': float(record['thickness_mm']),
                        'Si_status': 'report_nominal_unverified_batch_composition',
                        'ODF_status': 'report_estimated_not_independent_EBSD',
                        'orientations_sha256': file_hash(euler_path), 'references': {}}
            if grain_samples is not None:
                samples_path = material_dir / 'grain_samples.csv'
                write_csv(samples_path, grain_samples)
                material['grain_samples'] = {'path': samples_path.relative_to(run_dir).as_posix(),
                                            'sha256': file_hash(samples_path)}
                material['texture_sampling'] = sampling_metadata
            SimulationConfig.SI_CONTENT = params['Si_content']
            for direction, angle in [('RD', 0), ('TD', 90)]:
                # Copy references into the run: subsequent library edits cannot
                # silently change the validation target of an existing run.
                src = PROJECT / 'go_steel_data/output' / f'{grade}_{direction}.csv'
                ref = material_dir / f'reference_{direction}.csv'
                ref.write_bytes(src.read_bytes())
                material['references'][direction] = {
                    'path': ref.relative_to(run_dir).as_posix(),
                    'sha256': file_hash(ref), 'source': src.relative_to(ROOT).as_posix(),
                    'status': 'existing_processed_catalog_reference_not_batch_measurement'}
                script_dir = material_dir / direction / 'scripts'
                script_dir.mkdir(parents=True)
                for grain_id, euler in enumerate(eulers, 1):
                    path = script_dir / f'grain_{grain_id:03d}.mx3'
                    path.write_text(pilot_script(euler, angle), encoding='utf-8')
                    output = material_dir / direction / 'raw' / f'grain_{grain_id:03d}.out'
                    manifest['jobs'].append({'grade': grade, 'direction': direction,
                        'angle': angle, 'grain_id': grain_id,
                        'script': path.relative_to(run_dir).as_posix(),
                        'script_sha256': file_hash(path),
                        'output': output.relative_to(run_dir).as_posix()})
            manifest['materials'].append(material)
    finally:
        SimulationConfig.SI_CONTENT = original_si
    write_json(run_dir / 'manifest.json', manifest)
    return manifest


def resolve_mumax(explicit=None):
    """Resolve an explicit executable, MUMAX3_EXE, local runtime, or PATH."""
    requested = explicit or os.environ.get('MUMAX3_EXE')
    if requested:
        binary = Path(requested).expanduser().resolve()
        if not binary.is_file():
            raise FileNotFoundError(f'MuMax3 executable does not exist: {binary}')
        return binary
    local = ROOT / '.runtime/mumax3/mumax3.exe'
    if local.is_file():
        return local
    found = shutil.which('mumax3')
    if found:
        return Path(found).resolve()
    raise FileNotFoundError('Set --mumax or MUMAX3_EXE, or add mumax3 to PATH')


def run_jobs(run_dir, manifest, max_jobs=None, mumax=None, only_grade=None, only_direction=None):
    # Two writers on one run_status can lose successfully completed jobs.
    # Serialize ownership per run; a crash leaves a lock for explicit review.
    lock = run_dir / 'runner.lock'
    try:
        handle = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except FileExistsError as exc:
        raise ValueError('Run directory already locked; inspect its runner.lock and process') from exc
    try:
        with os.fdopen(handle, 'w', encoding='utf-8') as stream:
            stream.write(json.dumps({'pid': os.getpid(), 'created_utc': datetime.now(timezone.utc).isoformat()}))
        return _run_jobs(run_dir, manifest, max_jobs, mumax, only_grade, only_direction)
    finally:
        lock.unlink()


def _run_jobs(run_dir, manifest, max_jobs=None, mumax=None, only_grade=None, only_direction=None):
    mumax = resolve_mumax(mumax)
    runtime = {'mumax_binary_sha256': file_hash(mumax),
               'mumax_version_query': subprocess.run([str(mumax), '-v'],
                    capture_output=True, text=True, errors='replace').stdout,
               'jobs': []}
    status_path = run_dir / 'run_status.json'
    if status_path.exists():
        previous_runtime = json.loads(status_path.read_text(encoding='utf-8'))
        if previous_runtime['mumax_binary_sha256'] != runtime['mumax_binary_sha256']:
            raise ValueError('MuMax3 binary changed; use a new run directory')
        runtime['jobs'] = previous_runtime['jobs']
    done = {j['script']: j for j in runtime['jobs'] if j['exit_code'] == 0}
    executed = 0
    for idx, job in enumerate(manifest['jobs'], 1):
        if only_grade and job['grade'] != only_grade:
            continue
        if only_direction and job['direction'] != only_direction:
            continue
        output = run_dir / job['output']
        table = output / 'table.txt'
        if job['script'] in done:
            previous = done[job['script']]
            if (table.exists() and file_hash(table) == previous['table_sha256']
                    and file_hash(run_dir / job['script']) == previous['script_sha256']):
                continue
            raise ValueError(f'Completed job contents changed: {job["script"]}')
        if max_jobs is not None and executed >= max_jobs:
            break
        if output.exists():
            raise ValueError(f'Unverified output already exists: {output}; preserve it and use a new run')
        output.parent.mkdir(parents=True, exist_ok=True)
        log_path = output.parent / (output.stem + '.log')
        started = time.perf_counter()
        with log_path.open('w', encoding='utf-8') as log:
            proc = subprocess.run([str(mumax), '-http=127.0.0.1:0', '-o', str(output),
                                   str(run_dir / job['script'])], stdout=log,
                                   stderr=subprocess.STDOUT, cwd=PROJECT)
        result = {**job, 'exit_code': proc.returncode, 'execution_kind': 'native',
                  'elapsed_seconds': round(time.perf_counter() - started, 3),
                  'table_sha256': file_hash(table) if table.exists() else None}
        if proc.returncode == 0:
            extract_loop_midpoint(table, H_GRID, angle_deg=job['angle'])
        runtime['jobs'].append(result)
        write_json(status_path, runtime)
        print(f'[{idx}/{len(manifest["jobs"])}] {job["grade"]} {job["direction"]} '
              f'grain {job["grain_id"]}: exit={proc.returncode}, '
              f'{result["elapsed_seconds"]}s', flush=True)
        if proc.returncode:
            raise RuntimeError(f'MuMax failed; inspect {log_path}')
        executed += 1


def read_frozen_reference(run_dir, item):
    path = run_dir / item['path']
    if file_hash(path) != item['sha256']:
        raise ValueError('Frozen reference hash mismatch')
    data = np.loadtxt(path, delimiter=',', comments='#')
    return np.interp(H_GRID, data[:, 0], data[:, 1])


def metrics(H, predicted, reference):
    err = np.asarray(predicted) - np.asarray(reference)
    active = H >= 100
    return {'rmse_T': float(np.sqrt(np.mean(err[active] ** 2))),
            'mae_T': float(np.mean(np.abs(err[active]))),
            'low_field_rmse_T': float(np.sqrt(np.mean(err[(H >= 100) & (H <= 1000)] ** 2))),
            'high_field_rmse_T': float(np.sqrt(np.mean(err[H >= 5000] ** 2))),
            'B800_error_T': float(np.interp(800, H, err)),
            'B1000_error_T': float(np.interp(1000, H, err))}


def analyze(run_dir, manifest):
    sampling = manifest.get('texture_sampling_version', LEGACY_SAMPLING_VERSION)
    status = json.loads((run_dir / 'run_status.json').read_text(encoding='utf-8'))
    completed = {j['script']: j for j in status['jobs'] if j['exit_code'] == 0}
    anchors, aggregate, targets = [], {}, {}
    for material in manifest['materials']:
        grade = material['grade']
        for direction, angle in [('RD', 0), ('TD', 90)]:
            jobs = [j for j in manifest['jobs'] if j['grade'] == grade
                    and j['direction'] == direction]
            curves = []
            for job in jobs:
                if job['script'] not in completed:
                    raise ValueError('Incomplete pilot: all material/direction jobs required')
                if (file_hash(run_dir / job['script']) != job['script_sha256']
                        or completed[job['script']]['script_sha256'] != job['script_sha256']):
                    raise ValueError('Executed script differs from the frozen manifest')
                table = run_dir / job['output'] / 'table.txt'
                if file_hash(table) != completed[job['script']]['table_sha256']:
                    raise ValueError('Raw table changed after solve')
                curve = extract_loop_midpoint(table, H_GRID, angle_deg=angle)
                curves.append(curve['B'])
                write_json(table.parent / 'extraction.json', curve)
            values = np.array(curves)
            mean, guard = guard_B(H_GRID, values.mean(axis=0))
            std = values.std(axis=0, ddof=1) if len(values) > 1 else np.zeros(len(H_GRID))
            raw_path = run_dir / grade / f'aggregate_{direction}.csv'
            write_csv(raw_path, ({'H_A_per_m': float(h), 'B_raw_mean_T': float(b),
                'grain_std_T': float(s), 'n_grains': len(values)}
                for h, b, s in zip(H_GRID, mean, std)))
            reference = read_frozen_reference(run_dir, material['references'][direction])
            aggregate[grade, direction] = mean
            targets[grade, direction] = reference
            anchors.append({'grade': grade, 'direction': direction, 'params': material['params'],
                'texture_sampling_version': sampling,
                'H': H_GRID.tolist(), 'delta_B': (reference - mean).tolist(),
                'raw_aggregate_sha256': file_hash(raw_path), 'n_grains_completed': len(values),
                'reference': material['references'][direction], 'guard': guard})
    payload = {'calibration_version': VERSION, 'physics_version': PHYSICS_VERSION,
        'texture_sampling_version': sampling,
        'status': 'experimental_pilot', 'H_axis': 'physical_A_per_m',
        'curve_definition': 'equal_volume_mean_of_major_loop_midpoint_proxy',
        'feature_distance_scales': {'f_Goss': .5, 'theta_0_deg': 10,
                                   'halfwidth_deg': 10, 'Si_content': .2},
        'feature_scale_status': 'fixed_protocol_prior_not_fitted',
        'manifest_sha256': file_hash(run_dir / 'manifest.json'), 'anchors': anchors,
        'analysis_source_sha256': file_hash(Path(__file__)),
        'calibration_module_sha256': file_hash(PROJECT / 'modules/material_calibration.py')}
    bank_path = run_dir / 'calibration_bank.json'
    write_json(bank_path, payload)
    bank = CalibrationBank.load(bank_path)
    evaluation, predictions = [], []
    for material in manifest['materials']:
        grade = material['grade']
        for direction in ('RD', 'TD'):
            raw, reference = aggregate[grade, direction], targets[grade, direction]
            # Physically omit the held-out material from the fitted payload.
            fold_payload = {**payload, 'anchors': [a for a in anchors if a['grade'] != grade]}
            fold_path = run_dir / f'bank_holdout_{grade}.json'
            write_json(fold_path, fold_payload)
            fold = CalibrationBank.load(fold_path)
            lomo = fold.correct(H_GRID, raw, material['params'], direction=direction,
                                exclude_grades=[grade], texture_sampling_version=sampling)
            fitted = bank.correct(H_GRID, raw, material['params'], direction=direction,
                                  texture_sampling_version=sampling)
            # Diagnostic control: remove transfer of the small-ensemble raw
            # residual while retaining exactly the same training-only weights.
            # This is a reference-only baseline, not a MuMax calibration result.
            assert grade not in lomo['anchor_weights']
            reference_only = sum(w * targets[g, direction]
                                 for g, w in lomo['anchor_weights'].items())
            reference_only, _ = guard_B(H_GRID, reference_only)
            for label, curve in [('raw', raw), ('fitted_same_material', fitted['B']),
                                 ('leave_one_material_out', lomo['B']),
                                 ('reference_only_holdout_baseline', reference_only)]:
                evaluation.append({'grade': grade, 'direction': direction, 'method': label,
                    'n_grains': manifest['n_grains'], **metrics(H_GRID, curve, reference),
                    'outside_feature_box': lomo['outside_feature_box'] if label.endswith('out') else '',
                    'bank_sha256': fold.bank_sha256 if label in ('leave_one_material_out',
                                   'reference_only_holdout_baseline') else bank.bank_sha256})
            write_json(run_dir / grade / f'holdout_prediction_{direction}.json', lomo)
            for h, r, target, fit, held, baseline in zip(H_GRID, raw, reference,
                                                        fitted['B'], lomo['B'], reference_only):
                predictions.append({'grade': grade, 'direction': direction, 'H_A_per_m': float(h),
                    'B_raw_T': float(r), 'B_reference_T': float(target),
                    'B_fitted_T': float(fit), 'B_holdout_T': float(held),
                    'B_reference_only_holdout_T': float(baseline)})
    write_csv(run_dir / 'validation_metrics.csv', evaluation)
    write_csv(run_dir / 'curve_comparison.csv', predictions)
    held = [r for r in evaluation if r['method'] == 'leave_one_material_out']
    # Keep training-format diagnostics outside data/datasets. Both trainers
    # reject this role until a properly independent simulation set is built.
    diagnostic_rows = []
    from modules.maxwell_exporter import export_calibrated_pair
    for material in manifest['materials']:
        grade = material['grade']
        raw_pair = {'material_id': grade, 'params': material['params'],
                    'simulation_physics_version': PHYSICS_VERSION,
                    'texture_sampling_version': sampling,
                    'curve_definition': payload['curve_definition'],
                    'manifest_sha256': payload['manifest_sha256'],
                    'thickness_mm': material['thickness_mm']}
        row = {'material_id': grade, **material['params'], 'N_grains': manifest['n_grains'],
            'dataset_role': 'calibration_fit_diagnostics',
            'simulation_physics_version': PHYSICS_VERSION, 'reference_correction_version': VERSION,
            'H_axis': 'physical_A_per_m', 'calibration_sha256': bank.bank_sha256}
        row['texture_sampling_version'] = sampling
        fitted_pair, held_pair = {}, {}
        for direction, angle in [('RD', 0), ('TD', 90)]:
            raw = aggregate[grade, direction]
            fitted_pair[direction] = bank.correct(H_GRID, raw, material['params'], direction=direction,
                                                 texture_sampling_version=sampling)
            held_pair[direction] = json.loads((run_dir / grade /
                                      f'holdout_prediction_{direction}.json').read_text(encoding='utf-8'))
            raw_pair[direction] = {'H': H_GRID.tolist(), 'B': raw.tolist()}
            for h in [100, 200, 500, 1000, 2000, 3000, 5000, 7500]:
                row[f'B_{angle}deg_H{h}'] = float(np.interp(h, H_GRID, fitted_pair[direction]['B']))
        write_json(run_dir / grade / 'raw_pair.json', raw_pair)
        write_json(run_dir / grade / 'fitted_pair.json', fitted_pair)
        export_calibrated_pair(fitted_pair['RD'], fitted_pair['TD'], f'PILOT_{grade}_fit',
            thickness_mm=material['thickness_mm'], export_dir=str(run_dir / 'exports'))
        export_calibrated_pair(held_pair['RD'], held_pair['TD'], f'PILOT_{grade}_holdout',
            thickness_mm=material['thickness_mm'], export_dir=str(run_dir / 'exports'))
        diagnostic_rows.append(row)
    write_csv(run_dir / 'calibration_fit_diagnostics.csv', diagnostic_rows)
    summary = {'status': 'experimental_pilot_not_promoted', 'calibration_version': VERSION,
        'texture_sampling_version': sampling,
        'physics_version': PHYSICS_VERSION, 'n_materials': 4,
        'n_grains_per_material_direction': manifest['n_grains'],
        'completed_simulations': len(manifest['jobs']), 'H_axis': 'physical_A_per_m',
        'H_scale': 1.0, 'bank_sha256': bank.bank_sha256,
        'mean_holdout_rmse_T': float(np.mean([r['rmse_T'] for r in held])),
        'max_abs_holdout_B800_error_T': float(max(abs(r['B800_error_T']) for r in held)),
        'B800_target_0_05T_pass_all_directions': all(abs(r['B800_error_T']) < .05 for r in held),
        'mean_raw_rmse_T': float(np.mean([r['rmse_T'] for r in evaluation if r['method'] == 'raw'])),
        'mean_fitted_rmse_T': float(np.mean([r['rmse_T'] for r in evaluation if r['method'] == 'fitted_same_material'])),
        'mean_reference_only_holdout_rmse_T': float(np.mean([r['rmse_T'] for r in evaluation
                                      if r['method'] == 'reference_only_holdout_baseline'])),
        'max_reference_only_holdout_B800_error_T': float(max(abs(r['B800_error_T'])
                     for r in evaluation if r['method'] == 'reference_only_holdout_baseline')),
        'AMAT_export_status': '8_experimental_BH_only_files_zero_uncalibrated_loss',
        'limitations': ['Nominal Si/estimated ODF from report, not same-batch composition/EBSD',
            'Existing processed catalog curves, not independently authenticated measurements',
            'Small grain ensemble; no convergence evidence',
            'Major-loop midpoint proxy is not a virgin or normal magnetization curve',
            'No measured Hc, Br or iron-loss calibration; no motor solve'],
        'validation_metrics': evaluation}
    write_json(run_dir / 'summary.json', summary)
    print(json.dumps({k: v for k, v in summary.items() if k not in ('validation_metrics', 'limitations')},
                     ensure_ascii=False, indent=2), flush=True)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, default=ROOT / 'calibration/pilot_20261003_n8')
    parser.add_argument('--n-grains', type=int, default=8)
    parser.add_argument('--seed', type=int, default=20261003)
    parser.add_argument('--texture-sampling-version', choices=[SAMPLING_VERSION, LEGACY_SAMPLING_VERSION],
                        help='New runs default to Haar/prefix v2; existing frozen runs retain their protocol')
    parser.add_argument('--prepare', action='store_true')
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--analyze', action='store_true')
    parser.add_argument('--max-jobs', type=int)
    parser.add_argument('--mumax', type=Path, help='MuMax3 executable (or use MUMAX3_EXE/PATH)')
    parser.add_argument('--only-grade', choices=GRADES, help='Run only this grade; manifest stays complete')
    parser.add_argument('--only-direction', choices=['RD', 'TD'], help='Run only this direction')
    args = parser.parse_args()
    if args.n_grains < 2:
        parser.error('At least two grains are required for the pilot')
    manifest = prepare(args.run_dir.resolve(), args.n_grains, args.seed, args.texture_sampling_version)
    print(f'Prepared {len(manifest["jobs"])} jobs in {args.run_dir}', flush=True)
    if args.run:
        run_jobs(args.run_dir.resolve(), manifest, args.max_jobs, args.mumax,
                 args.only_grade, args.only_direction)
    if args.analyze:
        analyze(args.run_dir.resolve(), manifest)


if __name__ == '__main__':
    main()
