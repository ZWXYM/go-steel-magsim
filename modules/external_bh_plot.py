"""Plot inputs from verified saved curves, clipped to their physical support."""
import csv
import io
import re

import numpy as np

from modules.external_bh_validation import read_measurement_csv


def curve_windows(summary, *, candidate=None):
    directions = summary.get('comparison', {})
    result = {}
    for direction in ('RD','TD'):
        if direction not in directions or (candidate is not None and direction not in candidate):
            continue
        row = directions[direction]
        common = candidate[direction]['H_range_A_per_m'] if candidate is not None else row.get('overlap_H_range_A_per_m')
        if not common or common[0] >= common[1]:
            continue
        result[direction] = {'common': common}
        focus = (row.get('field_weighted') or {}).get('bands', {}).get('focus', {}).get('H_range_A_per_m')
        if focus and common[0] <= focus[0] < focus[1] <= common[1]:
            result[direction]['focus'] = focus
    return result


def _limit(rows, max_points):
    if len(rows) <= max_points:
        return rows
    return [rows[i] for i in np.linspace(0, len(rows)-1, max_points, dtype=int)]


def _clipped_line(h, b, low, high):
    x, y = np.asarray(h, dtype=float), np.asarray(b, dtype=float)
    if (len(x) < 2 or x.ndim != 1 or y.shape != x.shape
            or not np.all(np.isfinite(x)) or not np.all(np.isfinite(y)) or not np.all(x[1:] > x[:-1])
            or not x[0] <= low < high <= x[-1]):
        raise ValueError('绘图范围超出已保存曲线的物理 H 支持')
    grid = np.concatenate(([low], x[(x > low) & (x < high)], [high]))
    values = np.interp(grid, x, y)
    if not np.all(np.isfinite(values)):
        raise ValueError('绘图插值超出有限数值范围')
    rows = list(zip(grid.tolist(), values.tolist()))
    return _limit(rows, 900), len(rows) > 900


def measured_chart(blob, direction, bounds, curves):
    measurement = read_measurement_csv(blob, direction)
    original = measurement['points']
    if measurement['source_order'] == 'descending':
        original = original[::-1]
    low, high = bounds
    h, b = ([point[key] for point in original] for key in ('H_A_per_m', 'input_B_T'))
    measured, limited = _clipped_line(h, b, low, high)
    observed = [[p['H_A_per_m'], p['input_B_T'], p['source_row']]
                for p in original if low <= p['H_A_per_m'] <= high]
    markers = _limit(observed, 240)
    series = [dict(key='measured', label='原表（实心点）', line=measured)]
    for key, label, x, y in curves:
        points, clipped = _clipped_line(x, y, low, high)
        series.append(dict(key=key, label=label, line=points))
        limited |= clipped
    return dict(direction=direction,H_range_A_per_m=bounds,series=series,points=markers,
                original_points_in_range=len(observed),display_downsampled=limited or len(markers)!=len(observed))


def _short_version(model):
    version = model.get('calibration_version', '')
    match = re.search(r'_v(\d+)$', version)
    return 'V'+match[1] if match else model['calibration_id']


def comparison_plot_curves(blob, report, direction):
    model = report['model']
    primary = _short_version(model)
    secondary = report.get('comparison_model')
    other = _short_version(secondary) if secondary else None
    if other == primary:
        primary += ' · ' + model['calibration_id']
        other += ' · ' + secondary['calibration_id']
    labels = {'fit': primary+' 拟合', 'holdout': primary+' 整牌号留出'}
    if secondary:
        labels.update(secondary_fit=other+' 拟合',secondary_holdout=other+' 整牌号留出')
    curves = {key: ([], []) for key in labels}
    rows = csv.DictReader(io.StringIO(blob.decode('utf-8-sig'), newline=''), strict=True)
    try:
        for row in rows:
            if row['direction'] == direction and row['model'] in curves:
                h,b = curves[row['model']]
                h.append(float(row['H_A_per_m']))
                b.append(float(row['B_T']))
    except csv.Error as error:
        raise ValueError('保存的模型曲线表格式无效') from error
    if secondary and curves['fit'] == curves['secondary_fit']:
        labels['fit'] = primary+'/'+other+' 拟合（重合）'
        del labels['secondary_fit']
    return [(key,label,*curves[key]) for key,label in labels.items()]
