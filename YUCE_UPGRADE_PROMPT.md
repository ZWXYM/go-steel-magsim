# YUCE 平台升级实施指南

> **使用说明：** 本文档是一份完整的实施指南，在 `C:\Users\10760\Desktop\YUCE\` 目录下打开新的 Claude Code 对话后，
> 将本文档内容发送给 Claude Code 作为任务说明，按顺序执行所有步骤。

---

## 背景与约束

YUCE 是一个用于 Fe-Si 取向硅钢微磁学仿真的 Flask 平台，使用 MuMax3 GPU 仿真 + pyAEDT + XGBoost。
当前工作目录：`C:\Users\10760\Desktop\YUCE\`

**绝对不能修改的内容（仿真参数锁定）：**
- `odf_texture.py`（原 `1.py`）中的所有计算逻辑
- `mx3_generator.py`（原 `generate_individual_scripts.py`）中 `SimulationConfig` 类的所有参数值
  （GRID_SIZE_X/Y/Z、CELL_SIZE、MSAT、AEX、ALPHA、KU1_BASE、H_MAX、N_STEPS 等）
- `bh_extractor.py`（原 `see.py`）中的所有物理计算函数
- `batch_scheduler.py`（原 `generate_parallel_batch.py`）的所有脚本生成逻辑
- 现有 Flask API 端点的函数签名（只增加新端点，不修改旧端点）
- HTML 现有功能区（只在末尾追加新标签页）

---

## 第 0 步：文件重命名

在 PowerShell 中执行以下命令，将随意命名的文件改为语义化名称：

```powershell
cd "C:\Users\10760\Desktop\YUCE"
Rename-Item "1.py"                          "odf_texture.py"
Rename-Item "see.py"                        "bh_extractor.py"
Rename-Item "generate_individual_scripts.py" "mx3_generator.py"
Rename-Item "generate_parallel_batch.py"    "batch_scheduler.py"
```

然后在 `app.py` 中更新对应的 import 语句（找到并替换以下三行，其余全部保持不变）：

```python
# 旧（第 18-20 行附近）：
import generate_individual_scripts as gis
import generate_parallel_batch as gpb
import see as see_module

# 改为：
import mx3_generator as gis
import batch_scheduler as gpb
import bh_extractor as see_module
```

同时更新 `get_texture_module()` 中 `1.py` 的路径（在 `app.py` 第 27 行附近）：

```python
# 旧：
spec = importlib.util.spec_from_file_location("texture_gen", os.path.join(SCRIPT_DIR, "1.py"))

# 改为：
spec = importlib.util.spec_from_file_location("texture_gen", os.path.join(SCRIPT_DIR, "odf_texture.py"))
```

---

## 第 1 步：创建数据目录

```powershell
cd "C:\Users\10760\Desktop\YUCE"
New-Item -ItemType Directory -Force -Path "data\datasets"
New-Item -ItemType Directory -Force -Path "data\models"
New-Item -ItemType Directory -Force -Path "data\exports"
```

---

## 第 2 步：创建 `dataset_builder.py`

在 `C:\Users\10760\Desktop\YUCE\` 下创建 `dataset_builder.py`，内容如下。
该模块扫描 `output/` 目录，对每个配置的各角度晶粒仿真结果调用 `bh_extractor.py` 提取 B-H 曲线，
在 8 个标准 H 节点上跨晶粒平均，生成 XGBoost 训练所需的 CSV 数据集。

**重要：调用 `bh_extractor.extract_magnetic_properties()` 时完全沿用原有参数，不做任何修改。**

```python
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
```

---

## 第 3 步：创建 `ml_trainer.py`

```python
"""
ml_trainer.py
XGBoost 多输出回归：从 5 个织构参数预测 B-H 曲线（3 个角度 × 8 个 H 节点 + 9 个标量目标）。
"""
import os
import pickle
import json
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd
from sklearn.multioutput import MultiOutputRegressor
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import train_test_split
from sklearn.metrics import r2_score, mean_absolute_percentage_error
from xgboost import XGBRegressor

STANDARD_H_POINTS = [100, 200, 500, 1000, 2000, 3000, 5000, 7500]
FEATURE_COLS = ['f_Goss', 'theta_0_deg', 'halfwidth_deg', 'N_grains', 'Si_content']

# 角度 → 列前缀映射（供 predict_bh 使用）
ANGLE_PREFIX = {0: 'B_0deg', 45: 'B_45deg', 90: 'B_90deg'}
SCALAR_TARGET_TEMPLATES = ['Hc_{a}deg', 'Mr_{a}deg', 'mu_max_{a}deg']

DEFAULT_XGB_PARAMS = {
    'n_estimators':     300,
    'max_depth':        6,
    'learning_rate':    0.05,
    'subsample':        0.8,
    'colsample_bytree': 0.8,
    'random_state':     42,
    'eval_metric':      'rmse',
    'verbosity':        0,
}


def _build_target_cols(df_columns: list) -> list:
    """从 DataFrame 列名中自动检测有效的目标列。"""
    candidates = []
    for angle in [0, 45, 90]:
        pfx = f'B_{angle}deg'
        for h in STANDARD_H_POINTS:
            col = f'{pfx}_H{h}'
            if col in df_columns:
                candidates.append(col)
        for tpl in SCALAR_TARGET_TEMPLATES:
            col = tpl.format(a=angle)
            if col in df_columns:
                candidates.append(col)
    return candidates


