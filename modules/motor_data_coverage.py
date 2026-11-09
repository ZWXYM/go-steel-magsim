"""Version-separated saved motor rows and exact CSV-content reuse, read only."""
import csv,io,re
from datetime import datetime,timezone
from flask import Blueprint,jsonify,send_file
from modules.motor_dataset_export import DATASET_ID_PATTERN,METRICS
from modules.system_data import json_bytes,sha256

VERSION='saved_motor_case_coverage_v1'


class MotorDataCoverage:
    def __init__(self,optimization):self.optimization=optimization

    def snapshot(self):
        rows,errors=[],[];readable=set()
        try:catalog=self.optimization.datasets()
        except (OSError,ValueError,KeyError,TypeError) as e:catalog=[];errors.append(dict(dataset=None,error=str(e)))
        seen=set()
        for item in catalog:
            identity=item.get('id')
            try:
                if not isinstance(identity,str) or not re.fullmatch(DATASET_ID_PATTERN,identity) or identity in seen:raise ValueError('目录中的数据集身份无效或重复')
                seen.add(identity);data=self.optimization.dataset(identity)
                if data['id']!=identity:raise ValueError('返回数据集身份与目录不符')
                source=data['source_hashes'];local=[];cases=set()
                if not isinstance(source,dict) or any(not isinstance(n,str) or not isinstance(h,str) or not re.fullmatch('[a-f0-9]{64}',h) for n,h in source.items()):raise ValueError('数据来源哈希格式不符')
                for row in data['rows']:
                    case=row['case']
                    if not isinstance(case,str) or not case or case in cases:raise ValueError('数据集中的案例身份重复或缺失')
                    cases.add(case)
                    prefix=('archive/'+case+'/' if identity=='v8_2_archive' else 'bound_archive/'+case+'/' if identity=='v8_2_bound_archive' else case+'/')
                    sources={n:h for n,h in source.items() if identity!='v8_1' and n.replace('\\','/').startswith(prefix) and n.casefold().endswith('.csv')}
                    content='csv_'+sha256(json_bytes(sorted(sources.values()))) if sources else None
                    native=row.get('input_source')=='official_CSV_verified' and bool(sources) and identity!='v8_1'
                    local.append(dict(dataset=identity,model_version=data['version'],measurement_protocol=data['measurement_protocol'],
                        case=case,material=row.get('material'),input_accepted=row.get('input_accepted'),input_source=row.get('input_source'),
                        native_csv_verified=native,historical_processed_estimate=identity=='v8_1',
                        metrics={name:row.get('metrics',{}).get(name) for name in METRICS},
                        input_error=row.get('input_error') or row.get('reason') or row.get('error'),
                        csv_source_file_count=len(sources),csv_sources=sources,csv_content_group=content,
                        dataset_source_hashes_sha256=sha256(json_bytes(source)),scope=data['scope']))
                rows.extend(local);readable.add(identity)
            except (OSError,ValueError,KeyError,TypeError) as e:errors.append(dict(dataset=identity,error=str(e)))
        groups={}
        for row in rows:
            if row['csv_content_group']:groups.setdefault(row['csv_content_group'],[]).append(dict(dataset=row['dataset'],case=row['case']))
        for row in rows:
            row['same_csv_content_records']=len(groups.get(row['csv_content_group'],[]))
        csv_rows=[row for row in rows if row['csv_content_group']]
        return dict(schema=VERSION,created_utc=datetime.now(timezone.utc).isoformat(),rows=rows,errors=errors,
            csv_content_groups=[dict(id=k,records=v) for k,v in sorted(groups.items())],
            counts=dict(complete=not errors,dataset_count=len(readable),saved_rows=len(rows),
                original_accepted_rows=sum(r['input_accepted'] is True for r in rows),
                native_csv_accepted_rows=sum(r['native_csv_verified'] and r['input_accepted'] is True for r in rows),
                rows_with_csv_sources=len(csv_rows),distinct_csv_content_sets=len(groups),
                repeated_csv_content_rows=len(csv_rows)-len(groups),independent_measured_samples=None),
            scope='逐版本保存案例清点，不混拼排名。相同CSV内容组只表示完整源CSV哈希集合相同，不能推断独立试验/材料数；不同CSV也不自动算独立实测。历史处理、异常与原值保持。',
            new_native_solves=0,new_analyses=0,new_predictions=0,new_training=0)

    def csv(self,snapshot=None):
        data=snapshot if snapshot is not None else self.snapshot();out=io.StringIO(newline='')
        fields=['dataset','model_version','measurement_protocol','case','material','input_accepted','input_source',
            'native_csv_verified','historical_processed_estimate',*METRICS,'csv_source_file_count','csv_content_group',
            'same_csv_content_records','dataset_source_hashes_sha256','input_error','csv_sources','scope']
        w=csv.DictWriter(out,fieldnames=fields);w.writeheader()
        for row in data['rows']:
            values={k:row.get(k) for k in fields};values.update(row['metrics']);values['csv_sources']=json_bytes(row['csv_sources']).decode('utf8');w.writerow(values)
        for error in data['errors']:w.writerow(dict(dataset=error['dataset'],input_error=error['error'],scope='该数据集未纳入本次可读清单'))
        return out.getvalue().encode('utf-8-sig')


def create_motor_coverage_routes(manager):
    bp=Blueprint('motor_data_coverage',__name__)
    @bp.get('/api/workbench/system-data/motor-coverage')
    def snapshot():
        response=jsonify(manager.snapshot());response.cache_control.no_store=True;return response
    @bp.get('/api/workbench/system-data/motor-coverage.csv')
    def download():
        response=send_file(io.BytesIO(manager.csv()),as_attachment=True,download_name='saved_motor_case_coverage.csv',mimetype='text/csv')
        response.cache_control.no_store=True;return response
    return bp
