"""Isolated V8_2 material scans, a persistent queue, and official CSV summaries."""
from __future__ import annotations

import csv
import json
import math
import os
import re
import socket
import subprocess
import threading
import uuid
from datetime import datetime,timezone
from pathlib import Path

import numpy as np
from modules.material_calibration import file_hash

GOES=re.compile(r"Name='(GOES_V3_\d{2}(?:_\d+)?)'")
PARTS=re.compile(r"\$begin 'GeometryPart'.*?\$end 'GeometryPart'",re.S)


def write_json(path,value):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    os.replace(tmp,path)


def license_config():
    value=os.environ.get('ANSYSLMD_LICENSE_FILE')
    if value:
        return value,'existing_environment'
    ini=Path(os.environ.get('ProgramFiles','C:/Program Files'))/'ANSYS Inc/Shared Files/Licensing/ansyslmd.ini'
    servers=[line.split('=',1)[1].strip() for line in ini.read_text().splitlines() if line.startswith('SERVER=')] if ini.exists() else []
    return ';'.join(servers),'existing_workstation_SERVER_setting'


def license_status():
    value,source=license_config()
    checked=[]
    for entry in value.split(';'):
        match=re.fullmatch(r'(\d+)@([^\s@]+)',entry)
        if not match:
            continue
        port,host=int(match.group(1)),match.group(2)
        try:
            with socket.create_connection((host,port),timeout=.5):
                return dict(available=True,source=source,scope='TCP connectivity only; AEDT must check out an actual license')
        except OSError:
            checked.append(entry)
    return dict(available=False,source=source,reason='许可证服务器无法连接；请启动已有许可服务或设置有效的 ANSYSLMD_LICENSE_FILE',checked_servers=checked)


def inspect_template(text,material=None):
    names,cs=[],[]
    def patch(match):
        block=match.group()
        name=GOES.search(block)
        if not name:
            return block
        names.append(name.group(1))
        coord=re.search(r'PartCoordinateSystem=(\d+)',block)
        if not coord or int(coord.group(1))<=1:
            raise ValueError('GOES 插片缺少已激活的局部坐标系')
        cs.append(int(coord.group(1)))
        if material is None:
            return block
        block,n=re.subn(r"MaterialValue='\"[^\"]*\"'",lambda _:f"MaterialValue='\"{material}\"'",block,count=1)
        if n!=1:
            raise ValueError('插片材料字段不唯一')
        return block
    result=PARTS.sub(patch,text)
    if len(names)!=48 or len(set(names))!=48 or len(set(cs))!=48:
        raise ValueError('模板必须包含 48 个不同 GOES 插片及 48 个不同局部坐标系')
    return result,dict(object_count=48,object_names=names,coordinate_system_ids=cs)


def numeric_csv(path):
    with Path(path).open(encoding='utf-8-sig',newline='') as stream:
        reader=csv.DictReader(stream)
        headers=reader.fieldnames or []
        rows=list(reader)
    if not rows:
        raise ValueError('官方 CSV 没有数据: '+str(path))
    def values(column):
        a=np.array([float(row[column]) for row in rows])
        if not np.all(np.isfinite(a)):
            raise ValueError('官方 CSV 含非有限值')
        return a
    return headers,values


def unit(column,allowed):
    match=re.search(r'\[([^]]+)\]',column)
    if not match or match.group(1) not in allowed:
        raise ValueError('CSV 单位无法识别: '+column)
    return allowed[match.group(1)]


