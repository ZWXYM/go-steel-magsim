"""
reference_corrector.py
材料基准映射校正 — 差分修正 + 锚点标定

核心公式 (material_mapping_strategy.md Step 3-5):
    B_corrected(H; ODF) = B_sim(H; ODF) + Δ(H; ODF)

    Δ(H; ODF) = Σ_k  w_k(ODF) × δ_k(H)
    δ_k(H)    = B_ref_k(H) - B_sim_k(H; ODF_anchor_k)   [锚点修正量]
    w_k(ODF)  = 1/(d_k + ε) / Σ 1/(d_j + ε)            [逆距离权重]

物理背景：
  - 仿真（Stoner-Wohlfarth 单磁畴）：非 Goss 晶粒被"锁定"在各自易轴方向，
    贡献 m_RD ≈ cos(θ)，导致聚合 B 偏低 ~0.1–0.3T（取决于织构强度）。
  - 实测：180° 畴壁运动使材料在 H >> Hc（≈5 A/m）时几乎完全饱和，
    B(800 A/m) 接近 μ₀·Msat。
  - 修正函数 δ(H) 正是弥补这一物理机制缺失。

初始校准来源（无真实仿真数据时）：
  - B_sim_anchor 由 Stoner-Wohlfarth 聚合模型解析估算（_estimate_sim_bh）
  - B_ref 来自 go_steel_data/output/*.csv（实测曲线数字化）
  - 首次调用时自动计算并缓存到 data/reference_anchors/<grade>_<dir>_delta.json

更新校准：
  运行真实流水线后执行：
    python tools/recalibrate_anchor.py --sim-csv <path> --grade B27R090 --dir RD
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

import numpy as np
from scipy.interpolate import CubicSpline

# ─── 物理常数 ─────────────────────────────────────────────────────────────────
_MU0  = 4.0e-7 * np.pi
_MSAT = 1.56e6       # A/m  Fe-3%Si
_KU1  = 3.6e4        # J/m³ Fe-3%Si [Moses 2012]
_HK   = 2.0 * _KU1 / (_MU0 * _MSAT)   # ≈ 36 728 A/m

# ─── 锚点 ODF 参数 ────────────────────────────────────────────────────────────
# 基于宝钢/JFE 产品手册及 IEC 60404-8-7 的典型织构估计
ANCHOR_ODF: dict[str, dict] = {
    'B23R075': {'f_Goss': 0.92, 'theta_mean_deg': 3.0,  'sigma_deg': 6.0 },
    'B27R090': {'f_Goss': 0.82, 'theta_mean_deg': 6.0,  'sigma_deg': 8.0 },
    'B27R095': {'f_Goss': 0.70, 'theta_mean_deg': 9.0,  'sigma_deg': 10.0},
    'B30P105': {'f_Goss': 0.65, 'theta_mean_deg': 11.0, 'sigma_deg': 11.0},
}

# H 公共网格 — 覆盖实测范围，对数均匀采样
_H_GRID = np.unique(np.concatenate([
    np.logspace(-1, np.log10(50000), 100),
    [10, 50, 100, 200, 500, 800, 1000, 2000, 3000, 5000, 7500, 10000, 20000, 50000],
])).astype(float)

# ─── ODF 距离 & 权重 ─────────────────────────────────────────────────────────
def odf_distance(p1: dict, p2: dict) -> float:
    """
    ODF 参数空间加权欧氏距离（material_mapping_strategy.md Step 5）。
    f_Goss 权重 ×4，角度权重 ×0.01（单位不同，避免量纲主导）。
    """
    df = (p1.get('f_Goss', 0.8)       - p2.get('f_Goss', 0.8))       ** 2 * 4.0
    dt = (p1.get('theta_mean_deg', 6)  - p2.get('theta_mean_deg', 6)) ** 2 * 0.01
    ds = (p1.get('sigma_deg', 8)       - p2.get('sigma_deg', 8))      ** 2 * 0.01
    return float(np.sqrt(df + dt + ds))


def _odf_from_params(params: dict) -> dict:
    """将 predict_bh 的 params 转为标准 ODF 字典。"""
    return {
        'f_Goss':        float(params.get('f_Goss', 0.82)),
        'theta_mean_deg':float(params.get('theta_0_deg', 6.0)),
        'sigma_deg':     float(params.get('halfwidth_deg', 8.0)),
    }


def anchor_weights(odf_params: dict) -> dict[str, float]:
    """多锚点逆距离权重，归一化到 1。"""
    dists = {g: odf_distance(odf_params, v) for g, v in ANCHOR_ODF.items()}
    inv   = {g: 1.0 / (d + 1e-4) for g, d in dists.items()}
    total = sum(inv.values())
    return {g: w / total for g, w in inv.items()}


def nearest_anchor(odf_params: dict) -> str:
    """返回最近锚点牌号。"""
    dists = {g: odf_distance(odf_params, v) for g, v in ANCHOR_ODF.items()}
    return min(dists, key=dists.get)


# ─── 参考数据加载 ─────────────────────────────────────────────────────────────
def _go_steel_dir() -> Path:
    here = Path(__file__).resolve().parent
    for p in [here.parent / 'go_steel_data' / 'output',
              here / 'go_steel_data' / 'output']:
        if p.exists():
            return p
    raise FileNotFoundError('go_steel_data/output/ 未找到。')


def load_reference_bh(grade: str, direction: str = 'RD') -> tuple[np.ndarray, np.ndarray]:
    """读取 go_steel_data/output/<grade>_<direction>.csv，返回 (H_arr, B_arr)。"""
    csv_path = _go_steel_dir() / f'{grade}_{direction}.csv'
    if not csv_path.exists():
        raise FileNotFoundError(f'参考数据不存在: {csv_path}')
    rows = []
    with open(csv_path, encoding='utf-8') as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith('#'):
                continue
            parts = s.split(',')
            if len(parts) == 2:
                try:
                    rows.append((float(parts[0]), float(parts[1])))
                except ValueError:
                    pass
    if not rows:
        raise ValueError(f'CSV 为空: {csv_path}')
    H, B = zip(*sorted(rows))
    return np.array(H, dtype=float), np.array(B, dtype=float)


# ─── Stoner-Wohlfarth 聚合仿真估算 ──────────────────────────────────────────
def _sw_aggregate_b(H_arr: np.ndarray,
                    theta_samples_deg: np.ndarray,
                    Msat: float = _MSAT,
                    Hk: float   = _HK) -> np.ndarray:
    """
    Stoner-Wohlfarth 上支路聚合 B(H)，向量化 Newton 迭代求解。

    初始化修正：原代码用 ψ=0 作为所有晶粒初始猜测，导致硬轴晶粒
    (θ≈90°) 在 H>>Hk 时收敛到能量极大点 (m_H≈−1)。
    修正方案：Newton 从 ψ=0 求解；对 H>Hk 的区间额外与高场解析近似
    m_H ≈ 1 − sin²(θ)·Hk/(2H) 做加权混合，强制物理收敛。
    """
    theta = np.radians(np.asarray(theta_samples_deg, dtype=float))  # (N_g,)
    H_val = np.asarray(H_arr, dtype=float)                          # (N_H,)
    H     = H_val[:, None]                                           # (N_H, 1)
    t     = theta[None, :]                                           # (1, N_g)

    # Newton 迭代（ψ=0 初始化，适用于 H < Hk 区间）
    psi = np.zeros((len(H_arr), len(theta_samples_deg)))
    for _ in range(20):
        f  = Hk / 2.0 * np.sin(2 * psi) - H * np.sin(psi - t)
        df = Hk * np.cos(2 * psi)        - H * np.cos(psi - t)
        df = np.where(np.abs(df) < 1e-9, 1e-9 * np.sign(df + 1e-30), df)
        psi = psi - f / df
        psi = np.clip(psi, -np.pi / 2.0, np.pi / 2.0)

    m_H_newton = np.cos(psi - t)   # (N_H, N_g)

    # 高场解析近似：H >> Hk 时所有晶粒趋向与外场对齐，m_H → 1
    # 领先阶修正：m_H ≈ 1 − sin²(θ)·Hk/(2H)
    H_norm    = np.maximum(H_val, 1e-9) / Hk              # (N_H,)
    sin2_t    = np.sin(theta) ** 2                         # (N_g,)
    m_H_high  = np.clip(1.0 - sin2_t[None, :] / (2.0 * H_norm[:, None]), 0.0, 1.0)

    # 混合权重：H < 0.2·Hk → 全用 Newton；H > 0.6·Hk → 全用解析
    # Newton 在 H >> Hk 对硬轴晶粒收敛至能量极大点，解析式更可靠
    blend = np.clip((H_norm - 0.2) / 0.4, 0.0, 1.0)[:, None]  # (N_H, 1)
    m_H = (1.0 - blend) * m_H_newton + blend * m_H_high

    B = _MU0 * (H_val + Msat * m_H.mean(axis=1))
    return np.clip(B, 0.0, _MU0 * (H_val + Msat))  # 物理上限


def _estimate_sim_bh(grade: str, H_grid: np.ndarray,
                     direction: str = 'RD',
                     n_grains: int = 300) -> np.ndarray:
    """
    物理估算锚点材料的仿真 B-H 上支路（无真实流水线数据时使用）。

    Goss 组分：Gaussian(θ₀, σ) 分布
    非 Goss 组分：均匀随机方向
    TD 方向：所有晶粒旋转 90°
    """
    params = ANCHOR_ODF[grade]
    f      = params['f_Goss']
    theta0 = params['theta_mean_deg']
    sigma  = params['sigma_deg']

    rng      = np.random.default_rng(0)
    n_goss   = max(1, int(n_grains * f))
    n_rand   = n_grains - n_goss

    theta_goss = rng.normal(theta0, sigma, n_goss)
    theta_rand = rng.uniform(0.0, 90.0, n_rand)
    theta_all  = np.concatenate([theta_goss, theta_rand])
    theta_all  = np.clip(np.abs(theta_all), 0.0, 90.0)

    if direction == 'TD':
        theta_all = np.clip(90.0 - theta_all, 0.0, 90.0)

    return _sw_aggregate_b(H_grid, theta_all)


# ─── 校准数据存储 ─────────────────────────────────────────────────────────────
def _anchor_dir() -> Path:
    here = Path(__file__).resolve().parent
    d    = here.parent / 'data' / 'reference_anchors'
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_anchor_delta(grade: str, direction: str,
                      H_grid: np.ndarray, delta_B: np.ndarray,
                      source: str = 'physics_estimate') -> str:
    """保存 δ(H) = B_ref - B_sim_anchor 到 JSON。"""
    path = _anchor_dir() / f'{grade}_{direction}_delta.json'
    path.write_text(json.dumps({
        'grade':      grade,
        'direction':  direction,
        'source':     source,
        'anchor_odf': ANCHOR_ODF.get(grade, {}),
        'H_grid':     H_grid.tolist(),
        'delta_B':    delta_B.tolist(),
        'note':       'delta_B = B_ref - B_sim_anchor; add to sim output to calibrate.',
    }, ensure_ascii=False, indent=2), encoding='utf-8')
    return str(path)


def load_anchor_delta(grade: str, direction: str = 'RD') -> Optional[dict]:
    """加载已保存的 δ(H) 校准数据，不存在则返回 None。"""
    path = _anchor_dir() / f'{grade}_{direction}_delta.json'
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding='utf-8'))


# ─── 全局 spline 缓存 ─────────────────────────────────────────────────────────
_delta_spline_cache: dict[str, CubicSpline] = {}


def _get_delta_spline(grade: str, direction: str = 'RD') -> CubicSpline:
    """
    获取 δ(H) 三次样条。优先从磁盘缓存加载；不存在时基于物理模型估算并缓存。
    """
    key = f'{grade}_{direction}'
    if key in _delta_spline_cache:
        return _delta_spline_cache[key]

    cal = load_anchor_delta(grade, direction)
    if cal:
        H_g = np.array(cal['H_grid'],  dtype=float)
        dB  = np.array(cal['delta_B'], dtype=float)
    else:
        print(f'[reference_corrector] 首次为 {grade}/{direction} 生成物理估算校准...', flush=True)
        H_g         = _H_GRID.copy()
        H_ref, B_ref = load_reference_bh(grade, direction)
        B_ref_i      = np.interp(H_g, H_ref, B_ref, left=0.0, right=float(B_ref[-1]))
        B_sim_i      = _estimate_sim_bh(grade, H_g, direction=direction)
        dB           = B_ref_i - B_sim_i
        save_anchor_delta(grade, direction, H_g, dB, source='physics_estimate_SW_model')
        print(f'  δ(800 A/m) = {np.interp(800, H_g, dB):.4f} T', flush=True)

    cs = CubicSpline(H_g, dB, bc_type='not-a-knot', extrapolate=False)
    _delta_spline_cache[key] = cs
    return cs


# ─── TD 方向辅助函数 ──────────────────────────────────────────────────────────
def _estimate_hk_sim_td(H_sim: np.ndarray, B_sim_td: np.ndarray) -> float:
    """
    估计仿真 TD 各向异性场 Hk_sim_td：B_sim_td 首次达到 50%×Bmax 对应的 H_sim。

    物理含义：TD 方向磁化由相干旋转主导，Hk_sim 对应仿真晶粒集合的有效各向异性场。
    """
    B = np.asarray(B_sim_td, dtype=float)
    H = np.asarray(H_sim, dtype=float)
    B_max = float(np.max(B)) if float(np.max(B)) > 1e-6 else 1.42
    threshold = 0.5 * B_max
    for i in range(len(B) - 1):
        if B[i] <= threshold < B[i + 1]:
            t = (threshold - B[i]) / (B[i + 1] - B[i])
            return float(H[i] + t * (H[i + 1] - H[i]))
    mask = B > threshold
    if mask.any():
        return float(H[np.argmax(mask)])
    return float(H[len(H) // 2])


def _get_hk_ref_td(odf_params: dict) -> float:
    """
    计算 IDW 加权参考材料 TD 各向异性场 Hk_ref_td：
    参考 BH 曲线中 B 首次达到 50%×Bmax 对应的 H（A/m）。
    """
    odf = _odf_from_params(odf_params) if 'theta_0_deg' in odf_params else odf_params
    weights = anchor_weights(odf)
    Hk_total = 0.0
    for grade, w in weights.items():
        if w < 1e-6:
            continue
        try:
            H_r, B_r = load_reference_bh(grade, 'TD')
            B_max = float(B_r[-1]) if len(B_r) > 0 else 2.0
            threshold = 0.5 * B_max
            for i in range(len(B_r) - 1):
                if B_r[i] <= threshold < B_r[i + 1]:
                    t_frac = (threshold - float(B_r[i])) / (float(B_r[i + 1]) - float(B_r[i]))
                    Hk_total += w * float(H_r[i] + t_frac * (float(H_r[i + 1]) - float(H_r[i])))
                    break
        except Exception as e:
            print(f'[reference_corrector] 警告：{grade}/TD Hk 估计失败: {e}', file=sys.stderr)
    return max(Hk_total, 10.0)


def get_td_scale(H_sim: np.ndarray, B_sim_td: np.ndarray, odf_params: dict) -> float:
    """
    计算 TD H 轴缩放因子：scale_TD = Hk_ref_TD / Hk_sim_TD。

    使用方法：
        H_real_TD = H_sim × scale_TD    （用于显示或参考插值）

    物理背景：
        RD: Hc_sim >> Hc_ref（单畴壁运动被高估），scale_RD ≈ 0.005
        TD: Hk_sim > Hk_ref（晶粒间相互作用使实际 Hk 偏低），scale_TD ≈ 0.1-0.3
    """
    Hk_sim = _estimate_hk_sim_td(H_sim, B_sim_td)
    Hk_ref = _get_hk_ref_td(odf_params)
    if Hk_sim <= 0:
        return 0.15
    return float(Hk_ref / Hk_sim)


def td_from_reference(H_pred: np.ndarray, odf_params: dict) -> np.ndarray:
    """
    TD 方向参考曲线直接插值（H_pred 视为真实 H，单位 A/m）。
    保留以兼容旧调用；apply_reference_correction 内部现已使用带 scale 的新路径。
    """
    odf = _odf_from_params(odf_params) if 'theta_0_deg' in odf_params else odf_params
    H   = np.asarray(H_pred, dtype=float)
    weights = anchor_weights(odf)
    B_out = np.zeros_like(H)
    for grade, w in weights.items():
        if w < 1e-6:
            continue
        try:
            H_ref, B_ref = load_reference_bh(grade, 'TD')
            B_out += w * np.interp(H, H_ref, B_ref, left=0.0, right=float(B_ref[-1]))
        except Exception as e:
            print(f'[reference_corrector] 警告：{grade}/TD 加载失败: {e}', file=sys.stderr)
    return np.clip(B_out, 0.0, 2.5)


# ─── 辅助：从 B_sim 曲线估计仿真 Hc ─────────────────────────────────────────
def _estimate_hc_sim_from_curve(H_sim: np.ndarray, B_sim: np.ndarray) -> float:
    """
    估计仿真 Hc：B_sim 首次超过 5%×Bmax 对应的 H（线性插值）。
    用于 hc_sim 未提供时的退化估计。
    """
    B_arr = np.asarray(B_sim, dtype=float)
    H_arr = np.asarray(H_sim, dtype=float)
    B_max = float(np.max(B_arr)) if np.max(B_arr) > 0 else 2.0
    threshold = max(0.05 * B_max, 1e-6)
    for i in range(len(B_arr) - 1):
        if B_arr[i] <= threshold < B_arr[i + 1]:
            t = (threshold - B_arr[i]) / (B_arr[i + 1] - B_arr[i])
            return float(H_arr[i] + t * (H_arr[i + 1] - H_arr[i]))
    mask = B_arr > threshold
    if mask.any():
        return float(H_arr[np.argmax(mask)])
    return float(H_arr[len(H_arr) // 2])


# ─── 主校正接口 ──────────────────────────────────────────────────────────────
def apply_reference_correction(H_pred:     np.ndarray,
                                B_sim:      np.ndarray,
                                odf_params: dict,
                                direction:  str   = 'RD',
                                weight_cap: float = 1.0,
                                hc_sim:     float = None,
                                si_content: float = 3.0) -> np.ndarray:
    """
    重设计版 δ(H) 修正 — log(H_real) 平滑混合法（RD 方向）。

    核心思路：
        仿真 BH 曲线缺失真实材料的"初始缓慢磁化"阶段（B_sim 在 H_sim < Hc_sim
        区间被裁剪为 0），且中间上升段斜率过高、膝部过于尖锐。

        修正方式：在真实 H 域（H_real = H_sim × Hc_ref/hc_sim）对参考数据和仿真
        数据做平滑混合（smoothstep），混合比例随 log(H_real) 单调从 1（纯参考）
        过渡到 0（纯仿真）：

            • H_real < H_lo (≈ 0.5×Hc_ref)  → 完全用参考数据（正确的初始磁化形状）
            • H_lo ≤ H_real ≤ H_hi (≈ 10×Hc_ref) → 平滑混合（斜率和膝部圆润）
            • H_real > H_hi                  → 主要用仿真数据（保留饱和特性）

        weight_cap 控制混合强度：0 = 不修正（纯仿真），1 = 完全混合。

    TD 方向：不变，继续使用 IDW 参考曲线直接插值。
    """
    H = np.asarray(H_pred, dtype=float)
    B = np.asarray(B_sim,  dtype=float).copy()

    if direction == 'TD':
        # TD 修正：用 Hk 驱动的 H 轴缩放将 H_sim 映射到真实 TD H 域，再插值参考曲线
        # 物理依据：TD 磁化由相干旋转主导（非畴壁运动），scale_TD = Hk_ref/Hk_sim ≈ 0.1-0.3
        #   比 RD 的 scale_RD ≈ 0.005 大约 30-50 倍
        odf_td   = _odf_from_params(odf_params) if 'theta_0_deg' in odf_params else odf_params
        Hk_sim_td = _estimate_hk_sim_td(H, B)
        Hk_ref_td = _get_hk_ref_td(odf_params)
        scale_td  = Hk_ref_td / max(Hk_sim_td, 1.0)
        H_real_td = H * scale_td          # H_sim → 真实 TD H（A/m）

        wts_td  = anchor_weights(odf_td)
        B_ref_td = np.zeros(len(H))
        for _grade, _w in wts_td.items():
            if _w < 1e-6:
                continue
            try:
                H_r, B_r = load_reference_bh(_grade, 'TD')
                B_ref_td += _w * np.interp(
                    H_real_td, H_r, B_r, left=0.0, right=float(B_r[-1])
                )
            except Exception as _e:
                print(f'[reference_corrector] 警告：{_grade}/TD 加载失败: {_e}',
                      file=sys.stderr)

        # weight_cap：0=纯仿真，1=纯参考（参考 B_ref_td 在整个 H_sim 范围都是物理正确值）
        B_corrected = (1.0 - float(weight_cap)) * B + float(weight_cap) * B_ref_td
        return np.clip(B_corrected, 0.0, _MU0 * (H + _MSAT))

    if weight_cap <= 0.0:
        return B

    # ── 参数准备 ───────────────────────────────────────────────────────────────
    try:
        from go_steel_reference import get_reference_hc
        Hc_ref = get_reference_hc(si_content=si_content)
    except Exception:
        Hc_ref = 2.0 * si_content  # 退化估计：Hc_ref ≈ 2×Si% A/m

    if hc_sim is None or float(hc_sim) <= 0:
        hc_sim = _estimate_hc_sim_from_curve(H, B)
    hc_sim = float(hc_sim)

    scale  = Hc_ref / hc_sim
    H_real = H * scale  # STANDARD_H_POINTS → 真实 H [A/m]

    # ── 从参考 CSV 插值得到全段参考 B ─────────────────────────────────────────
    odf     = _odf_from_params(odf_params) if 'theta_0_deg' in odf_params else odf_params
    weights = anchor_weights(odf)

    B_ref_all = np.zeros(len(H))
    for grade, w in weights.items():
        if w < 1e-6:
            continue
        try:
            H_ref_raw, B_ref_raw = load_reference_bh(grade, 'RD')
            B_ref_all += w * np.interp(
                H_real, H_ref_raw, B_ref_raw,
                left=0.0, right=float(B_ref_raw[-1])
            )
        except Exception as e:
            print(f'[reference_corrector] 警告：{grade}/RD 参考加载失败: {e}',
                  file=sys.stderr)

    # ── Smoothstep 混合权重（在 log H_real 域连续过渡） ──────────────────────
    # H_lo：低于此 H_real，完全用参考（≈ 0.5×Hc_ref，约 3 A/m for Si=3%）
    # H_hi：高于此 H_real，主要用仿真（≈ 10×Hc_ref，约 60 A/m for Si=3%）
    H_lo = 0.5  * Hc_ref
    H_hi = 10.0 * Hc_ref

    # alpha_sim：对仿真数据的权重（0 = 全参考，1 = 全仿真）
    log_t      = np.log(np.maximum(H_real, 1e-9) / H_lo) / np.log(H_hi / H_lo)
    t          = np.clip(log_t, 0.0, 1.0)
    alpha_sim  = t * t * (3.0 - 2.0 * t)   # cubic smoothstep

    # weight_cap 控制修正强度：0=不修正，1=完全混合
    # alpha_ref = 参考数据的比例 = (1 - alpha_sim) × weight_cap
    alpha_ref  = (1.0 - alpha_sim) * float(weight_cap)
    B_corrected = (1.0 - alpha_ref) * B + alpha_ref * B_ref_all

    B_max = _MU0 * (H + _MSAT)
    return np.clip(B_corrected, 0.0, B_max)


# ─── 便利函数：初始化所有锚点校准 ────────────────────────────────────────────
def initialize_all_calibrations(verbose: bool = True) -> dict[str, str]:
    """
    预计算并缓存所有牌号 RD/TD 校准数据。首次运行约 5-10 秒。
    返回 {grade_dir: file_path}。
    """
    results = {}
    for grade in ANCHOR_ODF:
        for direction in ['RD', 'TD']:
            try:
                _get_delta_spline(grade, direction)
                path = str(_anchor_dir() / f'{grade}_{direction}_delta.json')
                results[f'{grade}_{direction}'] = path
                if verbose:
                    print(f'  OK: {grade}/{direction}')
            except Exception as e:
                if verbose:
                    print(f'  FAIL: {grade}/{direction}: {e}')
    return results


# ─── 更新单个锚点校准（供 recalibrate_anchor.py 调用）────────────────────────
def update_anchor_from_simulation(grade:    str,
                                   direction: str,
                                   sim_H:     list,
                                   sim_B:     list) -> str:
    """
    用真实流水线仿真数据替换物理估算校准。

    Args:
        grade:     牌号字符串，如 'B27R090'
        direction: 'RD' 或 'TD'
        sim_H:     锚点参数仿真 H 数组 [A/m]
        sim_B:     锚点参数仿真聚合 B 数组 [T]

    Returns:
        保存的 JSON 路径。
    """
    H_ref, B_ref = load_reference_bh(grade, direction)
    sim_H_arr    = np.array(sim_H, dtype=float)
    sim_B_arr    = np.array(sim_B, dtype=float)

    # 统一到公共 H 网格（只取仿真和参考数据的重叠区间）
    H_min = max(float(sim_H_arr.min()), float(H_ref.min()))
    H_max = min(float(sim_H_arr.max()), float(H_ref.max()))
    H_grid = _H_GRID[((_H_GRID >= H_min) & (_H_GRID <= H_max))]
    if len(H_grid) < 5:
        raise ValueError(f'仿真与参考数据重叠 H 区间过小: [{H_min}, {H_max}] A/m')

    B_ref_i = np.interp(H_grid, H_ref, B_ref)
    B_sim_i = np.interp(H_grid, sim_H_arr, sim_B_arr)
    delta   = B_ref_i - B_sim_i

    # 清除缓存
    key = f'{grade}_{direction}'
    _delta_spline_cache.pop(key, None)

    return save_anchor_delta(grade, direction, H_grid, delta,
                              source='real_pipeline_simulation')
