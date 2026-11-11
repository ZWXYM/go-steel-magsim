"""Settle one interrupted batch after all of its recorded processes have exited."""
from contextlib import ExitStack
from datetime import datetime, timezone
import json
import math
import uuid

from modules.calibrated_motor_execution import read
from modules.material_library import sha
from modules.motor_queue import DispatchWindow, QueueLease, identity_status
from modules.motor_workbench import write_json


VERSION = 'interrupted_calibrated_batch_v1'
JOURNAL = 'interruption_recovery.json'
ACTIVE = ('starting', 'running', 'needs_attention')


def require_dead(identities):
    for owner in identities:
        if (not isinstance(owner, dict) or type(owner.get('pid')) is not int or owner['pid'] <= 0
                or type(owner.get('create_time')) not in (float, int)
                or not math.isfinite(owner['create_time']) or owner['create_time'] <= 0
                or identity_status(owner) != 'dead'):
            raise ValueError('尚有活动或身份未确认的专用进程，保持中断现场')


def _execution_identities(run, execution_state, queue_owner):
    execution = run.name
    launcher = read(run/'dispatch.json')['identity']
    worker = read(run/'worker_identity.json')
    session = read(run/'desktop_session.json')
    if (worker['launcher_identity'] != launcher or worker['launcher_is_self_or_verified_direct_parent'] is not True
            or session['dedicated_session'] is not True
            or session.get('native_PID_verified') is not True):
        raise ValueError('无法核对执行工作进程或专用 AEDT 会话身份')
    if queue_owner != dict(worker['identity'], token=execution_state['queue_token'], torque_execution_id=execution):
        raise ValueError('原生队列归属不是本次中断任务')
    identities = [launcher, worker['identity'], session['identity']]
    if (run/'budget_watch_dispatch.json').exists():
        identities.append(read(run/'budget_watch_dispatch.json')['identity'])
    if (run/'budget_watch_ready.json').exists():
        watch = read(run/'budget_watch_ready.json')
        if (watch['execution_id'] != execution or watch['queue_token'] != execution_state['queue_token']
                or watch['target_identity'] != session['identity'] or watch['target_verified'] is not True):
            raise ValueError('预算守护会话身份不符')
        identities.append(watch['identity'])
    return identities


def _capture(manager, folder, reservation, queue):
    record = manager.get(folder.name)
    state = read(folder/'state.json')
    if state['status'] not in ACTIVE or record['status'] != 'needs_attention':
        raise ValueError('请打开控制器已退出的中断批量')
    item = next((row for row in state['items'] if row['id'] == state.get('current_item')), None)
    if not item or item['status'] != 'analyzing':
        raise ValueError('当前中断不属于已下发的电机分析阶段')
    execution = item['records']['execution']['id']
    if execution not in state['new_execution_ids']:
        raise ValueError('执行任务不属于本批已下发记录')
    run = manager.chain.execution.path(execution)
    execution_state = read(run/'state.json')
    if (execution_state['id'] != execution or execution_state['status'] not in ACTIVE
            or execution_state['new_Maxwell_solves'] != 0):
        raise ValueError('执行状态需要单独核对')
    if any((run/name).exists() for name in ('desktop_unowned.json', 'solve_completed.json', 'result.json',
                                            'case/reports/Torque Plots.csv')):
        raise ValueError('存在未知会话或已写出的结果，需先核对原始记录')

    controller = read(folder/'controller.json')
    if (controller['batch_id'] != folder.name or controller['token'] != state['controller_token']
            or read(reservation) != controller):
        raise ValueError('批量控制器归属与保留记录不符')
    identities = [controller['identity'], controller['launcher_identity'],
                  *_execution_identities(run, execution_state, read(queue))]
    require_dead(identities)
    return dict(version=VERSION, id='interruption_'+uuid.uuid4().hex[:12],
                batch_id=folder.name, execution_id=execution, item_id=item['id'],
                recorded_utc=datetime.now(timezone.utc).isoformat(),
                batch_manifest_sha256=sha((folder/'manifest.json').read_bytes()),
                execution_manifest_sha256=sha((run/'manifest.json').read_bytes()), identities=identities,
                original_batch_state=(folder/'state.json').read_bytes().decode('utf8'),
                original_execution_state=(run/'state.json').read_bytes().decode('utf8'),
                original_batch_owner=reservation.read_bytes().decode('utf8'),
                original_queue_owner=queue.read_bytes().decode('utf8'),
                native_commands=0, new_native_solves=0)


