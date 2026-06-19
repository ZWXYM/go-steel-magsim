"""
paper_surrogate_trainer.py

Paper-grade surrogate model selection for calibrated B-H curves.

This module is intentionally separate from ml_trainer.py. The app's normal
trainer is optimized for quick interactive use, while this workflow produces a
traceable model-comparison artifact suitable for reporting in a paper.
"""
from __future__ import annotations

import argparse
import copy
import csv
import json
import pickle
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator, RegressorMixin, clone
from sklearn.decomposition import PCA
from sklearn.ensemble import ExtraTreesRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error
from sklearn.model_selection import KFold, train_test_split
from sklearn.multioutput import MultiOutputRegressor
from sklearn.preprocessing import StandardScaler
from xgboost import XGBRegressor

from ml_trainer import (
    BH_MAPE_MIN_ABS_T,
    DEFAULT_XGB_PARAMS,
    FEATURE_COLS,
    MIN_RELIABLE_EVAL_SAMPLES,
    _build_target_cols,
    _metric_bundle,
    _primary_target_cols,
)


DEFAULT_RANDOM_STATE = 42
PAPER_MODEL_CANDIDATES = {
    "direct_xgb": {
        "label": "Direct XGBoost",
        "regressor": "xgboost",
        "target_strategy": "direct",
        "description": "直接用多输出 XGBoost 回归每个 B-H 采样点。",
    },
    "pca_xgb": {
        "label": "PCA + XGBoost",
        "regressor": "xgboost",
        "target_strategy": "pca_target",
        "description": "先把 B-H 曲线压缩为 PCA 系数，再用 XGBoost 回归系数并反变换。",
    },
    "extra_trees": {
        "label": "Direct ExtraTrees",
        "regressor": "extra_trees",
        "target_strategy": "direct",
        "description": "直接用 ExtraTrees 多输出回归 B-H 采样点，作为非 boosting 树模型基线。",
    },
    "pca_extra_trees": {
        "label": "PCA + ExtraTrees",
        "regressor": "extra_trees",
        "target_strategy": "pca_target",
        "description": "PCA 目标压缩后用 ExtraTrees 回归系数，检验 PCA 对树集成模型的帮助。",
    },
}

