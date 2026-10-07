"""Frozen scan/screening plans. Monitoring never submits or retries a solver."""
import json
import re
import threading
import uuid
from datetime import datetime,timezone

from modules.material_calibration import file_hash
from modules.motor_optimization import validate_config
from modules.motor_queue import QueueLease
from modules.motor_workbench import write_json


class OptimizationPlans:
    def __init__(self,optimization):
        self.optimization=optimization
        self.motor=optimization.motor
        self.storage=optimization.storage/'plans'
        self.thread_lock=threading.Lock()
        self.monitor_active=False
        self.stop_event=threading.Event()

    def path(self,plan_id):
        if not isinstance(plan_id,str) or not re.fullmatch(r'plan_[0-9a-f]{12}',plan_id):
            raise ValueError('无效优化计划 ID')
        return self.storage/plan_id

    def get(self,plan_id):
        folder=self.path(plan_id)
        proposal=json.loads((folder/'plan.json').read_text(encoding='utf-8'))
        state=json.loads((folder/'state.json').read_text(encoding='utf-8'))
        if proposal.get('id')!=plan_id:
            raise ValueError('计划身份与目录不一致')
        if file_hash(folder/'plan.json')!=state['plan_sha256']:
            raise ValueError('优化计划配置已改变，请建立新计划')
        return dict(proposal,**state)

    def records(self):
        result=[]
        for path in sorted(self.storage.glob('plan_*'),reverse=True):
            if not re.fullmatch(r'plan_[0-9a-f]{12}',path.name) or not (path/'state.json').is_file():
                continue
            try:
                result.append(self.get(path.name))
            except (ValueError,KeyError,OSError,TypeError) as error:
                result.append(dict(id=path.name,status='needs_attention',error=str(error),source_readable=False))
        return result

    def prepare(self,data):
        if not isinstance(data,dict):
            raise ValueError('请输入扫描材料与筛选配置')
        config=validate_config(data.get('config',{}))
        job_id=data.get('job_id')
        if job_id:
            job=self.motor.state(job_id)
            manifest=json.loads((self.motor.path(job_id)/'manifest.json').read_text(encoding='utf-8'))
            materials=[c['material'] for c in manifest['cases']]
            if not materials:
                raise ValueError('该任务没有材料案')
        else:
            materials=data.get('materials')
            if not isinstance(materials,list) or not materials or not all(isinstance(m,str) for m in materials):
                raise ValueError('请选择材料牌号')
            reference=data.get('reference_material') or materials[0]
            if reference not in materials:
                raise ValueError('比较基准必须属于本次选择的材料')
            job=self.motor.prepare(materials,data.get('cores',4),data.get('measurement_protocol','v8_2_cycle1'))
            job_id=job['id']
            manifest=json.loads((self.motor.path(job_id)/'manifest.json').read_text(encoding='utf-8'))
        reference=data.get('reference_material') or materials[0]
        matches=[c for c in manifest['cases'] if c['material']==reference]
        if len(matches)!=1:
            raise ValueError('本任务需要唯一的比较基准材料')
        plan_id='plan_'+uuid.uuid4().hex[:12]
        folder=self.path(plan_id)
        folder.mkdir(parents=True)
        proposal=dict(id=plan_id,created_utc=datetime.now(timezone.utc).isoformat(),job_id=job_id,
                      materials=materials,reference_material=reference,reference_case_id=matches[0]['id'],
                      config=config,job_manifest_sha256=file_hash(self.motor.path(job_id)/'manifest.json'),
                      measurement_protocol=manifest.get('measurement_protocol','v8_2_cycle1'),
                      cores=manifest.get('cores'),scope='One frozen scan, same-job engineering score; original results unchanged',
                      automatic_native_retry=False,producer_sha256=file_hash(__file__))
        write_json(folder/'plan.json',proposal)
        write_json(folder/'state.json',dict(status='prepared' if job['status']=='prepared' else 'watching',plan_sha256=file_hash(folder/'plan.json')))
        if job['status'] in ('completed','completed_with_failures'):
            self.refresh(plan_id)
        self.start_monitor()
        return self.get(plan_id)

    def submit(self,plan_id):
        value=self.get(plan_id)
        if value['status']!='prepared':
            raise ValueError('计划已下发；不会重复提交或自动重试')
        self.motor.submit(value['job_id'])
        write_json(self.path(plan_id)/'state.json',dict(status='watching',plan_sha256=value['plan_sha256']))
        self.start_monitor()
        return self.get(plan_id)

    def refresh(self,plan_id):
        lease=QueueLease(self.path(plan_id)/'analysis.guard')
        if not lease.acquire():
            return self.get(plan_id)
        try:
            value=self.get(plan_id)
            if value['status']!='watching':
                return value
            state=dict(status='needs_attention',plan_sha256=value['plan_sha256'])
            try:
                job=self.motor.state(value['job_id'])
                if job['status'] in ('prepared','queued','starting','running'):
                    return value
                if file_hash(self.motor.path(value['job_id'])/'manifest.json')!=value['job_manifest_sha256']:
                    raise ValueError('任务定义在计划建立后改变')
                if job['status'] not in ('completed','completed_with_failures'):
                    raise ValueError('任务中断或未完成；现场保留，没有重试')
                dataset=self.optimization.dataset(value['job_id'])
                if value['reference_case_id'] not in {r['case'] for r in dataset['references']}:
                    raise ValueError('选定基准未完成或未通过；没有替换基准或伪造评分')
                report=self.optimization.analyze(dict(dataset=value['job_id'],reference=value['reference_case_id'],config=value['config']))
                state.update(status='completed',analysis_id=report['id'],best_case=report['best_case'],
                             meets_limits_count=report['meets_limits_count'],completed_utc=datetime.now(timezone.utc).isoformat())
            except (ValueError,KeyError,OSError) as error:
                state['error']=str(error)
            write_json(self.path(plan_id)/'state.json',state)
            return self.get(plan_id)
        finally:
            lease.release()

    def start_monitor(self):
        with self.thread_lock:
            if self.monitor_active or not any(p['status']=='watching' for p in self.records()):
                return
            self.monitor_active=True
            threading.Thread(target=self._monitor,daemon=True).start()

    def _monitor(self):
        try:
            while not self.stop_event.is_set():
                pending=[p for p in self.records() if p['status']=='watching']
                if not pending:
                    break
                for plan in pending:
                    try:
                        self.refresh(plan['id'])
                    except (ValueError,KeyError,OSError):
                        continue
                self.stop_event.wait(5)
        finally:
            with self.thread_lock:
                self.monitor_active=False
            if not self.stop_event.is_set():
                self.start_monitor()
