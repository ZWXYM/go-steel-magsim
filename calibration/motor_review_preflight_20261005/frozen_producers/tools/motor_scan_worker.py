"""Solve newly created scan copies in a dedicated AEDT session; preserve archives."""
from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from datetime import datetime,timezone
from pathlib import Path

PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT))
from modules.material_calibration import file_hash
from modules.motor_workbench import collect_metrics,write_json,license_config
from modules.motor_workbench import write_scan_summary
from modules.motor_queue import process_identity,identity_status
from modules.motor_model_audit import audit_project,comparison_status,LOSS_POLICY


def export_report(app,name,expressions,path):
    path.parent.mkdir(parents=True,exist_ok=True)
    reporter=app.odesign.GetModule('ReportSetup')
    if name in list(reporter.GetAllReportNames()):
        reporter.DeleteReports([name])
    reporter.CreateReport(name,'Transient','Rectangular Plot','Setup1 : Transient',
        ['Domain:=','Sweep'],['Time:=',['All']],
        ['X Component:=','Time','Y Component:=',expressions],[])
    reporter.ExportToFile(name,str(path),False)
    if not path.is_file():
        raise RuntimeError('官方 CSV 导出失败: '+name)


def configure_physics(app,project,case,manifest,folder):
    """Save actual library definitions, enable all insert losses in new copies."""
    app.save_project()
    before=audit_project(project)
    if manifest.get('loss_scope_policy')!=LOSS_POLICY:
        raise ValueError('旧损耗范围协议仅保留作历史证据；请生成新诊断任务')
    point=before['operating_point']
    cycles=3 if manifest.get('measurement_protocol')=='periodic_cycle3_v1' else 1
    if point['speed_rpm']!=3000 or point['poles']!=4 or point['points_per_cycle']!='30' or point['cycles']!=str(cycles):
        raise ValueError('实际转速/极数/时间采样与固定测量协议不符')
    if not before['local_CS_verified'] or before['moving_insert_count']!=48:
        raise ValueError('实际插片的局部坐标系或运动绑定不完整')
    if not all(m['embedded'] and m['loss_definition_present'] for m in before['materials']):
        raise ValueError('实际插片材料缺少可核验的损耗模型；不编造损耗系数')
    if any(n.startswith('unknown_ID_') for n in before['core_loss']['enabled_object_names'] or []):
        raise ValueError('原铁损对象无法映射，禁止丢失已有损耗对象')
    names=list(dict.fromkeys((before['core_loss']['enabled_object_names'] or [])+case['quality']['object_names']))
    if not app.set_core_losses(names,core_loss_on_field=False):
        raise ValueError('插片铁损激活失败')
    app.save_project()
    after=audit_project(project)
    if not after['core_loss']['complete_insert_coverage']:
        raise ValueError('保存项目中 48 插片铁损未全部激活')
    write_json(folder/'model_physics_audit.json',dict(before=before,after=after,
        loss_scope_policy=LOSS_POLICY,changed_existing_archive=False,
        original_enabled_objects_preserved=set(before['core_loss']['enabled_object_ids'])<=set(after['core_loss']['enabled_object_ids'])))
    return after


