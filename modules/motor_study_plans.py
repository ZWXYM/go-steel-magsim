"""Prepare an original scan plan from an explicit, frozen screening result."""
import io,json,re,zipfile
from datetime import datetime,timezone
from pathlib import Path
from flask import Blueprint,jsonify,request
from modules.motor_queue import QueueLease,native_transport_setting
from modules.motor_workbench import write_json
from modules.system_data import WorkpackStore,json_bytes,sha256

VERSION='study_to_original_scan_plan_v1'
WORKER_FILES=('tools/motor_scan_worker.py','modules/motor_workbench.py','modules/material_calibration.py',
              'modules/motor_queue.py','modules/motor_model_audit.py')


class MotorStudyPlans:
    def __init__(self,applications,plans,storage):
        self.applications,self.plans,self.motor=applications,plans,plans.motor
        self.storage=Path(storage).resolve()/'optimization_study_plans'
        self.packages=WorkpackStore(Path(storage).resolve()/'system_data')

    def path(self,artifact):
        if not isinstance(artifact,str) or not re.fullmatch(r'splan_[a-f0-9]{16}',artifact):raise ValueError('无效来源扫描计划')
        folder=(self.storage/artifact).resolve()
        if folder.parent!=self.storage:raise ValueError('来源扫描计划越出存储目录')
        return folder

    def _recipe(self,artifact):
        application=self.applications.get(artifact)
        blob=self.applications.download(artifact,'analysis.json').getvalue()
        if sha256(blob)!=application['files']['analysis.json']:raise ValueError('筛选来源在读取期间变化')
        analysis=json.loads(blob);data=analysis['dataset'];reference=application['reference']
        if application['torque_reference']!=reference:raise ValueError('比较与转矩基准不同，请在原优化页明确配置扫描')
        protocol=application['measurement_protocol']
        if protocol not in ('v8_2_cycle1','periodic_cycle3_v1'):raise ValueError('该原结果协议需在原优化页明确配置')
        catalog=self.motor.catalog()
        if not catalog['available']:raise ValueError(catalog.get('reason','原电机资源不可用'))
        if data['id'].startswith('scan_'):
            source=self.motor.path(data['id'])/'manifest.json';key='id';kind='same_scan_manifest'
        elif data['id'] in ('v8_2_archive','v8_2_bound_archive'):
            source=self.motor.root/'motor/v8_1/manifest.json';key='case';kind='original_library_manifest'
        else:raise ValueError('该历史基准没有同任务牌号映射，请在原优化页选择材料')
        source_blob=source.read_bytes();index=json.loads(source_blob);items=index['cases'] if isinstance(index,dict) else index
        candidates=[row['case'] for row in analysis['ranked'] if row['meets_limits'] and row['case']!=reference]
        if len(candidates)!=len(set(candidates)):raise ValueError('筛选候选案例重复')
        def mapping(case):
            matches=[row for row in items if row.get(key)==case]
            if len(matches)!=1 or matches[0].get('material') not in catalog['materials']:
                raise ValueError('案例没有唯一且可用的原库牌号映射：'+case)
            return dict(case_id=case,material=matches[0]['material'])
        reference_mapping=mapping(reference);choices=[mapping(case) for case in candidates]
        if len({row['material'] for row in [reference_mapping,*choices]})!=len(choices)+1:
            raise ValueError('多个源案例映射到相同牌号，请在原优化页明确选择')
        if sha256(self.motor.template.read_bytes())!=catalog['template_sha256']:raise ValueError('当前模板在读取期间变化')
        recipe=dict(schema=VERSION,application_id=artifact,application_sha256=sha256(json_bytes(application)),
            study_id=application['study_id'],configuration_id=application['configuration_id'],analysis_id=application['analysis_id'],
            source_dataset=data['id'],source_model_version=data['version'],measurement_protocol=protocol,
            config=application['config'],reference=reference_mapping,candidates=choices,
            mapping_kind=kind,mapping_source_sha256=sha256(source_blob),template_sha256=catalog['template_sha256'],
            template=catalog['template'],aedt_transport=native_transport_setting(),
            worker_source_sha256={name:sha256((self.motor.project/name).read_bytes()) for name in WORKER_FILES},
            producer_sha256=sha256(Path(__file__).read_bytes()))
        return recipe,catalog

    def preview(self,artifact):
        try:
            recipe,catalog=self._recipe(artifact)
            return dict(application_id=artifact,available=bool(recipe['candidates']),recipe=recipe,
                source_binding_sha256=sha256(json_bytes(recipe)),
                reason=None if recipe['candidates'] else '该配置没有其他可行候选，不准备仅基准的扫描',
                license=catalog.get('license'),new_native_solves=0)
        except (OSError,ValueError,KeyError,TypeError) as error:
            return dict(application_id=artifact,available=False,reason=str(error),new_native_solves=0)

    def _read(self,artifact):
        folder=self.path(artifact);entry=json.loads((folder/'entry.json').read_bytes())
        if entry.get('id')!=artifact:raise ValueError('来源扫描登记身份不符')
        package,blob=self.packages.read(entry['package_id'])
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            selected=json.loads(archive.read('selection.json'));record=json.loads(archive.read('handoff.json'))
            recipe=json.loads(archive.read('recipe.json'));plan_blob=archive.read('plan/plan.json');plan=json.loads(plan_blob)
            manifest_blob=archive.read('scan/manifest.json');manifest=json.loads(manifest_blob)
            prepared=json.loads(archive.read('plan/prepared_state.json'))
            with zipfile.ZipFile(io.BytesIO(archive.read('source_application.zip'))) as source:
                application=json.loads(source.read('application.json'))
        if (artifact!='splan_'+sha256(json_bytes(selected))[:16] or record['id']!=artifact
                or record['request']!=selected or selected['source_binding_sha256']!=sha256(json_bytes(recipe))
                or recipe['application_id']!=selected['application_id'] or sha256(json_bytes(application))!=recipe['application_sha256']
                or plan['id']!=record['plan_id'] or manifest['id']!=record['job_id'] or plan['job_id']!=manifest['id']
                or plan['config']!=application['config'] or plan['config']!=recipe['config']
                or plan['materials']!=record['materials'] or [row['material'] for row in manifest['cases']]!=record['materials']
                or plan['reference_material']!=recipe['reference']['material']
                or plan['measurement_protocol']!=recipe['measurement_protocol'] or plan['cores']!=selected['cores']
                or manifest['measurement_protocol']!=recipe['measurement_protocol'] or manifest['cores']!=selected['cores']
                or manifest['template_sha256']!=recipe['template_sha256'] or manifest['aedt_transport']!=recipe['aedt_transport']
                or manifest['worker_sha256']!=recipe['worker_source_sha256']['tools/motor_scan_worker.py']
                or any(manifest['support_source_sha256'].get(n)!=recipe['worker_source_sha256'][n] for n in WORKER_FILES if n.startswith('modules/'))
                or plan['job_manifest_sha256']!=sha256(manifest_blob)
                or prepared['plan_sha256']!=sha256(plan_blob) or prepared['status']!='prepared'):
            raise ValueError('来源扫描包的基准、配置、任务或来源身份不符')
        return record,package

    def get(self,artifact):
        record,package=self._read(artifact)
        result=dict(record,package_id=package['id'],package_sha256=package['sha256'],
            package_url='/api/workbench/system-data/packages/'+package['id']+'/download')
        try:result['current_plan_status']=self.plans.get(record['plan_id'])['status']
        except (OSError,ValueError,KeyError,TypeError) as error:result.update(current_plan_status='unavailable',current_plan_error=str(error))
        return result

    def prepare(self,artifact,selection):
        if (not isinstance(selection,dict) or set(selection)!={'source_binding_sha256','cases','cores'}
                or type(selection['cores']) is not int or not 1<=selection['cores']<=32
                or not isinstance(selection['cases'],list) or not 1<=len(selection['cases'])<=15
                or not all(isinstance(case,str) for case in selection['cases']) or len(set(selection['cases']))!=len(selection['cases'])):
            raise ValueError('请明确选择1–15个不同候选和1–32个计算核')
        preview=self.preview(artifact)
        if not preview['available']:raise ValueError(preview['reason'])
        if selection['source_binding_sha256']!=preview['source_binding_sha256']:raise ValueError('扫描来源或模板已变化，请刷新后重新选择')
        recipe=preview['recipe'];choices={row['case_id']:row['material'] for row in recipe['candidates']}
        if not set(selection['cases'])<=set(choices):raise ValueError('只可选择该保存筛选中的可行候选')
        selected=dict(application_id=artifact,source_binding_sha256=selection['source_binding_sha256'],cases=sorted(selection['cases']),cores=selection['cores'])
        identity='splan_'+sha256(json_bytes(selected))[:16];folder=self.path(identity)
        lease=QueueLease(self.storage/(identity+'.guard'))
        if not lease.acquire():raise ValueError('该来源扫描计划正在准备，请稍后查看')
        try:
            if (folder/'entry.json').exists():return dict(self.get(identity),created=False)
            if (folder/'state.json').exists():raise ValueError('此前准备未完成，记录已保留；不自动重复建任务')
            folder.mkdir(parents=True,exist_ok=True);write_json(folder/'state.json',dict(status='preparing',automatic_retry=False))
            plan_id=None
            try:
                materials=[recipe['reference']['material'],*[choices[case] for case in selected['cases']]]
                plan=self.plans.prepare(dict(materials=materials,reference_material=materials[0],cores=selected['cores'],
                    measurement_protocol=recipe['measurement_protocol'],config=recipe['config']))
                plan_id=plan['id'];job_id=plan['job_id']
                if plan['status']!='prepared':raise ValueError('新原扫描计划未处于准备状态，保留现场')
                after,_=self._recipe(artifact)
                if sha256(json_bytes(after))!=selected['source_binding_sha256']:raise ValueError('准备期间来源或模板变化，保留新任务待检查')
                record=dict(schema=VERSION,id=identity,request=selected,created_utc=datetime.now(timezone.utc).isoformat(),
                    application_id=artifact,study_id=recipe['study_id'],configuration_id=recipe['configuration_id'],
                    source_analysis_id=recipe['analysis_id'],plan_id=plan_id,job_id=job_id,materials=materials,
                    reference_material=materials[0],config=recipe['config'],measurement_protocol=recipe['measurement_protocol'],
                    source_model_version=recipe['source_model_version'],snapshot_status='prepared_at_creation',
                    native_submission_during_preparation=False,new_native_solves=0,new_predictions=0,new_analyses=0,new_training=0,
                    original_optimization_url='/motor-optimization?analysis='+recipe['analysis_id']+'#plans')
                files={'selection.json':json_bytes(selected),'recipe.json':json_bytes(recipe),'handoff.json':json_bytes(record),
                    'source_application.zip':self.applications.download(artifact,'application_bundle.zip').getvalue(),
                    'plan/plan.json':(self.plans.path(plan_id)/'plan.json').read_bytes(),
                    'plan/prepared_state.json':(self.plans.path(plan_id)/'state.json').read_bytes(),
                    'scan/manifest.json':(self.motor.path(job_id)/'manifest.json').read_bytes(),
                    'scan/prepared_state.json':(self.motor.path(job_id)/'state.json').read_bytes(),
                    'data_index.json':json_bytes(dict(version='system_data_workpack_v4',kind=VERSION,**record))}
                files['README.md']=('''# 筛选结果到原扫描计划

本包冻结明确候选、原库精确牌号映射、同一基准、约束和当前模板。复用原OptimizationPlans/MotorWorkbench生成隔离任务，仅准备，未启动扫描或原生应用。准备后执行状态请查看原优化页；本包保留创建时状态。

source_application.zip保存原筛选及研究，plan/与scan/保存新计划/任务的原始定义和准备状态。新任务没有复制原指标作为计算结果，完成后仍按同任务基准和原评分重新评估。新模板版本可能与历史数据不同，不能混拼排名。

完整原生工程留在本机任务目录，本包只含计划与来源。manifest验证文件完整性。相同来源/选择/核数重复准备重开原记录；失败保留，不自动重试。该包私有，不自动公开。
''').encode('utf8')
                files['manifest.json']=json_bytes(dict(version='system_data_workpack_v4',private_only=True,
                    files={name:sha256(blob) for name,blob in files.items()},new_native_solves=0,new_predictions=0,new_analyses=0))
                out=io.BytesIO()
                with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as archive:
                    for name,blob in files.items():archive.writestr(name,blob)
                package=self.packages.register_bytes(out.getvalue(),'筛选到扫描计划 '+identity,
                    str(len(materials))+'个原库牌号，固定同任务基准与原配置；仅准备，创建时未求解。原指标不作为新结果复用。')
                write_json(folder/'entry.json',dict(id=identity,package_id=package['id']))
                result=self.get(identity)
                write_json(folder/'state.json',dict(status='prepared',plan_id=plan_id,job_id=job_id,automatic_retry=False))
                return dict(result,created=True)
            except Exception as error:
                write_json(folder/'state.json',dict(status='needs_attention',plan_id=plan_id,error=str(error),automatic_retry=False));raise
        finally:lease.release()


def create_study_plan_routes(manager):
    bp=Blueprint('motor_study_plans',__name__)
    @bp.get('/api/workbench/optimization/study-applications/<artifact>/scan-plan')
    def preview(artifact):return jsonify(manager.preview(artifact))
    @bp.post('/api/workbench/optimization/study-applications/<artifact>/scan-plan')
    def prepare(artifact):
        value=manager.prepare(artifact,request.get_json());return jsonify(value),201 if value['created'] else 200
    @bp.get('/api/workbench/optimization/study-plans/<artifact>')
    def get(artifact):return jsonify(manager.get(artifact))
    return bp
