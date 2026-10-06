"""Uniform-flux native interface controls; not material accuracy validation."""
from copy import deepcopy
from pathlib import Path
import numpy as np

VERSION = 'uniform_flux_directional_controls_v2'
CASES = (
    ('td_global_y', 'original', 0, 'y', 'TD'),
    ('rd_rotated_y', 'original', 90, 'y', 'RD'),
    ('rd_swapped_y', 'swapped', 0, 'y', 'RD'),
    ('rd_isotropic_y', 'isotropic_rd', 0, 'y', 'RD'),
    ('rd_isotropic_rotated_y', 'isotropic_rd', 90, 'y', 'RD'),
    ('rd_global_x', 'original', 0, 'x', 'RD'),
)
GATES = dict(B_absolute_error_T=1e-4, B_transverse_T=1e-4,
             H_transverse_relative=1e-3, H_uniformity_relative=1e-3,
             equivalent_H_relative=1e-2, knot_H_relative=1e-2,
             TD_RD_minimum_ratio=5.)


def transformed_contract(source, variant):
    contract = deepcopy(source)
    contract['material_name'] = 'Control_' + variant
    curves = contract['curves']
    if variant == 'swapped':
        curves['RD'], curves['TD'] = curves['TD'], curves['RD']
    elif variant == 'isotropic_rd':
        curves['TD'] = deepcopy(curves['RD'])
    elif variant != 'original':
        raise ValueError('Unknown diagnostic transform')
    contract['diagnostic_transform'] = variant
    contract['scope'] = 'derived interface control, not a measured material'
    return contract


def make_protocol(source, linear_sanity=False):
    if linear_sanity:
        source = deepcopy(source)
        for direction, mu in (('RD',1000.),('TD',100.)):
            source['curves'][direction]['B'] = [float(4e-7*np.pi*mu*h) for h in source['curves'][direction]['H']]
    # A source knot avoids claiming a precise inverse of AEDT interpolation.
    index = source['curves']['RD']['H'].index(20.)
    target = source['curves']['RD']['B'][index]
    cases = []
    selected = CASES[:2] if linear_sanity else CASES
    for name, variant, angle, field_axis, direction in selected:
        curve = source['curves'][direction]
        b, h = np.asarray(curve['B']), np.asarray(curve['H'])
        if np.any(np.diff(b) <= 0):
            raise ValueError('Control needs strictly invertible source B knots')
        upper = int(np.searchsorted(b, target, side='left'))
        if upper == 0 or upper >= len(b):
            raise ValueError('Control flux outside source support')
        bracket = [float(h[upper-1]), float(h[upper])]
        knot = float(h[upper]) if b[upper] == target else None
        cases.append(dict(case_id=name, transform=variant, CS_angle_deg=angle,
            global_B_axis=field_axis, expected_source_direction=direction,
            expected_H_bracket_A_per_m=bracket, expected_H_knot_A_per_m=knot,
            linear_inverse_diagnostic_A_per_m=float(np.interp(target, b, h)),
            material=transformed_contract(source, variant)))
    return dict(protocol=VERSION, cases=cases, gates=GATES.copy(),
        mode='analytic_linear_sanity' if linear_sanity else 'calibrated_B23R075',
        analytical_mu_r={'RD':1000.,'TD':100.} if linear_sanity else None,
        target_B_T=float(target), square_side_m=.02,
        sample_points_m=[[x,y,0.] for x in (.005,.01,.015) for y in (.005,.01,.015)],
        setup=dict(MaximumPasses=6, MinimumPasses=2, MinimumConvergedPasses=1,
            PercentError=.1, RelativeResidual=1e-8, NonLinearResidual=1e-6,
            SmoothBHCurve=False), maximum_mesh_length_m=.002,
        cores=2, GPUs=0, case_timeout_seconds=100, retry_allowed=False,
        native_sessions=len(cases), maximum_field_solves=len(cases), motor_solves=0,
        scope='Axis-aligned uniform-flux interface test only; no motor, loss, interpolation accuracy or sample holdout claim')


