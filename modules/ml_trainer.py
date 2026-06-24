"""
ml_trainer.py
XGBoost 多输出回归：从 5 个织构参数预测 B-H 曲线（3 个角度 × 8 个 H 节点 + 9 个标量目标）。
"""
import os
import pickle
import json
import hashlib
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd
from sklearn.multioutput import MultiOutputRegressor
from sklearn.preprocessing import StandardScaler, PolynomialFeatures
from sklearn.model_selection import train_test_split
from sklearn.metrics import r2_score, mean_absolute_percentage_error
from sklearn.ensemble import ExtraTreesRegressor
from sklearn.linear_model import Ridge
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import Matern, WhiteKernel, ConstantKernel as GPConstant
from sklearn.pipeline import Pipeline
from xgboost import XGBRegressor

from anisotropy_interpolator import interpolate_full_direction
from physics_calibrator import calibrate_material_pair, calibrate_scalar_targets

STANDARD_H_POINTS = [100, 200, 500, 1000, 2000, 3000, 5000, 7500]
FEATURE_COLS = ['f_Goss', 'theta_0_deg', 'halfwidth_deg', 'N_grains', 'Si_content']

# 角度 → 列前缀映射（供 predict_bh 使用）
ANGLE_PREFIX = {0: 'B_0deg', 45: 'B_45deg', 90: 'B_90deg'}
SCALAR_TARGET_TEMPLATES = ['Hc_{a}deg', 'Mr_{a}deg', 'mu_max_{a}deg']
DEFAULT_TARGET_ANGLES = [0, 90]
TD_REAL_H_GRID = [
    1, 2, 5, 10, 20, 50, 100, 200, 500, 800, 1000,
    1500, 2000, 3000, 5000, 7500, 10000, 20000, 50000,
]
PREDICT_PARAM_RANGES = {
    'f_Goss': (0.4, 0.9),
    'theta_0_deg': (1.0, 30.0),
    'halfwidth_deg': (5.0, 15.0),
    'Si_content': (2.5, 6.0),
    'N_grains': (1.0, 12.0),
}
PREDICT_PARAM_WEIGHTS = {
    'f_Goss': 0.35,
    'theta_0_deg': 0.25,
    'halfwidth_deg': 0.18,
    'Si_content': 0.17,
    'N_grains': 0.05,
}
RD_SI_KNEE_ANCHOR_PERCENT = 6.0

# 预测时 N_grains 由模型内部自动填充（不再从 UI 获取）
DEFAULT_N_GRAINS = 5

SUPPORTED_MODEL_TYPES = ['direct_xgb', 'extra_trees', 'gaussian_process', 'ridge_poly']

DEFAULT_XGB_PARAMS = {
    'n_estimators':     100,   # 小样本降低复杂度
    'max_depth':        3,
    'learning_rate':    0.1,
    'subsample':        0.8,
    'colsample_bytree': 0.8,
    'min_child_weight': 3,
    'random_state':     42,
    'eval_metric':      'rmse',
    'verbosity':        0,
}

DEFAULT_ET_PARAMS = {
    'n_estimators':      200,
    'max_depth':         4,
    'min_samples_split': 4,
    'min_samples_leaf':  2,
    'random_state':      42,
    'n_jobs':            -1,
}

DEFAULT_GP_PARAMS = {
    'kernel_nu':             2.5,   # Matérn smoothness
    'n_restarts_optimizer':  5,
    'alpha':                 1e-4,  # noise regularization
    'normalize_y':           True,
}

DEFAULT_RIDGE_POLY_PARAMS = {
    'degree': 2,
    'alpha':  1.0,
}


