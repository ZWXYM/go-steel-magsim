"""Full Dirichlet coupon controls; no motor or measured-accuracy promotion."""
from copy import deepcopy
import math
import numpy as np
from modules.maxwell_directional_controls import GATES
from modules.maxwell_nonlinear_controls import payload as nonlinear_payload,verify_definition as verify_nonlinear
from modules.motor_model_audit import one,value,blocks

VERSION='full_boundary_interface_controls_v7'
MU0=4e-7*math.pi


def cs_frame(record,semantics):
    x=np.array(record['x_axis_absolute_m'],dtype=float);y=np.array(record['y_axis_absolute_m'],dtype=float)
    if semantics=='absolute_point':x-=record['origin_m'];y-=record['origin_m']
    elif semantics not in ('direction_vector','direction_vector_x_primary_legacy'):raise ValueError('Unknown axis semantics')
    primary='x' if semantics=='direction_vector_x_primary_legacy' or record['settings']['DrivenByXAxis']=='true' else 'y'
    if primary=='x':
        if np.linalg.norm(x)<1e-12:raise ValueError('Degenerate primary axis')
        x/=np.linalg.norm(x);y-=x*np.dot(x,y)
        if np.linalg.norm(y)<1e-12:raise ValueError('Degenerate secondary axis')
        y/=np.linalg.norm(y)
    else:
        if np.linalg.norm(y)<1e-12:raise ValueError('Degenerate primary axis')
        y/=np.linalg.norm(y);x-=y*np.dot(y,x)
        if np.linalg.norm(x)<1e-12:raise ValueError('Degenerate secondary axis')
        x/=np.linalg.norm(x)
    return np.column_stack([x,y,np.cross(x,y)])


def expected_H(rotation,B,mu=(1000.,100.,1000.)):
    rotation=np.asarray(rotation,dtype=float);B=np.asarray(B,dtype=float)
    if rotation.shape!=(3,3) or not np.allclose(rotation.T@rotation,np.eye(3),atol=1e-10):raise ValueError('Invalid frame')
    return (rotation@np.diag(1/(MU0*np.array(mu)))@rotation.T@B).tolist()


