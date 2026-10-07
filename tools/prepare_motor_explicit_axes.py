"""Prepare a new motor copy with explicit axis candidates; never solve it."""
import argparse
import json
import os
import shutil
import subprocess
import sys
import traceback
from pathlib import Path
PROJECT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(PROJECT))
from modules.motor_explicit_axes import candidates, same_vertices, verify_saved
from modules.maxwell_material_transport import digest
from modules.motor_model_audit import audit_project,one,blocks,value
from modules.motor_workbench import write_json,license_config
from modules.motor_queue import process_identity,identity_status
from modules.maxwell_native_readiness import require_configured_license_connection

TEMPLATE='calibration/diagnostics/motor_review_preflight_20261005/jobs/scan_456bd621b9f8/case_02/motor.aedt'
GEOMETRY='calibration/diagnostics/nonlinear_field_20261007/ready_v2/private_native/geometry/vertices.json'
RECORDS='calibration/diagnostics/nonlinear_field_20261007/ready_v2/saved_geometry_input.json'
PRODUCERS=('modules/maxwell_native_readiness.py','modules/motor_explicit_axes.py','modules/maxwell_air_interface.py',
    'modules/maxwell_interface_controls.py','modules/maxwell_nonlinear_controls.py',
    'modules/maxwell_directional_controls.py','modules/maxwell_material_transport.py',
    'modules/maxwell_native_convergence.py','modules/motor_model_audit.py',
    'modules/motor_workbench.py','modules/motor_queue.py','tools/prepare_motor_explicit_axes.py')


def prepare(root,study):
    if study.exists() or not study.is_relative_to(root):raise ValueError('New workspace study required')
    plan=candidates(json.loads((root/RECORDS).read_text()),json.loads((root/GEOMETRY).read_text()))
    before=audit_project(root/TEMPLATE)
    if not before['core_loss']['complete_insert_coverage'] or before['moving_insert_count']!=48:
        raise ValueError('Need the loss-complete isolated template')
    source={p:digest(root/p) for p in (TEMPLATE,GEOMETRY,RECORDS)}
    source.update({p.relative_to(root).as_posix():digest(p) for p in (root/'magsim/data/workbench/motor').glob('scan_*/state.json')})
    study.mkdir(parents=True);(study/'private_native').mkdir()
    shutil.copy2(root/TEMPLATE,study/'private_native/axes.aedt')
    protocol=dict(protocol='48_explicit_motor_axis_candidates_preflight_v1',candidates=plan,
        before_model=before,source_sha256=source,producer_sha256={p:digest(PROJECT/p) for p in PRODUCERS},
        maximum_native_sessions=1,wall_timeout_seconds=240,new_motor_solves=0,new_field_solves=0,
        retry_allowed=False,measured_rolling_direction=False,
        scope='New explicit candidates bound to actual geometry; no 48-insert physical response certification')
    write_json(study/'protocol.json',protocol);write_json(study/'state.json',dict(status='prepared_not_executed'))
    for name in PRODUCERS:
        dest=study/'frozen_producers'/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(PROJECT/name,dest)


def frozen(root,study):
    protocol=json.loads((study/'protocol.json').read_text())
    for p,h in protocol['producer_sha256'].items():
        if digest(PROJECT/p)!=h or digest(study/'frozen_producers'/p)!=h:raise ValueError('Frozen producer changed')
    for p,h in protocol['source_sha256'].items():
        if digest(root/p)!=h:raise ValueError('Original source changed')
    return protocol


def create_candidate(app,obj,item):
    record=item['object_CS'];p=record['origin_m']
    args=['NAME:ObjectCSParameters',['NAME:Origin','IsAttachedToEntity:=',False,'EntityID:=',-1,
        'FacetedBodyTriangleIndex:=',-1,'TriangleVertexIndex:=',-1,'PositionType:=','AbsolutePosition',
        'UParam:=',0,'VParam:=',0,'XPosition:=',f'{p[0]}meter','YPosition:=',f'{p[1]}meter','ZPosition:=',f'{p[2]}meter'],
        'MoveToEnd:=',True,'ReverseXAxis:=',False,'ReverseYAxis:=',False,'DrivenByXAxis:=',True]
    for label,key in (('xAxis','x_axis_absolute_m'),('yAxis','y_axis_absolute_m')):
        v=record[key];args.append(['NAME:'+label,'DirectionType:=','AbsoluteDirection','EdgeID:=',-1,'FaceID:=',-1,
            'xDirection:=',f'{v[0]}meter','yDirection:=',f'{v[1]}meter','zDirection:=',f'{v[2]}meter','UParam:=',0,'VParam:=',0])
    app.modeler.oeditor.CreateObjectCS(args,['NAME:Attributes','Name:=',item['new_CS'],'PartName:=',obj.name])
    obj.part_coordinate_system=item['new_CS']
    if obj.part_coordinate_system!=item['new_CS']:raise ValueError('Candidate not assigned')


