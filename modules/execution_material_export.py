"""Export the material definition saved in a completed equivalent motor case."""
import csv
import hashlib
import io
import json
import math
import re
from pathlib import Path

from modules.calibrated_motor_analysis import verify_solver_settings
from modules.catalog_motor_loss import catalog_material_name, verify_catalog_material
from modules.maxwell_material_transport import equivalent_material_name, verify_saved_equivalent_material
from modules.motor_model_audit import one, value

VERSION = 'execution_direction_equivalent_material_v1'
FILES = ('actual_material.amat', 'actual_material.metadata.json', 'actual_material_BH.csv')


def digest(blob):
    return hashlib.sha256(blob).hexdigest()


READER_SHA256 = digest(Path(__file__).read_bytes())


def execution_material_exports(record, folder):
    """Read saved nodes, not regenerated predictions; never change case files."""
    if record.get('result_available') is not True:
        raise ValueError('实际求解结果保存后才能导出本次材料')
    direction = record.get('requested_protocol', {}).get('equivalent_direction')
    if direction not in ('RD', 'TD'):
        raise ValueError('当前结果不是方向等效模型；请使用原材料包')
    folder = Path(folder)
    saved, contract = record['result'], record['material_contract']
    if record['id'] != folder.name or saved['execution_id'] != record['id']:
        raise ValueError('材料导出与执行身份不符')
    blob = (folder/'case/motor.aedt').read_bytes()
    if digest(blob) != saved['solved_project_sha256']:
        raise ValueError('实际保存工程已改变，未导出材料')
    metadata_blob = (folder/'inputs/material.metadata.json').read_bytes()
    if digest(metadata_blob) != contract['metadata_sha256']:
        raise ValueError('本次原材料metadata已改变，未导出材料')
    # AEDT/AMAT may use Windows CRLF; the native field reader expects logical lines.
    text = blob.decode('utf-8-sig').replace('\r\n', '\n').replace('\r', '\n')
    representation=record['requested_protocol'].get('curve_representation','Normal')
    catalog = record.get('catalog_loss')
    if catalog:
        transport = verify_catalog_material(text, contract, catalog)
        name = catalog_material_name(contract)
        loss_description = 'Manufacturer catalog engineering loss estimate.'
    else:
        transport = verify_saved_equivalent_material(text, contract, direction,representation)
        name = equivalent_material_name(contract, direction,representation)
        loss_description = 'Uncalibrated zero loss placeholders.'
    if (saved.get('actual_material_name') != name or
            saved.get('constitutive_model') != 'scalar_direction_equivalent_v1'):
        raise ValueError('结果的实际材料或模型不符')
    if representation=='Intrinsic' and saved.get('curve_representation')!='Intrinsic':
        raise ValueError('结果未绑定实际Intrinsic表示')
    body = one(text, name)
    amat = (f'# Actual saved direction-equivalent motor material: {name}\n'
            f'# Source execution: {record["id"]}; source direction: {direction}\n'
            f'# Isotropic {"normal B-H" if representation=="Normal" else "Intrinsic Bi-H"} engineering model. {loss_description}\n'
            f"$begin '{name}'" + body + f"$end '{name}'\n").encode('utf8')
    points = re.search(r'Points\[(\d+):\s*([^\]]+)\]', one(one(body, 'permeability'), 'BHCoordinates'))
    flat = [float(v.strip()) for v in points[2].split(',')]
    pairs = list(zip(flat[::2], flat[1::2]))
    if len(pairs) != transport['curve_points']:
        raise ValueError('实际导出节点数与保存材料核对不符')
    stream = io.StringIO(newline='')
    writer = csv.writer(stream)
    if representation=='Intrinsic':
        writer.writerow(['H_A_per_m','B_T','Bi_T'])
        writer.writerows((h,bi+4*math.pi*1e-7*h,bi) for h,bi in pairs)
    else:
        writer.writerow(['H_A_per_m', 'B_T'])
        writer.writerows(pairs)
    curve = stream.getvalue().encode('utf-8-sig')
    source_metadata = json.loads(metadata_blob)
    metadata = dict(
        version=VERSION, material_name=name, source_material_name=contract['material_name'],
        status='engineering_direction_equivalent_BH_only', constitutive_model='scalar_direction_equivalent_v1',
        source_direction=direction, applied_material_law='isotropic_normal_BH', exported_directions=[direction],
        H_axis='physical_A_per_m', H_scale=1., B_definition='normal_B', H_unit='A/m', B_unit='T',
        curve_points=len(pairs), curve_nodes_source='actual_saved_native_material_definition',
        source_points_unchanged=True, native_saved_definition_verified=True, export_roundtrip_verified=False,
        source_execution=record['id'], source_analysis_plan=record['analysis_plan_id'],
        parent_bank_sha256=record['parent_bank_sha256'], effective_bank_sha256=record['effective_bank_sha256'],
        training_grades=record['training_grades'], excluded_grades=record['excluded_grades'],
        source_import_amat_sha256=contract['amat_sha256'], source_import_metadata_sha256=contract['metadata_sha256'],
        source_solved_project_sha256=digest(blob), source_torque_CSV_sha256=saved['metrics']['torque_csv_sha256'],
        measurement_protocol=record['measurement_protocol'], model_version=saved['model_version'],
        solver_settings_saved=verify_solver_settings(text, record['requested_protocol'].get('solver_settings')),
        torque_numerical_acceptance=saved['metrics']['torque_numerical_acceptance'],
        conductivity_S_per_m=float(value(body, 'conductivity')),
        mass_density_kg_per_m3=float(value(body, 'mass_density')),
        native_equivalent_cut_edge_depth_m=contract['curves']['core_loss_equiv_cut_depth_m'],
        declared_sheet_thickness_mm=source_metadata.get('thickness_mm'),
        source_native_quality_status=(source_metadata.get('native_quality') or {}).get('status', 'not_recorded'),
        source_strict_training_eligible=source_metadata.get('strict_training_eligible'),
        core_loss_status='uncalibrated_zero_placeholders', efficiency_enabled=False,
        directional_field_verified=False, reader_sha256=READER_SHA256,
        files_sha256={'actual_material.amat': digest(amat), 'actual_material_BH.csv': digest(curve)},
        scope='Material actually assigned in this saved direction-equivalent execution; a single directional source curve is used isotropically. No measured anisotropy or calibrated loss claim.')
    if representation=='Intrinsic':
        metadata.update(curve_representation='Intrinsic',applied_material_law='isotropic_intrinsic_BiH',
            native_AMAT_ordinate='Bi_T',CSV_B_definition='Bi+mu0H',Bi_definition='B-mu0H',source_points_unchanged=False,
            source_physical_B_preserved_at_nodes=True,
            physical_B_reconstruction_max_error_T=transport['physical_B_reconstruction_max_error_T'],
            physical_B_reconstruction_tolerance_T=transport['physical_B_reconstruction_tolerance_T'])
    p=record['requested_protocol']
    if p.get('points_per_cycle')==100:
        metadata['execution_sampling']=dict(points_per_cycle=100,official_samples=p['source_samples'],
            time_step_s=p['requested_time_step_s'],stop_s=p['stop_s'],
            selected_window_ms=p['selected_window_ms'],wall_budget_seconds=record['budget']['wall_seconds'])
    if catalog:
        metadata.update(status='engineering_direction_equivalent_catalog_loss',
                        core_loss_status='manufacturer_catalog_engineering_estimate',
                        catalog_loss=catalog, coefficients_saved=transport['coefficients_saved'],
                        native_loss_scope=saved['catalog_loss_native_scope'],
                        scope='Saved scalar RD-equivalent material and engineering loss coefficients estimated from the manufacturer catalog.')
    return dict(zip(FILES, (amat, (json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)+'\n').encode('utf8'), curve)))
