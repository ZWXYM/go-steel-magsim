"""Screen the predeclared larger-N cohort without promoting calibration."""
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


def screen_values(values, comparison_n, limits):
    values = np.asarray(values)
    if not 2 <= comparison_n < len(values):
        raise ValueError('Smaller completed prefix required')
    delta = values[:comparison_n].mean(axis=0)-values.mean(axis=0)
    b800 = float(np.interp(800, H_GRID, delta))
    whole = float(np.max(np.abs(delta)))
    return dict(delta_B800_prefix_minus_largest_T=b800, max_curve_prefix_delta_T=whole,
                B800_prefix_pass=abs(b800) <= limits['abs_B800_prefix_T'],
                whole_curve_prefix_pass=whole <= limits['max_curve_prefix_T'])


def screen(run_dirs, protocol_path, output):
    protocol = json.loads(protocol_path.read_text(encoding='utf-8-sig'))
    stage = protocol['current_stage']
    seeds, directions = protocol['manifest_seeds'], stage['directions']
    if (len(seeds) < 3 or len(set(seeds)) != len(seeds) or len(run_dirs) != len(seeds)
            or protocol['texture_sampling_version'] != SAMPLING_VERSION):
        raise ValueError('At least three distinct predeclared Haar seeds required')
    values, sources, rows, ranges = {}, [], [], []
    for run, seed in zip(run_dirs, seeds):
        for direction in directions:
            v, source = read_ensemble(run, protocol['grade'], direction)
            if (source['seed'] != seed or source['n_grains'] != stage['n_grains']
                    or source['texture_sampling_version'] != SAMPLING_VERSION):
                raise ValueError('Completed cohort differs from predeclared stage')
            values[seed, direction] = v
            sources.append(source)
    for key in ('texture_distribution_sha256', 'texture_module_sha256', 'mumax_binary_sha256', 'params'):
        if any(s[key] != sources[0][key] for s in sources):
            raise ValueError('Cohort differs in ' + key)
    for (seed, direction), v in values.items():
        rows.append(dict(manifest_seed=seed, direction=direction,
            B800_largest_mean_T=float(np.interp(800, H_GRID, v.mean(axis=0))),
            **screen_values(v, stage['comparison_n'], protocol['thresholds'])))
    for direction in directions:
        curves = np.array([values[s, direction].mean(axis=0) for s in seeds])
        spread = np.ptp(curves, axis=0)
        b800 = float(np.interp(800, H_GRID, spread))
        ranges.append(dict(direction=direction, B800_seed_range_T=b800,
            max_curve_seed_range_T=float(spread.max()),
            B800_seed_range_pass=b800 <= protocol['thresholds']['max_B800_seed_range_T']))
    passed = all(r['B800_prefix_pass'] and r['whole_curve_prefix_pass'] for r in rows) and all(r['B800_seed_range_pass'] for r in ranges)
    if output.exists():
        raise ValueError('Preserve old derived reports; use a new output directory')
    # Write nothing until the entire declared stage has validated successfully.
    for run, seed in zip(run_dirs, seeds):
        for direction in directions:
            analyze(run, protocol['grade'], direction, output/f'seed{seed}_{direction}')
    report = dict(status='stage_sampling_screen_passed_only' if passed else 'stage_sampling_screen_failed',
        grade=protocol['grade'], n_grains=stage['n_grains'], directions=directions,
        material_seeds=[s['material_seed'] for s in sources],
        protocol_sha256=file_hash(protocol_path), analysis_source_sha256=file_hash(Path(__file__)),
        source=sources, ensembles=rows, seed_ranges=ranges,
        imported_evidence_count=sum(s['imported_evidence_count'] for s in sources),
        actual_native_execution_count=sum(s['native_execution_count'] for s in sources),
        reference_accuracy_validated=False, material_convergence_certified=False,
        calibration_bank_updated=False,
        limitations=protocol['limitations']+['Thresholds are engineering sampling screens, not experimental validation',
            'Largest N is a finite comparator; successful stage alone cannot certify all directions/materials'])
    write_json(output/'summary.json', report)
    print(json.dumps(dict(status=report['status'], ensembles=rows, seed_ranges=ranges), indent=2))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, action='append', required=True)
    parser.add_argument('--protocol', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    screen(args.run_dir, args.protocol, args.output_dir)
