"""Compare original magnetization samples on a shared physical H range."""
import csv
import io
import math
from itertools import chain

import numpy as np


MAX_CURVE_BYTES = 2 * 1024 * 1024
MU0 = 4e-7 * math.pi
H_COLUMNS = {
    'H_A_per_m': 1.0, 'H [A/m]': 1.0, 'H (A/m)': 1.0,
    'H_kA_per_m': 1000.0, 'H [kA/m]': 1000.0, 'H (kA/m)': 1000.0,
}
ORDINATE_COLUMNS = {
    'B_T': ('B', 1.0), 'B [T]': ('B', 1.0), 'B (T)': ('B', 1.0),
    'B_mT': ('B', 0.001), 'B [mT]': ('B', 0.001), 'B (mT)': ('B', 0.001),
    'J_T': ('J', 1.0), 'J [T]': ('J', 1.0), 'J (T)': ('J', 1.0),
    'J_mT': ('J', 0.001), 'J [mT]': ('J', 0.001), 'J (mT)': ('J', 0.001),
}


def _table_rows(reader):
    """Some source catalogs put the explicit unit header in a CSV comment."""
    comment_header = []
    for row in reader:
        cells = [cell.strip() for cell in row]
        if not any(cells):
            continue
        if cells[0].startswith('#'):
            cells[0] = cells[0][1:].strip()
            if any(column in H_COLUMNS for column in cells) and any(column in ORDINATE_COLUMNS for column in cells):
                comment_header = cells
            continue
        if not comment_header or any(column in H_COLUMNS or column in ORDINATE_COLUMNS for column in cells):
            return cells, reader
        return comment_header, chain([row], reader)
    return comment_header, reader


def read_measurement_csv(blob, direction):
    if not isinstance(blob, bytes) or not 0 < len(blob) <= MAX_CURVE_BYTES:
        raise ValueError(f'{direction} CSV 为空或超过 2 MiB')
    try:
        content = blob.decode('utf-8-sig')
    except UnicodeError as error:
        raise ValueError(f'{direction} 请保存为 UTF-8 CSV') from error
    reader = csv.reader(io.StringIO(content, newline=''), strict=True)
    try:
        header, data_rows = _table_rows(reader)
        h_columns = [i for i, column in enumerate(header) if column in H_COLUMNS]
        ordinate_columns = [i for i, column in enumerate(header) if column in ORDINATE_COLUMNS]
        if len(h_columns) != 1 or len(ordinate_columns) != 1:
            raise ValueError(f'{direction} CSV 需明确标注一列 H 及一列 B 或 J，例如 H_A_per_m,B_T')
        h_index, = h_columns
        ordinate_index, = ordinate_columns
        h_scale = H_COLUMNS[header[h_index]]
        ordinate, ordinate_scale = ORDINATE_COLUMNS[header[ordinate_index]]
        points = []
        for cells in data_rows:
            if not any(cell.strip() for cell in cells) or cells[0].lstrip().startswith('#'):
                continue
            try:
                if len(cells) != len(header):
                    raise ValueError
                h_source = float(cells[h_index])
                y_source = float(cells[ordinate_index])
            except ValueError as error:
                raise ValueError(f'{direction} CSV 第 {reader.line_num} 行缺少有效的 H 或 B/J') from error
            h = h_source * h_scale
            b = y_source * ordinate_scale
            if ordinate == 'J':
                b += MU0 * h
            if h < 0 or not math.isfinite(h) or not math.isfinite(b):
                raise ValueError(f'{direction} CSV 第 {reader.line_num} 行须为非负有限 H 和有限 B/J')
            points.append(dict(source_row=reader.line_num, source_H=h_source, source_ordinate=y_source,
                               H_A_per_m=h, input_B_T=b))
    except csv.Error as error:
        raise ValueError(f'{direction} CSV 第 {reader.line_num} 行格式无效') from error
    if len(points) < 2:
        raise ValueError(f'{direction} 至少需要两个采样点')
    field = [p['H_A_per_m'] for p in points]
    if all(a < b for a, b in zip(field, field[1:])):
        source_order = 'ascending'
    elif all(a > b for a, b in zip(field, field[1:])):
        source_order = 'descending'
    else:
        raise ValueError(f'{direction} 的 H 须严格单调。请提供单条磁化曲线，完整回线需单独处理')
    return dict(H_column=header[h_index], ordinate_column=header[ordinate_index], ordinate=ordinate,
                source_order=source_order, points=points)


def _model_curve(curve):
    h = np.asarray(curve['H'], dtype=float)
    b = np.asarray(curve['B'], dtype=float)
    if (h.ndim != 1 or b.shape != h.shape or len(h) < 2 or not np.all(np.isfinite(h))
            or not np.all(np.isfinite(b)) or not np.all(h[1:] > h[:-1])):
        raise ValueError('保存的模型曲线缺少严格递增的物理 H')
    return h, b


def compare_direction(measurement, fitted, heldout):
    """Use the same original measurement rows for both sets of errors."""
    curves = {'fit': _model_curve(fitted), 'holdout': _model_curve(heldout)}
    h_min = min(measurement['points'][0]['H_A_per_m'], measurement['points'][-1]['H_A_per_m'])
    h_max = max(measurement['points'][0]['H_A_per_m'], measurement['points'][-1]['H_A_per_m'])
    low = max(h_min, *(curve[0][0] for curve in curves.values()))
    high = min(h_max, *(curve[0][-1] for curve in curves.values()))
    errors = {name: [] for name in curves}
    rows = []
    for point in measurement['points']:
        h = point['H_A_per_m']
        row = dict(point, in_comparison=bool(low <= h <= high))
        for name, (grid, values) in curves.items():
            model_b = float(np.interp(h, grid, values)) if row['in_comparison'] else None
            residual = model_b - point['input_B_T'] if model_b is not None else None
            if residual is not None:
                if not math.isfinite(residual):
                    raise ValueError('模型与原表差值超出有限数值范围')
                errors[name].append(residual)
            row[name + '_B_T'] = model_b
            row[name + '_error_T'] = residual
        rows.append(row)

    count = len(errors['fit'])
    metrics = {}
    for name, values in errors.items():
        metrics[name] = dict(
            rmse_T=math.hypot(*(v / math.sqrt(count) for v in values)),
            mae_T=math.fsum(abs(v) / count for v in values),
        ) if count else None
    b800 = None
    if low <= 800 <= high:
        points = measurement['points']
        ascending = points if measurement['source_order'] == 'ascending' else reversed(points)
        h, b = zip(*((p['H_A_per_m'], p['input_B_T']) for p in ascending))
        input_b = float(np.interp(800, h, b))
        b800 = dict(input_B_T=input_b, at_original_point=800 in h)
        for name, (grid, values) in curves.items():
            b800[name + '_B_T'] = float(np.interp(800, grid, values))
            b800[name + '_error_T'] = b800[name + '_B_T'] - input_b
        if any(not math.isfinite(value) for value in b800.values()):
            raise ValueError('B800 插值或误差超出有限数值范围')

    return dict(H_column=measurement['H_column'], ordinate_column=measurement['ordinate_column'],
                source_order=measurement['source_order'], measurement_points=len(rows),
                measurement_H_range_A_per_m=[h_min, h_max], overlap_H_range_A_per_m=[float(low), float(high)]
                if low <= high else None, compared_points=count, metrics=metrics, B800=b800, rows=rows)
