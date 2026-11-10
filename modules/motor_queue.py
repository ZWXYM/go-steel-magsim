"""OS-held queue lease and conservative process ownership checks.

Never terminate processes. A dead web server does not imply a dead solver.
"""
from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path

import psutil


def process_identity(pid=None):
    process=psutil.Process(pid or os.getpid())
    return dict(pid=process.pid,create_time=process.create_time())


def identity_status(identity):
    try:
        pid=identity['pid']
        if not isinstance(pid,int) or isinstance(pid,bool) or pid<=0:
            return 'unknown'
        process=psutil.Process(pid)
        # Legacy PID-only locks cannot distinguish reuse from a live owner.
        if 'create_time' not in identity:
            return 'unknown'
        created=float(identity['create_time'])
        if not math.isfinite(created):
            return 'unknown'
        return 'alive' if abs(process.create_time()-created)<.01 else 'dead'
    except (psutil.NoSuchProcess,psutil.ZombieProcess):
        return 'dead'
    except (KeyError,TypeError,ValueError,psutil.AccessDenied):
        return 'unknown'


def require_native_pid(native_desktop, expected_pid):
    """Read the server PID; a client-side requested PID is not an attachment proof."""
    if type(expected_pid) is not int or expected_pid<=0:
        raise ValueError('原生会话PID无效')
    try:
        actual=native_desktop.GetProcessID()
    except Exception as error:
        raise ValueError('无法从AEDT核对实际会话PID；未继续操作') from error
    if type(actual) is not int or actual!=expected_pid:
        raise ValueError(f'AEDT实际会话PID与已核对会话不符：期望{expected_pid}，实际{actual}；未继续操作')
    return actual


def owned_rot_moniker_matches(name,version,pid):
    """Match one PID/version, never the global active Desktop or a PID prefix."""
    if type(pid) is not int or pid<=0 or not isinstance(version,str) or not re.fullmatch(r'\d{4}\.\d+',version):
        raise ValueError('停止目标PID或版本无效')
    return isinstance(name,str) and bool(re.search(r'(?:^|!)(?:Ansoft\.)?ElectronicsDesktop\.'+re.escape(version)+r'\.\d+:'+str(pid)+r'$',name))


class OwnedNativeStopClient:
    """A stop-only client; it does not save, close, kill or adopt a Desktop."""
    def __init__(self,native,identity,moniker):
        self.odesktop=native;self.identity=dict(identity);self.aedt_process_id=identity['pid']
        self.attachment=dict(method='Windows_ROT_exact_PID',moniker=moniker)
        require_native_pid(native,self.aedt_process_id)

    def stop_simulations(self,clean_stop=True):
        if clean_stop is not True:raise ValueError('预算停止仅允许clean_stop=True')
        if identity_status(self.identity)!='alive':raise ValueError('停止目标PID/创建时间已改变，未停止')
        require_native_pid(self.odesktop,self.aedt_process_id)
        return self.odesktop.StopSimulations(True)


def connect_owned_stop_client(identity,version):
    """Bind an exact Windows ROT moniker without SDK global-session fallback."""
    owned_rot_moniker_matches('',version,identity.get('pid'))
    if identity_status(identity)!='alive':raise ValueError('专用停止目标身份不可确认，未连接')
    if os.name!='nt':raise ValueError('当前环境不支持Windows确切会话停止连接')
    import pythoncom
    from ansys.aedt.core.internal.clr_module import win32_client
    pythoncom.CoInitialize();context=pythoncom.CreateBindCtx(0);table=pythoncom.GetRunningObjectTable();selected=[]
    for moniker in table.EnumRunning():
        try:name=moniker.GetDisplayName(context,moniker)
        except pythoncom.com_error:continue
        if owned_rot_moniker_matches(name,version,identity['pid']):selected.append((moniker,name))
    if len(selected)!=1:raise ValueError('确切PID停止目标缺失或不唯一，未回退到其他会话')
    native=win32_client.Dispatch(table.GetObject(selected[0][0]).QueryInterface(pythoncom.IID_IDispatch))
    client=OwnedNativeStopClient(native,identity,selected[0][1])
    if identity_status(identity)!='alive':raise ValueError('停止连接期间专用进程身份改变')
    return client


def native_transport_setting():
    requested=os.environ.get('MAGSIM_AEDT_TRANSPORT','auto')
    if requested not in ('auto','grpc','com'):raise ValueError('AEDT连接方式必须为auto、grpc或com')
    return requested


def configure_native_transport(requested):
    if requested not in ('auto','grpc','com'):raise ValueError('AEDT连接方式必须为auto、grpc或com')
    if requested!='auto':
        from ansys.aedt.core.generic.settings import settings
        settings.use_grpc_api=requested=='grpc'
    return requested


