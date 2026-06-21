"""
dataset_builder.py
将 MuMax3 仿真结果（output/目录）聚合为 XGBoost 训练集 CSV。
"""
import os
import re
import json
import warnings
import contextlib
import io
from collections import Counter
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd
from scipy.interpolate import interp1d

import bh_extractor as see_module
from physics_calibrator import (
    calibrate_bh_curve,
    calibrate_scalar_targets,
    validate_grain_curve,
    write_sidecar_report,
)
from go_steel_reference import get_reference_hc

# 8 个标准化 H 采样节点（A/m），与 Maxwell B-H 数据范围对齐
STANDARD_H_POINTS = [100, 200, 500, 1000, 2000, 3000, 5000, 7500]

# 电机优化/训练模式角度（RD / TD）
MOTOR_ANGLES = [0, 90]
# 全极图模式角度
FULL_ANGLES  = [0, 30, 45, 60, 90, 120, 135, 150, 180]


def _parse_config_name(name: str) -> dict:
    """
    从目录名（如 F70_T15_N1000）解析 f_Goss, theta_0_deg, N_grains。
    返回 dict，解析失败的字段为 None。
    """
    result = {'f_Goss': None, 'theta_0_deg': None, 'halfwidth_deg': None, 'N_grains': None}
    m = re.search(r'F(\d+)', name)
    if m:
        result['f_Goss'] = int(m.group(1)) / 100.0
    m = re.search(r'T(\d+)', name)
    if m:
        result['theta_0_deg'] = float(m.group(1))
    m = re.search(r'hw(\d+)', name, re.IGNORECASE)
    if m:
        result['halfwidth_deg'] = float(m.group(1))
    m = re.search(r'N(\d+)', name)
    if m:
        result['N_grains'] = int(m.group(1))
    return result


def _parse_param_file(param_file: str) -> dict:
    """
    从 simulation_parameters_template.txt 提取参数。
    支持 key=value 和 key: value 两种格式。
    """
    result = {}
    try:
        text = Path(param_file).read_text(encoding='utf-8', errors='ignore')
        patterns = {
            'f_Goss':       r'(?:f[_\s]?goss|Goss texture fraction).*?[=:]\s*([\d.]+)',
            'theta_0_deg':  r'(?:theta[_\s]?0|Texture rotation angle).*?[=:]\s*([\d.]+)',
            'halfwidth_deg':r'(?:half[_\s]?width|Texture halfwidth).*?[=:]\s*([\d.]+)',
            'N_grains':     r'(?:Number of grains|\bN(?:_grains)?\b).*?[=:]\s*(\d+)',
            'Si_content':   r'(?:Si content|Si%).*?[=:]\s*([\d.]+)',
        }
        for key, pat in patterns.items():
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                result[key] = float(m.group(1))
        policy = re.search(r'halfwidth[_\s]?policy\s*[=:]\s*([A-Za-z0-9_\-]+)', text, re.IGNORECASE)
        if policy:
            result['halfwidth_policy'] = policy.group(1)
    except Exception:
        pass
    return result


def _get_grain_files(angle_dir: str) -> list:
    """返回 angle_dir 下所有 grain_*.txt 文件路径，排序后。"""
    p = Path(angle_dir)
    return sorted(p.glob('grain_*.txt'))


def _interp_to_standard_H(H, B) -> np.ndarray:
    guarded = calibrate_bh_curve(H, B, source='dataset_builder')
    H_sorted = np.array(guarded['H'], dtype=float)
    B_sorted = np.array(guarded['B'], dtype=float)
    return np.interp(STANDARD_H_POINTS, H_sorted, B_sorted,
                     left=B_sorted[0], right=B_sorted[-1])


def _trimmed_mean(values: np.ndarray, proportion: float = 0.2) -> list[float]:
    arr = np.asarray(values, dtype=float)
    if arr.ndim != 2 or len(arr) == 0:
        return []
    if len(arr) < 5:
        return np.median(arr, axis=0).tolist()
    k = int(np.floor(len(arr) * proportion))
    if k <= 0 or len(arr) - 2 * k <= 0:
        return np.mean(arr, axis=0).tolist()
    ordered = np.sort(arr, axis=0)
    return np.mean(ordered[k:len(arr) - k], axis=0).tolist()


