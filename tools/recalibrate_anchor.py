"""
tools/recalibrate_anchor.py
用真实流水线仿真数据更新锚点 δ(H) 校准。

Usage:
    python tools/recalibrate_anchor.py \
        --sim-csv  data/pipeline_run/B27R090_RD_aggregate.csv \
        --grade    B27R090 \
        --dir      RD

CSV 格式：两列，第一列 H (A/m)，第二列 B (T)，可含 # 注释行。

效果：替换 data/reference_anchors/B27R090_RD_delta.json 中的物理估算值，
      改为真实仿真结果，提高后续预测精度。

初始状态说明：
    若 data/reference_anchors/ 目录下无文件，首次调用 apply_reference_correction()
    会自动生成物理估算（Stoner-Wohlfarth 聚合模型），约 5 秒完成。
    本脚本在有真实流水线数据后替换这些估算值。
"""

import argparse
import sys
from pathlib import Path

# 允许从项目根目录或 tools/ 目录运行
_here = Path(__file__).resolve().parent
sys.path.insert(0, str(_here.parent / 'modules'))
sys.path.insert(0, str(_here.parent))


def load_csv(path: str):
    rows = []
    with open(path, encoding='utf-8') as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith('#'):
                continue
            parts = s.split(',')
            if len(parts) >= 2:
                try:
                    rows.append((float(parts[0]), float(parts[1])))
                except ValueError:
                    pass
    if not rows:
        raise ValueError(f'CSV 无有效数据: {path}')
    H, B = zip(*sorted(rows))
    return list(H), list(B)


def main():
    parser = argparse.ArgumentParser(
        description='用流水线仿真数据更新锚点参考修正 δ(H)')
    parser.add_argument('--sim-csv',  required=True,
                        help='锚点仿真聚合 B-H 曲线 CSV (H [A/m], B [T])')
    parser.add_argument('--grade',    required=True,
                        choices=['B23R075', 'B27R090', 'B27R095', 'B30P105'],
                        help='材料牌号（对应锚点 ODF 参数）')
    parser.add_argument('--dir',      default='RD', choices=['RD', 'TD'],
                        help='方向（默认 RD）')
    parser.add_argument('--preview',  action='store_true',
                        help='仅打印修正量预览，不写入文件')
    args = parser.parse_args()

    from reference_corrector import (
        update_anchor_from_simulation,
        load_reference_bh,
        _estimate_sim_bh,
        _H_GRID,
    )
    import numpy as np

    print(f'加载仿真数据: {args.sim_csv}')
    sim_H, sim_B = load_csv(args.sim_csv)
    print(f'  {len(sim_H)} 个数据点，H 范围: [{min(sim_H):.0f}, {max(sim_H):.0f}] A/m')

    # 加载参考数据预览
    H_ref, B_ref = load_reference_bh(args.grade, args.dir)
    ref_800 = float(np.interp(800, H_ref, B_ref))
    sim_800 = float(np.interp(800, sim_H, sim_B))
    print(f'B800 参考值 = {ref_800:.4f} T')
    print(f'B800 仿真值 = {sim_800:.4f} T')
    print(f'δ(800 A/m) = {ref_800 - sim_800:+.4f} T')

    if args.preview:
        print('[预览模式] 不写入文件。')
        return

    out = update_anchor_from_simulation(args.grade, args.dir, sim_H, sim_B)
    print(f'已更新校准文件: {out}')
    print('下次调用 predict_bh() 时将自动使用新校准数据。')


if __name__ == '__main__':
    main()
