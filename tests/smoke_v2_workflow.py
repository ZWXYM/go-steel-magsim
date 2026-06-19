from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
MODULES = ROOT / "modules"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(MODULES))

from anisotropy_interpolator import interpolate_full_direction
from dataset_builder import DatasetBuilder, MOTOR_ANGLES, STANDARD_H_POINTS
from maxwell_exporter import export_from_prediction
from ml_trainer import BHPredictor, MIN_RELIABLE_EVAL_SAMPLES
from odf_texture import generate_batch_lhs
from paper_surrogate_trainer import (
    PaperSurrogateTrainer,
    get_paper_training_presets,
    resolve_paper_training_config,
)
from pipeline_runner import DEFAULT_FIXED_HALFWIDTH_DEG, get_pipeline_presets, resolve_pipeline_config


def _copy_existing_grains(dst_config: Path) -> None:
    src_files = sorted((ROOT / "output").glob("**/grain_*.txt"))
    if not src_files:
        raise RuntimeError("No existing grain_*.txt files available for fixture seeding")

    for angle in (0, 90):
        angle_dir = dst_config / f"angle_{angle:03d}"
        angle_dir.mkdir(parents=True, exist_ok=True)
        for i, src in enumerate(src_files[:2], start=1):
            shutil.copyfile(src, angle_dir / f"grain_{i:03d}.txt")


def make_fixture() -> Path:
    out_root = ROOT / "tests" / "fixtures" / "output_minimal"
    config = out_root / "F70_T15_N10"
    if config.exists():
        shutil.rmtree(config)
    config.mkdir(parents=True, exist_ok=True)
    (config / "simulation_parameters_template.txt").write_text(
        "\n".join([
            "Goss texture fraction (f_Goss):      0.70",
            "Texture rotation angle (theta_0):    15 degrees",
            "Texture halfwidth (sigma):           10.0",
            "Halfwidth policy:                    fixed_for_pipeline",
            "Number of grains:                    10",
            "Si content:                          3.0",
        ]),
        encoding="utf-8",
    )
    _copy_existing_grains(config)
    return out_root


def test_interpolator_boundaries() -> None:
    rd = {"H": STANDARD_H_POINTS, "B": [0.35, 0.55, 0.9, 1.25, 1.55, 1.72, 1.92, 2.02]}
    td = {"H": STANDARD_H_POINTS, "B": [0.25, 0.42, 0.72, 1.02, 1.28, 1.42, 1.62, 1.75]}
    full = interpolate_full_direction(rd, td, angles_deg=[0, 90, 180])
    assert np.allclose(full["curves"]["0"]["B"], full["curves"]["180"]["B"])
    assert np.allclose(full["curves"]["0"]["B"], interpolate_full_direction(rd, td, angles_deg=[0])["curves"]["0"]["B"])
    assert np.all(np.diff(full["curves"]["90"]["B"]) >= -1e-12)
    assert "transfer_matrices" in full
    assert full["transfer_matrices"]["90"]["rd_td_projection_weights"][1] > 0.99


def test_dataset_builder_fixture() -> Path:
    out_root = make_fixture()
    db = DatasetBuilder(output_root=str(out_root), dataset_dir=str(ROOT / "tests" / "tmp" / "datasets"))
    configs = db.scan_output_dir()
    assert len(configs) == 1
    assert MOTOR_ANGLES == [0, 90]
    df = db.build_dataset(target_angles=MOTOR_ANGLES)
    assert len(df) == 1
    assert all(f"B_45deg_H{h}" not in df.columns for h in STANDARD_H_POINTS)
    assert all(f"B_0deg_H{h}" in df.columns for h in STANDARD_H_POINTS)
    assert all(f"B_90deg_H{h}" in df.columns for h in STANDARD_H_POINTS)
    sidecar = out_root / "F70_T15_N10" / "material_representative_summary.json"
    assert sidecar.exists()
    report = json.loads(sidecar.read_text(encoding="utf-8"))
    assert report["angles"]["0"]["status"] == "ok"
    assert report["angles"]["90"]["status"] == "ok"
    saved = Path(db.save_dataset(df, tag="smoke"))
    assert saved.exists()
    assert saved.with_suffix(".metadata.json").exists()
    return saved


def _write_param_template(config: Path, f_goss: float, theta: int, n_grains: int) -> None:
    config.mkdir(parents=True, exist_ok=True)
    (config / "simulation_parameters_template.txt").write_text(
        "\n".join([
            f"Goss texture fraction (f_Goss):      {f_goss:.2f}",
            f"Texture rotation angle (theta_0):    {theta} degrees",
            "Texture halfwidth (sigma):           10.0",
            "Halfwidth policy:                    fixed_for_pipeline",
            f"Number of grains:                    {n_grains}",
            "Si content:                          3.0",
        ]),
        encoding="utf-8",
    )
    _copy_existing_grains(config)


