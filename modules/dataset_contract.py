"""Prevent accidental mixing of incompatible physical models/label protocols."""
from pathlib import Path
import hashlib
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
    if versions['reference_correction_version'] == 'fixed_h_delta_v1':
        if (versions['simulation_physics_version'] != 'cubic_sample_frame_v2'
                or versions['H_axis'] != 'physical_A_per_m'
                or versions['calibration_sha256'] == 'legacy_unspecified'):
            raise ValueError('fixed_h_delta_v1 数据缺少匹配的物理版本、H 轴或校准哈希')
    if dataset_path:
        versions['dataset_sha256'] = hashlib.sha256(Path(dataset_path).read_bytes()).hexdigest()
    return versions
