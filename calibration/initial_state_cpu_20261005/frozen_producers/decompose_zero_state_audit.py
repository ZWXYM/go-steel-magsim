"""Reproduce the posthoc H0 identity decomposition; never apply a correction."""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np


def decompose(report_path):
    report_path = Path(report_path)
    report = json.loads(report_path.read_text(encoding='utf-8'))
    rows = []
    for s in report['summaries']:
        grains = [d for d in report['grain_details'] if d['grade'] == s['grade'] and d['direction'] == s['direction']]
        if len(grains) != s['grains'] or len(grains) < 2:
            raise ValueError('Incomplete native material/direction group')
        zero = np.array([d['native_unforced_midpoint_H0_T'] for d in grains])
        b800 = np.array([d['B800_before_guard_T'] for d in grains])
        if not np.all(np.isfinite(zero)) or not np.all(np.isfinite(b800)):
            raise ValueError('Non-finite native state values')
        rows.append(dict(grade=s['grade'], direction=s['direction'],
            high_unforced_H0_grain_ids=[d['grain_id'] for d in grains if abs(d['native_unforced_midpoint_H0_T']) > .02],
            mean_unforced_H0_T=float(zero.mean()), mean_native_B800_T=float(b800.mean()),
            mean_B800_minus_unforced_H0_diagnostic_T=float((b800-zero).mean()),
            grain_B800_std_T=float(b800.std(ddof=1)), grain_B800_minus_H0_std_T=float((b800-zero).std(ddof=1)),
            endpoint_failures=s['endpoint_screen_failures'], inversion_failures=s['inversion_screen_failures'],
            torque_recorded=all(d['max_residual_torque_T'] is not None for d in grains),
            no_offset_subtraction_applied=True, no_grain_removed=True))
    return dict(role='posthoc_native_H0_decomposition_diagnostic_not_a_correction',
        input_report_sha256=hashlib.sha256(report_path.read_bytes()).hexdigest(), high_H0_screen_T=.02,
        H0_screen_reason='same 0.02 T numerical scale used by existing inversion screening; no accuracy certification',
        rows=rows, no_reference_targets_used=True, model_changed=False, new_native_jobs=0)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('Use a new output file; previous evidence is never overwritten')
    result = decompose(args.report)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(dict(groups=len(result['rows']), applied_correction=False, GPU_used=False)))