def solve_case(path,case,manifest,preflight_only=False):
    import psutil
    os.environ.setdefault('ANSWAIT','0')
    license_source='existing_environment'
    if not os.environ.get('ANSYSLMD_LICENSE_FILE'):
        config,license_source=license_config()
        if config:
            os.environ['ANSYSLMD_LICENSE_FILE']=config
    from ansys.aedt.core import Desktop,Maxwell2d
    existing={p.pid for p in psutil.process_iter(['name']) if (p.info['name'] or '').lower()=='ansysedt.exe'}
    project=path/case['project']
    if not project.resolve().is_relative_to(path.resolve()) or file_hash(project)!=case['project_sha256']:
        raise ValueError('扫描副本路径/哈希改变')
    # A fresh case must have no inherited solver output or lock.
    if project.with_suffix('.aedtresults').exists() or Path(str(project)+'.lock').exists():
        raise ValueError('该扫描副本已经存在求解结果/锁；请保留并创建新任务')
    folder=project.parent
    app=None
    desktop=None
    owned=False
    try:
        desktop=Desktop(version=manifest['aedt_version'],non_graphical=True,new_desktop=True,close_on_exit=False)
        pid=desktop.aedt_process_id
        if pid in existing:
            raise RuntimeError('未取得独立 AEDT 进程，拒绝改动已有会话')
        owned=True
        write_json(folder/'desktop_session.json',dict(identity=process_identity(pid),
            dedicated_session=True,created_utc=datetime.now(timezone.utc).isoformat()))
        app=Maxwell2d(project=str(project),design=manifest['design'],solution_type='Transient',
            version=manifest['aedt_version'],non_graphical=True,new_desktop=False,
            aedt_process_id=pid,close_on_exit=False,remove_lock=False)
        cycles=3 if manifest.get('measurement_protocol')=='periodic_cycle3_v1' else 1
        app['NumTorqueCycles']=str(cycles)
        if abs(float(app.get_evaluated_value('NumTorqueCycles'))-cycles)>1e-8:
            raise ValueError('周期配置未实际生效')
        assignments=[]
        for name in case['quality']['object_names']:
            obj=app.modeler[name]
            cs=obj.part_coordinate_system
            if cs!=name+'_CS':
                raise ValueError('局部坐标系未激活: '+name+' -> '+str(cs))
            # Setting this property loads a library material if needed; only
            # these 48 copied objects are touched, no geometric operation.
            obj.material_name=case['material']
            if obj.material_name.casefold()!=case['material'].casefold():
                raise ValueError('材料未实际赋值: '+name)
            assignments.append(dict(object=name,coordinate_system=cs,material=obj.material_name))
        physics=configure_physics(app,project,case,manifest,folder)
        validation=app.validate_simple(str(folder/'validation.log'))
        if validation!=1:
            raise ValueError('AEDT 模型验证未通过: '+str(validation))
        write_json(folder/'model_validation.json',dict(aedt_pid=pid,dedicated_session=True,
            valid=validation,assignments=assignments,object_count=len(assignments),license_source=license_source))
        setup=app.get_setup(manifest['setup'])
        setup.props['SaveFieldsType']='Every N Steps'
        setup.props['N Steps']='1'
        setup.props['Steps From']='0s'
        setup.props['Steps To']=setup.props.get('StopTime','0.01s')
        if not setup.update():
            raise ValueError('求解配置保存失败')
        app.save_project()
        if preflight_only:
            return dict(material=case['material'],case_id=case['id'],preflight_only=True,
                solve_successful=False,new_Maxwell_solves=0,model_physics_audit=physics,validation=validation)
        ok=app.analyze_setup(manifest['setup'],cores=manifest['cores'],use_auto_settings=False,revert_to_initial_mesh=False)
        if not ok:
            raise RuntimeError('Maxwell 求解失败')
        export_report(app,'WB_Torque',['-Moving1.Torque','TorqueDQ'],folder/'reports/Torque Plots.csv')
        export_report(app,'WB_Current',['InputCurrent(WG_Ph1_P1)','InputCurrent(WG_Ph2_P1)','InputCurrent(WG_Ph3_P1)'],folder/'reports/Drive Current Plots.csv')
        for name in ('CoreLoss','StrandedLoss','SolidLoss'):
            export_report(app,'WB_'+name,[name],folder/'maxwell_reports'/f'{name}.csv')
        result=collect_metrics(folder,manifest.get('measurement_protocol','v8_2_cycle1'))
        app.save_project()
        result.update(material=case['material'],case_id=case['id'],model_version=manifest['model_version'],
            template_sha256=manifest['template_sha256'],initial_project_sha256=case['project_sha256'],
            solved_project_sha256=file_hash(project),aedt_pid=pid,solve_successful=True)
        result['model_physics_audit']=audit_project(project)
        result['comparison_eligibility']=comparison_status(result)
        write_json(folder/'result.json',result)
        return result
    except Exception:
        if desktop is not None and owned:
            try:
                write_json(folder/'desktop_failure_messages.json',dict(messages=list(desktop.odesktop.GetMessages('','',0)),aedt_pid=pid))
            except Exception:
                pass
        raise
    finally:
        if desktop is not None and owned:
            desktop.release_desktop(close_projects=True,close_on_exit=True)


