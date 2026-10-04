"""Exact finite-ensemble composition/within-component diagnostics, not labels."""
import argparse
import csv
import json
import sys
from pathlib import Path

import numpy as np

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from modules.material_calibration import file_hash
from modules.texture_sampling import SAMPLING_VERSION
from tools.analyze_sampling_convergence import read_ensemble
from tools.run_calibration_pilot import H_GRID, write_csv, write_json

VERSION = 'component_fixed_prior_diagnostic_v1'


def decompose(values, components, prior):
    values = np.asarray(values, dtype=float)
    components = np.asarray(components)
    if (values.ndim != 2 or len(values) != len(components) or not np.isfinite(values).all()
            or not 0 < prior < 1 or not set(components) <= {'goss', 'haar_background'}):
        raise ValueError('Finite matrix, matching component labels and interior prior required')
    groups = [values[components == kind] for kind in ('goss', 'haar_background')]
    if any(len(group) < 2 for group in groups):
        raise ValueError('At least two completed grains in each component required')
    g, b = [group.mean(axis=0) for group in groups]
    q = len(groups[0]) / len(values)
    mean = values.mean(axis=0)
    fixed = prior*g + (1-prior)*b
    count_term = (q-prior)*(g-b)
    within_g = q*groups[0].var(axis=0)
    within_b = (1-q)*groups[1].var(axis=0)
    between = q*(1-q)*(g-b)**2
    closure_mean = mean-fixed-count_term
    closure_variance = values.var(axis=0)-within_g-within_b-between
    if max(np.max(np.abs(closure_mean)), np.max(np.abs(closure_variance))) > 1e-12:
        raise ValueError('Decomposition identity failed')
    return dict(n=len(values), goss_count=len(groups[0]), background_count=len(groups[1]),
                realized_fraction=q, prior_fraction=prior, goss_mean=g, background_mean=b,
                realized_mean=mean, fixed_prior_diagnostic=fixed, count_term=count_term,
                within_goss_population_variance=within_g,
                within_background_population_variance=within_b,
                between_component_population_variance=between,
                total_population_variance=values.var(axis=0),
                iid_mean_plugin_SE=values.std(axis=0, ddof=1)/np.sqrt(len(values)),
                fixed_prior_plugin_SE=np.sqrt(prior**2*groups[0].var(axis=0, ddof=1)/len(groups[0])
                    + (1-prior)**2*groups[1].var(axis=0, ddof=1)/len(groups[1])),
                max_mean_identity_residual_T=float(np.max(np.abs(closure_mean))),
                max_variance_identity_residual_T2=float(np.max(np.abs(closure_variance))))


def symmetric_difference(first, second):
    q1, q2 = first['realized_fraction'], second['realized_fraction']
    q = (q1+q2)/2
    count = (q2-q1)*(first['goss_mean']+second['goss_mean']
                    -first['background_mean']-second['background_mean'])/2
    within_g = q*(second['goss_mean']-first['goss_mean'])
    within_b = (1-q)*(second['background_mean']-first['background_mean'])
    delta = second['realized_mean']-first['realized_mean']
    if np.max(np.abs(delta-count-within_g-within_b)) > 1e-12:
        raise ValueError('Symmetric seed-difference identity failed')
    return dict(delta=delta, count=count, within_goss=within_g, within_background=within_b)


def analyze(run_dirs, grade, directions, output_dir):
    groups, provenance = {}, []
    for run in run_dirs:
        manifest = json.loads((run/'manifest.json').read_text(encoding='utf-8-sig'))
        material = next(m for m in manifest['materials'] if m['grade'] == grade)
        for direction in directions:
            values, source = read_ensemble(run, grade, direction)
            if source['texture_sampling_version'] != SAMPLING_VERSION:
                raise ValueError('Component diagnostics require Haar-prefix grain identities')
            with (run/material['grain_samples']['path']).open(encoding='utf-8-sig') as stream:
                samples = list(csv.DictReader(stream))
            if [int(s['grain_id']) for s in samples] != list(range(1, len(values)+1)):
                raise ValueError('Grain provenance does not match completed ensemble')
            result = decompose(values, [s['component'] for s in samples], material['params']['f_Goss'])
            key = (source['seed'], direction)
            if key in groups:
                raise ValueError('Duplicate seed/direction ensemble')
            groups[key] = result
            provenance.append(dict(direction=direction, source=source,
                                   grain_samples_sha256=material['grain_samples']['sha256']))
    sources = [p['source'] for p in provenance]
    for field in ('texture_distribution_sha256', 'texture_module_sha256', 'mumax_binary_sha256', 'params', 'n_grains'):
        if any(s[field] != sources[0][field] for s in sources):
            raise ValueError('Cohort differs in ' + field)
    rows, stats, pairs = [], [], []
    for (seed, direction), result in groups.items():
        row = {k: v for k, v in result.items() if not isinstance(v, np.ndarray)}
        row.update(manifest_seed=seed, direction=direction)
        for field in ('realized_mean', 'fixed_prior_diagnostic', 'count_term', 'iid_mean_plugin_SE', 'fixed_prior_plugin_SE'):
            row[field+'_H800_T'] = float(np.interp(800, H_GRID, result[field]))
        stats.append(row)
        for index, h in enumerate(H_GRID):
            rows.append(dict(manifest_seed=seed, direction=direction, H_A_per_m=float(h),
                **{k: float(v[index]) for k, v in result.items() if isinstance(v, np.ndarray)}))
    for direction in directions:
        seeds = sorted(seed for seed, d in groups if d == direction)
        for i, a in enumerate(seeds):
            for b in seeds[i+1:]:
                delta = symmetric_difference(groups[a, direction], groups[b, direction])
                pairs.append(dict(direction=direction, seed1=a, seed2=b,
                    **{k+'_H800_T': float(np.interp(800, H_GRID, v)) for k, v in delta.items()},
                    max_abs_delta_curve_T=float(np.max(np.abs(delta['delta']))),
                    max_identity_residual_T=float(np.max(np.abs(delta['delta']-delta['count']
                        -delta['within_goss']-delta['within_background'])))))
    report = dict(status='exploratory_exact_finite_ensemble_decomposition', aggregation_version=VERSION,
        grade=grade, H_axis='physical_A_per_m', curve='guarded_major_loop_midpoint_proxy',
        analysis_source_sha256=file_hash(Path(__file__)), source=provenance,
        ensembles=stats, symmetric_seed_differences=pairs,
        calibration_bank_updated=False, training_labels_created=False,
        limitations=['Fixed-prior estimator is a diagnostic control chosen after N64; no promotion or selection on its error',
                    'Population variance components describe observed grain values, not uncertainty in measured EBSD',
                    'Plug-in standard errors assume independent draws; no simultaneous band or convergence certificate',
                    'Signed seed-difference terms are exact accounting, not causal shares or fractions of explained variance',
                    'Only known simulated grains; composition/ODF are report priors, no independent material accuracy'])
    if output_dir.exists():
        raise ValueError('Preserve existing diagnostics; use a new output directory')
    output_dir.mkdir(parents=True)
    write_csv(output_dir/'component_curve_data.csv', rows)
    write_json(output_dir/'summary.json', report)
    print(json.dumps(dict(ensembles=stats, seed_differences=pairs), indent=2))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, action='append', required=True)
    parser.add_argument('--grade', default='B30P105')
    parser.add_argument('--direction', choices=['RD', 'TD'], action='append')
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    analyze(args.run_dir, args.grade, args.direction or ['RD', 'TD'], args.output_dir)
