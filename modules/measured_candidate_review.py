"""Review a bounded measured curve on a different saved table for the same grade."""
import io
import math
import zipfile

from modules.external_bh_plot import curve_windows, comparison_plot_curves, measured_chart, read_saved_model_curves
from modules.external_bh_validation import compare_curves, field_weighted_errors, read_measurement_csv
from modules.system_data import csv_bytes, json_bytes, sha256, workpack_bytes


VERSION = 'measured_bh_candidate_review_v1'
FILENAME = 'measured_bh_candidate_review.zip'
REPORT = 'candidate_review.json'


def reviewable_reference(report):
    return (report.get('mode') == 'compare' and report.get('declared_grade_status') == 'matches_selected'
            and report['source']['source_kind'] in ('measurement', 'published_measurement', 'digitized')
            and bool(report['inputs']) and set(report['inputs']) <= {'RD','TD'})


def _curve(measurement):
    points = measurement['points']
    if measurement['source_order'] == 'descending':
        points = points[::-1]
    return tuple((p['H_A_per_m'], p['input_B_T']) for p in points)


def _same_points(a, b):
    x, y = _curve(a), _curve(b)
    return len(x) == len(y) and all(math.isclose(h0,h1,rel_tol=1e-12,abs_tol=1e-10)
                                   and math.isclose(b0,b1,rel_tol=1e-12,abs_tol=1e-12)
                                   for (h0,b0),(h1,b1) in zip(x,y))


def build_review(reference, ref_blob, ref_report, candidate, candidate_blob, candidate_report):
    if not reviewable_reference(ref_report):
        raise ValueError('请选择对应牌号且方向明确的实测原表核对记录')
    grade = ref_report['model']['grade']
    if candidate_report['grade'].casefold() != grade.casefold():
        raise ValueError('实测候选与复核原表的牌号不同')
    directions = sorted(set(ref_report['inputs']) & set(candidate_report['directions']))
    if not directions:
        raise ValueError('实测候选与复核原表没有共同的已声明方向')
    roles = ['fit','holdout']
    if ref_report.get('comparison_model'):
        roles += ['secondary_fit','secondary_holdout']
    summary = dict(version=VERSION, grade=grade, model=ref_report['model'], source=ref_report['source'],
                   candidate_source=candidate_report['source'], comparison={},
                   original_reference=dict(id=reference['id'],sha256=reference['sha256']),
                   original_candidate=dict(id=candidate['id'],sha256=candidate['sha256']),
                   specimen_independence_verified=False, native_material_generated=False)
    if ref_report.get('comparison_model'):
        summary['comparison_model'] = ref_report['comparison_model']
    files = {'source/reference.zip':ref_blob, 'source/candidate.zip':candidate_blob}
    with zipfile.ZipFile(io.BytesIO(ref_blob)) as ref, zipfile.ZipFile(io.BytesIO(candidate_blob)) as source:
        candidate_origin = source.read('source/original_comparison.zip')
        if sha256(candidate_origin) != candidate_report['source_package']['sha256']:
            raise ValueError('候选的原比较档案哈希不符')
        stored_models = ref.read('model_curves.csv')
        with zipfile.ZipFile(io.BytesIO(candidate_origin)) as fitting:
            for direction in directions:
                path = f'original/{direction}.csv'
                raw, initial = ref.read(path), fitting.read(path)
                if (sha256(initial) != candidate_report['directions'][direction]['source_sha256']
                        or sha256(raw) != ref_report['inputs'][direction]['sha256']):
                    raise ValueError('来源原表哈希不符')
                observation = read_measurement_csv(raw,direction)
                if _same_points(observation, read_measurement_csv(initial,direction)):
                    raise ValueError(f'{direction} 复核表与候选的建模原点相同，请选择另一份表')
                measured = read_measurement_csv(source.read(f'candidate/{direction}.csv'),direction)
                h,b = zip(*_curve(measured))
                curves = {key:dict(H=grid,B=values) for key,(grid,values) in
                          read_saved_model_curves(stored_models,direction,roles).items()}
                curves['candidate'] = dict(H=h,B=b)
                row = compare_curves(observation,curves)
                request = (ref_report['comparison'][direction].get('field_weighted') or {}).get('bands',{}).get('focus',{})
                row['field_weighted'] = field_weighted_errors(observation,curves,focus=request.get('requested_H_range_A_per_m'))
                summary['comparison'][direction] = row
                files[f'comparison/{direction}.csv'] = csv_bytes(row.pop('rows'))
    if not any(row['field_weighted']['bands']['common']['span_A_per_m'] > 0 for row in summary['comparison'].values()):
        raise ValueError('实测候选与复核原表没有可比较的 H 区间')
    files[REPORT] = json_bytes(summary)
    return workpack_bytes(files)


def review_plot(blob, summary, direction, scope):
    bounds = curve_windows(summary)[direction][scope]
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        reference, candidate = z.read('source/reference.zip'),z.read('source/candidate.zip')
    with zipfile.ZipFile(io.BytesIO(reference)) as z:
        raw = z.read(f'original/{direction}.csv')
        models = comparison_plot_curves(z.read('model_curves.csv'),summary,direction)
    with zipfile.ZipFile(io.BytesIO(candidate)) as z:
        h,b = zip(*_curve(read_measurement_csv(z.read(f'candidate/{direction}.csv'),direction)))
    return measured_chart(raw,direction,bounds,[*models,('candidate','实测区间候选',h,b)])
