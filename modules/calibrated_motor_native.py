"""One-shot native import of frozen BH packages, isolated from solve queues."""
import json
import os
import re
import shutil
import subprocess
import threading
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from modules.material_library import sha
from modules.maxwell_material_transport import load_contract
from modules.maxwell_native_readiness import require_configured_license_connection
from modules.motor_queue import QueueLease, identity_status, process_identity
from modules.motor_workbench import write_json

SUPPORT = ('modules/calibrated_motor_native.py', 'modules/maxwell_material_transport.py',
           'modules/maxwell_native_readiness.py', 'modules/motor_workbench.py',
           'modules/motor_queue.py', 'modules/motor_model_audit.py',
           'tools/calibrated_motor_import_worker.py')
TERMINAL = ('imported_not_solved', 'failed', 'needs_attention')


def bank_digest(blob):
    # Existing bank identity is canonical JSON, separate from file-byte hashes.
    return sha(json.dumps(json.loads(blob),sort_keys=True,separators=(',',':'),allow_nan=False).encode())


def verify_inputs(folder, project):
    folder, project = Path(folder).resolve(), Path(project).resolve()
    manifest = json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
    if (manifest['id'] != folder.name or manifest.get('new_native_solves') != 0 or
            manifest.get('efficiency_enabled') is not False or
            manifest.get('optimization_ranking_enabled') is not False or
            manifest.get('budget') != dict(native_sessions=1, field_solves=0, motor_solves=0, wall_seconds=240)):
        raise ValueError('原生导入范围或身份不符')
    for name, digest in manifest['input_hashes'].items():
        path = (folder/name).resolve()
        if not path.is_relative_to(folder/'inputs') or sha(path.read_bytes()) != digest:
            raise ValueError('冻结导入输入改变：'+name)
    if set(manifest['producer_hashes']) != set(SUPPORT):
        raise ValueError('冻结导入程序清单不符')
    for name, digest in manifest['producer_hashes'].items():
        if sha((project/name).read_bytes()) != digest:
            raise ValueError('冻结导入程序已改变；请保留本记录并准备新副本：'+name)
    parent = json.loads((folder/'inputs/parent_manifest.json').read_text(encoding='utf-8'))
    contract = load_contract(folder/'inputs/material.amat')
    if (contract != parent['material_contract'] or contract != manifest['material_contract'] or
            parent['id'] != manifest['preparation_id'] or
            parent['effective_bank_sha256'] != manifest['effective_bank_sha256'] or
            parent['parent_bank_sha256'] != manifest['parent_bank_sha256'] or
            parent['excluded_grades'] != manifest['excluded_grades'] or
            sha((folder/'inputs/motor.aedt').read_bytes()) != parent['files']['motor.aedt']):
        raise ValueError('导入合同与父准备记录不符')
    with zipfile.ZipFile(folder/'inputs/source_package.zip') as archive:
        if (sha((folder/'inputs/source_package.zip').read_bytes()) != parent['source_package_sha256'] or
                archive.read('material.amat') != (folder/'inputs/material.amat').read_bytes() or
                archive.read('metadata.json') != (folder/'inputs/material.metadata.json').read_bytes() or
                bank_digest(archive.read('parent_bank.json')) != manifest['parent_bank_sha256'] or
                bank_digest(archive.read('effective_bank.json')) != manifest['effective_bank_sha256']):
            raise ValueError('原包、实际训练库或材料字节不符')
    return manifest