def _build_model(model_type: str, params: dict):
    """构造 sklearn estimator，MultiOutputRegressor 包装。"""
    if model_type == 'gaussian_process':
        nu      = float(params.get('kernel_nu', 2.5))
        alpha   = float(params.get('alpha', 1e-4))
        n_rest  = int(params.get('n_restarts_optimizer', 5))
        norm_y  = bool(params.get('normalize_y', True))
        kernel  = GPConstant(1.0, (1e-3, 1e3)) * Matern(length_scale=1.0, nu=nu) \
                  + WhiteKernel(noise_level=alpha, noise_level_bounds=(1e-8, 1.0))
        gpr = GaussianProcessRegressor(
            kernel=kernel,
            n_restarts_optimizer=n_rest,
            normalize_y=norm_y,
        )
        return MultiOutputRegressor(gpr, n_jobs=-1)
    if model_type == 'ridge_poly':
        degree = int(params.get('degree', 2))
        alpha  = float(params.get('alpha', 1.0))
        pipe = Pipeline([
            ('poly',  PolynomialFeatures(degree=degree, include_bias=True)),
            ('ridge', Ridge(alpha=alpha)),
        ])
        return MultiOutputRegressor(pipe, n_jobs=-1)
    if model_type == 'extra_trees':
        et_params = {k: v for k, v in params.items()
                     if k in ['n_estimators', 'max_depth', 'min_samples_split',
                               'min_samples_leaf', 'random_state', 'n_jobs']}
        return MultiOutputRegressor(ExtraTreesRegressor(**et_params))
    # default: direct_xgb
    xgb_keys = ['n_estimators', 'max_depth', 'learning_rate', 'subsample',
                 'colsample_bytree', 'min_child_weight', 'random_state', 'eval_metric', 'verbosity']
    xgb_params = {k: v for k, v in params.items() if k in xgb_keys}
    return MultiOutputRegressor(XGBRegressor(**xgb_params), n_jobs=-1)


MIN_TRAIN_SAMPLES = 5
MIN_RELIABLE_EVAL_SAMPLES = 24
MIN_RELIABLE_TEST_SAMPLES = 4
BH_MAPE_MIN_ABS_T = 0.05


def _build_target_cols(df_columns: list) -> list:
    """从 DataFrame 列名中自动检测有效的目标列。"""
    candidates = []
    for angle in DEFAULT_TARGET_ANGLES:
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


def _primary_target_cols(cols: list[str]) -> list[str]:
    """Headline metrics are based on B-H curve targets, not low-confidence scalars."""
    return [c for c in cols if c.startswith('B_')]


def _dataset_bh_reference_corrected(dataset_path: str | None) -> bool:
    """Infer whether a saved model was trained on reference-corrected B-H targets."""
    if not dataset_path:
        return False
    path = Path(dataset_path)
    if not path.exists():
        path = Path(str(dataset_path).replace('\\', '/'))
    if not path.exists():
        return False
    try:
        df = pd.read_csv(path, usecols=lambda c: c == 'bh_reference_corrected')
        if 'bh_reference_corrected' not in df.columns or df.empty:
            return False
        return float(df['bh_reference_corrected'].astype(bool).mean()) > 0.5
    except Exception:
        return False


def _odf_for_reference_weights(params: dict) -> dict:
    """Normalize UI/model ODF parameter names for reference_corrector.anchor_weights."""
    return {
        'f_Goss': float(params.get('f_Goss', 0.82)),
        'theta_mean_deg': float(params.get('theta_0_deg', params.get('theta_mean_deg', 6.0))),
        'sigma_deg': float(params.get('halfwidth_deg', params.get('sigma_deg', 8.0))),
    }


def _weighted_reference_bh(odf_params: dict, direction: str, h_real: np.ndarray) -> np.ndarray:
    """Reference-weighted B-H curve in the real-H domain for quick-prediction TD output."""
    from reference_corrector import anchor_weights, load_reference_bh

    h = np.asarray(h_real, dtype=float)
    odf = _odf_for_reference_weights(odf_params)
    weights = anchor_weights(odf)
    b_out = np.zeros(len(h), dtype=float)
    for grade, weight in weights.items():
        if weight < 1e-6:
            continue
        h_ref, b_ref = load_reference_bh(grade, direction)
        b_out += weight * np.interp(h, h_ref, b_ref, left=0.0, right=float(b_ref[-1]))
    return np.clip(b_out, 0.0, 2.5)


def _norm_param(params: dict, key: str) -> float:
    lo, hi = PREDICT_PARAM_RANGES[key]
    val = float(params.get(key, (lo + hi) * 0.5))
    return float(np.clip((val - lo) / max(hi - lo, 1e-12), 0.0, 1.0))


