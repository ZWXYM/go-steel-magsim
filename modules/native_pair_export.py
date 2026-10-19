"""CPU-only raw RD/TD export from a frozen, explicitly labelled pilot ensemble.

No reference correction, fitting, prediction, solver or source writes. This
first adapter deliberately rejects the original unlabelled-table workflow.
"""
import hashlib
import json
import math
import re
from pathlib import Path

from modules.material_calibration import PHYSICS_VERSION,features,file_hash
from modules.native_quality import unassessed_quality
from modules.texture_sampling import LEGACY_SAMPLING_VERSION,SAMPLING_VERSION
from tools.analyze_sampling_convergence import read_ensemble
from tools.run_calibration_pilot import H_GRID


def source_file(base,relative):
    if not isinstance(relative,str) or Path(relative).is_absolute():
        raise ValueError('来源路径必须相对于冻结目录')
    path=(base/relative).resolve()
    if not path.is_relative_to(base) or not path.is_file():
        raise ValueError('来源文件缺失或超出冻结目录：'+relative)
    return path


def export_pair(run_dir,grade,output):
    base=Path(run_dir).resolve();output=Path(output).resolve()
    if output.exists():raise ValueError('输出目录已存在；不覆盖来源或旧导出')
    if output.is_relative_to(base):raise ValueError('导出目录不能写入原冻结来源')
    if not output.parent.is_dir():raise ValueError('输出父目录不存在')
    if not isinstance(grade,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',grade):raise ValueError('无效材料ID')
    manifest_path=source_file(base,'manifest.json')
    manifest=json.loads(manifest_path.read_text(encoding='utf8'))
    if manifest.get('physics_version')!=PHYSICS_VERSION or manifest.get('H_axis')!='physical_A_per_m':
        raise ValueError('来源不是当前物理H协议，不能改名转换旧训练CSV')
    sampling=manifest.get('texture_sampling_version',LEGACY_SAMPLING_VERSION)
    if sampling not in (LEGACY_SAMPLING_VERSION,SAMPLING_VERSION):raise ValueError('未知采样协议')
    selected=[m for m in manifest['materials'] if m['grade']==grade]
    if len(selected)!=1:raise ValueError('来源材料缺失或重复')
    material=selected[0];features(material['params'])
    thickness=float(material['thickness_mm'])
    if not math.isfinite(thickness) or thickness<=0:raise ValueError('来源厚度必须为正')
    source_file(base,grade+'/orientations.csv')
    if 'grain_samples' in material:source_file(base,material['grain_samples']['path'])
    source_file(base,'run_status.json')
    jobs=[j for j in manifest['jobs'] if j['grade']==grade]
    for job in jobs:
        if job.get('direction') not in ('RD','TD') or job.get('angle')!={'RD':0,'TD':90}[job['direction']]:
            raise ValueError('方向必须从冻结角度绑定，不能按响应自动交换RD/TD')
        script=source_file(base,job['script'])
        source_file(base,job['output']+'/table.txt')
        text=script.read_text(encoding='utf8')
        if f'// Physics version: {PHYSICS_VERSION}' not in text:
            raise ValueError('实际脚本物理版本注释与冻结manifest不符')
        ms=re.search(r'^Msat\s*=\s*([0-9.eE+-]+)',text,re.M)
        if not ms or float(ms.group(1))!=1.56e6:raise ValueError('当前提取器只支持已核验的Msat=1.56e6来源')
        if 'tableaddvar(branch' not in text:raise ValueError('脚本缺少显式branch；不能冒充pilot协议')
    pair=dict(material_id=grade,params=material['params'],simulation_physics_version=PHYSICS_VERSION,
        texture_sampling_version=sampling,curve_definition='major_loop_midpoint_proxy',
        H_axis='physical_A_per_m',manifest_sha256=file_hash(manifest_path),thickness_mm=thickness,
        native_quality=unassessed_quality(grade))
    evidence={}
    for direction in ('RD','TD'):
        values,record=read_ensemble(base,grade,direction)
        pair[direction]=dict(H=H_GRID.tolist(),B=values.mean(axis=0).tolist())
        evidence[direction]=record
    # All validation completes before a new directory is created.
    output.mkdir()
    raw=json.dumps(pair,ensure_ascii=False,indent=2,allow_nan=False).encode('utf8')
    (output/'raw_pair.json').write_bytes(raw)
    receipt=dict(schema='native_pair_export_v1',source_manifest_sha256=pair['manifest_sha256'],
        source_run_status_sha256=file_hash(base/'run_status.json'),directions=evidence,
        raw_pair_sha256=hashlib.sha256(raw).hexdigest(),H_axis='physical_A_per_m',
        reference_correction_applied=False,new_native_solves=0,new_predictions=0,
        quality_scope='source bytes checked; no new numerical or independent material certification')
    (output/'source_manifest.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf8')
    return receipt
