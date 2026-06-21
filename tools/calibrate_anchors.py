"""
tools/calibrate_anchors.py
锚点 δ(H) 全自动校准程序

为 4 个锚点牌号生成 ODF 织构文件、MX3 仿真脚本、MuMax3 批处理脚本，
聚合晶粒结果并更新 data/reference_anchors/{grade}_RD_delta.json。

用法（在项目根目录运行）：
    # 步骤 1-3：生成文件、批处理脚本（不实际运行仿真）
    python tools/calibrate_anchors.py

    # 等待 MuMax3 仿真完成后（批处理脚本见 tools/mumax_anchors.ps1）：
    python tools/calibrate_anchors.py --from-step 4

    # 预览，不执行任何文件操作：
    python tools/calibrate_anchors.py --preview

步骤说明：
    1  生成 ODF 织构文件         → preinput/anchors/
    2  生成 MX3 仿真脚本         → grain_scripts/anchors/
    3  生成 MuMax3 批处理脚本    → tools/mumax_anchors.ps1 + mumax_anchors.sh
       ─── 等待用户手动运行 MuMax3 ───
    4  聚合晶粒结果              → data/sim_bh/{grade}_RD.csv
    5  更新 δ(H) 锚点 JSON       → data/reference_anchors/{grade}_RD_delta.json
    6  打印校验比对表

注意：首次使用（Bootstrap）状态下，δ(H) 已由 Stoner-Wohlfarth 物理模型估算。
      运行本脚本并使用真实 MuMax3 数据（100 颗不同取向晶粒）替换后，精度会提升。
      中间测试用少量晶粒可以验证流程，但不应写入生产 δ 文件。
"""

import sys
import os
import io
import json
import contextlib
import logging
import argparse
from datetime import datetime
from pathlib import Path

import numpy as np

# ─────────────────────────────────────────────────────────────────────────────
# 路径配置（全部自动推算，无硬编码）
# ─────────────────────────────────────────────────────────────────────────────
PROJECT_DIR = Path(__file__).resolve().parent.parent   # tools/../  = 项目根目录
MODULES_DIR = PROJECT_DIR / 'modules'
TOOLS_DIR   = PROJECT_DIR / 'tools'

STATE_FILE  = PROJECT_DIR / 'data' / '.calib_state.json'
LOG_FILE    = PROJECT_DIR / 'data' / 'calibration.log'
SIM_BH_DIR  = PROJECT_DIR / 'data' / 'sim_bh'
BATCH_PS1   = TOOLS_DIR / 'mumax_anchors.ps1'
BATCH_SH    = TOOLS_DIR / 'mumax_anchors.sh'

# 项目内相对路径（os.chdir(PROJECT_DIR) 后使用）
ODF_DIR  = 'preinput/anchors'
MX3_DIR  = 'grain_scripts/anchors'
OUT_DIR  = 'output/anchors'
RUN_NAME = 'anchors'

# ─────────────────────────────────────────────────────────────────────────────
# 锚点参数（与 reference_corrector.py 中的 ANCHOR_ODF 对应）
# ─────────────────────────────────────────────────────────────────────────────
ANCHOR_CONFIGS = {
    'B23R075': {'f_Goss': 0.92, 'theta_0': 3.0,  'halfwidth': 6.0,  'N_grains': 50},
    'B27R090': {'f_Goss': 0.82, 'theta_0': 6.0,  'halfwidth': 8.0,  'N_grains': 50},
    'B27R095': {'f_Goss': 0.70, 'theta_0': 9.0,  'halfwidth': 10.0, 'N_grains': 50},
    'B30P105': {'f_Goss': 0.65, 'theta_0': 11.0, 'halfwidth': 11.0, 'N_grains': 50},
}

MSAT              = 1.56e6
STANDARD_H_POINTS = [100, 200, 500, 1000, 2000, 3000, 5000, 7500]


