"""Prepare missing CPU stages or reopen an exact saved chain; never dispatch."""
from modules.calibrated_motor_analysis import protocol
from modules.material_library import sha
from modules.material_motor_workflow import LINEAGE, same_source, material_url
from modules.motor_queue import QueueLease


class MaterialMotorChain:
    def __init__(self, library, preparation, native, analysis, execution):
        self.library, self.preparation, self.native = library, preparation, native
        self.analysis, self.execution = analysis, execution
        self.lock = preparation.storage.parent / 'material_chain.lease'

    @staticmethod
    def records(component):
        rows = component.records()
        invalid = [r['id'] for r in rows if r['status'] == 'invalid']
        if invalid:
            raise ValueError('现有记录不可核对，请先检查，未重复创建：' + ', '.join(invalid))
        return rows

    @staticmethod
    def pick(rows, priorities):
        return max(rows, key=lambda r: (priorities.get(r['status'], 0), r.get('created_utc', ''))) if rows else None

    @staticmethod
    def linked(rows, key, parent):
        children = [r for r in rows if r.get(key) == parent['id']]
        if any(not same_source(r, parent) for r in children):
            raise ValueError('现有子记录与父记录来源不符，未重复创建')
        return children

    def ensure(self, data):
        if (not isinstance(data, dict) or set(data) - {'calibration', 'key', 'cores', 'measurement_protocol'}
                or not {'calibration', 'key'} <= set(data)):
            raise ValueError('请选择保存材料、核数及分析协议')
        cores = data.get('cores', 2)
        if type(cores) is not int or not 1 <= cores <= 2:
            raise ValueError('本入口核数需为1–2整数')
        measurement = data.get('measurement_protocol', 'v8_2_cycle1')
        protocol(measurement)
        lease = QueueLease(self.lock)
        if not lease.acquire():
            raise ValueError('同一存储正在准备材料链路，请稍后重开')
        try:
            return self._ensure(data['calibration'], data['key'], cores, measurement)
        finally:
            lease.release()

    def _ensure(self, calibration, key, cores, measurement):
        source = self.library.detail(calibration, key)
        # Read/verify every stage before the first mutation: unreadable records
        # cannot be treated as absent records after an interrupted request.
        saved = {name: self.records(component) for name, component in (
            ('preparation', self.preparation), ('native_import', self.native),
            ('analysis_plan', self.analysis), ('execution', self.execution))}
        template = sha(self.preparation.motor.template.read_bytes())
        candidates = [r for r in saved['preparation']
            if r.get('calibration_id') == calibration and r.get('material_key') == key
            and same_source(r, source) and r['cores'] == cores and r['template_sha256'] == template
            and all(r['files'].get('package/' + n) == source['files'][n]['sha256']
                    for n in ('material.amat', 'metadata.json'))]
        # Prefer a verified branch already further along, even if another
        # duplicate preparation was made later using the manual legacy button.
        execution_priority = {'completed': 100, 'completed_after_budget': 90,
            'running': 80, 'starting': 80, 'prepared': 70}
        def matching_plans(native):
            return [r for r in self.linked(saved['analysis_plan'], 'native_import_id', native)
                if r['measurement_protocol'] == measurement and r['cores'] == cores]
        def plan_priority(plan):
            runs = self.linked(saved['execution'], 'analysis_plan_id', plan)
            rank = max([50] + [execution_priority.get(r['status'], 0) for r in runs]) if plan['status'] == 'model_snapshot_ready_not_submitted' else 30
            return (rank, plan.get('created_utc', ''))
        def native_rank(native):
            if native['status'] == 'imported_not_solved':
                return max([30] + [plan_priority(p)[0] for p in matching_plans(native)])
            return {'running': 25, 'starting': 25, 'ready': 20}.get(native['status'], 0)
        def prep_priority(r):
            children = self.linked(saved['native_import'], 'preparation_id', r)
            return (max((native_rank(c) for c in children), default=-1), r.get('created_utc', ''))
        prep = max(candidates, key=prep_priority) if candidates else None
        result = dict(created=[], reused=[], records={}, native_dispatch_performed=False,
            new_native_sessions=0, new_native_solves=0, efficiency_enabled=False,
            optimization_ranking_enabled=False, material_url=material_url(calibration, key),
            calibration_id=calibration, material_key=key, measurement_protocol=measurement,
            cores=cores, **{k: source[k] for k in LINEAGE})

        def keep(stage, record, created=False):
            result['records'][stage] = dict(id=record['id'], status=record['status'])
            result['created' if created else 'reused'].append(dict(stage=stage, id=record['id']))
            return record

        def finish(status, label):
            result.update(status=status, next_step=label,
                href='/calibrated-motor/analysis/' + result['records']['analysis_plan']['id']
                    if 'analysis_plan' in result['records'] else result['material_url'])
            return result

        if prep:
            keep('preparation', prep)
        else:
            prep = keep('preparation', self.preparation.prepare(dict(calibration=calibration, key=key, cores=cores)), True)
        children = self.linked(saved['native_import'], 'preparation_id', prep)
        native = max(children, key=lambda r: (native_rank(r), r.get('created_utc', ''))) if children else None
        if native:
            keep('native_import', native)
        else:
            native = keep('native_import', self.native.prepare(dict(preparation=prep['id'])), True)
        plans = matching_plans(native)
        plan = max(plans, key=plan_priority) if plans else None
        if plan:
            keep('analysis_plan', plan)
        if native['status'] not in ('ready', 'starting', 'running', 'imported_not_solved'):
            return finish('needs_review', '保留现有失败、过期或待检查记录，请先查看；未自动重试')
        if plan is None:
            plan = keep('analysis_plan', self.analysis.prepare(dict(native_import=native['id'],
                measurement_protocol=measurement, cores=cores)), True)
        if native['status'] in ('starting', 'running'):
            return finish('waiting_native_import', '现有导入正在执行，请查看进度')
        if native['status'] == 'ready':
            return finish('awaiting_manual_import', '准备已完成，请在材料页手动执行现有原生导入')
        if plan['status'] == 'ready_for_model_preparation':
            plan = self.analysis.prepare_model(plan['id'])
            result['records']['analysis_plan']['status'] = plan['status']
            result['created'].append(dict(stage='model_snapshot', id=plan['id']))
        elif plan['status'] != 'model_snapshot_ready_not_submitted':
            return finish('needs_review', '分析工程状态与导入结果不符，请查看现有记录')
        runs = self.linked(saved['execution'], 'analysis_plan_id', plan)
        run = self.pick(runs, execution_priority)
        if run:
            keep('execution', run)
        else:
            run = keep('execution', self.execution.prepare(dict(analysis_plan=plan['id'])), True)
        if run['status'] == 'completed' and run.get('result_available'):
            return finish('result_available', '已有实际转矩结果，打开分析页查看与下载')
        if run['status'] in ('starting', 'running'):
            return finish('waiting_execution', '现有转矩任务正在执行，请查看进度')
        if run['status'] == 'prepared':
            return finish('awaiting_manual_execution', '执行任务已准备，请在分析页手动开始')
        return finish('needs_review', '保留现有执行现场，请检查；未重试或派发新任务')
