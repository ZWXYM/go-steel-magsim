"""Apply an explicitly selected frozen study configuration using the original scorer."""
import io,json,re,zipfile
from datetime import datetime,timezone
from pathlib import Path
from flask import Blueprint,jsonify,request,send_file
from modules import motor_optimization as original
from modules.motor_queue import QueueLease
from modules.motor_workbench import write_json
from modules.system_data import json_bytes,sha256

VERSION='motor_study_application_v1'
ANALYSIS_FILES=('analysis.json','ranking.csv','analysis.md','analysis_bundle.zip')
FILES=('request.json','study_bundle.zip',*ANALYSIS_FILES,'README.md')


class MotorStudyApplications:
    def __init__(self,studies,storage):
        self.studies,self.optimization,self.storage=studies,studies.optimization,Path(storage).resolve()

    def path(self,artifact):
        if not isinstance(artifact,str) or not re.fullmatch(r'sapply_[a-f0-9]{16}',artifact):raise ValueError('无效配置筛选记录')
        path=(self.storage/artifact).resolve()
        if path.parent!=self.storage:raise ValueError('配置筛选记录越出存储目录')
        return path

    def _source(self,artifact):
        report,files=self.studies.content(artifact)
        binding=sha256(json_bytes({n:sha256(b) for n,b in files.items()}))
        return report,files,binding

    def _current(self,report,files):
        if report['original_scorer_sha256']!=sha256(Path(original.__file__).read_bytes()):
            raise ValueError('原评分程序版本已变化，请从当前数据重新建立研究')
        saved=json.loads(files['source_dataset.json']);current=self.optimization.dataset(report['dataset'])
        if (json_bytes(saved)!=json_bytes(current) or saved['id']!=report['dataset']
                or saved['version']!=report['model_version'] or saved['measurement_protocol']!=report['measurement_protocol']):
            raise ValueError('研究来源与当前数据不一致，请重新建立研究；未重用旧指标')
        return saved

    def preview(self,artifact):
        report,files,binding=self._source(artifact)
        try:self._current(report,files);matches=True;error=None
        except (OSError,ValueError,KeyError,TypeError) as exc:matches=False;error=str(exc)
        return dict(study_id=artifact,dataset=report['dataset'],model_version=report['model_version'],
            measurement_protocol=report['measurement_protocol'],reference=report['reference'],torque_reference=report['torque_reference'],
            source_binding_sha256=binding,source_matches=matches,source_error=error,
            configurations=[{k:row[k] for k in ('id','config','meets_limits_count','best_case','is_original_default')} for row in report['configurations']],
            new_native_solves=0,new_predictions=0)

    def _content(self,artifact):
        folder=self.path(artifact);record=json.loads((folder/'application.json').read_bytes())
        if record.get('id')!=artifact or record.get('schema')!=VERSION or record.get('status')!='completed':
            raise ValueError('配置筛选身份或保存状态不符')
        if set(record.get('files',{}))!=set(FILES):raise ValueError('配置筛选文件清单不符')
        content={name:(folder/name).read_bytes() for name in record['files']}
        for name,digest in record['files'].items():
            if sha256(content[name])!=digest:raise ValueError('保存的配置筛选文件已改变：'+name)
        selected=json.loads(content['request.json'])
        if artifact!='sapply_'+sha256(json_bytes(selected))[:16]:raise ValueError('配置筛选来源身份不符')
        if any(record.get(k)!=selected.get(k) for k in ('study_id','configuration_id','source_binding_sha256')):
            raise ValueError('配置筛选请求与记录不符')
        with zipfile.ZipFile(io.BytesIO(content['study_bundle.zip'])) as archive:
            frozen={name:archive.read(name) for name in archive.namelist()}
        if sha256(json_bytes({n:sha256(b) for n,b in frozen.items()}))!=selected['source_binding_sha256']:
            raise ValueError('保存的研究来源绑定不符')
        study=json.loads(frozen['study.json']);dataset=json.loads(frozen['source_dataset.json'])
        choices=[row for row in study['configurations'] if row['id']==selected['configuration_id']]
        if study['id']!=selected['study_id'] or len(choices)!=1:raise ValueError('保存的研究配置身份不符')
        chosen=choices[0];analysis=json.loads(content['analysis.json'])
        expected=dict(analysis_id=analysis['id'],dataset=dataset['id'],model_version=dataset['version'],
            measurement_protocol=dataset['measurement_protocol'],reference=study['reference'],torque_reference=study['torque_reference'],
            config=chosen['config'],is_original_default=chosen['is_original_default'],best_case=chosen['best_case'],
            meets_limits_count=chosen['meets_limits_count'],original_default_candidate_count=study['original_default_candidate_count'],
            source_hashes=study['source_hashes'],original_scorer_sha256=study['original_scorer_sha256'],
            new_native_solves=0,new_predictions=0,new_training=0,automatic_native_submission=False,original_default_changed=False)
        if (any(record.get(k)!=v for k,v in expected.items()) or analysis['ranked']!=chosen['ranked']
                or json_bytes(analysis['dataset'])!=json_bytes(dataset) or analysis['config']!=chosen['config']
                or analysis['reference']!=study['reference'] or analysis['torque_reference']!=study['torque_reference']):
            raise ValueError('保存的筛选摘要与原研究/分析不一致')
        content['application.json']=json_bytes(record)
        return record,content

    def get(self,artifact):return self._content(artifact)[0]

    def apply(self,artifact,selection):
        if (not isinstance(selection,dict) or set(selection)!={'configuration_id','source_binding_sha256'}
                or not isinstance(selection['configuration_id'],str)):
            raise ValueError('请明确选择研究配置及保存来源')
        report,files,binding=self._source(artifact)
        if binding!=selection['source_binding_sha256']:raise ValueError('保存研究已变化，请刷新后重新选择')
        choices=[row for row in report['configurations'] if row['id']==selection['configuration_id']]
        if len(choices)!=1:raise ValueError('该研究没有唯一的所选配置')
        chosen=choices[0];config=original.validate_config(chosen['config'])
        selected=dict(study_id=artifact,configuration_id=chosen['id'],source_binding_sha256=binding)
        identity='sapply_'+sha256(json_bytes(selected))[:16];folder=self.path(identity)
        lease=QueueLease(self.storage/(identity+'.guard'))
        if not lease.acquire():raise ValueError('该配置正在保存，请稍后查看，不重复下发')
        try:
            if (folder/'application.json').exists():return dict(self.get(identity),created=False)
            if (folder/'state.json').exists():raise ValueError('该配置曾开始但未完成，请检查保留记录；没有自动重试')
            saved=self._current(report,files)
            folder.mkdir(parents=True,exist_ok=True)
            write_json(folder/'request.json',selected)
            write_json(folder/'state.json',dict(id=identity,status='running',automatic_retry=False))
            analysis_id=None
            try:
                result=self.optimization.analyze(dict(dataset=report['dataset'],reference=report['reference'],config=config))
                analysis_id=result['id']
                if (json_bytes(result['dataset'])!=json_bytes(saved) or result['config']!=config
                        or result['reference']!=report['reference'] or result['torque_reference']!=report['torque_reference']
                        or result['ranked']!=chosen['ranked'] or result['best_case']!=chosen['best_case']
                        or result['meets_limits_count']!=chosen['meets_limits_count']):
                    raise ValueError('原程序筛选与所选研究配置不一致，保留新记录待检查')
                self._current(report,files)
                _,_,after=self._source(artifact)
                if after!=binding:raise ValueError('保存期间研究来源已变化，保留新记录待检查')
                for name in ANALYSIS_FILES:(folder/name).write_bytes(self.optimization.artifact(analysis_id,name).read_bytes())
                with zipfile.ZipFile(folder/'study_bundle.zip','x',zipfile.ZIP_DEFLATED) as archive:
                    for name,blob in files.items():archive.writestr(name,blob)
                (folder/'README.md').write_text('''# 从约束研究应用原筛选

本记录由明确选择的一组研究条件生成，调用原优化程序，不改变默认条件、原评分或旧数据。study_bundle.zip保留原研究六文件，analysis系列保留原程序本次四个出口。配置组数及重复应用不增加独立材料；未新增原生仿真、预测或训练。

同一研究、配置及文件来源重复请求返回此记录，不重复筛选。来源改变需新研究；中断或失败记录保留，不自动重试。未满足的默认配置仍可保存为零候选结果。放宽转矩等条件的可行解仅适用于该配置，不能称原默认约束已改善或新的物理最终排名。
''',encoding='utf8')
                record=dict(schema=VERSION,id=identity,status='completed',created_utc=datetime.now(timezone.utc).isoformat(),
                    **selected,analysis_id=analysis_id,dataset=report['dataset'],model_version=report['model_version'],
                    measurement_protocol=report['measurement_protocol'],reference=report['reference'],torque_reference=report['torque_reference'],
                    config=config,is_original_default=chosen['is_original_default'],meets_limits_count=result['meets_limits_count'],
                    best_case=result['best_case'],original_default_candidate_count=report['original_default_candidate_count'],
                    source_hashes=report['source_hashes'],original_scorer_sha256=report['original_scorer_sha256'],
                    files={name:sha256((folder/name).read_bytes()) for name in FILES},
                    optimization_url='/motor-optimization?analysis='+analysis_id,
                    new_native_solves=0,new_predictions=0,new_training=0,automatic_native_submission=False,
                    original_default_changed=False,final_physical_optimization_certified=False)
                write_json(folder/'application.json',record)
                self.get(identity)
                write_json(folder/'state.json',dict(id=identity,status='completed',analysis_id=analysis_id,automatic_retry=False))
                return dict(record,created=True)
            except Exception as exc:
                write_json(folder/'state.json',dict(id=identity,status='needs_attention',analysis_id=analysis_id,error=str(exc),automatic_retry=False))
                raise
        finally:lease.release()

    def download(self,artifact,name):
        record,content=self._content(artifact)
        if name=='application_bundle.zip':
            out=io.BytesIO()
            with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as archive:
                for item,blob in content.items():archive.writestr(item,blob)
            out.seek(0);return out
        if name not in (*FILES,'application.json'):raise ValueError('未知配置筛选输出文件')
        return io.BytesIO(content[name])


def create_study_application_routes(applications):
    bp=Blueprint('motor_study_applications',__name__)
    @bp.get('/api/workbench/optimization/sensitivity/<artifact>/applications')
    def preview(artifact):return jsonify(applications.preview(artifact))
    @bp.post('/api/workbench/optimization/sensitivity/<artifact>/applications')
    def apply(artifact):
        result=applications.apply(artifact,request.get_json());return jsonify(result),201 if result['created'] else 200
    @bp.get('/api/workbench/optimization/study-applications/<artifact>')
    def get(artifact):return jsonify(applications.get(artifact))
    @bp.get('/api/workbench/optimization/study-applications/<artifact>/files/<name>')
    def download(artifact,name):return send_file(applications.download(artifact,name),as_attachment=True,download_name=artifact+'_'+name)
    return bp
