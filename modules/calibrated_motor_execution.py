"""One-shot torque jobs from actually imported, frozen material models."""
import io
import json
import re
import shutil
import subprocess
import threading
import uuid
import zipfile
from datetime import datetime,timezone
from pathlib import Path

from modules.calibrated_motor_analysis import collect_torque,protocol
from modules.material_library import sha
from modules.maxwell_material_transport import load_contract,verify_saved_material
from modules.maxwell_native_readiness import require_configured_license_connection
from modules.motor_model_audit import audit_project
from modules.motor_queue import QueueLease,process_identity,identity_status
from modules.motor_workbench import write_json

VERSION='BH_only_motor_execution_v1'
BUDGET=dict(native_sessions=1,motor_solves=1,field_solves=0,max_cores=2,gpus=0,wall_seconds=1200)
SUPPORT=('modules/calibrated_motor_execution.py','modules/calibrated_motor_analysis.py',
    'modules/maxwell_material_transport.py','modules/maxwell_native_readiness.py','modules/motor_model_audit.py',
    'modules/motor_queue.py','modules/motor_workbench.py','tools/motor_scan_worker.py','tools/calibrated_motor_analysis_worker.py')
TERMINAL=('completed','completed_after_budget','failed','needs_attention')

def read(path):return json.loads(Path(path).read_text(encoding='utf8'))

def verify_model(path,manifest):
    verify_saved_material(Path(path).read_text(encoding='utf-8-sig'),manifest['material_contract'])
    audit=audit_project(path)
    if (not audit['local_CS_verified'] or audit['moving_insert_count']!=48 or
        not audit['core_loss']['complete_insert_coverage'] or
        any(r['material'].casefold()!=manifest['material_name'].casefold() for r in audit['inserts']) or
        audit['operating_point']['speed_rpm']!=3000 or audit['operating_point']['poles']!=4 or
        audit['core_loss']['enabled_object_names'] is None or
        any(n.startswith('unknown_ID_') for n in audit['core_loss']['enabled_object_names'])):
        raise ValueError('实际保存工程的材料、48插片/CS、损耗选择或运行点不符')
    return audit

def verify_inputs(folder,project,*,dispatch=False):
    folder,project=Path(folder).resolve(),Path(project).resolve();m=read(folder/'manifest.json')
    if (not re.fullmatch(r'bhexec_[0-9a-f]{12}',folder.name) or m['id']!=folder.name or m['version']!=VERSION or
        m['budget']!=BUDGET or type(m['cores']) is not int or not 1<=m['cores']<=2 or
        m['efficiency_enabled'] is not False or m['optimization_ranking_enabled'] is not False or
        m['requested_protocol']!=protocol(m['measurement_protocol']) or m['H_axis']!='physical_A_per_m' or m['H_scale']!=1 or
        set(m['input_hashes'])!={'inputs/motor.aedt','inputs/source_bundle.zip','inputs/material.amat','inputs/material.metadata.json'} or
        set(m['producer_hashes'])!=set(SUPPORT)):
        raise ValueError('转矩执行身份、固定预算或输入范围不符')
    for name,digest in m['input_hashes'].items():
        path=(folder/name).resolve()
        if not path.is_relative_to(folder/'inputs') or sha(path.read_bytes())!=digest:raise ValueError('执行冻结输入改变：'+name)
    contract=load_contract(folder/'inputs/material.amat')
    if contract!=m['material_contract'] or contract['material_name']!=m['material_name']:raise ValueError('执行材料合同不符')
    with zipfile.ZipFile(folder/'inputs/source_bundle.zip') as archive:
        plan=json.loads(archive.read('plan.json'));binding=json.loads(archive.read('model_binding.json'))
        if (plan['id']!=m['analysis_plan_id'] or plan['status']!='model_snapshot_ready_not_submitted' or
            plan['material_contract']!=contract or plan['native_import_id']!=m['native_import_id'] or
            any(plan[k]!=m[k] for k in ('parent_bank_sha256','effective_bank_sha256','training_grades','excluded_grades','measurement_protocol')) or
            binding['source_native_validation']!=1 or binding['native_import_id']!=m['native_import_id'] or
            sha(archive.read('model/motor.aedt'))!=binding['files']['model/motor.aedt'] or
            archive.read('model/motor.aedt')!=(folder/'inputs/motor.aedt').read_bytes() or
            archive.read('inputs/material.amat')!=(folder/'inputs/material.amat').read_bytes() or
            archive.read('inputs/material.metadata.json')!=(folder/'inputs/material.metadata.json').read_bytes()):
            raise ValueError('执行包与已导入工程/实际训练库不符')
    if dispatch:
        for name,digest in m['producer_hashes'].items():
            if sha((project/name).read_bytes())!=digest:raise ValueError('执行冻结程序已改变：'+name)
    return m