class BHPredictor:
    """XGBoost 代理模型：织构参数 → B-H 曲线。"""

    def __init__(self, model_dir: str = 'data/models'):
        self.model_dir = Path(model_dir)
        self.model_dir.mkdir(parents=True, exist_ok=True)
        self.model      = None
        self.scaler_X   = None
        self.feature_cols = FEATURE_COLS
        self.target_cols  = None
        self.metadata     = {}

    def train(self, dataset_path: str,
              xgb_params: dict = None,
              test_size: float = 0.2) -> dict:
        """
        训练并保存模型。
        返回 {model_id, r2_avg, mape_avg, r2_per_output, feature_importance, ...}
        """
        params = {**DEFAULT_XGB_PARAMS, **(xgb_params or {})}
        df = pd.read_csv(dataset_path)

        # 只保留特征列和有效目标列
        feat_cols = [c for c in FEATURE_COLS if c in df.columns]
        tgt_cols  = _build_target_cols(df.columns)

        if not feat_cols or not tgt_cols:
            raise ValueError('数据集缺少必要的特征列或目标列，请检查 CSV 格式')

        sub = df[feat_cols + tgt_cols].dropna()
        if len(sub) < 5:
            raise ValueError(f'有效样本数太少（{len(sub)}），至少需要 5 个')

        X = sub[feat_cols].values
        Y = sub[tgt_cols].values

        X_train, X_test, Y_train, Y_test = train_test_split(
            X, Y, test_size=test_size, random_state=42)

        scaler = StandardScaler()
        X_train_s = scaler.fit_transform(X_train)
        X_test_s  = scaler.transform(X_test)

        base = XGBRegressor(**params)
        model = MultiOutputRegressor(base, n_jobs=-1)
        model.fit(X_train_s, Y_train)

        Y_pred = model.predict(X_test_s)
        r2_vals = r2_score(Y_test, Y_pred, multioutput='raw_values')
        mape_vals = []
        for i in range(Y_test.shape[1]):
            mask = np.abs(Y_test[:, i]) > 1e-9
            if mask.sum() > 0:
                mape_vals.append(mean_absolute_percentage_error(
                    Y_test[mask, i], Y_pred[mask, i]))

        # 特征重要性：各 estimator 的均值
        fi_matrix = np.array([est.feature_importances_ for est in model.estimators_])
        fi_avg = fi_matrix.mean(axis=0)
        fi_dict = {feat_cols[i]: float(fi_avg[i]) for i in range(len(feat_cols))}

        # 保存
        model_id = f'model_{datetime.now().strftime("%Y%m%d_%H%M%S")}'
        save_dir = self.model_dir / model_id
        save_dir.mkdir(parents=True)

        with open(save_dir / 'model.pkl', 'wb')    as f: pickle.dump(model, f)
        with open(save_dir / 'scaler_X.pkl', 'wb') as f: pickle.dump(scaler, f)

        config = {
            'model_id':       model_id,
            'dataset_path':   dataset_path,
            'feature_cols':   feat_cols,
            'target_cols':    tgt_cols,
            'xgb_params':     params,
            'test_size':      test_size,
            'n_train':        len(X_train),
            'n_test':         len(X_test),
            'created':        datetime.now().isoformat(),
        }
        metrics = {
            'r2_avg':           float(np.mean(r2_vals)),
            'mape_avg':         float(np.mean(mape_vals)) if mape_vals else None,
            'r2_per_output':    {tgt_cols[i]: float(r2_vals[i]) for i in range(len(tgt_cols))},
            'feature_importance': fi_dict,
        }
        with open(save_dir / 'config.json',  'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        with open(save_dir / 'metrics.json', 'w', encoding='utf-8') as f:
            json.dump(metrics, f, ensure_ascii=False, indent=2)

        # 更新实例状态
        self.model       = model
        self.scaler_X    = scaler
        self.feature_cols = feat_cols
        self.target_cols  = tgt_cols
        self.metadata     = {**config, **metrics}

        return {'model_id': model_id, **metrics,
                'n_samples': len(sub), 'n_features': len(feat_cols),
                'n_targets': len(tgt_cols)}

    def load(self, model_id: str) -> None:
        """从 data/models/<model_id>/ 加载模型和配置。"""
        d = self.model_dir / model_id
        with open(d / 'model.pkl',    'rb') as f: self.model    = pickle.load(f)
        with open(d / 'scaler_X.pkl', 'rb') as f: self.scaler_X = pickle.load(f)
        with open(d / 'config.json',  'r', encoding='utf-8') as f:
            cfg = json.load(f)
        self.feature_cols = cfg['feature_cols']
        self.target_cols  = cfg['target_cols']
        self.metadata     = cfg

    def predict_bh(self, params: dict) -> dict:
        """
        快速推理。
        params: {f_Goss, theta_0_deg, halfwidth_deg, N_grains, Si_content}
        返回: {RD: {H, B}, Cross45: {H, B}, TD: {H, B}, scalars: {...}}
        """
        if self.model is None:
            raise RuntimeError('模型未加载，请先调用 load() 或 train()')

        x = np.array([[params.get(c, 0) for c in self.feature_cols]])
        x_s = self.scaler_X.transform(x)
        y_pred = self.model.predict(x_s)[0]

        tgt = dict(zip(self.target_cols, y_pred))
        H   = STANDARD_H_POINTS
        result = {}

        angle_map = {0: 'RD', 45: 'Cross45', 90: 'TD'}
        for angle, key in angle_map.items():
            pfx = f'B_{angle}deg'
            B   = [np.clip(tgt.get(f'{pfx}_H{h}', 0.0), 0.0, 2.5) for h in H]
            result[key] = {'H': H, 'B': [round(b, 6) for b in B], 'unit': 'A/m, T'}

        scalars = {}
        for angle in [0, 45, 90]:
            for tpl in SCALAR_TARGET_TEMPLATES:
                col = tpl.format(a=angle)
                if col in tgt:
                    scalars[col] = float(tgt[col])
        result['scalars']     = scalars
        result['params_used'] = params
        return result

    def list_models(self) -> list[dict]:
        """返回所有已保存模型的元信息，按创建时间逆序。"""
        items = []
        for d in sorted(self.model_dir.iterdir(), reverse=True):
            if not d.is_dir():
                continue
            try:
                with open(d / 'config.json',  'r', encoding='utf-8') as f: cfg = json.load(f)
                with open(d / 'metrics.json', 'r', encoding='utf-8') as f: met = json.load(f)
                items.append({
                    'model_id':  d.name,
                    'created':   cfg.get('created', ''),
                    'r2_avg':    met.get('r2_avg'),
                    'mape_avg':  met.get('mape_avg'),
                    'n_samples': cfg.get('n_train', 0) + cfg.get('n_test', 0),
                    'n_targets': len(cfg.get('target_cols', [])),
                })
            except Exception:
                pass
        return items

    def delete_model(self, model_id: str) -> bool:
        """删除指定模型目录。"""
        import shutil
        d = self.model_dir / model_id
        if d.exists():
            shutil.rmtree(d)
            return True
        return False
```

---

## 第 4 步：创建 `maxwell_exporter.py`

该模块生成与 `go_steel_data/output/GO_Steel_*.amat` 格式完全一致的 XML 文件。

```python
"""
maxwell_exporter.py
将 B-H 曲线导出为 ANSYS Maxwell .amat 材料文件（XML 格式）。
格式参考：go_steel_data/output/GO_Steel_B23R075.amat
"""
import os
from pathlib import Path
from datetime import datetime

# .amat XML 模板（与现有 go_steel_data/generate_pyaedt_import.py 中的格式完全一致）
_HEADER = '''<?xml version="1.0" encoding="UTF-8"?>
<Material xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <MatProperty name="Name" fullname="name" type="string">{mat_name}</MatProperty>
  <MatProperty name="CoordinateSystemType" fullname="coordinate system type" type="choice">Cartesian</MatProperty>
  <MatProperty name="BulkOrSurface" fullname="bulk or surface" type="choice">Bulk</MatProperty>
  <MatProperty name="MassDensity" fullname="mass density" type="float" unit="kg_per_m3">{density:.0f}</MatProperty>

'''

_FOOTER = '''  <MatProperty name="CoreLossModel" fullname="core loss model" type="choice">Electrical Steel</MatProperty>
  <MatProperty name="Thickness" fullname="thickness" type="float" unit="mm">{thickness}</MatProperty>
  <MatProperty name="Conductivity" fullname="conductivity" type="float" unit="siemens_per_m">2000000</MatProperty>
</Material>
'''


def _preprocess_bh(H: list, B: list) -> tuple[list, list]:
    """
    预处理 B-H 数据：去掉 H<=0，排序，去重，确保 B 单调不减。
    返回 (H_clean, B_clean)。
    """
    import numpy as np
    H_arr = np.array(H, dtype=float)
    B_arr = np.array(B, dtype=float)

    # 仅保留 H > 0
    mask = H_arr > 0
    H_arr = H_arr[mask]
    B_arr = B_arr[mask]

    if len(H_arr) == 0:
        return [0.1], [0.0]

    # 排序
    idx = np.argsort(H_arr)
    H_arr = H_arr[idx]
    B_arr = B_arr[idx]

    # 去重（保留第一个）
    _, uid = np.unique(H_arr, return_index=True)
    H_arr = H_arr[uid]
    B_arr = B_arr[uid]

    return H_arr.tolist(), B_arr.tolist()


def generate_amat_content(mat_name: str,
                           rd_H: list, rd_B: list,
                           td_H: list = None, td_B: list = None,
                           nd_mu_r: float = 1000.0,
                           density_kg_m3: float = 7650.0,
                           thickness_mm: float = 0.35) -> str:
    """
    生成完整 .amat 文件内容字符串。
    td_H/td_B 为 None 时，TD 与 RD 相同（各向同性退化）。
    """
    if td_H is None or td_B is None:
        td_H, td_B = rd_H, rd_B

    rd_H_c, rd_B_c = _preprocess_bh(rd_H, rd_B)
    td_H_c, td_B_c = _preprocess_bh(td_H, td_B)

    lines = [_HEADER.format(mat_name=mat_name, density=density_kg_m3)]

    # RD (X)
    lines.append('  <!-- Rolling Direction (X) - Nonlinear BH -->\n')
    lines.append('  <MatProperty name="BH_Data_X" fullname="BH data X" type="dataset">\n')
    for h, b in zip(rd_H_c, rd_B_c):
        lines.append(f'    <DataPoint X="{h:.6f}" Y="{b:.6f}"/>\n')
    lines.append('  </MatProperty>\n\n')

    # TD (Y)
    lines.append('  <!-- Transverse Direction (Y) - Nonlinear BH -->\n')
    lines.append('  <MatProperty name="BH_Data_Y" fullname="BH data Y" type="dataset">\n')
    for h, b in zip(td_H_c, td_B_c):
        lines.append(f'    <DataPoint X="{h:.6f}" Y="{b:.6f}"/>\n')
    lines.append('  </MatProperty>\n\n')

    # ND (Z) - 线性标量
    lines.append('  <!-- Normal Direction (Z) - Linear permeability -->\n')
    lines.append(f'  <MatProperty name="Permeability_Z" fullname="relative permeability Z" '
                 f'type="float">{nd_mu_r:.1f}</MatProperty>\n\n')

    lines.append(_FOOTER.format(thickness=thickness_mm))
    return ''.join(lines)


def save_amat_file(content: str, mat_name: str,
                   export_dir: str = 'data/exports') -> str:
    """保存到 data/exports/<mat_name>.amat，返回文件路径。"""
    Path(export_dir).mkdir(parents=True, exist_ok=True)
    path = Path(export_dir) / f'{mat_name}.amat'
    path.write_text(content, encoding='utf-8')
    return str(path)


def export_from_bh_curves(rd_H: list, rd_B: list,
                           td_H: list = None, td_B: list = None,
                           mat_name: str = None,
                           thickness_mm: float = 0.35,
                           export_dir: str = 'data/exports') -> str:
    """
    从手动 B-H 数据生成并保存 .amat 文件，返回文件路径。
    mat_name 为 None 时自动生成时间戳命名。
    """
    if mat_name is None:
        mat_name = f'GO_Sim_{datetime.now().strftime("%Y%m%d_%H%M%S")}'
    content = generate_amat_content(mat_name, rd_H, rd_B, td_H, td_B,
                                    thickness_mm=thickness_mm)
    return save_amat_file(content, mat_name, export_dir)


def export_from_prediction(prediction_result: dict,
                            mat_name: str,
                            thickness_mm: float = 0.35,
                            export_dir: str = 'data/exports') -> str:
    """
    从 BHPredictor.predict_bh() 的返回值直接生成 .amat。
    prediction_result 格式：{'RD': {'H':[...], 'B':[...]}, 'TD': {...}, ...}
    """
    rd = prediction_result.get('RD', {})
    td = prediction_result.get('TD', {})
    content = generate_amat_content(
        mat_name,
        rd.get('H', []), rd.get('B', []),
        td.get('H', []), td.get('B', []),
        thickness_mm=thickness_mm
    )
    return save_amat_file(content, mat_name, export_dir)


def list_exports(export_dir: str = 'data/exports') -> list[dict]:
    """列出 data/exports/ 下所有 .amat 文件的元信息。"""
    result = []
    d = Path(export_dir)
    if not d.exists():
        return result
    for f in sorted(d.glob('*.amat'), reverse=True):
        result.append({
            'name':    f.name,
            'path':    str(f),
            'size_kb': round(f.stat().st_size / 1024, 1),
            'created': datetime.fromtimestamp(f.stat().st_mtime).strftime('%Y-%m-%d %H:%M:%S'),
        })
    return result
```

---

## 第 5 步：创建 `pipeline_runner.py`

```python
"""
pipeline_runner.py
自动流水线编排：LHS 织构采样 → MX3 脚本 → 批处理脚本 → 等待仿真 → 聚合数据集 → 训练 XGBoost。
通过 SSE（Server-Sent Events）向前端推送进度。
"""
import json
import time
import uuid
import threading
from datetime import datetime
from pathlib import Path

import mx3_generator as gis
import batch_scheduler as gpb
from dataset_builder import DatasetBuilder, MOTOR_ANGLES, FULL_ANGLES
from ml_trainer import BHPredictor

STAGES = [
    'texture_gen',   # 0: 织构文件生成
    'script_gen',    # 1: MX3 脚本生成
    'batch_gen',     # 2: 批处理脚本生成
    'wait_sim',      # 3: 等待用户运行仿真
    'analyze',       # 4: 结果聚合为数据集
    'train',         # 5: XGBoost 训练
]
STAGE_NAMES = {
    'texture_gen': '织构文件生成',
    'script_gen':  'MX3 脚本生成',
    'batch_gen':   '批处理脚本生成',
    'wait_sim':    '等待仿真完成',
    'analyze':     '结果聚合分析',
    'train':       'XGBoost 训练',
}


class PipelineRunner:
    def __init__(self):
        self.pipelines: dict[str, dict] = {}

    def _state(self, pid: str) -> dict:
        return self.pipelines[pid]

    def _log(self, pid: str, msg: str):
        s = self._state(pid)
        ts = datetime.now().strftime('%H:%M:%S')
        entry = f'[{ts}] {msg}'
        s['log'].append(entry)
        if len(s['log']) > 200:
            s['log'] = s['log'][-200:]
        print(entry)

    def _set_stage(self, pid: str, stage: str, pct: int = 0):
        s = self._state(pid)
        s['stage']       = stage
        s['stage_index'] = STAGES.index(stage)
        s['progress_pct'] = pct
        s['status']      = 'running' if stage != 'wait_sim' else 'waiting_sim'

    def start(self, config: dict) -> str:
        pid = str(uuid.uuid4())[:10]
        self.pipelines[pid] = {
            'id':           pid,
            'stage':        STAGES[0],
            'stage_index':  0,
            'total_stages': len(STAGES),
            'progress_pct': 0,
            'status':       'running',
            'log':          [],
            'results':      {},
            'error':        None,
            'config':       config,
            '_resume_event': threading.Event(),
        }
        thread = threading.Thread(target=self._run, args=(pid, config), daemon=True)
        thread.start()
        return pid

    def _run(self, pid: str, config: dict):
        s  = self._state(pid)
        ev = s['_resume_event']
        angle_mode = config.get('angle_mode', 'motor')
        target_angles = MOTOR_ANGLES if angle_mode == 'motor' else FULL_ANGLES
        n_samples = config.get('n_samples', 20)
        Msat = float(config.get('Msat', 1.52e6))

        try:
            # ── 阶段 0: 织构生成 ──────────────────────────────────
            self._set_stage(pid, 'texture_gen')
            self._log(pid, f'开始织构生成，样本数={n_samples}，角度模式={angle_mode}')

            import importlib.util, sys, os
            spec = importlib.util.spec_from_file_location(
                'odf_texture', os.path.join(os.getcwd(), 'odf_texture.py'))
            tex_mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(tex_mod)

            batch_dir = tex_mod.generate_batch_lhs(
                n_samples=n_samples,
                f_Goss_range=config.get('f_Goss_range', [0.4, 0.9]),
                theta_0_range=config.get('theta_0_range', [1, 30]),
                halfwidth_range=config.get('halfwidth_range', [5, 15]),
                N_grains_range=config.get('N_grains_range', [50, 150]),
                Si_content=config.get('Si_content', 3.0),
                output_dir=f'preinput/pipeline_{pid}'
            )
            s['results']['batch_dir'] = batch_dir
            self._log(pid, f'织构文件已保存至: {batch_dir}')
            s['progress_pct'] = 100

            # ── 阶段 1: MX3 脚本生成 ──────────────────────────────
            self._set_stage(pid, 'script_gen')
            self._log(pid, f'生成 MX3 脚本，角度={target_angles}')
            txt_files = list(Path(batch_dir).glob('grain_orientations_ODF_*.txt'))
            for i, tf in enumerate(txt_files):
                gis.generate_scripts_for_config(
                    str(tf), angles=target_angles,
                    output_dir=f'grain_scripts/pipeline_{pid}')
                s['progress_pct'] = int((i + 1) / len(txt_files) * 100)
            self._log(pid, f'MX3 脚本生成完成，共 {len(txt_files)} 个配置')

            # ── 阶段 2: 批处理脚本生成 ────────────────────────────
            self._set_stage(pid, 'batch_gen')
            self._log(pid, '生成批处理脚本 (.ps1 / .bat / .sh)')
            # 收集所有配置供 gpb 使用
            configs_for_gpb = gis.get_configs_in_dir(f'grain_scripts/pipeline_{pid}')
            batch_script = f'run_pipeline_{pid}.ps1'
            gpb.generate_multi_config_powershell_script(configs_for_gpb, batch_script)
            s['results']['batch_script'] = batch_script
            self._log(pid, f'批处理脚本: {batch_script}')
            s['progress_pct'] = 100

            # ── 阶段 3: 等待仿真 ──────────────────────────────────
            self._set_stage(pid, 'wait_sim')
            s['status'] = 'waiting_sim'
            self._log(pid, f'请在 GPU 机器上运行: {batch_script}')
            self._log(pid, '仿真完成后，请点击页面上的 [仿真已完成，继续] 按钮')
            ev.wait()  # 阻塞，直到 resume_after_sim 被调用
            self._log(pid, '收到继续信号，开始聚合分析...')

            # ── 阶段 4: 结果聚合 ──────────────────────────────────
            self._set_stage(pid, 'analyze')
            self._log(pid, '开始聚合仿真结果...')
            builder = DatasetBuilder()

            def progress_cb(cur, tot, name):
                s['progress_pct'] = int(cur / max(tot, 1) * 100)
                self._log(pid, f'  聚合配置 ({cur}/{tot}): {name}')

            df = builder.build_dataset(
                target_angles=target_angles,
                Msat=Msat,
                progress_callback=progress_cb
            )
            if len(df) == 0:
                raise RuntimeError('未找到有效仿真结果，请确认 output/ 目录下有对应配置的结果文件')

            ds_path = builder.save_dataset(df, tag=f'pipeline_{pid}')
            s['results']['dataset_path'] = ds_path
            s['results']['n_samples'] = len(df)
            self._log(pid, f'数据集已保存: {ds_path}（{len(df)} 个样本）')

            # ── 阶段 5: XGBoost 训练 ──────────────────────────────
            self._set_stage(pid, 'train')
            self._log(pid, '开始 XGBoost 训练...')
            predictor = BHPredictor()
            metrics = predictor.train(ds_path, xgb_params=config.get('xgb_params'))
            s['results']['model_id']  = metrics['model_id']
            s['results']['r2_avg']    = metrics['r2_avg']
            s['results']['mape_avg']  = metrics.get('mape_avg')
            s['progress_pct'] = 100
            s['status'] = 'completed'
            self._log(pid, f'训练完成！R²={metrics["r2_avg"]:.4f}，模型 ID={metrics["model_id"]}')

        except Exception as e:
            import traceback
            s['status'] = 'failed'
            s['error']  = str(e)
            self._log(pid, f'流水线失败: {e}')
            self._log(pid, traceback.format_exc())

    def get_state(self, pid: str) -> dict | None:
        s = self.pipelines.get(pid)
        if s is None:
            return None
        return {k: v for k, v in s.items() if k != '_resume_event'}

    def resume_after_sim(self, pid: str) -> bool:
        s = self.pipelines.get(pid)
        if s and s['status'] == 'waiting_sim':
            s['_resume_event'].set()
            return True
        return False

    def event_stream(self, pid: str):
        """SSE 生成器，每秒 yield 一次当前状态。"""
        while True:
            s = self.get_state(pid)
            if s is None:
                yield f'data: {json.dumps({"error": "未找到流水线"})}\n\n'
                break
            yield f'data: {json.dumps(s, ensure_ascii=False, default=str)}\n\n'
            if s['status'] in ('completed', 'failed'):
                break
            time.sleep(1)

    def list_pipelines(self) -> list[dict]:
        return [self.get_state(pid) for pid in self.pipelines]
```

> **注意：** `pipeline_runner.py` 中调用了 `odf_texture.generate_batch_lhs` 和
> `gis.generate_scripts_for_config` / `gis.get_configs_in_dir`。
> 这两个函数需要在后续步骤中的 `odf_texture.py` 和 `mx3_generator.py` 中实现（或验证已存在）。
> 如果 `odf_texture.py`（原 `1.py`）没有 `generate_batch_lhs` 函数，
> 则在 `pipeline_runner.py` 的阶段 0 中改为调用现有的批量 LHS 生成逻辑（与 `app.py` 中 `/api/texture/generate-batch` 端点相同的路径）。

---

## 第 6 步：修改 `mx3_generator.py`（原 generate_individual_scripts.py）

在 `SimulationConfig` 类定义的**下方**（不修改类内部内容），追加以下内容：

```python
# ── 角度模式常量（新增，不修改 DEFAULT_ANGLES）────────────────────
MOTOR_OPTIMIZATION_ANGLES = [0, 45, 90]   # RD / 45° 交叉 / TD，与 Maxwell FEA 对齐


def get_available_modes() -> dict:
    """返回所有可用角度模式的描述。"""
    return {
        'full': {
            'name':        '全极图模式',
            'angles':      SimulationConfig.DEFAULT_ANGLES,
            'description': '9 个离散角度，用于完整各向异性极图可视化和学术分析',
        },
        'motor': {
            'name':        '电机优化模式',
            'angles':      MOTOR_OPTIMIZATION_ANGLES,
            'description': '3 个角度 (RD=0°/45°/TD=90°)，直接对应 ANSYS Maxwell 各向异性材料定义，仿真时间约为全极图的 1/3',
        },
    }
```

同时，找到所有脚本生成函数（`generate_single_mode_scripts`、`generate_complex_mode_scripts` 等），
为每个函数的签名末尾**追加**可选参数 `angles: list = None`，并在函数体内将原来硬编码的
`SimulationConfig.DEFAULT_ANGLES` 替换为：

```python
angles = angles if angles is not None else SimulationConfig.DEFAULT_ANGLES
```

还需要新增一个辅助函数供 `pipeline_runner.py` 调用：

```python
def get_configs_in_dir(scripts_dir: str) -> list:
    """
    扫描 grain_scripts/<scripts_dir>/ 下的配置，返回适合传递给
    batch_scheduler.generate_multi_config_*_script 的 selected_configs 列表。
    """
    from pathlib import Path
    result = []
    base = Path(scripts_dir)
    for angle_dir in sorted(base.glob('*/angle_*')):
        config_name = angle_dir.parent.name
        angle_m = re.match(r'angle_(\d+)', angle_dir.name)
        if angle_m:
            mx3_files = list(angle_dir.glob('grain_*.mx3'))
            if mx3_files:
                result.append((config_name, {'n_grains': len(mx3_files)}, [int(angle_m.group(1))]))
    return result
```

---

## 第 7 步：在 `app.py` 末尾追加新 API 端点

在 `app.py` 现有代码**末尾**追加以下内容（`if __name__ == '__main__':` 之前）：

```python
# ════════════════════════════════════════════════════════════════════
# 新增模块（懒加载，启动失败不影响已有功能）
# ════════════════════════════════════════════════════════════════════
_dataset_builder = None
_ml_predictor    = None
_maxwell_exp     = None
_pipeline_runner = None

def _get_db():
    global _dataset_builder
    if _dataset_builder is None:
        from dataset_builder import DatasetBuilder
        _dataset_builder = DatasetBuilder()
    return _dataset_builder

def _get_ml():
    global _ml_predictor
    if _ml_predictor is None:
        from ml_trainer import BHPredictor
        _ml_predictor = BHPredictor()
    return _ml_predictor

def _get_mx():
    global _maxwell_exp
    if _maxwell_exp is None:
        import maxwell_exporter
        _maxwell_exp = maxwell_exporter
    return _maxwell_exp

def _get_pr():
    global _pipeline_runner
    if _pipeline_runner is None:
        from pipeline_runner import PipelineRunner
        _pipeline_runner = PipelineRunner()
    return _pipeline_runner


# ── 数据集管理 ──────────────────────────────────────────────────────

@app.route('/api/dataset/scan', methods=['GET'])
def dataset_scan():
    try:
        return jsonify({'configs': _get_db().scan_output_dir()})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/dataset/list', methods=['GET'])
def dataset_list():
    try:
        return jsonify({'datasets': _get_db().list_datasets()})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/dataset/aggregate', methods=['POST'])
def dataset_aggregate():
    data   = request.json or {}
    configs = data.get('configs')
    mode   = data.get('angle_mode', 'motor')
    Msat   = float(data.get('Msat', 1.52e6))
    from dataset_builder import MOTOR_ANGLES, FULL_ANGLES
    target_angles = MOTOR_ANGLES if mode == 'motor' else FULL_ANGLES
    tag    = data.get('tag', mode)
    tid    = create_task('数据集聚合')

    def do_aggregate():
        db = _get_db()
        progress_log = []
        def cb(cur, tot, name):
            progress_log.append(f'({cur}/{tot}) {name}')
            print(f'[aggregate] ({cur}/{tot}) {name}')
        df  = db.build_dataset(configs=configs, target_angles=target_angles,
                               Msat=Msat, progress_callback=cb)
        path = db.save_dataset(df, tag=tag)
        return {'dataset_path': path, 'n_samples': len(df), 'log': progress_log}

    run_task(tid, do_aggregate)
    return jsonify({'task_id': tid})


# ── ML 训练 / 推理 ─────────────────────────────────────────────────

@app.route('/api/ml/train', methods=['POST'])
def ml_train():
    data = request.json or {}
    ds   = data.get('dataset_path')
    if not ds or not Path(ds).exists():
        return jsonify({'error': '数据集文件不存在'}), 400
    tid  = create_task('XGBoost训练')
    run_task(tid, _get_ml().train,
             ds,
             xgb_params=data.get('xgb_params'),
             test_size=float(data.get('test_size', 0.2)))
    return jsonify({'task_id': tid})

@app.route('/api/ml/models', methods=['GET'])
def ml_models():
    try:
        return jsonify({'models': _get_ml().list_models()})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/ml/predict', methods=['POST'])
def ml_predict():
    data = request.json or {}
    model_id = data.get('model_id')
    params   = data.get('params', {})
    try:
        pred = _get_ml()
        if model_id:
            pred.load(model_id)
        result = pred.predict_bh(params)
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e), 'traceback': traceback.format_exc()}), 500

