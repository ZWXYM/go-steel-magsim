"""Run fresh serial linear/air direction controls; preserve all original results."""
import argparse
import json
import os
import shutil
import subprocess
import sys
import traceback
from copy import deepcopy
from pathlib import Path
PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT))
from modules.maxwell_air_interface import make_execution_protocol, classify_edges, evaluate_fields
from modules.maxwell_interface_controls import material_payload, verify_material, cs_frame
from modules.maxwell_material_transport import digest
from modules.maxwell_directional_controls import read_vector_field
from modules.maxwell_native_convergence import read_convergence
from modules.motor_workbench import write_json, license_config
from modules.motor_model_audit import one, value
from modules.motor_queue import process_identity, identity_status
from tools.run_maxwell_interface_controls import object_cs, messages

GEOMETRY='calibration/diagnostics/nonlinear_field_20261007/ready_v2/saved_geometry_input.json'
PRODUCERS=('modules/maxwell_air_interface.py','modules/maxwell_interface_controls.py',
 'modules/maxwell_nonlinear_controls.py','modules/maxwell_directional_controls.py',
 'modules/maxwell_material_transport.py','modules/maxwell_native_convergence.py',
 'modules/motor_model_audit.py','modules/motor_workbench.py','modules/motor_queue.py',
 'tools/run_maxwell_interface_controls.py','tools/run_air_interface_controls.py')


def prepare(root,study):
    if study.exists() or not study.is_relative_to(root):
        raise ValueError('A fresh workspace study is required')
    record=json.loads((root/GEOMETRY).read_text())[1]
    protocol=make_execution_protocol(record)
    previous=json.loads((root/'provenance/quota_checkpoint_20261007.json').read_text())
    before=dict(previous['private_asset_sha256'])
    before.update(json.loads((root/'calibration/diagnostics/interface_controls_20261007/high_mu_linear_v7/source_before.json').read_text()))
    before.update({p.relative_to(root).as_posix():digest(p) for p in (root/'magsim/data/workbench/motor').glob('scan_*/state.json')})
    before.update({p.relative_to(root).as_posix():digest(p) for p in (root/'calibration/diagnostics/air_interface_20261007').rglob('*') if p.is_file()})
    if not all(digest(root/p)==h for p,h in before.items()):
        raise ValueError('Prior source/checkpoint changed')
    protocol.update(source_geometry=GEOMETRY,source_geometry_sha256=digest(root/GEOMETRY),
        source_object=record['object'],producer_sha256={p:digest(PROJECT/p) for p in PRODUCERS},
        cumulative_Maxwell_completed_before=previous['cumulative_Maxwell_completed']+len(list((root/'calibration/diagnostics/air_interface_20261007').glob('*/private_native/*/solve_completed.json'))),
        cumulative_motor_solves_before=4,cumulative_MuMax_success_before=680)
    study.mkdir(parents=True)
    write_json(study/'protocol.json',protocol);write_json(study/'source_CS_record.json',record)
    write_json(study/'source_before.json',before)
    write_json(study/'state.json',dict(status='prepared_not_executed',new_field_solves=0))
    for name in PRODUCERS:
        p=study/'frozen_producers'/name;p.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(PROJECT/name,p)


def frozen(study):
    protocol=json.loads((study/'protocol.json').read_text())
    for p,h in protocol['producer_sha256'].items():
        if digest(PROJECT/p)!=h or digest(study/'frozen_producers'/p)!=h:
            raise ValueError('Frozen producer changed')
    record=json.loads((study/'source_CS_record.json').read_text())
    if any(protocol[k]!=v for k,v in make_execution_protocol(record).items()):
        raise ValueError('Frozen protocol changed')
    return protocol


def material_case(case,record):
    result=dict(material_kind='simple',material=dict(material_name=case['material_name']),
        mu_r=[1000.,100.,1000.],object_CS=deepcopy(record))
    result['object_CS']['x_axis_absolute_m']=case['explicit_x_direction_m']
    result['object_CS']['y_axis_absolute_m']=case['explicit_y_direction_m']
    result['object_CS']['settings']['DrivenByXAxis']='true'
    return result


