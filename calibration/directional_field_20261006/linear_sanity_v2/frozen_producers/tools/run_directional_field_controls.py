"""Freeze six fresh Maxwell2D magnetostatic controls, then solve serially."""
import argparse
import json
import os
import shutil
import subprocess
import sys
import traceback
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from modules.maxwell_material_transport import digest, load_contract, native_arguments, verify_saved_material
from modules.maxwell_directional_controls import make_protocol, read_vector_field, evaluate_case, evaluate_suite
from modules.maxwell_native_convergence import read_convergence
from modules.motor_workbench import write_json, license_config
from modules.motor_queue import process_identity, identity_status

PRODUCERS = ('modules/maxwell_material_transport.py','modules/maxwell_directional_controls.py','modules/maxwell_native_convergence.py',
    'modules/motor_workbench.py','modules/motor_queue.py','tools/run_directional_field_controls.py')


def prepare(root, study, linear_sanity=False):
    if study.exists() or not study.is_relative_to(root):
        raise ValueError('Study must be a new workspace directory')
    study.mkdir(parents=True)
    source = root/'calibration/generalization_20261005/final/cal_65e506f6e207/materials/WB_B23R075_65e506f6e207.amat'
    inputs = study/'source_materials'
    inputs.mkdir()
    for path in (source, source.with_suffix('.metadata.json')):
        shutil.copy2(path, inputs/path.name)
    protocol = make_protocol(load_contract(inputs/source.name),linear_sanity=linear_sanity)
    protocol['prior_successful_field_controls_20261006'] = len(list((root/'calibration/diagnostics/directional_field_20261006').glob('*/private_native/*/solve_completed.json')))
    protocol['producer_sha256'] = {p:digest(PROJECT/p) for p in PRODUCERS}
    for name in PRODUCERS:
        target = study/'frozen_producers'/name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PROJECT/name, target)
    source_hashes = json.loads((root/'calibration/diagnostics/motor_review_20261005/source_before.json').read_text())
    for tree in ('calibration/generalization_20261005/final', 'calibration/diagnostics/material_transport_20261005/ready_v4/public_evidence'):
        source_hashes.update({p.relative_to(root).as_posix():digest(p) for p in (root/tree).rglob('*') if p.is_file()})
    if not all(digest(root/p)==h for p,h in source_hashes.items()):
        raise ValueError('Existing sources changed')
    write_json(study/'source_before.json', source_hashes)
    write_json(study/'protocol.json', protocol)
    write_json(study/'state.json', dict(status='prepared_not_executed', new_field_solves=0, new_motor_solves=0))


def frozen(study):
    protocol = json.loads((study/'protocol.json').read_text())
    for name, sha in protocol['producer_sha256'].items():
        if digest(PROJECT/name) != sha or digest(study/'frozen_producers'/name) != sha:
            raise ValueError('Producer changed since freeze')
    source = next((study/'source_materials').glob('*.amat'))
    old = load_contract(source)
    expected = make_protocol(old,linear_sanity=protocol['mode']=='analytic_linear_sanity')
    if any(protocol[k] != expected[k] for k in expected):
        raise ValueError('Frozen control protocol changed')
    return protocol


