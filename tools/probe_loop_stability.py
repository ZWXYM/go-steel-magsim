"""Isolated, predeclared numerical loop probes; never production labels."""
from __future__ import annotations

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
from modules.material_calibration import MU0, extract_loop_midpoint, file_hash
from tools.run_calibration_pilot import H_GRID, resolve_mumax, write_csv, write_json

VERSION = 'isolated_loop_stability_probe_v3'
MSAT = 1.56e6
VOLUME = 4 * 4 * (4e-9)**2 * 1e-9
# Cases were named in the preceding checkpoint, including low-guard controls.
CASES = [(20261004, 69, 'large_guard'), (20261004, 115, 'large_guard'),
         (20261004, 82, 'branch_order'), (20261005, 90, 'endpoint_drop'),
         (20261005, 102, 'branch_order'), (20261006, 71, 'midpoint_drop'),
         (20261004, 53, 'low_guard_control'), (20261005, 22, 'low_guard_control'),
         (20261006, 62, 'low_guard_control')]
CONDITIONS = [
    dict(id='default_telemetry', solver='minimize', hmax=50000, reset=False),
    dict(id='strict_minimize', solver='minimize', hmax=50000, reset=False, stop=1e-7, samples=20),
    dict(id='strict_relax_fallback', solver='hybrid', hmax=50000, reset=False, stop=1e-7, samples=20),
    dict(id='strict_relax_reset', solver='hybrid', hmax=50000, reset=True, stop=1e-7, samples=20),
    dict(id='strict_relax_high_reset', solver='hybrid', hmax=200000, reset=True, stop=1e-7, samples=20),
]


def probe_script(original, condition):
    """Preserve the original geometry/material axes; change only declared loop controls."""
    if condition['solver'] == 'original':
        return original
    prefix = original.split('branch :=')[0]
    if 'H_max := 50000.0' not in prefix or prefix.count('minimize()') != 1:
        raise ValueError('Unsupported source presaturation script')
    prefix = prefix.replace('H_max := 50000.0', f"H_max := {condition['hmax']:.1f}")
    settings = ''
    if condition['solver'] in ('relax','hybrid'):
        settings += 'RelaxTorqueThreshold = -1\n'
    if 'stop' in condition:
        settings += f"MinimizerStop = {condition['stop']:.12g}\nMinimizerSamples = {condition['samples']}\n"
    call = condition['solver'] + '()'
    if condition['solver']=='hybrid':
        settings += 'fallback_count := 0.0\n'
        call=('minimize()\nif maxTorque.Get() > 1e-5 {\nrelax()\n'
              'fallback_count = fallback_count + 1\n}')
    prefix = prefix.replace('minimize()', settings + call)
    prefix += '\ntableadd(maxTorque)\n'
    if condition['solver']=='hybrid':
        prefix += 'tableaddvar(fallback_count, "fallback_count", "")\n'
    grid = H_GRID.copy()
    if condition['hmax'] > H_GRID[-1]:
        grid = np.r_[grid, 75000., 100000., 150000., condition['hmax']]
    signed = np.r_[-grid[:0:-1], grid]
    body = '\nbranch := -1.0\ntableaddvar(branch, "branch", "")\nH := 0.0\n'
    for label, schedule in [(-1, signed[::-1]), (1, signed)]:
        body += f'branch = {label}.0\n'
        if label == 1 and condition['reset']:
            body += ('B_ext = vector(-mu0*H_max*Hx_dir, -mu0*H_max*Hy_dir, -mu0*H_max*Hz_dir)\n'
                     'm = uniform(-Hx_dir, -Hy_dir, -Hz_dir)\n' + call + '\n')
        for h in schedule:
            body += (f'H = {h:.12g}\n'
                     'B_ext = vector(mu0*H*Hx_dir, mu0*H*Hy_dir, mu0*H*Hz_dir)\n'
                     + call + '\ntablesave()\n')
    return prefix + body