@app.route('/api/ml/models/<model_id>', methods=['DELETE'])
def ml_delete_model(model_id):
    ok = _get_ml().delete_model(model_id)
    return jsonify({'success': ok})


# ── Maxwell 材料导出 ────────────────────────────────────────────────

@app.route('/api/analyze/export-maxwell', methods=['POST'])
def export_maxwell():
    import numpy as np
    data     = request.json or {}
    H_RD     = data.get('H_RD', [])
    B_RD     = data.get('B_RD', [])
    H_TD     = data.get('H_TD')
    B_TD     = data.get('B_TD')
    mat_name = data.get('mat_name') or f'GO_Sim_{datetime.now().strftime("%Y%m%d_%H%M%S")}'
    thickness = float(data.get('thickness_mm', 0.35))
    try:
        mx = _get_mx()
        path = mx.export_from_bh_curves(H_RD, B_RD, H_TD, B_TD,
                                         mat_name=mat_name, thickness_mm=thickness)
        content = mx.generate_amat_content(mat_name, H_RD, B_RD, H_TD, B_TD,
                                            thickness_mm=thickness)
        return send_file(
            io.BytesIO(content.encode('utf-8')),
            mimetype='application/octet-stream',
            as_attachment=True,
            download_name=f'{mat_name}.amat'
        )
    except Exception as e:
        return jsonify({'error': str(e), 'traceback': traceback.format_exc()}), 500

