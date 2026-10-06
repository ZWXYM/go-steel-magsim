"""Freeze six small new interpolation probes and read native insert vertices."""
import argparse
import json
import os
import shutil
import subprocess
import sys
import traceback
from pathlib import Path

PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT))
from modules.maxwell_nonlinear_controls import make_protocol,payload,verify_definition,saved_insert_geometry,evaluate,geometry_metrics
from modules.maxwell_material_transport import load_contract,digest
from modules.maxwell_directional_controls import read_vector_field
from modules.maxwell_native_convergence import read_convergence
from modules.motor_workbench import write_json,license_config
from modules.motor_queue import process_identity,identity_status

PRODUCERS=('modules/maxwell_nonlinear_controls.py','modules/maxwell_material_transport.py',
    'modules/maxwell_directional_controls.py','modules/maxwell_native_convergence.py',
    'modules/motor_model_audit.py','modules/motor_workbench.py','modules/motor_queue.py','tools/run_nonlinear_field_probe.py')
SOURCE='calibration/generalization_20261005/final/cal_65e506f6e207/materials/WB_B23R075_65e506f6e207.amat'
GEOMETRY='calibration/diagnostics/material_transport_20261005/ready_v4/private_native/transport.aedt'


def prepare(root,study):
    if study.exists() or not study.is_relative_to(root):raise ValueError('New workspace study required')
    study.mkdir(parents=True)
    inputs=study/'source_materials';inputs.mkdir()
    for p in (root/SOURCE,(root/SOURCE).with_suffix('.metadata.json')):shutil.copy2(p,inputs/p.name)
    records=saved_insert_geometry((root/GEOMETRY).read_text(encoding='utf-8-sig'))
    protocol=make_protocol(load_contract(inputs/Path(SOURCE).name),records[0])
    protocol.update(geometry_source=GEOMETRY,geometry_sha256=digest(root/GEOMETRY),
        producer_sha256={p:digest(PROJECT/p) for p in PRODUCERS},
        baseline_release='provenance/directional_field_release_20261006.json',
        baseline_release_sha256=digest(root/'provenance/directional_field_release_20261006.json'),
        cumulative_Maxwell_completed_before=12,cumulative_motor_solves_before=4,cumulative_MuMax_success_before=680)
    (study/'private_native/geometry').mkdir(parents=True)
    shutil.copy2(root/GEOMETRY,study/'private_native/geometry/read_only_copy.aedt')
    for name in PRODUCERS:
        target=study/'frozen_producers'/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(PROJECT/name,target)
    sources=json.loads((root/'calibration/diagnostics/directional_field_20261006/attempt_v1/source_before.json').read_text())
    sources.update({p.relative_to(root).as_posix():digest(p) for p in (root/'calibration/diagnostics/directional_field_20261006').rglob('*') if p.is_file()})
    assert all(digest(root/p)==h for p,h in sources.items())
    write_json(study/'saved_geometry_input.json',records)
    write_json(study/'source_before.json',sources)
    write_json(study/'protocol.json',protocol)
    write_json(study/'state.json',dict(status='prepared_not_executed',new_field_solves=0,new_motor_solves=0))


def frozen(study):
    protocol=json.loads((study/'protocol.json').read_text())
    for name,expected in protocol['producer_sha256'].items():
        if digest(PROJECT/name)!=expected or digest(study/'frozen_producers'/name)!=expected:raise ValueError('Frozen producer changed')
    records=json.loads((study/'saved_geometry_input.json').read_text())
    source=next((study/'source_materials').glob('*.amat'))
    expected=make_protocol(load_contract(source),records[0])
    if any(protocol[k]!=v for k,v in expected.items()):raise ValueError('Frozen protocol/input changed')
    return protocol