def verify_frozen(run, manifest):
    if manifest['protocol'] != VERSION:
        raise ValueError('Unsupported probe protocol')
    for row in manifest['frozen_files']:
        if file_hash(run / row['path']) != row['sha256']:
            raise ValueError('Frozen probe input changed: ' + row['path'])
    for job in manifest['jobs']:
        if file_hash(run / job['script']) != job['script_sha256']:
            raise ValueError('Frozen probe script changed')


def prepare(root, run):
    if run.exists():
        raise ValueError('Preserve previous probes; use a new directory')
    sources = []
    binary_hash = None
    for seed, grain, reason in CASES:
        source = root / f'calibration/runs/haar_v2_n128_seed{seed}'
        if (source / 'runner.lock').exists():
            raise ValueError('Source run is locked')
        manifest = json.loads((source/'manifest.json').read_text(encoding='utf-8'))
        status = json.loads((source/'run_status.json').read_text(encoding='utf-8'))
        if binary_hash is not None and binary_hash != status['mumax_binary_sha256']:
            raise ValueError('Source native binary differs')
        binary_hash = status['mumax_binary_sha256']
        job = next(j for j in manifest['jobs'] if j['grade']=='B30P105' and j['direction']=='TD' and j['grain_id']==grain)
        results = [j for j in status['jobs'] if j['script']==job['script'] and j['exit_code']==0]
        if len(results)!=1:
            raise ValueError('Source is incomplete or duplicate')
        result = results[0]
        table = source/job['output']/'table.txt'
        if (file_hash(source/job['script'])!=job['script_sha256'] or
                result['script_sha256']!=job['script_sha256'] or file_hash(table)!=result['table_sha256']):
            raise ValueError('Source native hashes differ')
        extract_loop_midpoint(table, H_GRID, angle_deg=90)
        sources.append((source, job, result, seed, grain, reason))
    run.mkdir(parents=True)
    cases, jobs, frozen = [], [], []
    for source, job, result, seed, grain, reason in sources:
        case_id = f's{seed}_g{grain:03d}'
        case_dir = run/'source_cases'/case_id
        case_dir.mkdir(parents=True)
        for src, name in [(source/job['script'],'original.mx3'),
                          (source/job['output']/'table.txt','original_table.txt')]:
            dest = case_dir/name
            shutil.copy2(src, dest)
            frozen.append(dict(path=dest.relative_to(run).as_posix(), sha256=file_hash(dest)))
        case = dict(id=case_id, manifest_seed=seed, material_seed=seed+3, grain_id=grain,
            selection_reason=reason, source_run_id=source.name, source_job=job,
            source_execution_kind=result.get('execution_kind','native'),
            source_manifest_sha256=file_hash(source/'manifest.json'),
            source_status_sha256=file_hash(source/'run_status.json'),
            source_table_sha256=result['table_sha256'], source_script_sha256=job['script_sha256'])
        cases.append(case)
        original = (case_dir/'original.mx3').read_text(encoding='utf-8')
        for condition in CONDITIONS:
            path = run/'scripts'/case_id/(condition['id']+'.mx3')
            path.parent.mkdir(parents=True, exist_ok=True)
            if condition['id']=='exact_repeat':
                path.write_bytes((case_dir/'original.mx3').read_bytes())
            else:
                path.write_bytes(probe_script(original, condition).encode('utf-8'))
            jobs.append(dict(case_id=case_id, condition=condition['id'], grade='B30P105',
                direction='TD', angle=90, grain_id=grain,
                script=path.relative_to(run).as_posix(), script_sha256=file_hash(path),
                output=f"raw/{case_id}/{condition['id']}.out"))
    manifest = dict(protocol=VERSION, predeclared_utc=datetime.now(timezone.utc).isoformat(),
        physics_version='cubic_sample_frame_v2', loop_protocol='isolated_diagnostic_not_production',
        H_axis='physical_A_per_m', common_H_grid=H_GRID.tolist(), conditions=CONDITIONS,
        cases=cases, jobs=jobs, frozen_files=frozen, mumax_binary_sha256=binary_hash,
        producer_sha256=file_hash(Path(__file__)), timeout_seconds_per_job=180,
        gates=dict(max_residual_torque_T=1e-5, max_branch_inversion_error_T=.02,
                   min_signed_endpoint_m_projection=.95, exact_repeat_max_B_difference_T=1e-5),
        promotion_allowed=False, selection='All 9 cases named in the preceding N128 checkpoint; no result-based exclusion',
        limitations=['Selected numerical probes are not a converged grain ensemble or independent material validation',
            'Low-guard controls are not presumed healthy; no reference curves used for choosing solver settings',
            'Relax/reset/high-field conditions are separate loop protocols, never mixed into the old bank',
            'No measured Hc, Br or loss can be inferred from these small-grid probes'])
    write_json(run/'manifest.json', manifest)
    return manifest


