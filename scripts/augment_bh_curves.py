#!/usr/bin/env python3
"""
BH Curve Data Augmentation for GO Steel — Maxwell .amat format
============================================================
对GO硅钢BH曲线进行有界合成扰动。合成样本须与源材料同组划分，
不能作为独立实验、独立微磁样本或成分因果响应的验证证据。

核心设计原则（v2）
------------------
H 网格固定不变：不同批次的同牌号硅钢按相同 H 网格测量，
差异体现在各 H 点的 B 值，而非测量场强选取。固定 H 网格
可精确控制误差上界，并使相似度验证简化为逐点比较。

扰动层次（总误差 ≤ max_rel_error，默认 8%）
  Layer 1 — 全局 B 缩放 alpha_B（±max_rel_error/2）
    物理含义：Fe-Si 含量微变 → 饱和磁感应强度 Bsat 整体偏移
  Layer 2 — 过渡区光滑形状噪声（剩余误差预算）
    物理含义：晶粒取向完整度的统计分布 → "膝部"弯曲程度差异
    实现：log-H 空间钟形包络 × 平滑高斯随机噪声
  强制约束：B(0)=0, dB/dH>0（严格单调递增）, B≤2.15 T

Usage
-----
  # 每个材料生成30个增强样本（默认）
  python augment_bh_curves.py

  # 每个材料生成1个样本，进行验证
  python augment_bh_curves.py --n 1

  # 自定义参数
  python augment_bh_curves.py --n 50 --seed 7 --max_err 0.08 --noise 0.03
"""

import numpy as np
import re
import os
import csv
import json
import argparse
from scipy.interpolate import PchipInterpolator
from scipy.ndimage import gaussian_filter1d


# ─── 解析 / 格式化 ─────────────────────────────────────────────────────────────

def parse_points_block(content, comp_name):
    """从 .amat 内容中提取指定 component 的 H, B 数组。"""
    idx = content.find(comp_name)
    if idx == -1:
        return None, None
    chunk = content[idx: idx + 3000]
    m = re.search(r'Points\[(\d+):\s*([^\]]+)\]', chunk)
    if not m:
        return None, None
    vals = [float(x.strip()) for x in m.group(2).split(',')]
    return np.array(vals[0::2]), np.array(vals[1::2])


def replace_points_in_component(content, comp_name, H_new, B_new):
    """在 .amat 内容中替换指定 component 的 Points[...] 数据。"""
    n_vals = len(H_new) * 2
    pairs = ', '.join(f"{h:.7g}, {b:.7g}" for h, b in zip(H_new, B_new))
    new_str = f"Points[{n_vals}: {pairs}]"

    idx = content.find(comp_name)
    chunk_start = idx
    chunk_end = content.find('$end', idx) + 50
    chunk = content[chunk_start:chunk_end]
    new_chunk = re.sub(r'Points\[\d+:[^\]]+\]', new_str, chunk, count=1)
    return content[:chunk_start] + new_chunk + content[chunk_end:]


def replace_scalar_param(content, key, new_val_str):
    return re.sub(
        rf"('{re.escape(key)}'=')([^']+)(')",
        rf"\g<1>{new_val_str}\g<3>",
        content
    )


