"""Validate frequency scaling on one declared specimen and one source table."""
import csv
import io
import json
import math
import zipfile
from collections import defaultdict
from pathlib import PureWindowsPath

import numpy as np
from flask import Blueprint, abort, redirect, render_template, request, url_for
from scipy.optimize import nnls
from werkzeug.exceptions import RequestEntityTooLarge

from modules.external_bh_intake import SOURCE_LABELS
from modules.external_bh_validation import MAX_CURVE_BYTES
from modules.loop_frequency_series import SERIES_FILENAME, available_cycles, collect_loop_series
from modules.measured_loop_loss import DIRECTION_LABELS
from modules.system_data import csv_bytes, json_bytes, sha256, workpack_bytes


VERSION = 'frequency_loss_validation_v2'
LOOP_SERIES_REPORT_VERSION = 'frequency_loss_validation_v3_cycles'
READABLE_VERSIONS = {VERSION, LOOP_SERIES_REPORT_VERSION, 'frequency_loss_validation_v1'}
PACKAGE_FILENAME = 'frequency_loss_validation.zip'
SOURCE_KINDS = dict(SOURCE_LABELS, reference_statistics='公开测量参考统计')
AMPLITUDE_COLUMNS = {'peak_B_T': 'B', 'peak_J_T': 'J', 'peak_polarization_J_T': 'J'}
LOSS_COLUMNS = {
    'specific_loss_W_per_kg': ('W/kg', 'standard_u_W_per_kg'),
    'specific_power_loss_xref_W_per_kg': ('W/kg', 'reference_u_W_per_kg'),
    'volume_loss_W_per_m3': ('W/m³', 'standard_u_W_per_m3'),
    'specific_energy_loss_J_per_kg': ('W/kg', 'standard_u_J_per_kg'),
    'volume_energy_loss_J_per_m3': ('W/m³', 'standard_u_J_per_m3'),
}
ENERGY_INPUT_UNITS = {'specific_energy_loss_J_per_kg': 'J/kg', 'volume_energy_loss_J_per_m3': 'J/m³'}
OBSERVED_PEAK_COLUMNS = {'observed_peak_B_T': 'B', 'observed_peak_J_T': 'J'}
CONFIGURATION_COLUMNS = ('configuration', 'nominal_strip_configuration')
MAX_ROWS = 4000


def _positive(raw, label):
    try:
        value = float(raw)
    except (ValueError, TypeError) as error:
        raise ValueError(f'{label}须为正有限数值') from error
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f'{label}须为正有限数值')
    return value


def read_frequency_table(blob):
    if not blob or len(blob) > MAX_CURVE_BYTES:
        raise ValueError('损耗表须为非空 CSV，最多 2 MiB')
    try:
        stream = io.StringIO(blob.decode('utf-8-sig'), newline='')
    except UnicodeDecodeError as error:
        raise ValueError('请使用 UTF-8 CSV') from error
    reader = csv.DictReader(stream, strict=True)
    try:
        columns = reader.fieldnames or []
        amplitude = [name for name in AMPLITUDE_COLUMNS if name in columns]
        loss = [name for name in LOSS_COLUMNS if name in columns]
        config = [name for name in CONFIGURATION_COLUMNS if name in columns]
        if (len(columns) != len(set(columns)) or 'frequency_Hz' not in columns
                or len(amplitude) != 1 or len(loss) != 1 or len(config) > 1):
            raise ValueError('表头须有频率、一种峰值量和一种损耗单位；列名不可重复')
        amplitude_column, loss_column = amplitude[0], loss[0]
        observed = [column for column in OBSERVED_PEAK_COLUMNS if column in columns]
        if observed and (len(observed) > 1 or OBSERVED_PEAK_COLUMNS[observed[0]] != AMPLITUDE_COLUMNS[amplitude_column]):
            raise ValueError('实测峰值列须与分组峰值使用相同的 B 或 J')
        unit, u_column = LOSS_COLUMNS[loss_column]
        energy_unit = ENERGY_INPUT_UNITS.get(loss_column)
        if any(u != u_column and u in columns for _, u in LOSS_COLUMNS.values()):
            raise ValueError('标准不确定度列应与损耗列对应')
        identity = {key: set() for key in ('sample_label', 'table_variant', 'direction') if key in columns}
        rows, seen = [], set()
        for row in reader:
            if None in row or None in row.values() or len(rows) >= MAX_ROWS:
                raise ValueError(f'第 {reader.line_num} 行列数不符，或超过 {MAX_ROWS} 个数据点')
            i = reader.line_num
            for key, values in identity.items():
                value = row[key].strip()
                if not value or len(value) > 1200:
                    raise ValueError(f'第 {i} 行 {key} 须填写明确值')
                values.add(value)
            f = _positive(row['frequency_Hz'], f'第 {i} 行频率')
            peak = _positive(row[amplitude_column], f'第 {i} 行峰值')
            if (peak, f) in seen:
                raise ValueError(f'第 {i} 行峰值与频率组合重复，请分开不同测量系列')
            seen.add((peak, f))
            u = row.get(u_column, '').strip()
            configuration = row[config[0]].strip() if config else None
            page = row.get('page', '').strip()
            if any(len(value or '') > 1200 for value in (configuration, page)):
                raise ValueError(f'第 {i} 行原表配置或页码过长')
            input_loss = _positive(row[loss_column], f'第 {i} 行损耗')
            input_u = _positive(u, f'第 {i} 行标准不确定度') if u else None
            point = dict(source_row=i, frequency_Hz=f, peak_T=peak, loss=input_loss, standard_u=input_u,
                         configuration=configuration or None, source_page=page or None)
            if energy_unit:
                point.update(input_energy_per_cycle=input_loss, input_energy_standard_u=input_u,
                             loss=_positive(input_loss*f, f'第 {i} 行换算后的功率损耗'),
                             standard_u=_positive(input_u*f, f'第 {i} 行换算后的标准不确定度')
                             if input_u is not None else None)
            if observed:
                measured = _positive(row[observed[0]], f'第 {i} 行实测峰值')
                point.update(observed_peak_T=measured, observed_minus_group_peak_T=measured-peak)
            rows.append(point)
    except csv.Error as error:
        raise ValueError('CSV 列或引号格式不正确') from error
    if not rows or any(len(values) != 1 for values in identity.values()):
        raise ValueError('每张表只能包含一个试样、方向和统计版本')
    table = dict(rows=rows, identity={key: next(iter(values)) for key, values in identity.items()},
                 amplitude=AMPLITUDE_COLUMNS[amplitude_column], loss_unit=unit,
                 amplitude_column=amplitude_column, loss_column=loss_column)
    if energy_unit:
        table['input_energy_unit'] = energy_unit
    if observed:
        table['observed_peak_column'] = observed[0]
    return table


