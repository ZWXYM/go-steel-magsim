"""Identify the actual workspace/server without replacing or stopping a listener."""
from __future__ import annotations

import hashlib
import json
import os
import socket
import urllib.request
from datetime import datetime,timezone
from pathlib import Path

SCHEMA='go_steel_system_runtime_v1'
IDENTITY_KEYS=('schema','project_path','root_path','storage_path','cpu_only','auto_resume_queue','source_fingerprint')


def default_root(project):
    project=Path(project).resolve()
    return project.parent if project.name=='magsim' and (project.parent/'calibration/pilot_20261003_n8/manifest.json').is_file() else project


def source_manifest(project):
    project=Path(project).resolve()
    files=[project/'app.py',project/'tools/start_workbench.py',project/'tools/motor_scan_worker.py']
    for folder,pattern in [('modules','*.py'),('templates','*.html'),('static/js','*.js')]:
        files.extend((project/folder).rglob(pattern))
    result={}
    for path in sorted(set(files)):
        if not path.is_file() or '__pycache__' in path.parts:
            continue
        if not path.resolve().is_relative_to(project):
            raise ValueError('程序源文件位于当前工程目录之外')
        result[path.relative_to(project).as_posix()]=hashlib.sha256(path.read_bytes()).hexdigest()
    encoded=json.dumps(result,sort_keys=True,separators=(',',':')).encode()
    return result,hashlib.sha256(encoded).hexdigest()


def runtime_identity(project,root,storage,*,cpu_only=None,auto_resume_queue=None):
    manifest,fingerprint=source_manifest(project)
    return dict(schema=SCHEMA,project_path=str(Path(project).resolve()),root_path=str(Path(root).resolve()),
        storage_path=str(Path(storage).resolve()),source_fingerprint=fingerprint,source_file_count=len(manifest),
        cpu_only=os.environ.get('MAGSIM_CPU_ONLY')=='1' if cpu_only is None else cpu_only,
        auto_resume_queue=os.environ.get('MAGSIM_AUTO_RESUME_QUEUE','1')=='1' if auto_resume_queue is None else auto_resume_queue,
        started_utc=datetime.now(timezone.utc).isoformat(),process_id=os.getpid(),
        identity_scope='Source snapshot at startup; later file edits do not reload this process')


def same_runtime(actual,expected):
    return isinstance(actual,dict) and all(key in actual and actual[key]==expected[key] for key in IDENTITY_KEYS)


def read_listener_identity(port):
    try:
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f'http://127.0.0.1:{port}/api/workbench/runtime',timeout=1) as response:
            if response.status!=200:
                return None
            payload=response.read(65537)
            if len(payload)>65536:
                return None
            return json.loads(payload)
    except (OSError,ValueError):
        return None


def port_is_free(port):
    with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as sock:
        try:
            sock.bind(('127.0.0.1',port))
            return True
        except OSError:
            return False


def choose_endpoint(port,expected,*,fallback=True,max_ports=20):
    if not isinstance(port,int) or not 1<=port<=65535:
        raise ValueError('端口需为 1–65535')
    skipped=[]
    candidates=range(port,min(65536,port+(max_ports if fallback else 1)))
    for candidate in candidates:
        if port_is_free(candidate):
            return dict(port=candidate,reuse=False,skipped_ports=skipped)
        if same_runtime(read_listener_identity(candidate),expected):
            return dict(port=candidate,reuse=True,skipped_ports=skipped)
        skipped.append(candidate)
    raise ValueError('目标端口已由其他版本或应用使用；原进程保留。请用 --port 指定其他端口')