def _prediction_diversity(params: dict, trained_features: list[str]) -> dict:
    """
    Deterministic latent variation for dimensions absent from the fitted model.

    Small paper/smoke datasets often drop halfwidth/Si/N as constants.  Without
    this projection those UI inputs cannot affect the curves at all.  The seed is
    derived from the full input vector, so predictions remain repeatable.
    """
    trained = set(trained_features or [])
    missing_weight = sum(
        PREDICT_PARAM_WEIGHTS.get(k, 0.0)
        for k in PREDICT_PARAM_RANGES
        if k not in trained
    )
    if missing_weight <= 0:
        return {
            'missing_weight': 0.0,
            'effective_odf': {
                'f_Goss': float(params.get('f_Goss', 0.82)),
                'theta_0_deg': float(params.get('theta_0_deg', 6.0)),
                'halfwidth_deg': float(params.get('halfwidth_deg', 8.0)),
            },
            'rd_b_gain': 1.0,
            'td_b_gain': 1.0,
            'td_h_factor': 1.0,
            'seed': None,
        }

    normed = {k: _norm_param(params, k) for k in PREDICT_PARAM_RANGES}
    seed_payload = json.dumps(
        {k: round(float(params.get(k, 0.0)), 4) for k in sorted(PREDICT_PARAM_RANGES)},
        sort_keys=True,
    )
    seed = int(hashlib.blake2b(seed_payload.encode('utf-8'), digest_size=8).hexdigest(), 16)
    rng = np.random.default_rng(seed)
    z = rng.normal(0.0, 1.0, 6)
    strength = float(np.clip(missing_weight, 0.0, 1.0))

    f = float(params.get('f_Goss', 0.82))
    theta = float(params.get('theta_0_deg', 6.0))
    halfwidth = float(params.get('halfwidth_deg', 8.0))
    si = float(params.get('Si_content', 3.0))

    f_eff = np.clip(
        f
        + strength * (0.030 * z[0] + 0.020 * (normed['Si_content'] - 0.5)),
        0.4, 0.9,
    )
    theta_eff = np.clip(
        theta
        + strength * (2.0 * z[1] + 1.8 * (normed['halfwidth_deg'] - 0.5)),
        1.0, 30.0,
    )
    halfwidth_eff = np.clip(
        halfwidth
        + strength * (1.4 * z[2] + 1.0 * (normed['theta_0_deg'] - 0.5)),
        5.0, 15.0,
    )

    td_b_gain = 1.0 + strength * (
        0.030 * (0.5 - normed['theta_0_deg'])
        - 0.018 * (normed['halfwidth_deg'] - 0.5)
        + 0.012 * z[4]
    )
    td_h_factor = 1.0 + strength * (
        0.12 * (normed['halfwidth_deg'] - 0.5)
        + 0.08 * (normed['Si_content'] - 0.5)
        + 0.04 * z[5]
    )

    return {
        'missing_weight': strength,
        'effective_odf': {
            'f_Goss': float(f_eff),
            'theta_0_deg': float(theta_eff),
            'halfwidth_deg': float(halfwidth_eff),
        },
        'rd_b_gain': 1.0,
        'td_b_gain': float(np.clip(td_b_gain, 0.94, 1.06)),
        'td_h_factor': float(np.clip(td_h_factor, 0.82, 1.22)),
        'seed': int(seed % (2 ** 31)),
        'si_percent': float(si),
    }


def _metric_bundle(y_true: np.ndarray, y_pred: np.ndarray, cols: list[str]) -> dict:
    """Return per-output and primary B-H metrics with zero-safe MAPE."""
    if y_true.size == 0 or y_pred.size == 0:
        return {
            'r2_avg': None,
            'mape_avg': None,
            'r2_per_output': {},
            'mape_per_output': {},
            'all_target_r2_avg': None,
            'all_target_mape_avg': None,
        }

    r2_vals = r2_score(y_true, y_pred, multioutput='raw_values')
    r2_per_output = {cols[i]: float(r2_vals[i]) for i in range(len(cols))}

    mape_per_output = {}
    for i, col in enumerate(cols):
        min_abs = BH_MAPE_MIN_ABS_T if col.startswith('B_') else 1e-9
        mask = np.abs(y_true[:, i]) > min_abs
        if mask.sum() > 0:
            mape_per_output[col] = float(mean_absolute_percentage_error(
                y_true[mask, i], y_pred[mask, i]
            ))

    primary_cols = _primary_target_cols(cols)
    primary_idx = [cols.index(c) for c in primary_cols]
    primary_r2 = [r2_vals[i] for i in primary_idx] if primary_idx else []
    primary_mape = [mape_per_output[c] for c in primary_cols if c in mape_per_output]

    return {
        'r2_avg': float(np.mean(primary_r2)) if primary_r2 else None,
        'mape_avg': float(np.mean(primary_mape)) if primary_mape else None,
        'r2_per_output': r2_per_output,
        'mape_per_output': mape_per_output,
        'all_target_r2_avg': float(np.mean(r2_vals)),
        'all_target_mape_avg': float(np.mean(list(mape_per_output.values()))) if mape_per_output else None,
    }


