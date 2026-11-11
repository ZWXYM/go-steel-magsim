"""Persistent calibrated-material batches; CPU preparation and explicit serial start."""
import csv
import io
import json
import os
import re
import subprocess
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from modules.material_library import sha
from modules.material_motor_workflow import LINEAGE, same_source
from modules.motor_queue import DispatchWindow, QueueLease, process_identity, identity_status
from modules.motor_workbench import write_json
from modules.calibrated_motor_native import SUPPORT as IMPORT_SUPPORT
from modules.calibrated_motor_execution import SUPPORT as EXEC_SUPPORT,execution_budget,execution_support
from modules.calibrated_motor_analysis import protocol,DEFAULT_PROTOCOL
from modules.catalog_motor_loss import PROTOCOL as LOSS_PROTOCOL
from modules.material_loss_reference import GRADES

VERSION = 'calibrated_material_batch_v1'
TERMINAL = ('completed', 'completed_with_failures', 'failed', 'needs_attention', 'paused')
SUPPORT = tuple(sorted(set(IMPORT_SUPPORT + EXEC_SUPPORT + (
    'modules/material_motor_batches.py', 'modules/material_motor_chain.py',
    'modules/calibrated_motor_preparation.py', 'modules/material_library.py',
    'tools/material_motor_batch_worker.py'))))


def read(path): return json.loads(Path(path).read_text(encoding='utf8'))


def expected_budget(items, cores, measurement='v8_2_cycle1'):
    pending=[r for r in items if r['initial_status'] not in ('result_available','needs_review')]
    imports=sum(r['records']['native_import']['status']=='ready' for r in pending)
    solves=sum('execution' not in r['records'] or r['records']['execution']['status']=='prepared' for r in pending)
    motor_seconds=execution_budget(measurement)['wall_seconds']
    return dict(max_new_imports=imports,max_new_motor_solves=solves,
        max_new_native_sessions=imports+solves,cores=cores,gpus=0,
        per_import_seconds=240,per_motor_seconds=motor_seconds,wall_seconds=imports*260+solves*(motor_seconds+20)+120)


def workspace_chain(project, root, storage):
    from modules.calibration_workbench import CalibrationWorkbench
    from modules.material_library import MaterialLibrary
    from modules.motor_workbench import MotorWorkbench
    from modules.calibrated_motor_preparation import CalibratedMotorPreparation
    from modules.calibrated_motor_native import CalibratedMotorNative
    from modules.calibrated_motor_analysis import CalibratedMotorAnalysis
    from modules.calibrated_motor_execution import CalibratedMotorExecution
    from modules.motor_waveforms import MotorWaveforms
    from modules.material_motor_chain import MaterialMotorChain
    storage = Path(storage)
    library = MaterialLibrary(CalibrationWorkbench(root, storage/'calibration'))
    motor = MotorWorkbench(root, storage/'motor', project)
    prep = CalibratedMotorPreparation(library, motor, storage/'calibrated_motor')
    native = CalibratedMotorNative(prep, motor, storage/'calibrated_motor_native')
    analysis = CalibratedMotorAnalysis(native, MotorWaveforms(motor), storage/'calibrated_motor_analysis')
    execution = CalibratedMotorExecution(analysis, motor, storage/'calibrated_motor_execution')
    return MaterialMotorChain(library, prep, native, analysis, execution)


