"""Derived batch summary; preserve the verified package's original bytes."""
import io
import csv
import json
import math
import re
import zipfile
from pathlib import PurePosixPath, Path

from modules.material_library import sha
from modules.material_input_view import MaterialInputView, PARAMETERS

VERSION='calibrated_batch_summary_v4'


def torque_findings(metrics):
    """Explain the existing saved acceptance flag; never change its thresholds."""
    if metrics.get('torque_numerical_acceptance') is not False:return []
    reasons=[]
    for key,condition,text in (
        ('T_avg_Nm',lambda v:v<=0,'平均转矩非正'),
        ('T_min_Nm',lambda v:v<=0,'存在非正转矩点'),
        ('T_max_Nm',lambda v:v>=10,'最高转矩达到原10 N·m上限'),
        ('K_T_ripple_pct',lambda v:v>=100,'脉动达到原100%上限')):
        value=metrics.get(key)
        if isinstance(value,(int,float)) and condition(value):reasons.append(text)
    if metrics.get('K_T_ripple_pct') is None:reasons.append('脉动不可计算')
    stability=metrics.get('periodic_torque_stability')
    if stability and stability.get('passed') is False:reasons.append('原周期间转矩差判据未通过')
    return reasons or ['原转矩数值判据未通过']


def metrics_nonpositive_points(metrics):
    """Locate original nonpositive points; absent arrays remain unknown."""
    if 'time_ms' not in metrics and 'torque_Nm' not in metrics:return None
    times=metrics.get('time_ms');torques=metrics.get('torque_Nm')
    if not isinstance(times,list) or not isinstance(torques,list) or len(times)!=len(torques):
        raise ValueError('原转矩时间与数据数组不完整')
    if not times or any(not isinstance(v,(int,float)) or isinstance(v,bool) or not math.isfinite(v) for v in times+torques):
        raise ValueError('原转矩点不是有限数值')
    if any(a>=b for a,b in zip(times,times[1:])):raise ValueError('原转矩时间无序')
    window=metrics.get('selected_window_ms')
    if window is not None and (not isinstance(window,(list,tuple)) or len(window)!=2 or any(not isinstance(v,(int,float)) or not math.isfinite(v) for v in window) or window[0]>window[1]):
        raise ValueError('原转矩指标窗口无效')
    return [dict(index=i,time_ms=t,torque_Nm=y,within_metric_window=(window[0]-1e-8<=t<=window[1]+1e-8) if window else None)
            for i,(t,y) in enumerate(zip(times,torques)) if y<=0]


def acceptance_text(metrics):
    flag=metrics.get('torque_numerical_acceptance')
    return '未通过：'+'、'.join(torque_findings(metrics)) if flag is False else '通过原转矩判据' if flag is True else '未报告'


def cell(value):
    return str(value).replace('|','\\|').replace('\r',' ').replace('\n',' ')


def number(value):
    if value is None:return '—'
    value=float(value)
    if not math.isfinite(value):raise ValueError('摘要指标不是有限数值')
    return format(value,'.6f')


def result_counts(record):
    results=[row for row in record['items'] if row.get('result_available')]
    return dict(reused=sum(row['status']=='reused_result' for row in results),
        passed=sum(row.get('metrics',{}).get('torque_numerical_acceptance') is True for row in results),
        failed=sum(row.get('metrics',{}).get('torque_numerical_acceptance') is False for row in results),
        unknown=sum(row.get('metrics',{}).get('torque_numerical_acceptance') is not True
                    and row.get('metrics',{}).get('torque_numerical_acceptance') is not False for row in results))


def material_inputs(batches, record):
    """Resolve saved names only when the library still matches the frozen batch."""
    entries=[]
    for row in record['items']:
        source=row['source']
        entry=dict(item=row['id'],selection=row['selection'],material_name=source['material_name'],
            identity_status='unavailable',source_material_id=None,input_params=None,
            effective_bank_sha256=source['effective_bank_sha256'],excluded_grades=source['excluded_grades'])
        try:
            detail=MaterialInputView(batches.chain.library).detail(row['selection']['calibration'],row['selection']['key'])
            for key in ('material_name','parent_bank_sha256','effective_bank_sha256','training_grades','excluded_grades'):
                if source[key]!=detail[key]:raise ValueError('保存材料与冻结批量来源不符：'+key)
            for name,key in (('material.amat','AMAT_sha256'),('metadata.json','metadata_sha256')):
                if source[key]!=detail['files'][name]['sha256']:raise ValueError('保存材料文件与冻结批量哈希不符：'+name)
            entry.update(identity_status='verified_saved_input',source_material_id=detail['source_material_id'],
                display_name=detail['display_name'],input_params=detail['input_params'],
                input_parameter_source=detail['input_parameter_source'],input_identity_scope=detail['input_identity_scope'],
                source_files_sha256={name:info['sha256'] for name,info in detail['files'].items()})
        except (AttributeError,ValueError,KeyError,TypeError,OSError) as error:
            # Old packages remain downloadable; unavailable identity never becomes measured data.
            entry['identity_error']='保存输入身份未能核对：'+str(error)
        entries.append(entry)
    return dict(version='batch_material_inputs_v1',batch=record['id'],entries=entries,
        scope='saved_model_parameters_not_measurement_certification',new_native_solves=0,new_predictions=0)