def _representative_grain(valid_grains: list[dict], material_B: np.ndarray) -> dict:
    if not valid_grains:
        return {'file': None, 'index': None, 'distance': None}

    rows = []
    for g in valid_grains:
        rows.append(list(g['B_interp']) + [
            g['scalars'].get('Hc', 0.0),
            g['scalars'].get('Mr', 0.0),
            g['scalars'].get('mu_max', 1.0),
        ])
    mat = np.asarray(rows, dtype=float)
    center = np.asarray(list(material_B) + [
        np.median([g['scalars'].get('Hc', 0.0) for g in valid_grains]),
        np.median([g['scalars'].get('Mr', 0.0) for g in valid_grains]),
        np.median([g['scalars'].get('mu_max', 1.0) for g in valid_grains]),
    ], dtype=float)

    scale = np.nanmedian(np.abs(mat - np.nanmedian(mat, axis=0)), axis=0)
    scale = np.where(scale > 1e-12, scale, np.nanstd(mat, axis=0))
    scale = np.where(scale > 1e-12, scale, 1.0)
    dist = np.sqrt(np.nanmean(((mat - center) / scale) ** 2, axis=1))
    idx = int(np.nanargmin(dist))
    return {
        'file': valid_grains[idx]['file'].name,
        'index': idx,
        'distance': float(dist[idx]),
    }


def _extract_bh_one_angle(angle_dir: str, Msat: float = 1.56e6,
                          reference_hc: float = None) -> dict | None:
    """
    处理单个角度目录下所有晶粒文件，返回材料级代表 B-H 数据。

    Returns:
        {
          'B_at_std_H': list[float],  # 长度 == len(STANDARD_H_POINTS)
          'Hc':   float,
          'Mr':   float,
          'mu_max': float,
          'n_grains_valid': int,
          'aggregation_method': str,
          'representative_grain_file': str,
          'quality_report': dict
        }
        或 None（无有效晶粒）
    """
    grain_files = _get_grain_files(angle_dir)
    if not grain_files:
        return None

    valid_grains = []
    rejected = Counter()
    errors = []

    for gf in grain_files:
        try:
            data = see_module.read_mumax_data(str(gf))
            with warnings.catch_warnings():
                warnings.simplefilter('ignore')
                with contextlib.redirect_stdout(io.StringIO()):
                    results, H_arr, M_arr, B_arr, M_mag, E_total = \
                        see_module.extract_magnetic_properties(data, Msat=Msat)

            H_curve = np.array(results.get('H_curve', []))
            B_curve = np.array(results.get('B_curve', []))
            scalars_raw = {
                # Hc from simulation (~36000 A/m, SW model limit) is overridden
                # with reference database value if provided.
                'Hc': reference_hc if reference_hc is not None else float(results.get('Hc', 0)),
                'Mr': float(results.get('Mr', 0)),
                'mu_max': float(results.get('mu_r_max_total', 1)),
            }

            ok, validation = validate_grain_curve(H_curve, B_curve, scalars_raw)
            if not ok:
                reasons = validation.get('reasons') or ['invalid_curve']
                for reason in reasons:
                    rejected[reason] += 1
                continue

            guarded = calibrate_bh_curve(H_curve, B_curve, source=str(gf))
            B_interp = _interp_to_standard_H(guarded['H'], guarded['B'])
            if len(B_interp) != len(STANDARD_H_POINTS) or not np.all(np.isfinite(B_interp)):
                rejected['interpolation_failed'] += 1
                continue

            scalars = calibrate_scalar_targets(scalars_raw, source=str(gf))['values']
            valid_grains.append({
                'file': gf,
                'B_interp': B_interp,
                'scalars': scalars,
                'validation': validation,
                'calibration_report': guarded['report'],
            })

        except Exception as exc:
            rejected['read_or_extract_failed'] += 1
            errors.append({'file': gf.name, 'error': str(exc)})

    if not valid_grains:
        return None

    B_matrix = np.asarray([g['B_interp'] for g in valid_grains], dtype=float)
    B_median = np.median(B_matrix, axis=0)
    B_trimmed = _trimmed_mean(B_matrix)
    material_curve = calibrate_bh_curve(
        STANDARD_H_POINTS, B_median, source=f'material_aggregate:{Path(angle_dir).name}'
    )
    B_material = np.asarray(material_curve['B'], dtype=float)

    rep = _representative_grain(valid_grains, B_material)
    Hc_vals = [g['scalars'].get('Hc', 0.0) for g in valid_grains]
    Mr_vals = [g['scalars'].get('Mr', 0.0) for g in valid_grains]
    mu_vals = [g['scalars'].get('mu_max', 1.0) for g in valid_grains]

    quality_report = {
        'angle_dir': str(angle_dir),
        'n_grains_total': len(grain_files),
        'n_grains_valid': len(valid_grains),
        'n_grains_rejected': len(grain_files) - len(valid_grains),
        'rejection_reasons': dict(rejected),
        'representative_grain_file': rep['file'],
        'representative_distance': rep['distance'],
        'aggregation_method': 'median_with_representative_grain',
        'trimmed_mean_B_at_std_H': B_trimmed,
        'calibration_report': material_curve['report'],
        'sample_errors': errors[:10],
    }

    return {
        'H': STANDARD_H_POINTS,
        'B': B_material.tolist(),
        'B_at_std_H': B_material.tolist(),
        'Hc': float(np.median(Hc_vals)),
        'Mr': float(np.median(Mr_vals)),
        'mu_max': float(np.median(mu_vals)),
        'n_grains_total': len(grain_files),
        'n_grains_valid': len(valid_grains),
        'n_grains_rejected': len(grain_files) - len(valid_grains),
        'n_grains_ok': len(valid_grains),
        'aggregation_method': 'median_with_representative_grain',
        'representative_grain_file': rep['file'],
        'quality_report': quality_report,
    }