def make_protocol(source,record,mode='meter_geometry'):
    if mode not in ('meter_geometry','millimeter_geometry','oblique_millimeter','axis_vectors_millimeter','explicit_iterations_millimeter','high_mu_millimeter'):raise ValueError('Unknown geometry-unit control')
    origin=np.asarray(record['origin_m'])-np.array([.01,.01,0.]);cases=[]
    plan=[('simple_global_origin','simple','global',False),('simple_global_shift','simple','global',True),
          ('simple_object_shift','simple','object',True),('simple_relative_shift','simple','relative',True),
          ('scalar_RD_full_boundary','scalar_rd','global',False),('tensor_RD_full_boundary','tensor','global',False)]
    if mode=='oblique_millimeter':
        plan=[('oblique_object_Bx','simple','object',True),('oblique_relative_Bx','simple','relative',True),
              ('oblique_object_By','simple','object',True),('oblique_object_isotropic_Bx','simple','object',True)]
    if mode=='axis_vectors_millimeter':
        plan=[('normalized_object_Bx','simple','object',True),('normalized_object_By','simple','object',True),
              ('normalized_object_Bxy','simple','object',True),('normalized_relative_Bxy','simple','relative',True)]
    if mode=='explicit_iterations_millimeter':
        plan=[('scalar_RD_iterations100','scalar_rd','global',False),('tensor_RD_iterations100','tensor','global',False)]
    if mode=='high_mu_millimeter':
        plan=[('source_H20_secant_linear','simple','global',False),('source_H20_interval_linear','simple','global',False)]
    for name,kind,cs,shift in plan:
        p=origin.tolist() if shift else [0.,0.,0.]
        contract=deepcopy(source);contract['material_name']='Interface_'+name
        target=MU0*1000*20 if kind=='simple' else source['curves']['RD']['B'][source['curves']['RD']['H'].index(20.)]
        B=[0.,target,0.] if name.endswith('_By') else [target,0.,0.]
        if name.endswith('_Bxy'):B=[target/math.sqrt(2),target/math.sqrt(2),0.]
        mu=[1000.,1000.,1000.] if 'isotropic' in name else [1000.,100.,1000.]
        if mode=='high_mu_millimeter':
            target=source['curves']['RD']['B'][source['curves']['RD']['H'].index(20.)]
            B=[target,0.,0.];mu=[]
            for direction in ('RD','TD'):
                curve=source['curves'][direction];i=curve['H'].index(20.)
                slope=curve['B'][i]/20. if 'secant' in name else (curve['B'][i]-curve['B'][i-1])/(20.-curve['H'][i-1])
                mu.append(slope/MU0)
            mu.append(1000.)
        hypotheses={'specified_frame':expected_H(np.eye(3),B,mu)}
        if cs=='relative':hypotheses={'specified_frame':expected_H(cs_frame(record,'absolute_point'),B)}
        elif cs=='object':hypotheses={s:expected_H(cs_frame(record,s),B,mu) for s in ('absolute_point','direction_vector','direction_vector_x_primary_legacy')}
        if mode=='oblique_millimeter' and cs=='object':
            hypotheses['global_orientation_ignored']=expected_H(np.eye(3),B,mu)
            if 'isotropic' in name:hypotheses={'isotropic_response':expected_H(np.eye(3),B,mu)}
        coordinate_record=deepcopy(record) if cs!='global' else None
        if mode=='axis_vectors_millimeter':
            R=cs_frame(record,'absolute_point')
            hypotheses={'desired_direction':expected_H(R,B,mu),
                        'global_orientation_ignored':expected_H(np.eye(3),B,mu)}
            if cs=='object':
                # This is a new, explicitly vector-valued diagnostic definition,
                # not an edit or reinterpretation of the old saved motor CS.
                coordinate_record['x_axis_absolute_m']=(.001*R[:,0]).tolist()
                coordinate_record['y_axis_absolute_m']=(.001*R[:,1]).tolist()
                coordinate_record['settings']['DrivenByXAxis']='true'
        if kind!='simple':hypotheses={'source_RD_knot':[20.,0.,0.]}
        expression=f'{B[0]:.17g}tesla*(Y - ({p[1]:.17g}meter)) - {B[1]:.17g}tesla*(X - ({p[0]:.17g}meter))'
        cases.append(dict(case_id=name,material_kind=kind,material=contract,CS_kind=cs,
            model_units='mm' if mode!='meter_geometry' else 'meter',
            object_CS=coordinate_record,mu_r=mu,
            origin_m=p,side_m=.02,target_B_vector_T=B,expected_H_hypotheses=hypotheses,
            boundary_expression=expression,boundary_CS='Global',boundary_kind='all_edges_vector_potential',
            sample_points_m=[[p[0]+x,p[1]+y,0.] for x in (.005,.01,.015) for y in (.005,.01,.015)],
            setup=dict(MaximumPasses=6,MinimumPasses=2,MinimumConvergedPasses=1,
                PercentError=.1,RelativeResidual=1e-8,NonLinearResidual=1e-6,SmoothBHCurve=False),
            maximum_mesh_length_m=.002,maximum_mesh_elements=1000,gates=GATES.copy()))
        if mode=='axis_vectors_millimeter':
            cases[-1]['original_geometry_CS_record']=deepcopy(record)
            cases[-1]['desired_frame_global']=R.tolist()
            cases[-1]['new_axes_are_direction_vectors']=cs=='object'
        if mode=='explicit_iterations_millimeter':
            cases[-1]['setup'].update(UseNonLinearIterNum=True,MinIterNum=1,MaxIterNum=100)
    return dict(protocol=VERSION,mode=mode,cases=cases,maximum_solve_attempts=len(cases),case_timeout_seconds=120,
        cores=2,GPUs=0,retry_allowed=False,motor_solves=0,
        nonlinear_requires_passed=[] if mode in ('oblique_millimeter','axis_vectors_millimeter','explicit_iterations_millimeter','high_mu_millimeter') else ['simple_global_origin','simple_global_shift','simple_relative_shift'],
        scope='Full Dirichlet analytic controls and unchanged-source nonlinear diagnostics; no measured-material, loss or motor ranking claim')


def material_payload(case):
    if case['material_kind']!='simple':return nonlinear_payload(case)
    mu=case['mu_r']
    if not all(math.isfinite(v) and v>0 for v in mu):raise ValueError('Invalid analytic permeability')
    return ['NAME:'+case['material']['material_name'],'CoordinateSystemType:=','Cartesian','BulkOrSurfaceType:=',1,
        ['NAME:PhysicsTypes','set:=',['Electromagnetic']],'permittivity:=','1',
        ['NAME:permeability','property_type:=','AnisoProperty','unit:=','',
            'component1:=',str(mu[0]),'component2:=',str(mu[1]),'component3:=',str(mu[2])],
        'conductivity:=','0','mass_density:=','7650',
        ['NAME:core_loss_type','property_type:=','ChoiceProperty','Choice:=','Electrical Steel'],
        'core_loss_kh:=','0','core_loss_kc:=','0','core_loss_ke:=','0','core_loss_kdc:=','0',
        'core_loss_equiv_cut_depth:=','0.23mm']


