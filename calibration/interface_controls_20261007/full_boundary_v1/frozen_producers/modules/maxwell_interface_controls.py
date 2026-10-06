"""Full Dirichlet coupon controls; no motor or measured-accuracy promotion."""
from copy import deepcopy
import math
import numpy as np
from modules.maxwell_directional_controls import GATES
from modules.maxwell_nonlinear_controls import payload as nonlinear_payload,verify_definition as verify_nonlinear
from modules.motor_model_audit import one,value,blocks

VERSION='full_boundary_interface_controls_v1'
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


def make_protocol(source,record):
    origin=np.asarray(record['origin_m'])-np.array([.01,.01,0.]);cases=[]
    plan=[('simple_global_origin','simple','global',False),('simple_global_shift','simple','global',True),
          ('simple_object_shift','simple','object',True),('simple_relative_shift','simple','relative',True),
          ('scalar_RD_full_boundary','scalar_rd','global',False),('tensor_RD_full_boundary','tensor','global',False)]
    for name,kind,cs,shift in plan:
        p=origin.tolist() if shift else [0.,0.,0.]
        contract=deepcopy(source);contract['material_name']='Interface_'+name
        target=MU0*1000*20 if kind=='simple' else source['curves']['RD']['B'][source['curves']['RD']['H'].index(20.)]
        B=[target,0.,0.];hypotheses={'specified_frame':expected_H(np.eye(3),B)}
        if cs=='relative':hypotheses={'specified_frame':expected_H(cs_frame(record,'absolute_point'),B)}
        elif cs=='object':hypotheses={s:expected_H(cs_frame(record,s),B) for s in ('absolute_point','direction_vector','direction_vector_x_primary_legacy')}
        if kind!='simple':hypotheses={'source_RD_knot':[20.,0.,0.]}
        expression=f'{target:.17g}tesla*(Y - ({p[1]:.17g}meter))'
        cases.append(dict(case_id=name,material_kind=kind,material=contract,CS_kind=cs,
            object_CS=deepcopy(record) if cs!='global' else None,mu_r=[1000.,100.,1000.],
            origin_m=p,side_m=.02,target_B_vector_T=B,expected_H_hypotheses=hypotheses,
            boundary_expression=expression,boundary_CS='Global',boundary_kind='all_edges_vector_potential',
            sample_points_m=[[p[0]+x,p[1]+y,0.] for x in (.005,.01,.015) for y in (.005,.01,.015)],
            setup=dict(MaximumPasses=6,MinimumPasses=2,MinimumConvergedPasses=1,
                PercentError=.1,RelativeResidual=1e-8,NonLinearResidual=1e-6,SmoothBHCurve=False),
            maximum_mesh_length_m=.002,maximum_mesh_elements=1000,gates=GATES.copy()))
    return dict(protocol=VERSION,cases=cases,maximum_solve_attempts=6,case_timeout_seconds=120,
        cores=2,GPUs=0,retry_allowed=False,motor_solves=0,
        nonlinear_requires_passed=['simple_global_origin','simple_global_shift','simple_relative_shift'],
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
        if float(value(mu,'component'+str(i)))!=expected:raise ValueError('Analytic tensor changed')
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
