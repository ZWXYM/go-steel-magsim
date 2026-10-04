"""Audit complete legacy-loop ensembles without rewriting labels or dropping grains."""
import argparse
import json
import sys
from pathlib import Path

import numpy as np

PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT))
from modules.material_calibration import file_hash
from tools.analyze_sampling_convergence import read_ensemble
from tools.probe_loop_stability import table_metrics
from tools.run_calibration_pilot import write_csv,write_json


def audit(run_dirs,output):
    rows,sources=[],[]
    for run in run_dirs:
        _,source=read_ensemble(run,'B30P105','TD')
        manifest=json.loads((run/'manifest.json').read_text(encoding='utf-8'))
        local=[]
        for job in manifest['jobs']:
            if job['grade']!='B30P105' or job['direction']!='TD':
                continue
            metric,points=table_metrics(run/job['output']/'table.txt')
            row={k:v for k,v in metric.items() if not isinstance(v,list)}
            row.update(manifest_seed=source['seed'],grain_id=job['grain_id'],
                table_sha256=file_hash(run/job['output']/'table.txt'),script_sha256=job['script_sha256'],
                max_abs_mz=max(abs(p['mz']) for p in points),
                endpoint_screen_failed=metric['min_signed_endpoint_m_projection']<.95,
                inversion_screen_failed=metric['max_branch_inversion_error_T']>.02)
            rows.append(row)
            local.append(row)
        sources.append(dict(source=source, manifest_sha256=file_hash(run/'manifest.json'),
            run_status_sha256=file_hash(run/'run_status.json'),
            endpoint_failed=sum(r['endpoint_screen_failed'] for r in local),
            inversion_failed=sum(r['inversion_screen_failed'] for r in local),
            low_guard_but_endpoint_failed=sum(r['endpoint_screen_failed'] and r['max_guard_change_T']<=.01 for r in local),
            low_guard_but_inversion_failed=sum(r['inversion_screen_failed'] and r['max_guard_change_T']<=.01 for r in local)))
    if output.exists():
        raise ValueError('Preserve earlier audit; use a new directory')
    output.mkdir(parents=True)
    write_csv(output/'raw_state_summary.csv',rows)
    report=dict(status='posthoc_raw_state_audit_no_exclusion',analysis_sha256=file_hash(Path(__file__)),
        metrics_producer_sha256=file_hash(PROJECT/'tools/probe_loop_stability.py'),
        rows=len(rows),sources=sources,all_grains_retained=True,source_tables_modified=False,
        screen=dict(min_signed_endpoint_m_projection=.95,max_branch_inversion_error_T=.02,low_guard_T=.01),
        limitations=['Screen thresholds locate numerical/path problems, not material accuracy certification',
            'Original tables have no residual torque telemetry; missing values remain unknown',
            'Signed endpoint checks use both endpoints of both branches',
            'Inversion compares B_desc(H) + B_asc(-H); H0 is not artificially zeroed here'])
    write_json(output/'summary.json',report)
    print(json.dumps(dict(rows=len(rows),counts=[{k:v for k,v in s.items() if k not in ('source','manifest_sha256','run_status_sha256')} for s in sources]),indent=2))
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run-dir',action='append',type=Path,required=True)
    p.add_argument('--output-dir',type=Path,required=True)
    a=p.parse_args()
    audit(a.run_dir,a.output_dir)
