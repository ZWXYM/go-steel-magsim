"""Direction-aware audit of complete signed native loops; CPU only.

Unforced H=0 values belong to single-grain major loops, not measured Br or a
demagnetized material. No reference curve enters these numerical diagnostics.
"""
from pathlib import Path
import numpy as np

from modules.material_calibration import MU0, extract_loop_midpoint, file_hash

VERSION = 'directional_native_loop_audit_v1'
MSAT = 1.56e6
VOLUME = 16e-9 * 16e-9 * 1e-9


def audit_table(path, H_grid, *, angle_deg):
    if not np.isfinite(angle_deg):
        raise ValueError('Non-finite field direction')
    path = Path(path)
    header = path.read_text(encoding='utf-8').splitlines()[0].split('\t')
    a = np.loadtxt(path, skiprows=1, ndmin=2)
    if not np.all(np.isfinite(a)):
        raise ValueError('Non-finite native table')
    def col(name, optional=False):
        ids = [i for i, v in enumerate(header) if v.strip().startswith(name + ' ')]
        if not ids:
            if optional:
                return None
            raise ValueError('Missing column ' + name)
        for i in ids[1:]:
            if not np.array_equal(a[:, ids[0]], a[:, i]):
                raise ValueError('Conflicting duplicate column ' + name)
        return a[:, ids[0]]
    direction = np.array([np.cos(np.deg2rad(angle_deg)), np.sin(np.deg2rad(angle_deg)), 0.])
    ext = np.column_stack([col('B_ext' + d) for d in 'xyz'])
    magnetization = np.column_stack([col('m' + d) for d in 'xyz'])
    applied = ext @ direction
    if np.max(np.linalg.norm(ext-applied[:, None]*direction, axis=1)) > 1e-9:
        raise ValueError('Applied field differs from the declared direction')
    h = applied / MU0
    m = magnetization @ direction
    b = MU0 * (h + MSAT*m)
    branch = col('branch')
    if set(branch) != {-1., 1.}:
        raise ValueError('Expected both signed major-loop branch labels')
    desc, asc = branch == -1, branch == 1
    if np.any(np.diff(h[desc]) >= 0) or np.any(np.diff(h[asc]) <= 0):
        raise ValueError('Duplicate/reversed branch schedule')
    expected = np.r_[-np.asarray(H_grid)[:0:-1], H_grid]
    if (len(h[desc]) != len(expected) or len(h[asc]) != len(expected)
            or not np.allclose(h[desc], expected[::-1], atol=.01, rtol=0)
            or not np.allclose(h[asc], expected, atol=.01, rtol=0)):
        raise ValueError('Incomplete/different signed physical H schedule')
    # Keep the true native H=0 values here; the guarded proxy has its own H0=0 convention.
    desc0 = float(np.interp(0, h[desc][::-1], b[desc][::-1]))
    asc0 = float(np.interp(0, h[asc], b[asc]))
    opposite = np.interp(-h[desc], h[asc], b[asc])
    endpoints = [float(np.sign(h[mask][i])*m[mask][i]) for mask in (desc, asc) for i in (0, -1)]
    torque = col('maxTorque', optional=True)
    midpoint = extract_loop_midpoint(path, H_grid, angle_deg=angle_deg)
    energy = col('E_total') / VOLUME
    return dict(diagnostic_version=VERSION, angle_deg=angle_deg, table_sha256=file_hash(path),
        rows=len(h), H_axis='physical_A_per_m',
        max_branch_inversion_error_T=float(np.max(np.abs(b[desc]+opposite))),
        min_signed_endpoint_m_projection=min(endpoints), signed_endpoint_m_projections=endpoints,
        max_residual_torque_T=float(torque.max()) if torque is not None else None,
        torque_status='recorded' if torque is not None else 'not_recorded_unknown',
        native_descending_H0_T=desc0, native_ascending_H0_T=asc0,
        native_unforced_midpoint_H0_T=(desc0+asc0)/2,
        native_branch_separation_H0_T=abs(desc0-asc0),
        branch_separation_B800_T=float(np.interp(800, H_grid,
            np.abs(np.array(midpoint['B_descending'])-midpoint['B_ascending']))),
        max_energy_density_J_per_m3=float(energy.max()), max_abs_mz=float(np.abs(magnetization[:, 2]).max()),
        B800_before_guard_T=float(np.interp(800, H_grid, midpoint['B_midpoint_before_guard'])),
        B800_after_guard_T=float(np.interp(800, H_grid, midpoint['B'])),
        guard_max_change_T=midpoint['report']['max_guard_change_T'],
        native_midpoint_before_guard_T=midpoint['B_midpoint_before_guard'],
        guarded_midpoint_B_T=midpoint['B'], native_H0_is_not_measured_Br=True,
        screening_is_not_material_validation=True)


def initial_state_script(original, condition, H_grid):
    """Prepare matched strict-solver state/path controls; never execute them."""
    marker = '// Pre-saturation: Start from saturated state'
    if original.count(marker) != 1:
        raise ValueError('Unsupported source pre-saturation declaration')
    prefix = original.split(marker)[0]
    if 'H_max := 50000.0' not in prefix or 'minimize()' in prefix or 'Hx_dir :=' not in prefix:
        raise ValueError('Unsupported frozen geometry/field prefix')
    if condition not in ('strict_major_loop', 'zero_transverse_pair'):
        raise ValueError('Unknown prepared state protocol')
    body = ('\n// Diagnostic only: not a measured normal curve or training label.\n'
        'MinimizerStop = 1e-7\nMinimizerSamples = 20\n'
        'tableadd(B_ext)\ntableadd(m)\ntableadd(E_total)\ntableadd(maxTorque)\n'
        'state_phase := 0.0\ntableaddvar(state_phase, "state_phase", "")\nH := 0.0\n')
    if condition == 'strict_major_loop':
        body += ('B_ext = vector(mu0*H_max*Hx_dir, mu0*H_max*Hy_dir, mu0*H_max*Hz_dir)\n'
            'm = uniform(Hx_dir, Hy_dir, Hz_dir)\nminimize()\n')
        signed = np.r_[-np.asarray(H_grid)[:0:-1], H_grid]
        schedules = ((-1, signed[::-1]), (1, signed))
    else:
        schedules = ((-2, H_grid), (2, H_grid))
    for phase, schedule in schedules:
        body += f'state_phase = {phase}.0\n'
        if condition == 'zero_transverse_pair':
            sign = 1 if phase > 0 else -1
            body += (f'B_ext = vector(0, 0, 0)\nm = uniform({-sign}.0*Hy_dir, {sign}.0*Hx_dir, 0)\n'
                '// Two opposite transverse initializations; neither is certified demagnetization.\n')
        for h in schedule:
            body += (f'H = {h:.12g}\nB_ext = vector(mu0*H*Hx_dir, mu0*H*Hy_dir, mu0*H*Hz_dir)\n'
                'minimize()\ntablesave()\n')
    return prefix + body
