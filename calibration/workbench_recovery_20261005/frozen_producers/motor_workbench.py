"""Isolated V8_2 material scans, a persistent queue, and official CSV summaries."""
from __future__ import annotations

import csv
import copy
import json
import math
import os
import re
import socket
import subprocess
import threading
import uuid
import zipfile
from datetime import datetime,timezone
from pathlib import Path

import numpy as np
from modules.material_calibration import file_hash
from modules.motor_queue import QueueLease,process_identity,identity_status,active_processes

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


def acceptance_reason(result):
    if result.get('accepted'):
        return '通过'
    if result.get('error'):
        return str(result['error'])
    reasons=[]
    periodic=result.get('periodic_stability')
    if periodic and not periodic['passed']:
        limits=periodic['thresholds']
        if periodic['max_phase_torque_delta_Nm']>limits['phase_torque_Nm']:
            reasons.append(f"周期波形差 {periodic['max_phase_torque_delta_Nm']:.6f} N·m > {limits['phase_torque_Nm']}")
        if periodic['relative_mean_torque_change']>limits['mean_torque_relative']:
            reasons.append('平均转矩周期差超过门限')
        if periodic['relative_core_loss_change']>limits['core_loss_relative']:
            reasons.append('铁损周期差超过门限')
    if result.get('T_avg_Nm',1)<=0 or result.get('T_min_Nm',1)<=0:
        reasons.append('转矩存在负值或零值')
    if result.get('T_max_Nm',0)>=10:
        reasons.append('最大转矩达到或超过 10 N·m')
    if result.get('K_T_ripple_pct',0) is not None and result.get('K_T_ripple_pct',0)>=100:
        reasons.append('脉动达到或超过 100%')
    if result.get('P_Fe_W',0)<0 or result.get('P_Cu_W',0)<0:
        reasons.append('损耗为负值')
    return '；'.join(reasons) or '未通过既定数值门限'


def write_scan_summary(path,manifest,results,source_path=None):
    """Write new summary artifacts only. Raw reports/results never change."""
    path=Path(path)
    source_path=Path(source_path or path)
    rows=[]
    for original in results:
        row=dict(original,acceptance_reason=acceptance_reason(original),
                 measurement_protocol=manifest.get('measurement_protocol','v8_2_cycle1'))
        periodic=row.get('periodic_stability') or {}
        row.update(phase_torque_delta_Nm=periodic.get('max_phase_torque_delta_Nm'),
            mean_torque_relative_change=periodic.get('relative_mean_torque_change'),
            core_loss_relative_change=periodic.get('relative_core_loss_change'))
        rows.append(row)
    valid=[r for r in rows if r.get('accepted')]
    summary=dict(id=manifest['id'],model_version=manifest['model_version'],template_sha256=manifest['template_sha256'],
        measurement_protocol=manifest.get('measurement_protocol','v8_2_cycle1'),
        results=rows,accepted=len(valid),failed_or_rejected=len(rows)-len(valid),
        best_torque=max(valid,key=lambda r:r['T_avg_Nm'])['material'] if valid else None,
        minimum_core_loss=min(valid,key=lambda r:r['P_Fe_W'])['material'] if valid else None,
        bundle_file='reports_bundle.zip',parent_job=manifest.get('parent_job'))
    fields=['case_id','material','measurement_protocol','accepted','acceptance_reason',
        'T_avg_Nm','K_T_ripple_pct','P_Fe_W','P_Cu_W','eta_estimate_pct','source_n_samples',
        'phase_torque_delta_Nm','mean_torque_relative_change','core_loss_relative_change','error']
    with (path/'summary.csv').open('x',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=fields,extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)
    with (path/'summary.json').open('x',encoding='utf-8') as stream:
        json.dump(summary,stream,ensure_ascii=False,indent=2,allow_nan=False)
    lines=['# 多牌号扫描结果',f"任务：{manifest['id']}；模型：{manifest['model_version']}",
        f"测量口径：{summary['measurement_protocol']}；通过 {len(valid)} / {len(rows)}",
        f"通过项中转矩最高：{summary['best_torque'] or '暂无'}；铁损最低：{summary['minimum_core_loss'] or '暂无'}",
        '全部失败/拒绝项均保留；效率为 Pout/(Pout+PFe+PCu) 估计，未含机械/杂散损耗。',
        '', '| 材料 | 验收原因 | 转矩 N·m | 脉动 % | 铁损 W |', '|---|---|---:|---:|---:|']
    for row in rows:
        reason=row['acceptance_reason'].replace('|','/').replace('\n',' ')
        lines.append(f"| {row['material']} | {reason} | {row.get('T_avg_Nm','')} | {row.get('K_T_ripple_pct','')} | {row.get('P_Fe_W','')} |")
    with (path/'summary.md').open('x',encoding='utf-8') as stream:
        stream.write('\n'.join(lines))
    files=[(source_path/'manifest.json','manifest.json')]
    files.extend((path/name,name) for name in ('summary.csv','summary.json','summary.md'))
    for case in manifest['cases']:
        folder=source_path/case['id']
        names=[folder/name for name in ('result.json','model_validation.json','desktop_session.json','failure.json') if (folder/name).is_file()]
        names.extend(folder.glob('reports/*.csv'))
        names.extend(folder.glob('maxwell_reports/*.csv'))
        files.extend((file,file.relative_to(source_path).as_posix()) for file in names)
    with zipfile.ZipFile(path/'reports_bundle.zip','x',zipfile.ZIP_DEFLATED) as bundle:
        for file,name in files:
            bundle.write(file,name)
    return summary


