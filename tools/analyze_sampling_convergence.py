"""Read frozen native tables and report empirical grain-sampling sensitivity.

Each prefix comparison is nested within one shuffled ensemble. It is not an
independent material validation or proof that its largest N has converged.
Only a complete requested grade/direction ensemble is accepted.
"""
import argparse
import csv
import json
import sys
from pathlib import Path

import numpy as np

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from modules.material_calibration import file_hash, extract_loop_midpoint
from tools.run_calibration_pilot import write_csv, write_json, H_GRID
from modules.texture_sampling import LEGACY_SAMPLING_VERSION, SAMPLING_VERSION


def read_ensemble(run_dir, grade, direction):
    manifest = json.loads((run_dir / 'manifest.json').read_text(encoding='utf-8'))
    if manifest['physics_version'] != 'cubic_sample_frame_v2':
        raise ValueError('Unsupported native physics version')
    if not np.array_equal(manifest['H_grid'], H_GRID):
        raise ValueError('Frozen H grid differs from the analysis protocol')
    jobs = sorted([j for j in manifest['jobs'] if j['grade'] == grade
                   and j['direction'] == direction], key=lambda j: j['grain_id'])
    if not jobs or len(jobs) != manifest['n_grains']:
        raise ValueError('Manifest has missing/duplicate requested grain jobs')
    if [j['grain_id'] for j in jobs] != list(range(1, len(jobs) + 1)):
        raise ValueError('Invalid grain ID sequence')
    status = json.loads((run_dir / 'run_status.json').read_text(encoding='utf-8'))
    successes = [j for j in status['jobs'] if j['exit_code'] == 0]
    completed = {j['script']: j for j in successes}
    if len(completed) != len(successes):
        raise ValueError('Duplicate successful native jobs in run status')
    values, hashes, unguarded = [], [], []
    material = next(m for m in manifest['materials'] if m['grade'] == grade)
    sampling = manifest.get('texture_sampling_version', LEGACY_SAMPLING_VERSION)
    if sampling == SAMPLING_VERSION:
        samples = material['grain_samples']
        if file_hash(run_dir / samples['path']) != samples['sha256']:
            raise ValueError('Frozen grain provenance changed')
    if file_hash(run_dir / grade / 'orientations.csv') != material['orientations_sha256']:
        raise ValueError('Frozen Euler samples changed')
    for job in jobs:
        if job['script'] not in completed:
            raise ValueError('Requested ensemble incomplete; finish its native jobs first')
        if completed[job['script']]['script_sha256'] != job['script_sha256']:
            raise ValueError('Executed script hash differs from the frozen manifest')
        table = run_dir / job['output'] / 'table.txt'
        if file_hash(run_dir / job['script']) != job['script_sha256']:
            raise ValueError('Frozen simulation script changed')
        digest = file_hash(table)
        if digest != completed[job['script']]['table_sha256']:
            raise ValueError('Native table changed after solve')
        curve = extract_loop_midpoint(table, H_GRID, angle_deg=job['angle'])
        values.append(curve['B'])
        unguarded.append(curve['B_midpoint_before_guard'])
        hashes.append({'grain_id': job['grain_id'], 'table_sha256': digest,
                       'script_sha256': job['script_sha256'],
                       'max_imposed_guard_change_T': curve['report']['max_guard_change_T']})
    return np.array(values), {'seed': manifest['seed'], 'n_grains': len(jobs),
        'material_seed': material.get('seed'), 'texture_sampling_version': sampling,
        'texture_distribution_sha256': material.get('texture_sampling', {}).get('distribution_sha256'),
        'texture_module_sha256': manifest.get('texture_module_sha256'),
        'unguarded_ensemble_mean_B_T': np.mean(unguarded, axis=0).tolist(),
        'params': material.get('params'),
        'manifest_sha256': file_hash(run_dir / 'manifest.json'),
        'orientations_sha256': material['orientations_sha256'],
        'mumax_binary_sha256': status['mumax_binary_sha256'], 'jobs': hashes}


def prefix_metrics(values, counts):
    full = values.mean(axis=0)
    rows = []
    for n in counts:
        if not 2 <= n <= len(values):
            raise ValueError('Prefix count outside completed ensemble')
        mean = values[:n].mean(axis=0)
        delta = mean - full
        rows.append({'n_grains': n, 'B800_raw_mean_T': float(np.interp(800, H_GRID, mean)),
            'B800_grain_std_T': float(np.interp(800, H_GRID, values[:n].std(axis=0, ddof=1))),
            'delta_B800_vs_largest_N_T': float(np.interp(800, H_GRID, delta)),
            'max_curve_delta_vs_largest_N_T': float(np.abs(delta).max()),
            'largest_N': len(values), 'comparison_role': 'nested_prefix_not_independent_validation'})
    return rows


