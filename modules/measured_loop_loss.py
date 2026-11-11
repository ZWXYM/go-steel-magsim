"""Integrate a source-bound, ordered magnetic hysteresis cycle."""
import io
import json
import math
import zipfile
from itertools import pairwise
from pathlib import PureWindowsPath

from flask import Blueprint, abort, redirect, render_template, request, url_for
from werkzeug.exceptions import RequestEntityTooLarge

from modules.external_bh_intake import SOURCE_LABELS
from modules.external_bh_validation import MAX_CURVE_BYTES, MU0, ORDINATE_COLUMNS, read_bh_points
from modules.system_data import csv_bytes, json_bytes, sha256, workpack_bytes


VERSION = 'measured_loop_loss_v1'
PACKAGE_FILENAME = 'measured_loop_loss.zip'
DIRECTION_LABELS = {'unspecified': '待核对', 'RD': 'RD（轧向）', 'TD': 'TD（横向）', 'other': '其他方向'}


def _varies_both_ways(values):
    return any(b > a for a, b in pairwise(values)) and any(b < a for a, b in pairwise(values))


def integrate_loop(table, frequency_hz, density_kg_m3=None):
    if not math.isfinite(frequency_hz) or frequency_hz <= 0:
        raise ValueError('频率须为正有限数值')
    if density_kg_m3 is not None and (not math.isfinite(density_kg_m3) or density_kg_m3 <= 0):
        raise ValueError('密度须为正有限数值')
    points = table['points']
    h = [p['H_A_per_m'] for p in points]
    b = [p['input_B_T'] for p in points]
    ordinate, factor = ORDINATE_COLUMNS[table['ordinate_column']]
    if ordinate == 'J':
        j = [p['source_ordinate'] * factor for p in points]
    else:
        j = [b_i - MU0 * h_i for h_i, b_i in zip(h, b)]
    if (len(set(zip(h, b))) < 3 or not _varies_both_ways(h)
            or not _varies_both_ways(j if ordinate == 'J' else b)):
        raise ValueError('回线至少需三个不同的点，且 H 与所报 B/J 在原顺序均有上升和下降')

    segments = []
    for i, point in enumerate(points):
        next_index = (i + 1) % len(points)
        h_mid = 0.5 * h[i] + 0.5 * h[next_index]
        energy_B = h_mid * (b[next_index] - b[i])
        energy_J = h_mid * (j[next_index] - j[i])
        if not all(map(math.isfinite, (j[i], energy_B, energy_J))):
            raise ValueError('回线段或物理 J 超出有限数值范围')
        segments.append(dict(point, J_T=j[i], next_source_row=points[next_index]['source_row'],
                             closing_segment=next_index == 0, segment_HdB_J_m3=energy_B,
                             segment_HdJ_J_m3=energy_J))
    try:
        signed_B = math.fsum(row['segment_HdB_J_m3'] for row in segments)
        signed_J = math.fsum(row['segment_HdJ_J_m3'] for row in segments)
    except OverflowError as error:
        raise ValueError('回线积分超出有限数值范围') from error
    primary = 'HdJ' if ordinate == 'J' else 'HdB'
    energy = abs(signed_J if ordinate == 'J' else signed_B)
    volume_power = energy * frequency_hz
    mass_power = volume_power / density_kg_m3 if density_kg_m3 is not None else None
    closing_energy = segments[-1]['segment_' + primary + '_J_m3']
    closing_fraction = abs(closing_energy) / energy if energy else None
    result = dict(samples=len(points), H_column=table['H_column'], ordinate_column=table['ordinate_column'],
                  peak_abs_H_A_per_m=max(map(abs, h)), peak_abs_B_T=max(map(abs, b)), peak_abs_J_T=max(map(abs, j)),
                  energy_basis=primary, signed_HdB_J_m3=signed_B, signed_HdJ_J_m3=signed_J,
                  HdB_minus_HdJ_J_m3=signed_B-signed_J, energy_loss_J_m3_per_cycle=energy,
                  volume_loss_W_m3=volume_power, mass_loss_W_kg=mass_power,
                  closing_delta_H_A_per_m=h[0]-h[-1], closing_delta_B_T=b[0]-b[-1],
                  closing_delta_J_T=j[0]-j[-1], closing_energy_J_m3=closing_energy,
                  closing_absolute_energy_fraction=closing_fraction)
    if any(not math.isfinite(value) for value in result.values() if isinstance(value, float)):
        raise ValueError('回线损耗或闭合统计超出有限数值范围')
    return result, segments