@app.route('/api/analyze/export-maxwell-from-prediction', methods=['POST'])
def export_maxwell_from_prediction():
    data     = request.json or {}
    model_id = data.get('model_id')
    params   = data.get('params', {})
    mat_name = data.get('mat_name') or f'GO_Pred_{datetime.now().strftime("%Y%m%d_%H%M%S")}'
    thickness = float(data.get('thickness_mm', 0.35))
    try:
        pred = _get_ml()
        if model_id:
            pred.load(model_id)
        result  = pred.predict_bh(params)
        mx      = _get_mx()
        content = mx.generate_amat_content(
            mat_name,
            result['RD']['H'], result['RD']['B'],
            result['TD']['H'], result['TD']['B'],
            thickness_mm=thickness
        )
        mx.save_amat_file(content, mat_name)
        return send_file(
            io.BytesIO(content.encode('utf-8')),
            mimetype='application/octet-stream',
            as_attachment=True,
            download_name=f'{mat_name}.amat'
        )
    except Exception as e:
        return jsonify({'error': str(e), 'traceback': traceback.format_exc()}), 500

@app.route('/api/analyze/exports', methods=['GET'])
def list_maxwell_exports():
    try:
        return jsonify({'exports': _get_mx().list_exports()})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── 自动流水线 ──────────────────────────────────────────────────────