# ─────────────────────────────────────────────────────────────────────────────
# 辅助函数
# ─────────────────────────────────────────────────────────────────────────────
def _short_name(grade: str) -> str:
    cfg = ANCHOR_CONFIGS[grade]
    return f"F{int(cfg['f_Goss']*100)}_T{int(cfg['theta_0'])}_N{cfg['N_grains']}"


def _odf_filename(grade: str) -> str:
    cfg = ANCHOR_CONFIGS[grade]
    return (
        f"grain_orientations_ODF_fGoss{cfg['f_Goss']:.2f}"
        f"_theta{int(cfg['theta_0'])}"
        f"_hw{int(cfg['halfwidth'])}"
        f"_N{cfg['N_grains']}.txt"
    )


def _read_sim_csv(csv_path: str) -> tuple:
    H, B = [], []
    with open(csv_path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or line.startswith('H_Am'):
                continue
            parts = line.split(',')
            if len(parts) == 2:
                H.append(float(parts[0]))
                B.append(float(parts[1]))
    return H, B


# ─────────────────────────────────────────────────────────────────────────────
# 日志设置
# ─────────────────────────────────────────────────────────────────────────────
def setup_logging():
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    fmt = '%(asctime)s [%(levelname)s] %(message)s'
    logging.basicConfig(
        level=logging.INFO,
        format=fmt,
        handlers=[
            logging.FileHandler(str(LOG_FILE), encoding='utf-8', mode='a'),
            logging.StreamHandler(sys.stdout),
        ],
    )


log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# 状态管理
# ─────────────────────────────────────────────────────────────────────────────
def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding='utf-8'))
        except Exception:
            pass
    return {
        'completed_steps': [],
        'odf_files':   {},
        'mx3_dirs':    {},
        'sim_bh_csvs': {},
        'old_delta':   {},
    }


def save_state(state: dict):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(
        json.dumps(state, indent=2, ensure_ascii=False), encoding='utf-8'
    )


# ─────────────────────────────────────────────────────────────────────────────
# 步骤 1：生成 ODF 织构文件
# ─────────────────────────────────────────────────────────────────────────────
def step1_generate_odf(state: dict, preview: bool) -> dict:
    log.info('=' * 64)
    log.info('Step 1  Generate ODF texture files')
    log.info('=' * 64)

    import odf_texture as odf

    Path(ODF_DIR).mkdir(parents=True, exist_ok=True)

    for grade, cfg in ANCHOR_CONFIGS.items():
        odf_path = f'{ODF_DIR}/{_odf_filename(grade)}'
        log.info(
            f'  {grade}:  f={cfg["f_Goss"]:.2f}  '
            f'theta0={cfg["theta_0"]:.0f}deg  hw={cfg["halfwidth"]:.0f}deg  '
            f'N={cfg["N_grains"]}'
        )
        log.info(f'          -> {odf_path}')
        state['odf_files'][grade] = odf_path

        if preview:
            continue
        if Path(odf_path).exists():
            log.info('          already exists, skipping')
            continue

        with contextlib.redirect_stdout(io.StringIO()):
            odf.generate_texture_with_odf(
                f_Goss=cfg['f_Goss'],
                theta_0=cfg['theta_0'],
                N_grains=cfg['N_grains'],
                halfwidth=cfg['halfwidth'],
                sampling_method='importance',
                plot_odf=False,
                output_dir=ODF_DIR,
            )

        if not Path(odf_path).exists():
            log.error(f'          generation failed')
        else:
            log.info(f'          done')

    if not preview:
        state['completed_steps'] = sorted(set(state['completed_steps']) | {1})
        save_state(state)
    return state