def worker(study,case_id):
    import psutil
    protocol=frozen(study)
    geometry=case_id=='geometry'
    case=None if geometry else next(c for c in protocol['cases'] if c['case_id']==case_id)
    folder=study/'private_native'/case_id
    project=folder/('read_only_copy.aedt' if geometry else 'control.aedt')
    if geometry:
        if digest(project)!=protocol['geometry_sha256'] or project.with_suffix('.aedtresults').exists():raise ValueError('Geometry copy not fresh')
    elif project.exists():raise ValueError('Do not retry probe project')
    os.environ.setdefault('ANSWAIT','0');config,_=license_config()
    if config:os.environ.setdefault('ANSYSLMD_LICENSE_FILE',config)
    from ansys.aedt.core import Desktop,Maxwell2d
    existing={p.pid for p in psutil.process_iter(['name']) if (p.info['name'] or '').lower()=='ansysedt.exe'}
    desktop=None;owned=False
    try:
        desktop=Desktop(version='2025.1',non_graphical=True,new_desktop=True,close_on_exit=False)
        pid=desktop.aedt_process_id
        if pid in existing:raise ValueError('Session not dedicated')
        owned=True;write_json(folder/'desktop_session.json',dict(identity=process_identity(pid),dedicated_session=True))
        app=Maxwell2d(project=str(project),design='Motor-CAD 2' if geometry else 'UniformFlux',
            solution_type=None if geometry else 'Magnetostatic',version='2025.1',non_graphical=True,
            new_desktop=False,aedt_process_id=pid,close_on_exit=False,remove_lock=False)
        if geometry:
            app.modeler.set_working_coordinate_system('Global')
            units=app.modeler.model_units
            factor={'mm':.001,'meter':1.,'m':1.}.get(units)
            if factor is None:raise ValueError('Unknown native geometry units')
            results=[]
            for record in json.loads((study/'saved_geometry_input.json').read_text()):
                obj=app.modeler[record['object']]
                cs=app.modeler.oeditor.GetPropertyValue('Geometry3DAttributeTab',obj.name,'Orientation')
                if cs!=record['CS']:raise ValueError('Native object CS differs')
                points=[[float(c)*factor for c in vertex.position] for vertex in obj.vertices]
                results.append(dict(object=obj.name,native_CS=cs,native_vertices_m=points,
                    hypotheses={kind:geometry_metrics(record,points,kind) for kind in ('absolute_point','direction_vector')}))
            write_json(folder/'vertices.json',dict(native_model_units=units,working_CS='Global',inserts=results,new_field_solves=0,new_motor_solves=0))
            return
        app.modeler.model_units='meter'
        app.materials.odefinition_manager.AddMaterial(payload(case))
        origin=case['origin_m'];side=case['square_side_m']
        obj=app.modeler.create_rectangle(origin,[side,side],name='Coupon',material='vacuum')
        if not obj:raise ValueError('Coupon creation failed')
        obj.material_name=case['material']['material_name'];obj.solve_inside=True
        if 'object_cs' in case:
            cs=case['object_cs'];p=cs['origin_m']
            parameters=['NAME:ObjectCSParameters',['NAME:Origin','IsAttachedToEntity:=',False,'EntityID:=',-1,
                'FacetedBodyTriangleIndex:=',-1,'TriangleVertexIndex:=',-1,'PositionType:=','AbsolutePosition',
                'UParam:=',0,'VParam:=',0,'XPosition:=',str(p[0])+'meter','YPosition:=',str(p[1])+'meter','ZPosition:=',str(p[2])+'meter'],
                'MoveToEnd:=',True,'ReverseXAxis:=',False,'ReverseYAxis:=',False]
            for label,key in (('xAxis','x_axis_absolute_m'),('yAxis','y_axis_absolute_m')):
                v=cs[key];parameters.append(['NAME:'+label,'DirectionType:=','AbsoluteDirection','EdgeID:=',-1,'FaceID:=',-1,
                    'xDirection:=',str(v[0])+'meter','yDirection:=',str(v[1])+'meter','zDirection:=',str(v[2])+'meter','UParam:=',0,'VParam:=',0])
            app.modeler.oeditor.CreateObjectCS(parameters,['NAME:Attributes','Name:=','TranslatedCS','PartName:=','Coupon'])
            obj.part_coordinate_system='TranslatedCS'
        edges={e.id:list(e.midpoint) for e in obj.edges}
        def edge(x=None,y=None):
            ids=[i for i,m in edges.items() if (x is None or abs(m[0]-x)<1e-10) and (y is None or abs(m[1]-y)<1e-10)]
            if len(ids)!=1:raise ValueError('Ambiguous edge')
            return ids[0]
        low,high=edge(y=origin[1]),edge(y=origin[1]+side)
        normal=[edge(x=origin[0]),edge(x=origin[0]+side)]
        if not app.assign_vector_potential([low],0,boundary='A0') or not app.assign_vector_potential([high],case['target_B_T']*side,boundary='A1'):raise ValueError('Boundary assignment failed')
        if not app.assign_symmetry(normal,symmetry_name='NormalFlux',is_odd=False):raise ValueError('Symmetry failed')
        if not app.mesh.assign_length_mesh('Coupon',maximum_length=str(case['maximum_mesh_length_m'])+'meter',maximum_elements=case['maximum_mesh_elements'],name='CouponMesh'):raise ValueError('Mesh budget failed')
        setup=app.create_setup('ControlSetup');setup.props.update(case['setup'])
        if not setup.update():raise ValueError('Setup update failed')
        app.save_project();verify_definition(project.read_text(encoding='utf-8-sig'),case)
        valid=app.validate_simple(str(folder/'validation.log'))
        write_json(folder/'preflight.json',dict(validation=valid,edges_m=edges,setup_properties=dict(setup.props),material_kind=case['material_kind']))
        if valid!=1:raise ValueError('Native model validation failed')
        write_json(folder/'solve_started.json',dict(case_id=case_id))
        if not app.analyze_setup('ControlSetup',cores=2,gpus=0,use_auto_settings=False):raise ValueError('Native solve failed')
        write_json(folder/'solve_completed.json',dict(case_id=case_id,new_field_solves=1))
        app.save_project()
        for quantity in ('B','H'):
            if not app.post.export_field_file(quantity,solution='ControlSetup : LastAdaptive',output_file=str(folder/(quantity+'.fld')),
                sample_points=case['sample_points_m'],reference_coordinate_system='Global',export_in_si_system=True,export_field_in_reference=True):raise ValueError('Missing native field export')
        if not app.export_convergence('ControlSetup',output_file=str(folder/'convergence.txt')):raise ValueError('Missing native convergence')
        B=read_vector_field(folder/'B.fld',case['sample_points_m']);H=read_vector_field(folder/'H.fld',case['sample_points_m'])
        result=evaluate(case,B,H,read_convergence(folder/'convergence.txt',case['setup']['PercentError']))
        result.update(saved_project_sha256=digest(project),native_validation=valid)
        write_json(folder/'result.json',result)
        write_json(folder/'native_messages.json',list(desktop.odesktop.GetMessages(app.project_name,app.design_name,0)))
    except Exception as exc:
        write_json(folder/'failure.json',dict(error=str(exc),traceback=traceback.format_exc()));raise
    finally:
        if desktop is not None and owned:desktop.release_desktop(close_projects=True,close_on_exit=True)


