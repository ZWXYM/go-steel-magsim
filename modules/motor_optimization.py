"""Read the author's saved workflow and screen one explicitly selected dataset."""
from __future__ import annotations

import csv
import json
import math
import re
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from modules.material_calibration import file_hash
from modules.motor_workbench import collect_metrics, write_json
from modules.motor_model_audit import audit_project
from modules.motor_failure_diagnostics import diagnose_job


WORKFLOW_FILES = {
    'baseline': ('motor/v6_2/outputs/step1_comparison.json',
                 'motor/v6_2/outputs/step1_comparison.csv'),
    'original_sweep': ('motor/v8_material_sweep/outputs/material_comparison.json',
                       'motor/v8_material_sweep/outputs/material_comparison.csv',
                       'motor/v8_material_sweep/outputs/material_ranking.md'),
    'v8_1': ('motor/v8_1/outputs/v8_1_material_comparison.json',
             'motor/v8_1/outputs/v8_1_material_comparison.csv',
             'motor/v8_1/outputs/v8_1_material_ranking.md'),
}
SCRIPT_FILES = (
    'motor/v8_material_sweep/scripts/run_material_sweep.py',
    'motor/v8_1/scripts/generate_v8_1_cases.py',
    'motor/v8_1/scripts/solve_export_v8_1_case.py',
    'motor/v8_1/scripts/collect_v8_1_results.py',
    'motor/v8_2/scripts/v8_2_run_resolve_batch.py',
    'motor/v8_2/scripts/v8_2_torque_pipeline.py',
)
DEFAULT_CONFIG = dict(min_ripple_reduction_pct=30.0, max_iron_loss_increase_pct=0.0,
                      min_torque_change_pct=0.0, ripple_weight=0.60,
                      iron_loss_weight=0.30, efficiency_weight=10.0)
METRICS = ('T_avg_Nm', 'K_T_ripple_pct', 'P_Fe_W', 'eta_estimate_pct')


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def validate_config(supplied):
    if not isinstance(supplied,dict) or set(supplied)-set(DEFAULT_CONFIG):
        raise ValueError('筛选配置字段不正确')
    config=dict(DEFAULT_CONFIG,**supplied)
    for name,value in config.items():
        if not finite(value) or abs(value)>1000 or (name.endswith('_weight') and value<0):
            raise ValueError('筛选配置需为有限数值；权重非负，数值绝对值不超过1000')
    if not sum(config[n] for n in config if n.endswith('_weight')):
        raise ValueError('至少设置一个非零评分权重')
    return config


def normalized(row, accepted=None, source='saved_summary'):
    metrics = {name: row.get(name) for name in METRICS}
    if metrics['eta_estimate_pct'] is None:
        metrics['eta_estimate_pct'] = row.get('eta_pct')
    return dict(case=row.get('case_id') or row.get('case') or row.get('group'),
                material=row.get('material') or row.get('material_assignment') or row.get('case'),
                metrics=metrics, input_accepted=bool(row.get('ok') if accepted is None else accepted),
                input_source=source, input_error=row.get('error') or row.get('reason'))


def screen_rows(rows, reference, torque_reference, config):
    """The original weighted score with explicitly selectable engineering limits."""
    ref, tref = reference['metrics'], torque_reference['metrics']
    if not all(finite(ref.get(name)) for name in METRICS) or not finite(tref.get('T_avg_Nm')):
        raise ValueError('基准缺少完整的转矩、脉动、铁损或效率指标')
    if min(ref['T_avg_Nm'], ref['K_T_ripple_pct'], ref['P_Fe_W'], tref['T_avg_Nm']) <= 0:
        raise ValueError('基准转矩、脉动和铁损需为正，不能用零损耗作相对筛选基准')
    result = []
    for source in rows:
        row = dict(source, score=None, meets_limits=False, reasons=[])
        m = row['metrics']
        if not source['input_accepted']:
            row['reasons'].append(source.get('input_error') or '原输入状态未通过数值筛查')
        if not all(finite(m.get(name)) for name in METRICS) or m.get('T_avg_Nm', 0) <= 0 or m.get('P_Fe_W', 0) <= 0 or m.get('K_T_ripple_pct', -1) < 0:
            row['reasons'].append('指标缺失、非有限或不能用于相对工程评分')
        if row['reasons']:
            result.append(row)
            continue
        ripple = (ref['K_T_ripple_pct'] - m['K_T_ripple_pct']) / ref['K_T_ripple_pct'] * 100
        iron = (ref['P_Fe_W'] - m['P_Fe_W']) / ref['P_Fe_W'] * 100
        torque = (m['T_avg_Nm'] - tref['T_avg_Nm']) / tref['T_avg_Nm'] * 100
        efficiency = m['eta_estimate_pct'] - ref['eta_estimate_pct']
        row.update(ripple_reduction_pct=ripple, iron_loss_reduction_pct=iron,
                   torque_change_pct=torque, efficiency_change_pctpt=efficiency,
                   score=config['ripple_weight'] * ripple + config['iron_loss_weight'] * iron + config['efficiency_weight'] * efficiency)
        if ripple < config['min_ripple_reduction_pct']:
            row['reasons'].append('脉动降幅未达要求')
        if -iron > config['max_iron_loss_increase_pct']:
            row['reasons'].append('铁损增幅超过限值')
        if torque < config['min_torque_change_pct']:
            row['reasons'].append('平均转矩未达下限')
        row['meets_limits'] = not row['reasons']
        result.append(row)
    return sorted(result, key=lambda r: (not r['meets_limits'], r['score'] is None,
                                        -r['score'] if r['score'] is not None else 0,
                                        str(r['case'])))


