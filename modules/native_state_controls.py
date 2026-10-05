"""Audit frozen state-phase controls without treating them as material curves."""
from pathlib import Path

import numpy as np

from modules.material_calibration import MU0, file_hash, guard_B
from modules.native_loop_diagnostics import MSAT, VOLUME

VERSION = 'native_state_controls_v1'


def audit_control(path, H_grid, *, angle_deg, condition):
    path = Path(path)
    grid = np.asarray(H_grid, dtype=float)
    if (not np.isfinite(angle_deg) or grid.ndim != 1 or len(grid) < 3
            or not np.all(np.isfinite(grid)) or grid[0] != 0 or np.any(np.diff(grid) <= 0)):
        raise ValueError('Invalid physical H grid or field direction')
    header = path.read_text(encoding='utf-8').splitlines()[0].split('\t')
    data = np.loadtxt(path, skiprows=1, ndmin=2)
    if data.shape[1] != len(header) or not np.all(np.isfinite(data)):
        raise ValueError('Malformed/non-finite native table')

    def col(name):
        ids = [i for i, text in enumerate(header) if text.strip().startswith(name + ' ')]
        if not ids:
            raise ValueError('Missing column ' + name)
        if any(not np.array_equal(data[:, ids[0]], data[:, i]) for i in ids[1:]):
            raise ValueError('Conflicting duplicate column ' + name)
        return data[:, ids[0]]

    direction = np.array([np.cos(np.deg2rad(angle_deg)), np.sin(np.deg2rad(angle_deg)), 0.])
    ext = np.column_stack([col('B_ext' + axis) for axis in 'xyz'])
    magnetization = np.column_stack([col('m' + axis) for axis in 'xyz'])
    applied = ext @ direction
    if np.max(np.linalg.norm(ext - applied[:, None] * direction, axis=1)) > 1e-9:
        raise ValueError('Applied field differs from declared direction')
    h = applied / MU0
    projection = magnetization @ direction
    b = MU0 * (h + MSAT * projection)
    phase, torque = col('state_phase'), col('maxTorque')
    if np.any(torque < 0):
        raise ValueError('Negative residual torque')
    energy = col('E_total') / VOLUME
    if condition == 'strict_major_loop':
        signed = np.r_[-grid[:0:-1], grid]
        schedules = ((-1., signed[::-1]), (1., signed))
    elif condition == 'zero_transverse_pair':
        schedules = ((-2., grid), (2., grid))
    else:
        raise ValueError('Unknown state control')
    expected_phase = np.concatenate([np.full(len(hs), label) for label, hs in schedules])
    expected_h = np.concatenate([hs for _, hs in schedules])
    if (not np.array_equal(phase, expected_phase) or h.shape != expected_h.shape
            or not np.allclose(h, expected_h, atol=.01, rtol=0)):
        raise ValueError('Incomplete/different state-phase physical H schedule')
    curves, states = [], []
    for label, _ in schedules:
        mask = phase == label
        order = np.argsort(h[mask], kind='stable')
        hs, bs = h[mask][order], b[mask][order]
        curves.append(np.interp(grid, hs, bs))
        states.append(dict(phase=label, native_H0_T=float(np.interp(0, hs, bs)),
            native_B800_T=float(np.interp(800, hs, bs)),
            max_residual_torque_T=float(torque[mask].max()),
            max_energy_density_J_per_m3=float(energy[mask].max())))
    result = dict(diagnostic_version=VERSION, condition=condition, angle_deg=angle_deg,
        H_axis='physical_A_per_m', H=grid.tolist(), table_sha256=file_hash(path), rows=len(h),
        state_curves_B_T=[curve.tolist() for curve in curves], states=states,
        max_residual_torque_T=float(torque.max()), torque_gate_passed=bool(torque.max() <= 1e-5),
        max_abs_mz=float(np.abs(magnetization[:, 2]).max()),
        max_state_separation_T=float(np.max(np.abs(curves[0] - curves[1]))),
        state_separation_B800_T=float(abs(np.interp(800, grid, curves[0] - curves[1]))),
        controls_are_not_material_curves=True, strict_training_eligible=False,
        promotion_allowed=False, no_measured_Hc_Br_or_loss=True)
    if condition == 'strict_major_loop':
        desc, asc = phase == -1, phase == 1
        opposite = np.interp(-h[desc], h[asc], b[asc])
        endpoints = [float(np.sign(h[mask][i]) * projection[mask][i])
            for mask in (desc, asc) for i in (0, -1)]
        midpoint = (curves[0] + curves[1]) / 2
        proxy = midpoint.copy()
        proxy[0] = 0  # Same diagnostic convention as old extractor; native H0 retained above.
        guarded, guard = guard_B(grid, proxy)
        result.update(native_unforced_midpoint_H0_T=float(midpoint[0]),
            B800_before_guard_T=float(np.interp(800, grid, proxy)),
            B800_after_guard_T=float(np.interp(800, grid, guarded)),
            native_midpoint_before_guard_T=proxy.tolist(), guarded_midpoint_B_T=guarded.tolist(),
            guard_max_change_T=guard['max_guard_change_T'], signed_endpoint_m_projections=endpoints,
            min_signed_endpoint_m_projection=min(endpoints),
            max_branch_inversion_error_T=float(np.max(np.abs(b[desc] + opposite))))
        result['endpoint_gate_passed'] = min(endpoints) >= .95
        result['inversion_gate_passed'] = result['max_branch_inversion_error_T'] <= .02
        result['numerical_screen_passed'] = bool(result['endpoint_gate_passed']
            and result['inversion_gate_passed'] and result['torque_gate_passed'])
    else:
        result['initializations_are_certified_demagnetization'] = False
    return result