# ─────────────────────────────────────────────────────────────────────────────
# 步骤 2：生成 MX3 仿真脚本
# ─────────────────────────────────────────────────────────────────────────────
def step2_generate_mx3(state: dict, preview: bool) -> dict:
    log.info('=' * 64)
    log.info('Step 2  Generate MX3 simulation scripts (RD direction, angle=0)')
    log.info('=' * 64)

    import mx3_generator as gis

    Path(MX3_DIR).mkdir(parents=True, exist_ok=True)

    for grade in ANCHOR_CONFIGS:
        odf_path = state['odf_files'].get(grade, f'{ODF_DIR}/{_odf_filename(grade)}')
        mx3_out  = f'{MX3_DIR}/{_short_name(grade)}'
        log.info(f'  {grade}:  {odf_path}')
        log.info(f'          -> {mx3_out}/')
        state['mx3_dirs'][grade] = mx3_out

        if preview:
            continue
        if not Path(odf_path).exists():
            log.error(f'          ODF file missing, skipping')
            continue

        existing = list(Path(mx3_out).glob('angle_000/grain_*.mx3')) if Path(mx3_out).exists() else []
        if existing:
            log.info(f'          {len(existing)} MX3 files exist, skipping')
            continue

        with contextlib.redirect_stdout(io.StringIO()):
            out_path = gis.generate_scripts_for_config(
                texture_file=odf_path,
                angles=[0],
                output_dir=MX3_DIR,
            )
        log.info(f'          script dir: {out_path}')

    if not preview:
        state['completed_steps'] = sorted(set(state['completed_steps']) | {2})
        save_state(state)
    return state


# ─────────────────────────────────────────────────────────────────────────────
# 步骤 3：生成 MuMax3 批处理脚本
# ─────────────────────────────────────────────────────────────────────────────
def step3_generate_batch(state: dict, preview: bool) -> dict:
    log.info('=' * 64)
    log.info('Step 3  Generate MuMax3 batch scripts')
    log.info('=' * 64)

    import mx3_generator as gis
    import batch_scheduler as gpb

    if preview:
        log.info(f'  [PREVIEW] Would scan {MX3_DIR}/ and write batch scripts')
        log.info(f'  [PREVIEW] Windows: {BATCH_PS1}')
        log.info(f'  [PREVIEW] Linux:   {BATCH_SH}')
        return state

    configs_raw = gis.get_configs_in_dir(MX3_DIR)
    if not configs_raw:
        log.error(f'  No configs in {MX3_DIR}/, complete step 2 first')
        return state

    configs_fixed = [
        (cname, {**cdata, 'output_name': Path(cname.replace('\\', '/')).name}, angles)
        for cname, cdata, angles in configs_raw
    ]

    log.info(f'  Found {len(configs_fixed)} configs:')
    for cname, cdata, angles in configs_fixed:
        log.info(f'    {cdata["output_name"]}  ->  output/{RUN_NAME}/{cdata["output_name"]}/angle_000/')

    TOOLS_DIR.mkdir(parents=True, exist_ok=True)
    with contextlib.redirect_stdout(io.StringIO()):
        gpb.generate_multi_config_powershell_script(
            configs_fixed, str(BATCH_PS1), run_name=RUN_NAME
        )
        gpb.generate_multi_config_bash_script(
            configs_fixed, str(BATCH_SH), run_name=RUN_NAME
        )

    state['completed_steps'] = sorted(set(state['completed_steps']) | {3})
    save_state(state)

    log.info('')
    log.info('  Batch scripts ready. To run MuMax3 (from project root):')
    log.info(f'    Windows:  & "{BATCH_PS1}"')
    log.info(f'    Linux:    bash "{BATCH_SH}"')
    log.info('')
    log.info('  After MuMax3 finishes, run:')
    log.info(f'    python tools/calibrate_anchors.py --from-step 4')
    return state


