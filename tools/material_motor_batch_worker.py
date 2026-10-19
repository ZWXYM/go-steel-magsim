"""Serial orchestration only. Native work is delegated to existing one-shot workers."""
import argparse
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT))
from modules.material_motor_batches import MaterialMotorBatches, workspace_chain, read, TERMINAL
from modules.material_motor_workflow import same_source
from modules.motor_queue import QueueLease, process_identity, identity_status
from modules.motor_workbench import write_json


class PauseRequested(Exception): pass


def check_budget(deadline):
    if time.monotonic()>deadline: raise TimeoutError('批量预算已到，保留现场，不再下发下一阶段')


def claim_controller(manager,folder,state):
    import psutil
    lease=QueueLease(manager.storage.parent/'native_dispatch.guard')
    deadline=time.monotonic()+8
    while not lease.acquire():
        if time.monotonic()>deadline:raise ValueError('批量派发交接锁未释放')
        time.sleep(.05)
    try:
        path=manager.storage/'active_controller.json';owner=read(path)
        if (owner['batch_id']!=state['id'] or owner['token']!=state['controller_token']
                or identity_status(owner['identity'])!='alive'):
            raise ValueError('批量控制器归属不符')
        launcher=owner['identity']
        if launcher['pid']!=os.getpid():
            parent=psutil.Process().parent()
            if parent is None or parent.pid!=launcher['pid'] or abs(parent.create_time()-launcher['create_time'])>=.01:
                raise ValueError('批量启动器不是当前控制器已验证的直接父进程')
        owner.update(identity=process_identity(),launcher_identity=launcher)
        write_json(folder/('controller_identity_'+owner['token']+'.json'),owner)
        write_json(path,owner);write_json(folder/'controller.json',owner)
        return owner
    finally:lease.release()


def resources_idle(manager):
    chain=manager.chain;motor=chain.preparation.motor
    if any(r['status'] in ('queued','starting','running') for r in motor.jobs()):return False
    if motor._ownership_obstacles():return False
    for component in (chain.native,chain.execution):
        for record in component.records():
            if record['status'] in ('invalid','needs_attention'):
                raise ValueError('已有原生记录需要检查，批量未下发下一项：'+record['id'])
            if record['status'] in ('starting','running'):return False
            for name in ('dispatch.json','desktop_session.json'):
                path=component.path(record['id'])/name
                if path.exists():
                    status=identity_status(read(path)['identity'])
                    if status=='unknown':raise ValueError('已有进程归属未知，保留现场')
                    if status=='alive':return False
    # A stale owner is retained for the existing explicit recovery operation.
    if (motor.storage/'queue.lock').exists():
        raise ValueError('已有队列归属记录，请先在扫描页检查恢复；未覆盖')
    return True


def wait_resources(manager,deadline):
    wait_until=min(deadline,time.monotonic()+30)
    while not resources_idle(manager):
        check_budget(deadline)
        if time.monotonic()>wait_until:raise ValueError('其他原生任务仍活动，批量暂停等待显式接续')
        time.sleep(.2)


