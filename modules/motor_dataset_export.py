"""Read-only snapshots of one author motor dataset and its recorded sources."""
import csv
import hashlib
import io
import json
import re
import zipfile
from pathlib import Path, PurePosixPath

VERSION = 'motor_engineering_dataset_bundle_v2'
METRICS = ('T_avg_Nm','K_T_ripple_pct','P_Fe_W','eta_estimate_pct')
READER_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def digest(blob):
    return hashlib.sha256(blob).hexdigest()


def dataset_usage(dataset):
    historical=dataset['id']=='v8_1'
    native=[row for row in dataset['rows'] if not historical and row.get('input_accepted') is True
        and row.get('input_source')=='official_CSV_verified']
    return dict(version='motor_dataset_usage_v1',
        kind='historical_processed_estimate' if historical else 'per_row_official_source',
        native_csv_accepted_count=len(native),saved_accepted_is_not_native_verification=True,
        independently_measured=False,final_physical_optimization_certified=False,
        note=('V8_1历史导出包含谐波处理/转矩列同步和材料宏观铁损估算；原判据通过不等于原生CSV验证，不能作为新增损耗改善证据。'
              if historical else '原生CSV来源与原数值判据逐行核对；报告估算、周期范围及损耗边界保持，不视为独立实测认证。'))


def metric_csv(dataset, rows):
    stream=io.StringIO(newline='');writer=csv.writer(stream)
    writer.writerow(['dataset','model_version','measurement_protocol','case','material','input_accepted',
        *METRICS,'input_source','input_error'])
    for row in rows:
        metrics=row.get('metrics') or {}
        writer.writerow([dataset['id'],dataset['version'],dataset['measurement_protocol'],row.get('case'),
            row.get('material'),row.get('input_accepted'),*[metrics.get(k) for k in METRICS],
            row.get('input_source',row.get('source')),row.get('input_error') or row.get('reason') or row.get('error')])
    return stream.getvalue().encode('utf-8-sig')


def dataset_bundle(manager, dataset_id):
    """Keep all rows, accepted rows and reference choices without new scoring."""
    dataset=manager.dataset(dataset_id)
    if dataset['id']!=dataset_id:raise ValueError('数据包与所选数据集身份不符')
    files={'dataset.json':json.dumps(dataset,ensure_ascii=False,indent=2,sort_keys=True,allow_nan=False).encode('utf8')}
    rows=dataset['rows'];accepted=[row for row in rows if row.get('input_accepted') is True]
    files['metrics.csv']=metric_csv(dataset,rows)
    files['accepted_metrics.csv']=metric_csv(dataset,accepted)
    usage=dataset_usage(dataset)
    native=[row for row in accepted if dataset_id!='v8_1' and row.get('input_source')=='official_CSV_verified']
    files['native_csv_metrics.csv']=metric_csv(dataset,native)
    files['data_usage.json']=json.dumps(usage,ensure_ascii=False,indent=2,sort_keys=True).encode('utf8')
    files['reference_choices.csv']=metric_csv(dataset,dataset['references'])
    sources=[]
    for name,expected in dataset['source_hashes'].items():
        scan_source=isinstance(dataset_id,str) and re.fullmatch(r'scan_[a-f0-9]{12}',dataset_id)
        # Historical scan keys combine a POSIX case prefix with Windows CSV names.
        portable=name.replace('\\','/') if scan_source else name
        relative=PurePosixPath(portable)
        if not name or relative.is_absolute() or '..' in relative.parts or '\\' in portable or ':' in portable or relative.as_posix()!=portable:
            raise ValueError('数据集来源路径不符')
        if dataset_id=='v8_1':
            if name!='motor/v8_1/outputs/v8_1_material_comparison.json':raise ValueError('V8_1来源不属于固定输出')
            path=manager.source_file('v8_1',relative.name)
        elif dataset_id=='v8_2_archive':
            if len(relative.parts)<3 or relative.parts[0]!='archive':raise ValueError('归档来源不属于该版本')
            path=manager.motor.archive_file(relative.parts[1],PurePosixPath(*relative.parts[2:]).as_posix())
        elif scan_source:
            folder=manager.motor.path(dataset_id).resolve();path=(folder/portable).resolve()
            if not path.is_relative_to(folder):raise ValueError('扫描来源越出所选任务')
        else:raise ValueError('未知数据集来源')
        blob=Path(path).read_bytes()
        if digest(blob)!=expected:raise ValueError('数据集原文件已改变：'+name)
        included=relative.suffix.casefold() in ('.json','.csv','.md')
        item=dict(source=name,sha256=expected,bytes=len(blob),included=included)
        if included:
            member='sources/'+portable
            if member in files:raise ValueError('数据集来源成员重复')
            files[member]=blob;item['member']=member
        else:item['omission_reason']='Native projects, profiles, logs and binary caches stay outside this analysis bundle'
        sources.append(item)
    source_snapshot=dict(dataset=dataset_id,model_version=dataset['version'],measurement_protocol=dataset['measurement_protocol'],
        sources=sources,scope=dataset['scope'],row_count=len(rows),saved_accepted_count=len(accepted),
        reference_choice_count=len(dataset['references']),data_usage=usage,
        native_csv_accepted_count=len(native),new_native_solves=0,new_analyses=0,new_predictions=0)
    files['source_snapshot.json']=json.dumps(source_snapshot,ensure_ascii=False,indent=2,sort_keys=True).encode('utf8')
    files['README.md']=(f"# 单数据集工程分析包\n\n数据集：{dataset_id}\n\n版本：{dataset['version']}；协议：{dataset['measurement_protocol']}\n\n"
        f"{dataset['scope']}\n\n保存条目{len(rows)}项，其中{len(accepted)}项通过各自原判据。\n\n"
        'metrics.csv保留所有条目及原始异常；accepted_metrics.csv只列原input_accepted=true的条目，不改变门限。'
        'native_csv_metrics.csv只列官方CSV来源且通过原判据的条目；V8_1为历史处理/估算，不能因此进入该原生子集。'
        'data_usage.json说明使用范围。'+usage['note']+'\n\n'
        'reference_choices.csv列页面可选基准，其中可能含原baseline/control以及metrics.csv中的条目，不能另算作独立实验。\n\n'
        '来源文件按原字节保留在sources/，每个文件的哈希及未包含原因见source_snapshot.json。未附带的官方CSV不从其他版本补齐。'
        '不同数据集/模型/协议不得混作同一次材料排名；铁损与效率仍按原工程报告/估算范围使用，不是新损耗校准或实测认证。\n\n'
        '本包只读取已保存数据，不新增筛选、预测、原生求解或排名，不修改原文件。\n').encode('utf8')
    files['manifest.json']=json.dumps(dict(version=VERSION,dataset=dataset_id,reader_sha256=READER_SHA256,
        files={name:digest(blob) for name,blob in files.items()},new_native_solves=0,new_analyses=0,
        scope='One saved engineering dataset and recorded source bytes; no new physical validation'),indent=2,sort_keys=True).encode('utf8')
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as archive:
        for name,blob in files.items():archive.writestr(name,blob)
    out.seek(0);return out
