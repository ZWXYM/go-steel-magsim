"""Reconcile an ordered Results/DataPoints workbook with its reported loss values."""
import io
import json
import math
import re
import zipfile
from pathlib import PureWindowsPath
from xml.etree.ElementTree import ParseError

from defusedxml.common import DefusedXmlException
from flask import Blueprint, abort, redirect, render_template, request, url_for
from openpyxl import DEFUSEDXML, load_workbook
from openpyxl.utils.exceptions import InvalidFileException
from werkzeug.exceptions import RequestEntityTooLarge

from modules.external_bh_intake import SOURCE_LABELS
from modules.measured_loop_loss import DIRECTION_LABELS, integrate_loop
from modules.system_data import csv_bytes, json_bytes, sha256, workpack_bytes


VERSION = 'measured_loop_workbook_v1'
PACKAGE_FILENAME = 'measured_loop_workbook.zip'
ORIGINAL_PATH = 'original/workbook.xlsx'
REPORT_PATH = 'loop_workbook_report.json'
MAX_WORKBOOK_BYTES = 8 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 48 * 1024 * 1024
MAX_CYCLES = 64
MAX_POINTS = 80000
RESULT_COLUMNS = ('Measurement', 'Coil system', 'Sample', 'Signal form', 'Nominal',
                  'Frequency [Hz]', 'Bmax [T]', 'Hmax [A/m]', 'Ps [W/kg]')
POINT_COLUMNS = ('Measurement', 'No.', 'H [A/m]', 'B [T]')
DECIMAL = r'[0-9]+(?:\.[0-9]+)?'
NOMINAL = re.compile(rf'({DECIMAL})\s*T')
GROUP = re.compile(rf'({DECIMAL})\s*T\s*/\s*({DECIMAL})\s*Hz')


def _read_rows(sheet, columns, limit):
    # Use the actual rows. Some workbook writers supply stale dimension metadata.
    sheet.reset_dimensions()
    rows = enumerate(sheet.iter_rows(max_col=len(columns), values_only=True), 1)
    first = next(rows, None)
    if first is None or first[1] != columns:
        raise ValueError(f'{sheet.title} 表头或单位与导入格式不符')
    for number, values in rows:
        if number > limit:
            raise ValueError(f'{sheet.title} 超过可读取行数')
        if any(value is not None for value in values):
            yield number, values


def _positive_integer(value):
    if isinstance(value, str) and re.fullmatch(r'[0-9]{1,9}', value):
        value = int(value)
    if type(value) is not int or value <= 0:
        raise ValueError('编号须为正整数')
    return value


def _number(value, *, minimum=None):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError('数值须为有限数值单元格')
    if minimum is not None and value < minimum:
        raise ValueError('所报峰值、频率或损耗超出有效范围')
    return value


def _bounded_xlsx(blob):
    if not 0 < len(blob) <= MAX_WORKBOOK_BYTES:
        raise ValueError('工作簿为空或超过 8 MiB')
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            entries = archive.infolist()
            if (len(entries) > 256 or sum(entry.file_size for entry in entries) > MAX_UNCOMPRESSED_BYTES
                    or len({entry.filename.casefold() for entry in entries}) != len(entries)
                    or any(entry.flag_bits & 1 for entry in entries)
                    or '[Content_Types].xml' not in archive.namelist()):
                raise ValueError('工作簿内容超出读取范围或使用了加密')
    except zipfile.BadZipFile as error:
        raise ValueError('无法读取 XLSX 工作簿') from error
    if not DEFUSEDXML:
        raise ValueError('工作簿读取环境缺少 defusedxml，请按依赖清单安装')


