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