class CalibratedMotorNative:
    def __init__(self, preparation, motor, storage):
        self.preparation, self.motor = preparation, motor
        self.project = motor.project
        self.storage = Path(storage).resolve()
        self.storage.mkdir(parents=True, exist_ok=True)

    def path(self, artifact):
        if not isinstance(artifact, str) or not re.fullmatch(r'calimport_[0-9a-f]{12}', artifact):
            raise ValueError('无效原生导入记录')
        path=(self.storage/artifact).resolve()
        if not path.is_relative_to(self.storage): raise ValueError('原生导入记录超出目录')
        return path

    def prepare(self, data):
        if not isinstance(data, dict) or set(data) != {'preparation'}:
            raise ValueError('请选择已冻结的电机准备副本')
        parent = self.preparation.get(data['preparation'])
        origin = self.preparation.path(parent['id'])
        artifact = 'calimport_'+uuid.uuid4().hex[:12]
        folder = self.path(artifact)
        inputs = folder/'inputs'
        inputs.mkdir(parents=True)
        for name, relative in (('motor.aedt','motor.aedt'), ('material.amat','package/material.amat'),
                               ('material.metadata.json','package/material.metadata.json'),
                               ('source_package.zip','package.zip'),
                               ('parent_manifest.json','manifest.json')):
            shutil.copy2(origin/relative, inputs/name)
        manifest = dict(id=artifact, created_utc=datetime.now(timezone.utc).isoformat(),
            preparation_id=parent['id'], material_name=parent['material_name'], cores=parent['cores'],
            parent_bank_sha256=parent['parent_bank_sha256'], effective_bank_sha256=parent['effective_bank_sha256'],
            training_grades=parent['training_grades'], excluded_grades=parent['excluded_grades'],
            material_contract=parent['material_contract'], new_native_solves=0,
            efficiency_enabled=False, optimization_ranking_enabled=False,
            budget=dict(native_sessions=1, field_solves=0, motor_solves=0, wall_seconds=240),
            input_hashes={p.relative_to(folder).as_posix():sha(p.read_bytes()) for p in inputs.iterdir()},
            producer_hashes={name:sha((self.project/name).read_bytes()) for name in SUPPORT})
        write_json(folder/'manifest.json', manifest)
        write_json(folder/'state.json',dict(id=artifact, status='ready', created_utc=manifest['created_utc']))
        verify_inputs(folder,self.project)
        return self.get(artifact)

    def get(self, artifact):
        folder = self.path(artifact)
        # Completed outputs survive subsequent source updates; frozen input checks still apply.
        manifest = json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
        state = json.loads((folder/'state.json').read_text(encoding='utf-8'))
        if (manifest['id'] != artifact or state['id'] != artifact or
                manifest.get('new_native_solves') != 0 or manifest.get('efficiency_enabled') is not False or
                manifest.get('optimization_ranking_enabled') is not False or
                state.get('status') not in ('ready','starting','running',*TERMINAL)):
            raise ValueError('导入记录身份不符')
        for name, digest in manifest['input_hashes'].items():
            path = (folder/name).resolve()
            if not path.is_relative_to(folder/'inputs') or sha(path.read_bytes()) != digest:
                raise ValueError('导入冻结文件改变：'+name)
        result = dict(manifest, **state)
        if state['status']=='ready':
            compatible=all((self.project/name).is_file() and sha((self.project/name).read_bytes())==digest
                           for name,digest in manifest['producer_hashes'].items())
            result['dispatch_compatible']=compatible
            if not compatible:
                result.update(status='ready_source_changed',error='准备后程序已更新；保留本记录，请从父副本准备新导入')
        if state['status'] in ('starting','running') and (folder/'attention.json').exists():
            attention=json.loads((folder/'attention.json').read_text(encoding='utf-8'))
            result.update(status='needs_attention',error=attention['error'])
        if state['status'] in ('starting','running') and (folder/'dispatch.json').exists():
            owner = json.loads((folder/'dispatch.json').read_text(encoding='utf-8'))['identity']
            if identity_status(owner) == 'dead':
                result.update(status='needs_attention', error='导入进程已退出；保留现场，不自动重试')
        if state['status'] == 'imported_not_solved':
            summary = json.loads((folder/'summary.json').read_text(encoding='utf-8'))
            if (summary.get('status') != 'native_import_preflight_passed_not_solved' or
                    summary.get('new_native_solves') != 0 or
                    sha((folder/'motor.aedt').read_bytes()) != summary['saved_project_sha256']):
                raise ValueError('导入保存结果不符')
            result['summary'] = summary
        result.update(efficiency_enabled=False, optimization_ranking_enabled=False, new_native_solves=0)
        return result

    def records(self):
        rows=[]
        for folder in self.storage.glob('calimport_*'):
            try: rows.append(self.get(folder.name))
            except (ValueError, KeyError, OSError) as exc:
                rows.append(dict(id=folder.name,status='invalid',error=str(exc)))
        return sorted(rows,key=lambda r:r.get('created_utc',''),reverse=True)

    def start(self, artifact):
        folder = self.path(artifact)
        lease = QueueLease(self.storage/'dispatch.lease')
        if not lease.acquire(): raise ValueError('另一项导入正在下发')
        try:
            manifest = verify_inputs(folder,self.project)
            if self.get(artifact)['status'] != 'ready' or any((folder/n).exists() for n in ('dispatch.json','worker.log','motor.aedt')):
                raise ValueError('已有执行痕迹；本记录不允许重复执行')
            for other in self.records():
                other_folder=self.path(other['id'])
                if other['id'] != artifact and other['status'] in ('starting','running','invalid'):
                    raise ValueError('其他导入尚未结束或需人工核对')
                for name in ('dispatch.json','desktop_session.json'):
                    if (other_folder/name).exists():
                        identity=json.loads((other_folder/name).read_text(encoding='utf-8'))['identity']
                        if identity_status(identity) != 'dead':
                            raise ValueError('已有导入进程/专用会话仍活动或身份不明')
            if any(j['status'] in ('queued','starting','running') for j in self.motor.jobs()):
                raise ValueError('请先等待当前电机扫描结束')
            try:
                readiness = require_configured_license_connection()
            except ValueError as exc:
                raise ValueError('现有许可服务无法连接；本次未启动 AEDT，准备记录保留。请启用已配置的许可服务后手动执行。') from exc
            python = self.motor.root/'.runtime/aedt/Scripts/python.exe'
            if not python.is_file(): raise ValueError('缺少本机 AEDT Python；尚未下发')
            state = dict(id=artifact,status='starting',created_utc=manifest['created_utc'],license_readiness=readiness)
            write_json(folder/'state.json',state)
            try:
                with (folder/'worker.log').open('x',encoding='utf-8') as log:
                    proc = subprocess.Popen([str(python),'-X','utf8',str(self.project/'tools/calibrated_motor_import_worker.py'),
                        '--folder',str(folder)],cwd=folder,stdout=log,stderr=subprocess.STDOUT,
                        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                write_json(folder/'dispatch.json',dict(identity=process_identity(proc.pid),utc=datetime.now(timezone.utc).isoformat()))
                threading.Thread(target=self._watch,args=(proc,folder),daemon=True).start()
            except Exception as exc:
                state.update(status='needs_attention',error=str(exc));write_json(folder/'state.json',state)
                raise ValueError('导入下发失败，已保留现场：'+str(exc)) from exc
            return self.get(artifact)
        finally: lease.release()

    @staticmethod
    def _watch(proc,folder):
        try: code=proc.wait(timeout=240)
        except subprocess.TimeoutExpired:
            code=None
        state=json.loads((folder/'state.json').read_text(encoding='utf-8'))
        if state['status'] not in TERMINAL:
            # Worker alone owns state.json: a late successful cleanup must not
            # race a watchdog replacing the same temporary state file.
            write_json(folder/'attention.json',dict(id=state['id'],
                error='原生导入超过240秒预算；保留进程和现场，禁止自动重试' if code is None else '导入进程退出 '+str(code)+'；请查看日志'))

    def download(self, artifact, name):
        record=self.get(artifact)
        if name not in ('manifest.json','state.json','summary.json','motor.aedt'):
            raise ValueError('未知导入文件')
        if name in ('summary.json','motor.aedt') and record['status'] != 'imported_not_solved':
            raise ValueError('原生保存核验通过后才提供此下载')
        return self.path(artifact)/name
