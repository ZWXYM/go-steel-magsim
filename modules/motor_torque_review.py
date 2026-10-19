"""Read-only review of saved torque; never solve, repair or change acceptance."""
import math
import re
from pathlib import Path

from modules.material_library import sha
from modules.material_batch_reports import torque_findings, metrics_nonpositive_points
from modules.motor_model_audit import blocks, value

VERSION = 'saved_motor_torque_review_v1'
SETTINGS = ('StopTime','TimeStep','NonlinearSolverResidual','UseNonLinearIterNum',
            'MinIterNum','MaxIterNum','UseAdaptiveTimeStep','SmoothBHCurve')


def profile_rows(text):
    """Read balanced native time groups; residual units deliberately uninterpreted."""
    text=text.replace("\r\n","\n").replace("\r","\n")
    rows = []
    for body in blocks(text, 'ProfileGroup'):
        match = re.fullmatch(r'Time - ([0-9.eE+-]+)s', value(body, 'Name') or '')
        if not match:
            continue
        body = body.replace("\\'", "'")
        residual = re.search(r"'Final Residual', ([0-9.eE+-]+)", body)
        calls = re.search(r"'Calls', (\d+)", body)
        time = float(match[1])
        number = float(residual[1]) if residual else None
        if not math.isfinite(time) or time < 0 or (number is not None and not math.isfinite(number)):
            raise ValueError('原生profile包含无效时间或残差')
        rows.append(dict(time_s=time, final_residual_raw=number,
                         matrix_calls=int(calls[1]) if calls else None))
    if any(a['time_s'] >= b['time_s'] for a,b in zip(rows, rows[1:])):
        raise ValueError('原生profile时间组重复或无序')
    return rows


def review_execution(executions, artifact):
    # Existing reader verifies official CSV, saved project and result binding.
    record = executions.get(artifact)
    if not record.get('result_available'):
        raise ValueError('实际转矩结果保存完成后才能读取检查记录')
    folder = executions.path(artifact)
    saved = record['result'];metrics = saved['metrics']
    project = folder/'case/motor.aedt';blob = project.read_bytes()
    if sha(blob) != saved['solved_project_sha256']:
        raise ValueError('保存工程在检查前改变')
    source_hashes = {'case/motor.aedt':sha(blob),
                     'case/reports/Torque Plots.csv':metrics['torque_csv_sha256'],
                     'result.json':sha((folder/'result.json').read_bytes())}
    text = blob.decode('utf-8-sig').replace('\r\n','\n').replace('\r','\n');setup = blocks(text,'Setup1')
    if len(setup) != 1:
        raise ValueError('保存Setup1不唯一')
    settings = {key:value(setup[0],key) for key in SETTINGS}
    expressions = sorted(set(expr for body in blocks(text,'WB_BH_Torque')
                              for expr in re.findall(r"(?m)^\s*Expr='([^'\r\n]+)'",body)))
    profiles=[];warnings=[];times=[t/1000 for t in metrics['time_ms']]
    paths=sorted((folder/'case/motor.aedtresults').rglob('*.profile'))
    if len(paths)>20:
        warnings.append('profile文件超过20份，仅读取前20份，未据此认证收敛')
    for path in paths[:20]:
        if not path.resolve().is_relative_to((folder/'case').resolve()):
            warnings.append('忽略工程目录外的profile');continue
        relative=path.relative_to(folder).as_posix()
        raw=path.read_bytes();source_hashes[relative]=sha(raw)
        try:
            content=raw.decode('utf-8-sig');rows=profile_rows(content)
            matches=len(rows)==len(times) and all(math.isclose(row['time_s'],t,rel_tol=1e-8,abs_tol=1e-10)
                                                     for row,t in zip(rows,times))
            profiles.append(dict(path=relative,sha256=sha(raw),time_grid_matches=matches,rows=rows,
                normal_completion_text_present='Normal Completion' in content))
        except (ValueError,UnicodeError) as exc:
            warnings.append('无法读取profile '+relative+'：'+str(exc))
    matching=[p for p in profiles if p['time_grid_matches']]
    chosen=matching[0] if len(matching)==1 else None
    if not chosen:
        warnings.append('没有唯一匹配官方CSV时间网格的profile；异常点不绑定残差')
    points=[]
    for point in metrics_nonpositive_points(metrics) or []:
        i=point['index']
        neighbor=lambda j:dict(index=j,time_ms=metrics['time_ms'][j],torque_Nm=metrics['torque_Nm'][j])
        points.append(dict(point,previous=neighbor(i-1) if i else None,
            next=neighbor(i+1) if i+1<len(times) else None,native_profile_row=chosen['rows'][i] if chosen else None))
    # Detect changes during reading without rewriting any artifact or raw source.
    for name,digest in source_hashes.items():
        if sha((folder/name).read_bytes())!=digest:
            raise ValueError('检查来源在读取期间改变：'+name)
    return dict(version=VERSION,producer_sha256=sha(Path(__file__).read_bytes()),execution_id=artifact,material_name=saved['material_name'],
        model_version=saved['model_version'],measurement_protocol=saved['measurement_protocol'],
        source_hashes=source_hashes,selected_window_ms=metrics['selected_window_ms'],
        torque_numerical_acceptance=metrics['torque_numerical_acceptance'],
        findings=torque_findings(metrics),nonpositive_points=points,saved_setup=settings,
        saved_report_expressions=expressions,profiles=profiles,warnings=warnings,
        cause_identified=False,nonlinear_convergence_certified=False,
        residual_interpretation='Raw profile Final Residual field; units/definition not equated to NonlinearSolverResidual. Matrix Calls is not an iteration-limit claim.',
        original_points_preserved=True,new_native_solves=0,automatic_retry=False,
        efficiency_enabled=False,optimization_ranking_enabled=False)