def native_worker(study,case_id):
    import psutil
    protocol=frozen(study);case=next(c for c in protocol['cases'] if c['case_id']==case_id)
    record=json.loads((study/'source_CS_record.json').read_text())
    material=material_case(case,record)
    folder=study/'private_native'/case_id;project=folder/'control.aedt'
    if project.exists():raise ValueError('No retry of a native project')
    config,_=license_config()
    if config:os.environ.setdefault('ANSYSLMD_LICENSE_FILE',config)
    os.environ.setdefault('ANSWAIT','0')
    from ansys.aedt.core import Desktop, Maxwell2d
    from ansys.aedt.core.generic.settings import settings
    settings.release_on_exception=False
    existing={p.pid for p in psutil.process_iter(['name']) if (p.info['name'] or '').lower()=='ansysedt.exe'}
    desktop=None;app=None;owned=False
    try:
        desktop=Desktop(version='2025.1',non_graphical=True,new_desktop=True,close_on_exit=False)
        pid=desktop.aedt_process_id
        if pid in existing:raise ValueError('Dedicated session required')
        owned=True;write_json(folder/'desktop_session.json',dict(identity=process_identity(pid),dedicated_session=True))
        app=Maxwell2d(project=str(project),design='MaterialAir',solution_type='Magnetostatic',version='2025.1',
            non_graphical=True,new_desktop=False,aedt_process_id=pid,close_on_exit=False)
        app.modeler.model_units='mm'
        app.modeler.set_working_coordinate_system('Global')
        if app.modeler.get_working_coordinate_system()!='Global':raise ValueError('Global geometry frame required')
        app.materials.odefinition_manager.AddMaterial(material_payload(material))
        # Create both halves before any CS operation so geometry stays Global.
        objects=[]
        for key,name in (('material_rectangle_m','MaterialHalf'),('air_rectangle_m','AirHalf')):
            origin,size=protocol[key]
            obj=app.modeler.create_rectangle([x*1000 for x in origin],[x*1000 for x in size],name=name,material='vacuum')
            if not obj:raise ValueError('Half coupon creation failed')
            obj.solve_inside=True;objects.append(obj)
        coupon,air=objects;coupon.material_name=case['material_name']
        if case['CS_kind']=='object':object_cs(app,coupon,material)
        elif case['CS_kind']=='relative':
            R=cs_frame(record,'absolute_point')
            cs=app.modeler.create_coordinate_system(origin=[x*1000 for x in record['origin_m']],name='ProbeCS',
                mode='axis',x_pointing=R[:,0].tolist(),y_pointing=R[:,1].tolist())
            if not cs:raise ValueError('Relative coordinate system creation failed')
            coupon.part_coordinate_system=cs.name
        if air.part_coordinate_system!='Global':raise ValueError('Air orientation changed')
        native_edges=[dict(id=e.id,object=obj.name,vertices_mm=[v.position for v in e.vertices]) for obj in objects for e in obj.edges]
        topology=classify_edges(native_edges)
        app.oboundary.AssignVectorPotential(['NAME:A_manufactured','Edges:=',topology['exterior_edge_ids'],
            'Value:=',protocol['boundary_expression'],'CoordinateSystem:=','Global'])
        if not app.mesh.assign_length_mesh([obj.name for obj in objects],maximum_length='.002meter',
            maximum_elements=1000,name='HalfMesh'):raise ValueError('Mesh budget assignment failed')
        setup=app.create_setup('ControlSetup');setup.props.update(protocol['setup'])
        if not setup.update():raise ValueError('Setup update failed')
        app.save_project();text=project.read_text(encoding='utf-8-sig');verify_material(text,material)
        if value(one(text,'Maxwell2DModel'),'ModelDepth')!='1meter':
            raise ValueError('Unit model depth was not saved')
        if protocol['boundary_expression'] not in text:raise ValueError('Boundary expression not saved')
        # The parser/mesh solver must accept the functional expression; saving
        # or a native AddMaterial return value alone is never treated as success.
        valid=app.validate_simple(str(folder/'validation.log'))
        write_json(folder/'preflight.json',dict(validation=valid,native_edges=native_edges,topology=topology,
            boundary_expression=protocol['boundary_expression'],boundary_coordinate_system='Global',
            native_material_orientation=coupon.part_coordinate_system,native_air_orientation=air.part_coordinate_system,
            saved_model_depth='1meter',
            material_mu_r=material['mu_r'],setup_properties=dict(setup.props),
            boundary_expression_saved=True,internal_boundary_assigned=False))
        if valid!=1:raise ValueError('Native preflight failed')
        write_json(folder/'solve_started.json',dict(case_id=case_id))
        if not app.analyze_setup('ControlSetup',cores=2,gpus=0,use_auto_settings=False):raise ValueError('Native solve failed')
        write_json(folder/'solve_completed.json',dict(case_id=case_id,new_field_solves=1));app.save_project()
        points=case['material_sample_points_m']+case['air_sample_points_m']
        points_file=folder/'points.pts'
        points_file.write_text('Unit=meter\n'+''.join(' '.join(str(x) for x in p)+'\n' for p in points),encoding='utf-8',newline='\n')
        for quantity in ('B','H'):
            if not app.post.export_field_file(quantity,solution='ControlSetup : LastAdaptive',output_file=str(folder/(quantity+'.fld')),
                sample_points_file=str(points_file),reference_coordinate_system='Global',export_in_si_system=True,
                export_field_in_reference=True):raise ValueError('Native field export failed')
        if not app.export_convergence('ControlSetup',output_file=str(folder/'convergence.txt')):
            raise ValueError('Official convergence export failed')
        B=read_vector_field(folder/'B.fld',points);H=read_vector_field(folder/'H.fld',points)
        result=evaluate_fields(case,B[:9],B[9:],read_convergence(folder/'convergence.txt',protocol['adaptive_percent_error']),protocol['planned_gates'])
        result.update(native_validation=valid,saved_project_sha256=digest(project),
            exported_native_H_diagnostic_only=dict(material_mean_A_per_m=H[:9].mean(axis=0).tolist(),air_mean_A_per_m=H[9:].mean(axis=0).tolist()),
            runtime_boundary_expression_and_shared_edge_topology_verified=True)
        write_json(folder/'result.json',result)
    except Exception as exc:
        write_json(folder/'failure.json',dict(error=str(exc),traceback=traceback.format_exc()));raise
    finally:
        if desktop is not None and owned:
            try:
                captured={}
                for scope,args in [('design',('control','MaterialAir',0)),('global',('','',0))]:
                    try:captured[scope]=list(desktop.odesktop.GetMessages(*args))
                    except Exception as exc:captured[scope+'_capture_error']=str(exc)
                write_json(folder/'native_messages.json',captured)
            finally:
                desktop.release_desktop(close_projects=True,close_on_exit=True)