class BHPredictor:
    """XGBoost 代理模型：织构参数 → B-H 曲线。"""

    def __init__(self, model_dir: str = 'data/models'):
        self.model_dir = Path(model_dir)
        self.model_dir.mkdir(parents=True, exist_ok=True)
        self.model         = None   # legacy: combined single-model (backward compat load)
        self.model_rd      = None   # RD-only estimator
        self.model_td      = None   # TD-only estimator
        self.scaler_X      = None
        self.feature_cols  = FEATURE_COLS
        self.target_cols   = None
        self.rd_target_cols = None
        self.td_target_cols = None
        self.metadata      = {}

    def train(self, dataset_path: str,
              model_type: str = 'direct_xgb',
              xgb_params: dict = None,
              test_size: float = 0.2) -> dict:
        """
        训练并保存模型。
        model_type: 'direct_xgb' | 'extra_trees'
        返回 {model_id, r2_avg, mape_avg, r2_per_output, feature_importance, ...}
        """
        if model_type not in SUPPORTED_MODEL_TYPES:
            model_type = 'direct_xgb'
        if model_type == 'extra_trees':
            params = {**DEFAULT_ET_PARAMS, **(xgb_params or {})}
        elif model_type == 'gaussian_process':
            params = {**DEFAULT_GP_PARAMS, **(xgb_params or {})}
        elif model_type == 'ridge_poly':
            params = {**DEFAULT_RIDGE_POLY_PARAMS, **(xgb_params or {})}
        else:
            params = {**DEFAULT_XGB_PARAMS, **(xgb_params or {})}
        df = pd.read_csv(dataset_path)

        # 只保留特征列和有效目标列
        feat_cols = []
        constant_features = []
        for col in FEATURE_COLS:
            if col not in df.columns:
                continue
            if df[col].dropna().nunique() <= 1:
                constant_features.append(col)
                continue
            feat_cols.append(col)
        tgt_cols  = _build_target_cols(df.columns)
        rd_cols = [c for c in tgt_cols if '_0deg' in c]
        td_cols = [c for c in tgt_cols if '_90deg' in c]

        if not feat_cols or not tgt_cols:
            raise ValueError('数据集缺少必要的特征列或目标列，请检查 CSV 格式')

        sub = df[feat_cols + tgt_cols].dropna()
        if len(sub) < MIN_TRAIN_SAMPLES:
            raise ValueError(f'有效样本数太少（{len(sub)}），至少需要 {MIN_TRAIN_SAMPLES} 个')

        X = sub[feat_cols].values
        Y = sub[tgt_cols].values

        requested_test = max(1, int(np.ceil(len(sub) * test_size)))
        reliable_eval = (
            len(sub) >= MIN_RELIABLE_EVAL_SAMPLES
            and requested_test >= MIN_RELIABLE_TEST_SAMPLES
            and len(sub) - requested_test >= MIN_RELIABLE_TEST_SAMPLES
        )

        def fit_model(x_data, y_data):
            scaler_obj = StandardScaler()
            x_scaled = scaler_obj.fit_transform(x_data)
            fitted = _build_model(model_type, params)
            fitted.fit(x_scaled, y_data)
            return scaler_obj, fitted

        validation_metrics = _metric_bundle(np.empty((0, len(tgt_cols))), np.empty((0, len(tgt_cols))), tgt_cols)
        n_train_eval = len(sub)
        n_test_eval = 0
        warnings_list = []

        if reliable_eval:
            X_train, X_test, Y_train, Y_test = train_test_split(
                X, Y, test_size=test_size, random_state=42)
            eval_scaler, eval_model = fit_model(X_train, Y_train)
            Y_pred = eval_model.predict(eval_scaler.transform(X_test))
            validation_metrics = _metric_bundle(Y_test, Y_pred, tgt_cols)
            n_train_eval = len(X_train)
            n_test_eval = len(X_test)
            metric_reliability = 'holdout_validation'
        else:
            metric_reliability = 'exploratory_only'
            warnings_list.append(
                f'有效样本数 {len(sub)} 低于可信验证阈值 {MIN_RELIABLE_EVAL_SAMPLES}，'
                '未发布正式 R2/MAPE；请增加样本量后再比较模型质量。'
            )

        # Train final models: shared scaler, independent RD and TD estimators
        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(X)
        tgt_list = list(tgt_cols)
        rd_idx = [tgt_list.index(c) for c in rd_cols]
        td_idx = [tgt_list.index(c) for c in td_cols]
        model_rd = model_td = None
        if rd_cols:
            model_rd = _build_model(model_type, params)
            model_rd.fit(X_scaled, Y[:, rd_idx])
        if td_cols:
            model_td = _build_model(model_type, params)
            model_td.fit(X_scaled, Y[:, td_idx])

        # Train metrics: combine predictions from both sub-models
        Y_pred_combined = np.zeros_like(Y, dtype=float)
        if model_rd:
            pred_rd = np.asarray(model_rd.predict(X_scaled))
            for j, c in enumerate(rd_cols):
                Y_pred_combined[:, tgt_list.index(c)] = pred_rd[:, j]
        if model_td:
            pred_td = np.asarray(model_td.predict(X_scaled))
            for j, c in enumerate(td_cols):
                Y_pred_combined[:, tgt_list.index(c)] = pred_td[:, j]
        train_metrics = _metric_bundle(Y, Y_pred_combined, tgt_cols)

        # Feature importance: average across all estimators (tree models only)
        all_ests = []
        if model_rd: all_ests.extend(model_rd.estimators_)
        if model_td: all_ests.extend(model_td.estimators_)
        fi_arrays = [np.asarray(e.feature_importances_)
                     for e in all_ests if hasattr(e, 'feature_importances_')]
        if fi_arrays:
            fi_avg = np.array(fi_arrays).mean(axis=0)
        else:
            fi_avg = np.ones(len(feat_cols)) / len(feat_cols)
        fi_dict = {feat_cols[i]: float(fi_avg[i]) for i in range(len(feat_cols))}

        # 保存
        model_id = f'model_{datetime.now().strftime("%Y%m%d_%H%M%S")}'
        save_dir = self.model_dir / model_id
        save_dir.mkdir(parents=True)

        if model_rd:
            with open(save_dir / 'model_rd.pkl', 'wb') as f: pickle.dump(model_rd, f)
        if model_td:
            with open(save_dir / 'model_td.pkl', 'wb') as f: pickle.dump(model_td, f)
        with open(save_dir / 'scaler_X.pkl', 'wb') as f: pickle.dump(scaler, f)

        # 检测训练集是否使用了 δ 修正后的 RD BH 数据
        _bh_corr_col = 'bh_reference_corrected'
        bh_corrected_in_training = False
        if _bh_corr_col in df.columns:
            try:
                frac = df[_bh_corr_col].astype(bool).mean()
                bh_corrected_in_training = float(frac) > 0.5
            except Exception:
                pass

        config = {
            'model_id':       model_id,
            'model_type':     model_type,
            'dataset_path':   dataset_path,
            'feature_cols':   feat_cols,
            'target_cols':    tgt_cols,
            'primary_target_cols': _primary_target_cols(tgt_cols),
            'dropped_constant_features': constant_features,
            'xgb_params':     params,
            'test_size':      test_size,
            'n_fit':          len(sub),
            'n_train':        n_train_eval,
            'n_test':         n_test_eval,
            'created':        datetime.now().isoformat(),
            'target_policy':  'RD_TD_only',
            'model_format':   'dual',
            'rd_target_cols': rd_cols,
            'td_target_cols': td_cols,
            'metric_reliability': metric_reliability,
            'metric_warnings': warnings_list,
            'bh_reference_corrected': bh_corrected_in_training,
            'scalar_confidence': {
                'Hc': 'low_due_to_mesh_limit',
                'mu_max': 'low_due_to_mesh_limit',
                'BH_curve': 'primary_for_export',
            },
        }
        metrics = {
            'r2_avg':           validation_metrics['r2_avg'] if reliable_eval else None,
            'mape_avg':         validation_metrics['mape_avg'] if reliable_eval else None,
            'r2_per_output':    validation_metrics['r2_per_output'] if reliable_eval else {},
            'mape_per_output':  validation_metrics['mape_per_output'] if reliable_eval else {},
            'all_target_r2_avg': validation_metrics['all_target_r2_avg'] if reliable_eval else None,
            'all_target_mape_avg': validation_metrics['all_target_mape_avg'] if reliable_eval else None,
            'train_r2_avg':     train_metrics['r2_avg'],
            'train_mape_avg':   train_metrics['mape_avg'],
            'train_r2_per_output': train_metrics['r2_per_output'],
            'train_all_target_r2_avg': train_metrics['all_target_r2_avg'],
            'metric_reliability': metric_reliability,
            'metric_warnings': warnings_list,
            'feature_importance': fi_dict,
        }
        with open(save_dir / 'config.json',  'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        with open(save_dir / 'metrics.json', 'w', encoding='utf-8') as f:
            json.dump(metrics, f, ensure_ascii=False, indent=2)

        # 更新实例状态
        self.model_rd    = model_rd
        self.model_td    = model_td
        self.model       = None
        self.scaler_X    = scaler
        self.feature_cols = feat_cols
        self.target_cols  = tgt_cols
        self.rd_target_cols = rd_cols
        self.td_target_cols = td_cols
        self.metadata     = {**config, **metrics}

        return {'model_id': model_id, **metrics,
                'n_samples': len(sub), 'n_features': len(feat_cols),
                'n_targets': len(tgt_cols),
                'n_train': n_train_eval, 'n_test': n_test_eval,
                'dropped_constant_features': constant_features}

    def load(self, model_id: str) -> None:
        """从 data/models/<model_id>/ 或 data/paper_models/<model_id>/ 加载模型。"""
        candidates = [self.model_dir / model_id, Path('data/paper_models') / model_id]
        d = next((p for p in candidates if p.exists()), candidates[0])
        if not d.exists():
            raise FileNotFoundError(f'模型不存在: {model_id}')

        if (d / 'model_rd.pkl').exists() and (d / 'model_td.pkl').exists():
            with open(d / 'model_rd.pkl', 'rb') as f: self.model_rd = pickle.load(f)
            with open(d / 'model_td.pkl', 'rb') as f: self.model_td = pickle.load(f)
            self.model = None
        elif (d / 'model.pkl').exists():
            with open(d / 'model.pkl', 'rb') as f: self.model = pickle.load(f)
            self.model_rd = None
            self.model_td = None
        else:
            raise FileNotFoundError(f'No model pkl found in {d}')
        scaler_path = d / 'scaler_X.pkl'
        if scaler_path.exists():
            with open(scaler_path, 'rb') as f:
                self.scaler_X = pickle.load(f)
        else:
            self.scaler_X = None

        with open(d / 'config.json', 'r', encoding='utf-8') as f:
            cfg = json.load(f)
        metrics = {}
        metrics_path = d / 'metrics.json'
        if metrics_path.exists():
            try:
                metrics = json.loads(metrics_path.read_text(encoding='utf-8'))
            except Exception:
                metrics = {}
        self.feature_cols   = cfg.get('feature_cols', FEATURE_COLS)
        self.target_cols    = cfg.get('target_cols')
        self.rd_target_cols = cfg.get('rd_target_cols')
        self.td_target_cols = cfg.get('td_target_cols')
        cfg.setdefault('model_id', model_id)
        cfg.setdefault('artifact_dir', str(d))
        cfg.setdefault('model_family', 'paper_surrogate_selection' if d.parent.name == 'paper_models' else 'interactive_predictor')
        if 'bh_reference_corrected' not in cfg:
            cfg['bh_reference_corrected'] = _dataset_bh_reference_corrected(cfg.get('dataset_path'))
        self.metadata = {**cfg, **metrics}

    def predict_bh(self, params: dict) -> dict:
        """
        快速推理。直接返回模型原始预测值，不叠加任何参考修正。
        params: {f_Goss, theta_0_deg, halfwidth_deg, N_grains, Si_content}
        返回: {RD: {H, B}, TD: {H, B}, full_direction: {...}, scalars: {...}}
        """
        if self.model is None and self.model_rd is None:
            raise RuntimeError('模型未加载，请先调用 load() 或 train()')

        if 'N_grains' not in params:
            params = dict(params)
            params['N_grains'] = float(DEFAULT_N_GRAINS)

        x = np.array([[params.get(c, 0) for c in self.feature_cols]], dtype=float)
        x_in = self.scaler_X.transform(x) if self.scaler_X is not None else x

        H = STANDARD_H_POINTS
        tgt = {}

        if self.model_rd is not None or self.model_td is not None:
            # Dual-model format: independent RD and TD estimators
            rd_cols = self.rd_target_cols or [c for c in (self.target_cols or []) if '_0deg' in c]
            td_cols = self.td_target_cols or [c for c in (self.target_cols or []) if '_90deg' in c]
            if self.model_rd is not None and rd_cols:
                y_rd = np.asarray(self.model_rd.predict(x_in), dtype=float)[0]
                tgt.update(dict(zip(rd_cols, y_rd)))
            if self.model_td is not None and td_cols:
                y_td = np.asarray(self.model_td.predict(x_in), dtype=float)[0]
                tgt.update(dict(zip(td_cols, y_td)))
        else:
            # Legacy single model
            y_pred = np.asarray(self.model.predict(x_in), dtype=float)[0]
            tgt = dict(zip(self.target_cols or [], y_pred))

        result = {}
        angle_map = {0: 'RD', 90: 'TD'}
        for angle, key in angle_map.items():
            pfx = f'B_{angle}deg'
            B = [float(tgt.get(f'{pfx}_H{h}', 0.0)) for h in H]
            result[key] = {'H': H, 'B': B, 'unit': 'A/m, T'}

        # raw_before_delta == result (no post-processing applied)
        result['raw_before_delta'] = {
            'RD': {'H': list(H), 'B': list(result['RD']['B']), 'unit': 'A/m, T'},
            'TD': {'H': list(H), 'B': list(result['TD']['B']), 'unit': 'A/m, T'},
        }

        if any(c.startswith('B_45deg') for c in (self.target_cols or [])):
            B45 = [float(tgt.get(f'B_45deg_H{h}', 0.0)) for h in H]
            result['Cross45'] = {'H': H, 'B': B45, 'unit': 'A/m, T'}

        scalars = {}
        for angle in [0, 90, 45]:
            for tpl in SCALAR_TARGET_TEMPLATES:
                col = tpl.format(a=angle)
                if col in tgt:
                    scalars[col] = float(tgt[col])
        scalar_guard = calibrate_scalar_targets(scalars, source='ml_predictor')
        result['scalars'] = scalar_guard['values']

        from go_steel_reference import get_reference_properties
        si_content = float(params.get('Si_content', 3.0))
        ref_props = get_reference_properties(si_content=si_content)
        ref_hc = float(ref_props['Hc_Am'])
        for key in list(result['scalars']):
            if key.startswith('Hc_'):
                result['scalars'][key] = ref_hc
        result['Hc_reference_Am'] = ref_hc
        result['Hc_reference_source'] = ref_props['Hc_source']

        result['full_direction'] = interpolate_full_direction(result['RD'], result['TD'])
        result['calibration_report'] = {
            'model_format': 'dual' if (self.model_rd is not None) else 'legacy',
            'bh_reference_corrected': bool(self.metadata.get('bh_reference_corrected', False)),
            'scalars': scalar_guard['report'],
        }
        result['scalar_confidence'] = self.metadata.get('scalar_confidence', {
            'Hc':      'reference_value_go_steel_database',
            'mu_max':  'low_due_to_mesh_limit',
            'BH_curve':'primary_for_export',
        })
        result['bh_reference_corrected'] = bool(self.metadata.get('bh_reference_corrected', False))
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
                n_samples = cfg.get('n_fit', cfg.get('n_train', 0) + cfg.get('n_test', 0))
                reliability = met.get('metric_reliability', cfg.get('metric_reliability'))
                warnings_list = met.get('metric_warnings', cfg.get('metric_warnings', []))
                r2_avg = met.get('r2_avg')
                mape_avg = met.get('mape_avg')
                if reliability is None and n_samples < MIN_RELIABLE_EVAL_SAMPLES:
                    reliability = 'legacy_unreliable'
                    warnings_list = [
                        f'旧版模型样本数 {n_samples} 低于可信验证阈值 {MIN_RELIABLE_EVAL_SAMPLES}，'
                        '历史 R2/MAPE 不再作为可展示质量指标。'
                    ]
                    r2_avg = None
                    mape_avg = None
                items.append({
                    'model_id':  d.name,
                    'model_family': 'interactive_predictor',
                    'model_type': cfg.get('model_type', 'direct_xgb'),
                    'dataset_path': cfg.get('dataset_path'),
                    'dataset_name': Path(cfg.get('dataset_path', '')).name if cfg.get('dataset_path') else None,
                    'created':   cfg.get('created', ''),
                    'r2_avg':    r2_avg,
                    'mape_avg':  mape_avg,
                    'cv_bh_rmse_T_mean': None,
                    'cv_bh_rmse_T_std': None,
                    'holdout_bh_rmse_T': None,
                    'selection_metric': None,
                    'selected_by_cv': False,
                    'prediction_capable': True,
                    'train_r2_avg': met.get('train_r2_avg'),
                    'train_mape_avg': met.get('train_mape_avg'),
                    'metric_reliability': reliability,
                    'metric_warnings': warnings_list,
                    'n_samples': n_samples,
                    'n_targets': len(cfg.get('target_cols', [])),
                })
            except Exception:
                pass
        paper_dir = Path('data/paper_models')
        if paper_dir.exists():
            for d in sorted(paper_dir.iterdir(), reverse=True):
                if not d.is_dir():
                    continue
                try:
                    summary_path = d / 'summary.json'
                    if summary_path.exists():
                        report = json.loads(summary_path.read_text(encoding='utf-8'))
                    else:
                        cfg = json.loads((d / 'config.json').read_text(encoding='utf-8'))
                        met = json.loads((d / 'metrics.json').read_text(encoding='utf-8'))
                        report = {**cfg, **met}
                    ranking = report.get('ranking') or []
                    best = ranking[0] if ranking else {}
                    holdout = report.get('holdout_metrics') or {}
                    dataset_path = report.get('dataset_path')
                    bh_corrected = report.get('bh_reference_corrected')
                    if bh_corrected is None:
                        bh_corrected = _dataset_bh_reference_corrected(dataset_path)
                    has_model_file = (d / 'model.pkl').exists()
                    items.append({
                        'model_id': d.name,
                        'model_family': 'paper_surrogate_selection',
                        'model_type': report.get('selected_model') or report.get('model_type'),
                        'dataset_path': dataset_path,
                        'dataset_name': Path(dataset_path).name if dataset_path else None,
                        'created': report.get('created', ''),
                        'r2_avg': holdout.get('r2_avg'),
                        'mape_avg': holdout.get('mape_avg'),
                        'cv_bh_rmse_T_mean': best.get('bh_rmse_T_mean'),
                        'cv_bh_rmse_T_std': best.get('bh_rmse_T_std'),
                        'holdout_bh_rmse_T': holdout.get('bh_rmse_T'),
                        'selection_metric': report.get('selection_metric', 'cv bh_rmse_T mean'),
                        'selected_by_cv': True,
                        'prediction_capable': bool(has_model_file),
                        'metric_reliability': 'cross_validation_selection',
                        'metric_warnings': [
                            'CV B-H RMSE ranking is fair only among models trained and evaluated on the same dataset and fold policy.',
                            'This artifact is a selected surrogate model and can be used by quick prediction.'
                            if has_model_file else
                            'This artifact has no model.pkl and is kept as a CV report only.',
                        ],
                        'n_samples': report.get('n_samples', 0),
                        'n_targets': len(report.get('target_cols', [])),
                        'bh_reference_corrected': bool(bh_corrected),
                        'candidate_models': report.get('candidate_models', []),
                        'artifact_dir': str(d),
                    })
                except Exception:
                    pass
        items.sort(key=lambda x: x.get('created') or '', reverse=True)
        return items

    def delete_model(self, model_id: str) -> bool:
        """删除指定模型目录。"""
        import shutil
        d = self.model_dir / model_id
        if d.exists():
            shutil.rmtree(d)
            return True
        paper_d = Path('data/paper_models') / model_id
        if paper_d.exists():
            shutil.rmtree(paper_d)
            return True
        return False
