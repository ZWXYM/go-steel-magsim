"""CPU-only raw RD/TD exporter; see modules.native_pair_export."""
import argparse
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
PROJECT=ROOT/'magsim' if (ROOT/'magsim/modules').is_dir() else ROOT
sys.path.insert(0,str(PROJECT))
from modules.native_pair_export import export_pair,source_file

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir',required=True,type=Path)
    parser.add_argument('--grade',required=True)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    try:
        receipt=export_pair(args.run_dir,args.grade,args.output)
    except (ValueError,KeyError,OSError,StopIteration) as exc:
        parser.exit(2,'未导出原生对：'+str(exc)+'\n')
    print(json.dumps({k:v for k,v in receipt.items() if k!='directions'},ensure_ascii=False,indent=2))