def execute(run, manifest, mumax, max_jobs=None):
    verify_frozen(run, manifest)
    binary = resolve_mumax(mumax)
    if file_hash(binary)!=manifest['mumax_binary_sha256']:
        raise ValueError('Probe binary differs from frozen native sources')
    if file_hash(Path(__file__))!=manifest['producer_sha256']:
        raise ValueError('Producer changed after predeclaration')
    lock = run/'runner.lock'
    handle = os.open(lock, os.O_CREAT|os.O_EXCL|os.O_WRONLY)
    try:
        with os.fdopen(handle,'w') as stream:
            json.dump(dict(pid=os.getpid(), started_utc=datetime.now(timezone.utc).isoformat()), stream)
        status_path = run/'run_status.json'
        status = json.loads(status_path.read_text(encoding='utf-8')) if status_path.exists() else dict(
            protocol=VERSION, manifest_sha256=file_hash(run/'manifest.json'),
            mumax_binary_sha256=file_hash(binary), jobs=[])
        if status['manifest_sha256']!=file_hash(run/'manifest.json') or status['mumax_binary_sha256']!=file_hash(binary):
            raise ValueError('Frozen runtime contract changed')
        done = {j['script']:j for j in status['jobs']}
        if len(done)!=len(status['jobs']):
            raise ValueError('Duplicate probe status records')
        count = 0
        for job in manifest['jobs']:
            output = run/job['output']
            if job['script'] in done:
                previous = done[job['script']]
                if (previous['exit_code']!=0 or previous['script_sha256']!=job['script_sha256'] or
                        file_hash(output/'table.txt')!=previous['table_sha256']):
                    raise ValueError('Previous probe failed or changed; preserve output and review')
                continue
            if max_jobs is not None and count>=max_jobs:
                break
            if output.exists():
                raise ValueError('Unverified output already exists; preserve and review')
            verify_frozen(run, manifest)
            output.parent.mkdir(parents=True,exist_ok=True)
            started=time.perf_counter()
            timed_out=False
            with output.with_suffix('.log').open('w',encoding='utf-8') as log:
                try:
                    p=subprocess.run([str(binary),'-http=127.0.0.1:0','-o',str(output),str(run/job['script'])],
                        cwd=PROJECT,stdout=log,stderr=subprocess.STDOUT,timeout=manifest['timeout_seconds_per_job'])
                    code=p.returncode
                except subprocess.TimeoutExpired:
                    # subprocess terminates only the process created by this call.
                    code=-124
                    timed_out=True
            table=output/'table.txt'
            result=dict(**job, exit_code=code, timed_out=timed_out, execution_kind='native',
                elapsed_seconds=round(time.perf_counter()-started,3), table_sha256=file_hash(table) if table.exists() else None)
            status['jobs'].append(result)
            write_json(status_path,status)
            print(f"{len(status['jobs'])}/{len(manifest['jobs'])} {job['case_id']} {job['condition']}: {code}, {result['elapsed_seconds']}s",flush=True)
            if code:
                raise RuntimeError('Probe failed; preserve output and inspect log')
            extract_loop_midpoint(table,H_GRID,angle_deg=90)
            count+=1
    finally:
        lock.unlink()


