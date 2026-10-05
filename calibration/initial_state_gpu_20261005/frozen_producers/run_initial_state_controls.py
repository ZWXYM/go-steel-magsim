"""Bounded serial execution of four previously frozen B30P105 state controls.

Preparation and analysis are CPU only. Native execution requires --execute;
use only after explicit human GPU authorization. Existing evidence is immutable.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from modules.material_calibration import file_hash
from modules.native_loop_diagnostics import audit_table
from modules.native_state_controls import audit_control, VERSION
from tools.run_calibration_pilot import H_GRID, resolve_mumax, write_json

PRODUCERS = ('modules/native_state_controls.py', 'modules/native_loop_diagnostics.py',
    'modules/material_calibration.py', 'tools/run_initial_state_controls.py',
    'tools/run_calibration_pilot.py')


def snapshot(directory):
    return {p.relative_to(directory).as_posix(): file_hash(p)
        for p in sorted(directory.rglob('*')) if p.is_file()}


def prepare(root, prepared, run, *, mumax=None, timeout=120):
    root, prepared, run = map(lambda p: Path(p).resolve(), (root, prepared, run))
    pilot = root / 'calibration/pilot_20261003_n8'
    if run.exists() or run == root or pilot in run.parents or prepared in run.parents:
        raise ValueError('Use a new output directory outside existing evidence')
    if timeout <= 0 or timeout > 120:
        raise ValueError('Timeout must be positive and at most 120 seconds')
    report = json.loads((prepared / 'report.json').read_text(encoding='utf-8'))
    if report['protocol']['selection'] != 'all four confirmed materials and RD/TD; fixed grain_id=1 for prepared controls':
        raise ValueError('Unexpected frozen case selection')
    for item in report['frozen_source_files']:
        source = (prepared / item['path']).resolve()
        if not source.is_relative_to(prepared) or file_hash(source) != item['sha256']:
            raise ValueError('Prepared source bytes changed')
    for job in report['jobs']:
        source = (prepared / job['script']).resolve()
        if not source.is_relative_to(prepared) or file_hash(source) != job['script_sha256']:
            raise ValueError('Prepared script bytes changed')
    selected = [j for j in report['jobs'] if j['grade'] == 'B30P105']
    identities = {(j['direction'], j['grain_id'], j['condition']) for j in selected}
    if len(selected) != 4 or identities != {(d, 1, c) for d in ('RD', 'TD')
            for c in ('strict_major_loop', 'zero_transverse_pair')}:
        raise ValueError('Expected fixed four B30P105 controls')
    binary = resolve_mumax(mumax)
    run.mkdir(parents=True)
    jobs = []
    frozen = {}
    for job in selected:
        name = job['case_id'] + '/' + job['condition'] + '.mx3'
        script = run / 'scripts' / name
        script.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(prepared / job['script'], script)
        for old_name in ('original.mx3', 'original_table.txt'):
            destination = run / 'source_cases' / job['case_id'] / old_name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(prepared / 'source_cases' / job['case_id'] / old_name, destination)
            frozen[destination.relative_to(run).as_posix()] = file_hash(destination)
        jobs.append({**{k: v for k, v in job.items() if k != 'status'},
            'angle': 0 if job['direction'] == 'RD' else 90,
            'script': script.relative_to(run).as_posix(),
            'output': 'raw/' + job['case_id'] + '/' + job['condition'] + '.out'})
    for name in PRODUCERS:
        destination = run / 'frozen_producers' / Path(name).name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PROJECT / name, destination)
        frozen[destination.relative_to(run).as_posix()] = file_hash(destination)
    protected = dict(pilot=snapshot(pilot), prepared=snapshot(prepared))
    write_json(run / 'source_before.json', protected)
    frozen['source_before.json'] = file_hash(run / 'source_before.json')
    manifest = dict(protocol=VERSION, predeclared_utc=datetime.now(timezone.utc).isoformat(),
        selection='B30P105 fixed grain_id=1, both directions, both frozen conditions; four jobs',
        prepared_report_sha256=file_hash(prepared / 'report.json'),
        source_manifest_sha256=report['source_manifest_sha256'],
        physics_version=report['physics_version'], H_axis='physical_A_per_m', H_grid=H_GRID.tolist(),
        jobs=jobs, frozen_files=frozen, producer_sha256={p: file_hash(PROJECT / p) for p in PRODUCERS},
        mumax_binary_sha256=file_hash(binary), timeout_seconds_per_job=timeout,
        maximum_native_attempts=4, parallel_jobs=1, promotion_allowed=False,
        gates=dict(min_signed_endpoint_m_projection=.95, max_branch_inversion_error_T=.02,
            max_residual_torque_T=1e-5),
        limitations=['Fixed grain 1 diagnostics do not rerun the four large-H0 grains',
            'Transverse initializations are not certified demagnetization or material normal curves',
            'Do not promote into calibration, surrogate training, Hc, Br, loss or motor rankings'])
    write_json(run / 'manifest.json', manifest)
    return manifest


def verify(run, manifest, *, live_producers=False):
    for relative, digest in manifest['frozen_files'].items():
        path = (run / relative).resolve()
        if not path.is_relative_to(run) or file_hash(path) != digest:
            raise ValueError('Frozen evidence changed: ' + relative)
    for job in manifest['jobs']:
        script, output = (run / job['script']).resolve(), (run / job['output']).resolve()
        if (not script.is_relative_to(run) or not output.is_relative_to(run)
                or file_hash(script) != job['script_sha256']):
            raise ValueError('Frozen job changed')
    if live_producers:
        for relative, digest in manifest['producer_sha256'].items():
            if file_hash(PROJECT / relative) != digest:
                raise ValueError('Producer changed; use a new run')


def execute(run, manifest, *, mumax=None):
    run = Path(run).resolve()
    verify(run, manifest, live_producers=True)
    binary = resolve_mumax(mumax)
    if file_hash(binary) != manifest['mumax_binary_sha256']:
        raise ValueError('Native binary changed')
    if len(manifest['jobs']) != 4 or manifest['parallel_jobs'] != 1:
        raise ValueError('Unexpected native budget')
    lock = run / 'runner.lock'
    handle = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    try:
        with os.fdopen(handle, 'w', encoding='utf-8') as stream:
            json.dump(dict(pid=os.getpid(), created_utc=datetime.now(timezone.utc).isoformat()), stream)
        status_path = run / 'run_status.json'
        if status_path.exists() or any((run / j['output']).exists() for j in manifest['jobs']):
            raise ValueError('Never retry/overwrite existing attempts; use a new study')
        status = dict(protocol=VERSION, manifest_sha256=file_hash(run / 'manifest.json'),
            mumax_binary_sha256=file_hash(binary), jobs=[])
        write_json(status_path, status)
        for job in manifest['jobs']:
            output = run / job['output']
            output.parent.mkdir(parents=True, exist_ok=True)
            log_path = output.parent / (output.stem + '.log')
            started = time.perf_counter()
            with log_path.open('w', encoding='utf-8') as log:
                try:
                    proc = subprocess.run([str(binary), '-http=127.0.0.1:0', '-o', str(output),
                        str(run / job['script'])], stdout=log, stderr=subprocess.STDOUT,
                        cwd=PROJECT, timeout=manifest['timeout_seconds_per_job'])
                    code = proc.returncode
                except subprocess.TimeoutExpired:
                    code = -124  # subprocess kills and waits only for this owned child.
            table = output / 'table.txt'
            result = {**job, 'exit_code': code, 'execution_kind': 'native',
                'elapsed_seconds': round(time.perf_counter() - started, 3),
                'table_sha256': file_hash(table) if table.is_file() else None,
                'status': 'completed' if code == 0 else ('timed_out' if code == -124 else 'native_failed')}
            if code == 0:
                try:
                    result['metrics'] = audit_control(table, manifest['H_grid'],
                        angle_deg=job['angle'], condition=job['condition'])
                except (ValueError, OSError) as error:
                    result.update(status='table_invalid', validation_error=str(error))
            status['jobs'].append(result)
            write_json(status_path, status)
            print(job['case_id'], job['condition'], result['status'], result['elapsed_seconds'], flush=True)
            if result['status'] != 'completed':
                break  # Keep remaining budget unattempted after execution/schema failure.
        return status
    finally:
        lock.unlink()


def analyze(run):
    run = Path(run).resolve()
    manifest = json.loads((run / 'manifest.json').read_text(encoding='utf-8'))
    verify(run, manifest, live_producers=True)
    status = json.loads((run / 'run_status.json').read_text(encoding='utf-8'))
    if (status['manifest_sha256'] != file_hash(run / 'manifest.json')
            or status['mumax_binary_sha256'] != manifest['mumax_binary_sha256']):
        raise ValueError('Native runtime source identity changed')
    expected = {j['script']: j for j in manifest['jobs']}
    seen, results = set(), []
    for record in status['jobs']:
        if record['script'] in seen or record['script'] not in expected:
            raise ValueError('Unexpected/duplicate execution record')
        seen.add(record['script'])
        job = expected[record['script']]
        if any(record.get(key) != value for key, value in job.items()):
            raise ValueError('Execution record differs from frozen job')
        table = run / job['output'] / 'table.txt'
        if record['table_sha256'] is not None and file_hash(table) != record['table_sha256']:
            raise ValueError('Native table changed')
        if record['status'] != 'completed' or record['exit_code'] != 0:
            results.append({k: v for k, v in record.items() if k != 'metrics'})
            continue
        metrics = audit_control(table, manifest['H_grid'], angle_deg=job['angle'], condition=job['condition'])
        if metrics != record['metrics']:
            raise ValueError('Native analysis differs from execution checkpoint')
        item = dict(case_id=job['case_id'], direction=job['direction'],
            grain_id=job['grain_id'], execution_status='completed', **metrics)
        if job['condition'] == 'strict_major_loop':
            old = audit_table(run / 'source_cases' / job['case_id'] / 'original_table.txt',
                manifest['H_grid'], angle_deg=job['angle'])
            keys = ('native_unforced_midpoint_H0_T', 'B800_before_guard_T',
                'min_signed_endpoint_m_projection', 'max_branch_inversion_error_T', 'max_residual_torque_T')
            item['original_fixed_grain_metrics'] = {k: old[k] for k in keys}
        results.append(item)
    report = dict(protocol=VERSION, manifest_sha256=file_hash(run / 'manifest.json'),
        attempted_jobs=len(status['jobs']), successful_native_jobs=sum(j['exit_code'] == 0 for j in status['jobs']),
        complete_controls=sum(j['status'] == 'completed' for j in status['jobs']),
        unattempted_jobs=[j['script'] for j in manifest['jobs'] if j['script'] not in seen], results=results,
        promotion_allowed=False, model_changed=False, measured_material_validation=False)
    destination = run / 'analysis.json'
    if destination.exists():
        if json.loads(destination.read_text(encoding='utf-8')) != report:
            raise ValueError('Existing analysis differs; preserve and use a new output')
    else:
        write_json(destination, report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=PROJECT.parent if PROJECT.name == 'magsim' else PROJECT)
    parser.add_argument('--prepared-dir', type=Path)
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--mumax', type=Path)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--analyze', action='store_true')
    args = parser.parse_args()
    run = args.run_dir.resolve()
    if args.prepared_dir:
        manifest = prepare(args.root, args.prepared_dir, run, mumax=args.mumax)
    else:
        manifest = json.loads((run / 'manifest.json').read_text(encoding='utf-8'))
    if args.execute:
        execute(run, manifest, mumax=args.mumax)
    if args.analyze:
        report = analyze(run)
        print(json.dumps({k: v for k, v in report.items() if k != 'results'}, indent=2))