def native_worker(root,study):
    import psutil
    require_configured_license_connection()
    protocol=frozen(root,study);project=study/'private_native/axes.aedt'
    if digest(project)!=protocol['source_sha256'][TEMPLATE]:raise ValueError('No retry of changed preparation copy')
    config,_=license_config()
    if config:os.environ.setdefault('ANSYSLMD_LICENSE_FILE',config)
    os.environ.setdefault('ANSWAIT','0')
    from ansys.aedt.core import Desktop,Maxwell2d
    from ansys.aedt.core.generic.settings import settings
    settings.release_on_exception=False
    existing={p.pid for p in psutil.process_iter(['name']) if (p.info['name'] or '').lower()=='ansysedt.exe'}
    desktop=None;owned=False
    try:
        desktop=Desktop(version='2025.1',non_graphical=True,new_desktop=True,close_on_exit=False)
        pid=desktop.aedt_process_id
        if pid in existing:raise ValueError('Dedicated session required')
        owned=True;write_json(study/'desktop_session.json',dict(identity=process_identity(pid),dedicated_session=True))
        app=Maxwell2d(project=str(project),design='Motor-CAD 2',version='2025.1',non_graphical=True,
            new_desktop=False,aedt_process_id=pid,close_on_exit=False,remove_lock=False)
        app.modeler.set_working_coordinate_system('Global')
        if app.modeler.model_units!='mm' or app.modeler.get_working_coordinate_system()!='Global':
            raise ValueError('Native geometry unit/frame differs')
        actual=[]
        for item in protocol['candidates']:
            obj=app.modeler[item['object']];points=[[float(v)*.001 for v in vertex.position] for vertex in obj.vertices]
            same_vertices(points,item['actual_vertices_m']);actual.append(dict(object=obj.name,vertices_m=points))
        write_json(study/'native_vertices_before.json',actual)
        for item in protocol['candidates']:create_candidate(app,app.modeler[item['object']],item)
        app.save_project();text=project.read_text(encoding='utf-8-sig')
        review=verify_saved(text,protocol['candidates'],protocol['before_model'])
        for item in protocol['candidates']:
            obj=app.modeler[item['object']]
            same_vertices([[float(v)*.001 for v in vertex.position] for vertex in obj.vertices],item['actual_vertices_m'])
        valid=app.validate_simple(str(study/'private_native/validation.log'))
        if valid!=1:raise ValueError('Native preparation preflight failed')
        review.update(status='saved_candidate_preflight_passed_not_solved',native_validation=valid,
            actual_vertices_unchanged=True,saved_project_sha256=digest(project),new_motor_solves=0,new_field_solves=0)
        write_json(study/'native_summary.json',review)
    except Exception as exc:
        write_json(study/'failure.json',dict(error=str(exc),traceback=traceback.format_exc(),new_motor_solves=0));raise
    finally:
        if desktop is not None and owned:desktop.release_desktop(close_projects=True,close_on_exit=True)


def execute(root,study):
    protocol=frozen(root,study)
    if json.loads((study/'state.json').read_text())['status']!='prepared_not_executed':raise ValueError('No retry')
    readiness=require_configured_license_connection()
    write_json(study/'dispatch_readiness.json',readiness)
    write_json(study/'state.json',dict(status='running_native_preparation'))
    command=[str(root/'.runtime/aedt/Scripts/python.exe'),'-X','utf8',str(Path(__file__).resolve()),
        '--root',str(root),'--study-dir',str(study),'--native-worker']
    with (study/'private_native/worker.log').open('x',encoding='utf-8') as stream:
        process=subprocess.Popen(command,cwd=study,stdout=stream,stderr=subprocess.STDOUT)
        write_json(study/'dispatch.json',dict(identity=process_identity(process.pid),command=command))
        try:rc=process.wait(timeout=protocol['wall_timeout_seconds'])
        except subprocess.TimeoutExpired:
            import psutil
            p=study/'desktop_session.json'
            if p.exists():
                session=json.loads(p.read_text())
                if session['dedicated_session'] and identity_status(session['identity'])=='alive':psutil.Process(session['identity']['pid']).terminate()
            if process.poll() is None:process.terminate()
            write_json(study/'state.json',dict(status='timeout_no_retry',new_motor_solves=0,new_field_solves=0))
            return 1
    write_json(study/'state.json',dict(status='preflight_passed_not_solved' if rc==0 else 'failed_no_retry',
        new_motor_solves=0,new_field_solves=0,exit_code=rc));return rc


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True);p.add_argument('--study-dir',type=Path,required=True)
    g=p.add_mutually_exclusive_group(required=True);g.add_argument('--prepare',action='store_true');g.add_argument('--execute',action='store_true');g.add_argument('--native-worker',action='store_true')
    a=p.parse_args();root=a.root.resolve();study=a.study_dir.resolve()
    if a.prepare:prepare(root,study)
    elif a.native_worker:native_worker(root,study)
    else:raise SystemExit(execute(root,study))
