"""Read-only material-to-motor progress; reuse verified records, never dispatch."""
import csv
import io
import json
import os
import zipfile
from datetime import datetime, timezone
from urllib.parse import urlencode

from modules.material_library import sha
from modules.motor_workbench import license_status

VERSION = 'material_motor_workflow_v1'
LINEAGE = ('material_name', 'parent_bank_sha256', 'effective_bank_sha256', 'training_grades', 'excluded_grades')
ACTIVE = ('starting', 'running')
REVIEW = ('failed', 'needs_attention', 'invalid', 'completed_after_budget')


def same_source(child, parent):
    return all(child.get(k) == parent.get(k) for k in LINEAGE)


def material_url(calibration, key):
    return '/material-library?' + urlencode(dict(calibration=calibration, key=key))


class MaterialMotorWorkflow:
    def __init__(self, library, preparation, native, analysis, execution):
        self.library, self.preparation, self.native = library, preparation, native
        self.analysis, self.execution = analysis, execution

    @staticmethod
    def _records(component, stage, issues):
        try:
            rows = component.records()
            for row in rows:
                if row['status'] == 'invalid':
                    issues.append(dict(stage=stage, id=row['id'], error=row.get('error', '文件核验失败')))
            return rows
        except (OSError, ValueError, KeyError, TypeError) as error:
            issues.append(dict(stage=stage, id=None, error=str(error)))
            return []

    def snapshot(self):
        issues = []
        try:
            materials = self.library.catalog()['records']
        except (OSError, ValueError, KeyError, TypeError) as error:
            materials = []
            issues.append(dict(stage='materials', id=None, error=str(error)))
        records = {name: self._records(component, name, issues) for name, component in
            [('preparation', self.preparation), ('native_import', self.native),
             ('analysis_plan', self.analysis), ('execution', self.execution)]}
        try:
            licensing = license_status()
        except (OSError, ValueError) as error:
            licensing = dict(available=False, reason=str(error))
        resource = dict(cpu_only=os.environ.get('MAGSIM_CPU_ONLY') == '1', license=licensing)
        resource['pending_native_tasks'] = [dict(stage=stage, id=r['id'], status=r['status'])
            for stage in ('native_import', 'execution') for r in records[stage]
            if r['status'] in (*ACTIVE, 'needs_attention', 'invalid')]
        used = {k: set() for k in records}
        items = []
        for calibration in materials:
            if calibration.get('error'):
                issues.append(dict(stage='materials', id=calibration['id'], error=calibration['error']))
            for entry in calibration['entries']:
                row = dict(calibration_id=calibration['id'], material_key=entry['key'],
                    kind=entry['kind'], label=entry['label'], available=entry['available'],
                    material_url=material_url(calibration['id'], entry['key']), branches=[])
                if not entry['available']:
                    row.update(error=entry.get('reason', '材料不可读'), next_step=dict(action='review', label='检查材料文件', href=row['material_url']))
                    items.append(row); continue
                try:
                    source = self.library.detail(calibration['id'], entry['key'])
                    issue_start = len(issues)
                    row.update({k: source[k] for k in LINEAGE})
                    row.update(H_axis=source['H_axis'], H_scale=source['H_scale'],
                        evidence_scope=source['evidence_scope'], efficiency_enabled=False, optimization_ranking_enabled=False)
                    for prep in records['preparation']:
                        if prep.get('calibration_id') != calibration['id'] or prep.get('material_key') != entry['key']: continue
                        used['preparation'].add(prep['id'])
                        if (not same_source(prep, source) or
                            any(prep.get('files',{}).get('package/'+name) != source['files'][name]['sha256']
                                for name in ('material.amat','metadata.json'))):
                            issues.append(dict(stage='preparation', id=prep['id'], error='准备记录与当前材料来源不一致'))
                            continue
                        branches = [dict(preparation=prep)]
                        imports = [r for r in records['native_import'] if r.get('preparation_id') == prep['id']]
                        branches = self._extend(branches, imports, 'native_import', prep, used, issues)
                        expanded = []
                        for branch in branches:
                            native = branch.get('native_import')
                            plans = [r for r in records['analysis_plan'] if native and r.get('native_import_id') == native['id']]
                            for candidate in self._extend([branch], plans, 'analysis_plan', native, used, issues):
                                plan = candidate.get('analysis_plan')
                                runs = [r for r in records['execution'] if plan and r.get('analysis_plan_id') == plan['id']]
                                expanded += self._extend([candidate], runs, 'execution', plan, used, issues)
                        for branch in expanded:
                            row['branches'].append(self._branch(branch, row['material_url'], resource))
                    row['next_step'] = self._next(row['branches'], row['material_url'])
                    if len(issues) > issue_start:
                        row['next_step'] = dict(action='review', label='链路来源需核对，不创建重复任务', href=row['material_url'])
                except (OSError, KeyError, ValueError, TypeError) as error:
                    row.update(available=False, error=str(error), next_step=dict(action='review', label='检查材料来源', href=row['material_url']))
                items.append(row)
        for stage, rows in records.items():
            for record in rows:
                if record['id'] not in used[stage] and record['status'] != 'invalid':
                    issues.append(dict(stage=stage, id=record['id'], error='记录未绑定到可读取的材料链路，未用于进度或结果'))
        # Unreadable stages must not imply that records do not exist or invite duplicate preparation.
        if any(issue['id'] is None for issue in issues):
            for row in items:
                row['next_step'] = dict(action='review', label='部分记录暂不可读，请刷新或检查', href=row['material_url'])
        return dict(version=VERSION, checked_utc=datetime.now(timezone.utc).isoformat(), resources=resource,
            materials=items, issues=issues, record_counts={k:len(v) for k,v in records.items()},
            totals=dict(materials=len(items), available_materials=sum(r['available'] for r in items),
                linked_branches=sum(len(r['branches']) for r in items),
                actual_torque_results=len({b['records']['execution']['id'] for r in items for b in r['branches'] if b['result_available']})),
            read_only=True, source_records_changed=False, new_native_sessions=0, new_native_solves=0,
            efficiency_enabled=False, optimization_ranking_enabled=False, system_complete=False)

    @staticmethod
    def _extend(branches, children, stage, parent, used, issues):
        valid = []
        for child in children:
            used[stage].add(child['id'])
            if not parent or not same_source(child, parent):
                issues.append(dict(stage=stage, id=child['id'], error='子记录与父记录材料/有效库/排除范围不一致'))
            else:
                valid.append(child)
        return [dict(branch, **{stage: child}) for branch in branches for child in valid] if valid else branches

    @staticmethod
    def _branch(records, material, resource):
        steps = {k: {field:r[field] for field in ('id','status','created_utc','phase','error','measurement_protocol') if field in r}
                 for k,r in records.items()}
        plan, run = records.get('analysis_plan'), records.get('execution')
        href = '/calibrated-motor/analysis/' + plan['id'] if plan else material
        result = bool(run and run.get('result_available') and run['status'] in ('completed','completed_after_budget'))
        next_step = dict(action='native_prepare', label='准备原生导入记录', href=material)
        rank = 1
        native = records.get('native_import')
        if native:
            status = native['status']; rank = 2
            if status == 'ready_source_changed':
                next_step.update(action='native_reprepare', label='程序已更新，保留旧记录并准备新导入')
            elif status == 'ready':
                next_step.update(action='native_start', label='执行现有导入与预检')
            elif status == 'imported_not_solved':
                rank = 3; next_step.update(action='plan_prepare', label='准备转矩分析计划')
                if plan:
                    next_step.update(href=href)
                    if plan['status'] == 'ready_for_model_preparation':
                        rank = 4; next_step.update(action='model_prepare', label='复制已导入分析工程')
                    elif plan['status'] == 'model_snapshot_ready_not_submitted':
                        rank = 5; next_step.update(action='execution_prepare', label='准备独立转矩执行任务')
                        if run:
                            rank = 6
                            next_step.update(action='execution_start', label='手动执行现有转矩任务')
                            if run['status'] == 'prepared_source_changed':
                                next_step.update(action='execution_reprepare', label='程序已更新，保留旧任务并准备新执行')
            if result:
                rank = 8; next_step.update(action='result', label='查看官方转矩与下载', href=href)
            for stage in ('native_import', 'analysis_plan', 'execution'):
                r = records.get(stage)
                if r and r['status'] in ACTIVE:
                    rank = 7; next_step.update(action='wait', label='任务正在执行，查看状态', href=href)
                elif r and r['status'] in REVIEW:
                    next_step.update(action='review', label='检查失败、超时或文件现场，不自动重试', href=href)
        blocked = None
        if next_step['action'] in ('native_start', 'execution_start'):
            if resource['cpu_only']: blocked = '当前实例为CPU预览'
            elif not resource['license'].get('available'): blocked = '现有许可服务不可连接'
            elif resource['pending_native_tasks']: blocked = '其他原生任务未结束或需要检查'
        next_step['blocked_reason'] = blocked
        out = dict(records=steps, next_step=next_step, progress_rank=rank, result_available=result)
        if result:
            out['torque'] = {k:run['result']['metrics'][k] for k in ('T_avg_Nm','T_min_Nm','T_max_Nm','K_T_ripple_pct','torque_csv_sha256')}
            out['result_scope'] = 'actual_calibrated_material_motor_torque_only'
        return out

    @staticmethod
    def _next(branches, href):
        if not branches: return dict(action='motor_prepare', label='准备隔离电机副本', href=href, blocked_reason=None)
        return max(branches, key=lambda b:(b['progress_rank'], b['next_step']['action'] not in ('review','native_reprepare','execution_reprepare')))['next_step']

    def bundle(self):
        snapshot = self.snapshot()
        files = {'workflow.json':json.dumps(snapshot, ensure_ascii=False, indent=2).encode('utf8')}
        stream = io.StringIO(newline=''); writer = csv.writer(stream)
        writer.writerow(['calibration_id','material_key','material_name','available','next_action','blocked_reason','effective_bank_sha256','excluded_grades','actual_torque_results'])
        for row in snapshot['materials']:
            writer.writerow([row['calibration_id'],row['material_key'],row.get('material_name',''),row['available'],row['next_step']['action'],
                row['next_step'].get('blocked_reason') or '',row.get('effective_bank_sha256',''),';'.join(row.get('excluded_grades',[])),sum(b['result_available'] for b in row['branches'])])
        files['materials.csv'] = stream.getvalue().encode('utf-8-sig')
        manifest = dict(version=VERSION, checked_utc=snapshot['checked_utc'], read_only=True, new_native_solves=0,
            files={name:dict(sha256=sha(blob),bytes=len(blob)) for name,blob in files.items()})
        out = io.BytesIO()
        with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as archive:
            for name,blob in files.items(): archive.writestr(name,blob)
            archive.writestr('manifest.json',json.dumps(manifest, ensure_ascii=False, indent=2))
        out.seek(0); return out
