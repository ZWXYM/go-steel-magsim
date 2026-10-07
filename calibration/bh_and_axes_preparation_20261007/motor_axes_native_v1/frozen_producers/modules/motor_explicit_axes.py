"""Isolated 48-insert axis candidates, bound to actual geometry, without solve."""
from copy import deepcopy
import numpy as np
from modules.maxwell_interface_controls import cs_frame
from modules.maxwell_nonlinear_controls import geometry_metrics, length_m
from modules.maxwell_air_interface import verify_explicit_object_axes
from modules.motor_model_audit import one, blocks, value, inspect_model


def same_vertices(actual, expected):
    a=np.asarray(actual,float); b=np.asarray(expected,float)
    if (a.ndim!=2 or a.shape!=b.shape or a.shape[1]!=3
            or not np.isfinite([a,b]).all() or len(a)<4):
        raise ValueError('Incomplete actual insert geometry')
    # Assignment independent of native vertex enumeration. Distinct points
    # must have a one-to-one match, never just equal centroids or extents.
    distances=np.linalg.norm(a[:,None,:]-b[None,:,:],axis=2)
    mapping=np.argmin(distances,axis=1)
    if len(set(mapping.tolist()))!=len(a) or np.max(distances[np.arange(len(a)),mapping])>1e-10:
        raise ValueError('Actual insert geometry changed')


def candidates(records, geometry):
    actual={r['object']:r for r in geometry['inserts']}
    if (len(records)!=48 or len(actual)!=48 or len({r['object'] for r in records})!=48
            or set(actual)!={r['object'] for r in records}
            or geometry['native_model_units']!='mm' or geometry['working_CS']!='Global'):
        raise ValueError('Need 48 uniquely bound Global native geometry records')
    output=[]
    for record in records:
        R=cs_frame(record,'absolute_point'); points=actual[record['object']]['native_vertices_m']
        metrics=geometry_metrics(record,points,'absolute_point')
        # Freeze a candidate based on the previous intended axes. The small
        # angle to actual geometry is evidence, not measured rolling direction.
        if metrics['RD_major_axis_unsigned_angle_deg']>4. or metrics['shape_aspect_SVD']<10.:
            raise ValueError('Candidate is incompatible with actual major axis')
        new=deepcopy(record);new['x_axis_absolute_m']=(.001*R[:,0]).tolist()
        new['y_axis_absolute_m']=(.001*R[:,1]).tolist();new['settings']['DrivenByXAxis']='true'
        output.append(dict(object=record['object'],object_id=record['object_id'],
            new_CS=record['object']+'_ExplicitCandidate',old_CS=record['CS'],
            frame_global=R.tolist(),object_CS=new,actual_vertices_m=points,
            major_axis_binding=metrics,measured_rolling_direction=False))
    return output


def verify_saved(text, plan, before):
    after=inspect_model(text)
    if (after['insert_count']!=48 or after['moving_insert_count']!=48
            or after['core_loss']['insert_enabled_count']!=48
            or after['core_loss']['enabled_object_ids']!=before['core_loss']['enabled_object_ids']
            or after['core_loss']['effect_on_field']!=before['core_loss']['effect_on_field']
            or after['setup']!=before['setup'] or after['operating_point']!=before['operating_point']):
        raise ValueError('Motor operating/loss/motion contract changed')
    coordinate_ops={value(op,'Name'):op for op in blocks(one(text,'CoordinateSystems'),'Operation')
        if value(op,'OperationType')=='CreateObjectCoordinateSystem'}
    original={r['name']:r for r in before['inserts']}; results=[]
    for item in plan:
        part=next(p for p in blocks(text,'GeometryPart') if value(one(p,'Attributes'),'Name')==item['object'])
        attributes=one(part,'Attributes');operation=coordinate_ops[item['new_CS']]
        if (int(value(attributes,'PartCoordinateSystem'))!=int(value(operation,'ID'))
                or int(value(operation,'ParentPartID'))!=item['object_id']
                or value(attributes,'MaterialValue').strip('"')!=original[item['object']]['material']
                or item['old_CS'] not in coordinate_ops):
            raise ValueError('Candidate CS/object/material binding changed')
        holders=[h for h in blocks(part,'ObjectCSHolderOperation')
            if value(h,'ID')==value(operation,'PlaceHolderOperationID')]
        if len(holders)!=1:
            raise ValueError('Missing unique new object-CS holder')
        audit=verify_explicit_object_axes(holders[0],item['frame_global'])
        origin=one(holders[0],'Origin')
        actual=[length_m(value(origin,key)) for key in ('XPosition','YPosition','ZPosition')]
        if not np.allclose(actual,item['object_CS']['origin_m'],atol=1e-12,rtol=0):
            raise ValueError('Candidate origin changed')
        results.append(dict(object=item['object'],new_CS=item['new_CS'],**audit))
    return dict(saved_explicit_candidate_count=len(results),axes=results,
        original_CS_definitions_retained=True,loss_objects_preserved=True,
        geometry_binding_preparation=True,all_48_physical_direction_verified=False,
        real_material_field_verified=False,motor_ranking_eligible=False)
