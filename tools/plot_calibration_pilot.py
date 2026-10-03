"""Plot every material/direction in the frozen four-material pilot.

See calibration/figure_contract.md. Curves show H >= 100 A/m on a log
axis; the source CSV retains the complete grid including H=0. No materials
or directions are excluded. Metrics use discrete, equally weighted H nodes.
"""
import argparse
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

plt.rcParams.update({'font.family': 'sans-serif', 'font.sans-serif': ['Arial', 'DejaVu Sans'],
    'font.size': 7, 'axes.labelsize': 7, 'axes.titlesize': 7, 'legend.fontsize': 7,
    'svg.fonttype': 'none', 'pdf.fonttype': 42, 'legend.frameon': False, 'axes.spines.top': False,
    'axes.spines.right': False, 'axes.linewidth': .7})

METHODS = [('B_reference_T', 'Reference', '#333333', '-'),
           ('B_raw_T', 'Raw loop proxy', '#999999', '--'),
           ('B_fitted_T', 'Same-material fit', '#759EAC', ':'),
           ('B_holdout_T', 'Material holdout', '#C57652', '-.')]
GRADES = ['B23R075', 'B27R090', 'B27R095', 'B30P105']


def save_figure(fig, path):
    fig.savefig(f'{path}.svg')
    fig.savefig(f'{path}.pdf')
    fig.savefig(f'{path}.png', dpi=300)
    plt.close(fig)


def plot(run_dir):
    data = pd.read_csv(run_dir / 'curve_comparison.csv')
    metrics = pd.read_csv(run_dir / 'validation_metrics.csv')
    expected = {(g, d) for g in GRADES for d in ('RD', 'TD')}
    if set(zip(data.grade, data.direction)) != expected:
        raise ValueError('All four materials and both directions are required')
    # Log-axis domain is declared in the figure contract; all source rows remain.
    width_inches = 183 / 25.4
    fig, axes = plt.subplots(2, 4, figsize=(width_inches, 112 / 25.4),
                             sharex=True, sharey=True, layout='constrained')
    for row, direction in enumerate(('RD', 'TD')):
        for col, grade in enumerate(GRADES):
            ax = axes[row, col]
            curve = data[(data.grade == grade) & (data.direction == direction)]
            curve = curve[curve.H_A_per_m >= 100].sort_values('H_A_per_m')
            for column, label, color, style in METHODS:
                ax.plot(curve.H_A_per_m, curve[column], color=color, ls=style,
                        label=label, linewidth=1.1)
            ax.set_xscale('log')
            ax.set_xlim(100, 50000)
            ax.set_ylim(0, 2.35)
            ax.set_xticks([100, 1000, 10000], labels=['100', '1k', '10k'])
            ax.set_title(f'{grade} / {direction}')
            if col == 0:
                ax.set_ylabel('B (T)')
            if row == 1:
                ax.set_xlabel('Physical H (A/m)')
            ax.text(-.02, 1.13, chr(ord('a') + row * 4 + col),
                    transform=ax.transAxes, fontweight='bold', fontsize=8)
    handles, labels = axes[0, 0].get_legend_handles_labels()
    fig.legend(handles, labels, loc='outside lower center', ncol=2)
    fig.suptitle('Four-material calibration pilot: 8 grains/direction; no measured loss model', fontsize=7)
    save_figure(fig, run_dir / 'calibration_curves')

    fig, axes = plt.subplots(1, 2, figsize=(width_inches, 84 / 25.4), layout='constrained')
    for direction, marker, offset in [('RD', 'o', -.1), ('TD', 's', .1)]:
        for method, color, style in [('raw', '#999999', '--'),
                                     ('leave_one_material_out', '#C57652', '-'),
                                     ('reference_only_holdout_baseline', '#759EAC', ':')]:
            subset = metrics[(metrics.direction == direction) & (metrics.method == method)].set_index('grade').loc[GRADES]
            if not np.all(subset.rmse_T > 0):
                raise ValueError('Positive RMSE required for log-axis comparison')
            label = {'raw': 'raw', 'leave_one_material_out': 'delta holdout',
                     'reference_only_holdout_baseline': 'reference only'}[method]
            axes[0].plot(np.arange(4) + offset, subset.rmse_T, marker=marker,
                         color=color, ls=style, label=f'{direction}: {label}')
        held = metrics[(metrics.direction == direction) &
                       (metrics.method == 'leave_one_material_out')].set_index('grade').loc[GRADES]
        axes[1].plot(np.arange(4) + offset, held.B800_error_T, marker=marker,
                     color='#759EAC' if direction == 'RD' else '#C57652', label=direction)
    for ax in axes:
        ax.set_xticks(range(4), labels=GRADES, rotation=25, ha='right')
    axes[0].set_ylabel('Discrete H-node RMSE (T)')
    axes[0].set_yscale('log')
    axes[0].set_title('Raw, material holdout, reference-only baseline')
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc='outside lower center', ncol=3, fontsize=6)
    axes[1].set_title('Material holdout: B800 error')
    axes[1].set_ylabel('Predicted minus reference B800 (T)')
    axes[1].axhline(.05, color='#777777', ls=':', lw=.8)
    axes[1].axhline(-.05, color='#777777', ls=':', lw=.8)
    axes[1].axhline(0, color='#cccccc', lw=.5)
    axes[1].legend()
    for letter, ax in zip('ab', axes):
        ax.text(-.05, 1.12, letter, transform=ax.transAxes, fontweight='bold', fontsize=8)
    save_figure(fig, run_dir / 'calibration_errors')
    (run_dir / 'figure_QA.md').write_text(
        '# Figure QA\n\nPython/matplotlib only. All 4 materials and RD/TD retained. '
        'Curves use H>=100 A/m on log axes; full H=0..50000 source CSV retained. '
        'No p values or confidence intervals. n=8 grains/direction, one seed; '
        'four material holdout folds. RMSE weights the prescribed H nodes equally. '
        'SVG text editable; PDF TrueType; PNG 300 dpi. No data or image retouching. '
        'Preflight warnings: no TIFF/600 dpi because this is an engineering diagnosis, '
        'not a submission bundle; width is explicitly 183/25.4 inches; the H>=100 '
        'filter is the positivity guard for the log axis.\n',
        encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run_dir', type=Path)
    plot(parser.parse_args().run_dir)