def analyze(run_dir, grade, direction, output_dir, baseline=None):
    values, provenance = read_ensemble(run_dir, grade, direction)
    comparison = None
    if baseline:
        old, old_source = read_ensemble(baseline, grade, direction)
        if old_source['texture_sampling_version'] != provenance['texture_sampling_version']:
            raise ValueError('Cannot treat different texture sampling protocols as a seed comparison')
        if old_source['texture_distribution_sha256'] != provenance['texture_distribution_sha256']:
            raise ValueError('Texture distribution differs between ensembles')
        if provenance['texture_sampling_version'] == SAMPLING_VERSION:
            if old_source['texture_module_sha256'] != provenance['texture_module_sha256']:
                raise ValueError('Texture sampler code differs between ensembles')
        if old_source['mumax_binary_sha256'] != provenance['mumax_binary_sha256']:
            raise ValueError('Cannot compare ensembles solved with different MuMax binaries')
        if old_source['params'] != provenance['params']:
            raise ValueError('Material parameters differ; this is not a pure sampling comparison')
        delta = values.mean(axis=0) - old.mean(axis=0)
        comparison = {'baseline_source': old_source,
            'delta_B800_raw_mean_T': float(np.interp(800, H_GRID, delta)),
            'max_curve_delta_T': float(np.abs(delta).max()),
            'role': ('independent_seed_ensembles_same_analytic_distribution'
                     if provenance['texture_sampling_version'] == SAMPLING_VERSION
                     else 'legacy_separate_ensembles_not_nested'),
            'same_seed': old_source['seed'] == provenance['seed'],
            'same_seed_does_not_imply_same_samples': provenance['texture_sampling_version'] == LEGACY_SAMPLING_VERSION}
    counts = [n for n in (4, 8, 16, 32, 64) if n <= len(values)]
    if len(values) not in counts:
        counts.append(len(values))
    rows = prefix_metrics(values, counts)
    manifest = json.loads((run_dir / 'manifest.json').read_text(encoding='utf-8'))
    material = next(m for m in manifest['materials'] if m['grade'] == grade)
    if provenance['texture_sampling_version'] == SAMPLING_VERSION:
        with (run_dir / material['grain_samples']['path']).open(encoding='utf-8-sig') as stream:
            samples = list(csv.DictReader(stream))
        for row in rows:
            n = row['n_grains']
            row['goss_count'] = sum(s['component'] == 'goss' for s in samples[:n])
            row['goss_fraction_realized'] = row['goss_count'] / n
    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(output_dir / 'prefix_comparison.csv', rows)
    grain_rows = [{'grain_id': i + 1, 'H_A_per_m': float(h), 'B_raw_T': float(b)}
                  for i, curve in enumerate(values) for h, b in zip(H_GRID, curve)]
    write_csv(output_dir / 'grain_curves.csv', grain_rows)
    report = {'status': 'exploratory_sampling_diagnostic_not_convergence_certification',
        'analysis_protocol': 'grain_sampling_sensitivity_v2',
        'analysis_source_sha256': file_hash(Path(__file__)),
        'grade': grade, 'direction': direction, 'H_axis': 'physical_A_per_m',
        'curve_definition': 'guarded_major_loop_midpoint_proxy',
        'source': provenance, 'prefix_comparison': rows,
        'limitations': ['Largest N is a finite comparison, not ground truth',
            'Grain standard deviation is not an experimental confidence interval',
            'Sampling sensitivity is conditional on the versioned prior, not a measured EBSD distribution',
            'One direction/material does not validate a complete calibration bank']}
    guarded_B800 = float(np.interp(800, H_GRID, values.mean(axis=0)))
    unguarded_B800 = float(np.interp(800, H_GRID, provenance['unguarded_ensemble_mean_B_T']))
    report['guard_diagnostic'] = {'B800_unguarded_ensemble_mean_T': unguarded_B800,
        'B800_guarded_ensemble_mean_T': guarded_B800,
        'B800_imposed_mean_change_T': guarded_B800 - unguarded_B800,
        'max_grain_guard_change_T': max(j['max_imposed_guard_change_T'] for j in provenance['jobs']),
        'grains_with_guard_change': sum(j['max_imposed_guard_change_T'] > 1e-12 for j in provenance['jobs']),
        'role': 'imposed_prior_not_observed_magnetization'}
    if comparison is not None:
        report['baseline_comparison'] = comparison
    write_json(output_dir / 'summary.json', report)
    print(json.dumps({k: v for k, v in report.items() if k not in ('source', 'limitations')}, indent=2))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run_dir', type=Path)
    parser.add_argument('--grade', required=True)
    parser.add_argument('--direction', choices=['RD', 'TD'], required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--baseline', type=Path)
    args = parser.parse_args()
    analyze(args.run_dir, args.grade, args.direction, args.output_dir, args.baseline)
