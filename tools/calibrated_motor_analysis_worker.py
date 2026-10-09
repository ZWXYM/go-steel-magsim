"""Dedicated, one-shot BH-only motor analysis; export official torque only."""
import argparse
import csv
import math
import os
import re
import shutil
import sys
import time
import traceback
from datetime import datetime,timezone
from pathlib import Path

PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT))
from modules.calibrated_motor_analysis import collect_torque
from modules.calibrated_motor_execution import read,verify_inputs,verify_model,BUDGET
from modules.material_library import sha
from modules.motor_queue import QueueLease,process_identity,identity_status
from modules.motor_workbench import write_json,license_config
from modules.maxwell_native_readiness import require_configured_license_connection
from tools.motor_scan_worker import export_report

def check_budget(folder,deadline):
    if (folder/'attention.json').exists() or time.monotonic()>deadline:raise TimeoutError('预算已超时，停止下发后续步骤，现场保留')

def seconds(value):
    match=re.fullmatch(r'([0-9.eE+-]+)\s*(s|ms|us|ns)',str(value))
    if not match:raise ValueError('保存时间参数不是可核对的显式SI时间：'+str(value))
    return float(match[1])*{'s':1,'ms':1e-3,'us':1e-6,'ns':1e-9}[match[2]]

def configure(app,project,m,folder):
    before=verify_model(project,m);p=m['requested_protocol']
    app['NumTorqueCycles']=str(p['cycles']);app['NumTorquePointsPerCycle']='30'
    if (abs(float(app.get_evaluated_value('NumTorqueCycles'))-p['cycles'])>1e-8 or
        abs(float(app.get_evaluated_value('NumTorquePointsPerCycle'))-30)>1e-8):raise ValueError('周期/采样变量未实际应用')
    setup=app.get_setup(m['setup'])
    setup.props.update(StopTime=format(p['stop_s'],'.12g')+'s',TimeStep=format(p['requested_time_step_s'],'.12g')+'s',
        UseAdaptiveTimeStep=False,AutoDetectSteadyState=False,FastReachSteadyState=False,
        SaveFieldsType='Every N Steps',**{'N Steps':'1','Steps From':'0s','Steps To':format(p['stop_s'],'.12g')+'s'})
    if setup.update() is not True:raise ValueError('原生时间设置更新失败')
    app.save_project(str(project));after=verify_model(project,m)
    if (after['operating_point']['cycles']!=str(p['cycles']) or after['operating_point']['points_per_cycle']!='30' or
        not math.isclose(seconds(after['setup']['StopTime']),p['stop_s'],rel_tol=1e-10,abs_tol=1e-12) or
        not math.isclose(seconds(after['setup']['TimeStep']),p['requested_time_step_s'],rel_tol=1e-10,abs_tol=1e-12) or
        after['setup']['UseAdaptiveTimeStep']!='false' or after['setup']['AutoDetectSteadyState']!='false' or
        after['setup']['FastReachSteadyState']!='false' or
        set(before['core_loss']['enabled_object_names'])!=set(after['core_loss']['enabled_object_names'])):
        raise ValueError('实际保存周期/时间设置或原损耗对象与请求不符')
    valid=app.validate_simple(str(folder/'validation.log'))
    if valid!=1:raise ValueError('实际分析工程预检未通过：'+str(valid))
    record=dict(native_configuration_verified=True,requested_protocol=p,setup=after['setup'],operating_point=after['operating_point'],
        validation=valid,configured_project_sha256=sha(project.read_bytes()),core_loss_object_names=after['core_loss']['enabled_object_names'],
        original_loss_selection_preserved=True,efficiency_enabled=False,optimization_ranking_enabled=False)
    write_json(folder/'configuration.json',record);return record

