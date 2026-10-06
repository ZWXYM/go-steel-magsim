"""Frozen interpolation controls and conservative saved insert geometry audit."""
from copy import deepcopy
import math
import re
import numpy as np
from modules.maxwell_directional_controls import GATES, evaluate_case
from modules.maxwell_material_transport import native_arguments, verify_saved_material
from modules.motor_model_audit import blocks, one, value, inspect_model

VERSION='nonlinear_interpolation_and_object_CS_probe_v1'
MU0=4e-7*math.pi


def make_protocol(source, object_cs):
    plan=[('scalar_RD_discrete','scalar_rd',False,.002,20.),
          ('scalar_RD_smooth','scalar_rd',True,.002,20.),
          ('tensor_RD_smooth','tensor',True,.002,20.),
          ('tensor_RD_smooth_fine','tensor',True,.001,20.),
          ('tensor_RD_lower_flux','tensor',False,.002,10.),
          ('translated_object_CS_linear','object_cs_linear',False,.002,20.)]
    cases=[]
    for name,kind,smooth,mesh,source_h in plan:
        contract=deepcopy(source);contract['material_name']='Probe_'+name
        contract['scope']='diagnostic transform only; not a new measured material'
        target=source['curves']['RD']['B'][source['curves']['RD']['H'].index(source_h)]
        if kind=='object_cs_linear':
            for direction,mu in (('RD',1000.),('TD',100.)):
                contract['curves'][direction]['B']=[float(MU0*mu*h) for h in contract['curves'][direction]['H']]
            target=MU0*1000.*20.
        case=dict(case_id=name,material_kind=kind,material=contract,target_B_T=float(target),
            global_B_axis='x',expected_H_knot_A_per_m=source_h,
            expected_H_bracket_A_per_m=[source_h,source_h],CS_angle_deg=0,
            square_side_m=.02,origin_m=[0.,0.,0.],maximum_mesh_length_m=mesh,
            maximum_mesh_elements=4000 if mesh==.001 else 1000,
            setup=dict(MaximumPasses=8,MinimumPasses=2,MinimumConvergedPasses=1,
                PercentError=.1,RelativeResidual=1e-8,NonLinearResidual=1e-6,SmoothBHCurve=smooth),
            gates=GATES.copy(),sample_points_m=[[x,y,0.] for x in (.005,.01,.015) for y in (.005,.01,.015)])
        if kind=='object_cs_linear':
            origin=np.asarray(object_cs['origin_m'])
            case['object_cs']=deepcopy(object_cs)
            case['origin_m']=(origin-np.array([.01,.01,0.])).tolist()
            case['sample_points_m']=[(origin+np.array([x,y,0.])).tolist() for x in (-.005,0.,.005) for y in (-.005,0.,.005)]
            case['expected_H_hypotheses_A_per_m']=object_CS_hypotheses(object_cs,target)
        cases.append(case)
    return dict(protocol=VERSION,cases=cases,case_timeout_seconds=120,geometry_timeout_seconds=100,
        native_sessions=7,maximum_field_solves=6,motor_solves=0,cores=2,GPUs=0,retry_allowed=False,
        gates=GATES.copy(),scope='Paired interpolation diagnostics and object-CS semantics; no measured accuracy, loss or final motor ranking claim')


def payload(case):
    arguments=native_arguments(case['material'])
    if case['material_kind']=='scalar_rd':
        index=next(i for i,a in enumerate(arguments) if isinstance(a,list) and a[0]=='NAME:permeability')
        component=deepcopy(arguments[index][5])
        if component[0]!='NAME:component1':
            raise ValueError('Unexpected native material payload')
        component[0]='NAME:permeability'
        arguments[index]=component
    return arguments


def verify_definition(text,case):
    if case['material_kind']!='scalar_rd':
        return verify_saved_material(text,case['material'])
    body=one(text,case['material']['material_name']);mu=one(body,'permeability')
    if value(mu,'property_type')!='nonlinear' or (value(mu,'BTypeForSingleCurve') or '').casefold()!='normal' or value(mu,'HUnit')!='A_per_meter' or value(mu,'BUnit')!='tesla' or value(mu,'IsTemperatureDependent')!='false':
        raise ValueError('Scalar native permeability definition differs')
    coordinates=one(mu,'BHCoordinates');match=re.search(r'Points\[(\d+):\s*([^\]]+)\]',coordinates)
    if not match:
        raise ValueError('Scalar curve points not saved')
    data=np.array([float(p.strip()) for p in match[2].split(',')])
    expected=case['material']['curves']['RD'];pairs=np.column_stack([expected['H'],expected['B']]).ravel()
    if len(data)!=int(match[1]) or data.shape!=pairs.shape or not np.allclose(data,pairs,rtol=1e-10,atol=2e-7):
        raise ValueError('Scalar saved curve differs from frozen RD')
    for key in ('core_loss_kh','core_loss_kc','core_loss_ke','core_loss_kdc'):
        if float(value(body,key))!=0:
            raise ValueError('Diagnostic losses must stay zero')
    return dict(serialized_transport_verified=True,material_name=case['material']['material_name'],
        scalar_direction='RD only; not an RD/TD constitutive model',curve_points=len(expected['H']),
        directional_field_verified=False,loss_calibration_verified=False,motor_ranking_eligible=False)


def length_m(text):
    match=re.fullmatch(r'([0-9.eE+-]+)(mm|meter|m)',text or '')
    if not match:
        raise ValueError('Need a literal SI/model-unit length, not an assumed expression')
    number=float(match[1])*{'mm':.001,'meter':1.,'m':1.}[match[2]]
    if not math.isfinite(number):
        raise ValueError('Nonfinite geometry coordinate')
    return number


