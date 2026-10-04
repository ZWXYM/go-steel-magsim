"""Plot all predeclared frozen sampling ensembles and nested prefixes.

Source data remain complete. H=0 is excluded only from the logarithmic display,
with this count/rule recorded in figure_metadata.json. No grain or seed is
omitted; simulated proxies are explicitly labelled and no CI is fabricated.
"""
import argparse
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from modules.material_calibration import file_hash
from tools.run_calibration_pilot import H_GRID, write_csv, write_json

matplotlib.rcParams.update({'font.family': 'sans-serif',
    'font.sans-serif': ['Arial', 'Helvetica', 'DejaVu Sans'], 'font.size': 7,
    'axes.titlesize': 8, 'axes.labelsize': 7, 'xtick.labelsize': 7, 'ytick.labelsize': 7,
    'legend.fontsize': 7, 'svg.fonttype': 'none', 'pdf.fonttype': 42,
    'axes.spines.top': False, 'axes.spines.right': False,
    'axes.linewidth': .8, 'legend.frameon': False, 'savefig.facecolor': 'white'})


def plot(analysis_dir, output_dir):
    summary_path = analysis_dir / 'screening_summary.json'
    summary = json.loads(summary_path.read_text(encoding='utf-8'))
    seeds = sorted({row['manifest_seed'] for row in summary['per_seed_direction']})
    if len(seeds) != 2:
        raise ValueError('Figure requires both predeclared seeds')
    output_dir.mkdir(parents=True, exist_ok=True)
    # 183 mm journal width; the top row carries the full-curve argument.
    fig, axes = plt.subplots(2, 2, figsize=(183/25.4, 125/25.4),
                             gridspec_kw={'height_ratios': [1.25, 1]})
    colors, styles, markers = ['#315B85', '#A55E32'], ['-', '--'], ['o', 's']
    curve_rows, prefix_rows, sources = [], [], []
    for column, direction in enumerate(('RD', 'TD')):
        ax_curve, ax_prefix = axes[0, column], axes[1, column]
        for index, seed in enumerate(seeds):
            path = analysis_dir / f'seed{seed}_{direction}/summary.json'
            report = json.loads(path.read_text(encoding='utf-8'))
            material_seed = report['source']['material_seed']
            grid = np.loadtxt(path.parent/'grain_curves.csv', delimiter=',', skiprows=1)
            n = report['source']['n_grains']
            if len(grid) != n * len(H_GRID):
                raise ValueError('Source grain curves are incomplete')
            h = grid[:, 1].reshape(n, len(H_GRID))
            if not np.array_equal(h, np.broadcast_to(H_GRID, h.shape)):
                raise ValueError('Source H grids differ')
            mean = grid[:, 2].reshape(n, len(H_GRID)).mean(axis=0)
            positive = H_GRID > 0
            ax_curve.plot(H_GRID[positive], mean[positive], color=colors[index],
                          linestyle=styles[index], linewidth=1.2, label=f'Seed {material_seed}')
            prefix = report['prefix_comparison']
            ax_prefix.plot([r['n_grains'] for r in prefix], [r['B800_raw_mean_T'] for r in prefix],
                           color=colors[index], linestyle=styles[index], marker=markers[index],
                           markersize=3.5, linewidth=1)
            curve_rows.extend({'direction': direction, 'manifest_seed': seed, 'material_seed': material_seed, 'n_grains': n,
                'H_A_per_m': float(h), 'B_guarded_midpoint_mean_T': float(b)} for h, b in zip(H_GRID, mean))
            prefix_rows.extend({'direction': direction, 'manifest_seed': seed, 'material_seed': material_seed, **r} for r in prefix)
            sources.append({'group': f'seed{seed}_{direction}', 'summary_sha256': file_hash(path),
                            'manifest_seed': seed, 'material_seed': material_seed,
                            'grain_curves_sha256': file_hash(path.parent/'grain_curves.csv')})
        ax_curve.set_xscale('log')
        ax_curve.set_xlabel('Physical H (A/m)')
        ax_curve.set_ylabel('Guarded midpoint mean B (T)')
        ax_curve.set_title(f'{direction}: complete N64 ensembles', loc='left')
        ax_curve.legend(loc='upper left', handlelength=2.3)
        ax_curve.set_ylim(bottom=0)
        ax_prefix.set_xlabel('Nested grain count N')
        ax_prefix.set_ylabel('Mean B at H = 800 A/m (T)')
        ax_prefix.set_xticks([4, 8, 16, 32, 64])
        ax_prefix.set_ylim(bottom=0)
        ax_prefix.set_title(f'{direction}: prefix sensitivity', loc='left')
    for label, ax in zip('abcd', axes.flat):
        ax.text(-.14, 1.09, label, transform=ax.transAxes, fontsize=8, fontweight='bold')
        ax.tick_params(direction='out', length=3, width=.6)
    fig.subplots_adjust(left=.1, right=.98, bottom=.15, top=.93, hspace=.65, wspace=.32)
    fig.text(.1, .035, 'B30P105 | numerical midpoint proxies | two seeds | N64 is a finite comparison', fontsize=7)
    base = output_dir/'sampling_diagnostics'
    fig.savefig(base.with_suffix('.svg'))
    fig.savefig(base.with_suffix('.pdf'))
    fig.savefig(base.with_suffix('.png'), dpi=300)
    fig.savefig(base.with_suffix('.tiff'), dpi=600, pil_kwargs={'compression': 'tiff_lzw'})
    plt.close(fig)
    write_csv(output_dir/'curve_source_data.csv', curve_rows)
    write_csv(output_dir/'prefix_source_data.csv', prefix_rows)
    write_json(output_dir/'figure_metadata.json', {'source_summary_sha256': file_hash(summary_path),
        'plot_source_sha256': file_hash(Path(__file__)), 'groups': sources,
        'curve_source_rows': len(curve_rows), 'prefix_source_rows': len(prefix_rows),
        'display_excluded_points': 4, 'display_exclusion_rule': 'One H=0 mean node per ensemble only on logarithmic panels; retained in source CSV',
        'grains_or_seeds_excluded': 0, 'center': 'equal_volume_arithmetic_mean',
        'error_bars': 'none; no experimental confidence intervals', 'width_mm': 183, 'height_mm': 125,
        'status': 'simulation_sampling_diagnostic_not_material_validation'})
    return base


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--analysis-dir', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    print(plot(args.analysis_dir, args.output_dir))
