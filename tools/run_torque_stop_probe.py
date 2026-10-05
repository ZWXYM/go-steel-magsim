"""Fixed, bounded numerical probe of four previously identified large-H0 grains."""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from modules.material_calibration import file_hash
from modules.native_loop_diagnostics import audit_table, initial_state_script
from modules.native_state_controls import audit_control
from tools.run_calibration_pilot import H_GRID, resolve_mumax, write_json
from tools.run_initial_state_controls import snapshot

VERSION = 'large_H0_stop_probe_v1'
CASES = [('B23R075','RD',8), ('B27R090','TD',2), ('B30P105','RD',7), ('B30P105','TD',5)]
STOPS = [('strict_dm_1e7',1e-7), ('tight_dm_1e8',1e-8)]
PRODUCERS = ('tools/run_torque_stop_probe.py', 'tools/run_initial_state_controls.py',
    'modules/native_state_controls.py','modules/native_loop_diagnostics.py',
    'modules/material_calibration.py','tools/run_calibration_pilot.py')


def telemetry(path, stop):
    header = Path(path).read_text(encoding='utf-8').splitlines()[0].split('\t')
    data = np.loadtxt(path,skiprows=1,ndmin=2)
    columns = [i for i,text in enumerate(header) if text.strip().startswith('LastErr ')]
    if len(columns) != 1:
        raise ValueError('Expected unique LastErr telemetry')
    dm = data[:,columns[0]]
    if not np.all(np.isfinite(dm)) or np.any(dm<0):
        raise ValueError('Invalid magnetization-step telemetry')
    return dict(max_sampled_dM=float(dm.max()),stop_dM=stop,
        all_recorded_dM_below_stop=bool(dm.max()<=stop*(1+1e-6)),
        dM_is_not_torque=True)


def prepare(root, run, *, mumax=None):
    root,run = Path(root).resolve(),Path(run).resolve()
    pilot = root/'calibration/pilot_20261003_n8'
    if run.exists() or run==root or pilot in run.parents:
        raise ValueError('Preserve existing evidence; use a new run directory')
    manifest = json.loads((pilot/'manifest.json').read_text(encoding='utf-8'))
    runtime = json.loads((pilot/'run_status.json').read_text(encoding='utf-8'))
    binary = resolve_mumax(mumax)
    if file_hash(binary)!=runtime['mumax_binary_sha256']:
        raise ValueError('Use the frozen source MuMax binary')
    selected = []
    for grade,direction,grain in CASES:
        jobs = [j for j in manifest['jobs'] if (j['grade'],j['direction'],j['grain_id'])==(grade,direction,grain)]
        if len(jobs)!=1:
            raise ValueError('Expected unique frozen large-H0 case')
        job = jobs[0]
        done = [j for j in runtime['jobs'] if j['script']==job['script'] and j['exit_code']==0]
        if (len(done)!=1 or done[0]['script_sha256']!=job['script_sha256']
                or file_hash(pilot/job['script'])!=job['script_sha256']
                or file_hash(pilot/job['output']/'table.txt')!=done[0]['table_sha256']):
            raise ValueError('Native source identity changed')
        selected.append((job,done[0]))
    run.mkdir(parents=True)
    frozen,jobs = {},[]
    for job,done in selected:
        case = f"{job['grade']}_{job['direction']}_g{job['grain_id']:03d}"
        folder = run/'source_cases'/case
        folder.mkdir(parents=True)
        for path,name in ((pilot/job['script'],'original.mx3'),(pilot/job['output']/'table.txt','original_table.txt')):
            shutil.copy2(path,folder/name)
            frozen[(folder/name).relative_to(run).as_posix()] = file_hash(folder/name)
    # All four strict cases first, then all four tight cases. Stop on native/schema failure.
    for condition,stop in STOPS:
        for job,done in selected:
            case = f"{job['grade']}_{job['direction']}_g{job['grain_id']:03d}"
            original = (run/'source_cases'/case/'original.mx3').read_text(encoding='utf-8')
            script = initial_state_script(original,'strict_major_loop',H_GRID)
            assert script.count('MinimizerStop = 1e-7')==1
            script = script.replace('MinimizerStop = 1e-7',f'MinimizerStop = {stop:.12g}')
            script = script.replace('tableadd(maxTorque)','tableadd(maxTorque)\ntableadd(LastErr)')
            destination = run/'scripts'/case/(condition+'.mx3')
            destination.parent.mkdir(parents=True,exist_ok=True)
            destination.write_text(script,encoding='utf-8')
            jobs.append(dict(case_id=case,grade=job['grade'],direction=job['direction'],angle=job['angle'],
                grain_id=job['grain_id'],condition=condition,stop_dM=stop,
                script=destination.relative_to(run).as_posix(),script_sha256=file_hash(destination),
                output=f'raw/{case}/{condition}.out',source_script_sha256=job['script_sha256'],
                source_table_sha256=done['table_sha256']))
    for producer in PRODUCERS:
        destination = run/'frozen_producers'/Path(producer).name
        destination.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(PROJECT/producer,destination)
        frozen[destination.relative_to(run).as_posix()] = file_hash(destination)
    before = snapshot(pilot)
    write_json(run/'source_before.json',before)
    frozen['source_before.json'] = file_hash(run/'source_before.json')
    result = dict(protocol=VERSION,predeclared_utc=datetime.now(timezone.utc).isoformat(),
        selection='All four large-H0 grains identified before this study; no grain exclusions or reference fitting',
        cases=CASES,stops=STOPS,jobs=jobs,frozen_files=frozen,
        producer_sha256={p:file_hash(PROJECT/p) for p in PRODUCERS},
        source_manifest_sha256=file_hash(pilot/'manifest.json'),H_axis='physical_A_per_m',H_grid=H_GRID.tolist(),
        mumax_binary_sha256=file_hash(binary),timeout_seconds_per_job=60,maximum_attempts=8,parallel_jobs=1,
        gates=dict(min_signed_endpoint_m_projection=.95,max_branch_inversion_error_T=.02,max_residual_torque_T=1e-5),
        promotion_allowed=False,material_validation=False,
        stopping_criterion='MinimizerStop is sampled magnetization change, not maxTorque; telemetry recorded separately',
        upstream_source_commit='ee077035',
        source_links={n:f'https://github.com/mumax/3/blob/ee077035/engine/{n}.go' for n in ('minimizer','torque','relax')})
    write_json(run/'manifest.json',result)
    return result