def _apply(manager, folder, reservation, queue, receipt):
    run = manager.chain.execution.path(receipt['execution_id'])
    if (receipt['version'] != VERSION or receipt['batch_id'] != folder.name
            or receipt['batch_manifest_sha256'] != sha((folder/'manifest.json').read_bytes())
            or receipt['execution_manifest_sha256'] != sha((run/'manifest.json').read_bytes())):
        raise ValueError('中断记录与当前冻结任务不符')
    recovered_at = receipt['recorded_utc']
    before_batch = json.loads(receipt['original_batch_state'])
    before_run = json.loads(receipt['original_execution_state'])
    controller = read(folder/'controller.json')
    if (controller != json.loads(receipt['original_batch_owner']) or controller['batch_id'] != folder.name
            or controller['token'] != before_batch['controller_token'] or before_batch['id'] != folder.name
            or before_batch['current_item'] != receipt['item_id'] or before_run['id'] != run.name
            or run.name not in before_batch['new_execution_ids']):
        raise ValueError('归档的批量/执行身份不符')
    identities = [controller['identity'], controller['launcher_identity'],
                  *_execution_identities(run, before_run, json.loads(receipt['original_queue_owner']))]
    if identities != receipt['identities']:
        raise ValueError('归档的专用进程身份改变')
    require_dead(identities)
    after_run = dict(before_run, status='failed', phase='failed',
                     error='专用会话已退出。中断现场已归档，未形成完整结果。', interruption_recovery=receipt['id'])
    after_run.setdefault('finished_utc', recovered_at)
    after_batch = dict(before_batch, status='completed_with_failures', current_item=None, updated_utc=recovered_at,
                       error='中断任务已归档。完整结果仍可查看。', interruption_recovery=receipt['id'], items=[])
    for row in before_batch['items']:
        if row['id'] == receipt['item_id']:
            row = dict(row, status='failed', error=after_run['error'])
        elif row['status'] not in ('completed','reused_result','failed','blocked'):
            row = dict(row, status='blocked', error='批量中断时本条尚未下发。')
        after_batch['items'].append(row)

    states = [(run/'state.json', before_run, after_run), (folder/'state.json', before_batch, after_batch)]
    owners = [(queue, folder/'interrupted_queue_owner.json', receipt['original_queue_owner']),
              (reservation, folder/'interrupted_batch_owner.json', receipt['original_batch_owner'])]
    for path, before, after in states:
        if read(path) not in (before, after):
            raise ValueError('待归档任务状态已变化，未继续')
    for path, archived, original in owners:
        if path.exists() == archived.exists() or (path if path.exists() else archived).read_bytes() != original.encode('utf8'):
            raise ValueError('队列归属文件已变化，未继续')
    for path, _, after in states:
        if read(path) != after:
            write_json(path, after)
    for path, archived, _ in owners:
        if path.exists():
            path.rename(archived)
    write_json(folder/'interruption_applied.json', dict(id=receipt['id'], execution_id=run.name,
                                                       native_commands=0, new_native_solves=0))


def settle_interruption(manager, artifact):
    folder = manager.path(artifact).resolve()
    if folder.parent != manager.storage:
        raise ValueError('中断批量目录超出当前存储')
    with ExitStack() as stack:
        window = stack.enter_context(DispatchWindow(manager.storage.parent, recover_batch=artifact))
        motor = manager.chain.preparation.motor
        for path in (manager.storage/'controller.lease', manager.chain.execution.storage/'dispatch.lease',
                     motor.storage/'queue.guard', manager.chain.native.storage/'dispatch.lease',
                     manager.chain.native.storage/'execution.lease'):
            lease = QueueLease(path)
            if not lease.acquire():
                raise ValueError('存在活动执行租约，保持中断现场')
            stack.callback(lease.release)
        if (folder/'interruption_applied.json').exists():
            return manager.get(artifact)
        journal = folder/JOURNAL
        queue = motor.storage/'queue.lock'
        if journal.exists():
            receipt = read(journal)
        else:
            receipt = _capture(manager, folder, window.reservation, queue)
            write_json(journal, receipt)
        _apply(manager, folder, window.reservation, queue, receipt)
        return manager.get(artifact)
