"""
pipeline_runner.py
自动流水线编排：LHS 织构采样 → MX3 脚本 → 批处理脚本 → 等待仿真 → 聚合数据集 → 训练 XGBoost。
通过 SSE（Server-Sent Events）向前端推送进度。
"""
import json
import copy
import time
import uuid
import threading
import contextlib
import io
import sys
import platform
import subprocess
from datetime import datetime
from pathlib import Path

import mx3_generator as gis
import batch_scheduler as gpb
from dataset_builder import DatasetBuilder, MOTOR_ANGLES, FULL_ANGLES
from ml_trainer import BHPredictor, MIN_RELIABLE_EVAL_SAMPLES

DEFAULT_FIXED_HALFWIDTH_DEG = 10.0

PIPELINE_PRESETS = {
    "smoke": {
        "label": "测试级别 / 冒烟测试",
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
        "label": "Lite / 轻量训练",
        "description": "轻量可用模型，会输出并展示验证指标。",
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
        "label": "Std / 标准训练",
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
        "label": "Max / 全量训练",
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
    """Estimate MuMax3 task count before a one-click pipeline starts."""
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
    base = {}
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
        try:
            print(entry)
        except UnicodeEncodeError:
            enc = getattr(sys.stdout, 'encoding', None) or 'utf-8'
            print(entry.encode(enc, errors='replace').decode(enc, errors='replace'))

    def _set_stage(self, pid: str, stage: str, pct: int = 0):
        s = self._state(pid)
        s['stage']       = stage
        s['stage_index'] = STAGES.index(stage)
        s['progress_pct'] = pct
        s['status']      = 'running' if stage != 'wait_sim' else 'waiting_sim'

    def start(self, config: dict) -> str:
        config = resolve_pipeline_config(config)
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
        script_angles = FULL_ANGLES if angle_mode == 'full' else MOTOR_ANGLES
        train_angles = MOTOR_ANGLES
        n_samples = int(config.get('n_samples', 20))
        Msat = float(config.get('Msat', 1.56e6))

        try:
            s['results']['preset_id'] = config.get('preset_id') or config.get('preset')
            s['results']['preset_label'] = config.get('preset_label')
            s['results']['estimated_tasks'] = config.get('estimated_tasks')
            if config.get('preset_label'):
                self._log(pid, f'使用预设: {config["preset_label"]}')
            if config.get('estimated_tasks'):
                est = config['estimated_tasks']
                self._log(pid, f'预计 MuMax3 任务数: {est.get("mumax3_tasks")} '
                               f'({est.get("n_samples")} samples x {est.get("n_angles")} angles x '
                               f'{est.get("n_grains_est")} grains)')
            # ── 阶段 0: 织构生成 ──────────────────────────────────
            self._set_stage(pid, 'texture_gen')
            self._log(pid, f'开始织构生成，样本数={n_samples}，角度模式={angle_mode}')
            self._log(pid, '当前工作流使用固定织构半宽 10 deg 以减少无效重复样本。')

            import importlib.util, sys, os
            tex_path = Path(__file__).resolve().parent / 'odf_texture.py'
            spec = importlib.util.spec_from_file_location(
                'odf_texture', str(tex_path))
            tex_mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(tex_mod)

            batch_dir = tex_mod.generate_batch_lhs(
                n_samples=n_samples,
                f_Goss_range=config.get('f_Goss_range', [0.4, 0.9]),
                theta_0_range=config.get('theta_0_range', [1, 30]),
                halfwidth_range=[DEFAULT_FIXED_HALFWIDTH_DEG, DEFAULT_FIXED_HALFWIDTH_DEG],
                N_grains_range=config.get('N_grains_range', [50, 150]),
                Si_content=config.get('Si_content', 3.0),
                output_dir=f'preinput/pipeline_{pid}'
            )
            s['results']['batch_dir'] = batch_dir
            self._log(pid, f'织构文件已保存至: {batch_dir}')
            s['progress_pct'] = 100

            # ── 阶段 1: MX3 脚本生成 ──────────────────────────────
            self._set_stage(pid, 'script_gen')
            gis.SimulationConfig.N_STEPS = max(4, int(config.get('sim_n_steps', gis.SimulationConfig.N_STEPS)))
            self._log(pid, f'仿真步数 N_STEPS={gis.SimulationConfig.N_STEPS}')
            if 'sim_h_max' in config:
                gis.SimulationConfig.H_MAX = float(config.get('sim_h_max'))
                self._log(pid, f'最大外场 H_MAX={gis.SimulationConfig.H_MAX:g} A/m')
            self._log(pid, f'生成 MX3 脚本，角度={script_angles}')
            txt_files = list(Path(batch_dir).glob('grain_orientations_ODF_*.txt'))
            for i, tf in enumerate(txt_files):
                with contextlib.redirect_stdout(io.StringIO()):
                    gis.generate_scripts_for_config(
                        str(tf), angles=script_angles,
                        output_dir=f'grain_scripts/pipeline_{pid}')
                s['progress_pct'] = int((i + 1) / len(txt_files) * 100)
            self._log(pid, f'MX3 脚本生成完成，共 {len(txt_files)} 个配置')

            # ── 阶段 2: 批处理脚本生成 ────────────────────────────
            self._set_stage(pid, 'batch_gen')
            self._log(pid, '生成批处理脚本 (.ps1 / .sh)')
            configs_for_gpb = gis.get_configs_in_dir(f'grain_scripts/pipeline_{pid}')
            Path('scripts').mkdir(exist_ok=True)
            batch_ps1 = f'scripts/run_pipeline_{pid}.ps1'
            batch_sh  = f'scripts/run_pipeline_{pid}.sh'
            with contextlib.redirect_stdout(io.StringIO()):
                gpb.generate_multi_config_powershell_script(configs_for_gpb, batch_ps1)
                gpb.generate_multi_config_bash_script(configs_for_gpb, batch_sh)
            s['results']['batch_script']    = batch_ps1
            s['results']['batch_script_sh'] = batch_sh
            self._log(pid, f'批处理脚本（Windows）: {batch_ps1}')
            self._log(pid, f'批处理脚本（Linux）:   {batch_sh}')
            s['progress_pct'] = 100

            # ── 阶段 3: 自动运行仿真（或回退到手动等待） ────────────
            self._set_stage(pid, 'wait_sim')

            # 检测 MuMax3 是否可用
            mumax3_available = False
            try:
                subprocess.run(['mumax3', '-v'], capture_output=True, timeout=12)
                mumax3_available = True
            except Exception:
                pass

            if mumax3_available:
                s['status'] = 'running_sim'
                is_win = platform.system() == 'Windows'
                sim_script = batch_ps1 if is_win else batch_sh
                cmd = (['powershell', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', sim_script]
                       if is_win else ['bash', sim_script])
                self._log(pid, f'检测到 MuMax3，自动执行仿真脚本: {sim_script}')
                try:
                    proc = subprocess.Popen(
                        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True, encoding='utf-8', errors='replace',
                    )
                    for line in proc.stdout:
                        stripped = line.rstrip()
                        if stripped:
                            self._log(pid, stripped)
                    proc.wait()
                    if proc.returncode != 0:
                        raise RuntimeError(f'仿真脚本退出码 {proc.returncode}，请检查 MuMax3 配置')
                    self._log(pid, f'仿真自动完成 (exit={proc.returncode})')
                except RuntimeError:
                    raise
                except Exception as exc:
                    self._log(pid, f'⚠ 自动仿真异常: {exc}，切换为手动等待')
                    s['status'] = 'waiting_sim'
                    ev.wait()
            else:
                s['status'] = 'waiting_sim'
                self._log(pid, '⚠ 未在 PATH 中检测到 mumax3')
                self._log(pid, f'  → 请手动在 GPU 机器上运行: {batch_ps1 if platform.system() == "Windows" else batch_sh}')
                self._log(pid, '  → 仿真完成后，点击 [仿真已完成，继续] 按钮')
                ev.wait()

            self._log(pid, '进入聚合分析阶段...')

            # ── 阶段 4: 结果聚合 ──────────────────────────────────
            self._set_stage(pid, 'analyze')
            self._log(pid, '开始聚合仿真结果...')
            builder = DatasetBuilder()
            pipeline_marker = f'pipeline_{pid}'
            pipeline_configs = [
                cfg for cfg in builder.scan_output_dir()
                if pipeline_marker in Path(cfg['config_path']).parts
            ]
            if not pipeline_configs:
                raise RuntimeError(f'未找到当前流水线 {pipeline_marker} 的仿真结果')

            def progress_cb(cur, tot, name):
                s['progress_pct'] = int(cur / max(tot, 1) * 100)
                self._log(pid, f'  聚合配置 ({cur}/{tot}): {name}')

            df = builder.build_dataset(
                config_paths=[cfg['config_path'] for cfg in pipeline_configs],
                target_angles=train_angles,
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
            if len(df) < MIN_RELIABLE_EVAL_SAMPLES:
                self._log(pid, f'样本数 {len(df)} 低于可信验证阈值 {MIN_RELIABLE_EVAL_SAMPLES}，本轮将标记为探索模型。')
            predictor = BHPredictor()
            metrics = predictor.train(
                ds_path,
                xgb_params=config.get('xgb_params'),
                test_size=float(config.get('test_size', 0.2)),
            )
            s['results']['model_id']  = metrics['model_id']
            s['results']['r2_avg']    = metrics['r2_avg']
            s['results']['mape_avg']  = metrics.get('mape_avg')
            s['results']['train_r2_avg'] = metrics.get('train_r2_avg')
            s['results']['train_mape_avg'] = metrics.get('train_mape_avg')
            s['results']['metric_reliability'] = metrics.get('metric_reliability')
            s['results']['metric_warnings'] = metrics.get('metric_warnings', [])
            n_actual = len(df)
            self._log(pid, f'开始候选模型交叉验证选择（{", ".join(config.get("candidate_models") or ["direct_xgb","extra_trees","pca_xgb","pca_extra_trees"])}）...')
            try:
                from paper_surrogate_trainer import PaperSurrogateTrainer, resolve_paper_training_config
                paper_cfg = resolve_paper_training_config({
                    'preset': config.get('paper_training_preset') or config.get('preset_id') or config.get('preset') or 'lite',
                    'candidate_models': config.get('candidate_models'),
                })
                # Cap min_samples so multi-model selection always runs on available data.
                # With n_actual samples we need at least 1 sample per fold in the test split;
                # use n_actual // 4 as the effective minimum (leaves 75% for training).
                effective_min = max(4, n_actual // 4)
                effective_n_splits = min(int(paper_cfg.get('n_splits', 3)), max(2, n_actual // 6))
                self._log(pid, f'  样本数={n_actual}，有效 min_samples={effective_min}，CV folds={effective_n_splits}')
                paper_result = PaperSurrogateTrainer(
                    output_dir=config.get('paper_model_dir', 'data/paper_models'),
                    random_state=int(paper_cfg.get('random_state', 42)),
                    xgb_params=paper_cfg.get('xgb_params'),
                    extra_trees_params=paper_cfg.get('extra_trees_params'),
                    pca_variance=float(paper_cfg.get('pca_variance', 0.999)),
                    pca_max_components=paper_cfg.get('pca_max_components'),
                    candidate_models=paper_cfg.get('candidate_models'),
                ).run(
                    ds_path,
                    target_scope=paper_cfg.get('target_scope', 'bh_only'),
                    min_samples=effective_min,
                    test_size=float(paper_cfg.get('test_size', 0.25)),
                    n_splits=effective_n_splits,
                    preset_id=paper_cfg.get('preset_id'),
                    preset_label=paper_cfg.get('preset_label'),
                )
                s['results']['paper_model_selection'] = paper_result
                s['results']['paper_model_status'] = paper_result.get('status')
                s['results']['selected_candidate_model'] = paper_result.get('selected_model')
                if paper_result.get('ranking'):
                    best = paper_result['ranking'][0]
                    s['results']['cv_bh_rmse_T_mean'] = best.get('bh_rmse_T_mean')
                    s['results']['cv_bh_mae_T_mean'] = best.get('bh_mae_T_mean')
                if paper_result.get('status') == 'ok':
                    self._log(pid, f'候选模型选择完成：{paper_result.get("selected_model")}，CV B-H RMSE={s["results"].get("cv_bh_rmse_T_mean")}')
                else:
                    self._log(pid, f'候选模型选择未生成正式结果：{paper_result.get("message", paper_result.get("status"))}')
            except Exception as exc:
                s['results']['paper_model_status'] = 'failed'
                s['results']['paper_model_error'] = str(exc)
                self._log(pid, f'候选模型选择失败，但快速预测模型已保存：{exc}')
            s['progress_pct'] = 100
            s['status'] = 'completed'
            if metrics.get('r2_avg') is None:
                self._log(pid, f'训练完成（探索模型），训练集R2={metrics.get("train_r2_avg", 0):.4f}，模型 ID={metrics["model_id"]}')
            else:
                self._log(pid, f'训练完成！验证R2={metrics["r2_avg"]:.4f}，模型 ID={metrics["model_id"]}')

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
