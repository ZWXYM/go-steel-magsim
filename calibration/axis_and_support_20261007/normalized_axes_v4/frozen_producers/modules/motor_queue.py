"""OS-held queue lease and conservative process ownership checks.

Never terminate processes. A dead web server does not imply a dead solver.
"""
from __future__ import annotations

import json
import math
import os
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
