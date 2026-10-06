"""Prepare fresh full-boundary controls, execute once, and retain native failures."""
import argparse
import json
import os
import shutil
import subprocess
import sys
import traceback
from pathlib import Path
PROJECT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(PROJECT))
from modules.maxwell_interface_controls import make_protocol,material_payload,verify_material,evaluate,cs_frame
from modules.maxwell_material_transport import load_contract,digest
from modules.maxwell_directional_controls import read_vector_field
from modules.maxwell_native_convergence import read_convergence
from modules.motor_workbench import write_json,license_config
from modules.motor_queue import process_identity,identity_status

SOURCE='calibration/generalization_20261005/final/cal_65e506f6e207/materials/WB_B23R075_65e506f6e207.amat'
GEOMETRY='calibration/diagnostics/nonlinear_field_20261007/ready_v2/saved_geometry_input.json'
PRODUCERS=('modules/maxwell_interface_controls.py','modules/maxwell_nonlinear_controls.py',
 'modules/maxwell_directional_controls.py','modules/maxwell_material_transport.py','modules/maxwell_native_convergence.py',
 'modules/motor_model_audit.py','modules/motor_workbench.py','modules/motor_queue.py','tools/run_maxwell_interface_controls.py')


def prepare(root,study):
    if study.exists() or not study.is_relative_to(root):raise ValueError('New workspace study required')
    record=json.loads((root/GEOMETRY).read_text())[0]
    protocol=make_protocol(load_contract(root/SOURCE),record)
    protocol.update(producer_sha256={p:digest(PROJECT/p) for p in PRODUCERS},
        cumulative_Maxwell_completed_before=17,cumulative_motor_solves_before=4,cumulative_MuMax_success_before=680)
    study.mkdir(parents=True);(study/'source_materials').mkdir()
    for p in (root/SOURCE,(root/SOURCE).with_suffix('.metadata.json')):shutil.copy2(p,study/'source_materials'/p.name)
    shutil.copy2(root/GEOMETRY,study/'saved_geometry_input.json')
    for name in PRODUCERS:
        p=study/'frozen_producers'/name;p.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(PROJECT/name,p)
    before=json.loads((root/'calibration/diagnostics/nonlinear_field_20261007/ready_v2/source_before.json').read_text())
    tree=root/'calibration/diagnostics/nonlinear_field_20261007'
    before.update({p.relative_to(root).as_posix():digest(p) for p in tree.rglob('*') if p.is_file()})
    assert all(digest(root/p)==h for p,h in before.items())
    write_json(study/'source_before.json',before);write_json(study/'protocol.json',protocol)
    write_json(study/'state.json',dict(status='prepared_not_executed',new_field_solves=0))


def frozen(study):
    protocol=json.loads((study/'protocol.json').read_text())
    for p,h in protocol['producer_sha256'].items():
        if digest(PROJECT/p)!=h or digest(study/'frozen_producers'/p)!=h:raise ValueError('Frozen producer changed')
    record=json.loads((study/'saved_geometry_input.json').read_text())[0]
    material=load_contract(next((study/'source_materials').glob('*.amat')))
    if any(protocol[k]!=v for k,v in make_protocol(material,record).items()):raise ValueError('Frozen inputs changed')
    return protocol


def messages(desktop,app,folder):
    result={}
    for scope,args in [('design',(app.project_name,app.design_name,0)),('global',('','',0))]:
        try:result[scope]=list(desktop.odesktop.GetMessages(*args))
        except Exception as exc:result[scope+'_capture_error']=str(exc)
    write_json(folder/'native_messages.json',result)


def object_cs(app,obj,case):
    record=case['object_CS'];p=record['origin_m']
    args=['NAME:ObjectCSParameters',['NAME:Origin','IsAttachedToEntity:=',False,'EntityID:=',-1,
      'FacetedBodyTriangleIndex:=',-1,'TriangleVertexIndex:=',-1,'PositionType:=','AbsolutePosition',
      'UParam:=',0,'VParam:=',0,'XPosition:=',f'{p[0]}meter','YPosition:=',f'{p[1]}meter','ZPosition:=',f'{p[2]}meter'],
      'MoveToEnd:=',True,'ReverseXAxis:=',False,'ReverseYAxis:=',False,
      'DrivenByXAxis:=',record['settings']['DrivenByXAxis']=='true']
    for label,key in [('xAxis','x_axis_absolute_m'),('yAxis','y_axis_absolute_m')]:
        v=record[key];args.append(['NAME:'+label,'DirectionType:=','AbsoluteDirection','EdgeID:=',-1,'FaceID:=',-1,
          'xDirection:=',f'{v[0]}meter','yDirection:=',f'{v[1]}meter','zDirection:=',f'{v[2]}meter','UParam:=',0,'VParam:=',0])
    app.modeler.oeditor.CreateObjectCS(args,['NAME:Attributes','Name:=','ProbeCS','PartName:=',obj.name])
    obj.part_coordinate_system='ProbeCS'