def material_inputs_csv(identities):
    stream=io.StringIO(newline='');writer=csv.writer(stream)
    writer.writerow(['item','source_material_id','material_name','calibration','key',*PARAMETERS,
                     'effective_bank_sha256','excluded_grades','identity_status','identity_error'])
    for row in identities['entries']:
        params=row.get('input_params') or {}
        writer.writerow([row['item'],row['source_material_id'],row['material_name'],row['selection']['calibration'],
            row['selection']['key'],*[params.get(key) for key in PARAMETERS],row['effective_bank_sha256'],
            json.dumps(row['excluded_grades'],ensure_ascii=False),row['identity_status'],row.get('identity_error','')])
    return stream.getvalue().encode('utf-8-sig')


def verified_operating_point(item, execution):
    """Bind the saved operating point to this batch item before deriving power."""
    def finite(value):
        return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value)
    source=item.get('source') or {};metrics=item.get('metrics') or {}
    result=execution.get('result') or {};actual=result.get('metrics') or {};records=item.get('records') or {}
    if (item.get('result_available') is not True or execution.get('result_available') is not True
            or not isinstance(item.get('execution_id'),str) or not re.fullmatch(r'bhexec_[a-f0-9]{12}',item['execution_id'])
            or execution.get('id')!=item['execution_id']
            or (records.get('execution') or {}).get('id')!=execution['id']
            or (records.get('analysis_plan') or {}).get('id')!=execution.get('analysis_plan_id')
            or not execution.get('analysis_plan_id')
            or result.get('native_configuration_verified') is not True or result.get('native_analyze_succeeded') is not True):
        raise ValueError('执行身份、分析计划或原生工况未核验')
    for key in ('material_name','effective_bank_sha256'):
        if not isinstance(source.get(key),str) or not source[key] or execution.get(key)!=source[key]:
            raise ValueError('执行与本条材料来源不符：'+key)
    if (result.get('material_name')!=source['material_name']
            or not isinstance(source.get('excluded_grades'),list)
            or not isinstance(execution.get('excluded_grades'),list)
            or execution['excluded_grades']!=source['excluded_grades']
            or not isinstance(item.get('measurement_protocol'),str) or not item['measurement_protocol']
            or execution.get('measurement_protocol')!=item['measurement_protocol']
            or not isinstance(item.get('model_version'),str) or not item['model_version']
            or result.get('model_version')!=item['model_version']):
        raise ValueError('材料、排除范围、周期或结果版本不符')
    csv_hash=metrics.get('torque_csv_sha256')
    if (not isinstance(csv_hash,str) or not re.fullmatch(r'[a-f0-9]{64}',csv_hash)
            or actual.get('torque_csv_sha256')!=csv_hash
            or not finite(metrics.get('T_avg_Nm')) or not finite(actual.get('T_avg_Nm'))
            or actual['T_avg_Nm']!=metrics['T_avg_Nm']):
        raise ValueError('官方转矩CSV或平均转矩与本条结果不符')
    speed=(execution.get('requested_protocol') or {}).get('speed_rpm')
    if not finite(speed) or speed<=0:raise ValueError('保存转速缺失或无效')
    power=actual['T_avg_Nm']*2*math.pi*speed/60
    if not math.isfinite(power):raise ValueError('功率折算不是有限数值')
    return dict(speed_rpm=speed,electromagnetic_mechanical_power_W=power)