def table_metrics(table):
    header=Path(table).read_text(encoding='utf-8').splitlines()[0].split('\t')
    a=np.loadtxt(table,skiprows=1,ndmin=2)
    def column(name):
        matches=[i for i,v in enumerate(header) if v.strip().startswith(name+' ')]
        if not matches:
            raise ValueError('Missing column '+name)
        # Native MuMax adds mx/my/mz by default, and historic scripts also
        # tableadd(m). Accept only byte-numerically identical repeated data.
        for i in matches[1:]:
            if not np.array_equal(a[:,matches[0]],a[:,i]):
                raise ValueError('Conflicting duplicate column '+name)
        return matches[0]
    if not np.all(np.isfinite(a)):
        raise ValueError('Nonfinite native table')
    h=a[:,column('B_exty')]/MU0
    m=a[:,column('my')]
    b=MU0*(h+MSAT*m)
    branch=a[:,column('branch')]
    if set(branch)!=set((-1,1)):
        raise ValueError('Unexpected branch labels')
    desc=branch==-1
    asc=branch==1
    if np.any(np.diff(h[desc])>=0) or np.any(np.diff(h[asc])<=0):
        raise ValueError('Native branch schedule has duplicate/reversed fields')
    if abs(h[desc].max()-h[asc].max())>.01 or abs(h[desc].min()-h[asc].min())>.01:
        raise ValueError('Branches have different endpoint fields')
    # Compare opposite branches at opposite fields, including the unforced H=0.
    opposite=np.interp(-h[desc],h[asc],b[asc])
    sym=float(np.max(np.abs(b[desc]+opposite)))
    endpoints=[float(np.sign(h[mask][i])*m[mask][i]) for mask in (desc,asc) for i in (0,-1)]
    energy=a[:,column('E_total')]/VOLUME
    torque_col=[i for i,v in enumerate(header) if v.strip().startswith('maxTorque ')]
    torque=a[:,torque_col[0]] if len(torque_col)==1 else None
    extracted=extract_loop_midpoint(table,H_GRID,angle_deg=90)
    result=dict(max_branch_inversion_error_T=sym, min_signed_endpoint_m_projection=min(endpoints),
        signed_endpoint_m_projections=endpoints, max_energy_density_J_per_m3=float(energy.max()),
        max_residual_torque_T=float(torque.max()) if torque is not None else None,
        hmax_A_per_m=float(h.max()), rows=len(h),
        unforced_midpoint_H0_T=float((np.interp(0,h[desc][::-1],b[desc][::-1])+np.interp(0,h[asc],b[asc]))/2),
        B800_before_guard_T=float(np.interp(800,H_GRID,extracted['B_midpoint_before_guard'])),
        B800_after_guard_T=float(np.interp(800,H_GRID,extracted['B'])),
        max_guard_change_T=extracted['report']['max_guard_change_T'],
        common_midpoint_before_guard_T=extracted['B_midpoint_before_guard'],
        common_guarded_B_T=extracted['B'])
    points=[dict(row=i,branch=int(branch[i]),H_A_per_m=float(h[i]), B_T=float(b[i]),
        mx=float(a[i,column('mx')]),my=float(m[i]),mz=float(a[i,column('mz')]),
        energy_density_J_per_m3=float(energy[i]), residual_torque_T=float(torque[i]) if torque is not None else '') for i in range(len(h))]
    return result,points


