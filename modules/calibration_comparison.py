"""Compare saved whole-material holdouts on the same source curves and folds."""
import csv
import io
import json
import math
import re
import zipfile
from pathlib import Path

from modules.calibration_transfer import error_metrics
from modules.system_data import json_bytes, sha256


VERSION = 'saved_calibration_comparison_v1'
METRICS = ('rmse_T', 'mae_T', 'B800_error_T', 'B1000_error_T')
METHODS = {'calibration_fit': 'corrected_B', 'nested_material_holdout': 'holdout_B'}


def _load_report(storage, artifact, expected):
    if not re.fullmatch(r'cal_[0-9a-f]{12}', artifact or '') or not re.fullmatch(r'[0-9a-f]{64}', expected or ''):
        raise ValueError('请选择两个已保存的校准记录')
    path = (storage/artifact/'report.json').resolve()
    if path.parent.parent != storage:
        raise ValueError('校准报告位于当前存储之外')
    blob = path.read_bytes()
    if sha256(blob) != expected:
        raise ValueError('所选报告已变化，请刷新后重新选择')
    report = json.loads(blob)
    if report['id'] != artifact or report['H_axis'] != 'physical_A_per_m':
        raise ValueError('报告身份或物理 H 轴不符')
    return report, blob


def _unique_rows(rows, key, expected, message):
    result = {key(row): row for row in rows}
    if len(result) != len(rows) or set(result) != expected:
        raise ValueError(message)
    return result


def _read_curves(report, grades, expected):
    folds = _unique_rows(report['nested_validation']['outer_folds'], lambda row: row['heldout'],
                         grades, '校准报告缺少完整的逐牌号留出折')
    for grade, fold in folds.items():
        training = fold['selection']['training_grades']
        if set(training) != grades - {grade} or len(training) != len(grades) - 1:
            raise ValueError('留出训练集与对应牌号不符：'+grade)
    return _unique_rows(report['comparisons'], lambda row: (row['grade'], row['direction']), expected,
                        '校准报告缺少完整的牌号与方向曲线')


def _verify_metrics(report, curves):
    metrics = _unique_rows([row for row in report['metrics'] if row['method'] in METHODS],
                           lambda row: (row['grade'], row['direction'], row['method']),
                           {(*key, method) for key in curves for method in METHODS}, '保存的拟合或整牌号留出指标不完整')
    for key, curve in curves.items():
        h = curve['H']
        if not h or h[0] > 800 or h[-1] < 1000:
            raise ValueError('物理 H 范围没有覆盖 B800 和 B1000：' + ' / '.join(key))
        for method, column in METHODS.items():
            calculated = error_metrics(h, curve[column], curve['reference_B'])
            saved = metrics[(*key, method)]
            for metric in METRICS:
                if not math.isclose(saved[metric], calculated[metric], abs_tol=1e-12, rel_tol=1e-10):
                    raise ValueError('保存误差与原曲线不符：' + ' / '.join((*key, method, metric)))
    return metrics


def compare_calibrations(storage, baseline, candidate, baseline_sha, candidate_sha):
    if baseline == candidate:
        raise ValueError('请选择两个不同的校准记录')
    storage = Path(storage).resolve()
    left, left_bytes = _load_report(storage, baseline, baseline_sha)
    right, right_bytes = _load_report(storage, candidate, candidate_sha)
    source_fields = ('source_manifest_sha256', 'source_bank_sha256', 'physics_version',
                     'texture_sampling_version', 'source_native_evidence', 'evidence_status')
    if any(left[key] != right[key] for key in source_fields):
        raise ValueError('两个报告的材料来源或仿真协议不同，不能配对比较')
    grades = set(left['selected_grades'])
    if (len(grades) < 2 or set(right['selected_grades']) != grades
            or any(len(report['selected_grades']) != len(grades) for report in (left,right))):
        raise ValueError('两个报告须包含相同的一组留出牌号')
    keys = {(grade, direction) for grade in grades for direction in ('RD', 'TD')}
    lc, rc = [_read_curves(report, grades, keys) for report in (left, right)]
    for key in keys:
        if any(lc[key][field] != rc[key][field] for field in ('H', 'raw_B', 'reference_B')):
            raise ValueError('原始、参考曲线或物理场节点不同：'+' / '.join(key))
    lm, rm = _verify_metrics(left, lc), _verify_metrics(right, rc)
    rows = []
    for grade, direction in sorted(keys):
        row = dict(grade=grade, direction=direction, source_H_points=len(lc[(grade,direction)]['H']))
        for method, prefix in (('calibration_fit','fit'), ('nested_material_holdout','holdout')):
            a, b = [values[(grade,direction,method)] for values in (lm,rm)]
            for metric in METRICS:
                row[prefix+'_baseline_'+metric] = a[metric]
                row[prefix+'_candidate_'+metric] = b[metric]
            row[prefix+'_delta_rmse_T'] = b['rmse_T']-a['rmse_T']
            row[prefix+'_delta_abs_B800_error_T'] = abs(b['B800_error_T'])-abs(a['B800_error_T'])
        rows.append(row)

    def identity(report, digest, side):
        holdout = [row['holdout_'+side+'_rmse_T'] for row in rows]
        b800 = [abs(row['holdout_'+side+'_B800_error_T']) for row in rows]
        fit = [row['fit_'+side+'_rmse_T'] for row in rows]
        return dict(id=report['id'], report_sha256=digest, calibration_version=report['calibration_version'],
                    bank_sha256=report['bank_sha256'], fit_mean_rmse_T=math.fsum(fit)/len(fit),
                    material_holdout_mean_rmse_T=math.fsum(holdout)/len(holdout),
                    material_holdout_max_abs_B800_error_T=max(b800))

    deltas = [row['holdout_delta_rmse_T'] for row in rows]
    result = dict(version=VERSION, baseline=identity(left,baseline_sha,'baseline'),
                  candidate=identity(right,candidate_sha,'candidate'), rows=rows, grades=sorted(grades),
                  improved_holdout_directions=sum(delta < -1e-12 for delta in deltas),
                  worsened_holdout_directions=sum(delta > 1e-12 for delta in deltas),
                  source_manifest_sha256=left['source_manifest_sha256'], H_axis=left['H_axis'],
                  scope='Existing same-source nested whole-material holdouts and fits; retrospective development comparison.',
                  new_fits=0, new_predictions=0, new_native_solves=0)
    return result, {'baseline/report.json': left_bytes, 'candidate/report.json': right_bytes}


def comparison_csv(result):
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream,fieldnames=list(result['rows'][0]))
    writer.writeheader()
    writer.writerows(result['rows'])
    return stream.getvalue().encode('utf-8-sig')


def comparison_bundle(result, reports):
    files = dict(reports, **{'comparison.json':json_bytes(result), 'comparison.csv':comparison_csv(result)})
    files['manifest.json'] = json_bytes(dict(version='system_data_workpack_v5',private_only=True,
                                              files={name:sha256(blob) for name,blob in files.items()}))
    stream = io.BytesIO()
    with zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED) as archive:
        for name, blob in files.items():
            archive.writestr(zipfile.ZipInfo(name), blob, compress_type=zipfile.ZIP_DEFLATED)
    stream.seek(0)
    return stream
