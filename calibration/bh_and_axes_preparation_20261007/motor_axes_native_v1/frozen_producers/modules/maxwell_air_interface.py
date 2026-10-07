"""Manufactured anisotropic/air interface solution and independent B gates."""
from copy import deepcopy
import math
import re
import numpy as np
from modules.maxwell_interface_controls import cs_frame, MU0
from modules.motor_model_audit import one, value, blocks

VERSION = 'anisotropic_air_manufactured_solution_v1'


def verify_air_material(text):
    """Bind omitted scalars only to AEDT's unmodified system vacuum reference."""
    body=one(text,'vacuum')
    if (value(body,'Library'),value(body,'LibLocation'),value(body,'ModSinceLib')) != ('Materials','SysLibrary','false'):
        raise ValueError('Need the unmodified system vacuum library reference')
    omitted=[]
    for field,expected in (('permeability',1.),('conductivity',0.)):
        if blocks(body,field):raise ValueError('Vacuum contains a non-scalar override')
        scalar=value(body,field)
        if scalar is None:omitted.append(field)
        elif float(scalar)!=expected:raise ValueError('Vacuum scalar override differs')
    return dict(system_vacuum_reference_verified=True, omitted_default_fields=omitted,
        omitted_field_scope='System-library defaults; not explicitly serialized scalar proof',
        expected_library_mu_r=1.,expected_library_conductivity_S_per_m=0.)


def verify_explicit_object_axes(text, expected_frame):
    """Decode new axes including AEDT's normalized secondary vector.

    A complete unitless vector must have unit length. It is never interpreted
    as absolute meter coordinates or used to reinterpret old motor records.
    """
    cs=one(text,'ObjectCSParameters')
    if any(value(cs,k)!=v for k,v in (('DrivenByXAxis','true'),('ReverseXAxis','false'),('ReverseYAxis','false'))):
        raise ValueError('New explicit object-axis settings differ')
    expected=np.asarray(expected_frame,float)
    if expected.shape!=(3,3) or not np.allclose(expected.T@expected,np.eye(3),atol=1e-12,rtol=0):
        raise ValueError('Invalid expected explicit frame')
    scopes={}
    for i,label in enumerate(('xAxis','yAxis')):
        body=one(cs,label)
        if value(body,'DirectionType')!='AbsoluteDirection':raise ValueError('Unsupported new axis definition')
        coordinates=[];dimensional=False;bare_nonzero=False
        for key in ('xDirection','yDirection','zDirection'):
            match=re.fullmatch(r'([0-9.eE+-]+)(meter|mm|m)?',value(body,key) or '')
            if not match:raise ValueError('Need literal axis components')
            number=float(match[1]);unit=match[2]
            if not math.isfinite(number):raise ValueError('Nonfinite axis component')
            dimensional |= unit is not None and number!=0
            bare_nonzero |= unit is None and number!=0
            coordinates.append(number*{'meter':1.,'mm':.001,'m':1.,None:1.}[unit])
        if dimensional and bare_nonzero:raise ValueError('Mixed dimensional and unitless nonzero axis components')
        axis=np.asarray(coordinates);norm=float(np.linalg.norm(axis))
        if norm<=1e-12:raise ValueError('Degenerate saved explicit axis')
        if bare_nonzero and not math.isclose(norm,1.,rel_tol=0,abs_tol=1e-12):
            raise ValueError('Unitless axis must be a normalized native direction')
        if not np.allclose(axis/norm,expected[:,i],atol=1e-12,rtol=0):
            raise ValueError('Saved explicit axis direction differs')
        scopes[label]='native_normalized_unitless_direction' if bare_nonzero else 'explicit_dimensional_direction'
    return dict(native_axis_directions_verified=True,scopes=scopes,legacy_motor_CS_reinterpreted=False)