def advance_item(manager,manifest,state,item,cursor,deadline,save):
    chain=manager.chain
    while True:
        check_budget(deadline);manager.verify_producers(manifest)
        records=manager.item_records(item,cursor)
        native,plan=records['native_import'],records['analysis_plan']
        run=records.get('execution')
        pause_path=getattr(manager,'current_folder',None)
        if pause_path and (pause_path/'pause_requested.json').exists():
            if run and run['status']=='completed' and run.get('result_available'):
                cursor.update(status='completed' if run['id'] in state['new_execution_ids'] else 'reused_result',error=None);save();return
            if native['status'] not in ('starting','running') and (not run or run['status'] not in ('starting','running')):
                raise PauseRequested('已暂停后续派发，现有阶段与结果保留')
        if native['status']=='ready':
            wait_resources(manager,deadline)
            if native['id'] in state['new_import_ids']:
                raise ValueError('已有导入下发尝试，不重复执行原记录')
            if len(state['new_import_ids'])>=manifest['budget']['max_new_imports']:
                raise ValueError('批量新增导入预算已满')
            state['new_import_ids'].append(native['id']);cursor['status']='importing';save()
            try:
                chain.execution.ensure_idle();chain.native.start(native['id'])
            except Exception:
                source=chain.native.path(native['id'])
                if chain.native.get(native['id'])['status']=='ready' and not any((source/n).exists() for n in ('dispatch.json','worker.log','motor.aedt')):
                    state['new_import_ids'].remove(native['id']);save()
                raise
        elif native['status'] in ('starting','running'):
            if cursor['status']!='importing':cursor['status']='importing';save()
            time.sleep(.5)
        elif native['status']!='imported_not_solved':
            cursor.update(status='blocked',error=native.get('error') or '现有导入不可执行：'+native['status']);save();return
        elif plan['status']=='ready_for_model_preparation':
            chain.analysis.prepare_model(plan['id']);cursor['status']='preparing_model';save()
        elif plan['status']!='model_snapshot_ready_not_submitted':
            raise ValueError('原生导入已完成但分析工程状态不符')
        else:
            run=records.get('execution')
            if run and 'execution' not in cursor['records']:
                cursor['records']['execution']=dict(id=run['id'],status=run['status']);save();continue
            if run is None:
                candidates=[r for r in chain.execution.records() if r.get('analysis_plan_id')==plan['id']]
                if any(not same_source(r,item['source']) for r in candidates):
                    raise ValueError('已有执行与批量材料来源不符')
                run=chain.pick(candidates,{'completed':5,'running':4,'starting':4,'prepared':3})
                if run is None:run=chain.execution.prepare(dict(analysis_plan=plan['id']))
                cursor['records']['execution']=dict(id=run['id'],status=run['status']);save()
                continue
            if run['status']=='completed' and run.get('result_available'):
                cursor.update(status='completed' if run['id'] in state['new_execution_ids'] else 'reused_result',error=None);save();return
            if run['status']=='prepared':
                wait_resources(manager,deadline)
                if run['id'] in state['new_execution_ids']:raise ValueError('已有执行下发尝试，不重复执行原记录')
                if len(state['new_execution_ids'])>=manifest['budget']['max_new_motor_solves']:
                    raise ValueError('批量新增电机预算已满')
                state['new_execution_ids'].append(run['id']);cursor['status']='analyzing';save()
                try: chain.execution.start(run['id'])
                except Exception:
                    source=chain.execution.path(run['id'])
                    if chain.execution.get(run['id'])['status']=='prepared' and not any((source/n).exists() for n in ('dispatch.json','worker.log','case')):
                        state['new_execution_ids'].remove(run['id']);save()
                    raise
            elif run['status'] in ('starting','running'):
                if cursor['status']!='analyzing':cursor['status']='analyzing';save()
                time.sleep(.5)
            elif run['status'] in ('failed','prepared_source_changed'):
                cursor.update(status='failed',error=run.get('error') or run['status']);save();return
            else:
                raise ValueError('执行需要检查，不下发后续项：'+run['status'])


def run(folder,root):
    folder=Path(folder).resolve();storage=folder.parent.parent
    if folder.parent.name!='calibrated_motor_batches':raise ValueError('批量目录不属于隔离存储')
    os.environ['MAGSIM_AUTO_RESUME_QUEUE']='0'
    manager=MaterialMotorBatches(workspace_chain(PROJECT,root,storage),folder.parent)
    manager.current_folder=folder
    manifest=read(folder/'manifest.json');state=read(folder/'state.json')
    lease=QueueLease(folder.parent/'controller.lease');owner=None;owned=False
    if state['status']!='starting':raise ValueError('仅允许显式开始的批量控制器')
    def save():write_json(folder/'state.json',state)
    try:
        if not lease.acquire():raise ValueError('已有批量控制器持有租约')
        owner=claim_controller(manager,folder,state);owned=True;manager.verify_producers(manifest)
        manager.get(folder.name)
        deadline=time.monotonic()+manifest['budget']['wall_seconds']
        state.update(status='running',controller_started_utc=datetime.now(timezone.utc).isoformat());save()
        for item,cursor in zip(manifest['items'],state['items']):
            if (folder/'pause_requested.json').exists():raise PauseRequested('已暂停后续派发，现有阶段与结果保留')
            if cursor['status'] in ('completed','reused_result','failed','blocked'):continue
            state['current_item']=item['id'];save()
            advance_item(manager,manifest,state,item,cursor,deadline,save)
            wait_resources(manager,deadline)
        if (folder/'pause_requested.json').exists():raise PauseRequested('当前阶段完成，已暂停后续派发')
        state.update(status='completed_with_failures' if any(r['status'] in ('failed','blocked') for r in state['items']) else 'completed',current_item=None)
    except PauseRequested as exc:
        state.update(status='paused',error=str(exc),current_item=None)
    except Exception as exc:
        state.update(status='needs_attention',error=str(exc))
        write_json(folder/('failure_'+state.get('controller_token','unclaimed')+'.json'),
            dict(error=str(exc),traceback=traceback.format_exc(),automatic_retry=False))
    finally:
        state['updated_utc']=datetime.now(timezone.utc).isoformat();save()
        if owned:
            try:
                if resources_idle(manager):
                    path=manager.storage/'active_controller.json'
                    if path.exists() and read(path).get('token')==owner['token']:
                        path.rename(folder/('reservation_released_'+owner['token']+'.json'))
            except Exception:
                # Unknown/live child ownership retains the reservation.
                pass
        lease.release()
    return state


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--folder',type=Path,required=True)
    parser.add_argument('--root',type=Path,required=True);args=parser.parse_args()
    outcome=run(args.folder,args.root)
    raise SystemExit(0 if outcome['status'] in ('completed','completed_with_failures','paused') else 1)
