"""Manufactured anisotropic/air interface solution; CPU preparation only."""
from copy import deepcopy
import math
import numpy as np
from modules.maxwell_interface_controls import cs_frame, MU0

VERSION = 'anisotropic_air_manufactured_solution_v1'


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
