"""Copy verified Haar-prefix native evidence into a larger, separate run.

Imported evidence is never counted as a new solve. Sources remain read-only.
"""
import argparse
import csv
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from modules.material_calibration import file_hash
from modules.texture_sampling import SAMPLING_VERSION
from tools.analyze_sampling_convergence import read_ensemble
from tools.run_calibration_pilot import write_json

VERSION = 'verified_native_prefix_reuse_v1'


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def confined(root, relative):
    path = (root/relative).resolve()
    if not path.is_relative_to(root) or path == root:
        raise ValueError('Evidence path escapes its run directory')
    return path


def reuse(source, target, grade, directions):
    source, target = source.resolve(), target.resolve()
    if source == target or source in target.parents or target in source.parents:
        raise ValueError('Use separate non-nested run directories')
    if (source/'runner.lock').exists():
        raise ValueError('Source run is locked; wait for native work to finish')
    lock = target/'runner.lock'
    try:
        handle = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except FileExistsError as exc:
        raise ValueError('Target run already locked') from exc
    try:
        with os.fdopen(handle, 'w', encoding='utf-8') as stream:
            json.dump(dict(pid=os.getpid(), kind=VERSION), stream)
        return _reuse(source, target, grade, directions)
    finally:
        lock.unlink()


def _reuse(source, target, grade, directions):
    initial_manifest_sha = file_hash(source/'manifest.json')
    initial_status_sha = file_hash(source/'run_status.json')
    old, new = read(source/'manifest.json'), read(target/'manifest.json')
    if (old.get('texture_sampling_version') != SAMPLING_VERSION
            or new.get('texture_sampling_version') != SAMPLING_VERSION):
        raise ValueError('Native prefix reuse requires Haar-prefix v2')
    if not set(directions) <= {'RD', 'TD'} or len(set(directions)) != len(directions) or not directions:
        raise ValueError('Distinct RD/TD directions required')
    for key in ('seed', 'physics_version', 'calibration_version', 'H_grid', 'registry_sha256', 'texture_module_sha256'):
        if old[key] != new[key]:
            raise ValueError('Prefix contract differs in ' + key)
    if old['n_grains'] >= new['n_grains']:
        raise ValueError('Destination grain count must be larger')
    a = next(m for m in old['materials'] if m['grade'] == grade)
    b = next(m for m in new['materials'] if m['grade'] == grade)
    for key in ('seed', 'params'):
        if a[key] != b[key]:
            raise ValueError('Material prefix differs in ' + key)
    if a['texture_sampling']['distribution_sha256'] != b['texture_sampling']['distribution_sha256']:
        raise ValueError('Analytic texture distribution differs')
    n = old['n_grains']
    def rows(root, material):
        path = confined(root, material['grain_samples']['path'])
        if file_hash(path) != material['grain_samples']['sha256']:
            raise ValueError('Frozen grain provenance changed')
        with path.open(encoding='utf-8-sig') as stream:
            return list(csv.DictReader(stream))
    if rows(source, a) != rows(target, b)[:n]:
        raise ValueError('Grain identities are not an exact prefix')
    for root, material in ((source, a), (target, b)):
        path = root/grade/'orientations.csv'
        if file_hash(path) != material['orientations_sha256']:
            raise ValueError('Frozen orientations changed')
    ea = np.loadtxt(source/grade/'orientations.csv', delimiter=',', skiprows=1)
    eb = np.loadtxt(target/grade/'orientations.csv', delimiter=',', skiprows=1)
    if not np.array_equal(ea, eb[:n]):
        raise ValueError('Euler samples are not an exact prefix')
    for direction in directions:
        # This validates every source native table and executed script before copying.
        read_ensemble(source, grade, direction)
        for material, root in ((a, source), (b, target)):
            ref = material['references'][direction]
            if file_hash(confined(root, ref['path'])) != ref['sha256']:
                raise ValueError('Frozen reference changed')
        if a['references'][direction]['sha256'] != b['references'][direction]['sha256']:
            raise ValueError('Frozen material references differ')
    old_status = read(source/'run_status.json')
    status_path = target/'run_status.json'
    runtime = read(status_path) if status_path.exists() else dict(
        mumax_binary_sha256=old_status['mumax_binary_sha256'],
        mumax_version_query=old_status.get('mumax_version_query', ''), jobs=[])
    if runtime['mumax_binary_sha256'] != old_status['mumax_binary_sha256']:
        raise ValueError('MuMax binary differs in target status')
    complete = {j['script']: j for j in runtime['jobs'] if j['exit_code'] == 0}
    if len(complete) != sum(j['exit_code'] == 0 for j in runtime['jobs']):
        raise ValueError('Duplicate target successful jobs')
    parent = {j['script']: j for j in old_status['jobs'] if j['exit_code'] == 0}
    new_jobs = {(j['direction'], j['grain_id']): j for j in new['jobs'] if j['grade'] == grade}
    planned = []
    for job in old['jobs']:
        if job['grade'] != grade or job['direction'] not in directions:
            continue
        other = new_jobs[job['direction'], job['grain_id']]
        if any(job[key] != other[key] for key in ('angle', 'script_sha256', 'grain_id')):
            raise ValueError('Frozen native script is not an exact prefix')
        if file_hash(confined(target, other['script'])) != job['script_sha256']:
            raise ValueError('Target native script changed')
        output = confined(target, other['output'])
        prior = complete.get(other['script'])
        if prior:
            if (prior['script_sha256'] != job['script_sha256']
                    or prior['table_sha256'] != parent[job['script']]['table_sha256']
                    or file_hash(output/'table.txt') != prior['table_sha256']):
                raise ValueError('Already completed target prefix differs')
        else:
            if output.exists():
                raise ValueError('Unverified target output exists; preserve it and inspect')
            planned.append((job, other))
    source_manifest_sha = file_hash(source/'manifest.json')
    source_status_sha = file_hash(source/'run_status.json')
    if source_manifest_sha != initial_manifest_sha or source_status_sha != initial_status_sha:
        raise ValueError('Source contract changed during prefix verification')
    for job, other in planned:
        original = parent[job['script']]
        if (source/'runner.lock').exists():
            raise ValueError('Source locked during prefix copy')
        output = confined(target, other['output'])
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(confined(source, job['output']), output)
        if file_hash(output/'table.txt') != original['table_sha256']:
            raise ValueError('Imported native table bytes changed during copy')
        origin = dict(protocol=VERSION, source_run_id=source.name,
                      source_manifest_sha256=source_manifest_sha,
                      source_status_sha256=source_status_sha, source_script=job['script'],
                      source_table_sha256=original['table_sha256'])
        root_origin = original.get('root_native_origin', dict(
            run_id=source.name, manifest_sha256=source_manifest_sha,
            script=job['script'], script_sha256=job['script_sha256'],
            table_sha256=original['table_sha256'],
            original_elapsed_seconds=original.get('elapsed_seconds')))
        runtime['jobs'].append(dict(**other, exit_code=0, elapsed_seconds=0.0,
            table_sha256=original['table_sha256'], execution_kind='imported_native_prefix',
            import_origin=origin, root_native_origin=root_origin))
        write_json(status_path, runtime)
    if (file_hash(source/'manifest.json') != initial_manifest_sha
            or file_hash(source/'run_status.json') != initial_status_sha):
        raise ValueError('Source contract changed during prefix copy; inspect preserved target')
    counts = dict(imported_native_evidence=sum(j.get('execution_kind') == 'imported_native_prefix'
                    for j in runtime['jobs'] if j['exit_code'] == 0),
                  newly_executed_native_solves=sum(j.get('execution_kind', 'native') == 'native'
                    for j in runtime['jobs'] if j['exit_code'] == 0))
    report = dict(protocol=VERSION, source_run_id=source.name, target_run_id=target.name,
                  added_imported_jobs=len(planned), **counts, native_execution_performed=False)
    print(json.dumps(report, indent=2))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-run', type=Path, required=True)
    parser.add_argument('--target-run', type=Path, required=True)
    parser.add_argument('--grade', default='B30P105')
    parser.add_argument('--direction', choices=['RD', 'TD'], action='append')
    args = parser.parse_args()
    reuse(args.source_run, args.target_run, args.grade, args.direction or ['RD', 'TD'])