def native_case(study, case_id):
    import psutil
    protocol = frozen(study)
    case = next(c for c in protocol['cases'] if c['case_id']==case_id)
    folder = study/'private_native'/case_id
    project = folder/'control.aedt'
    if project.exists():
        raise ValueError('Control project must be new; no retry')
    os.environ.setdefault('ANSWAIT','0')
    config, _ = license_config()
    if config:
        os.environ.setdefault('ANSYSLMD_LICENSE_FILE',config)
    from ansys.aedt.core import Desktop, Maxwell2d
    existing = {p.pid for p in psutil.process_iter(['name']) if (p.info['name'] or '').lower()=='ansysedt.exe'}
    desktop = None
    owned = False
    try:
        desktop = Desktop(version='2025.1',non_graphical=True,new_desktop=True,close_on_exit=False)
        pid = desktop.aedt_process_id
        if pid in existing:
            raise ValueError('Not a dedicated test session')
        owned = True
        write_json(folder/'desktop_session.json',dict(identity=process_identity(pid),dedicated_session=True))
        app = Maxwell2d(project=str(project),design='UniformFlux',solution_type='Magnetostatic',
            version='2025.1',non_graphical=True,new_desktop=False,aedt_process_id=pid,close_on_exit=False)
        app.modeler.model_units = 'meter'
        name = case['material']['material_name']
        app.materials.odefinition_manager.AddMaterial(native_arguments(case['material']))
        # Create with vacuum to avoid any cached material update overwriting the native nonlinear data.
        obj = app.modeler.create_rectangle([0,0,0],[.02,.02],name='Coupon',material='vacuum')
        if not obj:
            raise ValueError('Coupon creation failed')
        obj.material_name = name
        obj.solve_inside = True
        if case['CS_angle_deg']==90:
            cs = app.modeler.create_coordinate_system(origin=[0,0,0],name='RD90',mode='axis',
                x_pointing=[0,1,0],y_pointing=[-1,0,0])
            if not cs:
                raise ValueError('CS creation failed')
            obj.part_coordinate_system = cs.name
        else:
            obj.part_coordinate_system = 'Global'
        # Classify all four edges by their actual global midpoint.
        edges = {e.id:list(e.midpoint) for e in obj.edges}
        def edge(x=None,y=None):
            ids = [i for i,m in edges.items() if (x is None or abs(m[0]-x)<1e-10) and (y is None or abs(m[1]-y)<1e-10)]
            if len(ids)!=1:
                raise ValueError('Ambiguous coupon edge')
            return ids[0]
        flux = protocol['target_B_T']*protocol['square_side_m']
        if case['global_B_axis']=='y':
            low, high, normal, value = edge(x=0),edge(x=.02),[edge(y=0),edge(y=.02)],-flux
        else:
            low, high, normal, value = edge(y=0),edge(y=.02),[edge(x=0),edge(x=.02)],flux
        if not app.assign_vector_potential([low],0,boundary='A0') or not app.assign_vector_potential([high],value,boundary='A1'):
            raise ValueError('Vector potential assignment failed')
        if not app.assign_symmetry(normal,symmetry_name='NormalFlux',is_odd=False):
            raise ValueError('Normal-flux symmetry failed')
        if not app.mesh.assign_length_mesh('Coupon',maximum_length='.002meter',maximum_elements=1000,name='CouponMesh'):
            raise ValueError('Mesh budget failed')
        setup = app.create_setup('ControlSetup')
        if not setup:
            raise ValueError('Setup creation failed')
        setup.props.update(protocol['setup'])
        if not setup.update():
            raise ValueError('Setup update failed')
        app.save_project()
        saved = project.read_text(encoding='utf-8-sig')
        transport = verify_saved_material(saved,case['material'])
        actual_cs = app.modeler.oeditor.GetPropertyValue('Geometry3DAttributeTab','Coupon','Orientation')
        expected_cs = 'RD90' if case['CS_angle_deg']==90 else 'Global'
        if actual_cs != expected_cs:
            raise ValueError('Actual object CS mismatch')
        validation = app.validate_simple(str(folder/'validation.log'))
        write_json(folder/'preflight.json',dict(validation=validation,material=transport,
            actual_object_CS=actual_cs,edges_m=edges,boundary=dict(low=low,high=high,normal=normal,A1_Wb_per_m=value),
            setup_properties=dict(setup.props),saved_project_sha256=digest(project)))
        if validation != 1:
            raise ValueError('Native preflight failed')
        write_json(folder/'solve_started.json',dict(case_id=case_id))
        if not app.analyze_setup('ControlSetup',cores=2,gpus=0,use_auto_settings=False):
            raise ValueError('Native field solve failed')
        write_json(folder/'solve_completed.json',dict(case_id=case_id,new_field_solves=1))
        app.save_project()
        solution = 'ControlSetup : LastAdaptive'
        for quantity in ('B','H'):
            path = folder/(quantity+'.fld')
            if not app.post.export_field_file(quantity,solution=solution,output_file=str(path),
                sample_points=protocol['sample_points_m'],reference_coordinate_system='Global',
                export_in_si_system=True,export_field_in_reference=True):
                raise ValueError('Missing native '+quantity+' field export')
        convergence = app.export_convergence('ControlSetup',output_file=str(folder/'convergence.txt'))
        if not convergence or not (folder/'convergence.txt').is_file():
            raise ValueError('Missing native convergence export')
        B = read_vector_field(folder/'B.fld',protocol['sample_points_m'])
        H = read_vector_field(folder/'H.fld',protocol['sample_points_m'])
        result = evaluate_case(protocol,case,B,H)
        result['native_convergence'] = read_convergence(folder/'convergence.txt',protocol['setup']['PercentError'])
        result.update(native_validation=validation,saved_project_sha256=digest(project),
            actual_object_CS=actual_cs,field_sha256={q:digest(folder/(q+'.fld')) for q in ('B','H')})
        write_json(folder/'result.json',result)
    except Exception as exc:
        write_json(folder/'failure.json',dict(error=str(exc),traceback=traceback.format_exc()))
        raise
    finally:
        if desktop is not None and owned:
            desktop.release_desktop(close_projects=True,close_on_exit=True)