def _result_records(sheet):
    result, measurement_ids = {}, set()
    for row, values in _read_rows(sheet, RESULT_COLUMNS, 2 * MAX_CYCLES + 1):
        try:
            identifier, coil, sample, signal, nominal, frequency, peak_b, peak_h, loss = values
            identifier = _positive_integer(identifier)
            if not all(isinstance(x, str) and x.strip() and len(x) <= 120 and not x.startswith('=')
                       for x in (coil, sample, signal, nominal)):
                raise ValueError('缺少 Coil system、Sample、Signal form 或 Nominal')
            matched = NOMINAL.fullmatch(nominal.strip())
            if matched is None:
                raise ValueError('名义磁密须标注 T，例如 1.5 T')
            nominal_b = float(matched[1])
            if not math.isfinite(nominal_b) or nominal_b <= 0 or _number(frequency) <= 0:
                raise ValueError('名义磁密和频率须为正数')
            key = (nominal_b, frequency)
            if identifier in measurement_ids or key in result:
                raise ValueError('测量编号或名义磁密与频率组合重复')
            if len(result) == MAX_CYCLES:
                raise ValueError(f'单个工作簿最多读取 {MAX_CYCLES} 个周期')
            measurement_ids.add(identifier)
            result[key] = dict(measurement=identifier, results_row=row, coil_system=coil.strip(), sample=sample.strip(),
                               signal_form=signal.strip(), nominal_B_T=nominal_b, frequency_Hz=frequency,
                               reported_Bmax_T=_number(peak_b, minimum=0), reported_Hmax_A_per_m=_number(peak_h, minimum=0),
                               reported_Ps_W_kg=_number(loss, minimum=0))
        except ValueError as error:
            raise ValueError(f'Results 第 {row} 行：{error}') from error
    if not result:
        raise ValueError('Results 没有可复核的测量记录')
    return result


def _cycle_points(sheet, results):
    groups = {key: [] for key in results}
    count = 0
    for row, (label, sample_no, h, b) in _read_rows(sheet, POINT_COLUMNS, MAX_POINTS + MAX_CYCLES + 1):
        try:
            match = GROUP.fullmatch(label.strip()) if isinstance(label, str) else None
            key = tuple(map(float, match.groups())) if match else None
            if key not in groups:
                raise ValueError('测量标签须匹配 Results 的名义磁密与频率，例如 1.5T/50Hz')
            points = groups[key]
            if _positive_integer(sample_no) != len(points) + 1 or (points and row != points[-1]['source_row'] + 1):
                raise ValueError('每个周期的 No. 须从 1 连续递增，原表行须连续')
            if count == MAX_POINTS:
                raise ValueError(f'工作簿最多读取 {MAX_POINTS} 个采样点')
            h, b = _number(h), _number(b)
            points.append(dict(source_row=row, source_sample_no=sample_no, H_A_per_m=h, input_B_T=b))
            count += 1
        except ValueError as error:
            raise ValueError(f'DataPoints 第 {row} 行：{error}') from error
    return groups


def replay_workbook(blob):
    _bounded_xlsx(blob)
    try:
        workbook = load_workbook(io.BytesIO(blob), read_only=True, data_only=False, keep_links=False)
        try:
            if not {'Results', 'DataPoints'} <= set(workbook.sheetnames):
                raise ValueError('工作簿须包含 Results 和 DataPoints')
            results = _result_records(workbook['Results'])
            groups = _cycle_points(workbook['DataPoints'], results)
        finally:
            workbook.close()
    except (InvalidFileException, ParseError, DefusedXmlException, zipfile.BadZipFile, KeyError,
            TypeError, IndexError, AttributeError, RuntimeError, OSError, OverflowError) as error:
        raise ValueError('无法读取工作簿中的原始数值') from error

    summaries, segments = [], []
    for key, original in results.items():
        points = groups[key]
        if not points:
            raise ValueError(f'Results 第 {original["results_row"]} 行没有对应采样周期')
        table = dict(H_column='H [A/m]', ordinate_column='B [T]', points=points)
        try:
            integrated, cycle_segments = integrate_loop(table, original['frequency_Hz'])
        except ValueError as error:
            raise ValueError(f'Results 第 {original["results_row"]} 行的回线无效：{error}') from error
        at_peak = max(points, key=lambda point: point['input_B_T'])
        summaries.append(dict(
            original, first_data_row=points[0]['source_row'], last_data_row=points[-1]['source_row'],
            sampled_positive_B_peak_T=at_peak['input_B_T'], H_at_positive_B_peak_A_per_m=at_peak['H_A_per_m'],
            positive_peak_data_row=at_peak['source_row'], **integrated,
        ))
        segments.extend(dict(measurement=original['measurement'], **segment) for segment in cycle_segments)
    return summaries, segments