def saved_insert_geometry(text):
    audit=inspect_model(text)
    if not audit['local_CS_verified']:
        raise ValueError('48 saved CS assignments required')
    records=[]
    for item in audit['inserts']:
        part=next(p for p in blocks(text,'GeometryPart') if value(one(p,'Attributes'),'Name')==item['name'])
        poly=[o for o in blocks(part,'Operation') if value(o,'OperationType')=='Polyline']
        operations=[value(o,'OperationType') for o in blocks(part,'Operation')]
        # Duplicated bodies have no independent polyline. Their current vertices
        # must come from AEDT; never infer them from the parent's history.
        points=None
        if poly:
            if len(poly)!=1 or value(poly[0],'ReferenceCoordSystemID')!='1':
                raise ValueError('Unsupported literal polyline reference')
            points=np.array([[length_m(value(p,k)) for k in ('X','Y','Z')] for p in blocks(one(poly[0],'PolylineParameters'),'PLPoint')])
            if len(points)<4 or not np.isfinite(points).all():
                raise ValueError('Incomplete insert polyline')
        elif operations!=['DuplicateBodyAroundAxis']:
            raise ValueError('Unsupported geometry history')
        cs=one(part,'ObjectCSParameters');origin=one(cs,'Origin')
        if value(origin,'PositionType')!='AbsolutePosition' or value(origin,'IsAttachedToEntity')!='false':
            raise ValueError('Unsupported object-CS anchor')
        axes={}
        for axis in ('xAxis','yAxis'):
            body=one(cs,axis)
            if value(body,'DirectionType')!='AbsoluteDirection':
                raise ValueError('Unsupported object-CS direction')
            axes[axis]=[length_m(value(body,k)) for k in ('xDirection','yDirection','zDirection')]
        settings={k:value(cs,k) for k in ('ReverseXAxis','ReverseYAxis','DrivenByXAxis','MoveToEnd')}
        if settings['ReverseXAxis']!='false' or settings['ReverseYAxis']!='false':
            raise ValueError('Unbudgeted CS axis reversal')
        records.append(dict(object=item['name'],object_id=item['object_id'],CS=item['coordinate_system_name'],
            CS_id=item['coordinate_system_id'],origin_m=[length_m(value(origin,k)) for k in ('XPosition','YPosition','ZPosition')],
            x_axis_absolute_m=axes['xAxis'],y_axis_absolute_m=axes['yAxis'],settings=settings,
            serialized_operation_types=operations,
            polyline_points_m=None if points is None else points.tolist(),
            current_global_vertices_verified=False))
    return records


def frame(record,interpretation):
    x=np.array(record['x_axis_absolute_m']);y=np.array(record['y_axis_absolute_m'])
    if interpretation=='absolute_point':
        x-=record['origin_m'];y-=record['origin_m']
    elif interpretation!='direction_vector':
        raise ValueError('Unknown CS hypothesis')
    if np.linalg.norm(x)<1e-12:
        raise ValueError('Degenerate CS x axis')
    x/=np.linalg.norm(x);y-=x*np.dot(x,y)
    if np.linalg.norm(y)<1e-12:
        raise ValueError('Degenerate CS y axis')
    y/=np.linalg.norm(y);z=np.cross(x,y)
    return np.column_stack([x,y,z])


def object_CS_hypotheses(record,target):
    answer={}
    for kind in ('absolute_point','direction_vector'):
        rotation=frame(record,kind)
        nu=rotation@np.diag([1/(MU0*1000),1/(MU0*100),1/(MU0*1000)])@rotation.T
        answer[kind]=(nu@np.array([target,0.,0.])).tolist()
    return answer


def evaluate(case,B,H,convergence):
    result=evaluate_case(case,case,B,H)
    result['native_convergence']=convergence
    result['field_and_source_knot_passed']=result['passed']
    if case['material_kind']=='object_cs_linear':
        H=np.asarray(H);errors={}
        for kind,expected in case['expected_H_hypotheses_A_per_m'].items():
            expected=np.array(expected)
            errors[kind]=float(np.max(np.linalg.norm(H-expected,axis=1))/np.linalg.norm(expected))
        matches=[k for k,e in errors.items() if e<=case['gates']['equivalent_H_relative']]
        result.update(CS_hypothesis_relative_errors=errors,CS_interpretation=matches[0] if len(matches)==1 else None)
        result['passed']=bool(len(matches)==1 and result['checks']['B_target'] and result['checks']['B_direction'] and convergence['adaptive_converged'])
    else:
        result['passed']=result['passed'] and convergence['adaptive_converged']
    result.update(calibrated_RD_TD_field_verified=False,loss_calibration_verified=False,motor_ranking_eligible=False)
    return result


def geometry_metrics(record,points,interpretation):
    points=np.asarray(points,dtype=float)
    if points.ndim!=2 or points.shape[1]!=3 or len(points)<4 or not np.isfinite(points).all():
        raise ValueError('Incomplete native geometry vertices')
    unique=np.unique(points,axis=0);center=unique.mean(axis=0)
    _,s,axes=np.linalg.svd(unique-center,full_matrices=False)
    major=axes[0];axis=frame(record,interpretation)[:,0]
    angle=float(np.degrees(np.arccos(np.clip(abs(np.dot(major,axis)),0.,1.))))
    return dict(object=record['object'],CS=record['CS'],CS_interpretation=interpretation,
        vertex_count=len(points),major_axis_global=major.tolist(),RD_axis_global=axis.tolist(),
        RD_major_axis_unsigned_angle_deg=angle,shape_aspect_SVD=float(s[0]/s[1]) if s[1]>0 else None,
        scope='Principal axis of actual native vertices; does not establish measured rolling direction')