def verify(run, manifest):
    if manifest['protocol']!=VERSION or len(manifest['jobs'])!=8:
        raise ValueError('Unexpected probe protocol/budget')
    for p,h in manifest['frozen_files'].items():
        target=(run/p).resolve()
        if not target.is_relative_to(run) or file_hash(target)!=h:
            raise ValueError('Frozen source changed')
    for job in manifest['jobs']:
        for field in ('script','output'):
            if not (run/job[field]).resolve().is_relative_to(run):
                raise ValueError('Probe path outside run')
        if file_hash(run/job['script'])!=job['script_sha256']:
            raise ValueError('Frozen script changed')
    for p,h in manifest['producer_sha256'].items():
        if file_hash(PROJECT/p)!=h:
            raise ValueError('Live producer changed; use a new study')


def execute(run,manifest,*,mumax=None):
    run=Path(run).resolve()
    verify(run,manifest)
    binary=resolve_mumax(mumax)
    if file_hash(binary)!=manifest['mumax_binary_sha256']:
        raise ValueError('MuMax binary changed')
    handle=os.open(run/'runner.lock',os.O_WRONLY|os.O_CREAT|os.O_EXCL)
    try:
        with os.fdopen(handle,'w',encoding='utf-8') as stream:
            json.dump(dict(pid=os.getpid()),stream)
        destination=run/'run_status.json'
        if destination.exists() or any((run/j['output']).exists() for j in manifest['jobs']):
            raise ValueError('Never retry an existing attempt')
        status=dict(protocol=VERSION,manifest_sha256=file_hash(run/'manifest.json'),jobs=[])
        write_json(destination,status)
        for job in manifest['jobs']:
            output=run/job['output']
            output.parent.mkdir(parents=True,exist_ok=True)
            started=time.perf_counter()
            with (output.parent/(output.stem+'.log')).open('w',encoding='utf-8') as log:
                try:
                    proc=subprocess.run([str(binary),'-http=127.0.0.1:0','-o',str(output),str(run/job['script'])],
                        stdout=log,stderr=subprocess.STDOUT,cwd=PROJECT,timeout=manifest['timeout_seconds_per_job'])
                    code=proc.returncode
                except subprocess.TimeoutExpired:
                    code=-124
            table=output/'table.txt'
            result={**job,'exit_code':code,'elapsed_seconds':round(time.perf_counter()-started,3),
                'table_sha256':file_hash(table) if table.is_file() else None,
                'execution_kind':'native','status':'completed' if code==0 else ('timed_out' if code==-124 else 'native_failed')}
            if code==0:
                try:
                    result['metrics']={**audit_control(table,manifest['H_grid'],angle_deg=job['angle'],condition='strict_major_loop'),
                        **telemetry(table,job['stop_dM'])}
                except (ValueError,OSError) as error:
                    result.update(status='table_invalid',validation_error=str(error))
            status['jobs'].append(result)
            write_json(destination,status)
            print(job['case_id'],job['condition'],result['status'],result['elapsed_seconds'],flush=True)
            if result['status']!='completed':
                break
        return status
    finally:
        (run/'runner.lock').unlink()


