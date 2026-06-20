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
from sklearn.ensemble import ExtraTreesRegressor
from xgboost import XGBRegressor

from anisotropy_interpolator import interpolate_full_direction
from physics_calibrator import calibrate_material_pair, calibrate_scalar_targets

STANDARD_H_POINTS = [100, 200, 500, 1000, 2000, 3000, 5000, 7500]
FEATURE_COLS = ['f_Goss', 'theta_0_deg', 'halfwidth_deg', 'N_grains', 'Si_content']

# 角度 → 列前缀映射（供 predict_bh 使用）
ANGLE_PREFIX = {0: 'B_0deg', 45: 'B_45deg', 90: 'B_90deg'}
SCALAR_TARGET_TEMPLATES = ['Hc_{a}deg', 'Mr_{a}deg', 'mu_max_{a}deg']
DEFAULT_TARGET_ANGLES = [0, 90]

# 预测时 N_grains 由模型内部自动填充（不再从 UI 获取）
DEFAULT_N_GRAINS = 5

SUPPORTED_MODEL_TYPES = ['direct_xgb', 'extra_trees']

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

DEFAULT_ET_PARAMS = {
    'n_estimators':      300,
    'max_depth':         None,
    'min_samples_split': 2,
    'random_state':      42,
    'n_jobs':            -1,
}