@app.route('/api/pipeline/start', methods=['POST'])
def pipeline_start():
    config = request.json or {}
    pid    = _get_pr().start(config)
    return jsonify({'pipeline_id': pid})

@app.route('/api/pipeline/<pid>/state', methods=['GET'])
def pipeline_state(pid):
    s = _get_pr().get_state(pid)
    if s is None:
        return jsonify({'error': '未找到该流水线'}), 404
    return jsonify(s)

@app.route('/api/pipeline/<pid>/stream')
def pipeline_stream(pid):
    def generate():
        for chunk in _get_pr().event_stream(pid):
            yield chunk
    return Response(stream_with_context(generate()),
                    mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache',
                             'X-Accel-Buffering': 'no'})

@app.route('/api/pipeline/<pid>/resume', methods=['POST'])
def pipeline_resume(pid):
    ok = _get_pr().resume_after_sim(pid)
    return jsonify({'success': ok, 'error': None if ok else '流水线未处于等待仿真状态'})

@app.route('/api/pipeline/list', methods=['GET'])
def pipeline_list():
    return jsonify({'pipelines': _get_pr().list_pipelines()})
```

---

## 第 8 步：在 `templates/index.html` 末尾追加 4 个新标签页

在 HTML 中找到现有标签按钮列表（`<button class="tab-btn" ...>` 的集合），
在末尾**追加**以下 4 个新按钮（不修改已有按钮）：

```html
<button class="tab-btn" data-target="section-dataset">📊 数据集</button>
<button class="tab-btn" data-target="section-training">🤖 XGBoost训练</button>
<button class="tab-btn" data-target="section-prediction">⚡ 快速预测</button>
<button class="tab-btn" data-target="section-pipeline">🔄 自动流水线</button>
```

在 HTML `<body>` 末尾（`</body>` 之前）追加以下 4 个 `<section>` 块：

```html
<!-- ═══════════════════════════════════════════════════════════
     数据集管理
════════════════════════════════════════════════════════════════ -->
<section id="section-dataset" class="tab-section" style="display:none">
  <h2>📊 数据集管理</h2>
  <p style="color:#666;font-size:13px">
    扫描 <code>output/</code> 目录，将仿真晶粒结果聚合为 XGBoost 训练集 CSV。
  </p>

  <div style="display:flex;gap:10px;align-items:center;margin-bottom:12px">
    <button onclick="datasetScan()">🔍 扫描可用配置</button>
    <select id="ds-angle-mode">
      <option value="motor">电机优化模式 (0°/45°/90°)</option>
      <option value="full">全极图模式 (9 个角度)</option>
    </select>
    <button onclick="datasetAggregate()">⚙️ 聚合选中配置</button>
  </div>

  <div id="ds-scan-result" style="margin-bottom:16px"></div>

  <h3 style="font-size:14px;margin-bottom:8px">已保存的训练集</h3>
  <div id="ds-list"></div>
  <button onclick="loadDatasetList()" style="margin-top:8px">🔄 刷新列表</button>
</section>


<!-- ═══════════════════════════════════════════════════════════
     XGBoost 训练
════════════════════════════════════════════════════════════════ -->
<section id="section-training" class="tab-section" style="display:none">
  <h2>🤖 XGBoost 训练</h2>

  <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:16px">
    <label>数据集
      <select id="train-dataset" style="width:100%"></select>
    </label>
    <label>测试集比例
      <input type="number" id="train-test-size" value="0.2" min="0.1" max="0.4" step="0.05" style="width:100%">
    </label>
    <label>n_estimators
      <input type="number" id="xgb-n-est" value="300" style="width:100%">
    </label>
    <label>max_depth
      <input type="number" id="xgb-depth" value="6" style="width:100%">
    </label>
    <label>learning_rate
      <input type="number" id="xgb-lr" value="0.05" step="0.005" style="width:100%">
    </label>
    <label>subsample
      <input type="number" id="xgb-sub" value="0.8" step="0.05" style="width:100%">
    </label>
  </div>

  <div style="display:flex;gap:10px;margin-bottom:16px">
    <button onclick="startTraining()">🚀 开始训练</button>
    <button onclick="loadDatasetListForTraining()">🔄 刷新数据集</button>
  </div>

  <div id="train-status" style="margin-bottom:16px"></div>

  <div id="train-results" style="display:none">
    <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:16px">
      <div style="background:#f5f5f5;padding:12px;border-radius:6px;text-align:center">
        <div style="font-size:12px;color:#666">平均 R²</div>
        <div style="font-size:24px;font-weight:600" id="res-r2">—</div>
      </div>
      <div style="background:#f5f5f5;padding:12px;border-radius:6px;text-align:center">
        <div style="font-size:12px;color:#666">平均 MAPE</div>
        <div style="font-size:24px;font-weight:600" id="res-mape">—</div>
      </div>
      <div style="background:#f5f5f5;padding:12px;border-radius:6px;text-align:center">
        <div style="font-size:12px;color:#666">模型 ID</div>
        <div style="font-size:13px;font-weight:600;word-break:break-all" id="res-model-id">—</div>
      </div>
    </div>
    <canvas id="fi-chart" height="120"></canvas>
  </div>

  <h3 style="font-size:14px;margin:16px 0 8px">已保存的模型</h3>
  <div id="model-list"></div>
  <button onclick="loadModelList()">🔄 刷新模型列表</button>
</section>


<!-- ═══════════════════════════════════════════════════════════
     快速预测（XGBoost 推理）
