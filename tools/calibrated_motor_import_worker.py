"""Dedicated preparation worker. No Analyze calls and no solver queue insertion."""
import argparse
import json
import os
import shutil
import sys
import traceback
from pathlib import Path

PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT))
from modules.calibrated_motor_native import verify_inputs
from modules.material_library import sha
from modules.maxwell_material_transport import native_arguments, verify_saved_material
from modules.maxwell_native_readiness import require_configured_license_connection
from modules.motor_model_audit import audit_project
from modules.motor_queue import QueueLease, process_identity, identity_status
from modules.motor_workbench import license_config, write_json


def import_and_preflight(app,project,contract,folder):
    before=audit_project(project)
    if not before['local_CS_verified'] or before['moving_insert_count'] != 48:
        raise ValueError('原生副本的48插片/CS范围不符')
    names=app.materials.odefinition_manager.GetProjectMaterialNames()
    name=contract['material_name']
    if name.casefold() in {n.casefold() for n in names}:
        raise ValueError('新副本已有同名材料定义；不覆盖')
    app.materials.odefinition_manager.AddMaterial(native_arguments(contract))
    if name not in app.materials.odefinition_manager.GetProjectMaterialNames():
        raise ValueError('原生材料定义未添加')
    app.save_project(str(project))
    verification=verify_saved_material(project.read_text(encoding='utf-8-sig'),contract)
    for record in before['inserts']:
        obj=app.modeler[record['name']]
        obj.material_name=name
        if obj.material_name.casefold()!=name.casefold() or obj.part_coordinate_system != record['name']+'_CS':
            raise ValueError('插片材料/坐标系赋值不符：'+record['name'])
    original=before['core_loss']['enabled_object_names']
    if any(n.startswith('unknown_ID_') for n in original):
        raise ValueError('原损耗对象无法识别')
    selected=list(dict.fromkeys(original+[r['name'] for r in before['inserts']]))
    if app.set_core_losses(selected,core_loss_on_field=False) is not True:
        raise ValueError('插片损耗选择保存失败')
    app.save_project(str(project))
    after=audit_project(project)
    verification=verify_saved_material(project.read_text(encoding='utf-8-sig'),contract)
    if (not after['local_CS_verified'] or after['moving_insert_count']!=48 or
            not after['core_loss']['complete_insert_coverage'] or
            not set(original).issubset(after['core_loss']['enabled_object_names']) or
            any(r['material'].casefold()!=name.casefold() for r in after['inserts'])):
        raise ValueError('实际保存的材料/方向引用/原损耗对象覆盖不符')
    write_json(folder/'model_audit.json',dict(before=before,after=after))
    valid=app.validate_simple(str(folder/'validation.log'))
    if valid != 1: raise ValueError('原生模型预检返回 '+str(valid))
    app.save_project(str(project))
    verification=verify_saved_material(project.read_text(encoding='utf-8-sig'),contract)
    return dict(status='native_import_preflight_passed_not_solved',material=verification,
        material_assignment_count=48,insert_core_loss_selected=48,original_loss_objects_preserved=True,
        native_validation=valid,saved_project_sha256=sha(project.read_bytes()),
        H_axis='physical_A_per_m',H_scale=1,new_native_solves=0,
        directional_field_verified=False,loss_calibration_verified=False,
        efficiency_enabled=False,optimization_ranking_enabled=False,
        scope='Native saved RD/TD definitions and object references only; no field/motor response or loss calculation')


def run(folder):
    folder=Path(folder).resolve()
    state=json.loads((folder/'state.json').read_text(encoding='utf-8'))
    lease=QueueLease(folder.parent/'execution.lease')
    desktop=None;owned=False;identity=None;summary=None
    try:
        if not lease.acquire(): raise ValueError('其他原生导入仍在执行')
        manifest=verify_inputs(folder,PROJECT)
        if state['status'] != 'starting' or (folder/'motor.aedt').exists():
            raise ValueError('已有原生执行痕迹，禁止重提')
        require_configured_license_connection()
        state.update(status='running');write_json(folder/'state.json',state)
        project=folder/'motor.aedt'
        shutil.copy2(folder/'inputs/motor.aedt',project)
        os.environ.setdefault('ANSYS_WAIT_FOR_LICENSE','0')
        configured,_=license_config()
        if configured: os.environ.setdefault('ANSYSLMD_LICENSE_FILE',configured)
        import psutil
        from ansys.aedt.core import Desktop,Maxwell2d
        # Every pre-existing PID is excluded, even when its name is unreadable.
        existing={p.pid for p in psutil.process_iter()}
        desktop=Desktop(version='2025.1',non_graphical=True,new_desktop=True,close_on_exit=False)
        pid=desktop.aedt_process_id
        if not pid or pid in existing: raise ValueError('AEDT未建立新的专用会话')
        identity=process_identity(pid);owned=True
        write_json(folder/'desktop_session.json',dict(identity=identity,dedicated_session=True,closed=False))
        app=Maxwell2d(project=str(project),design='Motor-CAD 2',version='2025.1',
            non_graphical=True,new_desktop=False,aedt_process_id=pid,close_on_exit=False,remove_lock=False)
        summary=import_and_preflight(app,project,manifest['material_contract'],folder)
        summary.update(parent_bank_sha256=manifest['parent_bank_sha256'],effective_bank_sha256=manifest['effective_bank_sha256'],
            excluded_grades=manifest['excluded_grades'],preparation_id=manifest['preparation_id'])
    except Exception as exc:
        state.update(status='failed',error=str(exc))
        write_json(folder/'failure.json',dict(error=str(exc),traceback=traceback.format_exc(),new_native_solves=0))
    finally:
        cleanup_error=None
        if owned and desktop is not None and identity_status(identity)=='alive':
            try:
                if desktop.release_desktop(close_projects=True,close_on_exit=True) is False:
                    raise ValueError('原生API未确认释放专用会话')
                write_json(folder/'desktop_session.json',dict(identity=identity,dedicated_session=True,closed=True))
            except Exception as exc: cleanup_error=str(exc)
        if summary is not None:
            write_json(folder/'summary.json',summary)
            state.update(status='imported_not_solved')
        if cleanup_error:
            state.update(status='needs_attention',error='专用会话清理未确认：'+cleanup_error)
        write_json(folder/'state.json',state)
        lease.release()
    return state


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--folder',type=Path,required=True)
    args=parser.parse_args()
    result=run(args.folder)
    print(json.dumps(result,ensure_ascii=False),flush=True)
    raise SystemExit(0 if result['status']=='imported_not_solved' else 1)