def execute(root,study):
    protocol=frozen(study)
    if json.loads((study/'state.json').read_text())['status']!='prepared_not_executed':raise ValueError('No retry of an existing study')
    results=[];failures=[]
    write_json(study/'state.json',dict(status='native_probes_running',new_motor_solves=0))
    for case_id in ['geometry']+[c['case_id'] for c in protocol['cases']]:
        folder=study/'private_native'/case_id;folder.mkdir(parents=True,exist_ok=True)
        command=[str(root/'.runtime/aedt/Scripts/python.exe'),'-X','utf8',str(Path(__file__).resolve()),'--root',str(root),'--study-dir',str(study),'--native-worker',case_id]
        with (folder/'worker.log').open('x',encoding='utf-8') as stream:
            process=subprocess.Popen(command,cwd=study,stdout=stream,stderr=subprocess.STDOUT)
            write_json(folder/'dispatch.json',dict(identity=process_identity(process.pid),command=command))
            try:rc=process.wait(timeout=protocol['geometry_timeout_seconds'] if case_id=='geometry' else protocol['case_timeout_seconds'])
            except subprocess.TimeoutExpired:
                import psutil
                path=folder/'desktop_session.json'
                if path.exists():
                    record=json.loads(path.read_text())
                    if record['dedicated_session'] and identity_status(record['identity'])=='alive':psutil.Process(record['identity']['pid']).terminate()
                if process.poll() is None:process.terminate()
                rc=-1
        if rc!=0:failures.append(dict(case_id=case_id,exit_code=rc));break
        if case_id!='geometry':
            results.append(json.loads((folder/'result.json').read_text()));print(case_id,results[-1]['passed'],flush=True)
    solved=len(list((study/'private_native').glob('*/solve_completed.json')))
    summary=dict(protocol=protocol['protocol'],cases=results,failures=failures,new_field_solves=solved,new_motor_solves=0,new_MuMax_solves=0,
        cumulative_Maxwell_completed=protocol['cumulative_Maxwell_completed_before']+solved,
        cumulative_motor_solves=protocol['cumulative_motor_solves_before'],cumulative_MuMax_success=protocol['cumulative_MuMax_success_before'],
        calibrated_RD_TD_field_verified=False,loss_calibration_verified=False,motor_ranking_eligible=False,
        native_run_complete=not failures and len(results)==len(protocol['cases']))
    write_json(study/'summary.json',summary)
    write_json(study/'state.json',dict(status='native_probes_completed_not_promoted' if summary['native_run_complete'] else 'native_probes_failed_no_retry',new_field_solves=solved,new_motor_solves=0))
    return 0 if summary['native_run_complete'] else 1


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--root',type=Path,required=True);parser.add_argument('--study-dir',type=Path,required=True)
    group=parser.add_mutually_exclusive_group(required=True);group.add_argument('--prepare',action='store_true');group.add_argument('--execute',action='store_true');group.add_argument('--native-worker')
    args=parser.parse_args();root,study=args.root.resolve(),args.study_dir.resolve()
    if args.prepare:prepare(root,study)
    elif args.native_worker:worker(study,args.native_worker)
    else:raise SystemExit(execute(root,study))
