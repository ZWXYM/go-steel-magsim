"""Start the existing platform and the workbench at an explicit local port."""
import argparse
import os
import sys
import threading
import webbrowser
import json
import urllib.request
from pathlib import Path

PROJECT=Path(__file__).resolve().parents[1]
os.chdir(PROJECT)
native_dir=PROJECT.parent/'.runtime/mumax3'
if (native_dir/'mumax3.exe').is_file():
    os.environ['PATH']=str(native_dir)+os.pathsep+os.environ.get('PATH','')
sys.path.insert(0,str(PROJECT))

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=5001)
    parser.add_argument('--open-browser',action='store_true')
    args=parser.parse_args()
    print(f'Workbench: http://127.0.0.1:{args.port}/workbench',flush=True)
    if args.open_browser:
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{args.port}/api/workbench/samples',timeout=2) as response:
                data=json.load(response)
            if data.get('calibration_version') and len(data.get('samples',[]))==4:
                webbrowser.open(f'http://127.0.0.1:{args.port}/workbench')
                raise SystemExit(0)
        except (OSError,ValueError):
            pass
    from app import app
    if args.open_browser:
        threading.Timer(1,lambda:webbrowser.open(f'http://127.0.0.1:{args.port}/workbench')).start()
    app.run(host='127.0.0.1',port=args.port,debug=False,use_reloader=False,threaded=True)
