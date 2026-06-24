"""
pipeline_runner.py

Automatic pipeline orchestration:
texture sampling -> MX3 script generation -> batch script generation ->
simulation wait/run -> dataset aggregation -> surrogate model training.
"""
from __future__ import annotations

import contextlib
import copy
import ctypes
import io
import json
import platform
import shutil
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

import batch_scheduler as gpb
import mx3_generator as gis
from dataset_builder import DatasetBuilder, FULL_ANGLES, MOTOR_ANGLES
from ml_trainer import BHPredictor, MIN_RELIABLE_EVAL_SAMPLES


DEFAULT_FIXED_HALFWIDTH_DEG = 10.0

PIPELINE_PRESETS = {
    "smoke": {
        "label": "快速检查",
        "description": "验证一键链路可用，不发布正式验证指标。",
        "metric_expectation": "exploratory_only",
        "config": {
            "n_samples": 6,
            "angle_mode": "motor",
            "f_Goss_range": [0.55, 0.85],
            "theta_0_range": [3, 25],
            "halfwidth_range": [DEFAULT_FIXED_HALFWIDTH_DEG, DEFAULT_FIXED_HALFWIDTH_DEG],
            "N_grains_range": [4, 4],
            "Si_content": 3.0,
            "sim_n_steps": 8,
            "test_size": 0.25,
            "xgb_params": {
                "n_estimators": 80,
                "max_depth": 2,
                "learning_rate": 0.08,
                "subsample": 0.95,
                "colsample_bytree": 0.95,
                "random_state": 42,
                "eval_metric": "rmse",
                "verbosity": 0,
                "n_jobs": -1,
            },
        },
    },
    "lite": {
        "label": "小规模训练",
        "description": "小规模可用模型，会输出并展示验证指标。",
        "metric_expectation": "holdout_validation",
        "config": {
            "n_samples": 24,
            "angle_mode": "motor",
            "f_Goss_range": [0.45, 0.90],
            "theta_0_range": [1, 30],
            "halfwidth_range": [DEFAULT_FIXED_HALFWIDTH_DEG, DEFAULT_FIXED_HALFWIDTH_DEG],
            "N_grains_range": [8, 8],
            "Si_content": 3.0,
            "sim_n_steps": 40,
            "test_size": 0.25,
            "xgb_params": {
                "n_estimators": 220,
                "max_depth": 3,
                "learning_rate": 0.05,
                "subsample": 0.9,
                "colsample_bytree": 0.9,
                "random_state": 42,
                "eval_metric": "rmse",
                "verbosity": 0,
                "n_jobs": -1,
            },
        },
    },
    "std": {
        "label": "标准规模训练",
        "description": "标准研究规模，推荐作为正式实验起点。",
        "metric_expectation": "holdout_validation",
        "config": {
            "n_samples": 64,
            "angle_mode": "motor",
            "f_Goss_range": [0.40, 0.92],
            "theta_0_range": [1, 35],
            "halfwidth_range": [DEFAULT_FIXED_HALFWIDTH_DEG, DEFAULT_FIXED_HALFWIDTH_DEG],
            "N_grains_range": [16, 16],
            "Si_content": 3.0,
            "sim_n_steps": 150,
            "sim_h_max": 50000.0,
            "test_size": 0.20,
            "xgb_params": {
                "n_estimators": 400,
                "max_depth": 3,
                "learning_rate": 0.04,
                "subsample": 0.9,
                "colsample_bytree": 0.9,
                "random_state": 42,
                "eval_metric": "rmse",
                "verbosity": 0,
                "n_jobs": -1,
            },
        },
    },
    "max": {
        "label": "大规模训练",
        "description": "全量高成本配置，适合最终批量数据构建。",
        "metric_expectation": "holdout_validation",
        "config": {
            "n_samples": 128,
            "angle_mode": "motor",
            "f_Goss_range": [0.35, 0.95],
            "theta_0_range": [0, 40],
            "halfwidth_range": [DEFAULT_FIXED_HALFWIDTH_DEG, DEFAULT_FIXED_HALFWIDTH_DEG],
            "N_grains_range": [32, 32],
            "Si_content": 3.0,
            "sim_n_steps": 150,
            "sim_h_max": 50000.0,
            "test_size": 0.20,
            "xgb_params": {
                "n_estimators": 650,
                "max_depth": 4,
                "learning_rate": 0.03,
                "subsample": 0.9,
                "colsample_bytree": 0.9,
                "random_state": 42,
                "eval_metric": "rmse",
                "verbosity": 0,
                "n_jobs": -1,
            },
        },
    },
}