PAPER_TRAINING_PRESETS = {
    "smoke": {
        "label": "测试级别 / 冒烟模型选择",
        "description": "快速验证候选模型、CV 报告和保存链路。",
        "config": {
            "min_samples": 8,
            "test_size": 0.25,
            "n_splits": 3,
            "target_scope": "bh_only",
            "pca_variance": 0.999,
            "pca_max_components": 4,
            "candidate_models": list(PAPER_MODEL_CANDIDATES),
            "xgb_params": {
                "n_estimators": 40,
                "max_depth": 2,
                "learning_rate": 0.08,
                "subsample": 0.95,
                "colsample_bytree": 0.95,
                "random_state": DEFAULT_RANDOM_STATE,
                "eval_metric": "rmse",
                "verbosity": 0,
                "n_jobs": -1,
            },
            "extra_trees_params": {
                "n_estimators": 80,
                "min_samples_leaf": 2,
                "random_state": DEFAULT_RANDOM_STATE,
                "n_jobs": -1,
            },
        },
    },
    "lite": {
        "label": "Lite / 轻量模型选择",
        "description": "面向 24+ 样本的小规模正式候选比较。",
        "config": {
            "min_samples": MIN_RELIABLE_EVAL_SAMPLES,
            "test_size": 0.25,
            "n_splits": 3,
            "target_scope": "bh_only",
            "pca_variance": 0.999,
            "pca_max_components": 6,
            "candidate_models": list(PAPER_MODEL_CANDIDATES),
            "xgb_params": {
                "n_estimators": 160,
                "max_depth": 3,
                "learning_rate": 0.05,
                "subsample": 0.9,
                "colsample_bytree": 0.9,
                "random_state": DEFAULT_RANDOM_STATE,
                "eval_metric": "rmse",
                "verbosity": 0,
                "n_jobs": -1,
            },
            "extra_trees_params": {
                "n_estimators": 220,
                "min_samples_leaf": 2,
                "random_state": DEFAULT_RANDOM_STATE,
                "n_jobs": -1,
            },
        },
    },
    "std": {
        "label": "Std / 标准论文级模型选择",
        "description": "面向 48+ 样本的标准交叉验证候选比较。",
        "config": {
            "min_samples": 48,
            "test_size": 0.20,
            "n_splits": 5,
            "target_scope": "bh_only",
            "pca_variance": 0.999,
            "pca_max_components": 8,
            "candidate_models": list(PAPER_MODEL_CANDIDATES),
            "xgb_params": {
                "n_estimators": 400,
                "max_depth": 3,
                "learning_rate": 0.04,
                "subsample": 0.9,
                "colsample_bytree": 0.9,
                "random_state": DEFAULT_RANDOM_STATE,
                "eval_metric": "rmse",
                "verbosity": 0,
                "n_jobs": -1,
            },
            "extra_trees_params": {
                "n_estimators": 600,
                "min_samples_leaf": 2,
                "random_state": DEFAULT_RANDOM_STATE,
                "n_jobs": -1,
            },
        },
    },
    "max": {
        "label": "Max / 全量论文级模型选择",
        "description": "面向 96+ 样本的高成本候选比较，用于最终模型报告。",
        "config": {
            "min_samples": 96,
            "test_size": 0.20,
            "n_splits": 5,
            "target_scope": "bh_only",
            "pca_variance": 0.9995,
            "pca_max_components": 12,
            "candidate_models": list(PAPER_MODEL_CANDIDATES),
            "xgb_params": {
                "n_estimators": 700,
                "max_depth": 4,
                "learning_rate": 0.03,
                "subsample": 0.9,
                "colsample_bytree": 0.9,
                "random_state": DEFAULT_RANDOM_STATE,
                "eval_metric": "rmse",
                "verbosity": 0,
                "n_jobs": -1,
            },
            "extra_trees_params": {
                "n_estimators": 1000,
                "min_samples_leaf": 1,
                "random_state": DEFAULT_RANDOM_STATE,
                "n_jobs": -1,
            },
        },
    },
}


def get_paper_training_presets() -> dict:
    return {
        preset_id: {
            "id": preset_id,
            "label": preset["label"],
            "description": preset["description"],
            "config": copy.deepcopy(preset["config"]),
            "candidate_model_relationships": copy.deepcopy(PAPER_MODEL_CANDIDATES),
            "selection_metric": "cv bh_rmse_T mean",
        }
        for preset_id, preset in PAPER_TRAINING_PRESETS.items()
    }


def resolve_paper_training_config(config: dict | None) -> dict:
    incoming = copy.deepcopy(config or {})
    preset_id = incoming.get("preset") or incoming.get("preset_id") or "lite"
    preset = PAPER_TRAINING_PRESETS.get(preset_id, PAPER_TRAINING_PRESETS["lite"])
    incoming.pop("preset", None)
    incoming.pop("preset_id", None)
    base = copy.deepcopy(preset["config"])
    base["preset"] = preset_id if preset_id in PAPER_TRAINING_PRESETS else "lite"
    base["preset_id"] = base["preset"]
    base["preset_label"] = preset["label"]

    preset_xgb = base.get("xgb_params", {})
    incoming_xgb = incoming.pop("xgb_params", None)
    preset_extra = base.get("extra_trees_params", {})
    incoming_extra = incoming.pop("extra_trees_params", None)
    for key, value in incoming.items():
        base[key] = value
    if incoming_xgb is not None:
        merged = copy.deepcopy(preset_xgb)
        merged.update(incoming_xgb)
        base["xgb_params"] = merged
    if incoming_extra is not None:
        merged = copy.deepcopy(preset_extra)
        merged.update(incoming_extra)
        base["extra_trees_params"] = merged

    base["candidate_models"] = [
        name for name in base.get("candidate_models", [])
        if name in PAPER_MODEL_CANDIDATES
    ] or list(PAPER_MODEL_CANDIDATES)
    return base