════════════════════════════════════════════════════════════════ -->
<section id="section-prediction" class="tab-section" style="display:none">
  <h2>⚡ 快速预测</h2>
  <p style="color:#666;font-size:13px">选择已训练的模型，输入织构参数，毫秒级返回 B-H 曲线。</p>

  <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:16px">
    <label>模型
      <select id="pred-model" style="width:100%"></select>
    </label>
    <label>f<sub>Goss</sub> <small>(0.4-0.9)</small>
      <input type="number" id="pred-fgoss" value="0.70" min="0.4" max="0.9" step="0.01" style="width:100%">
    </label>
    <label>θ₀ [°] <small>(1-30)</small>
      <input type="number" id="pred-theta" value="15" min="1" max="30" step="1" style="width:100%">
    </label>
    <label>半宽 σ [°] <small>(5-15)</small>
      <input type="number" id="pred-hw" value="10" min="5" max="15" step="1" style="width:100%">
    </label>
    <label>晶粒数 N
      <input type="number" id="pred-n" value="100" min="10" max="300" step="10" style="width:100%">
    </label>
    <label>Si 含量 [%]
      <input type="number" id="pred-si" value="3.0" min="2.5" max="3.5" step="0.1" style="width:100%">
    </label>
  </div>

  <div style="display:flex;gap:10px;margin-bottom:16px">
    <button onclick="runPrediction()">⚡ 立即预测</button>
    <button onclick="loadModelListForPred()">🔄 刷新模型</button>
  </div>

  <div id="pred-result" style="display:none">
    <canvas id="bh-chart" height="200" style="margin-bottom:16px"></canvas>
    <div id="pred-scalars" style="margin-bottom:12px;font-size:13px"></div>

    <div style="display:flex;gap:10px;flex-wrap:wrap">
      <button onclick="exportMaxwellFromPred()">⬇️ 导出 Maxwell .amat</button>
      <button onclick="copyBHData()">📋 复制 B-H 数据 (TSV)</button>
    </div>
  </div>

  <!-- Maxwell 导出对话框（内联，非 fixed） -->
  <div id="maxwell-pred-dialog" style="display:none;margin-top:12px;
       padding:14px;border:1px solid #ddd;border-radius:6px;background:#fafafa">
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:10px">
      <label>材料名称
        <input id="pred-mat-name" placeholder="GO_Pred_F70_T15" style="width:100%">
      </label>
      <label>厚度 (mm)
        <input id="pred-thickness" value="0.35" type="number" step="0.01" style="width:100%">
      </label>
    </div>
    <button onclick="doExportMaxwellFromPred()">⬇️ 下载 .amat</button>
    <button onclick="document.getElementById('maxwell-pred-dialog').style.display='none'"
            style="margin-left:8px">取消</button>
  </div>
</section>


<!-- ═══════════════════════════════════════════════════════════
     自动流水线
════════════════════════════════════════════════════════════════ -->
<section id="section-pipeline" class="tab-section" style="display:none">
  <h2>🔄 自动训练流水线</h2>
  <p style="color:#666;font-size:13px">一键从织构参数采样到 XGBoost 模型，含仿真脚本自动生成。</p>

  <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:16px">
    <label>样本数
      <input type="number" id="pipe-n" value="20" min="5" max="300" style="width:100%">
    </label>
    <label>角度模式
      <select id="pipe-angle-mode" style="width:100%">
        <option value="motor">电机优化 (0°/45°/90°)  —  推荐</option>
        <option value="full">全极图 (9 个角度)</option>
      </select>
    </label>
    <label>f<sub>Goss</sub> 范围
      <div style="display:flex;gap:6px">
        <input type="number" id="pipe-fg-lo" value="0.4" step="0.05" style="flex:1">
        <span>~</span>
        <input type="number" id="pipe-fg-hi" value="0.9" step="0.05" style="flex:1">
      </div>
    </label>
    <label>θ₀ [°] 范围
      <div style="display:flex;gap:6px">
        <input type="number" id="pipe-th-lo" value="1" style="flex:1">
        <span>~</span>
        <input type="number" id="pipe-th-hi" value="30" style="flex:1">
      </div>
    </label>
    <label>半宽 σ [°] 范围
      <div style="display:flex;gap:6px">
        <input type="number" id="pipe-hw-lo" value="5" style="flex:1">
        <span>~</span>
        <input type="number" id="pipe-hw-hi" value="15" style="flex:1">
      </div>
    </label>
    <label>晶粒数 N 范围
      <div style="display:flex;gap:6px">
        <input type="number" id="pipe-n-lo" value="50" style="flex:1">
        <span>~</span>
        <input type="number" id="pipe-n-hi" value="150" style="flex:1">
      </div>
    </label>
  </div>

  <button onclick="startPipeline()" style="margin-bottom:16px">▶ 启动流水线</button>

  <div id="pipeline-panel" style="display:none">
    <!-- 阶段进度 -->
    <div id="pipeline-stages" style="margin-bottom:14px"></div>

    <!-- 等待仿真提示 -->
    <div id="pipeline-sim-wait" style="display:none;background:#fff8e1;
         border:1px solid #f0c040;border-radius:6px;padding:12px;margin-bottom:12px">
      <strong>⏳ 仿真脚本已生成</strong>
      <p id="sim-script-name" style="font-family:monospace;margin:6px 0"></p>
      <p style="font-size:13px;margin:0 0 10px">请在 GPU 机器上运行该脚本，完成后点击下方按钮继续。</p>
      <button onclick="resumePipeline()">✅ 仿真已完成，继续</button>
    </div>

    <!-- 实时日志 -->
    <div style="font-size:12px;font-family:monospace;background:#111;color:#0f0;
         padding:10px;border-radius:4px;height:200px;overflow-y:auto" id="pipe-log"></div>

    <!-- 完成结果 -->
    <div id="pipeline-complete" style="display:none;background:#e8f5e9;
         border:1px solid #81c784;border-radius:6px;padding:14px;margin-top:12px">
      <strong>🎉 流水线完成！</strong>
      <div id="pipe-final-results" style="margin-top:8px;font-size:13px"></div>
      <button onclick="switchToTab('section-prediction')" style="margin-top:10px">
        → 前往快速预测
      </button>
    </div>
  </div>
</section>
```

在 HTML 末尾 `</body>` 之前，追加以下 JavaScript（内联 `<script>` 标签）：

```html
<script>
// ═══════════════════════════════════════════════════
// 数据集管理
// ═══════════════════════════════════════════════════
let _selectedConfigs = [];
let _bhChartInstance = null;
let _fiChartInstance = null;
let _currentPredResult = null;
let _currentPipelineId = null;

function datasetScan() {
  document.getElementById('ds-scan-result').innerHTML = '扫描中...';
  fetch('/api/dataset/scan').then(r => r.json()).then(data => {
    if (data.error) { alert(data.error); return; }
    const configs = data.configs || [];
    _selectedConfigs = configs.map(c => c.config_name);
    const html = configs.length === 0
      ? '<p style="color:#999">未找到可聚合的配置目录</p>'
      : `<table style="width:100%;font-size:13px;border-collapse:collapse">
          <tr style="background:#f0f0f0"><th>配置名</th><th>晶粒数</th><th>可用角度</th></tr>
          ${configs.map(c => `
            <tr style="border-bottom:1px solid #eee">
              <td style="padding:4px 8px;font-family:monospace">${c.config_name}</td>
              <td style="padding:4px 8px;text-align:center">${c.n_grains}</td>
              <td style="padding:4px 8px">${c.available_angles.join(', ')}°</td>
            </tr>`).join('')}
        </table><p style="font-size:12px;color:#666;margin-top:6px">已选择全部 ${configs.length} 个配置</p>`;
    document.getElementById('ds-scan-result').innerHTML = html;
  });
}

function datasetAggregate() {
  const mode = document.getElementById('ds-angle-mode').value;
  fetch('/api/dataset/aggregate', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({configs: _selectedConfigs.length ? _selectedConfigs : null,
                          angle_mode: mode})
  }).then(r => r.json()).then(d => {
    if (d.error) { alert(d.error); return; }
    pollTask(d.task_id, res => {
      alert(`聚合完成！${res.n_samples} 个样本 → ${res.dataset_path}`);
      loadDatasetList();
    });
  });
}

function loadDatasetList() {
  fetch('/api/dataset/list').then(r => r.json()).then(d => {
    const ds = d.datasets || [];
    document.getElementById('ds-list').innerHTML = ds.length === 0
      ? '<p style="color:#999">暂无数据集</p>'
      : ds.map(x => `<div style="display:flex;gap:8px;align-items:center;padding:6px;
           border-bottom:1px solid #eee;font-size:13px">
           <span style="flex:1;font-family:monospace">${x.name}</span>
           <span style="color:#666">${x.rows} 行</span>
           <span style="color:#666">${x.cols} 列</span>
           <span style="color:#999;font-size:12px">${x.created}</span>
         </div>`).join('');
  });
}

// ═══════════════════════════════════════════════════
// XGBoost 训练
// ═══════════════════════════════════════════════════
function loadDatasetListForTraining() {
  fetch('/api/dataset/list').then(r => r.json()).then(d => {
    const sel = document.getElementById('train-dataset');
    sel.innerHTML = (d.datasets || []).map(x =>
      `<option value="${x.path}">${x.name} (${x.rows} 行)</option>`).join('');
  });
}

