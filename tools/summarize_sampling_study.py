"""Screen a predeclared two-seed study; never certify material calibration."""
import argparse
import json
import sys
from pathlib import Path

import numpy as np

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from modules.material_calibration import file_hash
from modules.texture_sampling import SAMPLING_VERSION
from tools.analyze_sampling_convergence import analyze, read_ensemble
from tools.run_calibration_pilot import H_GRID, write_json


def summarize(run_dirs, protocol_path, output_dir):
    protocol = json.loads(protocol_path.read_text(encoding='utf-8-sig'))
    if len(run_dirs) != 2 or protocol['texture_sampling_version'] != SAMPLING_VERSION:
        raise ValueError('This screening protocol requires exactly two Haar-prefix ensembles')
    grade = protocol['grade']
    results, curves, rows = [], {}, []
    # Validate the whole requested cohort before producing derived reports.
    for run_dir, seed in zip(run_dirs, protocol['manifest_seeds']):
        for direction in protocol['directions']:
            values, source = read_ensemble(run_dir, grade, direction)
            if (source['texture_sampling_version'] != SAMPLING_VERSION or source['seed'] != seed
                    or source['n_grains'] != protocol['grains_per_seed_direction']):
                raise ValueError('Completed cohort differs from predeclared protocol')
            curves[seed, direction] = values.mean(axis=0)
    for index, (run_dir, seed) in enumerate(zip(run_dirs, protocol['manifest_seeds'])):
        for direction in protocol['directions']:
            result = analyze(run_dir, grade, direction, output_dir / f'seed{seed}_{direction}',
                             baseline=run_dirs[0] if index else None)
            results.append(result)
            prefix32 = next(r for r in result['prefix_comparison'] if r['n_grains'] == 32)
            final = result['prefix_comparison'][-1]
            limits = protocol['screening_thresholds_T']
            rows.append({'manifest_seed': seed, 'material_seed': result['source']['material_seed'],
                'direction': direction, 'B800_N64_raw_guarded_T': final['B800_raw_mean_T'],
                'goss_fraction_N64_realized': final['goss_fraction_realized'],
                'delta_B800_N32_minus_N64_T': prefix32['delta_B800_vs_largest_N_T'],
                'max_curve_delta_N32_vs_N64_T': prefix32['max_curve_delta_vs_largest_N_T'],
                'B800_prefix_screen_pass': abs(prefix32['delta_B800_vs_largest_N_T']) <= limits['abs_B800_n32_minus_n64_per_seed_direction'],
                'whole_curve_prefix_screen_pass': prefix32['max_curve_delta_vs_largest_N_T'] <= limits['max_curve_n32_minus_n64_per_seed_direction'],
                'guard_diagnostic': result['guard_diagnostic']})
    seeds = protocol['manifest_seeds']
    between = []
    for direction in protocol['directions']:
        delta = curves[seeds[1], direction] - curves[seeds[0], direction]
        b800 = float(np.interp(800, H_GRID, delta))
        between.append({'direction': direction, 'delta_B800_N64_seed2_minus_seed1_T': b800,
                        'max_curve_delta_between_N64_seeds_T': float(np.max(np.abs(delta))),
                        'B800_seed_screen_pass': abs(b800) <= limits['abs_B800_between_n64_seeds_per_direction']})
    passed = all(r['B800_prefix_screen_pass'] and r['whole_curve_prefix_screen_pass'] for r in rows) and all(r['B800_seed_screen_pass'] for r in between)
    output_dir.mkdir(parents=True, exist_ok=True)
    report = {'status': 'sampling_screen_passed_B30P105_two_seeds_only' if passed else 'sampling_screen_failed_needs_more_grains_seeds',
              'texture_sampling_version': SAMPLING_VERSION, 'grade': grade,
              'predeclared_protocol_sha256': file_hash(protocol_path),
              'summary_source_sha256': file_hash(Path(__file__)),
              'sampler_source_sha256': results[0]['source']['texture_module_sha256'],
              'completed_requested_jobs': 4 * protocol['grains_per_seed_direction'],
              'per_seed_direction': rows, 'between_seeds': between,
              'calibration_bank_updated': False, 'material_convergence_certified': False,
              'limitations': protocol['limitations'] + ['Largest N is a finite baseline; screen thresholds do not certify independent B800 accuracy',
                                                        'Guarded and unguarded midpoint diagnostics remain engineering proxies']}
    write_json(output_dir / 'screening_summary.json', report)
    print(json.dumps(report, indent=2))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, action='append', required=True)
    parser.add_argument('--protocol', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    summarize(args.run_dir, args.protocol, args.output_dir)
