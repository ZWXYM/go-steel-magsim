"""Analytic BH representation diagnostics; never replace reference curves."""
from copy import deepcopy
import math
import re
import numpy as np
from modules.maxwell_interface_controls import GATES, evaluate as field_evaluate
from modules.maxwell_nonlinear_controls import payload
from modules.motor_model_audit import one, value, blocks

MU0 = 4e-7 * math.pi
VERSION = 'constant_mu_working_range_BH_representation_v1'


def diagnostic_curve(curve):
    h = np.asarray(curve['H'], dtype=float)
    b = np.asarray(curve['B'], dtype=float)
    if (h.shape != b.shape or not np.isfinite([h, b]).all()
            or len(h) < 20 or h[0] != 0 or b[0] != 0
            or np.any(np.diff(h) <= 0) or np.any(np.diff(b) <= 0)
            or 20. not in h or 50. not in h or h[-2] <= 50.):
        raise ValueError('Need explicit source knots and a positive normal curve')
    slope = float(b[list(h).index(20.)] / 20.)
    if slope < MU0:
        raise ValueError('Working slope below vacuum')
    # Deliberately artificial knee at H=50; no measured saturation assertion.
    transformed = slope * np.minimum(h, 50.) + MU0 * np.maximum(h-50., 0.)
    return dict(H=h.tolist(), B=transformed.tolist()), slope/MU0


def make_protocol(source, record=None, mode='constant_mu_BH'):
    if mode != 'constant_mu_BH':
        raise ValueError('Unsupported representation study')
    mu = []; material = deepcopy(source)
    for direction in ('RD', 'TD'):
        material['curves'][direction], relative = diagnostic_curve(source['curves'][direction])
        mu.append(relative)
    material['curves']['conductivity_S_per_m'] = 0.
    target = source['curves']['RD']['B'][source['curves']['RD']['H'].index(20.)]
    cases = []
    for kind in ('scalar_rd', 'tensor'):
        contract = deepcopy(material); name = 'constant_mu_BH_' + kind
        contract['material_name'] = 'Diagnostic_' + name
        contract['scope'] = 'analytic transformation, not a measured material'
        cases.append(dict(case_id=name, material_kind=kind, material=contract,
            CS_kind='global', object_CS=None, model_units='mm', origin_m=[0., 0., 0.],
            side_m=.02, target_B_vector_T=[target, 0., 0.],
            expected_H_hypotheses={'analytic_working_segment': [20., 0., 0.]},
            boundary_expression=f'{target:.17g}tesla*Y', boundary_CS='Global',
            sample_points_m=[[x, y, 0.] for x in (.005, .01, .015) for y in (.005, .01, .015)],
            setup=dict(MaximumPasses=6, MinimumPasses=2, MinimumConvergedPasses=1,
                PercentError=.1, RelativeResidual=1e-8, NonLinearResidual=1e-6, SmoothBHCurve=False),
            maximum_mesh_length_m=.002, maximum_mesh_elements=1000, gates=GATES.copy(),
            linear_working_B_limit_T=MU0*mu[0]*50.,
            expected_integrated_energy_J=.5*target*20.*.02**2,
            energy_relative_tolerance=1e-4))
    return dict(protocol=VERSION, mode=mode, cases=cases, cores=2, GPUs=0,
        maximum_solve_attempts=2, case_timeout_seconds=120, retry_allowed=False,
        motor_solves=0, nonlinear_requires_passed=[],
        analytic_transformation={'knee_H_A_per_m':50., 'working_slope': 'source B20/20',
            'tail_slope_T_m_per_A':MU0, 'source_H_knots_retained':True,
            'source_B_points_modified_in_diagnostic_copy_only':True, 'source_files_unchanged':True,
            'conductivity_zero_matches_simple_magnetostatic_baseline':True},
        scope='BH representation at a constant-mu working segment; not real-material accuracy or loss validation')


material_payload = payload


def verify_material(text, case):
    body = one(text, case['material']['material_name']); permeability = one(body, 'permeability')
    if value(body, 'CoordinateSystemType') != 'Cartesian':
        raise ValueError('Changed coordinate convention')
    scalar = case['material_kind'] == 'scalar_rd'
    if not scalar and value(permeability, 'property_type') != 'AnisoProperty':
        raise ValueError('Missing anisotropic normal BH representation')
    for i, direction in enumerate(('RD',) if scalar else ('RD', 'TD'), 1):
        component = permeability if scalar else one(permeability, 'component'+str(i))
        for key, expected in (('property_type','nonlinear'), ('BTypeForSingleCurve','normal'),
                              ('HUnit','a_per_meter'), ('BUnit','tesla'), ('IsTemperatureDependent','false')):
            if (value(component,key) or '').casefold() != expected:
                raise ValueError('Changed normal BH units or representation')
        coordinates = one(component, 'BHCoordinates')
        match = re.search(r'Points\[(\d+):\s*([^\]]+)\]', coordinates)
        if not match:
            raise ValueError('Missing serialized native BH points')
        actual = np.array([float(v) for v in match[2].split(',')])
        curve = case['material']['curves'][direction]
        expected = np.column_stack([curve['H'], curve['B']]).ravel()
        if (int(match[1]) != len(actual) or actual.shape != expected.shape
                or not np.allclose(actual, expected, rtol=1e-12, atol=1e-14)):
            raise ValueError('Analytic BH point transmission changed')
        pairs = actual.reshape(-1,2); slopes = np.diff(pairs[:,1])/np.diff(pairs[:,0])
        if np.min(slopes) < MU0*(1-1e-10) or not math.isclose(slopes[-1],MU0,rel_tol=1e-10):
            raise ValueError('Invalid normal-BH segment or tail slope')
    if not scalar:
        if blocks(permeability,'component3') or float(value(permeability,'component3')) != 1000.:
            raise ValueError('ND prior changed')
    for key in ('conductivity','core_loss_kh','core_loss_kc','core_loss_ke','core_loss_kdc'):
        if float(value(body,key)) != 0.:
            raise ValueError('Diagnostic conductivity/loss changed')
    return dict(analytic_normal_BH_saved=True, physical_H_units=True,
        measured_material=False, motor_ranking_eligible=False)


def evaluate(case, B, H, convergence):
    result = field_evaluate(case, B, H, convergence)
    B = np.asarray(B, dtype=float)
    # Integral of H(B) on the frozen linear operating segment. Refuse the
    # half-B-dot-H shortcut if ANY sampled point leaves that segment.
    inside = bool(np.max(np.linalg.norm(B,axis=1)) < case['linear_working_B_limit_T'])
    energy = convergence['passes'][-1]['energy_J']; expected = case['expected_integrated_energy_J']
    error = abs(energy-expected)/expected
    result['checks'].update(analytic_working_segment=inside,
        integrated_energy=bool(inside and error <= case['energy_relative_tolerance']))
    result.update(passed=all(result['checks'].values()), integrated_energy_J=expected,
        native_energy_relative_error=error, source_curve_accuracy_improved=False,
        actual_nonlinear_source_material_verified=False)
    return result