def analyze(run):
    run=Path(run).resolve()
    manifest=json.loads((run/'manifest.json').read_text(encoding='utf-8'))
    verify(run,manifest)
    status=json.loads((run/'run_status.json').read_text(encoding='utf-8'))
    if status['manifest_sha256']!=file_hash(run/'manifest.json'):
        raise ValueError('Manifest identity changed')
    expected={j['script']:j for j in manifest['jobs']}
    seen,details=set(),[]
    for record in status['jobs']:
        if record['script'] not in expected or record['script'] in seen:
            raise ValueError('Unexpected execution record')
        seen.add(record['script'])
        job=expected[record['script']]
        if any(record.get(k)!=v for k,v in job.items()):
            raise ValueError('Runtime differs from frozen job')
        table=run/job['output']/'table.txt'
        if record['table_sha256'] is not None and file_hash(table)!=record['table_sha256']:
            raise ValueError('Native table changed')
        item={k:v for k,v in record.items() if k!='metrics'}
        if record['status']=='completed' and record['exit_code']==0:
            metric={**audit_control(table,manifest['H_grid'],angle_deg=job['angle'],condition='strict_major_loop'),
                **telemetry(table,job['stop_dM'])}
            if metric!=record['metrics']:
                raise ValueError('Runtime metric identity changed')
            old=audit_table(run/'source_cases'/job['case_id']/'original_table.txt',manifest['H_grid'],angle_deg=job['angle'])
            item['metrics']=metric
            item['original_metrics']={k:old[k] for k in ('native_unforced_midpoint_H0_T','B800_before_guard_T',
                'min_signed_endpoint_m_projection','max_branch_inversion_error_T','max_residual_torque_T')}
        details.append(item)
    report=dict(protocol=VERSION,manifest_sha256=file_hash(run/'manifest.json'),results=details,
        attempted_jobs=len(status['jobs']),successful_native_jobs=sum(j['exit_code']==0 for j in status['jobs']),
        complete_controls=sum(j['status']=='completed' for j in status['jobs']),
        unattempted_jobs=[j['script'] for j in manifest['jobs'] if j['script'] not in seen],
        model_changed=False,promotion_allowed=False,material_validation=False)
    destination=run/'analysis.json'
    if destination.exists():
        if json.loads(destination.read_text(encoding='utf-8'))!=report:
            raise ValueError('Existing analysis differs; preserve it')
    else:
        write_json(destination,report)
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=PROJECT.parent if PROJECT.name=='magsim' else PROJECT)
    parser.add_argument('--run-dir',type=Path,required=True)
    parser.add_argument('--prepare',action='store_true')
    parser.add_argument('--execute',action='store_true')
    parser.add_argument('--analyze',action='store_true')
    parser.add_argument('--mumax',type=Path)
    args=parser.parse_args()
    run=args.run_dir.resolve()
    manifest=prepare(args.root,run,mumax=args.mumax) if args.prepare else json.loads((run/'manifest.json').read_text(encoding='utf-8'))
    if args.execute:
        execute(run,manifest,mumax=args.mumax)
    if args.analyze:
        report=analyze(run)
        print(json.dumps({k:v for k,v in report.items() if k!='results'},indent=2))
