# Paper-Grade B-H Surrogate Training Protocol

## Recommendation

Use a model-comparison workflow instead of treating one XGBoost run as the
final paper model.

Primary model family:

- `pca_xgb`: PCA target compression of calibrated RD/TD B-H curves followed by
  XGBoost regression.
- Baselines: `direct_xgb`, `extra_trees`, `pca_extra_trees`.

Relationship between the four candidate names:

| Candidate | Regressor | Output strategy | Meaning |
| --- | --- | --- | --- |
| `direct_xgb` | XGBoost | direct multi-output | Predict every B-H target point directly. |
| `pca_xgb` | XGBoost | PCA target compression | This is "PCA + XGBoost": predict PCA coefficients, then inverse-transform to B-H curves. |
| `extra_trees` | ExtraTrees | direct multi-output | Non-boosting tree ensemble baseline. |
| `pca_extra_trees` | ExtraTrees | PCA target compression | Same PCA-target idea, but with ExtraTrees instead of XGBoost. |

Reasoning:

- Inputs are low-dimensional tabular texture descriptors.
- MuMax3 samples are expensive, so deep neural networks are data-hungry for
  this stage.
- B-H curve points are strongly correlated; PCA target compression preserves
  curve shape better than independent pointwise regression.
- Physics calibration still runs before export, so model outputs remain
  monotone and Maxwell-ready.

## Minimum Acceptance

A model is paper-eligible only when all of the following are true:

- At least 24 valid material configurations after aggregation.
- A held-out validation set exists with at least 4 samples.
- Model selection is based on cross-validated B-H RMSE, not training loss.
- Final report includes model ranking, held-out B-H RMSE/MAE/MAPE/R2, feature
  columns, target columns, and dataset manifest.
- Scalar targets such as Hc and mu_max are not used as headline accuracy
  metrics because the current micromagnetic mesh makes them lower confidence.

## Recommended Dataset

Paper-minimum:

- 32 LHS texture samples.
- 8 to 16 grains per configuration.
- RD/TD angles only.
- `N_STEPS=100`.

Paper-recommended:

- 64 LHS texture samples.
- 16 grains per configuration.
- RD/TD angles only.
- `N_STEPS=100`.

Paper-strong:

- 96 or more LHS texture samples.
- 32 grains per configuration.
- RD/TD angles only.
- `N_STEPS=100`.

The current fast pilot model is not paper-eligible because it has only 6 valid
samples and was generated with fast `N_STEPS=8`.

## Presets

There are two preset layers.

Pipeline presets control how many MuMax3 simulations are generated:

| Preset | Samples | Grains | Estimated RD/TD tasks | Purpose |
| --- | ---: | ---: | ---: | --- |
| `smoke` | 6 | 4 | 48 | End-to-end link test only. |
| `lite` | 24 | 8 | 384 | Lightweight valid holdout metrics. |
| `std` | 64 | 16 | 2048 | Standard research run. |
| `max` | 128 | 32 | 8192 | High-cost final data generation. |

Paper-training presets control model selection on an already aggregated dataset:

| Preset | Min samples | CV folds | PCA max components | Candidate models |
| --- | ---: | ---: | ---: | --- |
| `smoke` | 8 | 3 | 4 | all four candidates |
| `lite` | 24 | 3 | 6 | all four candidates |
| `std` | 48 | 5 | 8 | all four candidates |
| `max` | 96 | 5 | 12 | all four candidates |

Model selection ranks candidates by cross-validated `bh_rmse_T_mean`; the
lowest B-H RMSE wins, with R2 used only as a secondary sanity check.

## Training Command

After the MuMax3 batch finishes and the dataset is aggregated:

```powershell
python modules\paper_surrogate_trainer.py data\datasets\<dataset>.csv --preset std
```

The output is saved under:

```text
data/paper_models/paper_model_YYYYMMDD_HHMMSS/
```

Key files:

- `summary.json`
- `model_ranking.csv`
- `cv_fold_metrics.csv`
- `metrics.json`
- `config.json`
- `model.pkl`

## Current Status

The existing dataset `dataset_20260618_045921_repaired_c965fb09-0.csv` is
correctly rejected by the paper trainer:

```text
status = insufficient_data
n_samples = 6
min_samples = 24
```