def collect_metrics(directory,measurement='v8_2_cycle1'):
    directory=Path(directory)
    report=directory/'reports/Torque Plots.csv'
    headers,values=numeric_csv(report)
    tc=next((s for s in headers if 'Time' in s),None)
    yc=next((s for s in headers if 'Moving1.Torque' in s),None)
    if tc is None or yc is None:
        raise ValueError('缺少 Time / Moving1.Torque 官方字段')
    time_s=values(tc)*unit(tc,{'s':1,'ms':1e-3,'us':1e-6,'ns':1e-9})
    torque=values(yc)*unit(yc,{'NewtonMeter':1,'Nm':1,'mNewtonMeter':1e-3})
    periodic=measurement=='periodic_cycle3_v1'
    if measurement not in ('v8_2_cycle1','periodic_cycle3_v1'):
        raise ValueError('未知电机测量协议')
    count,stop=(91,.03) if periodic else (31,.01)
    if len(time_s)!=count or not np.allclose(time_s,np.linspace(0,stop,count),atol=1e-10,rtol=1e-8):
        raise ValueError(f'该协议需完整 0–{stop*1000:g} ms 的 {count} 点官方扫描结果')
    raw_time,raw_torque=time_s.copy(),torque.copy()
    start=60 if periodic else 0
    time_s,torque=time_s[start:],torque[start:]
    def loss(name,skip_first):
        headers,values=numeric_csv(directory/'maxwell_reports'/f'{name}.csv')
        col=next((s for s in headers if name in s and 'Time' not in s),None)
        if col is None:
            raise ValueError('缺少官方损耗字段 '+name)
        t=next((s for s in headers if 'Time' in s),None)
        if t is None:
            raise ValueError('缺少损耗 Time 字段')
        ts=values(t)*unit(t,{'s':1,'ms':1e-3,'us':1e-6,'ns':1e-9})
        if len(ts)!=len(raw_time) or not np.allclose(ts,raw_time,atol=1e-10,rtol=1e-8):
            raise ValueError('转矩与损耗的时间网格不同')
        # Preserve V8_2: drop the first core-loss row, keep all copper rows.
        return values(col)*unit(col,{'W':1,'mW':1e-3,'kW':1e3})
    mean=float(torque.mean())
    raw_fe,raw_cu=loss('CoreLoss',True),loss('StrandedLoss',False)
    fe,cu=float(raw_fe[start+1:].mean()),float(raw_cu[start:].mean())
    ripple=float((torque.max()-torque.min())/mean*100) if mean!=0 else None
    pout=mean*3000*2*math.pi/60
    accepted=mean>0 and torque.min()>0 and torque.max()<10 and ripple is not None and ripple<100 and fe>=0 and cu>=0
    stability=None
    if periodic:
        previous=raw_torque[30:61]
        previous_fe=float(raw_fe[31:61].mean())
        peak=float(np.max(np.abs(torque-previous)))
        mean_change=abs(mean-float(previous.mean()))/max(abs(mean),1e-12)
        fe_change=abs(fe-previous_fe)/max(abs(fe),1e-12)
        stability=dict(cycles_compared=[2,3],max_phase_torque_delta_Nm=peak,
            relative_mean_torque_change=mean_change,relative_core_loss_change=fe_change,
            thresholds=dict(phase_torque_Nm=.1,mean_torque_relative=.02,core_loss_relative=.05),
            passed=bool(peak<=.1 and mean_change<=.02 and fe_change<=.05))
        accepted=accepted and stability['passed']
    result=dict(T_avg_Nm=mean,T_min_Nm=float(torque.min()),T_max_Nm=float(torque.max()),
        K_T_ripple_pct=ripple,P_Fe_W=fe,P_Cu_W=cu,P_out_W=pout,
        eta_estimate_pct=100*pout/(pout+fe+cu) if pout>0 and fe>=0 and cu>=0 else None,
        accepted=bool(accepted),n_samples=31,source_n_samples=count,time_ms=(time_s*1000).tolist(),torque_Nm=torque.tolist(),
        source='official_CSV_same_case_same_time_grid',metric_protocol=measurement,
        selected_window_ms=[float(time_s[0]*1000),float(time_s[-1]*1000)],periodic_stability=stability,
        efficiency_scope='Pout/(Pout+PFe+PCu), mechanical/stray losses excluded',
        csv_hashes={str(p.relative_to(directory)):file_hash(p) for p in directory.rglob('*.csv')})
    return result


