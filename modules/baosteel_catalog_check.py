"""Read the frozen, separate Baosteel Table D.1 comparison for display."""
import json
import math
from pathlib import Path


SOURCE = 'go_steel_data/output/baosteel_D1_catalog_check_v1.json'
VERSION = 'baosteel_D1_catalog_check_v1'
GRADES = ('B23R075', 'B27R090', 'B27R095', 'B30P105')


def read_catalog_check(project):
    root = Path(project).resolve()
    path = (root / SOURCE).resolve()
    try:
        if not path.is_relative_to(root):
            raise ValueError('来源不属于程序目录')
        raw = path.read_bytes()
        data = json.loads(raw)
        if (data['version'] != VERSION or data['H_axis'] != 'physical_A_per_m'
                or data['actual_material_changed'] is not False
                or data['independent_sample_validation'] is not False
                or tuple(row['grade'] for row in data['RD_B800_rows']) != GRADES
                or tuple(row['J_peak_T'] for row in data['B23_frequency_rows']) != (1.5, 1.7)):
            raise ValueError('来源类型、牌号或使用范围不符')
        for row in data['RD_B800_rows']:
            if (row['H_A_m'] != 800 or row['specimen_match'] is not False
                    or not math.isclose(row['J800_catalog_T'] + 4 * math.pi * 1e-7 * 800,
                                        row['B800_catalog_converted_T'], abs_tol=1e-12, rel_tol=0)
                    or not math.isclose(row['V4_fitted_RD_B_T'] - row['B800_catalog_converted_T'],
                                        row['V4_fit_minus_catalog_B_T'], abs_tol=1e-12, rel_tol=0)):
                raise ValueError('J800 与物理 B800 换算不符')
        points = {(row['grade'], row['J_peak_T'], row['frequency_Hz']): row['loss_W_kg']
                  for row in data['source_loss_rows']}
        if len(data['source_loss_rows']) != 16 or set(points) != {
                (grade, j, f) for grade in GRADES for j in (1.5, 1.7) for f in (50, 60)}:
            raise ValueError('资料附录频率或幅值不完整')
        for row in data['B23_frequency_rows']:
            j = row['J_peak_T']
            if (row['source_50_W_kg'] != points['B23R075', j, 50]
                    or row['source_60_W_kg'] != points['B23R075', j, 60]
                    or not math.isclose(100 * (row['candidate_100_nominal_J_W_kg'] /
                                        row['old_100_same_numeric_B_W_kg'] - 1),
                                        row['nominal_change_pct'], abs_tol=1e-9, rel_tol=0)):
                raise ValueError('名义幅值或频率对照不符')
        return raw, data
    except (OSError, ValueError, KeyError, TypeError, ZeroDivisionError) as error:
        raise ValueError('宝钢附录对照不可读取：' + str(error)) from error