def review_markdown(report):
    escape=lambda v:str(v).replace('|','\\|').replace('\n',' ').replace('\r',' ')
    out=['# 已保存转矩检查','',f"执行：`{report['execution_id']}`；材料：{escape(report['material_name'])}。",
        f"模型：`{report['model_version']}`；协议：`{report['measurement_protocol']}`。",
        f"检查生成器：`{report['version']}`；程序SHA：`{report['producer_sha256']}`。",
        '原数值判据：'+('通过' if report['torque_numerical_acceptance'] else '未通过')+'。',
        '；'.join(report['findings']),'','## 原始非正转矩点','',
        '| 时间 ms | 转矩 N·m | 在指标窗口内 | 前一点 / 后一点 N·m | 原profile残差字段 | 矩阵调用数 |',
        '| ---: | ---: | --- | ---: | ---: | ---: |']
    for point in report['nonpositive_points']:
        native=point['native_profile_row'] or {}
        before=point['previous'];after=point['next']
        out.append('| '+' | '.join(map(escape,[point['time_ms'],point['torque_Nm'],point['within_metric_window'],
            f"{before['torque_Nm'] if before else '—'} / {after['torque_Nm'] if after else '—'}",
            native.get('final_residual_raw','—'),native.get('matrix_calls','—')]))+' |')
    if not report['nonpositive_points']:out.append('未发现非正转矩点。')
    out+=['','## 保存设置','']+[f'- {key}：{escape(v) if v is not None else "未保存该字段"}' for key,v in report['saved_setup'].items()]
    out+=['','报告表达式：'+escape(', '.join(report['saved_report_expressions']) or '未读取到'),'','## 检查范围','',
        '保留全部原始点，不删点、不平滑、不重算；不改变原数值判据。未确定异常原因，未认证非线性收敛。',
        'Final Residual按原字段记录，未核实其与设置残差的量纲关系；Matrix Calls是矩阵调用数，不等同于已证明的迭代上限。',
        '时间网格匹配仅表示对应时间点，正常完成文字不代替收敛证明。新增求解0，不自动重试。']
    out+=['','## 来源哈希','']+[f'- {escape(k)}：`{v}`' for k,v in report['source_hashes'].items()]
    if report['warnings']:out+=['','## 未读取项','']+['- '+escape(w) for w in report['warnings']]
    return '\n'.join(out)+'\n'