def operating_points(batches, record):
    entries=[];saved={}
    for item in record['items']:
        metrics=(item.get('metrics') or {}) if item.get('result_available') else {}
        entry=dict(item=item['id'],material_name=item['source']['material_name'],
            execution=item.get('execution_id'),analysis_plan=(item.get('records',{}).get('analysis_plan') or {}).get('id'),
            measurement_protocol=item.get('measurement_protocol'),model_version=item.get('model_version'),
            effective_bank_sha256=item['source']['effective_bank_sha256'],excluded_grades=item['source']['excluded_grades'],
            torque_csv_sha256=metrics.get('torque_csv_sha256'),T_avg_Nm=metrics.get('T_avg_Nm'),
            torque_numerical_acceptance=metrics.get('torque_numerical_acceptance'),speed_rpm=None,
            electromagnetic_mechanical_power_W=None,status='unavailable',error='尚无实际转矩结果')
        if item.get('result_available'):
            try:
                key=item['execution_id']
                if key not in saved:saved[key]=batches.chain.execution.get(key)
                entry.update(verified_operating_point(item,saved[key]),status='verified_saved_operating_point',error=None)
            except (AttributeError,KeyError,TypeError,ValueError,OSError,OverflowError) as error:
                entry.update(status='unverified',error='保存工况未能核对：'+str(error))
        entries.append(entry)
    return dict(version='batch_operating_points_v1',batch=record['id'],entries=entries,
        power_definition='T_avg_Nm * 2*pi * saved_speed_rpm / 60; electromagnetic mechanical power, before mechanical losses; not nameplate rating',
        scope='Derived from verified saved execution and official torque CSV; not an efficiency or material ranking',
        new_native_solves=0,new_predictions=0,efficiency_enabled=False,optimization_ranking_enabled=False)


def operating_points_csv(points):
    stream=io.StringIO(newline='');writer=csv.writer(stream)
    fields=('item','material_name','execution','analysis_plan','measurement_protocol','model_version',
        'effective_bank_sha256','excluded_grades','T_avg_Nm','speed_rpm','electromagnetic_mechanical_power_W',
        'torque_numerical_acceptance','status','error','torque_csv_sha256')
    writer.writerow(fields)
    for entry in points['entries']:
        writer.writerow([json.dumps(entry[key],ensure_ascii=False) if key=='excluded_grades' else entry.get(key) for key in fields])
    return stream.getvalue().encode('utf-8-sig')


def summary_markdown(record, identities=None, operating=None):
    counts=result_counts(record)
    inputs={entry['item']:entry for entry in (identities or {}).get('entries',[])}
    lines=['# 批量任务摘要','',f"批量：`{record['id']}`；状态：{cell(record['status'])}。",
        f"协议：`{record['measurement_protocol']}`。材料{record['totals']['materials']}项，"
        f"可读取实际转矩{record['totals']['actual_torque_results']}项，本批新增完成{record['totals']['new_completed_motor_solves']}项，复用{counts['reused']}项。",
        f"原数值判据：通过{counts['passed']}项，未通过{counts['failed']}项，未报告{counts['unknown']}项。计算完成与数值判据分别列示。",'',
        '| 材料 / 来源 | 阶段 | 平均转矩 N·m | 最小 / 最大 N·m | 脉动 % | 原数值判据 |',
        '| --- | --- | ---: | ---: | ---: | --- |']
    for row in record['items']:
        m=row.get('metrics') if row.get('result_available') else None
        m=m or {}
        lines.append('| '+ ' | '.join([cell(inputs.get(row['id'],{}).get('display_name',row['source']['material_name']))+' / '+cell(row['selection']['key']),
            cell(row['status']),number(m.get('T_avg_Nm')),number(m.get('T_min_Nm'))+' / '+number(m.get('T_max_Nm')),
            number(m.get('K_T_ripple_pct')),cell(acceptance_text(m))])+' |')
    lines+=['','## 逐项来源与异常','']
    for row in record['items']:
        lines += [f"- {cell(row['id'])}：{cell(row['source']['material_name'])}；校准记录 `{cell(row['selection']['calibration'])}`，"
            f"材料键 `{cell(row['selection']['key'])}`。",
            f"  有效库 `{cell(row['source']['effective_bank_sha256'])}`；排除样品：{cell('、'.join(row['source']['excluded_grades']) or '无')}。"]
        identity=inputs.get(row['id'])
        if identity and identity['identity_status']=='verified_saved_input':
            lines.append('  原输入代号：'+cell(identity['source_material_id'])+'；建模参数：'+'，'.join(key+'='+str(identity['input_params'][key]) for key in PARAMETERS)+'。')
        elif identity:lines.append('  '+cell(identity['identity_error'])+'；保留AMAT名称与原来源，不补写参数。')
        if row.get('result_available'):
            lines.append(f"  实际执行 `{cell(row['execution_id'])}`，版本 `{cell(row['model_version'])}`；原CSV见 `{row['id']}/Torque Plots.csv`。")
            points=metrics_nonpositive_points(row['metrics'])
            if points:
                lines.append('  原始非正转矩点：'+'；'.join(f"{number(p['time_ms'])} ms / {number(p['torque_Nm'])} N·m（{'指标窗口内' if p['within_metric_window'] else '指标窗口外' if p['within_metric_window'] is False else '窗口未报告'}）" for p in points)+'。')
        elif row.get('error'):
            lines.append('  异常：'+cell(row['error']))
        else:lines.append('  尚无可读取的实际转矩结果。')
    if operating:
        lines+=['','## 保存工况与转矩折算功率','',
            '使用本条执行已核验的转速，按平均转矩×2πn/60折算电磁机械功率，未扣机械损耗，不是铭牌额定功率。工况无法核对时留空；原数值异常仍保留。',
            '| 条目 | 保存转速 r/min | 折算功率 W | 工况核对 |',
            '| --- | ---: | ---: | --- |']
        for entry in operating['entries']:
            lines.append('| '+' | '.join([cell(entry['item']),number(entry['speed_rpm']),
                number(entry['electromagnetic_mechanical_power_W']),cell('已核对' if entry['status']=='verified_saved_operating_point' else entry['error'])])+' |')
    lines+=['','保持选择顺序，未做材料排名。原转矩点不平滑、不重采样；标定、留出与预测身份分别保留。',
        '仅汇总已保存转矩，不补损耗或效率，不提交任何原生求解。','']
    return '\n'.join(lines)