def execute(root,study):
    protocol=frozen(study)
    if json.loads((study/'state.json').read_text())['status']!='prepared_not_executed':raise ValueError('No automatic study retry')
    write_json(study/'state.json',dict(status='running'))
    results=[];failures=[]
    for case in protocol['cases']:
        folder=study/'private_native'/case['case_id'];folder.mkdir(parents=True)
        cmd=[str(root/'.runtime/aedt/Scripts/python.exe'),'-X','utf8',str(Path(__file__).resolve()),
            '--root',str(root),'--study-dir',str(study),'--native-worker',case['case_id']]
        with (folder/'worker.log').open('x',encoding='utf-8') as stream:
            process=subprocess.Popen(cmd,cwd=study,stdout=stream,stderr=subprocess.STDOUT)
            write_json(folder/'dispatch.json',dict(identity=process_identity(process.pid),command=cmd))
            try:rc=process.wait(timeout=protocol['case_timeout_seconds'])
            except subprocess.TimeoutExpired:
                import psutil
                session=folder/'desktop_session.json'
                if session.exists():
                    record=json.loads(session.read_text())
                    if record['dedicated_session'] and identity_status(record['identity'])=='alive':
                        psutil.Process(record['identity']['pid']).terminate()
                if process.poll() is None:process.terminate()
                failures.append(dict(case_id=case['case_id'],error='Frozen timeout; no retry'));break
        if rc:
            p=folder/'failure.json';failure=json.loads(p.read_text()) if p.exists() else dict(error='Native worker failed')
            failures.append(dict(case_id=case['case_id'],error=failure['error']));print(case['case_id'],'native failure',flush=True)
            # Stop common preflight/API failures instead of repeating all cases.
            break
        results.append(json.loads((folder/'result.json').read_text()))
        print(case['case_id'],results[-1]['control_success'],flush=True)
    solved=len(list((study/'private_native').glob('*/solve_completed.json')))
    all_controls=len(results)==3 and all(r['control_success'] for r in results)
    summary=dict(protocol=protocol['protocol'],cases=results,failures=failures,
        skipped_not_dispatched=[c['case_id'] for c in protocol['cases'] if not (study/'private_native'/c['case_id']).exists()],
        new_field_solves=solved,new_motor_solves=0,new_MuMax_solves=0,
        cumulative_Maxwell_completed=protocol['cumulative_Maxwell_completed_before']+solved,
        cumulative_motor_solves=4,cumulative_MuMax_success=680,
        linear_air_independent_B_controls_verified=all_controls,
        full_direction_H_export_interface_verified=False,real_material_field_verified=False,
        all_48_motor_CS_verified=False,loss_calibration_verified=False,motor_ranking_eligible=False)
    write_json(study/'summary.json',summary)
    write_json(study/'state.json',dict(status='finished_not_promoted',new_field_solves=solved))
    return 1 if failures else 0


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,required=True);p.add_argument('--study-dir',type=Path,required=True)
    group=p.add_mutually_exclusive_group(required=True)
    group.add_argument('--prepare',action='store_true');group.add_argument('--execute',action='store_true');group.add_argument('--native-worker')
    a=p.parse_args();root=a.root.resolve();study=a.study_dir.resolve()
    if a.prepare:prepare(root,study)
    elif a.native_worker:native_worker(study,a.native_worker)
    else:raise SystemExit(execute(root,study))
