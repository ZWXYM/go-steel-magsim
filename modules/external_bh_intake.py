"""Archive source tables and compare confirmed directions with saved material curves."""
import io
import json
import math
import re
import zipfile
from pathlib import PureWindowsPath

from flask import Blueprint, abort, redirect, render_template, request, url_for
from werkzeug.exceptions import RequestEntityTooLarge

from modules.calibration_regularized import default_eligible
from modules.external_bh_validation import MAX_CURVE_BYTES, compare_curves, field_weighted_errors, read_measurement_csv
from modules.system_data import csv_bytes, json_bytes, sha256, workpack_bytes


VERSION = 'external_bh_validation_v1'
PACKAGE_FILENAME = 'external_bh_validation.zip'
DIRECTIONS = ('RD', 'TD')
SOURCE_LABELS = {
    'measurement': '实验原表（用户提供）',
    'published_measurement': '公开实测',
    'digitized': '图表数字化',
    'reference': '参考曲线',
    'demonstration': '流程联调',
    'unspecified': '来源类别待核对',
}
CURVE_LABELS = {
    'initial': '初始磁化曲线',
    'dc_magnetization': '直流磁化曲线',
    'normal_peak': '正常磁化曲线（峰值）',
    'unspecified': '曲线类型待核对',
}
EXCITATION_LABELS = {'dc': '直流', 'quasistatic': '准静态', 'ac': '交流', 'unspecified': '激励条件待核对'}
SELECTION = re.compile(r'(cal_[0-9a-f]{12})\|([A-Za-z0-9_-]+)\|([0-9a-f]{64})')
SECONDARY_SELECTION = re.compile(r'(cal_[0-9a-f]{12})\|([0-9a-f]{64})')


def _source(form):
    text_fields = ('source_reference', 'reported_grade', 'manufacturer', 'specimen_id', 'method', 'conditions')
    data = {key: form.get(key, '').strip() or None for key in text_fields}
    if not data['source_reference']:
        raise ValueError('请填写原表来源或测量记录编号')
    if any(value is not None and len(value) > 1200 for value in data.values()):
        raise ValueError('来源字段最多 1200 字')
    for field, labels in (('source_kind', SOURCE_LABELS), ('curve_kind', CURVE_LABELS),
                          ('excitation', EXCITATION_LABELS)):
        value = form.get(field, 'unspecified')
        if value not in labels:
            raise ValueError('来源类别、曲线种类或激励选项无效')
        data[field] = value
    for field in ('thickness_mm', 'frequency_hz', 'temperature_C'):
        raw = form.get(field, '').strip()
        try:
            value = float(raw) if raw else None
        except ValueError as error:
            raise ValueError('片厚、频率和温度请填写数值或留空') from error
        if value is not None and (not math.isfinite(value) or (field != 'temperature_C' and value <= 0)):
            raise ValueError('片厚、频率须为正有限数值，温度须为有限数值')
        data[field] = value
    if data['curve_kind'] == 'dc_magnetization':
        if data['excitation'] not in ('dc', 'unspecified') or data['frequency_hz'] is not None:
            raise ValueError('直流磁化曲线应采用直流激励，频率请留空')
        data['excitation'] = 'dc'
    data['direction_and_pairing_declared'] = form.get('paired') == 'yes'
    return data


def _focus_range(form):
    low, high = (form.get(key, '').strip() for key in ('focus_h_min', 'focus_h_max'))
    if not low and not high:
        return None
    try:
        return float(low), float(high)
    except ValueError as error:
        raise ValueError('指定 H 区间须同时填写起点和终点（A/m）') from error