def run(path):
    path=path.resolve()
    manifest=json.loads((path/'manifest.json').read_text(encoding='utf-8'))
    state=json.loads((path/'state.json').read_text(encoding='utf-8'))
    if state['status']!='starting' or manifest['id']!=path.name:
        raise ValueError('仅允许执行队列中尚未运行的独立扫描')
    if file_hash(Path(__file__))!=manifest['worker_sha256']:
        raise ValueError('求解程序在任务准备后改变，请重新创建扫描')
    for name,digest in manifest.get('support_source_sha256',{}).items():
        if file_hash(PROJECT/name)!=digest:
            raise ValueError('扫描指标程序在任务准备后改变，请重新创建扫描')
    state.update(status='running',started_utc=datetime.now(timezone.utc).isoformat())
    write_json(path/'state.json',state)
    results=[]
    for i,case in enumerate(manifest['cases']):
        for previous in manifest['cases'][:i]:
            session=path/previous['id']/'desktop_session.json'
            if session.exists() and identity_status(json.loads(session.read_text(encoding='utf-8'))['identity'])!='dead':
                state.update(status='needs_attention',error='前序独立 Desktop 尚未确认退出；保留现场及未执行牌号',current=None)
                write_json(path/'state.json',state)
                return 3
        state['current']=case['material']
        state['cases'][i]['status']='running'
        write_json(path/'state.json',state)
        print('开始 '+case['material'],flush=True)
        try:
            result=solve_case(path,case,manifest)
            state['cases'][i].update(status='completed' if result['accepted'] else 'rejected',metrics=result)
            results.append(result)
        except Exception as exc:
            failure=dict(case_id=case['id'],material=case['material'],accepted=False,error=str(exc),traceback=traceback.format_exc())
            results.append(failure)
            state['cases'][i].update(status='failed',error=str(exc))
            write_json(path/case['id']/'failure.json',failure)
            print(traceback.format_exc(),flush=True)
        state['completed']=i+1
        write_json(path/'state.json',state)
    summary=write_scan_summary(path,manifest,results)
    state.update(status='completed' if summary['accepted']==len(results) else 'completed_with_failures',
        finished_utc=datetime.now(timezone.utc).isoformat(),current=None,summary=summary)
    write_json(path/'state.json',state)
    return 0 if summary['accepted'] else 2


def preflight(path):
    """Dedicated native model validation only; cannot submit or solve a queue."""
    path=path.resolve()
    manifest=json.loads((path/'manifest.json').read_text(encoding='utf-8'))
    state=json.loads((path/'state.json').read_text(encoding='utf-8'))
    if state['status']!='prepared' or manifest['id']!=path.name:
        raise ValueError('原生预检仅允许全新准备副本')
    if file_hash(Path(__file__))!=manifest['worker_sha256']:
        raise ValueError('预检程序在准备后改变')
    for name,digest in manifest['support_source_sha256'].items():
        if file_hash(PROJECT/name)!=digest:
            raise ValueError('预检来源已改变')
    state.update(status='preflight_running',new_Maxwell_solves=0)
    write_json(path/'state.json',state)
    results=[]
    try:
        for case in manifest['cases']:
            # No retry: each fresh copy is changed and cannot be dispatched.
            results.append(solve_case(path,case,manifest,preflight_only=True))
        state.update(status='preflight_completed_not_solved',preflight_results=results)
        write_json(path/'preflight.json',dict(results=results,new_Maxwell_solves=0))
        return 0
    except Exception as exc:
        state.update(status='preflight_failed',error=str(exc),traceback=traceback.format_exc())
        raise
    finally:
        write_json(path/'state.json',state)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job-dir',type=Path,required=True)
    parser.add_argument('--preflight-only',action='store_true',help='Validate fresh copied models without any native solve')
    args=parser.parse_args()
    raise SystemExit(preflight(args.job_dir) if args.preflight_only else run(args.job_dir))