def manufactured_fields(rotation, Bx=.02513274122871835):
    R = np.asarray(rotation, dtype=float)
    if R.shape != (3, 3) or not np.allclose(R.T@R, np.eye(3), atol=1e-10):
        raise ValueError('Need an orthonormal material frame')
    if not np.allclose(R[2], [0., 0., 1.], atol=1e-10):
        raise ValueError('Only an in-plane Cartesian material frame is supported')
    if not math.isfinite(Bx) or Bx <= 0:
        raise ValueError('Positive, finite source flux required')
    nu = R@np.diag(1/(MU0*np.array([1000., 100., 1000.])))@R.T
    # Across a vertical interface without surface current: Bx is continuous
    # and Hy is continuous. Select Hy=0; material By follows its tensor.
    By = -nu[1, 0]/nu[1, 1]*Bx
    material_B = np.array([Bx, By, 0.])
    material_H = nu@material_B
    air_B = np.array([Bx, 0., 0.]); air_H = air_B/MU0
    area = .01*.02  # Each half coupon, unit depth.
    energy = .5*area*(float(material_B@material_H)+float(air_B@air_H))
    return dict(material_B_T=material_B.tolist(), material_H_analytic_A_per_m=material_H.tolist(),
        air_B_T=air_B.tolist(), air_H_analytic_A_per_m=air_H.tolist(),
        reluctivity_global_per_H=nu.tolist(), expected_total_energy_J=energy,
        normal_B_continuity_verified=True, tangential_H_continuity_verified=bool(abs(material_H[1])<1e-8),
        H_scope='Analytic preparation only; never substitute for exported native H')


def make_protocol(record):
    R = cs_frame(record, 'absolute_point')
    target = manufactured_fields(R)
    Bx, By, _ = target['material_B_T']
    cases = []
    for name, kind, positive in (
        ('explicit_object_axes', 'object', True),
        ('relative_axes', 'relative', True),
        ('ignored_global_axes_negative', 'global', False),
    ):
        frame = R if positive else np.eye(3)
        nu = frame@np.diag(1/(MU0*np.array([1000., 100., 1000.])))@frame.T
        cases.append(dict(case_id=name, CS_kind=kind, should_match_manufactured_solution=positive,
            object_CS_origin_m=deepcopy(record['origin_m']),
            explicit_x_direction_m=(.001*R[:,0]).tolist(), explicit_y_direction_m=(.001*R[:,1]).tolist(),
            desired_frame_global=R.tolist(), actual_planned_frame_global=frame.tolist(),
            expected=deepcopy(target), negative_control_interface_Hy_analytic=float((nu@np.array([Bx,By,0.]))[1]),
            material_sample_points_m=[[x,y,0.] for x in (-.0075,-.005,-.0025) for y in (-.005,0.,.005)],
            air_sample_points_m=[[x,y,0.] for x in (.0025,.005,.0075) for y in (-.005,0.,.005)]))
    return dict(protocol=VERSION, status='prepared_not_executed', cases=cases,
        model_units='mm', material_mu_r=[1000.,100.,1000.], air_mu_r=1.,
        material_rectangle_m=[[-.01,-.01,0.],[.01,.02]], air_rectangle_m=[[0.,-.01,0.],[.01,.02]],
        boundary_expression=f'{Bx:.17g}tesla*Y - {By:.17g}tesla*min(X,0meter)',
        boundary_coordinate_system='Global', boundary_edges='six exterior edges only; exclude shared x=0 interface',
        sample_point_units='meter', export_reference_CS='Global',
        maximum_native_attempts=3, cores=2, GPUs=0, case_timeout_seconds=120,
        maximum_passes=6, minimum_passes=2, adaptive_percent_error=.1,
        maximum_mesh_length_m=.002, maximum_mesh_elements=1000, retries_allowed=False,
        planned_gates=dict(B_vector_absolute_error_T=1e-4, B_uniformity_relative=.001,
                           native_energy_relative_error=1e-4, adaptive_converged_required=True),
        runtime_boundary_expression_and_shared_edge_topology_verified=False,
        native_execution_entrypoint_available=False, new_field_solves=0,
        material_accuracy_verified=False, direction_interface_verified=False, motor_ranking_eligible=False,
        purpose='Use independent B response across an air interface; homogeneous full-boundary B alone cannot identify its tensor')


def make_execution_protocol(record):
    """Keep the original CPU preparation immutable; freeze a new experiment."""
    protocol = make_protocol(record)
    protocol.update(protocol='anisotropic_air_native_controls_v2',
                    native_execution_entrypoint_available=True, model_depth_m=1.)
    protocol['setup'] = dict(MaximumPasses=6, MinimumPasses=2,
        MinimumConvergedPasses=1, PercentError=.1, RelativeResidual=1e-8,
        NonLinearResidual=1e-6, SmoothBHCurve=False)
    for case in protocol['cases']:
        case['material_name'] = 'AirControl_' + case['case_id']
    return protocol