class CalibratedMotorExecution:
    def __init__(self,analysis,motor,storage):
        self.analysis,self.motor=analysis,motor;self.project=motor.project
        self.storage=Path(storage).resolve();self.storage.mkdir(parents=True,exist_ok=True)

    def path(self,artifact):
        if not isinstance(artifact,str) or not re.fullmatch(r'bhexec_[0-9a-f]{12}',artifact):raise ValueError('无效转矩执行任务')
        path=(self.storage/artifact).resolve()
        if not path.is_relative_to(self.storage):raise ValueError('执行任务超出目录')
        return path

    def prepare(self,data):
        if not isinstance(data,dict) or set(data)!={'analysis_plan'}:raise ValueError('请选择已准备分析工程的计划')
        plan=self.analysis.get(data['analysis_plan'])
        if plan['status']!='model_snapshot_ready_not_submitted':raise ValueError('实际导入与分析工程复制通过后才能准备执行任务')
        origin=self.analysis.path(plan['id']);artifact='bhexec_'+uuid.uuid4().hex[:12];folder=self.path(artifact)
        inputs=folder/'inputs';inputs.mkdir(parents=True)
        shutil.copy2(origin/'model/motor.aedt',inputs/'motor.aedt');shutil.copy2(origin/'inputs/material.amat',inputs/'material.amat')
        shutil.copy2(origin/'inputs/material.metadata.json',inputs/'material.metadata.json')
        (inputs/'source_bundle.zip').write_bytes(self.analysis.bundle(plan['id']).getvalue())
        m=dict(id=artifact,version=VERSION,created_utc=datetime.now(timezone.utc).isoformat(),analysis_plan_id=plan['id'],
            native_import_id=plan['native_import_id'],material_name=plan['material_name'],material_contract=plan['material_contract'],
            parent_bank_sha256=plan['parent_bank_sha256'],effective_bank_sha256=plan['effective_bank_sha256'],
            training_grades=plan['training_grades'],excluded_grades=plan['excluded_grades'],H_axis='physical_A_per_m',H_scale=1,
            measurement_protocol=plan['measurement_protocol'],requested_protocol=plan['requested_protocol'],cores=min(plan['cores'],2),
            budget=BUDGET,design='Motor-CAD 2',setup='Setup1',aedt_version='2025.1',
            efficiency_enabled=False,optimization_ranking_enabled=False,
            input_hashes={p.relative_to(folder).as_posix():sha(p.read_bytes()) for p in inputs.iterdir()},
            producer_hashes={name:sha((self.project/name).read_bytes()) for name in SUPPORT})
        verify_model(inputs/'motor.aedt',m);write_json(folder/'manifest.json',m)
        write_json(folder/'state.json',dict(id=artifact,status='prepared',phase='not_started',new_Maxwell_solves=0))
        return self.get(artifact)

    def get(self,artifact):
        folder=self.path(artifact);m=verify_inputs(folder,self.project);s=read(folder/'state.json')
        if s['id']!=artifact or s['status'] not in ('prepared','starting','running',*TERMINAL):raise ValueError('执行状态身份不符')
        result=dict(m,**s);result.update(result_available=False,efficiency_enabled=False,optimization_ranking_enabled=False)
        if s['status']=='prepared':
            compatible=all((self.project/n).is_file() and sha((self.project/n).read_bytes())==h for n,h in m['producer_hashes'].items())
            result.update(dispatch_compatible=compatible)
            if not compatible:result.update(status='prepared_source_changed',error='执行程序已更新；保留记录，请准备新执行副本')
        if s['status'] in ('starting','running'):
            if (folder/'attention.json').exists():result.update(status='needs_attention',error=read(folder/'attention.json')['error'])
            elif (folder/'dispatch.json').exists() and identity_status(read(folder/'dispatch.json')['identity'])=='dead':
                result.update(status='needs_attention',error='执行进程已退出，现场保留，不自动重试')
        if s['status'] in ('completed','completed_after_budget'):
            saved=read(folder/'result.json');report=folder/'case/reports/Torque Plots.csv'
            if sha(report.read_bytes())!=saved['metrics']['torque_csv_sha256']:raise ValueError('实际官方转矩CSV哈希改变')
            metrics=collect_torque(report,m['measurement_protocol'])
            if (saved['execution_id']!=artifact or saved['analysis_plan_id']!=m['analysis_plan_id'] or
                saved['metrics']!=metrics or saved['native_analyze_succeeded'] is not True or saved['native_configuration_verified'] is not True or
                saved['new_Maxwell_solves']!=1 or saved['efficiency_enabled'] is not False or saved['optimization_ranking_enabled'] is not False or
                saved['effective_bank_sha256']!=m['effective_bank_sha256'] or saved['excluded_grades']!=m['excluded_grades'] or
                sha((folder/'case/motor.aedt').read_bytes())!=saved['solved_project_sha256']):raise ValueError('实际转矩结果或保存工程绑定不符')
            verify_saved_material((folder/'case/motor.aedt').read_text(encoding='utf-8-sig'),m['material_contract'])
            result.update(result=saved,result_available=True,new_Maxwell_solves=1)
        return result

    def records(self):
        rows=[]
        for folder in self.storage.glob('bhexec_*'):
            try:rows.append(self.get(folder.name))
            except (OSError,KeyError,ValueError,TypeError) as exc:rows.append(dict(id=folder.name,status='invalid',error=str(exc)))
        return sorted(rows,key=lambda r:r.get('created_utc',''),reverse=True)

    def ensure_idle(self):
        for record in self.records():
            if record['status'] in ('starting','running','needs_attention','invalid'):raise ValueError('转矩任务未结束或需核对，暂不下发其他原生任务')
            folder=self.path(record['id'])
            for name in ('dispatch.json','desktop_session.json'):
                if (folder/name).exists() and identity_status(read(folder/name)['identity'])!='dead':
                    raise ValueError('转矩进程/专用会话仍活动或身份不明，暂不下发其他原生任务')

    def start(self,artifact):
        folder=self.path(artifact);lease=QueueLease(self.storage/'dispatch.lease');locks=[]
        if not lease.acquire():raise ValueError('其他转矩任务正在下发')
        try:
            m=verify_inputs(folder,self.project,dispatch=True)
            if self.get(artifact)['status']!='prepared' or any((folder/n).exists() for n in ('dispatch.json','worker.log','case')):
                raise ValueError('已有执行痕迹，禁止重复执行')
            for record in self.records():
                other=self.path(record['id'])
                if record['id']!=artifact and record['status'] in ('starting','running','needs_attention','invalid'):raise ValueError('已有转矩任务未结束或需核对')
                for name in ('dispatch.json','desktop_session.json'):
                    if (other/name).exists() and identity_status(read(other/name)['identity'])!='dead':raise ValueError('已有执行进程/会话仍活动或身份不明')
            if self.motor._ownership_obstacles() or any(j['status'] in ('queued','starting','running') for j in self.motor.jobs()):
                raise ValueError('请先等待或核对既有扫描队列')
            native=self.analysis.native
            for record in native.records():
                if record['status'] in ('starting','running','needs_attention','invalid'):raise ValueError('原生导入未结束或需核对')
                for name in ('dispatch.json','desktop_session.json'):
                    other=native.path(record['id'])/name
                    if other.exists() and identity_status(read(other)['identity'])!='dead':raise ValueError('原生导入进程/会话仍活动或身份不明')
            try:readiness=require_configured_license_connection()
            except ValueError as exc:raise ValueError('现有许可服务不可连接；未下发，执行任务保留') from exc
            python=self.motor.root/'.runtime/aedt/Scripts/python.exe'
            if not python.is_file():raise ValueError('缺少本机AEDT Python，未下发')
            for path in (self.motor.storage/'queue.guard',native.storage/'dispatch.lease',native.storage/'execution.lease'):
                item=QueueLease(path)
                if not item.acquire():raise ValueError('扫描或导入仍在执行，未下发')
                locks.append(item)
            queue_owner=self.motor.storage/'queue.lock'
            if queue_owner.exists():raise ValueError('存在旧队列归属记录；请先在扫描页检查恢复，不覆盖')
            token=uuid.uuid4().hex
            s=dict(id=artifact,status='starting',phase='launching',new_Maxwell_solves=0,license_readiness=readiness,queue_token=token)
            write_json(folder/'state.json',s)
            try:
                with (folder/'worker.log').open('x',encoding='utf8') as log:
                    proc=subprocess.Popen([str(python),'-X','utf8',str(self.project/'tools/calibrated_motor_analysis_worker.py'),
                        '--folder',str(folder),'--motor-storage',str(self.motor.storage),'--native-storage',str(native.storage)],
                        cwd=folder,stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                owner=process_identity(proc.pid)
                write_json(folder/'dispatch.json',dict(identity=owner,utc=datetime.now(timezone.utc).isoformat()))
                write_json(queue_owner,dict(owner,token=token,torque_execution_id=artifact))
                threading.Thread(target=self._watch,args=(proc,folder),daemon=True).start()
            except Exception as exc:
                write_json(folder/'attention.json',dict(error='下发未确认，保留现场：'+str(exc)))
                raise ValueError('下发失败，现场保留：'+str(exc)) from exc
            return self.get(artifact)
        finally:
            for item in reversed(locks):item.release()
            lease.release()

    @staticmethod
    def _watch(proc,folder):
        try:code=proc.wait(timeout=BUDGET['wall_seconds'])
        except subprocess.TimeoutExpired:code=None
        if read(folder/'state.json')['status'] not in TERMINAL:
            write_json(folder/'attention.json',dict(error='超过1200秒预算，保留进程/现场，不自动重试' if code is None else '执行进程退出 '+str(code)+'，请查看日志'))

    def download(self,artifact,name):
        record=self.get(artifact)
        allowed={'manifest.json','state.json'}
        if record['result_available']:allowed.update({'result.json','summary.csv','case/reports/Torque Plots.csv'})
        if name not in allowed:raise ValueError('该任务当前没有可下载的官方结果')
        return self.path(artifact)/name

    def bundle(self,artifact):
        record=self.get(artifact);folder=self.path(artifact);out=io.BytesIO()
        with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('execution.json',json.dumps(record,ensure_ascii=False,indent=2))
            for name in record['input_hashes']:archive.write(folder/name,name)
            for name in ('failure.json','attention.json','configuration.json'):
                if (folder/name).is_file():archive.write(folder/name,name)
            if record['result_available']:
                for name in ('result.json','summary.csv','case/reports/Torque Plots.csv'):archive.write(folder/name,name)
        out.seek(0);return out