def estimate_pipeline_tasks(config: dict) -> dict:
    cfg = config or {}
    angle_mode = cfg.get("angle_mode", "motor")
    angles = FULL_ANGLES if angle_mode == "full" else MOTOR_ANGLES
    n_samples = int(cfg.get("n_samples", 20))
    grains_range = cfg.get("N_grains_range", [50, 150])
    if not isinstance(grains_range, (list, tuple)) or len(grains_range) != 2:
        grains_range = [50, 150]
    n_grains_mid = int(round((float(grains_range[0]) + float(grains_range[1])) / 2))
    n_grains_mid = max(1, n_grains_mid)
    return {
        "n_samples": n_samples,
        "n_angles": len(angles),
        "angles": angles,
        "n_grains_est": n_grains_mid,
        "mumax3_tasks": int(n_samples * len(angles) * n_grains_mid),
        "sim_n_steps": int(cfg.get("sim_n_steps", 100)),
    }


def _preset_payload(preset_id: str, preset: dict) -> dict:
    cfg = copy.deepcopy(preset["config"])
    payload = {
        "id": preset_id,
        "label": preset["label"],
        "description": preset["description"],
        "metric_expectation": preset["metric_expectation"],
        "min_reliable_eval_samples": MIN_RELIABLE_EVAL_SAMPLES,
        "config": cfg,
    }
    payload["estimate"] = estimate_pipeline_tasks(cfg)
    return payload


def get_pipeline_presets() -> dict:
    return {
        preset_id: _preset_payload(preset_id, preset)
        for preset_id, preset in PIPELINE_PRESETS.items()
    }


def resolve_pipeline_config(config: dict | None) -> dict:
    incoming = copy.deepcopy(config or {})
    preset_id = incoming.get("preset") or incoming.get("preset_id")
    incoming.pop("preset", None)
    incoming.pop("preset_id", None)
    base: dict = {}
    if preset_id in PIPELINE_PRESETS:
        base = copy.deepcopy(PIPELINE_PRESETS[preset_id]["config"])
        base["preset"] = preset_id
        base["preset_id"] = preset_id
        base["preset_label"] = PIPELINE_PRESETS[preset_id]["label"]
        base["metric_expectation"] = PIPELINE_PRESETS[preset_id]["metric_expectation"]

    preset_xgb = base.get("xgb_params", {})
    incoming_xgb = incoming.pop("xgb_params", None)
    for key, value in incoming.items():
        base[key] = value
    if incoming_xgb is not None:
        merged = copy.deepcopy(preset_xgb)
        merged.update(incoming_xgb)
        base["xgb_params"] = merged

    if "halfwidth_range" not in base:
        base["halfwidth_range"] = [DEFAULT_FIXED_HALFWIDTH_DEG, DEFAULT_FIXED_HALFWIDTH_DEG]
    base["estimated_tasks"] = estimate_pipeline_tasks(base)
    return base


STAGES = [
    "texture_gen",
    "script_gen",
    "batch_gen",
    "wait_sim",
    "analyze",
    "train",
]