function startTraining() {
  const ds = document.getElementById('train-dataset').value;
  if (!ds) { alert('请先选择或生成数据集'); return; }
  const xgbParams = {
    n_estimators:  parseInt(document.getElementById('xgb-n-est').value),
    max_depth:     parseInt(document.getElementById('xgb-depth').value),
    learning_rate: parseFloat(document.getElementById('xgb-lr').value),
    subsample:     parseFloat(document.getElementById('xgb-sub').value),
  };
  document.getElementById('train-status').textContent = '训练中，请稍候...';
  document.getElementById('train-results').style.display = 'none';
  fetch('/api/ml/train', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({dataset_path: ds, xgb_params: xgbParams,
                          test_size: parseFloat(document.getElementById('train-test-size').value)})
  }).then(r => r.json()).then(d => {
    if (d.error) { alert(d.error); return; }
    pollTask(d.task_id, res => {
      document.getElementById('train-status').textContent = '训练完成！';
      document.getElementById('res-r2').textContent = res.r2_avg != null ? res.r2_avg.toFixed(4) : '—';
      document.getElementById('res-mape').textContent = res.mape_avg != null
        ? (res.mape_avg * 100).toFixed(2) + '%' : '—';
      document.getElementById('res-model-id').textContent = res.model_id || '—';
      document.getElementById('train-results').style.display = 'block';
      // 特征重要性图
      renderFeatureImportance(res.feature_importance || {});
      loadModelList();
    });
  });
}

function renderFeatureImportance(fi) {
  if (typeof Chart === 'undefined') return;
  const labels = Object.keys(fi);
  const vals   = Object.values(fi).map(v => parseFloat(v.toFixed(4)));
  const ctx    = document.getElementById('fi-chart').getContext('2d');
  if (_fiChartInstance) _fiChartInstance.destroy();
  _fiChartInstance = new Chart(ctx, {
    type: 'bar',
    data: {labels, datasets: [{label: '特征重要性', data: vals,
                                backgroundColor: '#378ADD80', borderColor: '#378ADD', borderWidth: 1}]},
    options: {responsive: true, plugins: {legend: {display: false}},
              scales: {y: {beginAtZero: true}}}
  });
}

function loadModelList() {
  fetch('/api/ml/models').then(r => r.json()).then(d => {
    const models = d.models || [];
    document.getElementById('model-list').innerHTML = models.length === 0
      ? '<p style="color:#999">暂无已训练模型</p>'
      : models.map(m => `<div style="display:flex;gap:8px;align-items:center;padding:6px;
             border-bottom:1px solid #eee;font-size:13px">
             <span style="flex:1;font-family:monospace">${m.model_id}</span>
             <span style="color:#1D9E75">R²=${m.r2_avg != null ? m.r2_avg.toFixed(3) : '—'}</span>
             <span style="color:#666">${m.n_samples} 样本</span>
             <button onclick="deleteModel('${m.model_id}')" style="padding:2px 8px">删除</button>
           </div>`).join('');
  });
}

function deleteModel(mid) {
  if (!confirm('确定删除模型 ' + mid + ' ?')) return;
  fetch('/api/ml/models/' + mid, {method: 'DELETE'}).then(() => loadModelList());
}

// ═══════════════════════════════════════════════════
// 快速预测
// ═══════════════════════════════════════════════════
function loadModelListForPred() {
  fetch('/api/ml/models').then(r => r.json()).then(d => {
    const sel = document.getElementById('pred-model');
    const models = d.models || [];
    sel.innerHTML = models.map(m =>
      `<option value="${m.model_id}">${m.model_id} (R²=${m.r2_avg != null ? m.r2_avg.toFixed(3) : '?'})</option>`
    ).join('') || '<option value="">暂无模型</option>';
  });
}

function runPrediction() {
  const mid = document.getElementById('pred-model').value;
  if (!mid) { alert('请先训练或加载一个模型'); return; }
  const params = {
    f_Goss:       parseFloat(document.getElementById('pred-fgoss').value),
    theta_0_deg:  parseFloat(document.getElementById('pred-theta').value),
    halfwidth_deg: parseFloat(document.getElementById('pred-hw').value),
    N_grains:     parseInt(document.getElementById('pred-n').value),
    Si_content:   parseFloat(document.getElementById('pred-si').value),
  };
  fetch('/api/ml/predict', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({model_id: mid, params})
  }).then(r => r.json()).then(data => {
    if (data.error) { alert(data.error); return; }
    _currentPredResult = data;
    document.getElementById('pred-result').style.display = 'block';
    renderBHChart(data);
    renderScalars(data.scalars || {});
  });
}

function renderBHChart(data) {
  if (typeof Chart === 'undefined') return;
  const H = data.RD?.H || [];
  const makeDs = (key, label, color, dash) => ({
    label, data: (data[key]?.B || []).map((b,i) => ({x: H[i], y: b})),
    borderColor: color, backgroundColor: color+'30',
    borderDash: dash, pointRadius: 3, tension: 0.3
  });
  const ctx = document.getElementById('bh-chart').getContext('2d');
  if (_bhChartInstance) _bhChartInstance.destroy();
  _bhChartInstance = new Chart(ctx, {
    type: 'line',
    data: {datasets: [
      makeDs('RD',      'RD (0°)',    '#185FA5', []),
      makeDs('Cross45', '45°',        '#1D9E75', [5,3]),
      makeDs('TD',      'TD (90°)',   '#D85A30', [2,2]),
    ]},
    options: {responsive: true,
              scales: {x: {type:'linear', title:{display:true, text:'H (A/m)'}},
                       y: {title:{display:true, text:'B (T)'}}}}
  });
}

function renderScalars(s) {
  const rows = [0,45,90].map(a => `
    <tr>
      <td style="padding:3px 8px;font-weight:500">${a}°</td>
      <td style="padding:3px 8px">${(s[`Hc_${a}deg`]||0).toFixed(1)} A/m</td>
      <td style="padding:3px 8px">${(s[`Mr_${a}deg`]||0).toFixed(4)} A/m</td>
      <td style="padding:3px 8px">${(s[`mu_max_${a}deg`]||0).toFixed(1)}</td>
    </tr>`).join('');
  document.getElementById('pred-scalars').innerHTML =
    `<table style="font-size:12px;border-collapse:collapse">
       <tr style="background:#f0f0f0"><th style="padding:3px 8px">方向</th>
         <th>Hc</th><th>Mr</th><th>μ_max</th></tr>${rows}</table>`;
}

function exportMaxwellFromPred() {
  document.getElementById('maxwell-pred-dialog').style.display = 'block';
}

function doExportMaxwellFromPred() {
  if (!_currentPredResult) return;
  const matName  = document.getElementById('pred-mat-name').value || 'GO_Pred';
  const thickness = parseFloat(document.getElementById('pred-thickness').value) || 0.35;
  fetch('/api/analyze/export-maxwell-from-prediction', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      model_id:     document.getElementById('pred-model').value,
      params: {
        f_Goss:        parseFloat(document.getElementById('pred-fgoss').value),
        theta_0_deg:   parseFloat(document.getElementById('pred-theta').value),
        halfwidth_deg: parseFloat(document.getElementById('pred-hw').value),
        N_grains:      parseInt(document.getElementById('pred-n').value),
        Si_content:    parseFloat(document.getElementById('pred-si').value),
      },
      mat_name: matName,
      thickness_mm: thickness
    })
  }).then(r => r.blob()).then(blob => {
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = matName + '.amat';
    a.click();
    document.getElementById('maxwell-pred-dialog').style.display = 'none';
  });
}

function copyBHData() {
  if (!_currentPredResult) return;
  const rd = _currentPredResult.RD || {};
  const td = _currentPredResult.TD || {};
  const H  = rd.H || [];
  const lines = ['H (A/m)\tB_RD (T)\tB_TD (T)'];
  H.forEach((h,i) => lines.push(`${h}\t${(rd.B||[])[i]||''}\t${(td.B||[])[i]||''}`));
  navigator.clipboard.writeText(lines.join('\n'))
    .then(() => alert('B-H 数据已复制到剪贴板 (TSV 格式)'));
}

// ═══════════════════════════════════════════════════
// 自动流水线
// ═══════════════════════════════════════════════════
function startPipeline() {
  const config = {
    n_samples:      parseInt(document.getElementById('pipe-n').value),
    angle_mode:     document.getElementById('pipe-angle-mode').value,
    f_Goss_range:   [parseFloat(document.getElementById('pipe-fg-lo').value),
                     parseFloat(document.getElementById('pipe-fg-hi').value)],
    theta_0_range:  [parseFloat(document.getElementById('pipe-th-lo').value),
                     parseFloat(document.getElementById('pipe-th-hi').value)],
    halfwidth_range:[parseFloat(document.getElementById('pipe-hw-lo').value),
                     parseFloat(document.getElementById('pipe-hw-hi').value)],
    N_grains_range: [parseInt(document.getElementById('pipe-n-lo').value),
                     parseInt(document.getElementById('pipe-n-hi').value)],
    Si_content:     3.0,
  };
  fetch('/api/pipeline/start', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(config)
  }).then(r => r.json()).then(d => {
    if (d.error) { alert(d.error); return; }
    _currentPipelineId = d.pipeline_id;
    document.getElementById('pipeline-panel').style.display = 'block';
    document.getElementById('pipeline-complete').style.display = 'none';
    subscribePipeline(d.pipeline_id);
  });
}

