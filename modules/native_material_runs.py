"""Bounded new-material simulations using the existing texture and pilot producers."""
import json
import math
import os
import re
import shutil
import subprocess
import sys
import uuid
from datetime import datetime,timezone
from pathlib import Path
import psutil
from modules.calibration_transfer import load_bank
from modules.material_calibration import PHYSICS_VERSION,file_hash
from modules.motor_queue import DispatchWindow,QueueLease,process_identity,identity_status
from modules.motor_workbench import write_json

VERSION='compatible_native_material_run_v1'
BUDGET=dict(max_grains_per_direction=8,max_jobs=16,gpus=1,wall_seconds=120,job_seconds=20)
SUPPORT=('modules/native_material_runs.py','tools/native_material_run_worker.py',
 'modules/mx3_generator.py','modules/odf_texture.py','modules/texture_sampling.py',
 'tools/run_calibration_pilot.py','tools/analyze_sampling_convergence.py',
 'modules/material_calibration.py','modules/native_pair_export.py','modules/native_pair_exports.py','modules/motor_queue.py')

def read(path):return json.loads(Path(path).read_text(encoding='utf8'))
def python_path():
    candidate=Path(sys.prefix)/'Scripts/python.exe'
    return candidate if candidate.is_file() else Path(sys.executable)

def validate_recipe(data):
    if not isinstance(data,dict) or set(data)!={'calibration_id','material_id','params','thickness_mm','n_grains','seed'}:
        raise ValueError('材料仿真参数字段不完整')
    if not isinstance(data['material_id'],str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,40}',data['material_id']):raise ValueError('材料代号限1–40个英文字母、数字、下划线或连字符')
    p=data['params'];bounds=dict(f_Goss=(0,1),theta_0_deg=(0,45),halfwidth_deg=(.1,60),Si_content=(0,6.5))
    if not isinstance(p,dict) or set(p)!=set(bounds):raise ValueError('材料参数字段不正确')
    for key,(low,high) in bounds.items():
        value=p[key]
        if isinstance(value,bool) or not isinstance(value,(float,int)) or not math.isfinite(value) or not low<=value<=high:raise ValueError(key+'超出支持范围')
    n=data['n_grains'];seed=data['seed'];thickness=data['thickness_mm']
    if type(n) is not int or not 1<=n<=8:raise ValueError('每方向晶粒数需为1–8整数')
    if type(seed) is not int or not 0<=seed<=2**31-1:raise ValueError('随机种子需为非负整数')
    if isinstance(thickness,bool) or not isinstance(thickness,(int,float)) or not math.isfinite(thickness) or not .05<=thickness<=1:raise ValueError('厚度需为0.05–1mm')
    if not isinstance(data['calibration_id'],str) or not re.fullmatch(r'cal_[0-9a-f]{12}',data['calibration_id']):raise ValueError('请选择保存的校准库')
    return data

def verify_run(folder,project,*,dispatch=False):
    folder=Path(folder).resolve();project=Path(project).resolve();m=read(folder/'manifest.json')
    if not isinstance(m,dict) or not re.fullmatch(r'nativerun_[0-9a-f]{12}',folder.name) or m.get('id')!=folder.name or m.get('version')!=VERSION or m.get('budget')!=BUDGET:raise ValueError('仿真任务身份或预算不符')
    validate_recipe(m['recipe'])
    if file_hash(folder/'selected_bank.json')!=m['selected_bank_file_sha256']:raise ValueError('冻结校准库发生变化')
    bank=load_bank(folder/'selected_bank.json')
    if bank.bank_sha256!=m['selected_bank_sha256'] or bank.texture_sampling_version!=m['texture_sampling_version']:raise ValueError('生成协议与冻结校准库不符')
    if set(m['producer_hashes'])!=set(SUPPORT):raise ValueError('生产程序清单不符')
    if dispatch and any(file_hash(project/name)!=digest for name,digest in m['producer_hashes'].items()):raise ValueError('仿真程序已更新；保留旧任务，请新建任务')
    if (folder/'prepared.json').is_file():
        for name,digest in read(folder/'prepared.json')['input_hashes'].items():
            path=(folder/name).resolve()
            if not path.is_relative_to(folder/'run') or file_hash(path)!=digest:raise ValueError('冻结仿真输入发生变化')
    return m