def native_worker(study,case_id):
    import psutil
    protocol=frozen(study);case=next(c for c in protocol['cases'] if c['case_id']==case_id)
    folder=study/'private_native'/case_id;project=folder/'control.aedt'
    if project.exists():raise ValueError('No retry of native project')
    config,_=license_config()
    if config:os.environ.setdefault('ANSYSLMD_LICENSE_FILE',config)
    os.environ.setdefault('ANSWAIT','0')
    from ansys.aedt.core import Desktop,Maxwell2d
    existing={p.pid for p in psutil.process_iter(['name']) if (p.info['name'] or '').lower()=='ansysedt.exe'}
    desktop=None;app=None;owned=False
    try:
        desktop=Desktop(version='2025.1',non_graphical=True,new_desktop=True,close_on_exit=False)
        pid=desktop.aedt_process_id
        if pid in existing:raise ValueError('Session not dedicated')
        owned=True;write_json(folder/'desktop_session.json',dict(identity=process_identity(pid),dedicated_session=True))
        app=Maxwell2d(project=str(project),design='UniformFlux',solution_type='Magnetostatic',version='2025.1',
            non_graphical=True,new_desktop=False,aedt_process_id=pid,close_on_exit=False)
        app.modeler.model_units='meter'
        app.materials.odefinition_manager.AddMaterial(material_payload(case))
        obj=app.modeler.create_rectangle(case['origin_m'],[case['side_m']]*2,name='Coupon',material='vacuum')
        if not obj:raise ValueError('Coupon creation failed')
        obj.material_name=case['material']['material_name'];obj.solve_inside=True
        if case['CS_kind']=='object':object_cs(app,obj,case)
        elif case['CS_kind']=='relative':
            R=cs_frame(case['object_CS'],'absolute_point')
            cs=app.modeler.create_coordinate_system(origin=case['object_CS']['origin_m'],name='ProbeCS',mode='axis',
                x_pointing=R[:,0].tolist(),y_pointing=R[:,1].tolist())
            if not cs:raise ValueError('Relative frame failed')
            obj.part_coordinate_system=cs.name
        native_CS={}
        if case['CS_kind']!='global':
            try:
                child=app.modeler.oeditor.GetChildObject('ProbeCS')
                for name in child.GetPropNames():
                    v=child.GetPropValue(name)
                    native_CS[name]=v if isinstance(v,(str,int,float,bool,list,type(None))) else str(v)
            except Exception as exc:native_CS['property_read_error']=str(exc)
        ids=[e.id for e in obj.edges]
        if len(ids)!=4:raise ValueError('Need four actual coupon edges')
        app.oboundary.AssignVectorPotential(['NAME:A_uniform','Edges:=',ids,
            'Value:=',case['boundary_expression'],'CoordinateSystem:=','Global'])
        if not app.mesh.assign_length_mesh('Coupon',maximum_length='.002meter',maximum_elements=1000,name='CouponMesh'):raise ValueError('Mesh budget failed')
        setup=app.create_setup('ControlSetup');setup.props.update(case['setup'])
        if not setup.update():raise ValueError('Setup update failed')
        app.save_project();text=project.read_text(encoding='utf-8-sig');verify_material(text,case)
        if case['boundary_expression'] not in text:raise ValueError('Spatial boundary not saved')
        valid=app.validate_simple(str(folder/'validation.log'))
        write_json(folder/'preflight.json',dict(validation=valid,boundary_edges=ids,boundary_expression=case['boundary_expression'],
            native_CS=native_CS,native_orientation=obj.part_coordinate_system,setup_properties=dict(setup.props)))
        if valid!=1:raise ValueError('Native preflight failed')
        write_json(folder/'solve_started.json',dict(case_id=case_id))
        if not app.analyze_setup('ControlSetup',cores=2,gpus=0,use_auto_settings=False):raise ValueError('Native solve failed')
        write_json(folder/'solve_completed.json',dict(case_id=case_id,new_field_solves=1));app.save_project()
        for quantity in ('B','H'):
            if not app.post.export_field_file(quantity,solution='ControlSetup : LastAdaptive',output_file=str(folder/(quantity+'.fld')),
              sample_points=case['sample_points_m'],reference_coordinate_system='Global',export_in_si_system=True,export_field_in_reference=True):raise ValueError('Field export failed')
        if not app.export_convergence('ControlSetup',output_file=str(folder/'convergence.txt')):raise ValueError('Convergence export failed')
        B=read_vector_field(folder/'B.fld',case['sample_points_m']);H=read_vector_field(folder/'H.fld',case['sample_points_m'])
        result=evaluate(case,B,H,read_convergence(folder/'convergence.txt',case['setup']['PercentError']))
        result.update(saved_project_sha256=digest(project),native_validation=valid);write_json(folder/'result.json',result)
    except Exception as exc:
        write_json(folder/'failure.json',dict(error=str(exc),traceback=traceback.format_exc()));raise
    finally:
        if desktop is not None and app is not None and owned:messages(desktop,app,folder)
        if desktop is not None and owned:desktop.release_desktop(close_projects=True,close_on_exit=True)