class ScaledRegressor(BaseEstimator, RegressorMixin):
    """Feature-scaled wrapper around a multi-output-capable regressor."""

    def __init__(self, estimator: Any):
        self.estimator = estimator

    def fit(self, X, y):
        self.x_scaler_ = StandardScaler()
        Xs = self.x_scaler_.fit_transform(X)
        self.estimator_ = clone(self.estimator)
        self.estimator_.fit(Xs, y)
        return self

    def predict(self, X):
        Xs = self.x_scaler_.transform(X)
        return self.estimator_.predict(Xs)


class PCATargetRegressor(BaseEstimator, RegressorMixin):
    """
    Predict a low-dimensional PCA representation of B-H curves, then invert it.

    Reducing the target space keeps the curve outputs correlated and usually
    gives smoother B-H predictions than fitting every H-point independently.
    """

    def __init__(self, estimator: Any, variance: float = 0.999, max_components: int | None = None):
        self.estimator = estimator
        self.variance = variance
        self.max_components = max_components

    def fit(self, X, y):
        self.x_scaler_ = StandardScaler()
        Xs = self.x_scaler_.fit_transform(X)

        max_allowed = min(y.shape[0], y.shape[1])
        n_components: int | float = self.variance
        if self.max_components is not None:
            n_components = min(int(self.max_components), max_allowed)
        self.pca_ = PCA(n_components=n_components, svd_solver="full")
        z = self.pca_.fit_transform(y)

        self.estimator_ = clone(self.estimator)
        self.estimator_.fit(Xs, z)
        return self

    def predict(self, X):
        Xs = self.x_scaler_.transform(X)
        z = self.estimator_.predict(Xs)
        return self.pca_.inverse_transform(z)


@dataclass
class PreparedDataset:
    dataset_path: str
    feature_cols: list[str]
    target_cols: list[str]
    dropped_constant_features: list[str]
    X: np.ndarray
    Y: np.ndarray
    rows: int


def prepare_dataset(dataset_path: str, target_scope: str = "bh_only") -> PreparedDataset:
    df = pd.read_csv(dataset_path)

    feature_cols = []
    dropped = []
    for col in FEATURE_COLS:
        if col not in df.columns:
            continue
        if df[col].dropna().nunique() <= 1:
            dropped.append(col)
            continue
        feature_cols.append(col)

    all_targets = _build_target_cols(df.columns)
    target_cols = _primary_target_cols(all_targets) if target_scope == "bh_only" else all_targets

    if not feature_cols or not target_cols:
        raise ValueError("数据集缺少可训练的特征列或目标列")

    sub = df[feature_cols + target_cols].dropna()
    return PreparedDataset(
        dataset_path=dataset_path,
        feature_cols=feature_cols,
        target_cols=target_cols,
        dropped_constant_features=dropped,
        X=sub[feature_cols].to_numpy(dtype=float),
        Y=sub[target_cols].to_numpy(dtype=float),
        rows=len(sub),
    )


def bh_error_metrics(y_true: np.ndarray, y_pred: np.ndarray, target_cols: list[str]) -> dict:
    bundle = _metric_bundle(y_true, y_pred, target_cols)
    primary_idx = [i for i, col in enumerate(target_cols) if col.startswith("B_")]
    if primary_idx:
        yt = y_true[:, primary_idx]
        yp = y_pred[:, primary_idx]
        rmse = float(np.sqrt(mean_squared_error(yt, yp)))
        mae = float(mean_absolute_error(yt, yp))
        valid = np.abs(yt) > BH_MAPE_MIN_ABS_T
        mape = float(np.mean(np.abs((yt[valid] - yp[valid]) / yt[valid]))) if np.any(valid) else None
    else:
        rmse = mae = mape = None

    return {
        "r2_avg": bundle["r2_avg"],
        "mape_avg": bundle["mape_avg"],
        "bh_rmse_T": rmse,
        "bh_mae_T": mae,
        "bh_mape": mape,
        "all_target_r2_avg": bundle["all_target_r2_avg"],
    }