def verify_material(text,case):
    if case['material_kind']!='simple':return verify_nonlinear(text,case)
    body=one(text,case['material']['material_name']);mu=one(body,'permeability')
    if value(body,'CoordinateSystemType')!='Cartesian' or value(mu,'property_type')!='AnisoProperty' or blocks(mu,'BHCoordinates'):
        raise ValueError('Simple analytic tensor not saved')
    for i,expected in enumerate(case['mu_r'],1):
        # AEDT rounds scalar literals on save. Bind derived constants to 12
        # relative digits; this does not alter the physical field/energy gates.
        if not math.isclose(float(value(mu,'component'+str(i))),expected,rel_tol=1e-12,abs_tol=0.):raise ValueError('Analytic tensor changed')
    for key in ('conductivity','core_loss_kh','core_loss_kc','core_loss_ke','core_loss_kdc'):
        if float(value(body,key))!=0:raise ValueError('Analytic control scalar changed')
    return dict(simple_tensor_saved=True,mu_r=case['mu_r'],material_accuracy_verified=False,motor_ranking_eligible=False)


def evaluate(case,B,H,convergence):
    B=np.asarray(B);H=np.asarray(H);target=np.array(case['target_B_vector_T'])
    if B.shape!=H.shape or B.shape!=(9,3) or not np.isfinite([B,H]).all():raise ValueError('Incomplete field samples')
    gate=case['gates'];errors={k:float(np.max(np.linalg.norm(H-np.array(v),axis=1))/np.linalg.norm(v)) for k,v in case['expected_H_hypotheses'].items()}
    matches=[k for k,e in errors.items() if e<=gate['equivalent_H_relative']]
    norm=float(np.linalg.norm(H.mean(axis=0)))
    checks=dict(B_vector=bool(np.max(np.linalg.norm(B-target,axis=1))<=gate['B_absolute_error_T']),
        H_uniform=bool(norm>0 and np.linalg.norm(np.ptp(H,axis=0))<=gate['H_uniformity_relative']*norm),
        unique_H_hypothesis=len(matches)==1,adaptive_converged=convergence['adaptive_converged'])
    return dict(case_id=case['case_id'],mean_B_vector_T=B.mean(axis=0).tolist(),mean_H_vector_A_per_m=H.mean(axis=0).tolist(),
        hypothesis_relative_errors=errors,matched_H_hypothesis=matches[0] if len(matches)==1 else None,
        checks=checks,passed=all(checks.values()),native_convergence=convergence,
        calibrated_material_field_verified=False,loss_calibration_verified=False,motor_ranking_eligible=False)


def review_energy(case, B, H, convergence):
    """Independent linear-energy cross-check; never rewrite the producer result.

    Nine interior samples establish field uniformity; this comparison is valid
    only for the homogeneous, square, linear coupon with unit depth. A nonlinear
    material requires an integral of its constitutive law, not half B dot H.
    """
    if case['material_kind'] != 'simple':
        return dict(applicable=False, reason='Nonlinear energy needs constitutive integration',
                    interface_response_verified=False)
    B = np.asarray(B, dtype=float); H = np.asarray(H, dtype=float)
    if B.shape != (9, 3) or H.shape != B.shape or not np.isfinite([B, H]).all():
        raise ValueError('Incomplete linear-energy samples')
    energy = convergence['passes'][-1]['energy_J']
    if not math.isfinite(energy) or energy <= 0:
        raise ValueError('Missing positive native energy')
    # Convergence reports round total energy to five or six significant digits.
    # Fix this diagnostic tolerance independently of any observed discrepancy.
    tolerance = 1e-4
    volume = case['side_m'] ** 2  # Maxwell 2D XY: unit-depth coupon.
    projected = .5 * float(np.mean(np.sum(B * H, axis=1))) * volume
    hypotheses = {name: .5 * float(np.dot(case['target_B_vector_T'], value)) * volume
                  for name, value in case['expected_H_hypotheses'].items()}
    errors = {name: abs(value - energy) / energy for name, value in hypotheses.items()}
    matches = [name for name, error in errors.items() if error <= tolerance]
    raw = evaluate(case, B, H, convergence)
    agrees = abs(projected - energy) / energy <= tolerance
    same = len(matches) == 1 and matches[0] == raw['matched_H_hypothesis']
    return dict(applicable=True, relative_tolerance=tolerance, native_total_energy_J=energy,
                sampled_half_B_dot_H_energy_J=projected,
                field_energy_relative_error=abs(projected-energy)/energy,
                expected_energy_J=hypotheses, energy_hypothesis_relative_errors=errors,
                matched_energy_hypothesis=matches[0] if len(matches)==1 else None,
                field_energy_consistent=agrees, H_and_energy_hypotheses_agree=same,
                interface_response_verified=bool(raw['passed'] and agrees and same),
                calibrated_material_field_verified=False, motor_ranking_eligible=False)