def _nonnegative_prediction(train, holdout, uncertainties):
    frequencies = [row['frequency_Hz'] for row in train]
    f_scale = frequencies[-1]
    y_scale = max(row['loss'] for row in train)
    weighted = all(u is not None for u in uncertainties)
    min_u = min(uncertainties) if weighted else None
    weights = np.array([min_u/u for u in uncertainties]) if weighted else np.ones(len(train))
    powers = np.array([1., 1.5, 2.])
    x = (np.array(frequencies)/f_scale)[:, None] ** powers
    design = x*weights[:, None]
    values = np.array([row['loss']/y_scale for row in train]) * weights
    try:
        if min(weights) <= 0 or np.linalg.matrix_rank(design) < len(powers):
            raise ValueError('频率或不确定度的跨度导致拟合矩阵退化')
        scaled_coeff, _ = nnls(design, values)
    except (RuntimeError, np.linalg.LinAlgError) as error:
        raise ValueError('无法稳定拟合，请核对频率和损耗范围') from error
    coeff = [float(value)*y_scale for value in scaled_coeff]
    return (float(scaled_coeff @ ((holdout/f_scale)**powers))*y_scale,
            dict(basis_status='fit', weighting='inverse_standard_u' if weighted else 'equal',
                 basis_scale_Hz=f_scale, basis_coefficients=coeff))


def _fit_group(rows, holdout, peak):
    train = [row for row in rows if row['frequency_Hz'] != holdout]
    target = next((row for row in rows if row['frequency_Hz'] == holdout), None)
    frequencies = [row['frequency_Hz'] for row in train]
    if target is None or len(train) < 2 or not frequencies[0] < holdout < frequencies[-1]:
        raise ValueError(f'峰值 {peak:g} T 需有留出值，且两侧各有至少一档训练频率')
    actual = target['loss']
    low = next(row for row in reversed(train) if row['frequency_Hz'] < holdout)
    high = next(row for row in train if row['frequency_Hz'] > holdout)
    fraction = (holdout-low['frequency_Hz'])/(high['frequency_Hz']-low['frequency_Hz'])
    uncertainties = [row['standard_u'] for row in train]
    predictions = {}
    if len(train) >= 4:
        predictions['nonnegative_fit'], fit = _nonnegative_prediction(train, holdout, uncertainties)
    else:
        fit = dict(basis_status='insufficient_training_frequencies', weighting=None,
                   basis_scale_Hz=None, basis_coefficients=None)
    predictions.update(linear_lowest=train[0]['loss']*(holdout/frequencies[0]),
                       bracket_cycle_linear=(low['loss']*((1-fraction)*(holdout/low['frequency_Hz']))
                                             + high['loss']*(fraction*(holdout/high['frequency_Hz']))))
    errors = {name: dict(predicted=value, signed_error=value-actual,
                         signed_relative_error_pct=100*((value-actual)/actual))
              for name, value in predictions.items()}
    numbers = (fit['basis_coefficients'] or []) + [value for row in errors.values() for value in row.values()]
    if not all(math.isfinite(value) for value in numbers):
        raise ValueError('预测或误差超出有限数值范围，请核对原表单位和量级')
    return dict(peak_T=peak, actual_loss=actual, actual_standard_u=target['standard_u'],
                holdout_source_row=target['source_row'], training_frequencies_Hz=frequencies,
                training_source_rows=[row['source_row'] for row in train],
                bracket_Hz=[low['frequency_Hz'], high['frequency_Hz']],
                missing_training_standard_u=uncertainties.count(None),
                estimates=errors, **fit)