class ExternalBHIntake:
    def __init__(self, materials, packages):
        self.materials = materials
        self.packages = packages

    def _report(self, artifact):
        path = (self.materials.storage / artifact / 'report.json').resolve()
        if not path.is_relative_to(self.materials.storage):
            raise ValueError('校准报告位于任务存储之外')
        raw = path.read_bytes()
        report = json.loads(raw)
        if report['id'] != artifact or report['H_axis'] != 'physical_A_per_m':
            raise ValueError('校准记录身份或物理 H 轴不符')
        return report, sha256(raw)

    def choices(self):
        """List saved pairs cheaply. The library verifies the chosen materials on submission."""
        groups = []
        for path in self.materials.storage.glob('cal_*/report.json'):
            artifact = path.parent.name
            if not re.fullmatch(r'cal_[0-9a-f]{12}', artifact):
                continue
            try:
                report, digest = self._report(artifact)
                grades = sorted(set(report['known_material_exports']) & set(report['holdout_material_exports']))
                options = [dict(grade=grade, value='|'.join((artifact, grade, digest))) for grade in grades]
                if options:
                    groups.append(dict(id=artifact, label=report.get('display_label') or report['calibration_version'],
                                       created_utc=report.get('created_utc') or '', default=default_eligible(report),
                                       version_value='|'.join((artifact, digest)),
                                       options=options))
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return sorted(groups, key=lambda group: (group['default'], group['created_utc'], group['id']), reverse=True)

    def _models(self, selection):
        match = SELECTION.fullmatch(selection)
        if match is None:
            raise ValueError('请从列表选择已有校准与牌号')
        artifact, grade, expected = match.groups()
        report, digest = self._report(artifact)
        if digest != expected:
            raise ValueError('校准报告已变化，请重新选择')
        fit = self.materials.detail(artifact, 'fit:' + grade)
        holdout = self.materials.detail(artifact, 'holdout:' + grade)
        models = {'fit': fit, 'holdout': holdout}
        if (fit['evidence_scope'] != 'same_material_fit' or fit['excluded_grades']
                or holdout['evidence_scope'] != 'whole_material_exclusion' or holdout['excluded_grades'] != [grade]
                or grade not in fit['training_grades']
                or set(holdout['training_grades']) != set(fit['training_grades']) - {grade}
                or fit['parent_bank_sha256'] != report['bank_sha256']
                or fit['parent_bank_sha256'] != holdout['parent_bank_sha256']
                or any(model['files']['source_report.json']['sha256'] != expected for model in models.values())):
            raise ValueError('拟合与留出的校准来源或训练牌号不一致')
        identity = dict(calibration_id=artifact, calibration_version=report['calibration_version'],
                        grade=grade, source_report_sha256=expected, parent_bank_sha256=report['bank_sha256'])
        identity['models'] = {
            name: dict(material_key=model['key'], evidence_scope=model['evidence_scope'],
                       effective_bank_sha256=model['effective_bank_sha256'], training_grades=model['training_grades'],
                       excluded_grades=model['excluded_grades'], material_amat_sha256=model['files']['material.amat']['sha256'])
            for name, model in models.items()
        }
        return identity, models

    def save(self, form, uploads):
        mode = form.get('mode')
        if mode not in ('compare', 'archive'):
            raise ValueError('请选择比较或仅归档')
        source = _source(form)
        if not uploads:
            raise ValueError('请添加原始 CSV 文件')
        if set(uploads).difference(DIRECTIONS):
            raise ValueError('原表方向须为 RD 或 TD')
        summary = dict(version=VERSION, mode=mode, source=source, inputs={}, comparison={}, model=None,
                       specimen_independence_verified=False)
        files = {}
        for direction, (filename, blob) in uploads.items():
            if not 0 < len(blob) <= MAX_CURVE_BYTES:
                raise ValueError(f'{direction} CSV 为空或超过 2 MiB')
            member = f'original/{direction}.csv'
            files[member] = blob
            summary['inputs'][direction] = dict(original_filename=PureWindowsPath(filename).name,
                                                archive_path=member, sha256=sha256(blob), bytes=len(blob))
        title = '磁化曲线原表'
        if mode == 'compare':
            focus = _focus_range(form)
            if source['curve_kind'] == 'unspecified':
                raise ValueError('比较前请选择原表的曲线种类')
            directions = [direction for direction in DIRECTIONS if direction in uploads]
            if len(directions) == 1:
                if form.get('single_direction_confirmed') != 'yes':
                    raise ValueError('请确认单份原表的测量方向，或仅归档原表')
                source.update(direction=directions[0], direction_and_pairing_declared=False)
                summary['comparison_scope'] = 'single_direction'
            elif not source['direction_and_pairing_declared']:
                raise ValueError('同时比较 RD 和 TD 时，请确认两份原表的方向及可比条件')
            identity, models = self._models(form.get('selection', ''))
            reported_grade = source['reported_grade']
            if reported_grade and reported_grade.casefold() != identity['grade'].casefold():
                raise ValueError('原表标注牌号与所选模型不同；请重新选择对应牌号，或仅归档原表')
            comparison_identity = None
            secondary = form.get('secondary_selection', '')
            if secondary:
                match = SECONDARY_SELECTION.fullmatch(secondary)
                if match is None or match[1] == identity['calibration_id']:
                    raise ValueError('请选择另一条校准记录作为对照')
                selection = '|'.join((match[1], identity['grade'], match[2]))
                comparison_identity, comparison_models = self._models(selection)
                models.update({'secondary_' + name: model for name, model in comparison_models.items()})
            summary.update(model=identity, H_axis='physical_A_per_m',
                           declared_grade_status='matches_selected' if reported_grade else 'not_recorded',
                           method='Linear interpolation on physical H. All selected curves use the same original input rows in the common range. Errors are saved B minus input B.')
            if comparison_identity:
                summary['comparison_model'] = comparison_identity
            saved_rows = []
            for direction in directions:
                measurement = read_measurement_csv(uploads[direction][1], direction)
                curves = {name: model['curves'][direction] for name, model in models.items()}
                compared = compare_curves(measurement, curves)
                compared['field_weighted'] = field_weighted_errors(measurement, curves, focus=focus)
                files[f'comparison/{direction}.csv'] = csv_bytes(compared.pop('rows'))
                summary['comparison'][direction] = compared
                for name, curve in curves.items():
                    saved_rows.extend(dict(model=name, direction=direction, H_A_per_m=h, B_T=b)
                                      for h, b in zip(curve['H'], curve['B']))
            files['model_curves.csv'] = csv_bytes(saved_rows)
            title = '/'.join(directions) + ' 原表比较 · ' + identity['grade']
        files['external_bh_report.json'] = json_bytes(summary)
        description = SOURCE_LABELS[source['source_kind']] + '。' + (
            '保留上传字节、物理 H 比较、两种训练范围和保存曲线。' if mode == 'compare' else '保留上传字节和自报来源。')
        return self.packages.register_bytes(workpack_bytes(files), title, description, source_filename=PACKAGE_FILENAME)

    def read(self, artifact):
        record, blob = self.packages.read(artifact)
        if record.get('source_filename') != PACKAGE_FILENAME:
            raise ValueError('工作包类型不符')
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            report = json.loads(archive.read('external_bh_report.json'))
        if report['version'] != VERSION:
            raise ValueError('磁化曲线原表版本不符')
        return record, report