class MeasuredLoopWorkbook:
    def __init__(self, packages):
        self.packages = packages

    def save(self, form, filename, blob):
        source_reference = form.get('source_reference', '').strip()
        reported_grade = form.get('reported_grade', '').strip() or None
        source_kind = form.get('source_kind', 'unspecified')
        direction = form.get('direction', 'unspecified')
        if not source_reference or len(source_reference) > 1200:
            raise ValueError('请填写来源或测量记录编号，最多 1200 字')
        if len(reported_grade or '') > 120:
            raise ValueError('牌号最多 120 字')
        if source_kind not in SOURCE_LABELS or direction not in DIRECTION_LABELS:
            raise ValueError('来源类别或测量方向无效')
        if form.get('ordered_cycles') != 'yes':
            raise ValueError('请确认每组数据按原采样顺序记录一个周期')
        if PureWindowsPath(filename).suffix.lower() != '.xlsx':
            raise ValueError('请选择 .xlsx 工作簿')
        cycles, segments = replay_workbook(blob)
        samples = list(dict.fromkeys(cycle['sample'] for cycle in cycles))
        source = dict(source_reference=source_reference, source_kind=source_kind, direction=direction,
                      workbook_sample_labels=samples, reported_grade=reported_grade)
        report = dict(version=VERSION, source=source, original=dict(filename=PureWindowsPath(filename).name,
                      bytes=len(blob), sha256=sha256(blob)), cycles=cycles, point_count=len(segments),
                      ordered_cycles_declared=True, measurement_conditions_verified=False)
        # Excel interprets some text prefixes as formulas when opening a CSV.
        summary_rows = [{key: "'" + value if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@'))
                         else value for key, value in cycle.items()} for cycle in cycles]
        files = {ORIGINAL_PATH: blob, REPORT_PATH: json_bytes(report), 'cycle_summary.csv': csv_bytes(summary_rows),
                 'ordered_segments.csv': csv_bytes(segments)}
        title = '实测回线工作簿' + (' · ' + samples[0] if len(samples) == 1 else '')
        return self.packages.register_bytes(workpack_bytes(files), title, f'{len(cycles)} 个周期的原表与逐段复算。',
                                            source_filename=PACKAGE_FILENAME)

    def read(self, artifact):
        record, blob = self.packages.read(artifact)
        if record.get('source_filename') != PACKAGE_FILENAME:
            raise ValueError('工作包类型不符')
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            report = json.loads(archive.read(REPORT_PATH))
            if (report['version'] != VERSION or archive.getinfo(ORIGINAL_PATH).file_size != report['original']['bytes']
                    or sha256(archive.read(ORIGINAL_PATH)) != report['original']['sha256']):
                raise ValueError('工作簿原表与报告不符')
        return record, report


def create_measured_loop_workbook_routes(service):
    bp = Blueprint('measured_loop_workbook', __name__)
    labels = dict(source_labels=SOURCE_LABELS, direction_labels=DIRECTION_LABELS)

    @bp.get('/measured-loop-workbook')
    def page():
        return render_template('measured_loop_workbook.html', form={}, **labels)

    @bp.post('/measured-loop-workbook')
    def save():
        request.max_content_length = MAX_WORKBOOK_BYTES + 64 * 1024
        form = {}
        try:
            form = request.form.to_dict()
            upload = request.files.get('workbook')
            if upload is None or not upload.filename:
                raise ValueError('请选择工作簿')
            record = service.save(form, upload.filename, upload.stream.read(MAX_WORKBOOK_BYTES + 1))
        except RequestEntityTooLarge:
            return render_template('measured_loop_workbook.html', form=form, error='工作簿最多 8 MiB', **labels), 413
        except (ValueError, OSError) as error:
            return render_template('measured_loop_workbook.html', form=form, error=str(error), **labels), 400
        return redirect(url_for('.result', artifact=record['id']), code=303)

    @bp.get('/measured-loop-workbook/<artifact>')
    def result(artifact):
        try:
            record, report = service.read(artifact)
        except (ValueError, OSError, KeyError, TypeError, zipfile.BadZipFile):
            abort(404)
        return render_template('measured_loop_workbook.html', record=record, report=report, **labels)

    return bp
