"""Collect one completed original plan and its exact dataset without re-scoring."""
import io,json,re,zipfile
from pathlib import Path
from flask import Blueprint,jsonify,request
from modules.motor_dataset_export import dataset_bundle,dataset_usage
from modules.motor_queue import QueueLease
from modules.motor_workbench import write_json
from modules.system_data import WorkpackStore,json_bytes,sha256
from modules.system_data_optimization import SavedOptimizationExports

VERSION='original_plan_result_workpack_v1'


class MotorPlanResults:
    def __init__(self,plans,storage):
        self.plans,self.optimization,self.motor=plans,plans.optimization,plans.motor
        self.saved=SavedOptimizationExports(self.optimization)
        self.storage=Path(storage).resolve()/'optimization_plan_results'
        self.packages=WorkpackStore(Path(storage).resolve()/'system_data')

    def path(self,artifact):
        if not isinstance(artifact,str) or not re.fullmatch(r'presult_[a-f0-9]{16}',artifact):raise ValueError('无效计划结果记录')
        folder=(self.storage/artifact).resolve()
        if folder.parent!=self.storage:raise ValueError('计划结果不属于当前存储')
        return folder

    def _source(self,plan_id):
        plan=self.plans.get(plan_id)
        if plan['status']!='completed':raise ValueError('计划自动筛选完成后才可整理结果；当前：'+plan['status'])
        folder=self.plans.path(plan_id)
        plan_files={name:(folder/name).read_bytes() for name in ('plan.json','state.json')}
        if sha256(plan_files['plan.json'])!=plan['plan_sha256']:raise ValueError('计划在读取期间变化')
        report,files,analysis_binding=self.saved.read(plan['analysis_id'])
        data=self.optimization.dataset(plan['job_id'])
        job=self.motor.state(plan['job_id'])
        manifest=(self.motor.path(plan['job_id'])/'manifest.json').read_bytes()
        if (job['status'] not in ('completed','completed_with_failures')
                or sha256(manifest)!=plan['job_manifest_sha256']
                or data['id']!=plan['job_id'] or report['dataset']['id']!=data['id']
                or report['dataset']['source_hashes']!=data['source_hashes']
                or report['dataset']['version']!=data['version']
                or report['dataset']['measurement_protocol']!=data['measurement_protocol']
                or data['measurement_protocol']!=plan['measurement_protocol']
                or report['reference']!=plan['reference_case_id']
                or report.get('torque_reference',report['reference'])!=plan['reference_case_id']
                or report['config']!=plan['config']
                or report['best_case']!=plan['best_case'] or report['meets_limits_count']!=plan['meets_limits_count']
                or [c['material'] for c in json.loads(manifest)['cases']]!=plan['materials']):
            raise ValueError('计划、筛选、同任务基准、配置或当前官方结果来源不符')
        ref=next((c for c in json.loads(manifest)['cases'] if c['id']==plan['reference_case_id']),None)
        if ref is None or ref['material']!=plan['reference_material']:raise ValueError('计划基准材料与原案例不符')
        if self.plans.get(plan_id)!=plan or any((folder/name).read_bytes()!=blob for name,blob in plan_files.items()):raise ValueError('计划状态在读取期间变化')
        binding=dict(version=VERSION,plan_id=plan_id,plan_sha256=sha256(plan_files['plan.json']),
            state_sha256=sha256(plan_files['state.json']),analysis_id=report['id'],analysis_binding_sha256=analysis_binding,
            dataset_id=data['id'],dataset_source_hashes_sha256=sha256(json_bytes(data['source_hashes'])),
            producer_sha256=sha256(Path(__file__).read_bytes()))
        index=dict(schema=VERSION,binding=binding,plan_id=plan_id,analysis_id=report['id'],dataset=data['id'],
            model_version=data['version'],measurement_protocol=data['measurement_protocol'],materials=plan['materials'],
            reference_case=plan['reference_case_id'],reference_material=plan['reference_material'],config=plan['config'],
            best_case=report['best_case'],meets_limits_count=report['meets_limits_count'],
            row_count=len(data['rows']),saved_accepted_count=sum(row.get('input_accepted') is True for row in data['rows']),
            data_usage=dataset_usage(data),scope=data['scope'],new_native_solves=0,new_analyses=0,new_training=0,new_predictions=0)
        return index,plan_files,files,data

    def preview(self,plan_id):
        try:
            index,_,_,_=self._source(plan_id)
            return dict(available=True,source_binding_sha256=sha256(json_bytes(index['binding'])),summary=index)
        except (OSError,ValueError,KeyError,TypeError) as error:return dict(available=False,plan_id=plan_id,reason=str(error))

    def get(self,artifact):
        entry=json.loads((self.path(artifact)/'entry.json').read_bytes())
        if entry.get('id')!=artifact:raise ValueError('计划结果登记身份不符')
        package,blob=self.packages.read(entry['package_id'])
        with zipfile.ZipFile(io.BytesIO(blob)) as z:index=json.loads(z.read('data_index.json'))
        if index.get('schema')!=VERSION or artifact!='presult_'+sha256(json_bytes(index['binding']))[:16]:raise ValueError('计划结果包身份不符')
        return dict(id=artifact,plan_id=index['plan_id'],analysis_id=index['analysis_id'],dataset=index['dataset'],
            package_id=package['id'],package_sha256=package['sha256'],package_members=package['member_count'],summary=index,
            package_url='/api/workbench/system-data/packages/'+package['id']+'/download')

    def prepare(self,plan_id,selection):
        if not isinstance(selection,dict) or set(selection)!={'source_binding_sha256'}:raise ValueError('请先读取并确认该计划的结果来源')
        index,plans,analysis,data=self._source(plan_id)
        binding=sha256(json_bytes(index['binding']))
        if selection['source_binding_sha256']!=binding:raise ValueError('计划结果来源已变化，请刷新后重新整理')
        artifact='presult_'+binding[:16];folder=self.path(artifact);lease=QueueLease(self.storage/(artifact+'.guard'))
        if not lease.acquire():raise ValueError('该结果包正在整理，请稍后重开')
        try:
            if (folder/'entry.json').exists():return dict(self.get(artifact),created=False)
            if (folder/'state.json').exists():raise ValueError('此前整理未完成，现场保留；未自动重复生成')
            folder.mkdir(parents=True,exist_ok=True);write_json(folder/'state.json',dict(status='collecting',automatic_retry=False))
            try:
                raw=dataset_bundle(self.optimization,data['id']).getvalue()
                with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                    snapshot=json.loads(archive.read('dataset.json'))
                if (snapshot['source_hashes']!=data['source_hashes'] or snapshot['id']!=data['id']
                        or snapshot['version']!=data['version'] or snapshot['measurement_protocol']!=data['measurement_protocol']):
                    raise ValueError('官方数据在整理期间变化')
                files={'plan/'+n:b for n,b in plans.items()}
                files.update({'analysis/'+n:b for n,b in analysis.items()})
                files.update({'analysis/data_usage.json':json_bytes(index['data_usage']),
                    'motor_dataset.zip':raw,'data_index.json':json_bytes(index)})
                files['README.md']=('''# 原扫描计划完整结果包

从一个已完成自动筛选的原计划收集同任务数据，无需另选数据集或筛选。plan/保留原计划及完成状态，analysis/保留原四项筛选出口，motor_dataset.zip包含该任务全部案例、官方CSV、来源及异常。原基准、配置和零候选保持，不重新求解或评分。

每包仅对应一个原模型版本与测量协议；原数值判据、损耗估算/本构边界和周期范围保持。保存案例数不是独立实测样品数，完整包不等于最终物理认证。失败案例与原值保留，不剔除异常伪造成果。完整原生工程/日志/缓存仍在本机任务目录。

data_index.json描述固定来源，manifest.json核对成员完整性。本包为私有快照，自动登记到现有数据中心，不自动公开。同一来源再次整理重开同一包，0新仿真/预测/训练/分析。
''').encode('utf8')
                after,_,_,_=self._source(plan_id)
                if after!=index:raise ValueError('计划或筛选在整理期间变化')
                files['manifest.json']=json_bytes(dict(version='system_data_workpack_v4',private_only=True,
                    files={n:sha256(b) for n,b in files.items()},new_native_solves=0,new_analyses=0,new_predictions=0))
                out=io.BytesIO()
                with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
                    for n,b in files.items():z.writestr(n,b)
                package=self.packages.register_bytes(out.getvalue(),'原扫描计划结果 '+plan_id,
                    str(index['row_count'])+'案例，官方来源、原筛选和同任务基准自动配对；原候选'+str(index['meets_limits_count'])+'项，未重新评分。')
                write_json(folder/'entry.json',dict(id=artifact,package_id=package['id']))
                result=self.get(artifact);write_json(folder/'state.json',dict(status='saved',automatic_retry=False))
                return dict(result,created=True)
            except Exception as error:
                write_json(folder/'state.json',dict(status='needs_attention',error=str(error),automatic_retry=False));raise
        finally:lease.release()


def create_plan_result_routes(manager):
    bp=Blueprint('motor_plan_results',__name__)
    @bp.get('/api/workbench/optimization/plans/<plan_id>/result-package')
    def preview(plan_id):return jsonify(manager.preview(plan_id))
    @bp.post('/api/workbench/optimization/plans/<plan_id>/result-package')
    def prepare(plan_id):
        value=manager.prepare(plan_id,request.get_json());return jsonify(value),201 if value['created'] else 200
    @bp.get('/api/workbench/optimization/plan-results/<artifact>')
    def get(artifact):return jsonify(manager.get(artifact))
    return bp
