"""
dataset_builder.py
将 MuMax3 仿真结果（output/目录）聚合为 XGBoost 训练集 CSV。
"""
import os
import re
import json
import warnings
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd
from scipy.interpolate import interp1d

import bh_extractor as see_module

# 8 个标准化 H 采样节点（A/m），与 Maxwell B-H 数据范围对齐
STANDARD_H_POINTS = [100, 200, 500, 1000, 2000, 3000, 5000, 7500]

# 电机优化模式角度（RD / 45° / TD）
MOTOR_ANGLES = [0, 45, 90]
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
            'f_Goss':       r'f[_\s]?[Gg]oss\s*[=:]\s*([\d.]+)',
            'theta_0_deg':  r'theta\s*[=:]\s*([\d.]+)',
            'halfwidth_deg':r'half[_\s]?width\s*[=:]\s*([\d.]+)',
            'N_grains':     r'\bN\s*[=:]\s*(\d+)',
            'Si_content':   r'[Ss]i\s*[=:]\s*([\d.]+)',
        }
        for key, pat in patterns.items():
            m = re.search(pat, text)
            if m:
                result[key] = float(m.group(1))
    except Exception:
        pass
    return result


def _get_grain_files(angle_dir: str) -> list:
    """返回 angle_dir 下所有 grain_*.txt 文件路径，排序后。"""
    p = Path(angle_dir)
    return sorted(p.glob('grain_*.txt'))


def _extract_bh_one_angle(angle_dir: str, Msat: float = 1.52e6) -> dict | None:
    """
    处理单个角度目录下所有晶粒文件，返回平均 B-H 数据。

    Returns:
        {
          'B_at_std_H': list[float],  # 长度 == len(STANDARD_H_POINTS)
          'Hc':   float,   # A/m，所有晶粒均值
          'Mr':   float,   # A/m，所有晶粒均值
          'mu_max': float, # 最大相对磁导率，所有晶粒均值
          'n_grains_ok': int
        }
        或 None（无有效晶粒）
    """
    grain_files = _get_grain_files(angle_dir)
    if not grain_files:
        return None

    B_matrix = []   # shape: (n_grains_ok, len(STANDARD_H_POINTS))
    Hc_list, Mr_list, mu_max_list = [], [], []

    for gf in grain_files:
        try:
            data = see_module.read_mumax_data(str(gf))
            with warnings.catch_warnings():
                warnings.simplefilter('ignore')
                results, H_arr, M_arr, B_arr, M_mag, E_total = \
                    see_module.extract_magnetic_properties(data, Msat=Msat)

            H_curve = np.array(results.get('H_curve', []))
            B_curve = np.array(results.get('B_curve', []))

            if len(H_curve) < 3 or len(B_curve) < 3:
                continue

            # 确保单调递增
            sort_idx = np.argsort(H_curve)
            H_sorted = H_curve[sort_idx]
            B_sorted = B_curve[sort_idx]

            # 去重
            _, unique_idx = np.unique(H_sorted, return_index=True)
            H_sorted = H_sorted[unique_idx]
            B_sorted = B_sorted[unique_idx]

            if len(H_sorted) < 2:
                continue

            H_min, H_max = H_sorted[0], H_sorted[-1]
            interp = interp1d(H_sorted, B_sorted, kind='linear',
                              bounds_error=False,
                              fill_value=(B_sorted[0], B_sorted[-1]))

            B_interp = []
            for h in STANDARD_H_POINTS:
                if h < H_min:
                    B_interp.append(float(B_sorted[0]))
                elif h > H_max:
                    B_interp.append(float(B_sorted[-1]))
                else:
                    B_interp.append(float(interp(h)))

            B_matrix.append(B_interp)
            Hc_list.append(float(results.get('Hc', 0)))
            Mr_list.append(float(results.get('Mr', 0)) / Msat)  # 归一化
            mu_max_list.append(float(results.get('mu_r_max_total', 1)))

        except Exception:
            continue

    if not B_matrix:
        return None

    B_avg = np.mean(B_matrix, axis=0).tolist()
    return {
        'B_at_std_H':   B_avg,
        'Hc':           float(np.mean(Hc_list)),
        'Mr':           float(np.mean(Mr_list)) * Msat,
        'mu_max':       float(np.mean(mu_max_list)),
        'n_grains_ok':  len(B_matrix),
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

    def build_sample_row(self, config_info: dict,
                         target_angles: list = None,
                         Msat: float = 1.52e6) -> dict | None:
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
        }

        has_any = False
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
                continue

            bh = _extract_bh_one_angle(str(angle_dir), Msat=Msat)
            if bh is None:
                for h in STANDARD_H_POINTS:
                    row[f'B_{angle}deg_H{h}'] = None
                row[f'Hc_{angle}deg']     = None
                row[f'Mr_{angle}deg']     = None
                row[f'mu_max_{angle}deg'] = None
            else:
                has_any = True
                for i, h in enumerate(STANDARD_H_POINTS):
                    row[f'B_{angle}deg_H{h}'] = bh['B_at_std_H'][i]
                row[f'Hc_{angle}deg']     = bh['Hc']
                row[f'Mr_{angle}deg']     = bh['Mr']
                row[f'mu_max_{angle}deg'] = bh['mu_max']

        return row if has_any else None

    def build_dataset(self, configs: list[str] = None,
                      target_angles: list = None,
                      Msat: float = 1.52e6,
                      progress_callback=None) -> pd.DataFrame:
        """
        批量聚合所有（或指定）配置，返回 DataFrame。
        configs: 配置名列表；None 则全量扫描。
        progress_callback: callable(current, total, config_name) 用于进度报告。
        """
        all_configs = self.scan_output_dir()
        if configs is not None:
            all_configs = [c for c in all_configs if c['config_name'] in configs]

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
