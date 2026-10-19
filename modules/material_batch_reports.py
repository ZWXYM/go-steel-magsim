"""Derived batch summary; preserve the verified package's original bytes."""
import io
import csv
import json
import math
import zipfile
from pathlib import PurePosixPath, Path

from modules.material_library import sha

VERSION='calibrated_batch_summary_v2'


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


def summary_markdown(record):
    lines=['# 批量任务摘要','',f"批量：`{record['id']}`；状态：{cell(record['status'])}。",
        f"协议：`{record['measurement_protocol']}`。材料{record['totals']['materials']}项，"
        f"可读取实际转矩{record['totals']['actual_torque_results']}项，本批新增完成{record['totals']['new_completed_motor_solves']}项。",'',
        '| 材料 / 来源 | 阶段 | 平均转矩 N·m | 最小 / 最大 N·m | 脉动 % | 原数值判据 |',
        '| --- | --- | ---: | ---: | ---: | --- |']
    for row in record['items']:
        m=row.get('metrics') if row.get('result_available') else None
        m=m or {}
        lines.append('| '+ ' | '.join([cell(row['source']['material_name'])+' / '+cell(row['selection']['key']),
            cell(row['status']),number(m.get('T_avg_Nm')),number(m.get('T_min_Nm'))+' / '+number(m.get('T_max_Nm')),
            number(m.get('K_T_ripple_pct')),cell(acceptance_text(m))])+' |')
    lines+=['','## 逐项来源与异常','']
    for row in record['items']:
        lines += [f"- {cell(row['id'])}：{cell(row['source']['material_name'])}；校准记录 `{cell(row['selection']['calibration'])}`，"
            f"材料键 `{cell(row['selection']['key'])}`。",
            f"  有效库 `{cell(row['source']['effective_bank_sha256'])}`；排除样品：{cell('、'.join(row['source']['excluded_grades']) or '无')}。"]
        if row.get('result_available'):
            lines.append(f"  实际执行 `{cell(row['execution_id'])}`，版本 `{cell(row['model_version'])}`；原CSV见 `{row['id']}/Torque Plots.csv`。")
            points=metrics_nonpositive_points(row['metrics'])
            if points:
                lines.append('  原始非正转矩点：'+'；'.join(f"{number(p['time_ms'])} ms / {number(p['torque_Nm'])} N·m（{'指标窗口内' if p['within_metric_window'] else '指标窗口外' if p['within_metric_window'] is False else '窗口未报告'}）" for p in points)+'。')
        elif row.get('error'):
            lines.append('  异常：'+cell(row['error']))
        else:lines.append('  尚无可读取的实际转矩结果。')
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
    if 'summary.md' in files or 'source_manifest.json' in files:
        raise ValueError('源批量包已包含派生报告，未覆盖')
    if set(files)!=set(manifest['files']) or any(sha(files[n])!=h for n,h in manifest['files'].items()):
        raise ValueError('源批量包文件哈希不符，未生成摘要')
    record=json.loads(files['batch.json'])
    if any(obj.get(key) is not False for obj in (record,manifest)
            for key in ('efficiency_enabled','optimization_ranking_enabled')):
        raise ValueError('批量摘要的转矩范围不符')
    files['summary.md']=summary_markdown(record).encode('utf8')
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