# ─────────────────────────────────────────────────────────────────────────────
# 步骤 4：聚合晶粒仿真结果 → CSV
# ─────────────────────────────────────────────────────────────────────────────
def step4_aggregate(state: dict, preview: bool) -> dict:
    log.info('=' * 64)
    log.info('Step 4  Aggregate grain simulation results -> CSV')
    log.info('=' * 64)

    from dataset_builder import _extract_bh_one_angle
    from go_steel_reference import get_reference_hc

    SIM_BH_DIR.mkdir(parents=True, exist_ok=True)

    for grade in ANCHOR_CONFIGS:
        short     = _short_name(grade)
        angle_dir = str(Path(OUT_DIR) / short / 'angle_000')
        csv_path  = str(SIM_BH_DIR / f'{grade}_RD.csv')

        log.info(f'  {grade}  ({short})')
        log.info(f'    scan: {angle_dir}')

        state['sim_bh_csvs'][grade] = csv_path

        if preview:
            continue

        if not Path(angle_dir).exists():
            log.error(f'    directory missing - MuMax3 not complete?')
            continue

        grain_files = list(Path(angle_dir).glob('grain_*.txt'))
        if not grain_files:
            log.error(f'    no grain_*.txt files found')
            continue

        log.info(f'    found {len(grain_files)} grain files')

        ref_hc = get_reference_hc(grade=grade)
        result  = _extract_bh_one_angle(angle_dir, Msat=MSAT, reference_hc=ref_hc)
        if result is None:
            log.error(f'    aggregation failed (no valid grains)')
            continue

        B_vals  = result['B_at_std_H']
        n_valid = result['n_grains_valid']
        log.info(f'    valid grains: {n_valid}/{len(grain_files)}')
        log.info(f'    B @ 1000 A/m = {B_vals[STANDARD_H_POINTS.index(1000)]:.4f} T')

        with open(csv_path, 'w', encoding='utf-8', newline='') as f:
            f.write('# Anchor simulation aggregate\n')
            f.write(f'# Grade: {grade}  Direction: RD  n_valid: {n_valid}\n')
            f.write(f'# Generated: {datetime.now().isoformat()}\n')
            f.write('H_Am,B_T\n')
            for h, b in zip(STANDARD_H_POINTS, B_vals):
                f.write(f'{h},{b:.6f}\n')

        log.info(f'    -> {csv_path}')

    if not preview:
        state['completed_steps'] = sorted(set(state['completed_steps']) | {4})
        save_state(state)
    return state


# ─────────────────────────────────────────────────────────────────────────────
# 步骤 5：更新 δ(H) 锚点 JSON
# ─────────────────────────────────────────────────────────────────────────────
def step5_update_anchors(state: dict, preview: bool) -> dict:
    log.info('=' * 64)
    log.info('Step 5  Update delta(H) anchor JSON (real sim -> replaces SW estimate)')
    log.info('=' * 64)

    from reference_corrector import update_anchor_from_simulation, load_anchor_delta

    for grade in ANCHOR_CONFIGS:
        try:
            old_data = load_anchor_delta(grade, 'RD')
            if old_data:
                state['old_delta'][grade] = {
                    'H':     old_data['H_grid'],
                    'delta': old_data['delta_B'],
                }
        except Exception:
            pass

    for grade in ANCHOR_CONFIGS:
        csv_path = state['sim_bh_csvs'].get(grade)
        if not csv_path or not Path(csv_path).exists():
            log.error(f'  {grade}: no aggregate CSV, complete step 4 first')
            continue

        H_sim, B_sim = _read_sim_csv(csv_path)
        if len(H_sim) < 5:
            log.error(f'  {grade}: insufficient CSV data points ({len(H_sim)} < 5)')
            continue

        log.info(f'  {grade}:  H in [{H_sim[0]:.0f}, {H_sim[-1]:.0f}] A/m  ({len(H_sim)} pts)')

        if preview:
            log.info(f'          [PREVIEW] would call update_anchor_from_simulation("{grade}", "RD", ...)')
            continue

        try:
            saved_path = update_anchor_from_simulation(grade, 'RD', H_sim, B_sim)
            log.info(f'          -> updated: {saved_path}')
        except Exception as e:
            log.error(f'          update failed: {e}')

    if not preview:
        state['completed_steps'] = sorted(set(state['completed_steps']) | {5})
        save_state(state)
    return state


