"""Explicit fixed-grid sensitivity studies using the author's original scorer."""
import csv
import io
import itertools
import json
import re
import tempfile
import uuid
import zipfile
from datetime import datetime,timezone
from pathlib import Path

from flask import Blueprint,jsonify,render_template,request,send_file
from modules import motor_optimization as original
from modules.motor_dataset_export import dataset_usage
from modules.system_data import json_bytes,sha256

VERSION='motor_constraint_study_v1'
AXES=dict(min_ripple_reduction_pct=[10.,20.,30.,40.],min_torque_change_pct=[-2.,-1.,-.5,0.],max_iron_loss_increase_pct=[0.,2.,5.])
FILES=('study.json','source_dataset.json','configurations.csv','case_details.csv','README.md')
PRODUCER_SHA256=sha256(Path(__file__).read_bytes())
SCORER_SHA256=sha256(Path(original.__file__).read_bytes())


def csv_bytes(rows,fields):
    stream=io.StringIO(newline='');writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader();writer.writerows(rows)
    return stream.getvalue().encode('utf-8-sig')


class MotorConstraintStudies:
    def __init__(self,optimization,storage):
        self.optimization,self.storage=optimization,Path(storage).resolve()

    def path(self,artifact):
        if not isinstance(artifact,str) or not re.fullmatch(r'csens_[a-f0-9]{12}',artifact):raise ValueError('无效约束敏感性记录')
        folder=(self.storage/artifact).resolve()
        if folder.parent!=self.storage:raise ValueError('敏感性记录不属于当前存储')
        return folder

    def inputs(self,dataset):
        data=self.optimization.dataset(dataset)
        if data['id']!=dataset:raise ValueError('敏感性输入与所选数据集身份不符')
        return dict(dataset=data,source_hashes_sha256=sha256(json_bytes(data['source_hashes'])),axes=AXES,
            weights={key:original.DEFAULT_CONFIG[key] for key in ('ripple_weight','iron_loss_weight','efficiency_weight')})

    def run(self,selection):
        if not isinstance(selection,dict) or set(selection)!={'dataset','reference','source_hashes_sha256'}:
            raise ValueError('请选择数据集、明确基准及其来源版本')
        inputs=self.inputs(selection['dataset']);data=inputs['dataset']
        if not data['rows']:raise ValueError('该数据集没有可用于敏感性分析的已有案例')
        if selection['source_hashes_sha256']!=inputs['source_hashes_sha256']:
            raise ValueError('所选数据来源已变化，请刷新后重新选择')
        references={row['case']:row for row in data['references']}
        if selection['reference'] not in references:raise ValueError('请选择该数据集中可用的原基准')
        reference=references[selection['reference']]
        torque_reference=references['original_baseline'] if data['id']=='v8_1' and selection['reference']=='original_control' else reference
        configurations=[]
        for i,values in enumerate(itertools.product(*AXES.values())):
            config=original.validate_config(dict(original.DEFAULT_CONFIG,**dict(zip(AXES,values))))
            ranked=original.screen_rows(data['rows'],reference,torque_reference,config)
            configurations.append(dict(id=f'c{i:02d}',config=config,ranked=ranked,
                meets_limits_count=sum(row['meets_limits'] for row in ranked),
                best_case=next((row['case'] for row in ranked if row['meets_limits']),None),
                is_original_default=all(config[key]==original.DEFAULT_CONFIG[key] for key in AXES)))
        default=next(row for row in configurations if row['is_original_default'])
        if self.inputs(selection['dataset'])['source_hashes_sha256']!=inputs['source_hashes_sha256']:
            raise ValueError('数据来源在计算期间变化，未保存敏感性记录')
        artifact='csens_'+uuid.uuid4().hex[:12]
        report=dict(schema=VERSION,id=artifact,created_utc=datetime.now(timezone.utc).isoformat(),
            dataset=data['id'],model_version=data['version'],measurement_protocol=data['measurement_protocol'],scope=data['scope'],
            reference=selection['reference'],torque_reference=torque_reference['case'],data_usage=dataset_usage(data),
            source_hashes=data['source_hashes'],source_hashes_sha256=inputs['source_hashes_sha256'],
            axes=AXES,weights=inputs['weights'],configuration_count=len(configurations),source_case_count=len(data['rows']),
            configurations=configurations,configurations_with_feasible_case=sum(row['meets_limits_count']>0 for row in configurations),
            original_default_configuration=default['id'],original_default_candidate_count=default['meets_limits_count'],
            producer_sha256=PRODUCER_SHA256,original_scorer_sha256=SCORER_SHA256,
            new_native_solves=0,new_predictions=0,new_independent_materials=0,
            original_optimization_records_changed=False,final_physical_optimization_certified=False)
        summary=[];details=[]
        for row in configurations:
            summary.append(dict(configuration=row['id'],**row['config'],meets_limits_count=row['meets_limits_count'],
                best_case=row['best_case'],is_original_default=row['is_original_default']))
            for case in row['ranked']:
                details.append(dict(configuration=row['id'],case=case['case'],material=case['material'],
                    input_source=case.get('input_source'),input_accepted=case['input_accepted'],meets_limits=case['meets_limits'],score=case['score'],
                    **{name:case['metrics'].get(name) for name in original.METRICS},
                    **{name:case.get(name) for name in ('ripple_reduction_pct','iron_loss_reduction_pct','torque_change_pct','efficiency_change_pctpt')},
                    reasons='；'.join(case['reasons'])))
        files={'study.json':json_bytes(report),'source_dataset.json':json_bytes(data),
            'configurations.csv':csv_bytes(summary,list(summary[0])),
            'case_details.csv':csv_bytes(details,['configuration','case','material','input_source','input_accepted','meets_limits','score',*original.METRICS,
                'ripple_reduction_pct','iron_loss_reduction_pct','torque_change_pct','efficiency_change_pctpt','reasons'])}
        files['README.md']=('''# 原优化约束敏感性

本包从一份明确数据集和基准生成，复用作者原screen_rows/评分权重。固定4×4×3共48组约束，原默认配置同时保留；不改原优化记录或默认门限，不新增仿真、预测或独立材料。配置组数不是实验样本数。

configurations.csv是配置/可行数/首选；case_details.csv保留每组的全部原案例、未接受输入及原因；source_dataset.json和study.json保存原指标、来源哈希、版本、计算协议、使用范围和权重。V8_1原Control仍保留原Baseline转矩下限。下载/重开只读，不重新评分。

允许转矩下降等条件会影响可行性，不能把某一组可行解说成原默认约束已改善。历史估算、原生CSV、零损耗、稳态/本构/损耗验证范围沿用原数据说明，不作新的物理最终排名。完整原CSV可从系统数据中心下载对应数据集；本包只带数据快照和派生表。
''').encode('utf8')
        files['manifest.json']=json_bytes(dict(version=VERSION,files={name:sha256(blob) for name,blob in files.items()},new_native_solves=0))
        self.storage.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='_prepare_',dir=self.storage) as scratch:
            staged=Path(scratch)/artifact;staged.mkdir()
            for name,blob in files.items():(staged/name).write_bytes(blob)
            staged.rename(self.path(artifact))
        return self.get(artifact)

    def content(self,artifact):
        folder=self.path(artifact);names=(*FILES,'manifest.json')
        if any(not (folder/name).resolve().is_relative_to(folder) for name in names):raise ValueError('敏感性文件越出记录目录')
        files={name:(folder/name).read_bytes() for name in names};manifest=json.loads(files['manifest.json'])
        if manifest.get('version')!=VERSION or set(manifest.get('files',{}))!=set(FILES):raise ValueError('敏感性文件清单不符')
        if any(sha256(files[name])!=digest for name,digest in manifest['files'].items()):raise ValueError('保存敏感性文件已改变')
        report=json.loads(files['study.json'])
        if report.get('schema')!=VERSION or report.get('id')!=artifact:raise ValueError('保存敏感性身份不符')
        return report,files

    def get(self,artifact):return self.content(artifact)[0]

    def records(self):
        result=[]
        for folder in sorted(self.storage.glob('csens_*')):
            try:
                data=self.get(folder.name)
                result.append(dict(readable=True,**{key:data[key] for key in ('id','created_utc','dataset','model_version','reference','configuration_count','configurations_with_feasible_case','original_default_candidate_count')}))
            except (OSError,ValueError,KeyError,TypeError) as error:result.append(dict(id=folder.name,readable=False,error=str(error)))
        return sorted(result,key=lambda row:(row.get('created_utc',''),row['id']),reverse=True)

    def download(self,artifact,name):
        _,files=self.content(artifact)
        if name=='study_bundle.zip':
            out=io.BytesIO()
            with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as archive:
                for key,blob in files.items():archive.writestr(key,blob)
            out.seek(0);return out
        if name not in files:raise ValueError('未知敏感性输出文件')
        return io.BytesIO(files[name])


def create_constraint_study_routes(studies):
    bp=Blueprint('motor_constraint_studies',__name__)
    @bp.get('/motor-sensitivity')
    def page():return render_template('motor_constraint_studies.html')
    @bp.get('/api/workbench/optimization/sensitivity')
    def records():return jsonify(studies.records())
    @bp.get('/api/workbench/optimization/sensitivity/inputs/<dataset>')
    def inputs(dataset):return jsonify(studies.inputs(dataset))
    @bp.post('/api/workbench/optimization/sensitivity')
    def run():return jsonify(studies.run(request.get_json())),201
    @bp.get('/api/workbench/optimization/sensitivity/<artifact>')
    def get(artifact):return jsonify(studies.get(artifact))
    @bp.get('/api/workbench/optimization/sensitivity/<artifact>/files/<name>')
    def download(artifact,name):return send_file(studies.download(artifact,name),as_attachment=True,download_name=artifact+'_'+name)
    return bp