def evaluate_frequency_holdout(table, holdout):
    holdout = _positive(holdout, '留出频率')
    groups = defaultdict(list)
    for row in table['rows']:
        groups[row['peak_T']].append(row)
    return [_fit_group(sorted(rows, key=lambda row: row['frequency_Hz']), holdout, peak)
            for peak, rows in sorted(groups.items())]


def _peak_matching(table, raw_tolerance):
    if 'observed_peak_column' not in table:
        if raw_tolerance:
            raise ValueError('填写峰值允许偏差前，请在表中保留对应的实测峰值列')
        return None
    try:
        tolerance = float(raw_tolerance)
    except ValueError as error:
        raise ValueError('有实测峰值列时，请填写相对分组峰值的允许偏差（T）') from error
    if not math.isfinite(tolerance) or tolerance < 0:
        raise ValueError('允许的峰值偏差须为非负有限数值')
    worst = max(table['rows'], key=lambda row: abs(row['observed_minus_group_peak_T']))
    maximum = abs(worst['observed_minus_group_peak_T'])
    if maximum > tolerance and not math.isclose(maximum, tolerance, rel_tol=1e-12):
        raise ValueError(f"第 {worst['source_row']} 行实测峰值偏差 {maximum:g} T 超出 {tolerance:g} T")
    return dict(observed_peak_column=table['observed_peak_column'], tolerance_T=tolerance,
                maximum_abs_deviation_T=maximum)


class FrequencyLossValidation:
    def __init__(self, packages):
        self.packages = packages

    def save(self, form, filename, blob, *, cycle_series=None, cycle_files=None):
        table = read_frequency_table(blob)
        identity = table['identity']
        matching = _peak_matching(table, form.get('peak_tolerance_T', '').strip())
        fields = ('source_reference', 'specimen_id', 'reported_grade', 'method', 'conditions')
        source = {field: form.get(field, '').strip() or None for field in fields}
        source_kind = form.get('source_kind', 'unspecified')
        direction = form.get('direction', 'unspecified')
        if not source['source_reference'] or any(len(value or '') > 1200 for value in source.values()):
            raise ValueError('请填写表格来源或测量编号，文本字段最多 1200 字')
        if source_kind not in SOURCE_KINDS or direction not in DIRECTION_LABELS:
            raise ValueError('来源类别或测量方向无效')
        if form.get('one_series') != 'yes':
            raise ValueError('请确认原表只含一个试样、一个方向和一个统计版本')
        if 'sample_label' in identity:
            if source['specimen_id'] not in (None, identity['sample_label']):
                raise ValueError('填写的试样与表内 sample_label 不同')
            source['specimen_id'] = identity['sample_label']
        if not source['specimen_id']:
            raise ValueError('请填写原试样标识，或提供表内 sample_label')
        if 'direction' in identity:
            if identity['direction'] not in DIRECTION_LABELS or direction not in ('unspecified', identity['direction']):
                raise ValueError('填写的方向与表内 direction 不符，或表内方向无效')
            direction = identity['direction']
        source.update(source_kind=source_kind, direction=direction, input_table_identity=identity)
        holdout = _positive(form.get('holdout_frequency_Hz'), '留出频率')
        evaluations = evaluate_frequency_holdout(table, holdout)
        rows = [dict(row, role='holdout' if row['frequency_Hz'] == holdout else 'training') for row in table['rows']]
        configurations = sorted({row['configuration'] for row in rows if row['configuration'] is not None})
        report = dict(version=LOOP_SERIES_REPORT_VERSION if cycle_series else VERSION, source=source, table={k: table[k] for k in
                      ('amplitude', 'loss_unit', 'amplitude_column', 'loss_column')}, rows=rows,
                      configurations=configurations, configuration_missing_rows=sum(r['configuration'] is None for r in rows),
                      holdout_frequency_Hz=holdout, evaluations=evaluations,
                      original=dict(filename=PureWindowsPath(filename).name, bytes=len(blob), sha256=sha256(blob)))
        if 'input_energy_unit' in table:
            report['table']['input_energy_unit'] = table['input_energy_unit']
        if matching is not None:
            report['table']['peak_matching'] = matching
        if cycle_series:
            report['cycle_series'] = cycle_series
        compare = [dict(peak_T=result['peak_T'], holdout_frequency_Hz=holdout, loss_unit=table['loss_unit'],
                        holdout_source_row=result['holdout_source_row'], actual_loss=result['actual_loss'],
                        actual_standard_u=result['actual_standard_u'], basis_status=result['basis_status'],
                        weighting=result['weighting'] if method == 'nonnegative_fit' else None, estimator=method, **error)
                   for result in evaluations for method, error in result['estimates'].items()]
        files = {'original/loss_table.csv': blob, 'frequency_loss_report.json': json_bytes(report),
                 'holdout_comparison.csv': csv_bytes(compare), **(cycle_files or {})}
        title = '多频损耗留出复核 · ' + source['specimen_id'][:120]
        return self.packages.register_bytes(workpack_bytes(files), title, SOURCE_KINDS[source_kind] + ' · 同试样频率留出',
                                            source_filename=PACKAGE_FILENAME)

    def save_cycles(self, form, chosen):
        if form.get('one_series') != 'yes':
            raise ValueError('请确认回线来自同试样、同方向和共同试验条件')
        try:
            selected = [dict(zip(('id', 'sha256'), value.split('|', 1), strict=True)) for value in chosen]
            tolerance = float(form.get('tolerance_T', ''))
        except ValueError as error:
            raise ValueError('回线选择或允许峰值偏差无效') from error
        source, table, series, files = collect_loop_series(self.packages, selected,
            quantity=form.get('quantity'), peak_T=_positive(form.get('peak_T'), '目标峰值'),
            tolerance_T=tolerance, loss_basis=form.get('loss_basis'))
        data = {key: (value or '') for key, value in source.items()}
        data.update(one_series='yes', holdout_frequency_Hz=form.get('holdout_frequency_Hz', ''))
        return self.save(data, SERIES_FILENAME, table, cycle_series=series, cycle_files=files)

    def read(self, artifact):
        record, blob = self.packages.read(artifact)
        if record.get('source_filename') != PACKAGE_FILENAME:
            raise ValueError('工作包类型不符')
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            report = json.loads(archive.read('frequency_loss_report.json'))
        if report['version'] not in READABLE_VERSIONS:
            raise ValueError('多频工作包版本不符')
        return record, report


