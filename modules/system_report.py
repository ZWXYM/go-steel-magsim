"""Read a bounded set of saved state files without refreshing or resuming jobs."""
import hashlib
import json
import os
import platform
import sys
from datetime import datetime,timezone
from pathlib import Path
from flask import Blueprint,jsonify,render_template,request

GROUPS=(('calibration','校准记录','calibration','cal_*/report.json'),
        ('native','材料原生仿真','native_material_runs','nativerun_*/state.json'),
        ('motor','原库牌号扫描','motor','scan_*/state.json'),
        ('imports','校准材料导入','calibrated_motor_native','calimport_*/state.json'),
        ('analysis','电机分析准备','calibrated_motor_analysis','bhanalysis_*/state.json'),
        ('execution','电机执行','calibrated_motor_execution','bhexec_*/state.json'),
        ('batches','校准材料批量','calibrated_motor_batches','bhbatch_*/state.json'),
        ('optimization','优化计划','optimization/plans','plan_*/state.json'))
MAX_BYTES=2*1024*1024
MAX_RECORDS=1000


def report_bytes(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf8')


def invalid_number(value):raise ValueError('非有限JSON数值：'+value)


class SystemReport:
    def __init__(self,storage,runtime,license_status):
        self.storage=Path(storage).resolve();self.runtime=runtime;self.license_status=license_status

    def _record(self,path):
        row=dict(directory_id=path.parent.name,state_file=path.relative_to(self.storage).as_posix(),readable=False,status=None,state_file_sha256=None)
        try:
            if not path.resolve().is_relative_to(self.storage):raise ValueError('状态文件不在当前任务存储中')
            with path.open('rb') as stream:raw=stream.read(MAX_BYTES+1)
            row['bytes_read']=len(raw)
            if len(raw)>MAX_BYTES:raise ValueError('状态文件超过2MiB，未读取完整文件')
            row['state_file_sha256']=hashlib.sha256(raw).hexdigest()
            data=json.loads(raw.decode('utf-8-sig'),parse_constant=invalid_number)
            if not isinstance(data,dict):raise ValueError('状态文件顶层不是对象')
            row['readable']=True
            for key in ('id','status','created_utc','updated_utc','started_utc','finished_utc','error'):
                value=data.get(key)
                if isinstance(value,str):row[key]=value[:1000]
            for key in ('items','cases'):
                if isinstance(data.get(key),list):row[key+'_count']=len(data[key])
        except (OSError,ValueError,UnicodeError) as error:row['read_error']=str(error)[:1000]
        return row

    def _group(self,key,label,folder,pattern):
        result=dict(key=key,label=label,records=[],limit_reached=False)
        try:
            base=self.storage/folder
            if not base.resolve().is_relative_to(self.storage):raise ValueError('状态目录不在当前任务存储中')
            paths=sorted(base.glob(pattern));result['limit_reached']=len(paths)>MAX_RECORDS
            result['records']=[self._record(path) for path in paths[:MAX_RECORDS]]
        except (OSError,ValueError) as error:result['read_error']=str(error)[:1000]
        result['record_count']=len(result['records']);result['readable_count']=sum(r['readable'] for r in result['records'])
        return result

    def snapshot(self,web_address=''):
        result=dict(schema='go_steel_system_report_v1',created_utc=datetime.now(timezone.utc).isoformat(),
            web_address=web_address,workspace_at_capture=os.getcwd(),storage_root=str(self.storage),
            python=dict(version=platform.python_version(),executable=sys.executable,implementation=platform.python_implementation()),
            scope='Read-only saved-state snapshot; no job refresh, resume, solver, training or GPU probe')
        for name,provider in (('runtime',self.runtime),('license_TCP',self.license_status)):
            try:
                value=provider()
                if not isinstance(value,dict):raise ValueError('状态接口未返回对象')
                if name=='license_TCP':value={k:v for k,v in value.items() if k in ('available','source','scope','reason')}
                result[name]=dict(ok=True,value=value)
            except Exception as error:result[name]=dict(ok=False,error=str(error)[:1000])
        result['record_groups']=[self._group(*spec) for spec in GROUPS]
        result['report_sha256']=hashlib.sha256(report_bytes(result)).hexdigest()
        return result


def create_system_report_routes(report):
    bp=Blueprint('system_report',__name__)
    @bp.get('/system-report')
    def page():return render_template('system_report.html')
    @bp.get('/api/workbench/system-report')
    def snapshot():return jsonify(report.snapshot(request.host_url))
    return bp
