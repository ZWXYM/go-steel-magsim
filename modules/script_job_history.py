"""Append-only script logs and versioned job metadata; history never runs jobs."""
import copy
import json
import os
import re
import threading
import uuid
from contextlib import ExitStack
from pathlib import Path

SCHEMA='script_job_history_v1'
MAX_STATE_BYTES=1024*1024
MAX_RECORDS=1000

def invalid_number(value):raise ValueError('历史记录包含非有限数值：'+value)


class ScriptJobHistory:
    def __init__(self,root,owner):
        self.root=Path(root).resolve();self.owner=copy.deepcopy(owner);self.lock=threading.RLock()

    def folder(self,job_id):
        if not isinstance(job_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',job_id):raise ValueError('作业ID无效')
        folder=self.root/job_id
        if folder.resolve()!=folder:raise ValueError('历史目录不能使用路径别名')
        return folder

    def read(self,job_id):
        folder=self.folder(job_id);path=folder/'state.json'
        if path.resolve()!=path:raise ValueError('历史状态不能使用路径别名')
        with path.open('rb') as stream:raw=stream.read(MAX_STATE_BYTES+1)
        if len(raw)>MAX_STATE_BYTES:raise ValueError('历史元数据超过1MiB')
        value=json.loads(raw.decode('utf-8-sig'),parse_constant=invalid_number)
        if not isinstance(value,dict) or value.get('schema')!=SCHEMA or value.get('id')!=job_id:raise ValueError('历史记录身份或版本无效')
        if not isinstance(value.get('owner'),dict) or not value['owner'] or not isinstance(value.get('revision'),int) or isinstance(value['revision'],bool) or not isinstance(value.get('done'),bool) or not isinstance(value.get('status'),str) or not isinstance(value.get('script'),str):raise ValueError('历史记录结构不完整')
        return value

    def save(self,snapshot):
        value=json.loads(json.dumps(snapshot,allow_nan=False));job_id=value['id'];revision=value['revision']
        if isinstance(revision,bool) or not isinstance(revision,int) or revision<1:raise ValueError('历史修订号无效')
        if value.get('owner')!=self.owner:raise ValueError('不能修改其他实例的作业历史')
        if not isinstance(value.get('done'),bool) or not isinstance(value.get('status'),str) or not isinstance(value.get('script'),str):raise ValueError('历史记录结构不完整')
        value['schema']=SCHEMA;raw=json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False).encode('utf8')
        if len(raw)>MAX_STATE_BYTES:raise ValueError('历史元数据超过1MiB')
        folder=self.folder(job_id);path=folder/'state.json'
        with self.lock:
            if path.exists():
                old=self.read(job_id)
                if old.get('owner')!=self.owner:raise ValueError('已有历史属于其他或未知实例')
                if revision<=old.get('revision',0):return False
                if old.get('done') and (not value.get('done') or old.get('status')!=value.get('status')):raise ValueError('不能重放或改写终态历史')
            folder.mkdir(parents=True,exist_ok=True)
            if value.get('request_id'):
                receipt=dict(schema='script_job_receipt_v1',request_id=value['request_id'],id=job_id,total=value.get('total',0),request_identity=value.get('request_identity'))
                receipt_path=self._receipt_path(value['request_id']);receipt_path.parent.mkdir(parents=True,exist_ok=True)
                try:
                    with receipt_path.open('x',encoding='utf8',newline='') as stream:
                        stream.write(json.dumps(receipt,ensure_ascii=False,allow_nan=False));stream.flush();os.fsync(stream.fileno())
                except FileExistsError:
                    existing=self._read_receipt(value['request_id'])
                    if existing!=receipt:raise ValueError('请求回执已绑定其他作业，未下发新任务')
            temporary=folder/('state.'+uuid.uuid4().hex+'.tmp')
            temporary.write_bytes(raw);os.replace(temporary,path)
        return True

    def open_logs(self,job_id):
        with self.lock:
            if self.read(job_id).get('owner')!=self.owner:raise ValueError('不能追加其他实例的日志')
            folder=self.folder(job_id)
            for name in ('stdout.log','stdout.raw'):
                if (folder/name).resolve()!=folder/name:raise ValueError('日志不能使用路径别名')
            with ExitStack() as stack:
                text=stack.enter_context((folder/'stdout.log').open('a',encoding='utf8',newline=''))
                raw=stack.enter_context((folder/'stdout.raw').open('ab'))
                stack.pop_all()
                return text,raw

    def log_path(self,job_id,name):
        if name not in ('stdout.log','stdout.raw'):raise ValueError('未知日志类型')
        self.read(job_id);path=self.folder(job_id)/name
        if path.resolve()!=path or not path.is_file():raise FileNotFoundError('日志不存在或路径无效')
        return path

    def rows(self):
        paths=sorted(self.root.glob('*/state.json'));rows=[]
        for path in paths[:MAX_RECORDS]:
            try:
                rows.append(self.view(path.parent.name))
            except (OSError,ValueError,UnicodeError) as error:
                rows.append(dict(id=path.parent.name,read_error=str(error),read_only_history=True,can_stop_here=False))
        rows.sort(key=lambda row:(str(row.get('queued_at') or ''),row['id']),reverse=True)
        return dict(records=rows,limit_reached=len(paths)>MAX_RECORDS,history_root=str(self.root))

    def report_snapshot(self):
        """Bounded metadata only; saved states do not assert a worker is alive."""
        paths=sorted(self.root.glob('*/state.json'));rows=[]
        for path in paths[:MAX_RECORDS]:
            row=dict(id=path.parent.name,state_file=path.relative_to(self.root).as_posix(),
                     readable=False,read_only_history=True,worker_state='not_checked',can_stop_here=False)
            try:
                value=self.read(path.parent.name)
                for key in ('script','status','queued_at','started','finished','saved_utc','journal_error','process_control_error'):
                    if isinstance(value.get(key),str):row[key]=value[key][:4096]
                row.update(readable=True,done=value['done'],revision=value['revision'])
                row['owner']={key:value['owner'][key][:4096] if isinstance(value['owner'][key],str) else value['owner'][key]
                              for key in ('id','pid','started_utc') if isinstance(value['owner'].get(key),(str,int)) and not isinstance(value['owner'][key],bool)}
                origin=value.get('workspace_origin')
                if isinstance(origin,dict):row['workspace_origin']={key:origin[key][:4096] for key in ('path','token') if isinstance(origin.get(key),str)}
            except (OSError,ValueError,UnicodeError) as error:row['read_error']=str(error)[:1000]
            rows.append(row)
        return dict(schema='script_job_report_v1',history_root=str(self.root),records=rows,
                    record_count=len(rows),readable_count=sum(row['readable'] for row in rows),
                    limit_reached=len(paths)>MAX_RECORDS,read_only_history=True,worker_state='not_checked')

    def view(self,job_id):
        value=self.read(job_id);files=[];errors={}
        for name in ('stdout.log','stdout.raw'):
            try:self.log_path(job_id,name);files.append(name)
            except FileNotFoundError:pass
            except (OSError,ValueError) as error:errors[name]=str(error)
        return dict(value,read_only_history=True,can_stop_here=False,worker_state='not_checked',log_files=files,log_errors=errors)

    def receipt(self,request_id,identity):
        path=self._receipt_path(request_id)
        if path.exists():
            receipt=self._read_receipt(request_id)
            if receipt.get('request_identity')!=list(identity):raise ValueError('原请求已绑定其他脚本或目录')
            return receipt
        matches=[row for row in self.rows()['records'] if row.get('request_id')==request_id and not row.get('read_error')]
        if len(matches)>1:raise ValueError('原请求对应多份历史，请核对记录')
        if not matches:return None
        row=matches[0]
        if row.get('request_identity')!=list(identity):raise ValueError('原请求已绑定其他脚本或目录')
        return row

    def _receipt_path(self,request_id):
        if not isinstance(request_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{12,80}',request_id):raise ValueError('请求ID无效')
        path=self.root/'_receipts'/(request_id+'.json')
        if path.resolve()!=path:raise ValueError('请求回执不能使用路径别名')
        return path

    def _read_receipt(self,request_id):
        with self._receipt_path(request_id).open('rb') as stream:raw=stream.read(16385)
        if len(raw)>16384:raise ValueError('请求回执过大')
        value=json.loads(raw.decode('utf8'),parse_constant=invalid_number)
        if not isinstance(value,dict) or value.get('schema')!='script_job_receipt_v1' or value.get('request_id')!=request_id:raise ValueError('请求回执损坏')
        self.folder(value.get('id'))
        return value
