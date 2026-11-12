"""Build a fixed-amplitude frequency table from comparable saved cycle integrals."""
import io
import json
import math
import zipfile

from modules.external_bh_validation import ORDINATE_COLUMNS
from modules.measured_loop_loss import MeasuredLoopLoss, PACKAGE_FILENAME as LOOP_PACKAGE_FILENAME
from modules.system_data import csv_bytes
from modules.system_data_reference_evidence import collect_reference_package, validate_reference_selection


SERIES_VERSION = 'frequency_loss_loop_series_v1'
SERIES_FILENAME = 'loss_from_saved_cycles.csv'
CONDITIONS = ('source_reference', 'source_kind', 'specimen_id', 'reported_grade', 'direction', 'method', 'conditions')
MASS_VOLUME = {'volume': ('volume_loss_W_m3', 'volume_loss_W_per_m3'),
               'mass': ('mass_loss_W_kg', 'specific_loss_W_per_kg')}


def _conditions_known(source):
    return (source.get('direction') in ('RD', 'TD', 'other')
            and all(isinstance(source.get(field), str) and source[field].strip()
                    for field in ('source_reference', 'specimen_id', 'method', 'conditions')))


def available_cycles(packages):
    cycles = MeasuredLoopLoss(packages)
    rows = []
    for record in packages.records():
        if not record['readable'] or record.get('source_filename') != LOOP_PACKAGE_FILENAME:
            continue
        try:
            _, report = cycles.read(record['id'])
            source, result = report['source'], report['result']
            quantity = ORDINATE_COLUMNS[result['ordinate_column']][0]
            rows.append(dict(id=record['id'], sha256=record['sha256'], source=source, quantity=quantity,
                             peak_T=result[f'peak_abs_{quantity}_T'], eligible=_conditions_known(source)))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return rows


def collect_loop_series(packages, selected, *, quantity, peak_T, tolerance_T, loss_basis):
    selected = validate_reference_selection(selected)
    if len(selected) < 3:
        raise ValueError('请选择至少三档不同频率的已保存回线')
    if quantity not in ('B', 'J') or loss_basis not in MASS_VOLUME:
        raise ValueError('请选择原表幅值量和损耗单位')
    if not math.isfinite(peak_T) or not math.isfinite(tolerance_T) or not 0 <= tolerance_T < peak_T:
        raise ValueError('目标峰值须为正，允许偏差须大于等于零且小于峰值')
    loss_field, loss_column = MASS_VOLUME[loss_basis]
    peak_column = f'peak_{quantity}_T'
    baseline, density = None, None
    sources, files, seen_raw = {}, {}, set()
    for choice in selected:
        evidence, blob = collect_reference_package(packages, choice)
        if evidence['kind'] != 'ordered_loop':
            raise ValueError('只可使用已保存的单周期回线核算')
        raw_sha = evidence['originals']['original/cycle.csv']['sha256']
        if raw_sha in seen_raw:
            raise ValueError('同一原回线不可按不同频率重复使用')
        seen_raw.add(raw_sha)
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            report = json.loads(archive.read(evidence['report_path']))
        source, result = report['source'], report['result']
        if not _conditions_known(source) or source.get('single_cycle_declared') is not True:
            raise ValueError('每条回线须声明试样、方向、共同测量条件、方法和一个完整周期')
        identity = {field: source.get(field) for field in CONDITIONS}
        if baseline is None:
            baseline = identity
        elif identity != baseline:
            raise ValueError('所选回线的来源、试样、方向或共同测量条件不同')
        if ORDINATE_COLUMNS.get(result.get('ordinate_column'), (None,))[0] != quantity:
            raise ValueError('所选回线原表的峰值量不统一，不能混用 B 和 J')
        actual_peak = result[f'peak_abs_{quantity}_T']
        if not math.isfinite(actual_peak) or abs(actual_peak-peak_T) > tolerance_T:
            raise ValueError(f'回线 {evidence["id"]} 实际峰值 {actual_peak:g} T 超出允许偏差')
        if loss_basis == 'mass':
            current_density = (source.get('density_kg_m3'), source.get('density_reference'))
            if current_density[0] is None or not current_density[1] or (density and density != current_density):
                raise ValueError('按质量比较时，回线须使用相同且有来源的试样密度')
            density = current_density
        f, loss = source['frequency_hz'], result[loss_field]
        if loss is None or not math.isfinite(loss) or loss <= 0:
            raise ValueError('每档回线核算须有正的有限损耗')
        if f in sources:
            raise ValueError('每个频率只选一条回线，不混用同频不同幅值或重复测量')
        path = f'source_cycles/{evidence["id"]}.zip'
        files[path] = blob
        sources[f] = dict(id=evidence['id'], sha256=evidence['sha256'], path=path, original_csv_sha256=raw_sha,
                          frequency_Hz=f, observed_peak_T=actual_peak, loss=loss, samples=result['samples'],
                          closing_absolute_energy_fraction=result['closing_absolute_energy_fraction'])
    sources = [sources[f] for f in sorted(sources)]
    table = csv_bytes([{'frequency_Hz': row['frequency_Hz'], peak_column: peak_T, loss_column: row['loss']}
                       for row in sources])
    series = dict(version=SERIES_VERSION, peak_T=peak_T, tolerance_T=tolerance_T, amplitude=quantity,
                  loss_basis=loss_basis, cycles=sources, density_kg_m3=density[0] if density else None,
                  density_reference=density[1] if density else None, measurement_conditions_independently_verified=False)
    return baseline, table, series, files