def _build_model(model_type: str, params: dict):
    """构造 sklearn estimator，MultiOutputRegressor 包装。"""
    if model_type == 'extra_trees':
        et_params = {k: v for k, v in params.items()
                     if k in ['n_estimators', 'max_depth', 'min_samples_split',
                               'random_state', 'n_jobs']}
        return MultiOutputRegressor(ExtraTreesRegressor(**et_params))
    # default: direct_xgb
    xgb_keys = ['n_estimators', 'max_depth', 'learning_rate', 'subsample',
                 'colsample_bytree', 'random_state', 'eval_metric', 'verbosity']
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
        self.model      = None
        self.scaler_X   = None
        self.feature_cols = FEATURE_COLS
        self.target_cols  = None
        self.metadata     = {}

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

        scaler, model = fit_model(X, Y)
        train_pred = model.predict(scaler.transform(X))
        train_metrics = _metric_bundle(Y, train_pred, tgt_cols)

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
            'metric_reliability': metric_reliability,
            'metric_warnings': warnings_list,
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
        self.model       = model
        self.scaler_X    = scaler
        self.feature_cols = feat_cols
        self.target_cols  = tgt_cols
        self.metadata     = {**config, **metrics}

        return {'model_id': model_id, **metrics,
                'n_samples': len(sub), 'n_features': len(feat_cols),
                'n_targets': len(tgt_cols),
                'n_train': n_train_eval, 'n_test': n_test_eval,
                'dropped_constant_features': constant_features}

    def load(self, model_id: str) -> None:
        """从 data/models/<model_id>/ 加载模型和配置。"""
        d = self.model_dir / model_id
        with open(d / 'model.pkl',    'rb') as f: self.model    = pickle.load(f)
        with open(d / 'scaler_X.pkl', 'rb') as f: self.scaler_X = pickle.load(f)
        with open(d / 'config.json',  'r', encoding='utf-8') as f:
            cfg = json.load(f)
        self.feature_cols = cfg.get('feature_cols', FEATURE_COLS)
        self.target_cols  = cfg.get('target_cols')
        self.metadata     = cfg

    def predict_bh(self, params: dict) -> dict:
        """
        快速推理。
        params: {f_Goss, theta_0_deg, halfwidth_deg, N_grains, Si_content}
        返回: {RD: {H, B}, TD: {H, B}, full_direction: {...}, scalars: {...}}
        """
        if self.model is None:
            raise RuntimeError('模型未加载，请先调用 load() 或 train()')

        # N_grains 不再由用户在预测页面提供，后端自动填充
        if 'N_grains' not in params:
            params = dict(params)
            params['N_grains'] = float(DEFAULT_N_GRAINS)

        x = np.array([[params.get(c, 0) for c in self.feature_cols]])
        x_s = self.scaler_X.transform(x)
        y_pred = self.model.predict(x_s)[0]

        tgt = dict(zip(self.target_cols, y_pred))
        H   = STANDARD_H_POINTS
        result = {}

        angle_map = {0: 'RD', 90: 'TD'}
        for angle, key in angle_map.items():
            pfx = f'B_{angle}deg'
            B   = [np.clip(tgt.get(f'{pfx}_H{h}', 0.0), 0.0, 2.5) for h in H]
            result[key] = {'H': H, 'B': [float(b) for b in B], 'unit': 'A/m, T'}

        pair = calibrate_material_pair(result['RD'], result['TD'])
        result['RD'] = {
            'H': pair['RD']['H'],
            'B': [round(float(b), 6) for b in pair['RD']['B']],
            'unit': 'A/m, T',
        }
        result['TD'] = {
            'H': pair['TD']['H'],
            'B': [round(float(b), 6) for b in pair['TD']['B']],
            'unit': 'A/m, T',
        }

        # Legacy models may contain a 45 degree target. Preserve it for reading
        # compatibility, but new models do not train on it by default.
        if any(c.startswith('B_45deg') for c in self.target_cols or []):
            pfx = 'B_45deg'
            B = [np.clip(tgt.get(f'{pfx}_H{h}', 0.0), 0.0, 2.5) for h in H]
            result['Cross45'] = {'H': H, 'B': [round(float(b), 6) for b in B], 'unit': 'A/m, T'}

        scalars = {}
        for angle in [0, 90, 45]:
            for tpl in SCALAR_TARGET_TEMPLATES:
                col = tpl.format(a=angle)
                if col in tgt:
                    scalars[col] = float(tgt[col])
        scalar_guard = calibrate_scalar_targets(scalars, source='ml_predictor')
        result['scalars'] = scalar_guard['values']

        # Override predicted Hc with reference database value.
        # MuMax3 SW model gives Hc ≈ 36,000 A/m; real GO steel Hc < 10 A/m.
        from go_steel_reference import get_reference_properties
        si_content = float(params.get('Si_content', 3.0))
        ref_props = get_reference_properties(si_content=si_content)
        ref_hc = ref_props['Hc_Am']
        for key in list(result['scalars']):
            if key.startswith('Hc_'):
                result['scalars'][key] = ref_hc
        result['Hc_reference_Am'] = ref_hc
        result['Hc_reference_source'] = ref_props['Hc_source']

        # Apply reference B-H correction (delta-correction method).
        # Skipped when model was trained on already-corrected data.
        bh_corrected = False
        if not self.metadata.get('bh_reference_corrected', False):
            try:
                from physics_calibrator import correct_bh_with_reference
                odf_params = {
                    'f_Goss':         float(params.get('f_Goss', 0.82)),
                    'theta_0_deg':    float(params.get('theta_0_deg', 6.0)),
                    'halfwidth_deg':  float(params.get('halfwidth_deg', 8.0)),
                }
                for direction, key in [('RD', 'RD'), ('TD', 'TD')]:
                    curve = result[key]
                    corr = correct_bh_with_reference(
                        curve['H'], curve['B'], odf_params,
                        direction=direction, weight_cap=1.0,
                    )
                    result[key] = {
                        'H':    corr['H'],
                        'B':    [round(float(b), 6) for b in corr['B']],
                        'unit': 'A/m, T',
                    }
                bh_corrected = True
            except Exception as _corr_err:
                import warnings
                warnings.warn(f'reference_corrector 修正失败，使用原始仿真输出: {_corr_err}')

        result['full_direction'] = interpolate_full_direction(result['RD'], result['TD'])
        result['calibration_report'] = {
            'material_pair':        pair['report'],
            'scalars':              scalar_guard['report'],
            'bh_reference_corrected': bh_corrected,
        }
        result['scalar_confidence'] = self.metadata.get('scalar_confidence', {
            'Hc':      'reference_value_go_steel_database',
            'mu_max':  'low_due_to_mesh_limit',
            'BH_curve':'primary_for_export',
        })
        result['bh_reference_corrected'] = bh_corrected
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
                    'model_type': 'direct_xgb',
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
                        'prediction_capable': False,
                        'metric_reliability': 'cross_validation_selection',
                        'metric_warnings': [
                            'CV B-H RMSE ranking is fair only among models trained and evaluated on the same dataset and fold policy.',
                            'This artifact is a model-selection report; quick prediction currently uses data/models interactive predictors.',
                        ],
                        'n_samples': report.get('n_samples', 0),
                        'n_targets': len(report.get('target_cols', [])),
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