class PaperSurrogateTrainer:
    def __init__(
        self,
        output_dir: str = "data/paper_models",
        random_state: int = DEFAULT_RANDOM_STATE,
        xgb_params: dict | None = None,
        extra_trees_params: dict | None = None,
        pca_variance: float = 0.999,
        pca_max_components: int | None = 8,
        candidate_models: list[str] | None = None,
    ):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.random_state = random_state
        self.xgb_params = {**DEFAULT_XGB_PARAMS, **(xgb_params or {})}
        self.extra_trees_params = {
            "n_estimators": 500,
            "min_samples_leaf": 2,
            "random_state": self.random_state,
            "n_jobs": -1,
            **(extra_trees_params or {}),
        }
        self.pca_variance = float(pca_variance)
        self.pca_max_components = pca_max_components
        self.candidate_models = [
            name for name in (candidate_models or list(PAPER_MODEL_CANDIDATES))
            if name in PAPER_MODEL_CANDIDATES
        ] or list(PAPER_MODEL_CANDIDATES)

    def _candidate_models(self) -> dict[str, Any]:
        xgb = MultiOutputRegressor(XGBRegressor(**self.xgb_params), n_jobs=-1)
        xgb_latent = MultiOutputRegressor(XGBRegressor(**self.xgb_params), n_jobs=-1)
        extra = ExtraTreesRegressor(**self.extra_trees_params)
        extra_latent = ExtraTreesRegressor(**self.extra_trees_params)
        candidates = {
            "direct_xgb": ScaledRegressor(xgb),
            "pca_xgb": PCATargetRegressor(
                xgb_latent,
                variance=self.pca_variance,
                max_components=self.pca_max_components,
            ),
            "extra_trees": ScaledRegressor(extra),
            "pca_extra_trees": PCATargetRegressor(
                extra_latent,
                variance=self.pca_variance,
                max_components=self.pca_max_components,
            ),
        }
        return {name: candidates[name] for name in self.candidate_models}

    def run(
        self,
        dataset_path: str,
        target_scope: str = "bh_only",
        min_samples: int = MIN_RELIABLE_EVAL_SAMPLES,
        test_size: float = 0.2,
        n_splits: int = 5,
        preset_id: str | None = None,
        preset_label: str | None = None,
    ) -> dict:
        data = prepare_dataset(dataset_path, target_scope=target_scope)
        if data.rows < min_samples:
            return {
                "status": "insufficient_data",
                "dataset_path": dataset_path,
                "n_samples": data.rows,
                "min_samples": min_samples,
                "feature_cols": data.feature_cols,
                "target_cols": data.target_cols,
                "dropped_constant_features": data.dropped_constant_features,
                "preset_id": preset_id,
                "preset_label": preset_label,
                "message": f"需要至少 {min_samples} 个有效样本才能生成论文级模型对比报告。",
            }

        X_train, X_holdout, y_train, y_holdout = train_test_split(
            data.X,
            data.Y,
            test_size=test_size,
            random_state=self.random_state,
        )
        fold_count = min(n_splits, max(3, len(X_train) // 6))
        kfold = KFold(n_splits=fold_count, shuffle=True, random_state=self.random_state)

        candidates = self._candidate_models()
        cv_rows = []
        for name, model in candidates.items():
            for fold, (tr_idx, va_idx) in enumerate(kfold.split(X_train), start=1):
                fitted = clone(model)
                fitted.fit(X_train[tr_idx], y_train[tr_idx])
                pred = fitted.predict(X_train[va_idx])
                metrics = bh_error_metrics(y_train[va_idx], pred, data.target_cols)
                cv_rows.append({"model": name, "fold": fold, **metrics})

        cv_df = pd.DataFrame(cv_rows)
        ranking = (
            cv_df.groupby("model")
            .agg({
                "bh_rmse_T": ["mean", "std"],
                "bh_mae_T": ["mean", "std"],
                "bh_mape": ["mean", "std"],
                "r2_avg": ["mean", "std"],
            })
            .reset_index()
        )
        ranking.columns = [
            "_".join([str(x) for x in col if x]).strip("_")
            for col in ranking.columns.to_flat_index()
        ]
        ranking = ranking.sort_values(["bh_rmse_T_mean", "r2_avg_mean"], ascending=[True, False])
        best_name = str(ranking.iloc[0]["model"])

        selected = clone(candidates[best_name])
        selected.fit(X_train, y_train)
        holdout_pred = selected.predict(X_holdout)
        holdout_metrics = bh_error_metrics(y_holdout, holdout_pred, data.target_cols)

        final_model = clone(candidates[best_name])
        final_model.fit(data.X, data.Y)

        model_id = f"paper_model_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        save_dir = self.output_dir / model_id
        save_dir.mkdir(parents=True)

        with open(save_dir / "model.pkl", "wb") as f:
            pickle.dump(final_model, f)
        with open(save_dir / "selected_holdout_model.pkl", "wb") as f:
            pickle.dump(selected, f)

        cv_df.to_csv(save_dir / "cv_fold_metrics.csv", index=False, encoding="utf-8-sig")
        ranking.to_csv(save_dir / "model_ranking.csv", index=False, encoding="utf-8-sig")

        config = {
            "model_id": model_id,
            "model_type": best_name,
            "dataset_path": dataset_path,
            "target_scope": target_scope,
            "feature_cols": data.feature_cols,
            "target_cols": data.target_cols,
            "dropped_constant_features": data.dropped_constant_features,
            "n_samples": data.rows,
            "n_train_selection": int(len(X_train)),
            "n_holdout": int(len(X_holdout)),
            "n_cv_splits": int(fold_count),
            "random_state": self.random_state,
            "preset_id": preset_id,
            "preset_label": preset_label,
            "xgb_params": self.xgb_params,
            "extra_trees_params": self.extra_trees_params,
            "pca_variance": self.pca_variance,
            "pca_max_components": self.pca_max_components,
            "candidate_models": self.candidate_models,
            "candidate_model_relationships": PAPER_MODEL_CANDIDATES,
            "created": datetime.now().isoformat(),
        }
        metrics = {
            "status": "ok",
            "selected_model": best_name,
            "selection_metric": "cv bh_rmse_T mean",
            "holdout_metrics": holdout_metrics,
            "ranking": ranking.to_dict(orient="records"),
        }
        (save_dir / "config.json").write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
        (save_dir / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")

        report = {
            **config,
            **metrics,
            "artifact_dir": str(save_dir),
        }
        (save_dir / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report


def _main() -> None:
    parser = argparse.ArgumentParser(description="Run paper-grade B-H surrogate model comparison.")
    parser.add_argument("dataset_path")
    parser.add_argument("--output-dir", default="data/paper_models")
    parser.add_argument("--preset", default="lite", choices=list(PAPER_TRAINING_PRESETS))
    parser.add_argument("--target-scope", default="bh_only", choices=["bh_only", "all"])
    parser.add_argument("--min-samples", type=int)
    parser.add_argument("--test-size", type=float)
    parser.add_argument("--n-splits", type=int)
    args = parser.parse_args()
    config = resolve_paper_training_config({
        "preset": args.preset,
        "target_scope": args.target_scope,
        **({"min_samples": args.min_samples} if args.min_samples is not None else {}),
        **({"test_size": args.test_size} if args.test_size is not None else {}),
        **({"n_splits": args.n_splits} if args.n_splits is not None else {}),
    })
    result = PaperSurrogateTrainer(
        output_dir=args.output_dir,
        xgb_params=config["xgb_params"],
        extra_trees_params=config["extra_trees_params"],
        pca_variance=config["pca_variance"],
        pca_max_components=config["pca_max_components"],
        candidate_models=config["candidate_models"],
    ).run(
        args.dataset_path,
        target_scope=config["target_scope"],
        min_samples=int(config["min_samples"]),
        test_size=float(config["test_size"]),
        n_splits=int(config["n_splits"]),
        preset_id=config["preset_id"],
        preset_label=config["preset_label"],
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    _main()