def execute(app,project,m,folder,deadline,phase):
    check_budget(folder,deadline);phase('configuring');configuration=configure(app,project,m,folder)
    check_budget(folder,deadline);phase('solving')
    write_json(folder/'solve_attempt.json',dict(setup=m['setup'],utc=datetime.now(timezone.utc).isoformat(),cores=m['cores'],gpus=0))
    if app.analyze_setup(m['setup'],cores=m['cores'],gpus=0,use_auto_settings=False,revert_to_initial_mesh=False) is not True:
        raise RuntimeError('Maxwell转矩分析失败；不自动重试')
    # Collect a late solve as late, never silently turn an over-budget run into normal completion.
    phase('collecting');report=folder/'case/reports/Torque Plots.csv'
    export_report(app,'WB_BH_Torque',['-Moving1.Torque'],report)
    metrics=collect_torque(report,m['measurement_protocol'])
    app.save_project(str(project));verify_model(project,m)
    result=dict(execution_id=m['id'],analysis_plan_id=m['analysis_plan_id'],native_import_id=m['native_import_id'],
        material_name=m['material_name'],model_version='V8_2_object_CS_BH_only_torque_v1',measurement_protocol=m['measurement_protocol'],
        parent_bank_sha256=m['parent_bank_sha256'],effective_bank_sha256=m['effective_bank_sha256'],
        training_grades=m['training_grades'],excluded_grades=m['excluded_grades'],H_axis='physical_A_per_m',H_scale=1,
        native_analyze_succeeded=True,native_configuration_verified=configuration['native_configuration_verified'],
        new_Maxwell_solves=1,metrics=metrics,initial_project_sha256=m['input_hashes']['inputs/motor.aedt'],
        solved_project_sha256=sha(project.read_bytes()),budget_exceeded=bool((folder/'attention.json').exists() or time.monotonic()>deadline),
        efficiency_enabled=False,optimization_ranking_enabled=False,source_scope='actual_calibrated_material_motor_torque_only')
    write_json(folder/'result.json',result)
    names=['execution_id','material_name','measurement_protocol','T_avg_Nm','T_min_Nm','T_max_Nm','K_T_ripple_pct','torque_csv_sha256']
    with (folder/'summary.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=names);writer.writeheader()
        writer.writerow({key:(result[key] if key in result else metrics[key]) for key in names})
    return result

def create_session(project,m,folder):
    import psutil
    os.environ.setdefault('ANSYS_WAIT_FOR_LICENSE','0')
    configured,_=license_config()
    if configured:os.environ.setdefault('ANSYSLMD_LICENSE_FILE',configured)
    from ansys.aedt.core import Desktop
    existing={p.pid for p in psutil.process_iter()}
    desktop=Desktop(version=m['aedt_version'],non_graphical=True,new_desktop=True,close_on_exit=False)
    pid=desktop.aedt_process_id
    if not pid or pid in existing:
        write_json(folder/'desktop_unowned.json',dict(pid=pid,reason='not demonstrably new; untouched'))
        raise ValueError('没有确认新专用AEDT进程，未操作已有会话')
    try:identity=process_identity(pid)
    except Exception:
        write_json(folder/'desktop_session.json',dict(identity={'pid':pid},dedicated_session_unconfirmed=True,closed=False));raise
    write_json(folder/'desktop_session.json',dict(identity=identity,dedicated_session=True,closed=False))
    # Record ownership before app construction, which can fail independently.
    return desktop,identity

def open_app(project,m,identity):
    from ansys.aedt.core import Maxwell2d
    return Maxwell2d(project=str(project),design=m['design'],version=m['aedt_version'],non_graphical=True,
        new_desktop=False,aedt_process_id=identity['pid'],close_on_exit=False,remove_lock=False)

def run(folder,motor_storage,native_storage):
    folder=Path(folder).resolve();motor_storage=Path(motor_storage).resolve();native_storage=Path(native_storage).resolve()
    if (folder.parent.name!='calibrated_motor_execution' or motor_storage!=folder.parent.parent/'motor' or
        native_storage!=folder.parent.parent/'calibrated_motor_native'):raise ValueError('执行/扫描/导入目录不是同一存储的隔离子目录')
    state=read(folder/'state.json')
    if state['status']!='starting' or (folder/'case').exists():raise ValueError('仅允许尚未执行的starting任务，禁止重提原目录')
    locks=[];desktop=None;identity=None;result=None;owned_queue=False;deadline=time.monotonic()+BUDGET['wall_seconds']
    def phase(name):
        state.update(status='running',phase=name);write_json(folder/'state.json',state)
    try:
        # Native start/scan controllers use these same OS locks. Wait only for the launch handoff.
        handoff_deadline=time.monotonic()+5
        while True:
            for path in (motor_storage/'queue.guard',native_storage/'dispatch.lease',native_storage/'execution.lease'):
                lease=QueueLease(path)
                if not lease.acquire():break
                locks.append(lease)
            if len(locks)==3:break
            for lease in reversed(locks):lease.release()
            locks=[]
            if time.monotonic()>handoff_deadline:raise ValueError('扫描/导入仍占用执行锁，不启动AEDT')
            time.sleep(.1)
        queue=read(motor_storage/'queue.lock')
        if queue.get('token')!=state['queue_token'] or queue['pid']!=os.getpid() or identity_status(queue)!='alive':
            raise ValueError('未确认本任务的队列归属，不启动AEDT')
        owned_queue=True;m=verify_inputs(folder,PROJECT,dispatch=True);require_configured_license_connection()
        check_budget(folder,deadline);phase('opening');case=folder/'case';case.mkdir();project=case/'motor.aedt'
        shutil.copy2(folder/'inputs/motor.aedt',project)
        if project.with_suffix('.aedtresults').exists() or Path(str(project)+'.lock').exists():raise ValueError('新副本已有结果或锁，禁止执行')
        desktop,identity=create_session(project,m,folder)
        app=open_app(project,m,identity)
        result=execute(app,project,m,folder,deadline,phase)
        state.update(status='completed_after_budget' if result['budget_exceeded'] else 'completed',phase='finished',new_Maxwell_solves=1)
    except Exception as exc:
        state.update(status='failed',phase='failed',error=str(exc))
        write_json(folder/'failure.json',dict(error=str(exc),traceback=traceback.format_exc(),
            solve_attempted=(folder/'solve_attempt.json').exists(),new_completed_Maxwell_solves=1 if result else 0))
    finally:
        cleanup_error=None
        if desktop is not None and identity is not None:
            status=identity_status(identity)
            if status=='alive':
                try:
                    if desktop.release_desktop(close_projects=True,close_on_exit=True) is False:raise ValueError('原生API未确认释放')
                    write_json(folder/'desktop_session.json',dict(identity=identity,dedicated_session=True,closed=True))
                except Exception as exc:cleanup_error=str(exc)
            elif status!='dead':cleanup_error='专用会话身份无法确认，未关闭'
        if (folder/'desktop_unowned.json').exists() or ((folder/'desktop_session.json').exists() and identity is None):
            cleanup_error='AEDT归属无法确认，未关闭'
        if cleanup_error:state.update(status='needs_attention',error=cleanup_error)
        state['finished_utc']=datetime.now(timezone.utc).isoformat();write_json(folder/'state.json',state)
        if owned_queue and not cleanup_error:
            owner=motor_storage/'queue.lock'
            if owner.exists() and read(owner).get('token')==state['queue_token']:
                owner.rename(folder/'queue_owner_released.json')
        for lease in reversed(locks):lease.release()
    return state

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--folder',type=Path,required=True);parser.add_argument('--motor-storage',type=Path,required=True)
    parser.add_argument('--native-storage',type=Path,required=True);args=parser.parse_args()
    outcome=run(args.folder,args.motor_storage,args.native_storage)
    raise SystemExit(0 if outcome['status']=='completed' else 1)