STAGE_NAMES = {
    "texture_gen": "织构文件生成",
    "script_gen": "MX3 脚本生成",
    "batch_gen": "批处理脚本生成",
    "wait_sim": "等待仿真完成",
    "analyze": "结果聚合分析",
    "train": "XGBoost 训练",
}


class PipelineStopped(Exception):
    pass


class PipelineRunner:
    _PERSIST_DIR = Path("data/pipelines")

    def __init__(self):
        self.pipelines: dict[str, dict] = {}
        self._PERSIST_DIR.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _prevent_sleep():
        if platform.system() != "Windows":
            return
        try:
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000003)
        except Exception:
            pass

    @staticmethod
    def _allow_sleep():
        if platform.system() != "Windows":
            return
        try:
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
        except Exception:
            pass

    @staticmethod
    def _public_state(state: dict) -> dict:
        return {
            k: v for k, v in state.items()
            if k not in {"_resume_event", "_stop_event", "_thread", "_process"}
        }

    def _state(self, pid: str) -> dict:
        if pid not in self.pipelines:
            self._restore(pid)
        return self.pipelines[pid]

    def _persist(self, pid: str):
        state = self.pipelines.get(pid)
        if state is None:
            return
        self._PERSIST_DIR.mkdir(parents=True, exist_ok=True)
        state["updated_at"] = datetime.now().isoformat()
        try:
            (self._PERSIST_DIR / f"{pid}.json").write_text(
                json.dumps(self._public_state(state), ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
        except Exception as exc:
            print(f"[pipeline] persist failed {pid}: {exc}")

    def _restore(self, pid: str) -> bool:
        path = self._PERSIST_DIR / f"{pid}.json"
        if not path.exists():
            return False
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
            if state.get("status") in {"running", "running_sim", "stopping"}:
                state["status"] = "interrupted"
                state["error"] = state.get("error") or "Backend restarted while this pipeline was running."
            state["_resume_event"] = threading.Event()
            state["_stop_event"] = threading.Event()
            state["_thread"] = None
            state["_process"] = None
            self.pipelines[pid] = state
            return True
        except Exception as exc:
            print(f"[pipeline] restore failed {pid}: {exc}")
            return False

    def _load_all_persisted(self):
        self._PERSIST_DIR.mkdir(parents=True, exist_ok=True)
        for path in self._PERSIST_DIR.glob("*.json"):
            if path.stem not in self.pipelines:
                self._restore(path.stem)

    def _log(self, pid: str, msg: str):
        state = self._state(pid)
        entry = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
        state["log"].append(entry)
        if len(state["log"]) > 200:
            state["log"] = state["log"][-200:]
        try:
            print(entry)
        except UnicodeEncodeError:
            enc = getattr(sys.stdout, "encoding", None) or "utf-8"
            print(entry.encode(enc, errors="replace").decode(enc, errors="replace"))
        self._persist(pid)

    def _set_stage(self, pid: str, stage: str, pct: int = 0):
        state = self._state(pid)
        state["stage"] = stage
        state["stage_index"] = STAGES.index(stage)
        state["progress_pct"] = pct
        state["status"] = "running" if stage != "wait_sim" else "waiting_sim"
        self._persist(pid)

    def _check_stop(self, pid: str):
        state = self._state(pid)
        ev = state.get("_stop_event")
        if ev and ev.is_set():
            raise PipelineStopped("流水线已由用户终止。")

    def _wait_for_resume_or_stop(self, pid: str, resume_event: threading.Event):
        while not resume_event.is_set():
            self._check_stop(pid)
            resume_event.wait(1.0)

    def start(self, config: dict) -> str:
        config = resolve_pipeline_config(config)
        pid = str(uuid.uuid4())[:10]
        self.pipelines[pid] = {
            "id": pid,
            "stage": STAGES[0],
            "stage_index": 0,
            "total_stages": len(STAGES),
            "progress_pct": 0,
            "status": "running",
            "log": [],
            "results": {},
            "error": None,
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "config": config,
            "_resume_event": threading.Event(),
            "_stop_event": threading.Event(),
            "_thread": None,
            "_process": None,
        }
        thread = threading.Thread(target=self._run, args=(pid, config), daemon=True)
        self.pipelines[pid]["_thread"] = thread
        self._persist(pid)
        thread.start()
        return pid

    def _run_generated_stages(self, pid: str, config: dict, script_angles: list[int | float], n_samples: int):
        state = self._state(pid)
        angle_mode = config.get("angle_mode", "motor")

        self._check_stop(pid)
        self._set_stage(pid, "texture_gen")
        self._log(pid, f"开始织构生成，样本数={n_samples}，角度模式={angle_mode}")
        self._log(pid, f"当前工作流使用固定织构半宽 {DEFAULT_FIXED_HALFWIDTH_DEG:g} deg。")

        import importlib.util

        tex_path = Path(__file__).resolve().parent / "odf_texture.py"
        spec = importlib.util.spec_from_file_location("odf_texture", str(tex_path))
        tex_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tex_mod)

        batch_dir = tex_mod.generate_batch_lhs(
            n_samples=n_samples,
            f_Goss_range=config.get("f_Goss_range", [0.4, 0.9]),
            theta_0_range=config.get("theta_0_range", [1, 30]),
            halfwidth_range=[DEFAULT_FIXED_HALFWIDTH_DEG, DEFAULT_FIXED_HALFWIDTH_DEG],
            N_grains_range=config.get("N_grains_range", [50, 150]),
            Si_content=config.get("Si_content", 3.0),
            output_dir=f"preinput/pipeline_{pid}",
        )
        state["results"]["batch_dir"] = batch_dir
        state["progress_pct"] = 100
        self._log(pid, f"织构文件已保存至: {batch_dir}")

        self._check_stop(pid)
        self._set_stage(pid, "script_gen")
        gis.SimulationConfig.N_STEPS = max(4, int(config.get("sim_n_steps", gis.SimulationConfig.N_STEPS)))
        self._log(pid, f"仿真步数 N_STEPS={gis.SimulationConfig.N_STEPS}")
        if "sim_h_max" in config:
            gis.SimulationConfig.H_MAX = float(config.get("sim_h_max"))
            self._log(pid, f"最大外场 H_MAX={gis.SimulationConfig.H_MAX:g} A/m")
        self._log(pid, f"生成 MX3 脚本，角度={script_angles}")
        txt_files = list(Path(batch_dir).glob("grain_orientations_ODF_*.txt"))
        for i, tf in enumerate(txt_files):
            self._check_stop(pid)
            with contextlib.redirect_stdout(io.StringIO()):
                gis.generate_scripts_for_config(str(tf), angles=script_angles, output_dir=f"grain_scripts/pipeline_{pid}")
            state["progress_pct"] = int((i + 1) / max(len(txt_files), 1) * 100)
        self._log(pid, f"MX3 脚本生成完成，共 {len(txt_files)} 个配置")

        self._check_stop(pid)
        self._set_stage(pid, "batch_gen")
        self._log(pid, "生成批处理脚本 (.ps1 / .sh)")
        configs_for_batch = gis.get_configs_in_dir(f"grain_scripts/pipeline_{pid}")
        run_name = f"pipeline_{pid}"
        configs_for_batch = [
            (cname, {**cdata, "output_name": Path(cname.replace("\\", "/")).name}, angles)
            for cname, cdata, angles in configs_for_batch
        ]
        Path("scripts").mkdir(exist_ok=True)
        batch_ps1 = f"scripts/run_pipeline_{pid}.ps1"
        batch_sh = f"scripts/run_pipeline_{pid}.sh"
        with contextlib.redirect_stdout(io.StringIO()):
            gpb.generate_multi_config_powershell_script(configs_for_batch, batch_ps1, run_name=run_name)
            gpb.generate_multi_config_bash_script(configs_for_batch, batch_sh, run_name=run_name)
        state["results"]["batch_script"] = batch_ps1
        state["results"]["batch_script_sh"] = batch_sh
        state["progress_pct"] = 100
        self._log(pid, f"批处理脚本（Windows）: {batch_ps1}")
        self._log(pid, f"批处理脚本（Linux）:   {batch_sh}")

        self._check_stop(pid)
        self._set_stage(pid, "wait_sim")
        return batch_ps1, batch_sh

    def _run_or_wait_simulation(self, pid: str, batch_ps1: str, batch_sh: str):
        state = self._state(pid)
        resume_event = state["_resume_event"]

        mumax3_available = False
        try:
            subprocess.run(["mumax3", "-v"], capture_output=True, timeout=12)
            mumax3_available = True
        except Exception:
            pass

        if not mumax3_available:
            state["status"] = "waiting_sim"
            self._persist(pid)
            script = batch_ps1 if platform.system() == "Windows" else batch_sh
            self._log(pid, "未在 PATH 中检测到 mumax3")
            self._log(pid, f"请手动在 GPU 机器上运行: {script}")
            self._log(pid, "仿真完成后，点击 [仿真已完成，继续]。")
            self._wait_for_resume_or_stop(pid, resume_event)
            return

        state["status"] = "running_sim"
        self._persist(pid)
        is_win = platform.system() == "Windows"
        sim_script = batch_ps1 if is_win else batch_sh
        cmd = (
            ["powershell", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", sim_script]
            if is_win else ["bash", sim_script]
        )
        self._log(pid, f"检测到 MuMax3，自动执行仿真脚本: {sim_script}")
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            state["_process"] = proc
            for line in proc.stdout:
                self._check_stop(pid)
                stripped = line.rstrip()
                if stripped:
                    self._log(pid, stripped)
            proc.wait()
            if proc.returncode != 0:
                raise RuntimeError(f"仿真脚本退出码 {proc.returncode}，请检查 MuMax3 配置")
            self._log(pid, f"仿真自动完成 (exit={proc.returncode})")
        except PipelineStopped:
            raise
        except RuntimeError:
            raise
        except Exception as exc:
            self._log(pid, f"自动仿真异常: {exc}，切换为手动等待")
            state["status"] = "waiting_sim"
            self._persist(pid)
            self._wait_for_resume_or_stop(pid, resume_event)
        finally:
            state["_process"] = None
            self._persist(pid)

    def _aggregate_and_train(self, pid: str, config: dict, source_pid: str, train_angles: list[int | float], msat: float):
        state = self._state(pid)

        self._check_stop(pid)
        self._log(pid, "进入聚合分析阶段...")
        self._set_stage(pid, "analyze")
        builder = DatasetBuilder()
        pipeline_marker = f"pipeline_{source_pid}"
        pipeline_configs = [
            cfg for cfg in builder.scan_output_dir()
            if pipeline_marker in Path(cfg["config_path"]).parts
        ]
        if not pipeline_configs:
            raise RuntimeError(f"未找到当前流水线 {pipeline_marker} 的仿真结果")

        def progress_cb(cur, tot, name):
            self._check_stop(pid)
            state["progress_pct"] = int(cur / max(tot, 1) * 100)
            self._log(pid, f"  聚合配置 ({cur}/{tot}): {name}")

        df = builder.build_dataset(
            config_paths=[cfg["config_path"] for cfg in pipeline_configs],
            target_angles=train_angles,
            Msat=msat,
            progress_callback=progress_cb,
        )
        if len(df) == 0:
            raise RuntimeError("未找到有效仿真结果，请确认 output/ 目录下有对应结果文件")

        ds_tag = f"pipeline_{pid}" if source_pid == pid else f"pipeline_{source_pid}_rerun_{pid}"
        ds_path = builder.save_dataset(df, tag=ds_tag)
        state["results"]["dataset_path"] = ds_path
        state["results"]["n_samples"] = len(df)
        self._log(pid, f"数据集已保存: {ds_path} ({len(df)} 个样本)")

        self._check_stop(pid)
        self._set_stage(pid, "train")
        self._log(pid, "开始 XGBoost 训练...")
        if len(df) < MIN_RELIABLE_EVAL_SAMPLES:
            self._log(pid, f"样本数 {len(df)} 低于可信验证阈值 {MIN_RELIABLE_EVAL_SAMPLES}，本轮标记为探索模型。")

        predictor = BHPredictor()
        metrics = predictor.train(
            ds_path,
            xgb_params=config.get("xgb_params"),
            test_size=float(config.get("test_size", 0.2)),
        )
        state["results"]["model_id"] = metrics["model_id"]
        state["results"]["r2_avg"] = metrics["r2_avg"]
        state["results"]["mape_avg"] = metrics.get("mape_avg")
        state["results"]["train_r2_avg"] = metrics.get("train_r2_avg")
        state["results"]["train_mape_avg"] = metrics.get("train_mape_avg")
        state["results"]["metric_reliability"] = metrics.get("metric_reliability")
        state["results"]["metric_warnings"] = metrics.get("metric_warnings", [])

        n_actual = len(df)
        candidates = config.get("candidate_models") or ["direct_xgb", "extra_trees", "pca_xgb", "pca_extra_trees"]
        self._log(pid, f"开始候选模型交叉验证选择（{', '.join(candidates)}）...")
        try:
            from paper_surrogate_trainer import PaperSurrogateTrainer, resolve_paper_training_config

            paper_cfg = resolve_paper_training_config({
                "preset": config.get("paper_training_preset") or config.get("preset_id") or config.get("preset") or "lite",
                "candidate_models": config.get("candidate_models"),
            })
            effective_min = max(4, n_actual // 4)
            effective_n_splits = min(int(paper_cfg.get("n_splits", 3)), max(2, n_actual // 6))
            self._log(pid, f"  样本数 {n_actual}，有效 min_samples={effective_min}，CV folds={effective_n_splits}")
            paper_result = PaperSurrogateTrainer(
                output_dir=config.get("paper_model_dir", "data/paper_models"),
                random_state=int(paper_cfg.get("random_state", 42)),
                xgb_params=paper_cfg.get("xgb_params"),
                extra_trees_params=paper_cfg.get("extra_trees_params"),
                pca_variance=float(paper_cfg.get("pca_variance", 0.999)),
                pca_max_components=paper_cfg.get("pca_max_components"),
                candidate_models=paper_cfg.get("candidate_models"),
            ).run(
                ds_path,
                target_scope=paper_cfg.get("target_scope", "bh_only"),
                min_samples=effective_min,
                test_size=float(paper_cfg.get("test_size", 0.25)),
                n_splits=effective_n_splits,
                preset_id=paper_cfg.get("preset_id"),
                preset_label=paper_cfg.get("preset_label"),
            )
            state["results"]["paper_model_selection"] = paper_result
            state["results"]["paper_model_status"] = paper_result.get("status")
            state["results"]["selected_candidate_model"] = paper_result.get("selected_model")
            if paper_result.get("ranking"):
                best = paper_result["ranking"][0]
                state["results"]["cv_bh_rmse_T_mean"] = best.get("bh_rmse_T_mean")
                state["results"]["cv_bh_mae_T_mean"] = best.get("bh_mae_T_mean")
            if paper_result.get("status") == "ok":
                self._log(pid, f"候选模型选择完成: {paper_result.get('selected_model')}，CV B-H RMSE={state['results'].get('cv_bh_rmse_T_mean')}")
            else:
                self._log(pid, f"候选模型选择未生成正式结果: {paper_result.get('message', paper_result.get('status'))}")
        except Exception as exc:
            state["results"]["paper_model_status"] = "failed"
            state["results"]["paper_model_error"] = str(exc)
            self._log(pid, f"[提示] 候选模型多折评选需要更多样本（当前 {n_actual} 个），已跳过；仅保存 direct_xgb 探索模型。")
            self._log(pid, f"[detail] {exc}")

        state["progress_pct"] = 100
        state["status"] = "completed"
        self._persist(pid)
        if metrics.get("r2_avg") is None:
            self._log(pid, f"训练完成（探索模型），训练集 R2={metrics.get('train_r2_avg', 0):.4f}，模型 ID={metrics['model_id']}")
        else:
            self._log(pid, f"训练完成，验证 R2={metrics['r2_avg']:.4f}，模型 ID={metrics['model_id']}")

    def _run(self, pid: str, config: dict):
        state = self._state(pid)
        self._prevent_sleep()
        angle_mode = config.get("angle_mode", "motor")
        script_angles = FULL_ANGLES if angle_mode == "full" else MOTOR_ANGLES
        train_angles = MOTOR_ANGLES
        n_samples = int(config.get("n_samples", 20))
        msat = float(config.get("Msat", 1.56e6))
        source_pid = config.get("source_pipeline_id") or config.get("rerun_from_pipeline_id") or pid
        resume_from_results = bool(config.get("resume_from_results"))

        try:
            state["results"]["preset_id"] = config.get("preset_id") or config.get("preset")
            state["results"]["preset_label"] = config.get("preset_label")
            state["results"]["estimated_tasks"] = config.get("estimated_tasks")
            if resume_from_results:
                state["results"]["source_pipeline_id"] = source_pid
                self._log(pid, f"从已有仿真结果断点重跑: output/pipeline_{source_pid}")
            if config.get("preset_label"):
                self._log(pid, f"使用预设: {config['preset_label']}")
            if config.get("estimated_tasks"):
                est = config["estimated_tasks"]
                self._log(
                    pid,
                    f"预计 MuMax3 任务数: {est.get('mumax3_tasks')} "
                    f"({est.get('n_samples')} samples x {est.get('n_angles')} angles x "
                    f"{est.get('n_grains_est')} grains)",
                )

            if not resume_from_results:
                batch_ps1, batch_sh = self._run_generated_stages(pid, config, script_angles, n_samples)
                self._run_or_wait_simulation(pid, batch_ps1, batch_sh)
            else:
                self._set_stage(pid, "analyze")

            self._aggregate_and_train(pid, config, source_pid, train_angles, msat)
        except PipelineStopped as exc:
            state["status"] = "cancelled"
            state["error"] = str(exc)
            self._log(pid, "流水线已终止。")
        except Exception as exc:
            import traceback

            state["status"] = "failed"
            state["error"] = str(exc)
            self._persist(pid)
            self._log(pid, f"流水线失败: {exc}")
            self._log(pid, traceback.format_exc())
        finally:
            state["_process"] = None
            self._allow_sleep()
            self._persist(pid)

    def get_state(self, pid: str) -> dict | None:
        if pid not in self.pipelines:
            self._restore(pid)
        state = self.pipelines.get(pid)
        if state is None:
            return None
        return self._public_state(state)

    def resume_after_sim(self, pid: str) -> bool:
        if pid not in self.pipelines:
            self._restore(pid)
        state = self.pipelines.get(pid)
        if state and state.get("status") == "waiting_sim":
            state["_resume_event"].set()
            self._persist(pid)
            return True
        return False

    def terminate(self, pid: str) -> bool:
        if pid not in self.pipelines:
            self._restore(pid)
        state = self.pipelines.get(pid)
        if not state or state.get("status") in {"completed", "failed", "cancelled", "interrupted"}:
            return False
        state["status"] = "stopping"
        state["_stop_event"].set()
        state["_resume_event"].set()
        proc = state.get("_process")
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
            except Exception:
                pass
        self._log(pid, "已收到终止请求。")
        self._persist(pid)
        return True

    def rerun_from_checkpoint(self, pid: str) -> dict | None:
        src = self.get_state(pid)
        if not src:
            return None
        source_pid = src.get("source_pipeline_id") or pid
        config = copy.deepcopy(src.get("config") or {})
        output_dir = Path("output") / f"pipeline_{source_pid}"
        has_results = output_dir.is_dir() and any(output_dir.rglob("grain_*.txt"))
        if has_results:
            config["resume_from_results"] = True
            config["source_pipeline_id"] = source_pid
            mode = "from_results"
        else:
            config.pop("resume_from_results", None)
            config.pop("source_pipeline_id", None)
            mode = "full"
        config["rerun_from_pipeline_id"] = source_pid
        new_pid = self.start(config)
        return {"pipeline_id": new_pid, "mode": mode, "source_pipeline_id": source_pid}

    @staticmethod
    def _safe_remove(path: Path, root: Path, deleted: list[str]):
        try:
            resolved = path.resolve()
            resolved.relative_to(root.resolve())
        except Exception:
            return
        if resolved.is_dir():
            shutil.rmtree(resolved)
            deleted.append(str(path).replace("\\", "/") + "/")
        elif resolved.exists():
            resolved.unlink()
            deleted.append(str(path).replace("\\", "/"))

    def delete(self, pid: str, cascade: bool = True) -> dict | None:
        if pid not in self.pipelines:
            self._restore(pid)
        state = self.pipelines.get(pid)
        if state is None:
            return None
        if state.get("status") not in {"completed", "failed", "cancelled", "interrupted"}:
            self.terminate(pid)

        deleted: list[str] = []
        root = Path.cwd()
        if cascade:
            for path in [
                Path("preinput") / f"pipeline_{pid}",
                Path("grain_scripts") / f"pipeline_{pid}",
                Path("output") / f"pipeline_{pid}",
            ]:
                self._safe_remove(path, root, deleted)
            for ext in (".ps1", ".sh", ".bat"):
                self._safe_remove(Path("scripts") / f"run_pipeline_{pid}{ext}", root, deleted)

            results = state.get("results") or {}
            ds_path = results.get("dataset_path")
            if ds_path:
                p = Path(ds_path)
                try:
                    p.resolve().relative_to((root / "data" / "datasets").resolve())
                    self._safe_remove(p, root, deleted)
                    self._safe_remove(p.with_suffix(".metadata.json"), root, deleted)
                except Exception:
                    pass
            for p in Path("data/datasets").glob(f"*pipeline_{pid}*"):
                self._safe_remove(p, root, deleted)

            model_id = results.get("model_id")
            if model_id:
                self._safe_remove(Path("data/models") / model_id, root, deleted)
            paper = results.get("paper_model_selection") or {}
            artifact_dir = paper.get("artifact_dir")
            if artifact_dir:
                self._safe_remove(Path(artifact_dir), root, deleted)

        self._safe_remove(self._PERSIST_DIR / f"{pid}.json", root, deleted)
        self.pipelines.pop(pid, None)
        return {"deleted": deleted}

    def event_stream(self, pid: str):
        while True:
            state = self.get_state(pid)
            if state is None:
                yield f"data: {json.dumps({'error': '未找到流水线'}, ensure_ascii=False)}\n\n"
                break
            yield f"data: {json.dumps(state, ensure_ascii=False, default=str)}\n\n"
            if state.get("status") in {"completed", "failed", "cancelled", "interrupted"}:
                break
            time.sleep(1)

    def list_pipelines(self) -> list[dict]:
        self._load_all_persisted()
        items = [self.get_state(pid) for pid in self.pipelines]
        items = [item for item in items if item]
        return sorted(items, key=lambda x: x.get("updated_at") or x.get("created_at") or "", reverse=True)