class MaterialMotorBatches:
    def __init__(self, chain, storage):
        self.chain = chain
        self.storage = Path(storage).resolve(); self.storage.mkdir(parents=True, exist_ok=True)
        self.project = chain.preparation.motor.project

    def path(self, artifact):
        if not isinstance(artifact, str) or not re.fullmatch(r'bhbatch_[0-9a-f]{12}', artifact):
            raise ValueError('无效校准材料批量计划')
        return self.storage/artifact

    def producers(self, measurement=DEFAULT_PROTOCOL):
        names = sorted(set(SUPPORT + execution_support(measurement)))
        return {name: sha((self.project/name).read_bytes()) for name in names}

    def prepare(self, data):
        if (not isinstance(data, dict) or set(data)-{'materials','cores','measurement_protocol'}
                or not isinstance(data.get('materials'), list) or not 1 <= len(data['materials']) <= 8):
            raise ValueError('请选择1–8种保存材料')
        materials = data['materials']
        if any(not isinstance(r, dict) or set(r) != {'calibration','key'} for r in materials):
            raise ValueError('批量材料需包含校准记录和材料键')
        if len({(r['calibration'],r['key']) for r in materials}) != len(materials):
            raise ValueError('同一材料不能重复选择')
        measurement = data.get('measurement_protocol', DEFAULT_PROTOCOL)
        catalog = measurement == LOSS_PROTOCOL
        if catalog and any(row['key'] not in {'fit:'+grade for grade in GRADES} for row in materials):
            raise ValueError('目录铁损批量仅支持四个已标定牌号的拟合材料')
        lease = QueueLease(self.storage/'prepare.lease')
        if not lease.acquire(): raise ValueError('另一批材料正在准备，请稍后重开')
        try:
            items = []
            responses = self.chain.ensure_many([
                dict(selection, cores=data.get('cores',2), measurement_protocol=measurement) for selection in materials])
            for index, (selection, response) in enumerate(zip(materials, responses, strict=True), 1):
                prep = self.chain.preparation.get(response['records']['preparation']['id'])
                item = dict(id=f'item_{index:02d}', selection=selection,
                    source={**{k:response[k] for k in LINEAGE},
                        'AMAT_sha256':prep['files']['package/material.amat'],
                        'metadata_sha256':prep['files']['package/metadata.json'],
                        'template_sha256':prep['template_sha256']},
                    records=response['records'], initial_status=response['status'])
                if catalog:
                    plan = self.chain.analysis.get(response['records']['analysis_plan']['id'])
                    item['source']['catalog_loss_reference_sha256'] = plan['catalog_loss']['reference_sha256']
                items.append(item)
            if catalog and len({item['source']['catalog_loss_reference_sha256'] for item in items}) != 1:
                raise ValueError('所选目录铁损分析计划使用不同目录快照，请统一来源后准备批量')
            producers = self.producers(measurement)
            identity = dict(materials=[dict(selection=r['selection'],source=r['source']) for r in items],
                cores=response['cores'], measurement_protocol=response['measurement_protocol'],producer_hashes=producers)
            digest = sha(json.dumps(identity,sort_keys=True,separators=(',',':')).encode())
            for row in self.history():
                if row.get('idempotency_sha256') != digest:
                    continue
                old = self._record_or_error(row['id'])
                if old.get('idempotency_sha256') == digest:
                    old_executions = [item['records'].get('execution', {}).get('id') for item in old['items']]
                    new_executions = [item['records'].get('execution', {}).get('id') for item in items]
                    if old.get('interruption_recovery') and old_executions != new_executions:
                        continue  # An explicitly prepared replacement has a different case directory.
                    if old['status'] == 'completed_with_failures' and any(
                            not prior['result_available'] and old_id != new_id
                            and current['records'].get('execution', {}).get('status') == 'completed'
                            for prior, current, old_id, new_id in zip(old['items'], items, old_executions, new_executions)):
                        continue  # Keep the historical batch and bind the newly verified result in a fresh batch.
                    return dict(old, existing_batch_reused=True)
            artifact = 'bhbatch_'+uuid.uuid4().hex[:12]; folder = self.path(artifact); folder.mkdir()
            manifest = dict(id=artifact,version=VERSION,created_utc=datetime.now(timezone.utc).isoformat(),
                idempotency_sha256=digest,items=items,cores=response['cores'],
                measurement_protocol=response['measurement_protocol'],producer_hashes=producers,
                budget=expected_budget(items,response['cores'],response['measurement_protocol']),
                efficiency_enabled=False,optimization_ranking_enabled=False)
            write_json(folder/'manifest.json',manifest)
            write_json(folder/'state.json',dict(id=artifact,status='prepared',
                items=[dict(id=r['id'],records=r['records'],status='pending') for r in items],
                new_import_ids=[],new_execution_ids=[]))
            return dict(self.get(artifact),existing_batch_reused=False)
        finally: lease.release()

    def verify_producers(self, manifest):
        if manifest['producer_hashes'] != self.producers(manifest['measurement_protocol']):
            raise ValueError('批量程序已改变；保留本记录，不下发旧程序任务')

    def item_records(self, item, cursor, *, discover_execution=True):
        if item['id'] != cursor['id']: raise ValueError('批量项身份不符')
        values = {}
        components = dict(preparation=self.chain.preparation,native_import=self.chain.native,
            analysis_plan=self.chain.analysis,execution=self.chain.execution)
        for stage, reference in cursor['records'].items():
            if stage not in components: raise ValueError('批量阶段不符')
            if stage in item['records'] and reference['id'] != item['records'][stage]['id']:
                raise ValueError('冻结批量项的原记录ID改变')
            record = components[stage].get(reference['id'])
            if not same_source(record,item['source']): raise ValueError('批量项的材料/有效库来源不符')
            values[stage] = record
        prep = values['preparation']; native = values['native_import']; plan = values['analysis_plan']
        if (prep['calibration_id']!=item['selection']['calibration'] or prep['material_key']!=item['selection']['key']
                or prep['files']['package/material.amat'] != item['source']['AMAT_sha256']
                or prep['files']['package/metadata.json'] != item['source']['metadata_sha256']
                or prep['template_sha256'] != item['source']['template_sha256']
                or native['preparation_id'] != prep['id'] or plan['native_import_id'] != native['id']):
            raise ValueError('批量项的文件或父子记录绑定改变')
        if 'execution' in values and (values['execution']['analysis_plan_id'] != plan['id'] or values['execution'].get('preflight_only')):
            raise ValueError('批量项执行不属于冻结分析计划')
        if discover_execution and 'execution' not in values and plan['status']=='model_snapshot_ready_not_submitted':
            runs=self.chain.execution.records()
            if any(r['status']=='invalid' for r in runs):raise ValueError('执行记录不可核对，批量未创建替代任务')
            candidates=[r for r in runs if r.get('analysis_plan_id')==plan['id'] and not r.get('preflight_only')]
            if any(not same_source(r,item['source']) for r in candidates):raise ValueError('已有执行的批量来源不符')
            discovered=self.chain.pick(candidates,{'completed':5,'running':4,'starting':4,'prepared':3})
            if discovered:values['execution']=discovered
        return values

    def get(self, artifact):
        folder=self.path(artifact);manifest=read(folder/'manifest.json');state=read(folder/'state.json')
        if (manifest['id']!=artifact or state['id']!=artifact or manifest['version']!=VERSION
                or manifest.get('efficiency_enabled') is not False or manifest.get('optimization_ranking_enabled') is not False
                or type(manifest['cores']) is not int or not 1<=manifest['cores']<=2
                or not 1<=len(manifest['items'])<=8 or len(state['items'])!=len(manifest['items'])
                or manifest['budget']!=expected_budget(manifest['items'],manifest['cores'],manifest['measurement_protocol'])
                or state['status'] not in ('prepared','starting','running',*TERMINAL)):
            raise ValueError('批量计划身份或范围不符')
        protocol(manifest['measurement_protocol'])
        catalog = manifest['measurement_protocol'] == LOSS_PROTOCOL
        if catalog and len({item['source']['catalog_loss_reference_sha256'] for item in manifest['items']}) != 1:
            raise ValueError('目录铁损批量的冻结目录来源不统一')
        identity=dict(materials=[dict(selection=r['selection'],source=r['source']) for r in manifest['items']],
            cores=manifest['cores'],measurement_protocol=manifest['measurement_protocol'],producer_hashes=manifest['producer_hashes'])
        if sha(json.dumps(identity,sort_keys=True,separators=(',',':')).encode())!=manifest['idempotency_sha256']:
            raise ValueError('批量来源或程序身份改变')
        result=dict(manifest,**state);rows=[]
        discover_execution = state['status'] not in ('completed', 'completed_with_failures')
        for item,cursor in zip(manifest['items'],state['items']):
            records=self.item_records(item,cursor,discover_execution=discover_execution);run=records.get('execution')
            if catalog and records['analysis_plan']['catalog_loss']['reference_sha256'] != item['source']['catalog_loss_reference_sha256']:
                raise ValueError('目录铁损分析计划与冻结批量的目录来源不符')
            if (records['preparation']['cores']!=manifest['cores'] or records['analysis_plan']['cores']!=manifest['cores']
                    or records['analysis_plan']['measurement_protocol']!=manifest['measurement_protocol']
                    or (run and (run['cores']!=manifest['cores'] or run['measurement_protocol']!=manifest['measurement_protocol']))):
                raise ValueError('批量核数或分析协议与原计划不符')
            row=dict(id=item['id'],selection=item['selection'],source=item['source'],status=cursor['status'],
                error=cursor.get('error'),records={s:dict(id=r['id'],status=r['status']) for s,r in records.items()},
                analysis_url='/calibrated-motor/analysis/'+records['analysis_plan']['id'],result_available=False)
            if run and run.get('result_available'):
                row.update(result_available=True,metrics=run['result']['metrics'],execution_id=run['id'],
                    model_version=run['result']['model_version'],measurement_protocol=run['measurement_protocol'])
                if catalog:
                    loss = run['result']['loss_estimate_metrics']
                    row['catalog_loss_summary'] = {key: loss[key] for key in
                        ('official_samples','time_weighted_mean_W','minima_W','csv_hashes','object_report')}
                    row['catalog_loss_summary'].update(core_loss_breakdown=loss.get('core_loss_breakdown'),
                                                       budget_exceeded=run['result']['budget_exceeded'])
            if run and run.get('budget_stop') and run.get('status')=='failed':
                row.update(raw_error=row.get('error'),error=run['error'],budget_stop=run['budget_stop'])
            rows.append(row)
        result['items']=rows;result['dispatch_compatible']=manifest['producer_hashes']==self.producers(manifest['measurement_protocol'])
        result['pause_requested']=(folder/'pause_requested.json').exists()
        if state['status']=='prepared' and not result['dispatch_compatible']:result['status']='prepared_source_changed'
        if state['status'] in ('starting','running') and (folder/'controller.json').exists():
            if identity_status(read(folder/'controller.json')['identity'])!='alive':
                result.update(status='needs_attention',error='批量控制器已退出；保留各阶段，请显式接续')
        controller = folder/'controller.json'
        journal_pending = (folder/'interruption_recovery.json').exists() and not (folder/'interruption_applied.json').exists()
        if journal_pending or (result['status']=='needs_attention' and state.get('current_item') and controller.is_file()
                               and identity_status(read(controller)['identity'])=='dead'):
            result['interruption_recovery_available'] = True
        result['totals']=dict(materials=len(rows),actual_torque_results=sum(r['result_available'] for r in rows),
            new_completed_motor_solves=sum(r.get('execution_id') in state['new_execution_ids'] and r['result_available'] for r in rows))
        imports=state['new_import_ids'];executions=state['new_execution_ids']
        if (len(imports)!=len(set(imports)) or len(executions)!=len(set(executions))
                or len(imports)>manifest['budget']['max_new_imports'] or len(executions)>manifest['budget']['max_new_motor_solves']
                or not set(imports)<={r['records']['native_import']['id'] for r in manifest['items']}
                or not set(executions)<={r['records']['execution']['id'] for r in rows if 'execution' in r['records']}):
            raise ValueError('批量新增下发记录与冻结预算或原阶段不符')
        return result

    def history(self):
        """List saved phase labels without opening the native result files."""
        rows=[]
        for folder in self.storage.glob('bhbatch_*'):
            try:
                path = self.path(folder.name)
                manifest, state = read(path/'manifest.json'), read(path/'state.json')
                if (manifest['id']!=folder.name or state['id']!=folder.name or manifest['version']!=VERSION
                        or state['status'] not in ('prepared','starting','running',*TERMINAL)
                        or not isinstance(manifest['created_utc'],str)
                        or not re.fullmatch(r'[a-f0-9]{64}',manifest['idempotency_sha256'])):
                    raise ValueError('批量保存记录身份不符')
                rows.append(dict(id=folder.name,status=state['status'],created_utc=manifest['created_utc'],
                                 idempotency_sha256=manifest['idempotency_sha256']))
            except (ValueError,KeyError,OSError,TypeError) as error:
                rows.append(dict(id=folder.name,status='invalid',error=str(error)))
        return sorted(rows,key=lambda row:row.get('created_utc',''),reverse=True)

    def _record_or_error(self, artifact):
        try:
            return self.get(artifact)
        except (ValueError,KeyError,OSError,TypeError) as error:
            return dict(id=artifact,status='invalid',error=str(error))

    def records(self):
        rows = [self._record_or_error(folder.name) for folder in self.storage.glob('bhbatch_*')]
        return sorted(rows,key=lambda r:r.get('created_utc',''),reverse=True)

    def start(self, artifact, resume=False):
        if os.environ.get('MAGSIM_CPU_ONLY')=='1':raise ValueError('CPU预览不启动批量原生任务')
        folder=self.path(artifact)
        with DispatchWindow(self.storage.parent,recover_batch=artifact if resume else None) as window:
            record=self.get(artifact);manifest=read(folder/'manifest.json');state=read(folder/'state.json')
            self.verify_producers(manifest)
            allowed=('needs_attention','running','starting','paused') if resume else ('prepared',)
            if record['status'] not in allowed:raise ValueError('批量状态不允许开始或接续；不会重跑已完成批量')
            if (folder/'controller.json').exists() and identity_status(read(folder/'controller.json')['identity'])!='dead':
                raise ValueError('批量控制器仍活动或身份不明')
            if window.reservation.exists():
                previous=read(window.reservation)
                if previous['batch_id']!=artifact or identity_status(previous['identity'])!='dead':
                    raise ValueError('另一批量仍占用派发')
                history=folder/'controller_history';history.mkdir(exist_ok=True)
                window.reservation.rename(history/('reservation_'+uuid.uuid4().hex+'.json'))
            if (folder/'controller.json').exists():
                history=folder/'controller_history';history.mkdir(exist_ok=True)
                write_json(history/('controller_'+uuid.uuid4().hex+'.json'),read(folder/'controller.json'))
            if (folder/'pause_requested.json').exists():
                history=folder/'controller_history';history.mkdir(exist_ok=True)
                (folder/'pause_requested.json').rename(history/('pause_'+uuid.uuid4().hex+'.json'))
            token=uuid.uuid4().hex;state.update(status='starting',controller_token=token,error=None)
            write_json(folder/'state.json',state)
            python=self.chain.preparation.motor.root/'.runtime/magsim/Scripts/python.exe'
            try:
                with (folder/f'controller_{token}.log').open('x',encoding='utf8') as log:
                    proc=subprocess.Popen([str(python),'-X','utf8',str(self.project/'tools/material_motor_batch_worker.py'),
                        '--folder',str(folder),'--root',str(self.chain.preparation.motor.root)],cwd=folder,
                        stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                identity=process_identity(proc.pid)
                owner=dict(batch_id=artifact,token=token,identity=identity)
                write_json(window.reservation,owner);write_json(folder/'controller.json',owner)
            except Exception as exc:
                state.update(status='needs_attention',error='批量控制器下发未确认，保留现场：'+str(exc))
                write_json(folder/'state.json',state)
                raise ValueError(state['error']) from exc
        return self.get(artifact)

    def pause(self, artifact):
        folder=self.path(artifact);record=self.get(artifact)
        if record['status'] not in ('prepared','starting','running'):
            raise ValueError('当前批量没有可暂停的活动阶段')
        write_json(folder/'pause_requested.json',dict(requested_utc=datetime.now(timezone.utc).isoformat(),
            scope='Finish the current native stage, then stop dispatching; no process termination'))
        if record['status']=='prepared':
            state=read(folder/'state.json');state.update(status='paused');write_json(folder/'state.json',state)
        return self.get(artifact)

    def settle_interruption(self, artifact):
        from modules.motor_batch_recovery import settle_interruption
        return settle_interruption(self, artifact)

    def bundle(self, artifact):
        record=self.get(artifact);stream=io.StringIO(newline='')
        writer=csv.writer(stream);writer.writerow(['item','calibration','material_key','material','effective_bank_sha256',
            'excluded_grades','measurement_protocol','model_version','status','execution',
            'T_avg_Nm','T_min_Nm','T_max_Nm','K_T_ripple_pct','error'])
        files={'batch.json':json.dumps(record,ensure_ascii=False,indent=2).encode('utf8')}
        if record.get('interruption_recovery'):
            files['interruption_recovery.json'] = (self.path(artifact)/'interruption_recovery.json').read_bytes()
        catalog = record['measurement_protocol'] == LOSS_PROTOCOL
        if catalog:
            loss_stream=io.StringIO(newline='')
            loss_writer=csv.writer(loss_stream)
            loss_writer.writerow(['item','material_key','execution','reference_sha256','official_samples',
                'CoreLoss_mean_W','StrandedLoss_mean_W','SolidLoss_mean_W','verified_insert_CoreLoss_mean_W','object_report_status'])
        for row in record['items']:
            m=row.get('metrics',{})
            writer.writerow([row['id'],row['selection']['calibration'],row['selection']['key'],row['source']['material_name'],
                row['source']['effective_bank_sha256'],';'.join(row['source']['excluded_grades']),record['measurement_protocol'],
                row.get('model_version',''),row['status'],row.get('execution_id',''),
                *[m.get(k) for k in ('T_avg_Nm','T_min_Nm','T_max_Nm','K_T_ripple_pct')],row.get('error') or ''])
            if row['result_available']:
                files[row['id']+'/Torque Plots.csv']=self.chain.execution.download(row['execution_id'],'case/reports/Torque Plots.csv').read_bytes()
                if protocol(record['measurement_protocol']).get('equivalent_direction'):
                    for name,blob in self.chain.execution.material_exports(row['execution_id']).items():
                        files[row['id']+'/'+name]=blob
            if catalog:
                loss = row.get('catalog_loss_summary') or {}
                means = loss.get('time_weighted_mean_W') or {}
                breakdown = loss.get('core_loss_breakdown') or {}
                loss_writer.writerow([row['id'],row['selection']['key'],row.get('execution_id'),
                    row['source']['catalog_loss_reference_sha256'],loss.get('official_samples'),
                    *[means.get(name) for name in ('CoreLoss','StrandedLoss','SolidLoss')],
                    breakdown.get('insert_mean_W') if breakdown.get('closure_passed') else None,
                    loss.get('object_report',{}).get('status')])
                for name,digest in loss.get('csv_hashes',{}).items():
                    blob=self.chain.execution.download(row['execution_id'],name).read_bytes()
                    if sha(blob)!=digest:raise ValueError('批量原始损耗CSV在打包期间改变')
                    files[row['id']+'/'+Path(name).name]=blob
        if catalog:files['catalog_loss_summary.csv']=loss_stream.getvalue().encode('utf-8-sig')
        files['summary.csv']=stream.getvalue().encode('utf-8-sig')
        files['manifest.json']=json.dumps(dict(files={n:sha(b) for n,b in files.items()},efficiency_enabled=False,
            optimization_ranking_enabled=False,new_native_solves=0),indent=2).encode()
        out=io.BytesIO()
        with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as archive:
            for name,blob in files.items():archive.writestr(name,blob)
        out.seek(0);return out