def execute(root,study):
    protocol=frozen(study)
    if json.loads((study/'state.json').read_text())['status']!='prepared_not_executed':raise ValueError('No study retry')
    write_json(study/'state.json',dict(status='running'))
    results=[];failures=[];skipped=[]
    for case in protocol['cases']:
        if case['material_kind']!='simple' and not all(any(r['case_id']==key and r['passed'] for r in results) for key in protocol['nonlinear_requires_passed']):
            skipped.append(dict(case_id=case['case_id'],reason='Analytic full-boundary baseline gate failed'));continue
        folder=study/'private_native'/case['case_id'];folder.mkdir(parents=True)
        cmd=[str(root/'.runtime/aedt/Scripts/python.exe'),'-X','utf8',str(Path(__file__).resolve()),'--root',str(root),'--study-dir',str(study),'--native-worker',case['case_id']]
        with (folder/'worker.log').open('x',encoding='utf-8') as stream:
            process=subprocess.Popen(cmd,cwd=study,stdout=stream,stderr=subprocess.STDOUT)
            write_json(folder/'dispatch.json',dict(identity=process_identity(process.pid),command=cmd))
            try:rc=process.wait(timeout=protocol['case_timeout_seconds'])
            except subprocess.TimeoutExpired:
                import psutil
                p=folder/'desktop_session.json'
                if p.exists():
                    record=json.loads(p.read_text())
                    if record['dedicated_session'] and identity_status(record['identity'])=='alive':psutil.Process(record['identity']['pid']).terminate()
                if process.poll() is None:process.terminate()
                failures.append(dict(case_id=case['case_id'],error='Frozen timeout; no retry'));break
        if rc:
            failure=json.loads((folder/'failure.json').read_text()) if (folder/'failure.json').exists() else {'error':'Worker failed'}
            failures.append(dict(case_id=case['case_id'],error=failure['error']))
            print(case['case_id'],'native failure',flush=True)
            if failure['error']!='Native solve failed':break
        else:
            results.append(json.loads((folder/'result.json').read_text()));print(case['case_id'],results[-1]['passed'],flush=True)
    solved=len(list((study/'private_native').glob('*/solve_completed.json')))
    summary=dict(protocol=protocol['protocol'],cases=results,failures=failures,skipped=skipped,new_field_solves=solved,
        new_motor_solves=0,new_MuMax_solves=0,cumulative_Maxwell_completed=17+solved,
        cumulative_motor_solves=4,cumulative_MuMax_success=680,
        calibrated_material_field_verified=False,loss_calibration_verified=False,motor_ranking_eligible=False)
    write_json(study/'summary.json',summary);write_json(study/'state.json',dict(status='finished_not_promoted',new_field_solves=solved))
    return 1 if failures else 0


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--root',type=Path,required=True);parser.add_argument('--study-dir',type=Path,required=True)
    group=parser.add_mutually_exclusive_group(required=True);group.add_argument('--prepare',action='store_true');group.add_argument('--execute',action='store_true');group.add_argument('--native-worker')
    args=parser.parse_args();root=args.root.resolve();study=args.study_dir.resolve()
    if args.prepare:prepare(root,study)
    elif args.native_worker:native_worker(study,args.native_worker)
    else:raise SystemExit(execute(root,study))
