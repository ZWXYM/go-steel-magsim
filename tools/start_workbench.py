"""Start the existing platform and the workbench at an explicit local port."""
import argparse
import hashlib
import json
import os
import sys
import threading
import time
import uuid
import webbrowser
from pathlib import Path

PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT))
from modules.system_runtime import (default_root,runtime_identity,choose_endpoint,
    read_listener_identity,same_runtime,IDENTITY_KEYS)

ENDPOINT_HINT_SCHEMA='go_steel_endpoint_hints_v1'


def endpoint_hint_path(project):
    return Path(project).resolve()/'data/system_workbench/.launcher_endpoint_hints_v1.json'


def endpoint_hint_key(expected):
    return hashlib.sha256(json.dumps([expected[k] for k in IDENTITY_KEYS],
        separators=(',',':'),ensure_ascii=False).encode('utf8')).hexdigest()


def _endpoint_hints(project):
    path=endpoint_hint_path(project)
    try:
        if not path.resolve().is_relative_to(Path(project).resolve()) or path.stat().st_size>65536:return {}
        data=json.loads(path.read_text(encoding='utf8'))
        return data['entries'] if data['schema']==ENDPOINT_HINT_SCHEMA and isinstance(data['entries'],dict) else {}
    except (OSError,ValueError,KeyError,TypeError):return {}


def choose_endpoint_with_hint(project,port,expected,*,fallback=True):
    """A hint only locates a server; its full live identity still decides reuse."""
    try:
        entry=_endpoint_hints(project).get(endpoint_hint_key(expected),{})
        if not isinstance(entry,dict):entry={}
        hint=entry.get('port')
        if (isinstance(hint,int) and not isinstance(hint,bool) and isinstance(port,int)
                and port<=hint<min(65536,port+(128 if fallback else 1))
                and same_runtime(read_listener_identity(hint),expected)):
            return dict(port=hint,reuse=True,skipped_ports=[])
    except (ValueError,KeyError,TypeError):pass
    return choose_endpoint(port,expected,fallback=fallback)


def save_endpoint_hint(project,expected,port):
    """Best-effort local discovery metadata; never a source of task state."""
    path=endpoint_hint_path(project);temporary=None
    try:
        if (not isinstance(port,int) or isinstance(port,bool) or not 1<=port<=65535
                or Path(expected['project_path']).resolve()!=Path(project).resolve()
                or not path.resolve().is_relative_to(Path(project).resolve())):return False
        entries=_endpoint_hints(project);key=endpoint_hint_key(expected)
        entries.pop(key,None);entries[key]=dict(port=port,updated_at=time.time())
        entries=dict(list(entries.items())[-32:])
        path.parent.mkdir(parents=True,exist_ok=True)
        temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
        temporary.write_text(json.dumps(dict(schema=ENDPOINT_HINT_SCHEMA,entries=entries),
            ensure_ascii=False,indent=2),encoding='utf8')
        temporary.replace(path);return True
    except (OSError,ValueError,KeyError,TypeError):return False
    finally:
        if temporary is not None:
            try:temporary.unlink(missing_ok=True)
            except OSError:pass

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=5001)
    parser.add_argument('--check-config',action='store_true',help='Print resolved paths and modes as JSON; do not open a browser, bind a port or start the application')
    parser.add_argument('--open-browser',action='store_true')
    parser.add_argument('--cpu-only',action='store_true',help='Block solver/training submissions and automatic motor queue resume')
    parser.add_argument('--root',type=Path,help='Directory containing the calibration and motor resources')
    parser.add_argument('--storage',type=Path,help='Persistent workbench record directory')
    parser.add_argument('--page',choices=('system','workbench'),default='system')
    parser.add_argument('--no-fallback',action='store_true',help='Report an occupied port instead of choosing another')
    parser.add_argument('--resume-queue',action='store_true',help='Explicitly allow automatic queue recovery at startup')
    args=parser.parse_args()
    if args.cpu_only:
        os.environ['MAGSIM_CPU_ONLY']='1'
    root=Path(args.root or os.environ.get('GO_STEEL_THESIS_ROOT') or default_root(PROJECT)).resolve()
    storage=Path(args.storage or os.environ.get('MAGSIM_WORKBENCH_DIR') or PROJECT/'data/system_workbench').resolve()
    os.environ['GO_STEEL_THESIS_ROOT']=str(root)
    os.environ['MAGSIM_WORKBENCH_DIR']=str(storage)
    os.environ.setdefault('MAGSIM_AUTO_RESUME_QUEUE','0')
    if args.resume_queue:
        os.environ['MAGSIM_AUTO_RESUME_QUEUE']='1'
    if args.check_config:
        print(json.dumps(dict(project_path=str(PROJECT),root_path=str(root),storage_path=str(storage),
            cpu_only=os.environ.get('MAGSIM_CPU_ONLY')=='1',
            auto_resume_queue=os.environ.get('MAGSIM_AUTO_RESUME_QUEUE')=='1',
            requested_port=args.port,page=args.page,read_only=True,application_started=False,
            scope='Resolved launch configuration only; no license checkout or native readiness certification'),
            ensure_ascii=False,indent=2),flush=True)
        raise SystemExit(0)
    identity=runtime_identity(PROJECT,root,storage)
    try:
        endpoint=choose_endpoint_with_hint(PROJECT,args.port,identity,fallback=not args.no_fallback)
    except ValueError as error:
        parser.error(str(error))
    url=f"http://127.0.0.1:{endpoint['port']}/{args.page}"
    print(f'System: {url}\nResources: {root}\nRecords: {storage}',flush=True)
    if endpoint['skipped_ports']:
        print('Existing listeners preserved: '+', '.join(map(str,endpoint['skipped_ports'])),flush=True)
    if endpoint['reuse']:
        print('Reusing the same source version, workspace and resource mode.',flush=True)
        if args.open_browser:
            webbrowser.open(url)
        raise SystemExit(0)
    os.chdir(PROJECT)
    native_dir=root/'.runtime/mumax3'
    if (native_dir/'mumax3.exe').is_file():
        os.environ['PATH']=str(native_dir)+os.pathsep+os.environ.get('PATH','')
    from app import app
    save_endpoint_hint(PROJECT,identity,endpoint['port'])
    if args.open_browser:
        threading.Timer(1,lambda:webbrowser.open(url)).start()
    app.run(host='127.0.0.1',port=endpoint['port'],debug=False,use_reloader=False,threaded=True)