def parse_amat(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    m = re.search(r"\$begin '(\w+)'", content)
    name = m.group(1) if m else os.path.splitext(os.path.basename(filepath))[0]
    H_rd, B_rd = parse_points_block(content, 'component1')
    H_td, B_td = parse_points_block(content, 'component2')
    scalar_keys = ['conductivity', 'mass_density',
                   'core_loss_kh', 'core_loss_kc', 'core_loss_ke',
                   'core_loss_kdc', 'core_loss_equiv_cut_depth']
    params = {}
    for k in scalar_keys:
        m2 = re.search(rf"'{re.escape(k)}'='([^']+)'", content)
        params[k] = m2.group(1) if m2 else None
    return {'name': name, 'content': content,
            'H_rd': H_rd, 'B_rd': B_rd,
            'H_td': H_td, 'B_td': B_td,
            'params': params}


# ─── 物理扰动核心 ─────────────────────────────────────────────────────────────

def enforce_monotone(B, epsilon=1e-7):
    """强制 B 严格单调递增。"""
    B = B.copy()
    for i in range(1, len(B)):
        if B[i] <= B[i - 1]:
            B[i] = B[i - 1] + epsilon
    return B


def perturb_bh_curve(H_orig, B_orig, rng, is_RD=True,
                     max_rel_error=0.08, noise_frac=0.03):
    """
    对单条 BH 曲线施加物理约束扰动。

    H 网格保持不变。误差预算：
      - Layer 1（全局缩放）使用 ±max_rel_error/2
      - Layer 2（形状噪声）使用剩余预算的 noise_frac 比例

    保证每点相对误差 |B_aug - B_orig| / B_orig ≤ max_rel_error。

    返回: H_new (= H_orig), B_new, info_dict
    """
    assert B_orig[0] == 0.0 and H_orig[0] == 0.0
    n = len(H_orig)

    # ── Layer 1：全局 B 缩放 ─────────────────────────────────────────────
    half = max_rel_error / 2.0
    alpha_B = rng.uniform(1.0 - half, 1.0 + half)
    B_new = B_orig.copy()
    B_new[1:] = B_orig[1:] * alpha_B
    B_new[0] = 0.0

    # ── Layer 2：过渡区光滑形状噪声 ──────────────────────────────────────
    # 剩余预算 = max_rel_error - |alpha_B - 1|，噪声不超过此预算的 noise_frac
    remaining_budget = max_rel_error - abs(alpha_B - 1.0)
    amplitude = remaining_budget * noise_frac  # 保守使用预算

    if n >= 5 and amplitude > 1e-6:
        # log-H 空间钟形包络（RD 峰在前段，TD 峰在中段）
        log_h = np.log1p(H_orig)
        norm = (log_h - log_h[0]) / (log_h[-1] - log_h[0] + 1e-15)
        center = 0.30 if is_RD else 0.45
        bell = np.exp(-((norm - center) / 0.25) ** 2)
        bell[0] = 0.0        # 原点固定
        bell[-1] = 0.0       # 饱和端固定

        # 平滑相关噪声（物理上晶粒分布是连续变化的）
        sigma_smooth = max(1.5, n / 6.0)
        raw = rng.standard_normal(n)
        smooth = gaussian_filter1d(raw, sigma=sigma_smooth)
        peak = np.abs(smooth).max()
        if peak > 1e-10:
            smooth /= peak  # 归一化到 [-1, 1]

        # 噪声幅度 = amplitude * 局部 B 值（相对误差有界）
        noise = smooth * bell * amplitude * B_new
        noise[0] = 0.0
        B_new = B_new + noise
        B_new[0] = 0.0

    # ── 物理约束强制 ─────────────────────────────────────────────────────
    B_new = enforce_monotone(B_new)
    B_new = np.clip(B_new, 0.0, 2.15)
    B_new[0] = 0.0

    # ── 计算实际最大相对误差 ─────────────────────────────────────────────
    mask = B_orig > 1e-6
    rel_err = np.abs(B_new[mask] - B_orig[mask]) / B_orig[mask]
    actual_max_err = float(rel_err.max()) if len(rel_err) > 0 else 0.0
    actual_mean_err = float(rel_err.mean()) if len(rel_err) > 0 else 0.0

    return H_orig.copy(), B_new, {
        'alpha_B': float(alpha_B),
        'max_rel_error': actual_max_err,
        'mean_rel_error': actual_mean_err,
    }


def perturb_scalar_params(params, rng):
    """对材料标量参数施加微小扰动。"""
    new_params = dict(params)

    def _scale(val_str, rel):
        try:
            v = float(val_str)
            if v == 0.0:
                return val_str
            return f"{v * (1.0 + rng.uniform(-rel, rel)):.6e}"
        except (TypeError, ValueError):
            return val_str

    if new_params.get('conductivity'):
        new_params['conductivity'] = _scale(new_params['conductivity'], 0.03)
    if new_params.get('mass_density'):
        new_params['mass_density'] = _scale(new_params['mass_density'], 0.005)
    for k in ['core_loss_kh', 'core_loss_kc', 'core_loss_ke']:
        if new_params.get(k):
            new_params[k] = _scale(new_params[k], 0.12)
    cd = new_params.get('core_loss_equiv_cut_depth', '')
    if cd:
        m = re.match(r'([0-9.eE+\-]+)(.*)', cd)
        if m:
            v = float(m.group(1)) * (1.0 + rng.uniform(-0.08, 0.08))
            new_params['core_loss_equiv_cut_depth'] = f"{v:.5g}{m.group(2)}"
    return new_params


# ─── 相似度验证 ───────────────────────────────────────────────────────────────

def compute_similarity(B_orig, B_aug):
    """
    计算每点相对误差（H 网格相同，直接逐点比较）。
    返回: max_rel, mean_rel, per_point_rel_errors
    """
    mask = B_orig > 1e-6
    if not np.any(mask):
        return 0.0, 0.0, np.array([])
    rel = np.abs(B_aug[mask] - B_orig[mask]) / B_orig[mask]
    return float(rel.max()), float(rel.mean()), rel


# ─── 输出工具 ─────────────────────────────────────────────────────────────────

def write_amat(content, new_name, H_rd, B_rd, H_td, B_td, new_params, out_path):
    old_m = re.search(r"\$begin '(\w+)'", content)
    if old_m:
        old = old_m.group(1)
        content = content.replace(f"$begin '{old}'", f"$begin '{new_name}'", 1)
        content = content.replace(f"$end '{old}'",   f"$end '{new_name}'",   1)
        content = re.sub(r"(# Maxwell material library —\s*)\S+",
                         f"\\g<1>{new_name}", content, count=1)
    content = replace_points_in_component(content, 'component1', H_rd, B_rd)
    content = replace_points_in_component(content, 'component2', H_td, B_td)
    for k, v in new_params.items():
        if v is not None and k != 'core_loss_equiv_cut_depth':
            content = replace_scalar_param(content, k, v)
    cd = new_params.get('core_loss_equiv_cut_depth')
    if cd:
        content = replace_scalar_param(content, 'core_loss_equiv_cut_depth', cd)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(content)


# ─── 主流程 ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='GO钢BH曲线物理约束数据增强 v2')
    parser.add_argument('--n',       type=int,   default=30,
                        help='每个材料生成的增强样本数 (默认 30)')
    parser.add_argument('--seed',    type=int,   default=42,
                        help='随机种子 (默认 42)')
    parser.add_argument('--max_err', type=float, default=0.08,
                        help='允许的最大相对B误差 (默认 0.08 = 8%%)')
    parser.add_argument('--noise',   type=float, default=0.03,
                        help='形状噪声使用的剩余预算比例 (默认 0.03)')
    parser.add_argument('--out',     type=str,   default=None,
                        help='输出目录')
    parser.add_argument('--source', action='append', default=[],
                        help='源 .amat 路径，可重复；默认查找已有仿真导出')
    args = parser.parse_args()
    if args.n < 1 or not 0 <= args.max_err < 1 or not 0 <= args.noise <= 1:
        parser.error('Require n >= 1, 0 <= max_err < 1 and 0 <= noise <= 1')

    script_dir   = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(script_dir)
    exports_dir  = os.path.join(project_root, 'data', 'exports')
    out_dir      = args.out or os.path.join(exports_dir, 'augmented')

    source_files = args.source or [
        os.path.join(exports_dir, 'B23R075_sim.amat'),
        os.path.join(exports_dir, 'B23R080_sim.amat'),
        os.path.join(exports_dir, 'IEC_M120_35S_sim.amat'),
    ]
    source_files = [path for path in source_files if os.path.isfile(path)]
    if not source_files:
        parser.error('No source material found; pass --source <existing .amat>')
    os.makedirs(out_dir, exist_ok=True)

    rng = np.random.default_rng(args.seed)
    N   = args.n
    verify_mode = (N == 1)

    summary_rows = []
    meta_list    = []
    total        = 0
    # 全局误差统计
    all_max_errs = []

    for src_path in source_files:
        if not os.path.exists(src_path):
            print(f"[SKIP] not found: {src_path}")
            continue

        mat       = parse_amat(src_path)
        base_name = mat['name']
        print(f"\n{'─'*62}")
        print(f"Source: {base_name}")
        print(f"  RD: {len(mat['H_rd'])} pts, "
              f"H_max={mat['H_rd'][-1]:.1f} A/m, B_max={mat['B_rd'][-1]:.4f} T")
        print(f"  TD: {len(mat['H_td'])} pts, "
              f"H_max={mat['H_td'][-1]:.1f} A/m, B_max={mat['B_td'][-1]:.4f} T")

        mat_max_errs = []

        for i in range(1, N + 1):
            aug_name = f"{base_name}_aug{i:03d}"

            H_rd_new, B_rd_new, info_rd = perturb_bh_curve(
                mat['H_rd'], mat['B_rd'], rng,
                is_RD=True, max_rel_error=args.max_err, noise_frac=args.noise)
            H_td_new, B_td_new, info_td = perturb_bh_curve(
                mat['H_td'], mat['B_td'], rng,
                is_RD=False, max_rel_error=args.max_err, noise_frac=args.noise)

            new_params = perturb_scalar_params(mat['params'], rng)

            out_file = os.path.join(out_dir, f"{aug_name}.amat")
            write_amat(mat['content'], aug_name,
                       H_rd_new, B_rd_new, H_td_new, B_td_new,
                       new_params, out_file)

            # 相似度：H网格相同，直接比较
            rd_max, rd_mean, _ = compute_similarity(mat['B_rd'], B_rd_new)
            td_max, td_mean, _ = compute_similarity(mat['B_td'], B_td_new)
            sample_max_err = max(rd_max, td_max)
            mat_max_errs.append(sample_max_err)
            all_max_errs.append(sample_max_err)

            # 验证模式：详细打印
            if verify_mode:
                within = sample_max_err <= args.max_err
                tag = "[PASS]" if within else "[FAIL]"
                print(f"\n  {tag} {aug_name}")
                print(f"    RD alpha_B={info_rd['alpha_B']:.4f}  "
                      f"max_rel={rd_max:.2%}  mean_rel={rd_mean:.2%}")
                print(f"    TD alpha_B={info_td['alpha_B']:.4f}  "
                      f"max_rel={td_max:.2%}  mean_rel={td_mean:.2%}")
                print(f"    Overall max_rel={sample_max_err:.2%}  "
                      f"(limit={args.max_err:.0%})")

                # 打印逐点对比（非零点）
                print(f"\n    --- RD point-by-point comparison ---")
                print(f"    {'H (A/m)':>12}  {'B_orig (T)':>10}  "
                      f"{'B_aug (T)':>10}  {'rel_err':>8}")
                for h, bo, ba in zip(mat['H_rd'], mat['B_rd'], B_rd_new):
                    if bo > 0:
                        err = abs(ba - bo) / bo
                        print(f"    {h:>12.4g}  {bo:>10.5f}  {ba:>10.5f}  {err:>8.2%}")
                    else:
                        print(f"    {h:>12.4g}  {bo:>10.5f}  {ba:>10.5f}  {'---':>8}")

                print(f"\n    --- TD point-by-point comparison (first 10 pts) ---")
                print(f"    {'H (A/m)':>12}  {'B_orig (T)':>10}  "
                      f"{'B_aug (T)':>10}  {'rel_err':>8}")
                for h, bo, ba in zip(mat['H_td'][:10], mat['B_td'][:10], B_td_new[:10]):
                    if bo > 0:
                        err = abs(ba - bo) / bo
                        print(f"    {h:>12.4g}  {bo:>10.5f}  {ba:>10.5f}  {err:>8.2%}")
                    else:
                        print(f"    {h:>12.4g}  {bo:>10.5f}  {ba:>10.5f}  {'---':>8}")

            # 元数据
            row = {
                'dataset_role': 'synthetic_augmentation',
                'group_id': base_name,
                'evidence_status': 'assumed_perturbation_not_independent_measurement',
                'aug_name':    aug_name,
                'source':      base_name,
                'index':       i,
                'alpha_B_RD':  round(info_rd['alpha_B'], 6),
                'alpha_B_TD':  round(info_td['alpha_B'], 6),
                'max_rel_err_RD':  round(rd_max,  6),
                'max_rel_err_TD':  round(td_max,  6),
                'max_rel_err_all': round(sample_max_err, 6),
                'within_10pct':    sample_max_err <= 0.10,
                'RD_H_pts':    list(H_rd_new.round(6)),
                'RD_B_pts':    list(B_rd_new.round(7)),
                'TD_H_pts':    list(H_td_new.round(6)),
                'TD_B_pts':    list(B_td_new.round(7)),
                'conductivity':  new_params.get('conductivity', ''),
                'mass_density':  new_params.get('mass_density', ''),
                'core_loss_kh':  new_params.get('core_loss_kh', ''),
                'core_loss_kc':  new_params.get('core_loss_kc', ''),
                'core_loss_ke':  new_params.get('core_loss_ke', ''),
            }
            meta_list.append(row)

            # CSV 展开行
            for h, bo, ba in zip(H_rd_new, mat['B_rd'], B_rd_new):
                summary_rows.append([aug_name, base_name, 'RD',
                                     round(h, 4), round(bo, 5), round(ba, 5),
                                     round(info_rd['alpha_B'], 4), round(rd_max, 4)])
            for h, bo, ba in zip(H_td_new, mat['B_td'], B_td_new):
                summary_rows.append([aug_name, base_name, 'TD',
                                     round(h, 4), round(bo, 5), round(ba, 5),
                                     round(info_td['alpha_B'], 4), round(td_max, 4)])
            total += 1

        if not verify_mode:
            arr = np.array(mat_max_errs)
            print(f"  Generated {N} samples  |  "
                  f"max_rel_err: min={arr.min():.2%} mean={arr.mean():.2%} "
                  f"max={arr.max():.2%}  all<10%={np.all(arr<0.10)}")

    # ── 写 CSV ─────────────────────────────────────────────────────────────
    csv_path = os.path.join(out_dir, 'augmented_bh_data.csv')
    with open(csv_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['aug_name', 'source_material', 'direction',
                         'H_Am', 'B_orig_T', 'B_aug_T', 'alpha_B', 'max_rel_err'])
        writer.writerows(summary_rows)

    # ── 写 JSON 元数据 ────────────────────────────────────────────────────
    json_path = os.path.join(out_dir, 'augmented_metadata.json')
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(meta_list, f, ensure_ascii=False, indent=2)

    # ── 全局统计 ──────────────────────────────────────────────────────────
    arr_all = np.array(all_max_errs)
    n_pass = int(np.sum(arr_all <= 0.10))
    print(f"\n{'='*62}")
    print(f"Done. Generated {total} samples  |  output: {out_dir}")
    print(f"Similarity vs. original (max relative B error at same H pts):")
    print(f"  min={arr_all.min():.2%}  mean={arr_all.mean():.2%}  "
          f"max={arr_all.max():.2%}  within_10%={n_pass}/{total}")
    print(f"  CSV:  {csv_path}")
    print(f"  JSON: {json_path}")

    if arr_all.max() > 0.10:
        print(f"\n[WARNING] {total - n_pass} sample(s) exceed 10% limit. "
              f"Try reducing --max_err.")


if __name__ == '__main__':
    main()
