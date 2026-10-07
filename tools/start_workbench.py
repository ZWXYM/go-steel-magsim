"""Start the existing platform and the workbench at an explicit local port."""
import argparse
import os
import sys
import threading
import webbrowser
from pathlib import Path

PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT))
from modules.system_runtime import default_root,runtime_identity,choose_endpoint

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=5001)
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
    identity=runtime_identity(PROJECT,root,storage)
    try:
        endpoint=choose_endpoint(args.port,identity,fallback=not args.no_fallback)
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
    if args.open_browser:
        threading.Timer(1,lambda:webbrowser.open(url)).start()
    app.run(host='127.0.0.1',port=endpoint['port'],debug=False,use_reloader=False,threaded=True)
