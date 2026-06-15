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
