"""Freeze and verify four BH-only imports in a new native motor copy; no solve."""
import argparse
import json
import os
import shutil
import subprocess
import sys
import traceback
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from modules.maxwell_material_transport import compatible_import_copy, digest, load_contract, native_arguments, verify_saved_material
from modules.motor_model_audit import audit_project
from modules.motor_workbench import write_json, license_config
from modules.motor_queue import identity_status, process_identity

GRADES = ('B23R075', 'B27R090', 'B27R095', 'B30P105')
PRODUCERS = ('modules/maxwell_material_transport.py', 'modules/maxwell_exporter.py',
    'modules/motor_model_audit.py', 'modules/motor_workbench.py', 'modules/motor_queue.py',
    'tools/preflight_calibrated_materials.py')


def prepare(root, destination):
    destination = destination.resolve()
    if destination.exists() or not destination.is_relative_to(root.resolve()):
        raise ValueError('Study must be a new directory inside the workspace')
    destination.mkdir(parents=True)
    source = root/'calibration/generalization_20261005/final/cal_65e506f6e207/materials'
    cases = []
    for grade in GRADES:
        original = source/('WB_'+grade+'_65e506f6e207.amat')
        directory = destination/'source_materials'
        directory.mkdir(exist_ok=True)
        for path in (original, original.with_suffix('.metadata.json')):
            shutil.copy2(path, directory/path.name)
        normalized, contract = compatible_import_copy(directory/original.name, destination/'import_materials')
        cases.append(dict(grade=grade, input=normalized.relative_to(destination).as_posix(),
            source=original.relative_to(root).as_posix(), source_sha256=digest(original), contract=contract,
            normalized_sha256=digest(normalized), normalized_metadata_sha256=digest(normalized.with_suffix('.metadata.json'))))
    template = root/'motor/v8_2/cases/nippon_steel_23zh90/motorcad_full_v8_1_nippon_steel_23zh90.aedt'
    native = destination/'private_native'
    native.mkdir()
    shutil.copy2(template, native/'transport.aedt')
    producers = {p:digest(PROJECT/p) for p in PRODUCERS}
    for name in PRODUCERS:
        target = destination/'frozen_producers'/name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PROJECT/name, target)
    legacy_sources = json.loads((root/'calibration/diagnostics/motor_review_20261005/source_before.json').read_text())
    assert all(digest(root/p) == h for p,h in legacy_sources.items())
    write_json(destination/'source_before.json', legacy_sources)
    write_json(destination/'protocol.json', dict(protocol='four_material_directional_transport_preflight_v4',
        cases=cases, template_source=template.relative_to(root).as_posix(), template_sha256=digest(template),
        producer_sha256=producers, wall_timeout_seconds=240, retry_allowed=False,
        native_sessions=1, field_solves=0, motor_solves=0,
        import_route='documented native AddMaterial with NAME:Point arrays; saved definition must match',
        scope='four distinct nonlinear RD/TD imports, saved curve verification, B23R075 assignment to 48 copied inserts; no field response/loss claim',
        old_loss_scope_preserved_for_unsolved_import_test_only=True))
    write_json(destination/'state.json', dict(status='prepared_not_executed', new_Maxwell_solves=0))
    return destination


def verify_frozen(study):
    protocol = json.loads((study/'protocol.json').read_text())
    for name, expected in protocol['producer_sha256'].items():
        if digest(PROJECT/name) != expected:
            raise ValueError('Frozen producer changed: '+name)
    for case in protocol['cases']:
        path = study/case['input']
        if digest(path) != case['normalized_sha256'] or digest(path.with_suffix('.metadata.json')) != case['normalized_metadata_sha256']:
            raise ValueError('Frozen material input changed')
    return protocol


