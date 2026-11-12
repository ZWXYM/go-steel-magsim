"""Evaluate saved calibration versions on the original pilot reference samples."""
import argparse
import json
import math
import re
import statistics
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from modules.calibration_comparison import compare_calibrations, comparison_bundle, comparison_csv
from modules.external_bh_validation import compare_curves, field_weighted_errors, read_measurement_csv
from modules.system_data import WorkpackStore, sha256


def _band_errors(rows, curve_key, low_field):
    errors = [row[curve_key + '_error_T'] for row in rows
              if row['in_comparison'] and (row['H_A_per_m'] <= 800) == low_field]
    rmse = math.sqrt(statistics.fmean(value * value for value in errors)) if errors else None
    return len(errors), rmse


def reference_sample_comparison(calibration, pilot, ids, *, field_weighted=False):
    if (len(ids) < 2 or len(set(ids)) != len(ids)
            or any(not re.fullmatch(r'cal_[0-9a-f]{12}', artifact) for artifact in ids)):
        raise ValueError('Select at least two distinct saved calibration IDs')
    calibration, pilot = Path(calibration).resolve(), Path(pilot).resolve()
    digests = {artifact: sha256((calibration / artifact / 'report.json').read_bytes()) for artifact in ids}
    baseline, *candidates = ids
    reports = {}
    for artifact in candidates:
        _, pair = compare_calibrations(calibration, baseline, artifact, digests[baseline], digests[artifact])
        reports[baseline] = pair['baseline/report.json']
        reports[artifact] = pair['candidate/report.json']
    saved = {artifact: json.loads(raw) for artifact, raw in reports.items()}
    manifest_raw = (pilot / 'manifest.json').read_bytes()
    manifest_sha256 = sha256(manifest_raw)
    if any(report['source_manifest_sha256'] != manifest_sha256 for report in saved.values()):
        raise ValueError('The pilot manifest does not match the saved calibrations')
    materials = {row['grade']: row for row in json.loads(manifest_raw)['materials']}
    comparison_rows = {artifact: {(r['grade'], r['direction']): r for r in report['comparisons']}
                       for artifact, report in saved.items()}
    grades = sorted(saved[baseline]['selected_grades'])
    files = {'source/pilot_manifest.json': manifest_raw}
    files.update({f'source/{artifact}.json': raw for artifact, raw in reports.items()})
    sources, metrics, field_bands = [], [], []
    for grade in grades:
        for direction in ('RD', 'TD'):
            key = grade, direction
            ref = materials[grade]['references'][direction]
            path = (pilot / ref['path']).resolve()
            if not path.is_relative_to(pilot):
                raise ValueError('Reference is outside the pilot directory')
            raw = path.read_bytes()
            if sha256(raw) != ref['sha256']:
                raise ValueError(f'Frozen reference hash differs for {grade} {direction}')
            measurement = read_measurement_csv(raw, direction)
            files[f'original/{grade}_{direction}.csv'] = raw
            sources.append(dict(grade=grade, direction=direction, sha256=ref['sha256'], status=ref['status'],
                                path=ref['path'], original_source=ref.get('source')))
            original = comparison_rows[baseline][key]
            curves = {'uncalibrated': dict(H=original['H'], B=original['raw_B'])}
            models = {'uncalibrated': (None, 'uncalibrated_saved_ensemble')}
            for artifact in ids:
                row = comparison_rows[artifact][key]
                for scope, column in (('fit', 'corrected_B'), ('holdout', 'holdout_B')):
                    curve_key = artifact + '_' + scope
                    curves[curve_key] = dict(H=row['H'], B=row[column])
                    models[curve_key] = artifact, scope
            evaluated = compare_curves(measurement, curves)
            fields = field_weighted_errors(measurement, curves) if field_weighted else None
            if fields:
                field_bands.append(dict(grade=grade, direction=direction, **{
                    band: {k: v for k, v in data.items() if k != 'metrics'} for band, data in fields['bands'].items()}))
            samples = evaluated['rows']
            low_H, high_H = evaluated['overlap_H_range_A_per_m'] or (None, None)
            training = {'fit': grades, 'holdout': [g for g in grades if g != grade],
                        'uncalibrated_saved_ensemble': []}
            files[f'samples/{grade}_{direction}.csv'] = comparison_csv({'rows': samples})
            for curve_key, (artifact, scope) in models.items():
                low_count, low_rmse = _band_errors(samples, curve_key, True)
                high_count, high_rmse = _band_errors(samples, curve_key, False)
                statistic = evaluated['metrics'][curve_key] or {'rmse_T': None, 'mae_T': None}
                b800 = evaluated['B800']
                extra = {}
                if fields:
                    for band, value in fields['bands'].items():
                        error = value['metrics'][curve_key]
                        for statistic_name in ('rmse', 'mae'):
                            key = f'{statistic_name}_uniform_H_{band}_T'
                            extra[key] = error[f'{statistic_name}_T'] if error else None
                metrics.append(dict(grade=grade, direction=direction, curve=curve_key, calibration_id=artifact,
                                    scope=scope, training_grades='|'.join(training[scope]),
                                    excluded_grade=grade if scope == 'holdout' else None,
                                    original_points=evaluated['measurement_points'],
                                    compared_points=evaluated['compared_points'],
                                    common_H_min_A_per_m=low_H, common_H_max_A_per_m=high_H, **statistic,
                                    points_H_le_800=low_count, rmse_H_le_800_T=low_rmse,
                                    points_H_gt_800=high_count, rmse_H_gt_800_T=high_rmse,
                                    input_B800_T=b800['input_B_T'] if b800 else None,
                                    input_B800_at_original_point=b800['at_original_point'] if b800 else None,
                                    B800_error_T=b800[curve_key + '_error_T'] if b800 else None, **extra))

    summaries = []
    for artifact in ids:
        selected = [row for row in metrics if row['calibration_id'] == artifact and row['scope'] == 'holdout']
        values = [row['rmse_T'] for row in selected if row['rmse_T'] is not None]
        b800 = [abs(row['B800_error_T']) for row in selected if row['B800_error_T'] is not None]
        summaries.append(dict(calibration_id=artifact, version=saved[artifact]['calibration_version'],
                              report_sha256=digests[artifact], bank_sha256=saved[artifact]['bank_sha256'],
                              compared_directions=len(values), B800_directions=len(b800),
                              holdout_mean_direction_rmse_T=statistics.fmean(values) if values else None,
                              holdout_max_abs_B800_error_T=max(b800, default=None)))
        if field_weighted:
            for band in ('common','up_to_800','from_800'):
                values = [r[f'rmse_uniform_H_{band}_T'] for r in selected if r[f'rmse_uniform_H_{band}_T'] is not None]
                summaries[-1][f'holdout_mean_direction_rmse_uniform_H_{band}_T'] = statistics.fmean(values) if values else None
    result = dict(version='pilot_reference_samples_v1', H_axis='physical_A_per_m', baseline=baseline,
                  pilot_manifest_sha256=manifest_sha256, calibrations=summaries, sources=sources, rows=metrics,
                  scope='Retrospective evaluation on the frozen digitized pilot reference tables at their original sampling points.',
                  sample_independence_verified=False, new_fits=0, new_native_solves=0)
    if field_weighted:
        result.update(version='pilot_reference_samples_H_weighted_v1',
                      scope=result['scope'] + ' Also integrates the linear-interpolated residual uniformly over shared physical H.',
                      field_weighted_schema='linear_H_weighted_curve_error_v1', split_H_A_per_m=800., field_bands=field_bands)
    return result, files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--storage', type=Path, required=True, help='Workbench record directory')
    parser.add_argument('--pilot', type=Path, required=True, help='Original calibration pilot directory')
    parser.add_argument('--output', type=Path, required=True, help='A new ZIP file')
    parser.add_argument('--register', action='store_true', help='Register the verified ZIP in local system data')
    parser.add_argument('--field-weighted', action='store_true', help='Also integrate errors over shared physical H')
    parser.add_argument('calibrations', nargs='+', help='Saved calibration IDs with the baseline first')
    args = parser.parse_args()
    result, sources = reference_sample_comparison(args.storage / 'calibration', args.pilot, args.calibrations,
                                                  field_weighted=args.field_weighted)
    blob = comparison_bundle(result, sources).getvalue()
    with args.output.open('xb') as stream:
        stream.write(blob)
    record = None
    if args.register:
        record = WorkpackStore(args.storage / 'system_data').register_bytes(
            blob, '原点和 H 区间校准对照' if args.field_weighted else '原采样点校准对照',
            '冻结参考表的原点对照，保留来源与各版本训练范围。' + (' 附共同物理 H 区间积分误差。' if args.field_weighted else ''),
            source_filename='pilot_reference_samples_H_weighted.zip' if args.field_weighted else 'pilot_reference_samples.zip')
    print(json.dumps(dict(output=str(args.output), sha256=sha256(blob), package=record,
                          calibrations=result['calibrations']), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