class DatasetBuilder:
    """将 output/ 仿真结果聚合为 XGBoost 训练集。"""

    def __init__(self, output_root: str = 'output', dataset_dir: str = 'data/datasets'):
        self.output_root = Path(output_root)
        self.dataset_dir = Path(dataset_dir)
        self.dataset_dir.mkdir(parents=True, exist_ok=True)

    def scan_output_dir(self) -> list[dict]:
        """
        扫描 output_root，找到所有含有 angle_XXX/ 子目录的配置目录。
        返回 list[dict]，每个元素描述一个可聚合的配置。
        """
        configs = []
        for candidate in sorted(self.output_root.rglob('simulation_parameters_template.txt')):
            config_dir = candidate.parent
            angle_dirs = sorted(config_dir.glob('angle_*'))
            if not angle_dirs:
                continue

            # 解析可用角度
            available_angles = []
            for ad in angle_dirs:
                m = re.match(r'angle_(\d+)', ad.name)
                if m:
                    available_angles.append(int(m.group(1)))

            # 晶粒数（从第一个有效角度目录取）
            n_grains = 0
            if angle_dirs:
                n_grains = len(list(angle_dirs[0].glob('grain_*.txt')))

            # 参数：先读 txt，再从目录名补充
            params = _parse_param_file(str(candidate))
            name_params = _parse_config_name(config_dir.name)
            for k, v in name_params.items():
                if v is not None and k not in params:
                    params[k] = v

            configs.append({
                'config_path':      str(config_dir),
                'config_name':      config_dir.name,
                'available_angles': available_angles,
                'n_grains':         n_grains,
                'params':           params,
            })
        return configs

    def build_material_representative_summary(self, config_path: str,
                                              target_angles: list = None,
                                              Msat: float = 1.56e6,
                                              si_content: float = 3.0,
                                              write_report: bool = True) -> dict:
        """Return material-level representative curves for one config directory."""
        config_dir = Path(config_path)
        if target_angles is None:
            target_angles = []
            for ad in sorted(config_dir.glob('angle_*')):
                m = re.match(r'angle_(\d+)', ad.name)
                if m:
                    target_angles.append(int(m.group(1)))

        ref_hc = get_reference_hc(si_content=si_content)
        odf_params = _parse_config_name(config_dir.name)
        summary = {
            'config_name':   config_dir.name,
            'config_path':   str(config_dir),
            'f_Goss':        odf_params.get('f_Goss'),
            'theta_0_deg':   odf_params.get('theta_0_deg'),
            'halfwidth_deg': odf_params.get('halfwidth_deg'),
            'angles': {},
            'Hc_reference_Am': ref_hc,
            'scalar_confidence': {
                'Hc': 'reference_value_go_steel_database',
                'mu_max': 'low_due_to_mesh_limit',
                'BH_curve': 'primary_for_export',
            },
        }
        for angle in target_angles:
            angle_dir = config_dir / f'angle_{angle:03d}'
            if not angle_dir.exists():
                summary['angles'][str(angle)] = {'status': 'missing'}
                continue
            bh = _extract_bh_one_angle(str(angle_dir), Msat=Msat, reference_hc=ref_hc)
            if bh is None:
                summary['angles'][str(angle)] = {'status': 'invalid_or_empty'}
                continue
            summary['angles'][str(angle)] = {'status': 'ok', **bh}

        if write_report:
            sidecar = config_dir / 'material_representative_summary.json'
            summary['sidecar_path'] = write_sidecar_report(sidecar, summary)
        return summary

    def build_sample_row(self, config_info: dict,
                         target_angles: list = None,
                         Msat: float = 1.56e6) -> dict | None:
        """
        对单个配置目录构建一行训练数据。
        target_angles: 期望的角度列表；None 则使用 config_info['available_angles']。
        """
        config_dir = Path(config_info['config_path'])
        params = config_info.get('params', {})
        angles = target_angles if target_angles is not None else config_info['available_angles']

        row = {
            'config_name':    config_info['config_name'],
            'f_Goss':         params.get('f_Goss', None),
            'theta_0_deg':    params.get('theta_0_deg', None),
            'halfwidth_deg':  params.get('halfwidth_deg', None),
            'N_grains':       params.get('N_grains', config_info.get('n_grains', None)),
            'Si_content':     params.get('Si_content', 3.0),
            'halfwidth_policy': params.get('halfwidth_policy', None),
            'bh_reference_corrected': False,  # 有 RD 数据且 f_Goss 已知时会被置 True
        }

        si_content = float(params.get('Si_content', 3.0))
        ref_hc = get_reference_hc(si_content=si_content)
        has_any = False
        representative_summary = {
            'config_name': config_info['config_name'],
            'config_path': str(config_dir),
            'angles': {},
            'Hc_reference_Am': ref_hc,
            'scalar_confidence': {
                'Hc': 'reference_value_go_steel_database',
                'mu_max': 'low_due_to_mesh_limit',
                'BH_curve': 'primary_for_export',
            },
        }
        for angle in angles:
            angle_str = f'angle_{angle:03d}'
            angle_dir = config_dir / angle_str
            if not angle_dir.exists():
                # 跳过不存在的角度，但不中断
                for h in STANDARD_H_POINTS:
                    row[f'B_{angle}deg_H{h}'] = None
                row[f'Hc_{angle}deg']     = None
                row[f'Mr_{angle}deg']     = None
                row[f'mu_max_{angle}deg'] = None
                representative_summary['angles'][str(angle)] = {'status': 'missing'}
                continue

            bh = _extract_bh_one_angle(str(angle_dir), Msat=Msat, reference_hc=ref_hc)
            if bh is None:
                for h in STANDARD_H_POINTS:
                    row[f'B_{angle}deg_H{h}'] = None
                row[f'Hc_{angle}deg']     = None
                row[f'Mr_{angle}deg']     = None
                row[f'mu_max_{angle}deg'] = None
                representative_summary['angles'][str(angle)] = {'status': 'invalid_or_empty'}
            else:
                has_any = True
                # 展示摘要始终保存原始仿真聚合值（供分析页面显示）
                representative_summary['angles'][str(angle)] = {'status': 'ok', **bh}

                # 训练特征：RD(0°)方向用 δ 修正后的 B 值，其余方向保持原始值
                B_train = list(bh['B_at_std_H'])
                if angle == 0 and row.get('f_Goss') is not None and row.get('theta_0_deg') is not None:
                    try:
                        from modules.reference_corrector import apply_reference_correction
                        odf_p = {
                            'f_Goss':        row['f_Goss'],
                            'theta_0_deg':   row['theta_0_deg'],
                            'halfwidth_deg': row.get('halfwidth_deg') or 8.0,
                        }
                        B_corr = apply_reference_correction(
                            np.array(STANDARD_H_POINTS, dtype=float),
                            np.array(B_train, dtype=float),
                            odf_p, direction='RD',
                        )
                        B_train = B_corr.tolist()
                        row['bh_reference_corrected'] = True
                    except Exception as _e:
                        warnings.warn(f'[dataset_builder] δ-correction 失败 {config_dir.name}/angle=0: {_e}')

                for i, h in enumerate(STANDARD_H_POINTS):
                    row[f'B_{angle}deg_H{h}'] = B_train[i]
                row[f'Hc_{angle}deg']     = bh['Hc']
                row[f'Mr_{angle}deg']     = bh['Mr']
                row[f'mu_max_{angle}deg'] = bh['mu_max']
                row[f'n_grains_valid_{angle}deg'] = bh['n_grains_valid']
                row[f'representative_grain_{angle}deg'] = bh.get('representative_grain_file')

        if representative_summary['angles']:
            sidecar = config_dir / 'material_representative_summary.json'
            representative_summary['sidecar_path'] = write_sidecar_report(
                sidecar, representative_summary
            )
        return row if has_any else None

    def build_dataset(self, configs: list[str] = None,
                      config_paths: list[str] = None,
                      target_angles: list = None,
                      Msat: float = 1.56e6,
                      progress_callback=None) -> pd.DataFrame:
        """
        批量聚合所有（或指定）配置，返回 DataFrame。
        configs: 配置名列表；None 则全量扫描。
        progress_callback: callable(current, total, config_name) 用于进度报告。
        """
        all_configs = self.scan_output_dir()
        if configs is not None:
            all_configs = [c for c in all_configs if c['config_name'] in configs]
        if config_paths is not None:
            wanted = {str(Path(p).resolve()) for p in config_paths}
            all_configs = [
                c for c in all_configs
                if str(Path(c['config_path']).resolve()) in wanted
            ]

        rows = []
        for i, cfg in enumerate(all_configs):
            if progress_callback:
                progress_callback(i, len(all_configs), cfg['config_name'])
            row = self.build_sample_row(cfg, target_angles=target_angles, Msat=Msat)
            if row is not None:
                rows.append(row)

        df = pd.DataFrame(rows)
        return df

    def save_dataset(self, df: pd.DataFrame, tag: str = '') -> str:
        """保存到 data/datasets/dataset_YYYYMMDD_HHMMSS[_tag].csv，返回文件路径。"""
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        name = f'dataset_{ts}{"_" + tag if tag else ""}.csv'
        path = self.dataset_dir / name
        df.to_csv(path, index=False, encoding='utf-8-sig')
        metadata = {
            'dataset_path': str(path),
            'rows': int(len(df)),
            'columns': list(df.columns),
            'target_policy': 'RD_TD_only_by_default',
            'standard_H_points': STANDARD_H_POINTS,
            'scalar_confidence': {
                'Hc': 'reference_value_go_steel_database',
                'mu_max': 'low_due_to_mesh_limit',
                'BH_curve': 'primary_for_export',
            },
        }
        if 'halfwidth_deg' in df.columns and df['halfwidth_deg'].dropna().nunique() == 1:
            metadata['halfwidth_policy'] = 'fixed_for_pipeline'
            metadata['halfwidth_deg'] = float(df['halfwidth_deg'].dropna().iloc[0])
        write_sidecar_report(path.with_suffix('.metadata.json'), metadata)
        return str(path)

    def list_datasets(self) -> list[dict]:
        """列出 data/datasets/ 下所有 CSV 的元信息。"""
        result = []
        for f in sorted(self.dataset_dir.glob('*.csv'), reverse=True):
            try:
                df = pd.read_csv(f, nrows=1)
                n_rows = sum(1 for _ in open(f, encoding='utf-8-sig')) - 1
                result.append({
                    'name':    f.name,
                    'path':    str(f),
                    'rows':    n_rows,
                    'cols':    len(df.columns),
                    'created': datetime.fromtimestamp(f.stat().st_mtime).strftime('%Y-%m-%d %H:%M:%S'),
                })
            except Exception:
                pass
        return result