def read_vector_field(path, points):
    """Accept only finite 3-coordinate/3-vector rows, preserving every sample."""
    rows = []
    for line in Path(path).read_text(encoding='utf-8-sig').splitlines():
        parts = line.split()
        try:
            row = [float(p) for p in parts]
        except ValueError:
            continue
        if not row:
            continue
        if len(row) != 6 or not np.isfinite(row).all():
            raise ValueError('Invalid field row')
        rows.append(row)
    data = np.asarray(rows)
    if data.shape != (len(points), 6) or not np.allclose(data[:,:3], points, rtol=0, atol=1e-10):
        raise ValueError('Field samples differ from frozen SI points')
    return data[:,3:]


def evaluate_case(protocol, case, B, H):
    B, H = np.asarray(B), np.asarray(H)
    expected_shape = (len(protocol['sample_points_m']), 3)
    if B.shape != expected_shape or H.shape != expected_shape or not np.isfinite([B,H]).all():
        raise ValueError('Incomplete or nonfinite field samples')
    axis = {'x':0, 'y':1}[case['global_B_axis']]
    other = [i for i in range(3) if i != axis]
    hb = float(np.mean(H[:,axis]))
    gate = protocol['gates']
    lo, hi = case['expected_H_bracket_A_per_m']
    checks = dict(B_target=bool(np.max(np.abs(B[:,axis]-protocol['target_B_T'])) <= gate['B_absolute_error_T']),
        B_direction=bool(np.max(np.abs(B[:,other])) <= gate['B_transverse_T']),
        H_direction=bool(hb>0 and np.max(np.abs(H[:,other])) <= gate['H_transverse_relative']*hb),
        H_uniform=bool(hb>0 and np.ptp(H[:,axis]) <= gate['H_uniformity_relative']*hb),
        H_source_bracket=bool(lo*(1-gate['knot_H_relative']) <= hb <= hi*(1+gate['knot_H_relative'])))
    knot = case['expected_H_knot_A_per_m']
    if knot is not None:
        checks['H_source_knot'] = bool(abs(hb-knot) <= gate['knot_H_relative']*knot)
    return dict(case_id=case['case_id'], mean_B_vector_T=B.mean(axis=0).tolist(),
        mean_H_vector_A_per_m=H.mean(axis=0).tolist(), H_parallel_A_per_m=hb,
        checks=checks, passed=all(checks.values()))


def evaluate_suite(protocol, results):
    if [r['case_id'] for r in results] != [c['case_id'] for c in protocol['cases']]:
        raise ValueError('Missing, reordered or repeated control cases')
    h = {r['case_id']:r['H_parallel_A_per_m'] for r in results}
    base = h['rd_rotated_y']
    equivalence = {key:bool(base>0 and abs(value-base)/base <= protocol['gates']['equivalent_H_relative'])
        for key,value in h.items() if key != 'td_global_y'}
    ratio = h['td_global_y']/base if base>0 else None
    converged = all(r.get('native_convergence',{}).get('adaptive_converged') is True for r in results)
    passed = all(r['passed'] for r in results) and converged and all(equivalence.values()) and ratio is not None and ratio >= protocol['gates']['TD_RD_minimum_ratio']
    return dict(protocol=protocol['protocol'], mode=protocol.get('mode','calibrated_B23R075'),cases=results, equivalent_RD_controls=equivalence,
        TD_RD_H_ratio=ratio, axis_aligned_field_verified=bool(passed),
        native_convergence_verified=converged,
        calibrated_material_field_verified=bool(passed and protocol.get('mode')=='calibrated_B23R075'),
        motor_geometry_direction_verified=False, oblique_field_verified=False,
        loss_calibration_verified=False, motor_ranking_eligible=False,
        passed=bool(passed))