class MotorWorkbench:
    def __init__(self,root,storage,project):
        self.root=Path(root).resolve()
        self.storage=Path(storage).resolve()
        self.project=Path(project).resolve()
        self.storage.mkdir(parents=True,exist_ok=True)
        self.thread_lock=threading.Lock()
        self.monitor_stop=threading.Event()
        self.monitor_started=False
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

    def prepare(self,materials,cores=4,measurement='periodic_cycle3_v1',parent_job=None):
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
            support_source_sha256={name:file_hash(self.project/name) for name in ('modules/motor_workbench.py','modules/material_calibration.py','modules/motor_queue.py')},
            measurement_protocol=measurement,torque_cycles=3 if measurement=='periodic_cycle3_v1' else 1,
            result_reuse=False,existing_results_deleted=False,new_desktop=True,
            material_semantics='AEDT built-in material properties; local CS activation alone does not prove tensor RD/TD constitutive anisotropy')
        if parent_job:
            manifest.update(parent_job=parent_job,parent_manifest_sha256=file_hash(self.path(parent_job)/'manifest.json'),
                continuation_scope='unattempted_materials_only; no native result reuse')
        write_json(path/'manifest.json',manifest)
        write_json(path/'state.json',dict(id=job_id,status='prepared',measurement_protocol=measurement,created_utc=manifest['created_utc'],cases=cases,completed=0,total=len(cases),parent_job=parent_job))
        return self.state(job_id)

    def state(self,job_id):
        path=self.path(job_id)
        value=json.loads((path/'state.json').read_text(encoding='utf-8'))
        if (path/'worker.log').exists():
            value['log_tail']=(path/'worker.log').read_text(encoding='utf-8',errors='replace')[-5000:]
        return value

    def jobs(self):
        return sorted([self.state(p.name) for p in self.storage.glob('scan_*') if (p/'state.json').exists()],key=lambda j:j.get('created_utc',''),reverse=True)

    def export_summary(self,job_id):
        path=self.path(job_id)
        if self.state(job_id)['status'] not in ('completed','completed_with_failures'):
            raise ValueError('任务完成后才能生成完整汇总包')
        manifest=json.loads((path/'manifest.json').read_text(encoding='utf-8'))
        results=[]
        aliases={}
        for case in manifest['cases']:
            folder=path/case['id']
            if (folder/'result.json').exists():
                result=json.loads((folder/'result.json').read_text(encoding='utf-8'))
                protocol=manifest.get('measurement_protocol','v8_2_cycle1')
                legacy_alias='measurement_protocol' not in manifest and protocol=='v8_2_cycle1' and result['metric_protocol']=='v8_2_torque_all_core_drop_first_copper_all_v1'
                if result['metric_protocol']!=protocol and not legacy_alias:
                    raise ValueError('结果与任务的测量口径不同')
                if legacy_alias:
                    aliases[case['id']]=dict(stored=result['metric_protocol'],canonical=protocol,
                        verification='same full 31-point time grid; recomputed official torque/core/copper metrics')
                for name,digest in result['csv_hashes'].items():
                    source=(folder/name).resolve()
                    if not source.is_relative_to(folder.resolve()) or file_hash(source)!=digest:
                        raise ValueError('官方 CSV 路径/哈希改变，停止汇总')
                computed=collect_metrics(folder,protocol)
                for key in ('T_avg_Nm','T_min_Nm','T_max_Nm','P_Fe_W','P_Cu_W','K_T_ripple_pct'):
                    if result[key]!=computed[key] and (result[key] is None or computed[key] is None or abs(result[key]-computed[key])>1e-10):
                        raise ValueError('官方 CSV 与保存指标不符: '+key)
                if result['accepted']!=computed['accepted']:
                    raise ValueError('保存验收与当前固定口径不符')
            else:
                result=json.loads((folder/'failure.json').read_text(encoding='utf-8'))
            results.append(result)
        export_id='summary_'+uuid.uuid4().hex[:12]
        output=path/'exports'/export_id
        output.mkdir(parents=True)
        summary=write_scan_summary(output,manifest,results,path)
        write_json(output/'provenance.json',dict(source_job=job_id,source_manifest_sha256=file_hash(path/'manifest.json'),
            result_hashes={c['id']:file_hash(path/c['id']/('result.json' if (path/c['id']/'result.json').exists() else 'failure.json')) for c in manifest['cases']},
            derived_summary=True,raw_results_changed=False,new_native_solves=0,
            verified_legacy_protocol_aliases=aliases,
            summary_producer_sha256=file_hash(Path(__file__))))
        return dict(id=export_id,job_id=job_id,summary=summary,
            files={name:(output/name).relative_to(path).as_posix() for name in ('summary.csv','summary.md','reports_bundle.zip','provenance.json')})

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
        self.start_queue_monitor()
        return self.state(job_id)

    def _ownership_obstacles(self):
        obstacles=active_processes(self.storage,self.jobs())
        lock=self.storage/'queue.lock'
        if lock.exists():
            try:
                owner=json.loads(lock.read_text(encoding='utf-8'))
                status=identity_status(owner)
            except (OSError,ValueError):
                status='unknown'
            if status!='dead':
                obstacles.append(dict(record='queue_owner',status=status))
        return obstacles

    def queue_status(self):
        obstacles=self._ownership_obstacles()
        jobs=self.jobs()
        return dict(can_recover=not obstacles,active_ownership=obstacles,
            queued=sum(j['status']=='queued' for j in jobs),
            needs_attention=sum(j['status']=='needs_attention' for j in jobs))

    def start_queue_monitor(self):
        with self.thread_lock:
            if self.monitor_started:
                return
            self.monitor_started=True
        threading.Thread(target=self._watch_queue,daemon=True).start()

    def _watch_queue(self):
        try:
            while not self.monitor_stop.is_set():
                if not (self.storage/'queue.lock').exists() and not any(j['status'] in ('queued','starting','running') for j in self.jobs()):
                    break
                self.recover_queue()
                if self.monitor_stop.wait(15):
                    break
        finally:
            with self.thread_lock:
                self.monitor_started=False

    def _archive_lock(self):
        lock=self.storage/'queue.lock'
        if lock.exists():
            folder=self.storage/'queue_history'
            folder.mkdir(exist_ok=True)
            lock.rename(folder/('owner_'+uuid.uuid4().hex+'.json'))

    def _validate_queued(self,job_id):
        path=self.path(job_id)
        manifest=json.loads((path/'manifest.json').read_text(encoding='utf-8'))
        if manifest['id']!=job_id or file_hash(self.project/'tools/motor_scan_worker.py')!=manifest['worker_sha256']:
            raise ValueError('任务冻结程序已改变；请生成新任务，旧任务保留')
        for name,digest in manifest.get('support_source_sha256',{}).items():
            if name not in ('modules/motor_workbench.py','modules/material_calibration.py','modules/motor_queue.py') or file_hash(self.project/name)!=digest:
                raise ValueError('任务冻结指标/队列程序已改变；请生成新任务')
        if (path/'worker.log').exists() or (path/'dispatch.json').exists():
            raise ValueError('任务已有执行痕迹；禁止重新启动原任务')
        for case in manifest['cases']:
            project=(path/case['project']).resolve()
            if not project.is_relative_to(path.resolve()) or file_hash(project)!=case['project_sha256']:
                raise ValueError('扫描副本路径或哈希改变')
            if project.with_suffix('.aedtresults').exists() or Path(str(project)+'.lock').exists():
                raise ValueError('扫描副本已有原生结果/锁；保留现场')

    def _recover_under_lease(self):
        # Must be called only with the OS lease held and no live/unknown owner.
        record=dict(id='recovery_'+uuid.uuid4().hex[:12],utc=datetime.now(timezone.utc).isoformat(),jobs=[])
        updates=[]
        had_lock=(self.storage/'queue.lock').exists()
        self._archive_lock()
        for state in self.jobs():
            reason=None
            if state['status'] in ('starting','running'):
                reason='服务或求解进程中断；已保留现场，仅可新建未执行牌号任务'
            elif state['status']=='queued':
                try:
                    self._validate_queued(state['id'])
                except (ValueError,KeyError,OSError) as exc:
                    reason=str(exc)
            if reason:
                record['jobs'].append(dict(previous_state=copy.deepcopy(state),reason=reason))
                updates.append((state,reason))
        if record['jobs'] or had_lock:
            write_json(self.storage/'queue_history'/(record['id']+'.json'),record)
        else:
            record['id']=None
        for state,reason in updates:
            state.update(status='needs_attention',error=reason,current=None,recovery_record=record['id'])
            for case in state.get('cases',[]):
                if case['status']=='running':
                    case['status']='interrupted'
            write_json(self.path(state['id'])/'state.json',state)
        return record

    def recover_queue(self):
        lease=QueueLease(self.storage/'queue.guard')
        if not lease.acquire():
            return dict(status='busy',reason='队列仍由活动进程持有，保持等待')
        try:
            obstacles=self._ownership_obstacles()
            if obstacles:
                return dict(status='busy',reason='检测到活动或无法确认归属的进程，未改动任务',active_ownership=obstacles)
            with self.thread_lock:
                record=self._recover_under_lease()
                pending=any(j['status']=='queued' for j in self.jobs())
        finally:
            lease.release()
        if pending:
            threading.Thread(target=self._drain,daemon=True).start()
        return dict(status='recovered',record=record['id'],needs_attention=len(record['jobs']),queued_resumed=pending)

    def continue_unattempted(self,job_id):
        with self.thread_lock:
            state=self.state(job_id)
            if state['status']!='needs_attention':
                raise ValueError('仅可继续经过恢复检查的中断任务')
            if self._ownership_obstacles():
                raise ValueError('仍有活动或无法确认归属的进程，不能继续')
            manifest=json.loads((self.path(job_id)/'manifest.json').read_text(encoding='utf-8'))
            if file_hash(self.template)!=manifest['template_sha256']:
                raise ValueError('原模板已改变，不能自动延续原试验')
            prior=[json.loads(p.read_text(encoding='utf-8')) for p in self.storage.glob('scan_*/manifest.json')]
            if state.get('continuation_job') or any(m.get('parent_job')==job_id for m in prior):
                raise ValueError('已生成后续任务，请查看该任务，避免重复扫描')
            materials=[]
            for case in state.get('cases',[]):
                folder=self.path(job_id)/case['id']
                project=folder/'motor.aedt'
                if case['status']=='prepared' and not any((folder/name).exists() for name in ('result.json','failure.json','desktop_session.json','motor.aedtresults','motor.aedt.lock')):
                    expected=next(c for c in manifest['cases'] if c['id']==case['id'])
                    if file_hash(project)!=expected['project_sha256']:
                        raise ValueError('未执行副本已改变，不能自动继续')
                    materials.append(case['material'])
            if not materials:
                raise ValueError('没有未经尝试的牌号；已尝试项保留现场，重试需另建试验')
            child=self.prepare(materials,manifest['cores'],manifest.get('measurement_protocol','v8_2_cycle1'),parent_job=job_id)
            state['continuation_job']=child['id']
            write_json(self.path(job_id)/'state.json',state)
            return child

    def _drain(self):
        lease=QueueLease(self.storage/'queue.guard')
        if not lease.acquire():
            return
        owner_token=uuid.uuid4().hex
        reschedule=True
        owned=False
        try:
            if self._ownership_obstacles():
                reschedule=False
                return
            with self.thread_lock:
                self._recover_under_lease()
            if not license_status()['available']:
                reschedule=False
                return
            write_json(self.storage/'queue.lock',dict(process_identity(),token=owner_token))
            owned=True
            while True:
                with self.thread_lock:
                    queued=sorted([j for j in self.jobs() if j['status']=='queued'],key=lambda j:j.get('queued_utc',''))
                    if not queued:
                        break
                    job_id=queued[0]['id']
                    state=queued[0]
                    if not license_status()['available']:
                        reschedule=False
                        break
                    try:
                        self._validate_queued(job_id)
                    except (ValueError,KeyError,OSError) as exc:
                        state.update(status='needs_attention',error=str(exc))
                        write_json(self.path(job_id)/'state.json',state)
                        continue
                    state['status']='starting'
                    write_json(self.path(job_id)/'state.json',state)
                path=self.path(job_id)
                cmd=[str(self.root/'.runtime/aedt/Scripts/python.exe'),'-X','utf8',
                    str(self.project/'tools/motor_scan_worker.py'),'--job-dir',str(path)]
                try:
                    with (path/'worker.log').open('x',encoding='utf-8') as log:
                        p=subprocess.Popen(cmd,cwd=path,stdout=log,stderr=subprocess.STDOUT)
                        try:
                            identity=process_identity(p.pid)
                        except Exception:
                            identity=dict(pid=p.pid)
                        write_json(path/'dispatch.json',dict(identity=identity,job_id=job_id,
                            utc=datetime.now(timezone.utc).isoformat(),controller=process_identity()))
                        returncode=p.wait()
                    state=self.state(job_id)
                    if state['status'] not in ('completed','completed_with_failures','needs_attention'):
                        state['status']='failed'
                        state['error']=f'worker exit {returncode}; 请查看日志'
                        write_json(path/'state.json',state)
                    if active_processes(self.storage,self.jobs()):
                        reschedule=False
                        break
                except Exception as exc:
                    state=self.state(job_id)
                    state.update(status='failed',error=str(exc))
                    write_json(path/'state.json',state)
        finally:
            try:
                with self.thread_lock:
                    if owned:
                        self._archive_lock()
                    pending=any(j['status']=='queued' for j in self.jobs())
            finally:
                lease.release()
            if pending and reschedule:
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
