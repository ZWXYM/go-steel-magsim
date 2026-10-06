"""Reproduce frozen coupon diagnostics and publish a small evidence tree on CPU."""
import argparse
import json
import shutil
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from modules.maxwell_interface_controls import evaluate, verify_material, review_energy
from modules.maxwell_directional_controls import read_vector_field
from modules.maxwell_material_transport import digest, blocks
from modules.maxwell_native_convergence import read_convergence
from modules.motor_workbench import write_json
from modules.motor_queue import identity_status


def audit(root, study, output):
    if output.exists() or not output.is_relative_to(root) or not study.is_relative_to(root):
        raise ValueError('A new workspace evidence directory is required')
    protocol = json.loads((study / 'protocol.json').read_text())
    summary = json.loads((study / 'summary.json').read_text())
    before = json.loads((study / 'source_before.json').read_text())
    if not all(digest(root / p) == h for p, h in before.items()):
        raise ValueError('An older source or result changed')
    for p, h in protocol['producer_sha256'].items():
        if digest(study / 'frozen_producers' / p) != h:
            raise ValueError('Frozen producer changed')
    results = []; incomplete = []; released = 0
    for case in protocol['cases']:
        folder = study / 'private_native' / case['case_id']
        if not folder.exists():
            skip = next((item for item in summary['skipped'] if item['case_id'] == case['case_id']), None)
            if skip is None:
                raise ValueError('Missing case without a frozen skip reason')
            incomplete.append(dict(skip, status='skipped_not_executed'))
            continue
        session = json.loads((folder / 'desktop_session.json').read_text())
        if not session['dedicated_session'] or identity_status(session['identity']) != 'dead':
            raise ValueError('Dedicated native session remains alive')
        released += 1
        preflight = json.loads((folder / 'preflight.json').read_text())
        if preflight['validation'] != 1 or len(preflight['boundary_edges']) != 4:
            raise ValueError('Native full-boundary preflight differs')
        project = folder / 'control.aedt'
        verify_material(project.read_text(encoding='utf-8-sig'), case)
        if not (folder / 'result.json').exists():
            failure = json.loads((folder / 'failure.json').read_text())
            messages = json.loads((folder / 'native_messages.json').read_text())
            incomplete.append(dict(case_id=case['case_id'], status='native_failed', error=failure['error'],
                TAU_mesh_failure=any('Surface Mesh Generation Failed' in str(v) for v in messages.values()),
                native_validation=preflight['validation'], saved_project_sha256=digest(project)))
            continue
        raw = json.loads((folder / 'result.json').read_text())
        if digest(project) != raw['saved_project_sha256']:
            raise ValueError('Solved native project changed')
        B = read_vector_field(folder / 'B.fld', case['sample_points_m'])
        H = read_vector_field(folder / 'H.fld', case['sample_points_m'])
        convergence = read_convergence(folder / 'convergence.txt', case['setup']['PercentError'])
        result = evaluate(case, B, H, convergence)
        if any(raw[k] != v for k, v in result.items()):
            raise ValueError('CPU reproduction differs from frozen producer')
        result['energy_review'] = review_energy(case, B, H, convergence)
        result['producer_pass_is_direction_certification'] = False
        results.append(result)
    if len(results) != summary['new_field_solves']:
        raise ValueError('Completed native count differs')
    reviewed = dict(protocol=protocol['protocol'], mode=protocol.get('mode', 'meter_geometry'), cases=results,
        incomplete_cases=incomplete, new_field_solves=len(results), new_motor_solves=0, new_MuMax_solves=0,
        cumulative_Maxwell_completed=summary['cumulative_Maxwell_completed'],
        original_producer_pass_count=sum(r['passed'] for r in results),
        linear_field_and_energy_pass_count=sum(r['energy_review'].get('interface_response_verified', False) for r in results),
        old_source_files_unchanged=len(before), dedicated_sessions_released=released,
        calibrated_material_field_verified=False, object_CS_direction_verified=False,
        loss_calibration_verified=False, motor_ranking_eligible=False,
        interpretation='Original producer results retained. Linear energy is an additional veto; nonlinear and real-material gates remain unmet.')
    output.mkdir(parents=True)
    for name in ('protocol.json', 'summary.json', 'state.json', 'source_before.json', 'saved_geometry_input.json'):
        shutil.copy2(study / name, output / name)
    for name in ('source_materials', 'frozen_producers'):
        shutil.copytree(study / name, output / name)
    for case in protocol['cases']:
        source = study / 'private_native' / case['case_id']
        dest = output / 'cases' / case['case_id']; dest.mkdir(parents=True)
        if not source.exists():
            write_json(dest / 'failure_status.json', next(r for r in incomplete if r['case_id'] == case['case_id']))
            continue
        for name in ('B.fld', 'H.fld', 'points.pts', 'convergence.txt', 'preflight.json', 'result.json',
                     'solve_started.json', 'solve_completed.json', 'native_messages.json'):
            if (source / name).exists():
                shutil.copy2(source / name, dest / name)
        if not (source / 'result.json').exists():
            write_json(dest / 'failure_status.json', next(r for r in incomplete if r['case_id'] == case['case_id']))
        text = (source / 'control.aedt').read_text(encoding='utf-8-sig'); pieces = []
        for section in (case['material']['material_name'], 'ModelSetup', 'BoundarySetup', 'AnalysisSetup'):
            bodies = blocks(text, section)
            if len(bodies) != 1:
                raise ValueError('Ambiguous saved definition')
            pieces.append("$begin '" + section + "'\n" + bodies[0] + "\n$end '" + section + "'\n")
        (dest / 'saved_native_definitions.txt').write_text('\n'.join(pieces), encoding='utf-8', newline='\n')
    names = ('modules/maxwell_interface_controls.py', 'tools/audit_maxwell_interface_controls.py')
    write_json(output / 'audit_producer_sha256.json', {p: digest(PROJECT / p) for p in names})
    for name in names:
        dest = output / 'audit_producers' / name; dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PROJECT / name, dest)
    write_json(output / 'reviewed_summary.json', reviewed)
    return reviewed


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('root', 'study-dir', 'output-dir'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.root.resolve(), args.study_dir.resolve(), args.output_dir.resolve())
    print(json.dumps({k: v for k, v in result.items() if k not in ('cases', 'incomplete_cases')}, indent=2))
