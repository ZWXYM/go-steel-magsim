"""One fresh, owned AEDT access probe; no model edits or physical solves."""
import argparse
import json
import os
import shutil
import subprocess
import sys
import traceback
from pathlib import Path

PROJECT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(PROJECT))
from modules.maxwell_material_transport import digest
from modules.maxwell_native_readiness import require_configured_license_connection
from modules.motor_queue import process_identity,identity_status
from modules.motor_workbench import write_json,license_config

SOURCE='calibration/diagnostics/motor_review_preflight_20261005/jobs/scan_456bd621b9f8/case_02/motor.aedt'
PRODUCERS=('tools/check_native_design_access.py','modules/maxwell_native_readiness.py',
           'modules/maxwell_material_transport.py','modules/motor_queue.py','modules/motor_workbench.py')


def worker(study):
    import psutil
    require_configured_license_connection()
    protocol=json.loads((study/'protocol.json').read_text())
    if any(digest(PROJECT/p)!=h or digest(study/'frozen_producers'/p)!=h
           for p,h in protocol['producer_sha256'].items()):raise ValueError('Producer changed')
    if digest(study/'private_native/motor.aedt')!=protocol['source_project_sha256']:raise ValueError('Input changed')
    config,_=license_config()
    if config:os.environ.setdefault('ANSYSLMD_LICENSE_FILE',config)
    os.environ.setdefault('ANSWAIT','0')
    from ansys.aedt.core import Desktop
    from ansys.aedt.core.generic.settings import settings
    settings.release_on_exception=False
    existing={p.pid for p in psutil.process_iter(['name']) if (p.info['name'] or '').lower()=='ansysedt.exe'}
    desktop=None;owned=False;project=None;active=None
    try:
        desktop=Desktop(version='2025.1',non_graphical=True,new_desktop=True,close_on_exit=False)
        pid=desktop.aedt_process_id
        if pid in existing:raise ValueError('Refuse a pre-existing Desktop')
        owned=True;write_json(study/'private_native/desktop_session.json',dict(identity=process_identity(pid),dedicated_session=True))
        project=desktop.odesktop.OpenProject(str(study/'private_native/motor.aedt'))
        if project is None:raise ValueError('OpenProject returned no project')
        designs=list(project.GetTopDesignList());name='Motor-CAD 2'
        active=project.SetActiveDesign(name)
        if active is None:raise ValueError('SetActiveDesign returned no design')
        design_type=active.GetDesignType();solution_type=active.GetSolutionType()
        if design_type!='Maxwell 2D':raise ValueError('Unexpected design type: '+str(design_type))
        write_json(study/'private_native/access_result.json',dict(native_design_access_verified=True,
            design_type=design_type,solution_type=solution_type,designs=designs,
            initialization_only=True,physical_license_checkout_verified=False,
            new_field_solves=0,new_motor_solves=0,new_MuMax_solves=0,
            all_48_directions_verified=False,real_nonlinear_material_verified=False))
    except Exception as exc:
        write_json(study/'private_native/failure.json',dict(error=str(exc),traceback=traceback.format_exc()));raise
    finally:
        if desktop is not None and owned:
            messages={}
            for label,args in [('global',('','',0)),('design',('motor','Motor-CAD 2',0))]:
                try:messages[label]=list(desktop.odesktop.GetMessages(*args))
                except Exception as exc:messages[label+'_capture_error']=str(exc)
            write_json(study/'private_native/native_messages.json',messages)
            desktop.release_desktop(close_projects=True,close_on_exit=True)


def run(root,study):
    import psutil
    if study.exists() or not study.is_relative_to(root):raise ValueError('Fresh workspace directory required; no retry')
    readiness=require_configured_license_connection();source=root/SOURCE
    protocol=dict(protocol='native_design_access_after_license_restore_v1',source_project=SOURCE,
        source_project_sha256=digest(source),producer_sha256={p:digest(PROJECT/p) for p in PRODUCERS},
        readiness=readiness,max_new_sessions=1,new_physical_solves_budget=0,timeout_seconds=120)
    (study/'private_native').mkdir(parents=True);shutil.copy2(source,study/'private_native/motor.aedt')
    for p in PRODUCERS:
        out=study/'frozen_producers'/p;out.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(PROJECT/p,out)
    write_json(study/'protocol.json',protocol)
    cmd=[str(root/'.runtime/aedt/Scripts/python.exe'),'-X','utf8',str(Path(__file__).resolve()),'--study-dir',str(study),'--worker']
    timed_out=False
    with (study/'private_native/worker.log').open('x',encoding='utf-8') as stream:
        process=subprocess.Popen(cmd,cwd=study,stdout=stream,stderr=subprocess.STDOUT)
        write_json(study/'private_native/dispatch.json',dict(identity=process_identity(process.pid)))
        try:rc=process.wait(timeout=protocol['timeout_seconds'])
        except subprocess.TimeoutExpired:
            timed_out=True;record=study/'private_native/desktop_session.json'
            if record.exists():
                owned=json.loads(record.read_text())
                if owned.get('dedicated_session') is True and identity_status(owned['identity'])=='alive':
                    psutil.Process(owned['identity']['pid']).terminate()
            if process.poll() is None:process.terminate()
            rc=process.wait(timeout=10)
    result_file=study/'private_native/access_result.json'
    result=json.loads(result_file.read_text()) if result_file.exists() else dict(native_design_access_verified=False)
    failure=study/'private_native/failure.json'
    if failure.exists():result['error']=json.loads(failure.read_text())['error']
    record=study/'private_native/desktop_session.json'
    session_status=identity_status(json.loads(record.read_text())['identity']) if record.exists() else 'identity_not_recorded'
    summary=dict(**result,worker_return_code=rc,timed_out=timed_out,dedicated_session_after=session_status,
        original_project_unchanged=digest(source)==protocol['source_project_sha256'],
        cumulative_Maxwell_completed=39,cumulative_MuMax_success=680)
    if not summary['original_project_unchanged']:raise ValueError('Source project changed')
    write_json(study/'summary.json',summary)
    print(json.dumps(summary,ensure_ascii=False,indent=2))
    return 0 if rc==0 and summary.get('native_design_access_verified') and session_status=='dead' else 1


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path);p.add_argument('--study-dir',type=Path,required=True);p.add_argument('--worker',action='store_true')
    a=p.parse_args()
    if a.worker:worker(a.study_dir.resolve())
    else:raise SystemExit(run(a.root.resolve(),a.study_dir.resolve()))