def create_external_bh_routes(intake):
    bp = Blueprint('external_bh', __name__)
    labels = dict(source_labels=SOURCE_LABELS, curve_labels=CURVE_LABELS, excitation_labels=EXCITATION_LABELS)

    def form_page(form=None, error=None):
        return render_template('external_bh_validation.html', groups=intake.choices(), form=form or {},
                               error=error, **labels)

    @bp.get('/external-bh-validation')
    def page():
        return form_page()

    @bp.post('/external-bh-validation')
    def save():
        request.max_content_length = 2 * MAX_CURVE_BYTES + 64 * 1024
        form = {}
        try:
            form = request.form.to_dict()
            uploads = {}
            for direction in DIRECTIONS:
                files = [upload for upload in request.files.getlist(direction) if upload.filename]
                if len(files) > 1:
                    raise ValueError(f'{direction} 只接受一份原表')
                if files:
                    upload, = files
                    uploads[direction] = (upload.filename, upload.stream.read(MAX_CURVE_BYTES + 1))
            record = intake.save(form, uploads)
        except RequestEntityTooLarge:
            return form_page(form, '每份 CSV 最多 2 MiB'), 413
        except (ValueError, OSError, KeyError, TypeError) as error:
            return form_page(form, str(error)), 400
        return redirect(url_for('.result', artifact=record['id']), code=303)

    @bp.get('/external-bh-validation/<artifact>')
    def result(artifact):
        try:
            record, report = intake.read(artifact)
        except (ValueError, OSError, KeyError, TypeError):
            abort(404)
        return render_template('external_bh_validation.html', record=record, report=report, **labels)

    return bp