def create_frequency_loss_routes(service):
    bp = Blueprint('frequency_loss', __name__)
    labels = dict(source_kinds=SOURCE_KINDS, direction_labels=DIRECTION_LABELS)

    @bp.get('/frequency-loss-validation')
    def page():
        return render_template('frequency_loss_validation.html', form={}, **labels)

    @bp.post('/frequency-loss-validation')
    def save():
        request.max_content_length = MAX_CURVE_BYTES + 64*1024
        form = {}
        try:
            form = request.form.to_dict()
            upload = request.files.get('loss_table')
            if upload is None or not upload.filename:
                raise ValueError('请选择一份多频损耗 CSV')
            record = service.save(form, upload.filename, upload.stream.read(MAX_CURVE_BYTES + 1))
        except RequestEntityTooLarge:
            return render_template('frequency_loss_validation.html', form=form, error='损耗表最多 2 MiB', **labels), 413
        except (ValueError, OSError, TypeError) as error:
            return render_template('frequency_loss_validation.html', form=form, error=str(error), **labels), 400
        return redirect(url_for('.result', artifact=record['id']), code=303)

    @bp.get('/frequency-loss-validation/<artifact>')
    def result(artifact):
        try:
            record, report = service.read(artifact)
        except (ValueError, OSError, KeyError, TypeError):
            abort(404)
        return render_template('frequency_loss_validation.html', record=record, report=report, **labels)

    @bp.get('/frequency-loss-validation/loops')
    def cycles_page():
        return render_template('frequency_loss_cycles.html', cycles=available_cycles(service.packages), form={}, selected=[], **labels)

    @bp.post('/frequency-loss-validation/loops')
    def cycles_save():
        request.max_content_length = 64*1024
        form, chosen = {}, []
        try:
            form = request.form.to_dict()
            chosen = request.form.getlist('loop')
            record = service.save_cycles(form, chosen)
        except RequestEntityTooLarge:
            return render_template('frequency_loss_cycles.html', cycles=[], form=form, selected=[],
                                   error='回线选择请求过大', **labels), 413
        except (ValueError, OSError, KeyError, TypeError) as error:
            return render_template('frequency_loss_cycles.html', cycles=available_cycles(service.packages), form=form,
                                   selected=chosen, error=str(error), **labels), 400
        return redirect(url_for('.result', artifact=record['id']), code=303)

    return bp
