"""Build a bounded, monotone representation of an initial measurement table."""
import math

import numpy as np

from modules.external_bh_validation import field_weighted_errors


CANDIDATE_VERSION = 'measured_bh_candidate_v1'
CANDIDATE_FILENAME = 'measured_bh_candidate.zip'
MAX_NODES = 26


def candidate_eligible(report):
    source, model, inputs = (report.get(key) for key in ('source', 'model', 'inputs'))
    if not all(isinstance(item, dict) for item in (source, model, inputs)):
        return False
    grade, model_grade = source.get('reported_grade'), model.get('grade')
    if (report.get('mode') != 'compare' or report.get('declared_grade_status') != 'matches_selected'
            or source.get('source_kind') not in ('measurement', 'published_measurement')
            or source.get('curve_kind') != 'initial' or not isinstance(grade, str) or not grade
            or not isinstance(model_grade, str) or grade.casefold() != model_grade.casefold()):
        return False
    directions = set(inputs)
    if directions == {'RD', 'TD'}:
        return source.get('direction_and_pairing_declared') is True
    return (directions in ({'RD'}, {'TD'}) and report.get('comparison_scope') == 'single_direction'
            and source.get('direction') in directions)


def _isotonic(values):
    """Equal-weight least-squares projection to nondecreasing ordinates."""
    blocks = []
    for value in values:
        blocks.append((float(value), 1))
        while len(blocks) > 1 and blocks[-2][0] > blocks[-1][0]:
            right, n_right = blocks.pop()
            left, n_left = blocks.pop()
            n = n_left+n_right
            blocks.append((left*(n_left/n) + right*(n_right/n), n))
    return np.repeat([mean for mean, _ in blocks], [count for _, count in blocks])


def _largest_residual_nodes(h, b, limit):
    if len(h) <= limit:
        return np.arange(len(h))
    selected = [0, len(h)-1]
    while len(selected) < limit:
        residual = np.abs(np.interp(h, h[selected], b[selected])-b)
        residual[selected] = -1
        index = int(np.argmax(residual))
        if residual[index] <= 0:
            break
        selected.append(index)
        selected.sort()
    return np.asarray(selected, dtype=int)


def build_measured_candidate(measurement, *, max_nodes=MAX_NODES):
    if not isinstance(max_nodes, int) or isinstance(max_nodes, bool) or not 2 <= max_nodes <= MAX_NODES:
        raise ValueError(f'实测候选节点上限须为 2–{MAX_NODES}')
    points = measurement['points']
    if measurement['source_order'] == 'descending':
        points = points[::-1]
    h = np.asarray([p['H_A_per_m'] for p in points])
    b = np.asarray([p['input_B_T'] for p in points])
    if (len(h) < 2 or not np.all(np.isfinite(h)) or not np.all(np.isfinite(b))
            or h[0] < 0 or not np.all(h[1:] > h[:-1]) or np.any(b < 0)):
        raise ValueError('实测候选须有至少两个递增的非负 H 点，且 B 为非负有限数值')
    monotone = _isotonic(b)
    indices = _largest_residual_nodes(h, monotone, max_nodes)
    curve = dict(H=h[indices].tolist(), B=monotone[indices].tolist())
    interpolated = np.interp(h, curve['H'], curve['B'])
    shifts = monotone-b
    errors = interpolated-b
    weighted = field_weighted_errors(measurement, {'candidate': curve})['bands']['common']['metrics']['candidate']
    trace = [dict(source_row=p['source_row'], H_A_per_m=float(x), source_B_T=float(y),
                  monotone_B_T=float(z), candidate_B_T=float(fitted))
             for p, x, y, z, fitted in zip(points, h, b, monotone, interpolated)]
    nodes = [dict(H_A_per_m=float(h[i]), B_T=float(monotone[i]),
                  source_row=points[i]['source_row'], source_B_T=float(b[i]))
             for i in indices]
    metrics = dict(source_points=len(points), candidate_nodes=len(indices), H_range_A_per_m=[float(h[0]), float(h[-1])],
                   altered_source_points=int(np.count_nonzero(shifts)),
                   max_abs_monotone_shift_T=float(np.max(np.abs(shifts))),
                   max_abs_candidate_point_error_T=float(np.max(np.abs(errors))),
                   rmse_original_points_T=math.hypot(*(float(e)/math.sqrt(len(errors)) for e in errors)),
                   rmse_uniform_H_T=weighted['rmse_T'])
    return metrics, nodes, trace