def classify_edges(edges, tolerance_mm=1e-8):
    """Select actual exterior segments, excluding both shared-edge records.

    Inputs are native edge IDs and two native vertex positions in model mm.
    No edge ID or ordering is assumed. This geometry must have six exterior
    segments and one shared segment with one or two native edge identifiers.
    """
    exterior = []; interface = []; seen = set(); segments = []
    for edge in edges:
        ident = int(edge['id']); points = np.asarray(edge['vertices_mm'], float)
        if points.shape != (2, 3) or not np.isfinite(points).all():
            raise ValueError('Incomplete native edge vertices')
        if ident in seen:
            continue
        seen.add(ident)
        if np.max(abs(points[:, 2])) > tolerance_mm:
            raise ValueError('Nonplanar native edge')
        if np.linalg.norm(points[1]-points[0]) <= tolerance_mm:
            raise ValueError('Degenerate native edge')
        if np.max(abs(points[:, 0])) <= tolerance_mm:
            if not np.allclose(sorted(points[:, 1]), [-10., 10.], atol=tolerance_mm, rtol=0):
                raise ValueError('Shared interface geometry differs')
            interface.append(ident)
        elif any(np.max(abs(points[:, axis]-value)) <= tolerance_mm
                 for axis in (0, 1) for value in (-10., 10.)):
            if np.max(abs(points[:, :2])) > 10.+tolerance_mm:
                raise ValueError('Exterior segment outside frozen coupon')
            exterior.append(ident)
            segments.append(tuple(sorted(tuple(round(float(x), 7) for x in p[:2]) for p in points)))
        else:
            raise ValueError('Unexpected internal edge')
    if len(exterior) != 6 or len(interface) not in (1, 2):
        raise ValueError('Need six exterior edges and the unassigned shared interface')
    expected = {tuple(sorted(pair)) for pair in (
        ((-10.,-10.),(-10.,10.)), ((10.,-10.),(10.,10.)),
        ((-10.,-10.),(0.,-10.)), ((0.,-10.),(10.,-10.)),
        ((-10.,10.),(0.,10.)), ((0.,10.),(10.,10.)))}
    if set(segments) != expected:
        raise ValueError('Native exterior coverage differs from frozen two-half geometry')
    return dict(exterior_edge_ids=exterior, excluded_shared_edge_ids=interface)


def evaluate_fields(case, material_B, air_B, convergence, gates):
    """Judge B and official energy independently of exported native H."""
    material_B = np.asarray(material_B, float); air_B = np.asarray(air_B, float)
    if material_B.shape != (9, 3) or air_B.shape != (9, 3) or not np.isfinite([material_B, air_B]).all():
        raise ValueError('Complete finite B samples in both halves are required')
    expected = case['expected']; checks = {}; metrics = {}
    for name, data in (('material', material_B), ('air', air_B)):
        target = np.asarray(expected[name+'_B_T'], float)
        err = float(np.max(np.linalg.norm(data-target, axis=1)))
        variation = float(np.linalg.norm(np.ptp(data, axis=0))/np.linalg.norm(target))
        metrics[name+'_max_B_error_T'] = err
        metrics[name+'_B_uniformity_relative'] = variation
        metrics[name+'_mean_B_T'] = data.mean(axis=0).tolist()
        checks[name+'_B_match'] = err <= gates['B_vector_absolute_error_T']
        checks[name+'_B_uniform'] = variation <= gates['B_uniformity_relative']
    native_energy = float(convergence['passes'][-1]['energy_J'])
    if not math.isfinite(native_energy) or native_energy <= 0:
        raise ValueError('Missing positive official total energy')
    energy_err = abs(native_energy-expected['expected_total_energy_J'])/expected['expected_total_energy_J']
    metrics.update(native_total_energy_J=native_energy,
                   expected_total_energy_J=expected['expected_total_energy_J'],
                   native_energy_relative_error=energy_err)
    checks['native_energy_match'] = energy_err <= gates['native_energy_relative_error']
    checks['adaptive_converged'] = bool(convergence['adaptive_converged'])
    field_match = checks['material_B_match'] and checks['air_B_match']
    # A negative control succeeds only if the solve converges and independently
    # differs from the target B. A missing/failed solve never counts as rejection.
    success = (all(checks.values()) if case['should_match_manufactured_solution']
               else bool(checks['adaptive_converged'] and not field_match))
    return dict(case_id=case['case_id'], should_match_manufactured_solution=case['should_match_manufactured_solution'],
        checks=checks, metrics=metrics, manufactured_B_matched=bool(field_match),
        control_success=bool(success), native_convergence=convergence,
        native_H_used_for_B_or_energy_gate=False,
        real_material_field_verified=False, motor_ranking_eligible=False,
        scope='Constant linear manufactured solution; not real BH curves or all 48 motor coordinate systems')
