"""Inspect cloned, completed Maxwell results without solving or changing sources."""
import argparse
import json
import os
import shutil
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from modules.maxwell_material_transport import digest
from modules.motor_workbench import write_json, license_config
from modules.motor_queue import process_identity


def inspect(root, source, output):
    if output.exists() or not output.is_relative_to(root) or not source.is_relative_to(root):
        raise ValueError('A new workspace inspection directory is required')
    protocol = json.loads((source.parents[1] / 'protocol.json').read_text())
    case = next(c for c in protocol['cases'] if c['case_id'] == source.name)
    if not (source / 'solve_completed.json').exists():
        raise ValueError('Only completed, frozen results can be inspected')
    before = {p.relative_to(root).as_posix(): digest(p) for p in source.rglob('*') if p.is_file()}
    output.mkdir(parents=True)
    write_json(output / 'inspection_protocol.json', dict(
        source_case=source.relative_to(root).as_posix(), source_before=before,
        producer_sha256=digest(Path(__file__)), maximum_new_solves=0,
        scope='Dedicated session; cloned project and results; field/profile reads only'))
    private = output / 'private_clone'
    private.mkdir()
    project = private / 'control.aedt'
    shutil.copy2(source / 'control.aedt', project)
    shutil.copytree(source / 'control.aedtresults', private / 'control.aedtresults')
    shutil.copy2(source / 'points.pts', output / 'points.pts')
    config, _ = license_config()
    if config:
        os.environ.setdefault('ANSYSLMD_LICENSE_FILE', config)
    from ansys.aedt.core import Desktop, Maxwell2d
    import psutil
    existing = {p.pid for p in psutil.process_iter(['name'])
                if (p.info['name'] or '').lower() == 'ansysedt.exe'}
    desktop = None
    owned = False
    records = {}
    try:
        desktop = Desktop(version='2025.1', non_graphical=True, new_desktop=True, close_on_exit=False)
        pid = desktop.aedt_process_id
        if pid in existing:
            raise ValueError('Dedicated inspection session required')
        owned = True
        write_json(output / 'desktop_session.json', dict(identity=process_identity(pid), dedicated_session=True))
        app = Maxwell2d(project=str(project), design='UniformFlux', version='2025.1',
                        non_graphical=True, new_desktop=False, aedt_process_id=pid, close_on_exit=False)
        records['profile_export'] = bool(app.export_profile('ControlSetup', output_file=str(output / 'native.prof')))
        records['field_exports'] = {}
        # Documented standard quantities, kept separate from the original H file.
        for quantity in ('H', 'H_Vector', 'Energy', 'coEnergy'):
            try:
                result = app.post.export_field_file(
                    quantity, solution='ControlSetup : LastAdaptive', output_file=str(output / (quantity + '.fld')),
                    sample_points_file=str(output / 'points.pts'), reference_coordinate_system='Global',
                    export_in_si_system=True, export_field_in_reference=True)
                records['field_exports'][quantity] = bool(result)
            except Exception as exc:
                records['field_exports'][quantity] = dict(error=str(exc))
        fields = app.post.ofieldsreporter
        for name, operations in (
            ('B_dot_H_half', [('qty', 'B'), ('qty', 'H'), ('op', 'Dot'), ('scalar', .5), ('op', '*')]),
        ):
            try:
                fields.CalcStack('clear')
                for kind, value in operations:
                    if kind == 'qty':
                        fields.EnterQty(value)
                    elif kind == 'scalar':
                        fields.EnterScalar(value)
                    else:
                        fields.CalcOp(value)
                fields.ExportToFile(str(output / (name + '.fld')), str(output / 'points.pts'),
                    'ControlSetup : LastAdaptive', [], ['NAME:ExportOption', 'IncludePtInOutput:=', True,
                    'RefCSName:=', 'Global', 'PtInSI:=', True, 'FieldInRefCS:=', True])
                records['field_exports'][name] = (output / (name + '.fld')).exists()
            except Exception as exc:
                records['field_exports'][name] = dict(error=str(exc))
        records['setup_props'] = dict(app.get_setup('ControlSetup').props)
        records['setup_native_properties'] = {}
        try:
            child = app.odesign.GetChildObject('Analysis').GetChildObject('ControlSetup')
            for name in child.GetPropNames():
                value = child.GetPropValue(name)
                records['setup_native_properties'][name] = value if isinstance(value, (str, int, float, bool, list, type(None))) else str(value)
        except Exception as exc:
            records['setup_native_properties']['read_error'] = str(exc)
        records['native_messages'] = list(desktop.odesktop.GetMessages(app.project_name, app.design_name, 0))
        records['original_H_sha256'] = digest(source / 'H.fld')
        records['cloned_H_same_bytes'] = digest(output / 'H.fld') == records['original_H_sha256']
        records['case_id'] = case['case_id']
        records['new_field_solves'] = 0
    finally:
        if desktop is not None and owned:
            desktop.release_desktop(close_projects=True, close_on_exit=True)
        if not all(digest(root / p) == h for p, h in before.items()):
            raise ValueError('Original native result changed during inspection')
        records['original_source_unchanged_files'] = len(before)
        write_json(output / 'inspection_result.json', records)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('root', 'source-case', 'output-dir'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    inspect(args.root.resolve(), args.source_case.resolve(), args.output_dir.resolve())