def _positive(form, field, label, required=False):
    raw = form.get(field, '').strip()
    if not raw and not required:
        return None
    try:
        value = float(raw)
    except ValueError as error:
        raise ValueError(label + '须填写正有限数值') from error
    if not math.isfinite(value) or value <= 0:
        raise ValueError(label + '须填写正有限数值')
    return value


class MeasuredLoopLoss:
    def __init__(self, packages):
        self.packages = packages

    def save(self, form, filename, blob):
        fields = ('source_reference', 'reported_grade', 'specimen_id', 'method', 'conditions')
        source = {field: form.get(field, '').strip() or None for field in fields}
        if not source['source_reference'] or any(len(value or '') > 1200 for value in source.values()):
            raise ValueError('请填写来源或测量记录编号，文本字段最多 1200 字')
        if form.get('single_cycle') != 'yes':
            raise ValueError('请确认上传的是按采样顺序保存的一个完整周期')
        source_kind, direction = form.get('source_kind', 'unspecified'), form.get('direction', 'unspecified')
        if source_kind not in SOURCE_LABELS or direction not in DIRECTION_LABELS:
            raise ValueError('来源类别或测量方向无效')
        frequency = _positive(form, 'frequency_hz', '频率', required=True)
        density = _positive(form, 'density_kg_m3', '密度')
        density_reference = form.get('density_reference', '').strip() if density is not None else None
        if density is not None and not density_reference:
            raise ValueError('请填写所用试样密度的来源')
        if len(density_reference or '') > 1200:
            raise ValueError('密度来源最多 1200 字')
        table = read_bh_points(blob, '回线')
        result, segments = integrate_loop(table, frequency, density)
        source.update(source_kind=source_kind, direction=direction, frequency_hz=frequency, density_kg_m3=density,
                      density_reference=density_reference, single_cycle_declared=True)
        report = dict(version=VERSION, source=source, result=result, measurement_conditions_verified=False,
                      original=dict(filename=PureWindowsPath(filename).name, bytes=len(blob), sha256=sha256(blob)))
        files = {'original/cycle.csv': blob, 'ordered_segments.csv': csv_bytes(segments),
                 'loop_loss_report.json': json_bytes(report)}
        title = '实测回线核算' + (' · ' + source['reported_grade'][:120] if source['reported_grade'] else '')
        return self.packages.register_bytes(workpack_bytes(files), title, SOURCE_LABELS[source_kind] + ' · 单周期损耗核算。',
                                            source_filename=PACKAGE_FILENAME)

    def read(self, artifact):
        record, blob = self.packages.read(artifact)
        if record.get('source_filename') != PACKAGE_FILENAME:
            raise ValueError('工作包类型不符')
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            report = json.loads(archive.read('loop_loss_report.json'))
        if report['version'] != VERSION:
            raise ValueError('回线工作包版本不符')
        return record, report


def create_measured_loop_routes(loss):
    bp = Blueprint('measured_loop', __name__)
    labels = dict(source_labels=SOURCE_LABELS, direction_labels=DIRECTION_LABELS)

    @bp.get('/measured-loop-loss')
    def page():
        return render_template('measured_loop_loss.html', form={}, **labels)

    @bp.post('/measured-loop-loss')
    def save():
        request.max_content_length = MAX_CURVE_BYTES + 64 * 1024
        form = {}
        try:
            form = request.form.to_dict()
            upload = request.files.get('cycle')
            if upload is None or not upload.filename:
                raise ValueError('请选择一份回线 CSV')
            record = loss.save(form, upload.filename, upload.stream.read(MAX_CURVE_BYTES + 1))
        except RequestEntityTooLarge:
            return render_template('measured_loop_loss.html', form=form, error='回线 CSV 最多 2 MiB', **labels), 413
        except (ValueError, OSError, TypeError) as error:
            return render_template('measured_loop_loss.html', form=form, error=str(error), **labels), 400
        return redirect(url_for('.result', artifact=record['id']), code=303)

    @bp.get('/measured-loop-loss/<artifact>')
    def result(artifact):
        try:
            record, report = loss.read(artifact)
        except (ValueError, OSError, KeyError, TypeError):
            abort(404)
        return render_template('measured_loop_loss.html', record=record, report=report, **labels)

    return bp