class NativeMaterialRuns:
    def __init__(self,calibration,motor,native_pairs,storage):
        self.calibration=calibration;self.motor=motor;self.exports=native_pairs
        self.storage=Path(storage).resolve();self.project=motor.project;self.root=motor.root

    def path(self,artifact):
        if not isinstance(artifact,str) or not re.fullmatch(r'nativerun_[0-9a-f]{12}',artifact):raise ValueError('无效材料仿真ID')
        folder=(self.storage/artifact).resolve()
        if not folder.is_relative_to(self.storage):raise ValueError('材料仿真目录越界')
        return folder

    def prepare(self,data):
        validate_recipe(data)
        bank_path=self.calibration.get(data['calibration_id'])/'bank.json'
        bank=load_bank(bank_path)
        report=read(bank_path.parent/'report.json')
        if bank.bank_sha256!=report['bank_sha256']:raise ValueError('所选校准库与记录不符')
        artifact='nativerun_'+uuid.uuid4().hex[:12];folder=self.path(artifact);folder.mkdir(parents=True)
        shutil.copyfile(bank_path,folder/'selected_bank.json')
        manifest=dict(id=artifact,version=VERSION,recipe=data,budget=BUDGET,created_utc=datetime.now(timezone.utc).isoformat(),
            texture_sampling_version=bank.texture_sampling_version,physics_version=PHYSICS_VERSION,resource_root=str(self.root),
            selected_bank_sha256=bank.bank_sha256,selected_bank_file_sha256=file_hash(folder/'selected_bank.json'),
            producer_hashes={name:file_hash(self.project/name) for name in SUPPORT},input_role='user_defined_simulation_not_measured_grade')
        write_json(folder/'manifest.json',manifest);write_json(folder/'state.json',dict(status='preparing',new_MuMax_solves=0))
        try:
            with (folder/'prepare.log').open('x',encoding='utf8') as stream:
                result=subprocess.run([str(python_path()),'-X','utf8',str(self.project/'tools/native_material_run_worker.py'),'--folder',str(folder),'--prepare'],
                    cwd=folder,stdout=stream,stderr=subprocess.STDOUT,timeout=45,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            if result.returncode!=0 and read(folder/'state.json')['status']=='preparing':raise ValueError('脚本准备失败，请查看准备日志')
        except (OSError,subprocess.TimeoutExpired,ValueError) as exc:
            write_json(folder/'state.json',dict(status='preparation_failed',error=str(exc),new_MuMax_solves=0))
        return self.get(artifact)

    def get(self,artifact):
        folder=self.path(artifact);m=verify_run(folder,self.project);state=read(folder/'state.json')
        if not isinstance(state,dict) or 'status' not in state:raise ValueError('材料仿真状态格式不完整')
        compatible=all(file_hash(self.project/name)==digest for name,digest in m['producer_hashes'].items())
        result=dict(m,**state,producer_compatible=compatible)
        if state.get('export_id'):
            result['prediction_url']='/workbench?native_pair='+state['export_id']+'&calibration='+m['recipe']['calibration_id']+'#cal'
        return result

    def records(self):
        rows=[]
        for path in sorted(self.storage.glob('nativerun_*/manifest.json'),key=lambda p:p.stat().st_mtime,reverse=True):
            try:rows.append(self.get(path.parent.name))
            except (ValueError,KeyError,OSError) as exc:rows.append(dict(id=path.parent.name,status='needs_attention',error=str(exc)))
        return rows

    def start(self,artifact):
        folder=self.path(artifact)
        with DispatchWindow(self.storage.parent):
            record=self.get(artifact)
            if record['status'] not in ('ready','paused'):raise ValueError('任务不是待执行或已暂停状态；失败任务不自动重试')
            if record.get('elapsed_seconds',0)>=BUDGET['wall_seconds']:raise ValueError('累计执行预算已经耗尽，不能接续本任务')
            verify_run(folder,self.project,dispatch=True)
            if self.motor.queue_status()['active_ownership'] or self.motor.queue_status()['queued']:raise ValueError('电机队列正在使用资源，请完成后再开始材料仿真')
            if any(r['status'] in ('starting','running') for r in self.records()):raise ValueError('另一项材料仿真未结束，请先检查其状态')
            if any((p.info['name'] or '').lower() in ('mumax3.exe','mumax3') for p in psutil.process_iter(['name'])):raise ValueError('已有MuMax3进程正在运行，未下发新任务')
            from tools.run_calibration_pilot import resolve_mumax
            local=self.root/'.runtime/mumax3/mumax3.exe';binary=resolve_mumax(str(local) if local.is_file() else None)
            token=uuid.uuid4().hex;(folder/'launches').mkdir(exist_ok=True)
            write_json(folder/'launches'/f'{token}.json',dict(token=token,mumax_binary=str(binary),mumax_binary_sha256=file_hash(binary)))
            if (folder/'pause.request').exists():(folder/'pause.request').rename(folder/'launches'/f'{token}_previous_pause.json')
            write_json(folder/'state.json',dict(status='starting',queue_token=token,new_MuMax_solves=record.get('new_MuMax_solves',0),elapsed_seconds=record.get('elapsed_seconds',0)))
            try:
                with (folder/f'worker_{token}.log').open('x',encoding='utf8') as stream:
                    process=subprocess.Popen([str(python_path()),'-X','utf8',str(self.project/'tools/native_material_run_worker.py'),
                        '--folder',str(folder),'--token',token],cwd=folder,stdout=stream,stderr=subprocess.STDOUT,
                        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                try:identity=process_identity(process.pid)
                except (psutil.Error,TypeError):identity=dict(pid=int(process.pid))
                write_json(folder/'launches'/f'{token}_dispatch.json',dict(identity=identity))
            except OSError as exc:
                write_json(folder/'state.json',dict(status='failed',error='启动失败：'+str(exc),new_MuMax_solves=record.get('new_MuMax_solves',0)))
            return self.get(artifact)

    def pause(self,artifact):
        record=self.get(artifact)
        if record['status'] not in ('starting','running'):raise ValueError('当前没有可暂停的材料仿真')
        write_json(self.path(artifact)/'pause.request',dict(utc=datetime.now(timezone.utc).isoformat()))
        return dict(status='pause_requested',message='当前晶粒结束后暂停；不关闭其他应用')

    def reconcile(self,artifact):
        """CPU-only inspection: never stop a process or resubmit a native job."""
        lease=QueueLease(self.storage.parent/'native_dispatch.guard')
        if not lease.acquire():raise ValueError('任务交接中，请稍后检查现场')
        try:return self._reconcile(artifact)
        finally:lease.release()

    def _reconcile(self,artifact):
        folder=self.path(artifact);record=self.get(artifact);state=read(folder/'state.json')
        if state['status'] not in ('starting','running'):return record
        for identity in [state.get('worker_identity')]+[read(p).get('identity') for p in (folder/'launches').glob('*_dispatch.json')]:
            if identity and identity_status(identity)!='dead':raise ValueError('任务仍有活动或未确认的进程，保持现场')
        token=state['queue_token']
        for process in psutil.process_iter(['pid','name']):
            name=(process.info['name'] or '').lower()
            if name in ('mumax3.exe','mumax3'):raise ValueError('仍有MuMax3进程，未修改未完成状态')
            if name not in ('python.exe','python','python3','pythonw.exe'):continue
            try:args=process.cmdline()
            except (psutil.AccessDenied,psutil.NoSuchProcess):raise ValueError('进程归属暂不可读，保持现场')
            if '--folder' in args and '--token' in args:
                try:
                    if Path(args[args.index('--folder')+1]).resolve()==folder and args[args.index('--token')+1]==token:
                        raise ValueError('本任务工作进程仍在运行，保持现场')
                except IndexError:raise ValueError('工作进程参数不完整，保持现场')
        write_json(folder/'reconciliation.json',dict(previous_state=state,reason='worker_exited_and_no_active_MuMax',new_native_solves=0))
        write_json(folder/'state.json',dict(state,status='failed',error='工作进程已退出且无活动MuMax；部分输出保留，未自动重试'))
        return self.get(artifact)

    def bundle(self,artifact):
        import io,zipfile
        self.get(artifact);folder=self.path(artifact);out=io.BytesIO()
        with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
            for path in folder.rglob('*'):
                if path.is_file() and path.resolve().is_relative_to(folder):z.write(path,path.relative_to(folder).as_posix())
        out.seek(0);return out
