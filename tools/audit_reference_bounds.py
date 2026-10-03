"""Audit frozen reference polarization against an explicitly labelled Msat prior.

This reads existing curves only. It does not repair them, estimate Msat from
held-out curves, change a calibration bank, or authenticate their origin.
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from modules.material_calibration import MU0, file_hash, guard_B


def audit_curve(H, B, msat=1.56e6):
    h, b = np.asarray(H), np.asarray(B)
    polarization = b - MU0 * h
    excess = np.maximum(polarization - MU0 * msat, 0)
    repaired, guard = guard_B(h, b, msat)
    violations = excess > 1e-10
    return {'Msat_A_per_m_prior': msat, 'J_upper_bound_T': float(MU0 * msat),
            'max_reference_J_T': float(polarization.max()),
            'max_J_excess_T': float(excess.max()),
            'points_exceeding_J_bound': int(violations.sum()),
            'first_excess_H_A_per_m': float(h[violations][0]) if violations.any() else None,
            'J_decreasing_intervals': int((np.diff(polarization) < -1e-10).sum()),
            'max_imposed_guard_change_T': guard['max_guard_change_T'],
            'B800_T': float(np.interp(800, h, b)),
            'unchanged_reference': True}


def audit_run(run_dir):
    manifest = json.loads((run_dir / 'manifest.json').read_text(encoding='utf-8'))
    rows = []
    for material in manifest['materials']:
        for direction, ref in material['references'].items():
            path = run_dir / ref['path']
            digest = file_hash(path)
            if digest != ref['sha256']:
                raise ValueError(f'Frozen reference changed: {path}')
            values = np.loadtxt(path, delimiter=',', comments='#')
            rows.append({'grade': material['grade'], 'direction': direction,
                'reference_sha256': digest, 'reference_status': ref['status'],
                **audit_curve(values[:, 0], values[:, 1])})
    return {'status': 'diagnostic_not_parameter_fit',
        'analysis_source_sha256': file_hash(Path(__file__)),
        'manifest_sha256': file_hash(run_dir / 'manifest.json'), 'rows': rows,
        'affected_directions': sum(r['points_exceeding_J_bound'] > 0 for r in rows),
        'limitations': ['Uses fixed existing Msat prior; no held-out Msat fitting',
            'References are previously processed catalog curves, not authenticated batch measurements',
            'Manufacturer saturation induction without test H is not automatically a polarization limit']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run_dir', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = audit_run(args.run_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