def execute(root,study):
    protocol = frozen(study)
    if json.loads((study/'state.json').read_text())['status']!='prepared_not_executed':
        raise ValueError('No retry of previous controls')
    results = []
    failures = []
    write_json(study/'state.json',dict(status='native_controls_running',new_motor_solves=0))
    for case in protocol['cases']:
        folder = study/'private_native'/case['case_id']
        folder.mkdir(parents=True)
        command = [str(root/'.runtime/aedt/Scripts/python.exe'),'-X','utf8',str(Path(__file__).resolve()),
            '--root',str(root),'--study-dir',str(study),'--native-case',case['case_id']]
        with (folder/'worker.log').open('x',encoding='utf-8') as stream:
            process = subprocess.Popen(command,cwd=study,stdout=stream,stderr=subprocess.STDOUT)
            write_json(folder/'dispatch.json',dict(identity=process_identity(process.pid),command=command))
            try:
                rc = process.wait(timeout=protocol['case_timeout_seconds'])
            except subprocess.TimeoutExpired:
                import psutil
                record = folder/'desktop_session.json'
                if record.exists():
                    owner = json.loads(record.read_text())
                    if owner['dedicated_session'] and identity_status(owner['identity'])=='alive':
                        psutil.Process(owner['identity']['pid']).terminate()
                if process.poll() is None:
                    process.terminate()
                rc = -1
        if rc != 0:
            failures.append(dict(case_id=case['case_id'],exit_code=rc))
            break  # Do not spend remaining budget after native/API failure.
        results.append(json.loads((folder/'result.json').read_text()))
        print(case['case_id'],results[-1]['passed'],flush=True)
    solved = len(list((study/'private_native').glob('*/solve_completed.json')))
    summary = evaluate_suite(protocol,results) if not failures else dict(passed=False,cases=results,failures=failures,
        axis_aligned_field_verified=False,motor_ranking_eligible=False)
    summary.update(new_field_solves=solved,new_motor_solves=0,cumulative_motor_solves=4,
        cumulative_Maxwell_success=4+protocol['prior_successful_field_controls_20261006']+solved,cumulative_MuMax_success=680)
    write_json(study/'summary.json',summary)
    write_json(study/'state.json',dict(status='controls_passed' if summary['passed'] else 'controls_failed_no_retry',
        new_field_solves=solved,new_motor_solves=0))
    return 0 if summary['passed'] else 1


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--study-dir',type=Path,required=True)
    parser.add_argument('--linear-sanity',action='store_true',help='Prepare exactly two analytical constant-mu controls; no measured-material claim')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--prepare',action='store_true')
    group.add_argument('--execute',action='store_true')
    group.add_argument('--native-case')
    args = parser.parse_args()
    root,study = args.root.resolve(),args.study_dir.resolve()
    if args.prepare:
        prepare(root,study,linear_sanity=args.linear_sanity)
    elif args.native_case:
        native_case(study,args.native_case)
    else:
        raise SystemExit(execute(root,study))