def test_dataset_builder_path_filter() -> None:
    out_root = ROOT / "tests" / "tmp" / "output_filter"
    if out_root.exists():
        shutil.rmtree(out_root)
    left = out_root / "run_a" / "pipeline_alpha" / "F61_T10_N10"
    right = out_root / "run_b" / "pipeline_beta" / "F82_T20_N10"
    _write_param_template(left, 0.61, 10, 10)
    _write_param_template(right, 0.82, 20, 10)

    db = DatasetBuilder(output_root=str(out_root), dataset_dir=str(ROOT / "tests" / "tmp" / "datasets"))
    df = db.build_dataset(config_paths=[str(left)], target_angles=MOTOR_ANGLES)
    assert len(df) == 1
    assert df.iloc[0]["config_name"] == "F61_T10_N10"
    assert df.iloc[0]["halfwidth_policy"] == "fixed_for_pipeline"
    assert float(df.iloc[0]["Si_content"]) == 3.0


def test_texture_generator_exact_grain_count() -> None:
    out = ROOT / "tests" / "tmp" / "preinput_exact"
    if out.exists():
        shutil.rmtree(out)
    batch_dir = Path(generate_batch_lhs(
        n_samples=2,
        f_Goss_range=(0.6, 0.7),
        theta_0_range=(5, 10),
        halfwidth_range=(10, 10),
        N_grains_range=(4, 4),
        output_dir=str(out),
        save_plots=False,
    ))
    files = sorted(batch_dir.glob("grain_orientations_ODF_*.txt"))
    assert len(files) == 2
    for file in files:
        rows = [
            line for line in file.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        assert len(rows) == 4


def test_flask_full_direction_api() -> None:
    from app import app

    client = app.test_client()
    config_path = ROOT / "tests" / "fixtures" / "output_minimal" / "F70_T15_N10"
    response = client.get("/api/analysis/full-direction", query_string={
        "config_path": str(config_path),
        "Msat": "1520000",
    })
    assert response.status_code == 200, response.get_data(as_text=True)
    data = response.get_json()
    assert data["rd_source"] == "angle_000"
    assert data["td_source"] == "angle_090"
    assert "0" in data["curves"] and "90" in data["curves"] and "180" in data["curves"]
    assert "transfer_matrices" in data and "90" in data["transfer_matrices"]


def test_pipeline_presets_and_api() -> None:
    presets = get_pipeline_presets()
    assert set(presets) == {"smoke", "lite", "std", "max"}
    assert presets["smoke"]["config"]["n_samples"] < MIN_RELIABLE_EVAL_SAMPLES
    assert presets["lite"]["config"]["n_samples"] >= MIN_RELIABLE_EVAL_SAMPLES
    assert presets["lite"]["metric_expectation"] == "holdout_validation"
    assert presets["lite"]["estimate"]["mumax3_tasks"] == 24 * 2 * 8
    assert presets["max"]["estimate"]["mumax3_tasks"] > presets["std"]["estimate"]["mumax3_tasks"]

    resolved = resolve_pipeline_config({
        "preset": "lite",
        "n_samples": 30,
        "N_grains_range": [10, 10],
        "xgb_params": {"max_depth": 5},
    })
    assert resolved["preset_id"] == "lite"
    assert resolved["xgb_params"]["n_estimators"] == 220
    assert resolved["xgb_params"]["max_depth"] == 5
    assert resolved["estimated_tasks"]["mumax3_tasks"] == 30 * 2 * 10

    from app import app
    response = app.test_client().get("/api/pipeline/presets")
    assert response.status_code == 200
    data = response.get_json()
    assert data["presets"]["std"]["config"]["sim_n_steps"] == 100


def test_ml_dataset_script_generation_api() -> None:
    from app import app

    preset_response = app.test_client().get("/api/ml-dataset/presets")
    assert preset_response.status_code == 200
    presets = preset_response.get_json()["presets"]
    assert presets["lite"]["kind"] == "dataset_generation"
    assert "训练超参数" in presets["lite"]["description"]

    run_id = "ml_dataset_smoke_api"
    dirs = [
        ROOT / "preinput" / run_id,
        ROOT / "grain_scripts" / run_id,
    ]
    files = [
        ROOT / "scripts" / f"run_{run_id}.ps1",
        ROOT / "scripts" / f"{run_id}_manifest.json",
    ]
    for path in dirs:
        if path.exists():
            shutil.rmtree(path)
    for path in files:
        if path.exists():
            path.unlink()

    try:
        response = app.test_client().post("/api/ml-dataset/generate-scripts", json={
            "preset": "smoke",
            "run_id": run_id,
        })
        assert response.status_code == 200, response.get_data(as_text=True)
        data = response.get_json()
        assert data["run_id"] == run_id
        assert data["recommended_output_run"] == f"run_{run_id}"
        assert Path(data["batch_script"]).exists()
        assert Path(data["manifest_path"]).exists()
        script = Path(data["batch_script"]).read_text(encoding="utf-8")
        assert f"output\\run_{run_id}" in script
        assert f"grain_scripts\\{run_id}" in script
    finally:
        for path in dirs:
            if path.exists():
                shutil.rmtree(path)
        for path in files:
            if path.exists():
                path.unlink()


def make_training_dataset(path: Path, n_rows: int = 8) -> Path:
    rows = []
    for i in range(n_rows):
        f = 0.55 + i * 0.04
        theta = 4 + i * 3
        row = {
            "config_name": f"synthetic_{i}",
            "f_Goss": f,
            "theta_0_deg": theta,
            "halfwidth_deg": 10.0,
            "N_grains": 40 + i * 5,
            "Si_content": 2.9 + 0.02 * i,
        }
        for h in STANDARD_H_POINTS:
            base = min(2.05, 0.18 + 0.00024 * h + 0.25 * f - 0.002 * theta)
            row[f"B_0deg_H{h}"] = max(base, 0.01)
            row[f"B_90deg_H{h}"] = max(base * 0.86, 0.01)
        row["Hc_0deg"] = 100 + i
        row["Mr_0deg"] = 0.05 + i * 0.001
        row["mu_max_0deg"] = 1000 + i * 25
        row["Hc_90deg"] = 120 + i
        row["Mr_90deg"] = 0.04 + i * 0.001
        row["mu_max_90deg"] = 850 + i * 20
        rows.append(row)
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def test_training_prediction_and_export() -> None:
    assert DEFAULT_FIXED_HALFWIDTH_DEG == 10.0
    dataset = make_training_dataset(ROOT / "tests" / "tmp" / "synthetic_train.csv")
    predictor = BHPredictor(model_dir=str(ROOT / "tests" / "tmp" / "models"))
    metrics = predictor.train(
        str(dataset),
        xgb_params={"n_estimators": 8, "max_depth": 2, "learning_rate": 0.1, "n_jobs": 1},
        test_size=0.25,
    )
    assert "halfwidth_deg" in metrics["dropped_constant_features"]
    assert metrics["metric_reliability"] == "exploratory_only"
    assert metrics["r2_avg"] is None
    assert metrics["train_r2_avg"] is not None
    result = predictor.predict_bh({
        "f_Goss": 0.70,
        "theta_0_deg": 15,
        "halfwidth_deg": 10.0,
        "N_grains": 80,
        "Si_content": 3.0,
    })
    assert {"RD", "TD", "full_direction"}.issubset(result.keys())
    assert "Cross45" not in result
    assert np.allclose(result["full_direction"]["curves"]["0"]["B"], result["RD"]["B"])

    export_dir = ROOT / "tests" / "tmp" / "exports"
    amat = Path(export_from_prediction(result, "SMOKE_GO", export_dir=str(export_dir)))
    assert amat.exists()
    meta = amat.with_suffix(".metadata.json")
    assert meta.exists()
    text = amat.read_text(encoding="utf-8")
    for prop in ("BH_Data_X", "BH_Data_Y"):
        section = text.split(f'name="{prop}"', 1)[1].split("</MatProperty>", 1)[0]
        points = [tuple(map(float, m)) for m in re.findall(r'X="([^"]+)" Y="([^"]+)"', section)]
        assert all(points[i][0] <= points[i + 1][0] for i in range(len(points) - 1))
        assert all(points[i][1] <= points[i + 1][1] + 1e-12 for i in range(len(points) - 1))


def test_reliable_metric_release() -> None:
    dataset = make_training_dataset(ROOT / "tests" / "tmp" / "synthetic_train_reliable.csv", n_rows=30)
    predictor = BHPredictor(model_dir=str(ROOT / "tests" / "tmp" / "models_reliable"))
    metrics = predictor.train(
        str(dataset),
        xgb_params={"n_estimators": 8, "max_depth": 2, "learning_rate": 0.1, "n_jobs": 1},
        test_size=0.25,
    )
    assert metrics["metric_reliability"] == "holdout_validation"
    assert metrics["r2_avg"] is not None
    assert metrics["mape_avg"] is not None


def test_paper_surrogate_workflow() -> None:
    presets = get_paper_training_presets()
    assert set(presets) == {"smoke", "lite", "std", "max"}
    assert set(presets["std"]["config"]["candidate_models"]) == {
        "direct_xgb",
        "pca_xgb",
        "extra_trees",
        "pca_extra_trees",
    }
    assert presets["std"]["candidate_model_relationships"]["pca_xgb"]["target_strategy"] == "pca_target"
    resolved = resolve_paper_training_config({
        "preset": "lite",
        "n_splits": 4,
        "xgb_params": {"max_depth": 5},
        "extra_trees_params": {"n_estimators": 33},
    })
    assert resolved["preset_id"] == "lite"
    assert resolved["n_splits"] == 4
    assert resolved["xgb_params"]["n_estimators"] == 160
    assert resolved["xgb_params"]["max_depth"] == 5
    assert resolved["extra_trees_params"]["n_estimators"] == 33

    from app import app
    response = app.test_client().get("/api/ml/paper-presets")
    assert response.status_code == 200
    assert response.get_json()["presets"]["max"]["config"]["pca_max_components"] == 12

    small_dataset = make_training_dataset(ROOT / "tests" / "tmp" / "synthetic_train_paper_small.csv", n_rows=8)
    trainer = PaperSurrogateTrainer(
        output_dir=str(ROOT / "tests" / "tmp" / "paper_models"),
        xgb_params={"n_estimators": 6, "max_depth": 2, "learning_rate": 0.1, "n_jobs": 1},
    )
    small = trainer.run(str(small_dataset), min_samples=24, n_splits=3)
    assert small["status"] == "insufficient_data"

    dataset = make_training_dataset(ROOT / "tests" / "tmp" / "synthetic_train_paper.csv", n_rows=30)
    result = trainer.run(str(dataset), min_samples=24, n_splits=3)
    assert result["status"] == "ok"
    assert result["selected_model"] in {"direct_xgb", "pca_xgb", "extra_trees", "pca_extra_trees"}
    assert set(result["candidate_models"]) == {"direct_xgb", "pca_xgb", "extra_trees", "pca_extra_trees"}
    assert result["candidate_model_relationships"]["pca_xgb"]["label"] == "PCA + XGBoost"
    artifact_dir = Path(result["artifact_dir"])
    assert (artifact_dir / "summary.json").exists()
    assert (artifact_dir / "model_ranking.csv").exists()


def test_saved_model_metadata_listing() -> None:
    paper_dir = ROOT / "data" / "paper_models" / "paper_model_smoke_list_test"
    if paper_dir.exists():
        shutil.rmtree(paper_dir)
    paper_dir.mkdir(parents=True, exist_ok=True)
    dataset_path = str(ROOT / "tests" / "tmp" / "synthetic_train_paper.csv")
    (paper_dir / "summary.json").write_text(json.dumps({
        "model_id": paper_dir.name,
        "selected_model": "pca_xgb",
        "dataset_path": dataset_path,
        "created": "2099-01-01T00:00:00",
        "n_samples": 30,
        "target_cols": ["B_0deg_H100", "B_90deg_H100"],
        "candidate_models": ["direct_xgb", "pca_xgb"],
        "selection_metric": "cv bh_rmse_T mean",
        "holdout_metrics": {"bh_rmse_T": 0.0123, "r2_avg": 0.91, "mape_avg": 0.02},
        "ranking": [{"model": "pca_xgb", "bh_rmse_T_mean": 0.011, "bh_rmse_T_std": 0.001}],
    }, ensure_ascii=False), encoding="utf-8")
    try:
        predictor = BHPredictor(model_dir=str(ROOT / "tests" / "tmp" / "models"))
        models = predictor.list_models()
        item = next(m for m in models if m["model_id"] == paper_dir.name)
        assert item["model_type"] == "pca_xgb"
        assert item["dataset_name"] == "synthetic_train_paper.csv"
        assert item["cv_bh_rmse_T_mean"] == 0.011
        assert item["prediction_capable"] is False
    finally:
        shutil.rmtree(paper_dir)


if __name__ == "__main__":
    test_interpolator_boundaries()
    dataset_path = test_dataset_builder_fixture()
    test_dataset_builder_path_filter()
    test_texture_generator_exact_grain_count()
    test_flask_full_direction_api()
    test_pipeline_presets_and_api()
    test_ml_dataset_script_generation_api()
    test_training_prediction_and_export()
    test_reliable_metric_release()
    test_paper_surrogate_workflow()
    test_saved_model_metadata_listing()
    print(json.dumps({
        "status": "ok",
        "dataset_fixture": str(dataset_path),
        "checks": [
            "interpolator_boundaries",
            "material_representative_dataset",
            "dataset_path_filter",
            "exact_grain_count_texture_generation",
            "flask_full_direction_api",
            "pipeline_presets_and_api",
            "ml_dataset_script_generation_api",
            "constant_halfwidth_feature_drop",
            "small_sample_metric_reliability",
            "reliable_metric_release",
            "paper_training_presets_and_api",
            "paper_surrogate_model_selection",
            "saved_model_metadata_listing",
            "prediction_full_direction",
            "maxwell_metadata_export",
        ],
    }, ensure_ascii=False, indent=2))