class MotorOptimization:
    def __init__(self, root, storage, motor):
        self.root, self.storage, self.motor = Path(root).resolve(), Path(storage).resolve(), motor

    def source_file(self, source, name):
        allowed = WORKFLOW_FILES.get(source, ())
        if name not in {Path(p).name for p in allowed}:
            raise ValueError('不属于原流程的指定输出文件')
        path = self.root / next(p for p in allowed if Path(p).name == name)
        if not path.resolve().is_relative_to(self.root) or not path.is_file():
            raise FileNotFoundError('原流程输出文件未配置')
        return path

    def workflow(self):
        stages = []
        titles = {'baseline': '原电机与插片对照', 'original_sweep': '原多牌号扫描流程', 'v8_1': 'V8_1 已保存的16牌号筛选'}
        for key, relatives in WORKFLOW_FILES.items():
            path = self.root / relatives[0]
            if not path.is_file():
                stages.append(dict(id=key, title=titles[key], available=False, rows=[], ranked=[], files={}))
                continue
            data = json.loads(path.read_text(encoding='utf-8'))
            files = {Path(p).name: dict(relative_path=p, sha256=file_hash(self.root/p))
                     for p in relatives if (self.root/p).is_file()}
            stages.append(dict(id=key, title=titles[key], available=True, generated=data.get('generated'),
                               rows=data.get('rows', []), ranked=data.get('ranked', data.get('ranked_material_cases', [])),
                               best_case=data.get('best_case'), files=files,
                               source_scope='Read original saved outputs; no cleaning, solving or re-ranking applied'))
        scripts = [dict(relative_path=p, sha256=file_hash(self.root/p)) for p in SCRIPT_FILES if (self.root/p).is_file()]
        return dict(stages=stages, scripts=scripts, default_config=DEFAULT_CONFIG,
                    score_definition='score = 0.60*ripple_reduction_pct + 0.30*iron_loss_reduction_pct + 10*efficiency_change_pctpt',
                    original_criteria='脉动相对 V6_2 至少降低30%，铁损不高于 V6_2，转矩不低于原电机基线',
                    scope='保留作者原筛选结果与规则；网页任务使用独立副本和串行队列')

    def datasets(self):
        values = []
        if (self.root/WORKFLOW_FILES['v8_1'][0]).is_file():
            values.append(dict(id='v8_1', title='V8_1 原保存指标（原处理口径）'))
        if self.motor.archived_results():
            values.append(dict(id='v8_2_archive', title='V8_2 final 结果（官方CSV/原摘要）'))
        for job in self.motor.jobs():
            if job['status'] in ('completed', 'completed_with_failures'):
                values.append(dict(id=job['id'], title=job['id']+' · '+job.get('measurement_protocol', 'v8_2_cycle1')))
        return values

    def dataset(self, dataset_id):
        hashes, references, rows = {}, [], []
        if dataset_id == 'v8_1':
            path = self.source_file('v8_1', Path(WORKFLOW_FILES['v8_1'][0]).name)
            data = json.loads(path.read_text(encoding='utf-8'))
            hashes[WORKFLOW_FILES['v8_1'][0]] = file_hash(path)
            rows = [normalized(r, source='V8_1 original processed summary') for r in data['rows']]
            references = [dict(normalized(data[name], True), case='original_'+name) for name in ('baseline', 'control')]
            version, protocol = 'V8_1_original_saved', 'original_processed_metrics'
            scope = '沿用原 V8_1 保存指标；其曲线处理与损耗估算保持原口径'
        elif dataset_id == 'v8_2_archive':
            for item in self.motor.archived_results():
                metrics = item.get('metrics') or {}
                rows.append(normalized(dict(item.get('display_metrics') or {}, case=item['case'], material=item['case'], reason=item.get('reason')),
                                       bool(item.get('csv_verified') and metrics.get('accepted')), source='official_CSV_verified' if item.get('csv_verified') else 'report_only'))
                for relative, meta in item.get('files', {}).items():
                    hashes['archive/'+item['case']+'/'+relative] = meta['sha256']
            version, protocol = 'V8_2_final_resolved', 'v8_2_cycle1'
            scope = '作者 V8_2 final 单周期库材料结果；缺CSV和异常行保留，独立展示状态'
        elif isinstance(dataset_id, str) and re.fullmatch(r'scan_[0-9a-f]{12}', dataset_id):
            state = self.motor.state(dataset_id)
            if state['status'] not in ('completed', 'completed_with_failures'):
                raise ValueError('扫描完成后才能进行工程筛选')
            diagnoses={d['case_id']:d for d in diagnose_job(self.motor,state)['native_failure_diagnostics']}
            folder = self.motor.path(dataset_id)
            manifest = json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
            hashes['manifest.json'] = file_hash(folder/'manifest.json')
            version, protocol = manifest['model_version'], manifest.get('measurement_protocol', 'v8_2_cycle1')
            for case in manifest['cases']:
                path = folder/case['id']/'result.json'
                if not path.is_file():
                    failure = folder/case['id']/'failure.json'
                    error = json.loads(failure.read_text(encoding='utf-8')) if failure.is_file() else {}
                    diagnostic=diagnoses.get(case['id'])
                    rows.append(normalized(dict(case=case['id'], material=case['material'], error=diagnostic['reason'] if diagnostic else error.get('error', '未生成结果')), False))
                    if diagnostic:
                        hashes[diagnostic['source_file']]=diagnostic['source_sha256']
                    if failure.is_file():
                        hashes[case['id']+'/failure.json'] = file_hash(failure)
                    continue
                result = json.loads(path.read_text(encoding='utf-8'))
                legacy_alias = 'measurement_protocol' not in manifest and protocol=='v8_2_cycle1' and result.get('metric_protocol')=='v8_2_torque_all_core_drop_first_copper_all_v1'
                if result.get('metric_protocol') != protocol and not legacy_alias:
                    raise ValueError('结果与任务的测量口径不同')
                for relative, digest in result['csv_hashes'].items():
                    csv_path = (path.parent/relative).resolve()
                    if not csv_path.is_relative_to(path.parent.resolve()) or file_hash(csv_path) != digest:
                        raise ValueError('扫描官方 CSV 的路径或哈希改变，停止评分')
                    hashes[case['id']+'/'+relative] = digest
                computed = collect_metrics(path.parent, protocol)
                for name in METRICS:
                    if result.get(name) != computed.get(name):
                        raise ValueError('保存指标与官方 CSV 不一致：'+name)
                if result.get('accepted') != computed.get('accepted'):
                    raise ValueError('保存验收状态与官方 CSV 不一致')
                if result.get('model_physics_audit'):
                    native=path.parent/'motor.aedt'
                    if file_hash(native)!=result.get('solved_project_sha256') or audit_project(native)!=result['model_physics_audit']:
                        raise ValueError('求解项目与保存的物理核验不符')
                hashes[case['id']+'/result.json'] = file_hash(path)
                rows.append(normalized(dict(result, case=case['id'], material=case['material']), result['accepted'], 'official_CSV_verified'))
            scope = '本任务同一模板、材料范围和测量协议的工程筛选；保留原物理核验与损耗估算边界'
        else:
            raise ValueError('未知结果数据集')
        references += [r for r in rows if r['input_accepted'] and all(finite(r['metrics'].get(n)) for n in METRICS)]
        return dict(id=dataset_id, version=version, measurement_protocol=protocol, scope=scope,
                    rows=rows, references=references, source_hashes=hashes,
                    default_reference='original_control' if dataset_id=='v8_1' else (references[0]['case'] if references else None))

    def analyze(self, data):
        if not isinstance(data, dict):
            raise ValueError('请输入数据集、基准和筛选配置')
        dataset = self.dataset(data.get('dataset'))
        refs = {r['case']: r for r in dataset['references']}
        reference_id = data.get('reference') or dataset['default_reference']
        if reference_id not in refs:
            raise ValueError('请选择当前数据集中可用的基准案')
        config = validate_config(data.get('config', {}))
        torque_ref = refs['original_baseline'] if dataset['id']=='v8_1' and reference_id=='original_control' else refs[reference_id]
        rows = screen_rows(dataset['rows'], refs[reference_id], torque_ref, config)
        analysis_id = 'opt_'+uuid.uuid4().hex[:12]
        output = self.storage/analysis_id
        output.mkdir(parents=True)
        source = self.root/'motor/v8_1/scripts/collect_v8_1_results.py'
        report = dict(id=analysis_id, created_utc=datetime.now(timezone.utc).isoformat(), dataset=dataset,
                      reference=reference_id, torque_reference=torque_ref['case'], config=config, ranked=rows,
                      best_case=next((r['case'] for r in rows if r['meets_limits']), None),
                      meets_limits_count=sum(r['meets_limits'] for r in rows),
                      rule_source=dict(relative_path=source.relative_to(self.root).as_posix(), sha256=file_hash(source) if source.is_file() else None),
                      rule_adaptation='Original score weights and constraints; selected dataset references only. V8_1 original_control retains original_baseline torque reference.',
                      final_physical_optimization_certified=False, new_native_solves=0,
                      original_results_changed=False, producer_sha256=file_hash(Path(__file__)))
        write_json(output/'analysis.json', report)
        fields = ['case', 'material', 'input_source', 'input_accepted', 'meets_limits', 'score', *METRICS,
                  'ripple_reduction_pct', 'iron_loss_reduction_pct', 'torque_change_pct', 'efficiency_change_pctpt', 'reasons']
        with (output/'ranking.csv').open('x', encoding='utf-8-sig', newline='') as stream:
            writer = csv.DictWriter(stream, fields)
            writer.writeheader()
            for row in rows:
                writer.writerow({key: '; '.join(row['reasons']) if key=='reasons' else row['metrics'].get(key, row.get(key)) for key in fields})
        (output/'analysis.md').write_text('\n'.join([
            '# 电机工程筛选', '', f"数据集：{dataset['id']} / {dataset['version']} / {dataset['measurement_protocol']}",
            f"基准：{reference_id}；转矩基准：{torque_ref['case']}", f"满足配置：{report['meets_limits_count']}/{len(rows)}；首选：{report['best_case'] or '无'}",
            dataset['scope'], '配置与源文件哈希见 analysis.json；原数据保留，未新增求解。', '',
            '| 案例 | 满足配置 | 分数 | 原因 |', '| --- | --- | --- | --- |',
            *[f"| {r['case']} | {r['meets_limits']} | {r['score']} | {'; '.join(r['reasons'])} |" for r in rows]
        ]), encoding='utf-8')
        with zipfile.ZipFile(output/'analysis_bundle.zip', 'x', zipfile.ZIP_DEFLATED) as bundle:
            for name in ('analysis.json', 'ranking.csv', 'analysis.md'):
                bundle.write(output/name, name)
        return dict(report, files={n:f'/api/workbench/optimization/{analysis_id}/files/{n}' for n in ('analysis.json','ranking.csv','analysis.md','analysis_bundle.zip')})

    def artifact(self, artifact_id, name):
        if not isinstance(artifact_id, str) or not re.fullmatch(r'opt_[0-9a-f]{12}', artifact_id) or name not in ('analysis.json','ranking.csv','analysis.md','analysis_bundle.zip'):
            raise ValueError('无效筛选记录或文件')
        path = self.storage/artifact_id/name
        if not path.is_file() or not path.resolve().is_relative_to(self.storage):
            raise FileNotFoundError('筛选文件不存在')
        return path

    def history(self):
        return [dict(id=p.parent.name, created_utc=d['created_utc'], dataset=d['dataset']['id'], best_case=d['best_case'],
                     meets_limits_count=d['meets_limits_count'])
                for p in sorted(self.storage.glob('opt_*/analysis.json'), key=lambda p:p.stat().st_mtime, reverse=True)
                for d in [json.loads(p.read_text(encoding='utf-8'))]]