function subscribePipeline(pid) {
  const logEl = document.getElementById('pipe-log');
  const stagesEl = document.getElementById('pipeline-stages');
  const waitEl   = document.getElementById('pipeline-sim-wait');
  const doneEl   = document.getElementById('pipeline-complete');

  const stageNames = {
    texture_gen: '织构生成', script_gen: 'MX3脚本',
    batch_gen: '批处理脚本', wait_sim: '等待仿真',
    analyze: '结果聚合', train: 'XGBoost训练'
  };
  const STAGES = ['texture_gen','script_gen','batch_gen','wait_sim','analyze','train'];

  const es = new EventSource(`/api/pipeline/${pid}/stream`);
  es.onmessage = e => {
    const s = JSON.parse(e.data);
    if (s.error && !s.stage) { alert('流水线错误: ' + s.error); es.close(); return; }

    // 阶段进度条
    stagesEl.innerHTML = STAGES.map((st, i) => {
      const done    = i < s.stage_index;
      const current = st === s.stage;
      const pct     = current ? s.progress_pct : (done ? 100 : 0);
      const bg      = done ? '#1D9E75' : current ? '#378ADD' : '#ddd';
      return `<div style="margin-bottom:8px">
        <div style="display:flex;justify-content:space-between;font-size:12px;margin-bottom:2px">
          <span>${done?'✓':current?'▶':' '} ${stageNames[st]}</span>
          <span>${done?'完成':current?pct+'%':'等待'}</span>
        </div>
        <div style="background:#eee;border-radius:3px;height:6px">
          <div style="background:${bg};width:${pct}%;height:6px;border-radius:3px;transition:width .3s"></div>
        </div></div>`;
    }).join('');

    // 等待仿真提示
    if (s.status === 'waiting_sim') {
      waitEl.style.display = 'block';
      const scr = s.results?.batch_script || '';
      document.getElementById('sim-script-name').textContent = scr;
    } else {
      waitEl.style.display = 'none';
    }

    // 日志
    if (s.log && s.log.length) {
      logEl.innerHTML = s.log.slice(-80).map(l =>
        `<div>${l.replace(/</g,'&lt;')}</div>`).join('');
      logEl.scrollTop = logEl.scrollHeight;
    }

    // 完成
    if (s.status === 'completed') {
      es.close();
      doneEl.style.display = 'block';
      const r = s.results || {};
      document.getElementById('pipe-final-results').innerHTML =
        `<div>数据集: <code>${r.dataset_path || '—'}</code> (${r.n_samples || '?'} 个样本)</div>
         <div>模型 ID: <code>${r.model_id || '—'}</code></div>
         <div>R² = ${r.r2_avg != null ? r.r2_avg.toFixed(4) : '—'}</div>`;
      loadModelListForPred();
    }
    if (s.status === 'failed') {
      es.close();
      logEl.style.color = '#f00';
    }
  };
}

function resumePipeline() {
  if (!_currentPipelineId) return;
  fetch(`/api/pipeline/${_currentPipelineId}/resume`, {method: 'POST'})
    .then(r => r.json()).then(d => {
      if (!d.success) alert(d.error || '无法继续');
      document.getElementById('pipeline-sim-wait').style.display = 'none';
    });
}

function switchToTab(sectionId) {
  document.querySelectorAll('.tab-section').forEach(s => s.style.display = 'none');
  const target = document.getElementById(sectionId);
  if (target) target.style.display = 'block';
}

// ═══════════════════════════════════════════════════
// 分析页面：Maxwell 导出按钮支持（追加到现有 export 逻辑旁）
// 在现有存储 analysisResult 的地方，追加以下函数：
// ═══════════════════════════════════════════════════
let _rdBH = null;   // {H, B} 缓存来自最近一次分析（0°）
let _tdBH = null;   // {H, B} 缓存来自最近一次分析（90°）

// 若已有 window.analysisResult 则直接调用；
// 如无法自动识别方向，则弹出说明让用户先分析 0° 再分析 90°。
function exportCurrentToMaxwell() {
  let H, B;
  if (window.analysisResult) {
    H = window.analysisResult.H_curve;
    B = window.analysisResult.B_curve;
  } else {
    alert('请先在分析页面上传并分析一个仿真文件');
    return;
  }
  document.getElementById('maxwell-inline-dialog').style.display = 'block';
  window._pendingExportRD = {H, B};
}

function doExportCurrentMaxwell() {
  const rd = window._pendingExportRD;
  if (!rd) return;
  const matName   = document.getElementById('inline-mat-name').value || 'GO_Sim';
  const thickness = parseFloat(document.getElementById('inline-thickness').value) || 0.35;
  fetch('/api/analyze/export-maxwell', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({H_RD: rd.H, B_RD: rd.B, mat_name: matName, thickness_mm: thickness})
  }).then(r => r.blob()).then(blob => {
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = matName + '.amat';
    a.click();
    document.getElementById('maxwell-inline-dialog').style.display = 'none';
  });
}

// ── 页面初始化 ──────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  loadDatasetList();
  loadDatasetListForTraining();
  loadModelList();
  loadModelListForPred();
});
</script>
```

同时，在现有分析结果展示区域（有"导出 FEMM"按钮的地方旁）追加：

```html
<!-- 追加在 export-femm-btn 附近 -->
<button id="export-maxwell-inline-btn" onclick="exportCurrentToMaxwell()">
  ⚙️ 导出 Maxwell .amat
</button>

<div id="maxwell-inline-dialog" style="display:none;margin-top:10px;
     padding:12px;border:1px solid #ccc;border-radius:6px;background:#f9f9f9">
  <label>材料名称 <input id="inline-mat-name" placeholder="GO_Sim" style="width:200px"></label>
  <label style="margin-left:10px">厚度 (mm)
    <input id="inline-thickness" value="0.35" type="number" step="0.01" style="width:70px">
  </label>
  <button onclick="doExportCurrentMaxwell()" style="margin-left:10px">⬇️ 下载</button>
  <button onclick="document.getElementById('maxwell-inline-dialog').style.display='none'"
          style="margin-left:6px">取消</button>
</div>
```

---

## 第 9 步：安装依赖

```powershell
cd "C:\Users\10760\Desktop\YUCE"
pip install xgboost scikit-learn
# scipy、pandas、numpy 应已安装（see.py 使用）；若未安装：
pip install scipy pandas numpy
```

Chart.js（B-H 图表 + 特征重要性图）已在 HTML 中通过 CDN 引入，
在现有 `<head>` 部分追加（若未引入）：

```html
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
```

---

## 验证检查清单

实施完成后，在 `C:\Users\10760\Desktop\YUCE\` 目录下逐一确认：

```
[ ] odf_texture.py       存在（原 1.py 已重命名）
[ ] bh_extractor.py      存在（原 see.py 已重命名）
[ ] mx3_generator.py     存在（原 generate_individual_scripts.py 已重命名）
[ ] batch_scheduler.py   存在（原 generate_parallel_batch.py 已重命名）
[ ] app.py               import 已更新（gis/gpb/see_module 均已对应新名称）
[ ] dataset_builder.py   创建完成
[ ] ml_trainer.py        创建完成
[ ] maxwell_exporter.py  创建完成
[ ] pipeline_runner.py   创建完成
[ ] data/datasets/       目录存在
[ ] data/models/         目录存在
[ ] data/exports/        目录存在
[ ] python app.py        能正常启动（无 ImportError）
[ ] GET /api/dataset/scan   返回 200
[ ] GET /api/ml/models      返回 200
[ ] GET /api/analyze/exports 返回 200
[ ] HTML 中出现 4 个新标签页按钮
```

---

## 文件命名说明（归档）

| 新文件名 | 原文件名 | 含义 |
|---|---|---|
| `odf_texture.py` | `1.py` | Orientation Distribution Function 织构生成 |
| `bh_extractor.py` | `see.py` | B-H curve extractor，磁滞回线提取 |
| `mx3_generator.py` | `generate_individual_scripts.py` | MuMax3 仿真脚本代码生成器 |
| `batch_scheduler.py` | `generate_parallel_batch.py` | 并行批处理调度脚本生成器 |
| `dataset_builder.py` | *(新增)* | 仿真结果→训练集聚合 |
| `ml_trainer.py` | *(新增)* | XGBoost 训练与推理 |
| `maxwell_exporter.py` | *(新增)* | ANSYS Maxwell .amat 材料文件生成 |
| `pipeline_runner.py` | *(新增)* | 自动流水线编排（SSE 进度推送） |
