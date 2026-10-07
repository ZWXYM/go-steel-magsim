"""Derived failure details; keep producer states and native messages unchanged."""
import copy
import json
import re
import zipfile
from pathlib import Path

from modules.material_calibration import file_hash
from modules.motor_workbench import write_json


def diagnose_job(motor, job):
    value=copy.deepcopy(job)
    base=motor.path(value['id']).resolve()
    diagnostics=[]
    for case in value.get('cases',[]):
        if case.get('status')!='failed' or not re.fullmatch(r'case_\d{2,}',str(case.get('id',''))):
            continue
        source=(base/case['id']/'desktop_failure_messages.json').resolve()
        if not source.is_relative_to(base) or not source.is_file():
            continue
        try:
            data=json.loads(source.read_text(encoding='utf-8'))
            if not isinstance(data,dict) or not isinstance(data.get('messages'),list):
                continue
            messages=[str(m) for m in data.get('messages',[]) if '[error]' in str(m).casefold()]
            if not messages:
                continue
            text='\n'.join(messages)
            if re.search(r'find factorization fail|mfs solver failed',text,re.I):
                kind='solver_factorization_failure'
                reason='Maxwell矩阵分解失败；保留当前工程/网格和原生消息，需按错误位置检查该案'
            elif re.search(r'license.*(?:fail|not available|denied)|checkout.*fail',text,re.I):
                kind,reason='license_checkout_failure','原生许可获取失败；检查当前许可，不自动重提任务'
            elif re.search(r'(?:out of|insufficient) memory',text,re.I):
                kind,reason='solver_memory_failure','原生求解内存不足；检查资源与单案配置'
            elif re.search(r'mesh.*fail|TAU.*fail',text,re.I):
                kind,reason='mesh_generation_failure','原生网格生成失败；检查当前隔离副本的几何与网格'
            else:
                kind,reason='native_solver_failure','原生求解器报错；查看保存的原始错误消息'
            diagnostic=dict(case_id=case['id'],material=case.get('material'),kind=kind,reason=reason,
                            messages=messages,source_file=source.relative_to(base).as_posix(),source_sha256=file_hash(source),
                            source_state_changed=False,automatic_retry=False)
            case['native_failure']=diagnostic
            diagnostics.append(diagnostic)
        except (ValueError,OSError,TypeError):
            continue
    value['native_failure_diagnostics']=diagnostics
    return value


def append_export_diagnostics(motor, result):
    """Attach a derived review to this newly created export, never a raw result."""
    job=diagnose_job(motor,motor.state(result['job_id']))
    relative=Path(result['files']['reports_bundle.zip'])
    folder=(motor.path(result['job_id'])/relative.parent).resolve()
    if not folder.is_relative_to((motor.path(result['job_id'])/'exports').resolve()):
        raise ValueError('只能为新汇总目录附加原生错误说明')
    report=dict(source_job=result['job_id'],derived_review=True,
                source_state_sha256=file_hash(motor.path(result['job_id'])/'state.json'),
                failures=job['native_failure_diagnostics'],new_native_solves=0,raw_results_changed=False,
                review_version='native_failure_review_v1',producer_sha256=file_hash(Path(__file__)))
    if (folder/'native_failures.json').exists():
        raise ValueError('该新汇总已附加错误说明')
    write_json(folder/'native_failures.json',report)
    with zipfile.ZipFile(folder/'reports_bundle.zip','a',zipfile.ZIP_DEFLATED) as bundle:
        if 'native_failures.json' in bundle.namelist():
            raise ValueError('该新汇总已附加错误说明')
        bundle.write(folder/'native_failures.json','native_failures.json')
    result['files']['native_failures.json']=(relative.parent/'native_failures.json').as_posix()
    result['native_failure_diagnostics']=job['native_failure_diagnostics']
    return result
