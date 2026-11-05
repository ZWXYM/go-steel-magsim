"""Freeze existing optimizer outputs and linked plans without re-scoring."""
import io
import json
import re
import zipfile

from modules.motor_dataset_export import dataset_usage
from modules.system_data import json_bytes, sha256

FILES = ('analysis.json', 'ranking.csv', 'analysis.md', 'analysis_bundle.zip')


class SavedOptimizationExports:
    def __init__(self, optimization, plans=None):
        self.optimization, self.plans = optimization, plans

    def read(self, artifact):
        if not isinstance(artifact, str) or not re.fullmatch(r'opt_[a-f0-9]{12}', artifact):
            raise ValueError('原筛选记录编号不符')
        files = {name:self.optimization.artifact(artifact,name).read_bytes() for name in FILES}
        report = json.loads(files['analysis.json'])
        if (report['id'] != artifact or report['meets_limits_count'] != sum(row.get('meets_limits') is True for row in report['ranked'])
                or report['best_case'] != next((row['case'] for row in report['ranked'] if row.get('meets_limits') is True), None)):
            raise ValueError('原筛选身份或保存行状态不符')
        if not re.fullmatch(r'v8_1|v8_2_archive|scan_[a-f0-9]{12}',report['dataset']['id']):
            raise ValueError('原筛选的数据集身份不符')
        try:
            with zipfile.ZipFile(io.BytesIO(files['analysis_bundle.zip'])) as archive:
                if set(archive.namelist()) != set(FILES)-{'analysis_bundle.zip'} or len(archive.namelist()) != 3:
                    raise ValueError('原筛选ZIP成员不完整或重复')
                if any(archive.read(name)!=files[name] for name in FILES if name!='analysis_bundle.zip'):
                    raise ValueError('原筛选ZIP与原文件不一致')
        except zipfile.BadZipFile as error:
            raise ValueError('原筛选ZIP不可读') from error
        binding = sha256(json_bytes({name:sha256(blob) for name,blob in files.items()}))
        return report,files,binding

    def catalog(self):
        rows = []
        for path in sorted(self.optimization.storage.glob('opt_*/analysis.json'),reverse=True):
            try:
                report,_,binding = self.read(path.parent.name)
                rows.append(dict(id=report['id'],readable=True,created_utc=report['created_utc'],
                    dataset=report['dataset']['id'],version=report['dataset']['version'],
                    measurement_protocol=report['dataset']['measurement_protocol'],reference=report['reference'],
                    best_case=report['best_case'],meets_limits_count=report['meets_limits_count'],
                    source_binding_sha256=binding,data_usage=dataset_usage(report['dataset'])))
            except (OSError,ValueError,KeyError,TypeError) as error:
                rows.append(dict(id=path.parent.name,readable=False,error=str(error)))
        return sorted(rows,key=lambda row:(row.get('created_utc',''),row['id']),reverse=True)

    def collect(self, selection):
        if not isinstance(selection,dict) or set(selection)!={'id','source_binding_sha256'}:
            raise ValueError('请明确选择已有原优化筛选')
        report,files,binding = self.read(selection['id'])
        if binding != selection['source_binding_sha256']:
            raise ValueError('所选原筛选文件已变化，请刷新后重新选择')
        result={'analysis/'+name:blob for name,blob in files.items()}
        usage=dataset_usage(report['dataset']);result['analysis/data_usage.json']=json_bytes(usage)
        related,unreadable=[],[]
        for plan in self.plans.records() if self.plans else []:
            if plan.get('source_readable') is False:
                unreadable.append(plan['id']);continue
            if plan.get('analysis_id') != report['id']:
                continue
            folder=self.plans.path(plan['id'])
            before={name:(folder/name).read_bytes() for name in ('plan.json','state.json')}
            current=self.plans.get(plan['id'])
            if current.get('analysis_id')!=report['id'] or current.get('status')!='completed':
                raise ValueError('关联原计划与所选筛选不符')
            if current['job_id']!=report['dataset']['id'] or current['reference_case_id']!=report['reference'] or current['config']!=report['config']:
                raise ValueError('关联原计划的数据、基准或原配置不符')
            if any((folder/name).read_bytes()!=blob for name,blob in before.items()):
                raise ValueError('关联原计划在打包期间变化')
            for name,blob in before.items():result['plans/'+plan['id']+'/'+name]=blob
            related.append(dict(id=plan['id'],job_id=current['job_id'],
                plan_sha256=sha256(before['plan.json']),state_sha256=sha256(before['state.json'])))
        # The original files stay exact; the sidecar describes current reading scope.
        _,again,after=self.read(selection['id'])
        if binding!=after or files!=again:
            raise ValueError('原筛选在打包期间变化')
        index=dict(id=report['id'],dataset=report['dataset']['id'],version=report['dataset']['version'],
            measurement_protocol=report['dataset']['measurement_protocol'],reference=report['reference'],
            torque_reference=report.get('torque_reference'),config=report['config'],best_case=report['best_case'],
            meets_limits_count=report['meets_limits_count'],source_binding_sha256=binding,
            dataset_source_hashes=report['dataset']['source_hashes'],data_usage=usage,related_plans=related,
            plan_records_with_unreadable_association=unreadable,new_analyses=0,new_native_solves=0,
            final_physical_optimization_certified=False)
        result['index.json']=json_bytes(index)
        return index,result