# ─────────────────────────────────────────────────────────────────────────────
# 步骤 6：打印校验比对表
# ─────────────────────────────────────────────────────────────────────────────
def step6_verify(state: dict) -> None:
    log.info('=' * 64)
    log.info('Step 6  Verification comparison table')
    log.info('=' * 64)

    try:
        from reference_corrector import load_reference_bh
    except ImportError:
        log.error('  Cannot import reference_corrector')
        return

    CHECK_H = [200, 500, 1000, 2000, 5000]

    print()
    print('=' * 84)
    print(f"{'Grade':<10} {'H(A/m)':<8} {'B_ref(T)':<11} {'B_sim(T)':<11} "
          f"{'d_before(T)':<13} {'d_after(T)':<12} {'change'}")
    print('-' * 84)

    for grade in ANCHOR_CONFIGS:
        csv_path = state['sim_bh_csvs'].get(grade)
        if not csv_path or not Path(csv_path).exists():
            print(f'  {grade}: no sim CSV')
            continue

        try:
            H_ref, B_ref = load_reference_bh(grade, 'RD')
        except Exception as e:
            print(f'  {grade}: reference curve error: {e}')
            continue

        H_sim, B_sim = _read_sim_csv(csv_path)
        H_sim = np.array(H_sim)
        B_sim = np.array(B_sim)

        old_snap = state.get('old_delta', {}).get(grade)
        H_old = np.array(old_snap['H']) if old_snap else None
        D_old = np.array(old_snap['delta']) if old_snap else None

        first = True
        for h in CHECK_H:
            b_ref_h = float(np.interp(h, H_ref, B_ref))
            b_sim_h = float(np.interp(h, H_sim, B_sim))
            d_after = b_ref_h - b_sim_h

            if H_old is not None:
                d_before = float(np.interp(h, H_old, D_old))
                change   = d_after - d_before
                d_before_s = f'{d_before:+.4f}'
                change_s   = f'{change:+.4f}'
            else:
                d_before_s = '  N/A  '
                change_s   = '  N/A'

            grade_col = grade if first else ''
            first = False
            print(
                f'{grade_col:<10} {h:<8.0f} {b_ref_h:<11.4f} {b_sim_h:<11.4f} '
                f'{d_before_s:<13} {d_after:+.4f}      {change_s}'
            )

        print('-' * 84)

    print()
    delta_dir = PROJECT_DIR / 'data' / 'reference_anchors'
    print(f'delta(H) JSON: {delta_dir}')
    print(f'Log:           {LOG_FILE}')
    print()


# ─────────────────────────────────────────────────────────────────────────────
# 主程序
# ─────────────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description='Anchor delta(H) automatic calibration tool',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        '--from-step', type=int, default=1, metavar='N',
        help='Resume from step N (skip earlier steps). Use --from-step 4 after MuMax3.'
    )
    parser.add_argument(
        '--preview', action='store_true',
        help='Preview mode: print plan but perform no file operations'
    )
    args = parser.parse_args()

    setup_logging()

    log.info('')
    log.info('Anchor delta(H) calibration  --  %s', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    log.info('Project: %s', PROJECT_DIR)
    if args.preview:
        log.info('[PREVIEW] No file operations will be performed')

    os.chdir(str(PROJECT_DIR))
    sys.path.insert(0, str(MODULES_DIR))

    state = load_state()
    from_step = args.from_step
    if from_step > 1:
        log.info('Resuming from step %d  (completed: %s)', from_step, state['completed_steps'])

    try:
        if from_step <= 1:
            state = step1_generate_odf(state, args.preview)
        if from_step <= 2:
            state = step2_generate_mx3(state, args.preview)
        if from_step <= 3:
            state = step3_generate_batch(state, args.preview)
            if not args.preview:
                log.info('Step 3 complete. Run MuMax3 then: python tools/calibrate_anchors.py --from-step 4')
                return
        if from_step <= 4:
            state = step4_aggregate(state, args.preview)
        if from_step <= 5:
            state = step5_update_anchors(state, args.preview)
        step6_verify(state)

    except KeyboardInterrupt:
        log.info('Interrupted by user')
        sys.exit(1)
    except Exception as e:
        log.exception('Error: %s', e)
        sys.exit(1)


if __name__ == '__main__':
    main()