def bind_new_native_session(desktop, existing_pids):
    """Bind a newly created COM/gRPC server, including COM clients with no PID field."""
    client_pid=desktop.aedt_process_id
    if client_pid is not None and (type(client_pid) is not int or client_pid<=0 or client_pid in existing_pids):
        raise ValueError('没有确认新专用AEDT进程，未操作已有会话')
    try:actual=desktop.odesktop.GetProcessID()
    except Exception as error:raise ValueError('无法读取新AEDT的实际PID') from error
    if (type(actual) is not int or actual<=0 or actual in existing_pids
            or client_pid is not None and client_pid!=actual):
        raise ValueError('新AEDT实际PID无效、属于已有会话或与客户端不符')
    if list(desktop.odesktop.GetProjectList()):raise ValueError('新专用AEDT含有未预期工程，未继续操作')
    identity=process_identity(actual)
    if client_pid is None:desktop.aedt_process_id=actual
    require_native_pid(desktop.odesktop,actual)
    return identity


class QueueLease:
    """File stays on disk; OS releases its byte lock after process exit."""
    def __init__(self,path):
        self.path=Path(path)
        self.stream=None

    def acquire(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        stream=self.path.open('a+b')
        if stream.tell()==0:
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            stream.close()
            return False
        self.stream=stream
        return True

    def release(self):
        if self.stream is not None:
            self.stream.seek(0)
            try:
                if os.name=='nt':
                    import msvcrt
                    msvcrt.locking(self.stream.fileno(),msvcrt.LK_UNLCK,1)
                else:
                    import fcntl
                    fcntl.flock(self.stream.fileno(),fcntl.LOCK_UN)
            finally:
                self.stream.close()
                self.stream=None


def active_processes(storage,jobs):
    """Scope workers to this storage and Desktop sessions to saved identities."""
    storage=Path(storage).resolve()
    active=[]
    unresolved=False
    for job in jobs:
        folder=storage/job['id']
        records=list(folder.glob('case_*/desktop_session.json'))
        if job['status'] in ('starting','running'):
            records.append(folder/'dispatch.json')
        for record in records:
            if record.exists():
                try:
                    identity=json.loads(record.read_text(encoding='utf-8'))['identity']
                    status=identity_status(identity)
                except (KeyError,ValueError,OSError):
                    status='unknown'
                if status!='dead':
                    active.append(dict(job=job['id'],record=record.name,status=status))
        if job['status'] in ('starting','running'):
            if not (folder/'dispatch.json').exists() or any(
                    c['status']=='running' and not (folder/c['id']/'desktop_session.json').exists()
                    for c in job.get('cases',[])):
                unresolved=True
    # Covers the launch -> dispatch-record gap, without reading unrelated args.
    if not any(j['status'] in ('starting','running') for j in jobs) and not (storage/'queue.lock').exists():
        return active
    desktops=[]
    for process in psutil.process_iter(['pid','name']):
        try:
            name=(process.info['name'] or '').lower()
            if name in ('ansysedt.exe','ansysedt'):
                desktops.append(process.pid)
            if name not in ('python.exe','python','python3','pythonw.exe'):
                continue
            args=process.cmdline()
            if not any(Path(a).name=='motor_scan_worker.py' for a in args):
                continue
            if '--job-dir' not in args:
                active.append(dict(status='unknown',record='worker_without_job_dir'))
                continue
            directory=Path(args[args.index('--job-dir')+1]).resolve()
            if directory.parent==storage:
                active.append(dict(job=directory.name,status='alive',record='worker_process'))
        except (psutil.NoSuchProcess,psutil.ZombieProcess):
            continue
        except (psutil.AccessDenied,IndexError,OSError):
            if any(j['status'] in ('starting','running') for j in jobs):
                active.append(dict(status='unknown',record='process_ownership_unreadable'))
    if unresolved and desktops:
        active.append(dict(status='unknown',record='legacy_unresolved_Desktop',pids=desktops))
    return active


class DispatchWindow:
    """Serialize submission and respect a persistent calibrated-batch owner.

    This is a short handoff lock, never held for a whole solve. Ordinary scan
    submissions can still queue while another ordinary scan is running.
    """
    def __init__(self, storage, recover_batch=None):
        self.storage = Path(storage).resolve()
        self.recover_batch = recover_batch
        self.lease = QueueLease(self.storage/'native_dispatch.guard')

    @property
    def reservation(self):
        return self.storage/'calibrated_motor_batches/active_controller.json'

    def __enter__(self):
        if not self.lease.acquire():
            raise ValueError('另一项原生任务正在交接，请稍后再试')
        try:
            for path in (self.storage/'native_material_runs').glob('nativerun_*/state.json'):
                state=json.loads(path.read_text(encoding='utf8'))
                if not isinstance(state,dict) or 'status' not in state:raise ValueError('材料仿真状态暂不可读，请先检查该任务')
                if state.get('status') in ('starting','running'):
                    raise ValueError('材料MuMax仿真未结束；请先查看或暂停材料任务，未下发其他原生任务')
            if self.reservation.exists():
                owner = json.loads(self.reservation.read_text(encoding='utf8'))
                status = identity_status(owner['identity'])
                own = status == 'alive' and owner['identity'] == process_identity()
                recover = self.recover_batch is not None and status == 'dead' and owner.get('batch_id') == self.recover_batch
                if not own and not recover:
                    raise ValueError('校准材料批量任务占用派发；请在批量页查看或恢复，未下发其他原生任务')
            return self
        except Exception:
            self.lease.release()
            raise

    def __exit__(self, *args):
        self.lease.release()