def analyze(run, output):
    manifest=json.loads((run/'manifest.json').read_text(encoding='utf-8'))
    verify_frozen(run,manifest)
    if (run/'runner.lock').exists():
        raise ValueError('Probe run is locked')
    status=json.loads((run/'run_status.json').read_text(encoding='utf-8'))
    if status['manifest_sha256']!=file_hash(run/'manifest.json') or status['mumax_binary_sha256']!=manifest['mumax_binary_sha256']:
        raise ValueError('Probe runtime contract differs')
    done={j['script']:j for j in status['jobs'] if j['exit_code']==0}
    if len(done)!=len(manifest['jobs']) or len(done)!=len(status['jobs']):
        raise ValueError('Incomplete/duplicate/failed probe study')
    summaries,points=[],[]
    for case in manifest['cases']:
        source=run/'source_cases'/case['id']/'original_table.txt'
        baseline,base_points=table_metrics(source)
        for condition in ['source_original']+[c['id'] for c in manifest['conditions']]:
            table=source
            if condition!='source_original':
                job=next(j for j in manifest['jobs'] if j['case_id']==case['id'] and j['condition']==condition)
                record=done[job['script']]
                table=run/job['output']/'table.txt'
                if record['script_sha256']!=job['script_sha256'] or record['table_sha256']!=file_hash(table):
                    raise ValueError('Executed probe evidence changed')
            metric,rows=table_metrics(table)
            delta=np.asarray(metric['common_midpoint_before_guard_T'])-baseline['common_midpoint_before_guard_T']
            metric.update(case_id=case['id'],condition=condition,selection_reason=case['selection_reason'],
                table_sha256=file_hash(table),max_common_midpoint_change_from_source_T=float(np.abs(delta).max()),
                B800_change_from_source_T=float(np.interp(800,H_GRID,delta)))
            if condition in ('exact_repeat','default_telemetry'):
                if len(rows)!=len(base_points):
                    raise ValueError('Repeat field schedule differs')
                metric['max_raw_B_difference_from_source_T']=max(abs(r['B_T']-v['B_T']) for r,v in zip(rows,base_points))
            gates=manifest['gates']
            metric['numerical_screen_passed']=(metric['max_branch_inversion_error_T']<=gates['max_branch_inversion_error_T'] and
                metric['min_signed_endpoint_m_projection']>=gates['min_signed_endpoint_m_projection'] and
                metric['max_residual_torque_T'] is not None and metric['max_residual_torque_T']<=gates['max_residual_torque_T'])
            summaries.append(metric)
            points.extend(dict(case_id=case['id'],condition=condition,**row) for row in rows)
    if output.exists():
        raise ValueError('Preserve earlier analysis; use a new directory')
    output.mkdir(parents=True)
    write_csv(output/'native_points.csv',points)
    write_csv(output/'probe_summary.csv',({k:v for k,v in row.items() if not isinstance(v,list)} for row in summaries))
    report=dict(protocol=VERSION,manifest_sha256=file_hash(run/'manifest.json'),
        run_status_sha256=file_hash(run/'run_status.json'), analysis_sha256=file_hash(Path(__file__)),
        native_execution_count=len(done),copied_source_evidence_count=len(manifest['cases']),
        results=summaries, all_cases_retained=True, production_promoted=False,
        material_validation_passed=False,limitations=manifest['limitations'])
    write_json(output/'summary.json',report)
    print(json.dumps(dict(native_execution_count=len(done),
        passed_by_condition={c:sum(r['numerical_screen_passed'] for r in summaries if r['condition']==c) for c in ['source_original']+[v['id'] for v in CONDITIONS]}),indent=2))
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir',type=Path,required=True)
    parser.add_argument('--source-root',type=Path)
    parser.add_argument('--prepare',action='store_true')
    parser.add_argument('--run',action='store_true')
    parser.add_argument('--mumax')
    parser.add_argument('--max-jobs',type=int)
    parser.add_argument('--analyze',type=Path,metavar='NEW_OUTPUT_DIR')
    args=parser.parse_args()
    run=args.run_dir.resolve()
    if args.prepare:
        if args.source_root is None:
            parser.error('--prepare requires --source-root (consolidated workspace)')
        manifest=prepare(args.source_root.resolve(),run)
        print('Frozen '+str(len(manifest['jobs']))+' native probe jobs',flush=True)
    else:
        manifest=json.loads((run/'manifest.json').read_text(encoding='utf-8'))
    if args.run:
        execute(run,manifest,args.mumax,args.max_jobs)
    if args.analyze:
        analyze(run,args.analyze.resolve())
