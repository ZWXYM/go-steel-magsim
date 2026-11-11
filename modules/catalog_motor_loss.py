"""Apply saved catalog estimates to an independent 101-point motor model."""
import math
from pathlib import Path

import numpy as np

from modules.material_library import sha
from modules.material_loss_reference import MaterialLossReference
from modules.maxwell_material_transport import (
    equivalent_material_name, equivalent_native_arguments, verify_saved_equivalent_curve,
)
from modules.motor_model_audit import one, value
from modules.motor_workbench import numeric_csv, unit


PROTOCOL = 'v8_2_cycle1_RD_equivalent_intrinsic_fine100_catalog_loss_v1'
MODEL = 'V8_2_scalar_RD_equivalent_intrinsic_fine100_catalog_loss_v1'
SNAPSHOT = 'inputs/catalog_loss_reference.json'
REPORT = 'case/maxwell_reports/Catalog Losses.csv'
OBJECT_REPORT = 'case/maxwell_reports/Catalog CoreLoss by Object.csv'
QUANTITIES = ('CoreLoss', 'StrandedLoss', 'SolidLoss')


def loss_contract(raw, metadata, material):
    snapshot = MaterialLossReference.decode(raw)
    grade = (metadata.get('native_quality') or {}).get('material_id')
    row = next((row for row in snapshot['records'] if row['grade'] == grade), None)
    if (row is None or metadata.get('prediction_mode') != 'known_anchor_calibration'
            or metadata.get('excluded_grades') != []
            or any((metadata.get(direction + '_anchor_weights') or {}).get(grade) != 1
                   for direction in ('RD', 'TD'))):
        raise ValueError('目录铁损仅适用于同牌号的四个已标定样品；请选择该牌号的拟合材料')
    curves = material['curves']
    if (metadata.get('thickness_mm') != row['nominal_thickness_mm']
            or curves['mass_density_kg_per_m3'] != row['assumed_density_kg_m3']
            or curves['conductivity_S_per_m'] != row['assumed_conductivity_S_m']):
        raise ValueError('目录估计的片厚、假定密度或电导率与保存材料不符')
    estimate = row['saved_estimate']
    coefficients = dict(Kh=estimate['Kh_fit_volumetric'], Kc=estimate['Kc_classical_volumetric'],
                        Ke=estimate['Ke_fit_volumetric'], Y=2, Kdc=0)
    if not any(coefficients[key] > 0 for key in ('Kh', 'Kc', 'Ke')):
        raise ValueError('所选牌号没有可用的非零目录损耗估计')
    return dict(grade=grade, reference_version=snapshot['version'],
                reference_sha256=snapshot['snapshot_sha256'], reference_kind=row['reference_kind'],
                reference_document=snapshot['documents'][row['document']],
                reference_direction=row['reference_direction'], specimen_measurement=False,
                estimate_status=estimate['status'], coefficients=coefficients,
                coefficient_units=snapshot['volumetric_units'],
                source_frequencies_Hz=sorted({p['f_Hz'] for p in row['points']}),
                operating_electrical_frequency_Hz=100, frequency_extrapolated=True,
                nominal_thickness_mm=row['nominal_thickness_mm'],
                density_kg_per_m3=row['assumed_density_kg_m3'],
                conductivity_S_per_m=row['assumed_conductivity_S_m'],
                equivalent_cut_depth_m=curves['core_loss_equiv_cut_depth_m'],
                saved_fit_rmse_W_kg=estimate['fit_rmse_w_kg'], source_warning=estimate['warning'])


def catalog_material_name(material):
    return equivalent_material_name(material, 'RD', 'Intrinsic') + '_CatalogLoss'


def catalog_native_arguments(material, loss):
    args = equivalent_native_arguments(material, 'RD', 'Intrinsic')
    args[0] = 'NAME:' + catalog_material_name(material)
    for key, expected in loss['coefficients'].items():
        prop = 'core_loss_' + key.lower() + ':='
        if prop in args:
            args[args.index(prop) + 1] = str(expected)
        else:
            args.extend([prop, str(expected)])
    return args