def native_worker(study):
    import psutil
    protocol = verify_frozen(study)
    project = study/'private_native/transport.aedt'
    if digest(project) != protocol['template_sha256'] or project.with_suffix('.aedtresults').exists():
        raise ValueError('Native copy is no longer fresh')
    os.environ.setdefault('ANSWAIT', '0')
    config, _ = license_config()
    if config:
        os.environ.setdefault('ANSYSLMD_LICENSE_FILE', config)
    from ansys.aedt.core import Desktop, Maxwell2d
    existing = {p.pid for p in psutil.process_iter(['name']) if (p.info['name'] or '').lower() == 'ansysedt.exe'}
    desktop = None
    owned = False
    try:
        desktop = Desktop(version='2025.1', non_graphical=True, new_desktop=True, close_on_exit=False)
        pid = desktop.aedt_process_id
        if pid in existing:
            raise ValueError('Native session is not dedicated')
        owned = True
        write_json(study/'desktop_session.json', dict(identity=process_identity(pid), dedicated_session=True))
        app = Maxwell2d(project=str(project), design='Motor-CAD 2', version='2025.1',
            non_graphical=True, new_desktop=False, aedt_process_id=pid, close_on_exit=False, remove_lock=False)
        before = audit_project(project)
        imported = []
        for case in protocol['cases']:
            name = case['contract']['material_name']
            if name.casefold() in app.materials.material_keys:
                raise ValueError('Material name collision; no automatic renaming')
            contract = load_contract(study/case['input'])
            app.materials.odefinition_manager.AddMaterial(native_arguments(contract))
            if name not in list(app.materials.odefinition_manager.GetProjectMaterialNames()):
                raise ValueError('Native definition manager did not save the expected material')
            app.save_project()
            imported.append(dict(grade=case['grade'], **verify_saved_material(project.read_text(encoding='utf-8-sig'), case['contract'])))
        first = protocol['cases'][0]['contract']['material_name']
        assignments = []
        for record in before['inserts']:
            obj = app.modeler[record['name']]
            obj.material_name = first
            if obj.material_name.casefold() != first.casefold() or obj.part_coordinate_system != record['name']+'_CS':
                raise ValueError('Copied insert material or CS was not assigned')
            assignments.append(dict(object=record['name'], material=first, local_CS=obj.part_coordinate_system))
        app.save_project()
        saved = audit_project(project)
        for case in protocol['cases']:
            verify_saved_material(project.read_text(encoding='utf-8-sig'), case['contract'])
        if not saved['local_CS_verified'] or saved['moving_insert_count'] != 48:
            raise ValueError('Copied model bindings changed')
        if saved['core_loss']['enabled_object_ids'] != before['core_loss']['enabled_object_ids']:
            raise ValueError('BH-only import test changed loss scope')
        valid = app.validate_simple(str(study/'private_native/validation.log'))
        if valid != 1:
            raise ValueError('Native model validation failed: '+str(valid))
        write_json(study/'native_summary.json', dict(status='native_import_preflight_passed_not_solved',
            imported_materials=imported, assignments=assignments, validation=valid,
            saved_project_sha256=digest(project), before_model_audit=before, after_model_audit=saved,
            new_Maxwell_solves=0, new_field_controls=0, directional_field_verified=False,
            loss_calibration_verified=False, motor_ranking_eligible=False))
    except Exception as exc:
        write_json(study/'failure.json', dict(error=str(exc), traceback=traceback.format_exc(), new_Maxwell_solves=0))
        raise
    finally:
        if desktop is not None and owned:
            desktop.release_desktop(close_projects=True, close_on_exit=True)


def execute(root, study):
    verify_frozen(study)
    state = json.loads((study/'state.json').read_text())
    if state['status'] != 'prepared_not_executed':
        raise ValueError('No retry or resubmission of a native import study')
    write_json(study/'state.json', dict(status='native_import_running', new_Maxwell_solves=0))
    command = [str(root/'.runtime/aedt/Scripts/python.exe'), '-X', 'utf8', str(Path(__file__).resolve()),
        '--root', str(root), '--study-dir', str(study), '--native-worker']
    with (study/'private_native/worker.log').open('x', encoding='utf-8') as stream:
        process = subprocess.Popen(command, stdout=stream, stderr=subprocess.STDOUT, cwd=study)
        write_json(study/'dispatch.json', dict(identity=process_identity(process.pid), command=command))
        try:
            rc = process.wait(timeout=240)
        except subprocess.TimeoutExpired:
            import psutil
            record_path = study/'desktop_session.json'
            if record_path.exists():
                record = json.loads(record_path.read_text())
                if record['dedicated_session'] and identity_status(record['identity']) == 'alive':
                    psutil.Process(record['identity']['pid']).terminate()
            if process.poll() is None:
                process.terminate()
            write_json(study/'state.json', dict(status='native_import_timeout_no_retry', new_Maxwell_solves=0))
            raise
    write_json(study/'state.json', dict(status='native_import_preflight_passed_not_solved' if rc == 0 else 'native_import_failed_no_retry',
        new_Maxwell_solves=0, exit_code=rc))
    return rc


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--study-dir', type=Path, required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--prepare', action='store_true')
    group.add_argument('--execute', action='store_true')
    group.add_argument('--native-worker', action='store_true')
    args = parser.parse_args()
    root, study = args.root.resolve(), args.study_dir.resolve()
    if args.prepare:
        prepare(root, study)
    elif args.native_worker:
        native_worker(study)
    else:
        raise SystemExit(execute(root, study))