def report_bundle(batches, artifact):
    source=batches.bundle(artifact).getvalue()
    with zipfile.ZipFile(io.BytesIO(source)) as archive:
        names=archive.namelist()
        if len(names)!=len(set(names)):raise ValueError('源批量包存在重复成员')
        if any(PurePosixPath(n).is_absolute() or '..' in PurePosixPath(n).parts or '\\' in n or ':' in n for n in names):
            raise ValueError('源批量包路径不符')
        files={name:archive.read(name) for name in names}
    original=files.pop('manifest.json');manifest=json.loads(original)
    if any(name in files for name in ('summary.md','review.csv','source_manifest.json','material_inputs.json','material_inputs.csv','operating_points.json','operating_points.csv')):
        raise ValueError('源批量包已包含派生报告，未覆盖')
    if set(files)!=set(manifest['files']) or any(sha(files[n])!=h for n,h in manifest['files'].items()):
        raise ValueError('源批量包文件哈希不符，未生成摘要')
    record=json.loads(files['batch.json'])
    if any(obj.get(key) is not False for obj in (record,manifest)
            for key in ('efficiency_enabled','optimization_ranking_enabled')):
        raise ValueError('批量摘要的转矩范围不符')
    identities=material_inputs(batches,record)
    files['material_inputs.json']=json.dumps(identities,ensure_ascii=False,indent=2).encode('utf8')
    files['material_inputs.csv']=material_inputs_csv(identities)
    points=operating_points(batches,record)
    files['operating_points.json']=json.dumps(points,ensure_ascii=False,indent=2,allow_nan=False).encode('utf8')
    files['operating_points.csv']=operating_points_csv(points)
    files['summary.md']=summary_markdown(record,identities,points).encode('utf8')
    review=io.StringIO(newline='');writer=csv.writer(review)
    writer.writerow(['item','material','execution','torque_numerical_acceptance','findings','nonpositive_points_count','nonpositive_times_ms','nonpositive_torque_Nm','nonpositive_points_in_metric_window'])
    for row in record['items']:
        m=row.get('metrics',{}) if row.get('result_available') else {}
        points=metrics_nonpositive_points(m)
        writer.writerow([row['id'],row['source']['material_name'],row.get('execution_id',''),
            m.get('torque_numerical_acceptance'),'；'.join(torque_findings(m)),len(points) if points is not None else '',
            json.dumps([p['time_ms'] for p in points]) if points is not None else '',
            json.dumps([p['torque_Nm'] for p in points]) if points is not None else '',
            sum(p['within_metric_window'] is True for p in points) if points is not None and all(p['within_metric_window'] is not None for p in points) else ''])
    files['review.csv']=review.getvalue().encode('utf-8-sig')
    files['source_manifest.json']=original
    files['manifest.json']=json.dumps(dict(version=VERSION,source_manifest_sha256=sha(original),
        producer_sha256=sha(Path(__file__).read_bytes()),files={n:sha(b) for n,b in files.items()},
        original_members_preserved=True,new_native_solves=0,efficiency_enabled=False,
        optimization_ranking_enabled=False),ensure_ascii=False,indent=2).encode('utf8')
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as archive:
        for name,blob in files.items():archive.writestr(name,blob)
    out.seek(0);return out
