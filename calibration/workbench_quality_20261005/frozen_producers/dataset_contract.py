"""Prevent accidental mixing of incompatible physical models/label protocols."""
from pathlib import Path
import hashlib
import re
from modules.texture_sampling import LEGACY_SAMPLING_VERSION

CONTRACT_COLUMNS = {
    'simulation_physics_version': 'legacy_unspecified',
    'reference_correction_version': 'legacy_unspecified',
    'H_axis': 'legacy_unspecified',
    'calibration_sha256': 'legacy_unspecified',
    'texture_sampling_version': LEGACY_SAMPLING_VERSION,
}


def dataset_contract(df, dataset_path=None):
    versions = {}
    for column, fallback in CONTRACT_COLUMNS.items():
        values = (df[column].fillna(fallback).astype(str).replace('', fallback).unique().tolist()
                  if column in df else [fallback])
        if len(values) != 1:
            raise ValueError(f'数据集混合了不同 {column}: {values}; 请分开构建和训练')
        versions[column] = values[0]
    if 'dataset_role' in df and any(df['dataset_role'].fillna('') == 'calibration_fit_diagnostics'):
        raise ValueError('校准拟合诊断表不可作为正式代理训练集')
    if versions['reference_correction_version'] in ('fixed_h_delta_v1','fixed_h_nested_transfer_v2'):
        if (versions['simulation_physics_version'] != 'cubic_sample_frame_v2'
                or versions['H_axis'] != 'physical_A_per_m'
                or versions['calibration_sha256'] == 'legacy_unspecified'):
            raise ValueError(versions['reference_correction_version']+' 数据缺少匹配的物理版本、H 轴或校准哈希')
        from modules.native_quality import VERSION as QUALITY_VERSION
        required=('native_quality_contract_version','native_quality_status','native_quality_sha256','strict_training_eligible')
        if any(c not in df for c in required):
            raise ValueError('正式代理标签缺少原生质量合同；旧标定标签不可默认视为收敛')
        if (not (df['native_quality_contract_version'].fillna('')==QUALITY_VERSION).all()
                or not (df['native_quality_status'].fillna('')=='numerical_screen_passed').all()):
            raise ValueError('正式代理训练禁止原生筛查失败、未知收敛或未审计标签')
        if not df['strict_training_eligible'].astype('string').isin(['True','true']).all():
            raise ValueError('该输出是标定/预测诊断，尚未获准作为正式代理标签')
        hashes=df['native_quality_sha256'].fillna('').astype(str).tolist()
        if not all(re.fullmatch('[0-9a-f]{64}',v) for v in hashes):
            raise ValueError('原生质量来源哈希缺失或无效')
        versions['native_quality_contract_version']=QUALITY_VERSION
        versions['native_quality_audit_sha256s']=sorted(set(hashes))
    if dataset_path:
        versions['dataset_sha256'] = hashlib.sha256(Path(dataset_path).read_bytes()).hexdigest()
    return versions