def verify_catalog_material(text, material, loss):
    name = catalog_material_name(material)
    body, curve = verify_saved_equivalent_curve(text, material, 'RD', 'Intrinsic', name)
    if value(one(body, 'core_loss_type'), 'Choice') != 'Electrical Steel':
        raise ValueError('保存的目录铁损材料没有 Electrical Steel 损耗定义')
    coefficients = {}
    for key, expected in loss['coefficients'].items():
        actual = value(body, 'core_loss_' + key.lower())
        if actual is None or not math.isclose(float(actual), expected, rel_tol=1e-10, abs_tol=1e-12):
            raise ValueError('实际保存目录损耗系数不符：' + key)
        coefficients[key] = float(actual)
    return dict(curve, coefficients_saved=coefficients, catalog_loss=loss,
                scope='Scalar RD-equivalent Intrinsic B-H with catalog-based engineering loss coefficients.')


def verify_loss_scope(text, audit):
    """Keep the copied V8_2 field settings and original 50 loss objects."""
    enabled = audit['core_loss']['enabled_object_names']
    inserts = [row['name'] for row in audit['inserts']]
    others = set(enabled) - set(inserts)
    if (len(enabled) != 50 or len(set(enabled)) != 50 or not set(inserts) <= set(enabled)
            or others != {'Stator', 'Rotor_1'}
            or audit['core_loss']['effect_on_field'] != 'false'
            or audit['setup']['OutputPerObjectCoreLoss'] != 'true'
            or one(one(text, 'GlobalBoundData'), 'EddyEffect').strip()
            or any(row['stacking_type'] not in (None, 'Solid') for row in audit['materials'])):
        raise ValueError('目录损耗需保留48插片与原定转子损耗范围、逐对象输出及现有场设置')
    return dict(core_loss_objects=enabled, inserts=inserts, core_loss_effect_on_field=False,
                eddy_effect_objects=[], insert_material_stacking='solid',
                cut_edge_effect_active=False, per_object_core_loss_requested=True)


def object_expressions(names):
    return [f'CoreLoss({name})' for name in names]


def _read_report(path, expressions, torque_time_ms):
    digest = sha(path.read_bytes())
    headers, values = numeric_csv(path)
    names = [column.split(' [', 1)[0] for column in headers]
    if len(names) != len(set(names)) or set(names) != {'Time', *expressions}:
        raise ValueError('官方目录损耗CSV字段与请求不符：' + path.name)
    columns = dict(zip(names, headers))
    time_column = columns['Time']
    times = values(time_column) * unit(time_column, {'s': 1., 'ms': 1e-3, 'us': 1e-6, 'ns': 1e-9})
    if (len(times) != len(torque_time_ms)
            or not np.allclose(times, np.asarray(torque_time_ms) * 1e-3, rtol=1e-8, atol=1e-10)):
        raise ValueError('官方目录损耗CSV与转矩时间网格不符：' + path.name)
    traces = {name: values(columns[name]) * unit(columns[name], {'W': 1., 'mW': 1e-3, 'kW': 1e3})
              for name in expressions}
    if sha(path.read_bytes()) != digest:
        raise ValueError('官方目录损耗CSV在读取期间改变：' + path.name)
    return times, traces, digest


def collect_catalog_losses(folder, torque_time_ms, scope, object_status):
    folder = Path(folder)
    times, traces, digest = _read_report(folder / REPORT, QUANTITIES, torque_time_ms)
    widths = np.diff(times)

    def average(trace):
        return float(np.dot((trace[:-1] + trace[1:]) * .5, widths) / (times[-1] - times[0]))

    data = dict(time_ms=list(torque_time_ms), official_samples=len(times),
                time_weighted_mean_W={name: average(trace) for name, trace in traces.items()},
                minima_W={name: float(trace.min()) for name, trace in traces.items()},
                traces_W={name: trace.tolist() for name, trace in traces.items()},
                csv_hashes={REPORT: digest}, object_report=object_status,
                efficiency_enabled=False, optimization_ranking_enabled=False)
    if object_status['status'] == 'exported':
        _, per_object, digest = _read_report(folder / OBJECT_REPORT,
                                            object_expressions(scope['core_loss_objects']), torque_time_ms)
        inserts = sum(per_object[key] for key in object_expressions(scope['inserts']))
        total = sum(per_object.values())
        closure = total - traces['CoreLoss']
        data['csv_hashes'][OBJECT_REPORT] = digest
        data['core_loss_breakdown'] = dict(
            insert_mean_W=average(inserts),
            other_mean_W={name: average(per_object[f'CoreLoss({name})']) for name in ('Stator', 'Rotor_1')},
            max_abs_closure_error_W=float(np.max(abs(closure))),
            closure_passed=bool(np.allclose(total, traces['CoreLoss'], rtol=1e-5, atol=1e-5)))
    return data