class MotorWorkbench:
    def __init__(self,root,storage,project):
        self.root=Path(root).resolve()
        self.storage=Path(storage).resolve()
        self.project=Path(project).resolve()
        self.storage.mkdir(parents=True,exist_ok=True)
        self.thread_lock=threading.Lock()
        self.template=self.root/'motor/v8_2/cases/nippon_steel_23zh90/motorcad_full_v8_1_nippon_steel_23zh90.aedt'

    def catalog(self):
        path=self.root/'motor/v8_1/manifest.json'
        if not path.exists():
            return dict(available=False,materials=[],reason='当前公开副本未附带本机电机模型；请配置 GO_STEEL_THESIS_ROOT')
        manifest=json.loads(path.read_text(encoding='utf-8'))
        items=manifest['cases'] if isinstance(manifest,dict) else manifest
        materials=sorted({r['material'] for r in items})
        text=self.template.read_text(encoding='utf-8')
        _,quality=inspect_template(text)
        return dict(available=True,materials=materials,template='V8_2 23ZH90 object-CS clean template',
            template_path=str(self.template),template_sha256=file_hash(self.template),template_quality=quality,
            aedt_python_available=(self.root/'.runtime/aedt/Scripts/python.exe').is_file(),license=license_status())

    def path(self,job_id):
        if not isinstance(job_id,str) or not re.fullmatch(r'scan_[0-9a-f]{12}',job_id):
            raise ValueError('无效扫描任务 ID')
        return self.storage/job_id

    def prepare(self,materials,cores=4,measurement='periodic_cycle3_v1'):
        cat=self.catalog()
        if not cat['available']:
            raise ValueError(cat['reason'])
        if not isinstance(materials,list) or not materials or len(set(materials))!=len(materials) or not set(materials)<=set(cat['materials']):
            raise ValueError('请选择目录中不同的材料牌号')
        if not isinstance(cores,int) or not 1<=cores<=32:
            raise ValueError('并行核数需为 1–32')
        if measurement not in ('v8_2_cycle1','periodic_cycle3_v1'):
            raise ValueError('未知电机测量协议')
        text=self.template.read_text(encoding='utf-8')
        if file_hash(self.template)!=cat['template_sha256']:
            raise ValueError('模板在任务生成期间改变')
        job_id='scan_'+uuid.uuid4().hex[:12]
        path=self.path(job_id)
        path.mkdir()
        cases=[]
        for i,material in enumerate(materials,1):
            patched,quality=inspect_template(text,material)
            case=path/f'case_{i:02d}'
            case.mkdir()
            p=case/'motor.aedt'
            p.write_bytes(patched.encode('utf-8'))
            cases.append(dict(id=case.name,material=material,project=p.relative_to(path).as_posix(),
                project_sha256=file_hash(p),quality=quality,status='prepared'))
        manifest=dict(id=job_id,created_utc=datetime.now(timezone.utc).isoformat(),status='prepared',
            template_sha256=cat['template_sha256'],template_source=str(self.template),
            model_version='V8_2_object_CS_template',design='Motor-CAD 2',setup='Setup1',cores=cores,
            aedt_version='2025.1',cases=cases,worker_sha256=file_hash(self.project/'tools/motor_scan_worker.py'),
            support_source_sha256={name:file_hash(self.project/name) for name in ('modules/motor_workbench.py','modules/material_calibration.py')},
            measurement_protocol=measurement,torque_cycles=3 if measurement=='periodic_cycle3_v1' else 1,
            result_reuse=False,existing_results_deleted=False,new_desktop=True,
            material_semantics='AEDT built-in material properties; local CS activation alone does not prove tensor RD/TD constitutive anisotropy')
        write_json(path/'manifest.json',manifest)
        write_json(path/'state.json',dict(id=job_id,status='prepared',measurement_protocol=measurement,created_utc=manifest['created_utc'],cases=cases,completed=0,total=len(cases)))
        return self.state(job_id)

    def state(self,job_id):
        path=self.path(job_id)
        value=json.loads((path/'state.json').read_text(encoding='utf-8'))
        if (path/'worker.log').exists():
            value['log_tail']=(path/'worker.log').read_text(encoding='utf-8',errors='replace')[-5000:]
        return value

    def jobs(self):
        return sorted([self.state(p.name) for p in self.storage.glob('scan_*') if (p/'state.json').exists()],key=lambda j:j.get('created_utc',''),reverse=True)

    def submit(self,job_id):
        python=self.root/'.runtime/aedt/Scripts/python.exe'
        if not python.is_file():
            raise ValueError('本机 AEDT Python 环境不可用')
        if not license_status()['available']:
            raise ValueError(license_status()['reason'])
        with self.thread_lock:
            state=self.state(job_id)
            if state['status'] not in ('prepared',):
                raise ValueError('该任务已提交；重复试验请创建新的扫描')
            state['status']='queued'
            state['queued_utc']=datetime.now(timezone.utc).isoformat()
            write_json(self.path(job_id)/'state.json',state)
        threading.Thread(target=self._drain,daemon=True).start()
        return self.state(job_id)

    def _drain(self):
        # Process-wide and OS-level queue ownership; never duplicate workers.
        lock=self.storage/'queue.lock'
        try:
            fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
        except FileExistsError:
            return
        try:
            with os.fdopen(fd,'w') as stream:
                json.dump(dict(pid=os.getpid()),stream)
            while True:
                with self.thread_lock:
                    queued=sorted([j for j in self.jobs() if j['status']=='queued'],key=lambda j:j.get('queued_utc',''))
                    if not queued:
                        break
                    job_id=queued[0]['id']
                    state=queued[0]
                    state['status']='starting'
                    write_json(self.path(job_id)/'state.json',state)
                path=self.path(job_id)
                cmd=[str(self.root/'.runtime/aedt/Scripts/python.exe'),'-X','utf8',
                    str(self.project/'tools/motor_scan_worker.py'),'--job-dir',str(path)]
                try:
                    with (path/'worker.log').open('x',encoding='utf-8') as log:
                        p=subprocess.run(cmd,cwd=path,stdout=log,stderr=subprocess.STDOUT)
                    state=self.state(job_id)
                    if state['status'] not in ('completed','completed_with_failures'):
                        state['status']='failed'
                        state['error']=f'worker exit {p.returncode}; 请查看日志'
                        write_json(path/'state.json',state)
                except Exception as exc:
                    state=self.state(job_id)
                    state.update(status='failed',error=str(exc))
                    write_json(path/'state.json',state)
        finally:
            # A submit racing with the empty-queue check must get a new owner.
            with self.thread_lock:
                lock.unlink()
                pending=any(j['status']=='queued' for j in self.jobs())
            if pending:
                threading.Thread(target=self._drain,daemon=True).start()

    def archived_results(self):
        result=[]
        for case in sorted((self.root/'motor/v8_2/cases').glob('*')):
            report=case/'final_resolved_official_report.json'
            if not report.exists():
                continue
            record=json.loads(report.read_text(encoding='utf-8'))
            row=dict(case=case.name,version='V8_2',reported_metrics=record.get('thesis_metrics',{}),
                report_sha256=file_hash(report),csv_verified=False)
            try:
                # Never fall back to a different report prefix or another case.
                pair=case/'final_resolved_reports/Torque Plots.csv'
                if not pair.exists():
                    raise ValueError('该 final 版本官方 CSV 缺失；保留历史摘要，不参与当前可核验排名')
                # Same parser without copying anything back into the archive.
                import tempfile,shutil
                with tempfile.TemporaryDirectory() as tmp:
                    p=Path(tmp)
                    shutil.copytree(case/'final_resolved_reports',p/'reports')
                    shutil.copytree(case/'final_resolved_maxwell_reports',p/'maxwell_reports')
                    computed=collect_metrics(p)
                expected=record['thesis_metrics']
                for key,tol in [('T_avg_Nm',.00006),('K_T_ripple_pct',.006),('P_Fe_W',.0006),('P_Cu_W',.0006)]:
                    if abs(computed[key]-expected[key])>tol:
                        raise ValueError('官方 CSV 与该版本摘要不符: '+key)
                row.update(csv_verified=True,metrics=computed)
            except Exception as exc:
                row['reason']=str(exc)
            result.append(row)
        return result
