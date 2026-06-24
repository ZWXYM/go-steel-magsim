"""
bh_curve_analyzer.py

B-H 曲线宏观参数分析与对比绘图 —— 从 go_steel_data/analyze_all_go_steel.py 移植集成。
保留原始分析风格（PCHIP 插值 / Bertotti 拟合 / matplotlib 图表）。

主要集成入口：
  analyze_bh_pair(name, rd_H, rd_B, td_H, td_B, save_dir, ...) -> dict

独立运行（处理 go_steel_data/output/ 下全部参考等级）：
  python bh_curve_analyzer.py

Important physics boundary (same as original analyze_all_go_steel.py):
  A single-valued normal/initial BH curve cannot determine remanence Br,
  coercivity Hc, or hysteresis coefficients Kh/Kc/Ke uniquely.  Br/Hc need a
  full hysteresis loop.  Kh/Kc/Ke need loss-vs-frequency/flux-density data.
"""
from __future__ import annotations

import csv
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from scipy.interpolate import PchipInterpolator
from scipy.optimize import nnls

# Reference GO steel data directory (relative to this module's location)
_GODATA_OUTPUT = Path(__file__).resolve().parents[1] / 'go_steel_data' / 'output'
# Simulation exports directory (data/exports/ at project root)
_EXPORTS_DIR   = Path(__file__).resolve().parents[1] / 'data' / 'exports'

MU0 = 4.0 * math.pi * 1e-7
DEFAULT_DENSITY      = 7650.0
DEFAULT_CONDUCTIVITY = 2_000_000.0

H_DENSE = np.concatenate(([0.0], np.logspace(-1, math.log10(50_000), 700)))
H_PLOT  = H_DENSE[1:]
H_KEY   = [50, 100, 200, 500, 800, 1000, 2000, 5000]
B_TARGETS = [1.0, 1.5, 1.7, 1.8, 1.9]


@dataclass
class LossPoint:
    b_t: float
    f_hz: float
    p_w_kg: float
    source: str


@dataclass
class Material:
    name: str
    source_file: str
    rd: list[tuple[float, float]]
    td: list[tuple[float, float]]
    conductivity_s_m: float = DEFAULT_CONDUCTIVITY
    density_kg_m3: float   = DEFAULT_DENSITY
    thickness_m: float | None = None
    core_loss_kh_existing: float | None = None
    core_loss_kc_existing: float | None = None
    core_loss_ke_existing: float | None = None
    source_rank: int = 0
    loss_points: list[LossPoint] = field(default_factory=list)

    @property
    def thickness_mm(self) -> float | None:
        return self.thickness_m * 1000.0 if self.thickness_m is not None else None

    @property
    def short_name(self) -> str:
        return self.name.replace('GO_Steel_', '')


# Measured loss points from Baosteel EVI / GO manual and IEC grade specs.
# These are the only grades for which P15/50, P17/50 etc. are available;
# all other simulated materials will fall back to BH-only estimation.
LOSS_POINTS: dict[str, list[LossPoint]] = {
    'GO_Steel_B23R075': [
        LossPoint(1.5, 50, 0.56, 'Baosteel EVI table P15/50 typical'),
        LossPoint(1.7, 50, 0.74, 'Baosteel EVI table P17/50 typical'),
    ],
    'GO_Steel_B27R090': [
        LossPoint(1.5, 50, 0.63, 'Baosteel GO manual table P15/50 typical'),
        LossPoint(1.7, 50, 0.86, 'Baosteel GO manual table P17/50 typical'),
        LossPoint(1.5, 60, 0.83, 'Baosteel GO manual table P15/60 typical'),
        LossPoint(1.7, 60, 1.15, 'Baosteel GO manual table P17/60 typical'),
    ],
    'GO_Steel_B27R095': [
        LossPoint(1.5, 50, 0.66, 'Baosteel GO manual table P15/50 typical'),
        LossPoint(1.7, 50, 0.90, 'Baosteel GO manual table P17/50 typical'),
        LossPoint(1.5, 60, 0.87, 'Baosteel GO manual table P15/60 typical'),
        LossPoint(1.7, 60, 1.18, 'Baosteel GO manual table P17/60 typical'),
    ],
    'GO_Steel_B30P105': [
        LossPoint(1.5, 50, 0.73, 'Baosteel GO manual table P15/50 typical'),
        LossPoint(1.7, 50, 1.00, 'Baosteel GO manual table P17/50 typical'),
        LossPoint(1.5, 60, 0.96, 'Baosteel GO manual table P15/60 typical'),
        LossPoint(1.7, 60, 1.31, 'Baosteel GO manual table P17/60 typical'),
    ],
    'GO_Steel_Baosteel_B23R080': [
        LossPoint(1.5, 50, 0.57, 'Baosteel GO manual table P15/50 typical'),
        LossPoint(1.7, 50, 0.78, 'Baosteel GO manual table P17/50 typical'),
        LossPoint(1.5, 60, 0.75, 'Baosteel GO manual table P15/60 typical'),
        LossPoint(1.7, 60, 1.01, 'Baosteel GO manual table P17/60 typical'),
    ],
    'GO_Steel_Baosteel_B20R070': [
        LossPoint(1.5, 50, 0.51, 'Baosteel EVI table P15/50 typical'),
        LossPoint(1.7, 50, 0.68, 'Baosteel EVI table P17/50 typical'),
    ],
    'GO_Steel_Baosteel_B18R065': [
        LossPoint(1.5, 50, 0.48, 'Baosteel EVI table P15/50 typical'),
        LossPoint(1.7, 50, 0.64, 'Baosteel EVI table P17/50 typical'),
    ],
    'GO_Steel_IEC_M089_27P': [LossPoint(1.7, 50, 0.89, 'IEC grade max W17/50')],
    'GO_Steel_IEC_M111_30P': [LossPoint(1.7, 50, 1.11, 'IEC grade max W17/50')],
    'GO_Steel_IEC_M120_35S': [LossPoint(1.5, 50, 1.20, 'IEC grade max W15/50')],
}


# ── .amat parsing (same logic as original analyze_all_go_steel.py) ─────────

def _source_rank(path: Path) -> int:
    aggregate_names = {
        'GO_Steel_All_Grades.amat',
        'GO_Steel_Baosteel_EVI.amat',
        'GO_Steel_IEC_Grades.amat',
    }
    return 0 if path.name in aggregate_names else 1


def _parse_float_property(block: str, prop: str) -> float | None:
    match = re.search(rf"'{re.escape(prop)}'\s*=\s*'([^']+)'", block)
    if not match:
        return None
    text = match.group(1)
    num = re.search(r'[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?', text)
    return float(num.group(0)) if num else None


def _parse_points_from_component(block: str, component_name: str) -> list[tuple[float, float]]:
    comp_match = re.search(
        rf"\$begin '{component_name}'(?P<body>.*?)\$end '{component_name}'",
        block, re.S,
    )
    if not comp_match:
        return []
    body = comp_match.group('body')
    points_match = re.search(r'Points\[\s*\d+\s*:\s*(?P<vals>[^\]]+)\]', body, re.S)
    if not points_match:
        return []
    vals = [
        float(item)
        for item in re.findall(r'[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?', points_match.group('vals'))
    ]
    if len(vals) % 2:
        raise ValueError(f'Odd BH coordinate count in {component_name}')
    return sorted(zip(vals[0::2], vals[1::2]), key=lambda p: p[0])


def _iter_material_blocks(text: str) -> Iterable[tuple[str, str]]:
    # Match any top-level $begin 'name' (no leading whitespace = not a nested block)
    start_re = re.compile(r"^\$begin '([^']+)'", re.M)
    matches = list(start_re.finditer(text))
    for idx, match in enumerate(matches):
        name  = match.group(1)
        start = match.start()
        end   = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        block = text[start:end]
        end_marker = f"$end '{name}'"
        marker_idx = block.find(end_marker)
        if marker_idx >= 0:
            block = block[: marker_idx + len(end_marker)]
        yield name, block


def load_reference_materials(output_dir: Path | None = None) -> list[Material]:
    """
    Load all GO steel reference materials from go_steel_data/output/*.amat.
    Returns empty list if the directory does not exist (graceful fallback).
    """
    d = output_dir or _GODATA_OUTPUT
    if not d.is_dir():
        return []
    materials: dict[str, Material] = {}
    for path in sorted(d.glob('GO_Steel*.amat')):
        text = path.read_text(encoding='utf-8', errors='replace')
        rank = _source_rank(path)
        for name, block in _iter_material_blocks(text):
            rd = _parse_points_from_component(block, 'component1')
            td = _parse_points_from_component(block, 'component2')
            if not rd or not td:
                continue
            mat = Material(
                name=name,
                source_file=path.name,
                rd=rd,
                td=td,
                conductivity_s_m=_parse_float_property(block, 'conductivity') or DEFAULT_CONDUCTIVITY,
                density_kg_m3   =_parse_float_property(block, 'mass_density')  or DEFAULT_DENSITY,
                thickness_m     =_parse_float_property(block, 'core_loss_equiv_cut_depth'),
                core_loss_kh_existing=_parse_float_property(block, 'core_loss_kh'),
                core_loss_kc_existing=_parse_float_property(block, 'core_loss_kc'),
                core_loss_ke_existing=_parse_float_property(block, 'core_loss_ke'),
                source_rank=rank,
                loss_points=list(LOSS_POINTS.get(name, [])),
            )
            current = materials.get(name)
            if current is None or mat.source_rank >= current.source_rank:
                materials[name] = mat
    return sorted(materials.values(), key=lambda m: m.short_name)


def load_simulation_materials(exports_dir: Path | str | None = None) -> list[Material]:
    """
    Load simulation-generated materials from data/exports/*.amat.
    These are GO_Sim_* / GO_Pred_* files produced by maxwell_exporter.
    """
    d = Path(exports_dir) if exports_dir else _EXPORTS_DIR
    if not d.is_dir():
        return []
    materials: list[Material] = []
    seen: set[str] = set()
    for path in sorted(d.glob('*.amat'), reverse=True):  # newest first
        try:
            text = path.read_text(encoding='utf-8', errors='replace')
            for name, block in _iter_material_blocks(text):
                if name in seen:
                    continue
                rd = _parse_points_from_component(block, 'component1')
                td = _parse_points_from_component(block, 'component2')
                if not rd or not td:
                    continue
                mat = Material(
                    name=name,
                    source_file=path.name,
                    rd=rd,
                    td=td,
                    conductivity_s_m=_parse_float_property(block, 'conductivity') or DEFAULT_CONDUCTIVITY,
                    density_kg_m3   =_parse_float_property(block, 'mass_density')  or DEFAULT_DENSITY,
                    thickness_m     =_parse_float_property(block, 'core_loss_equiv_cut_depth'),
                    source_rank=1,
                )
                materials.append(mat)
                seen.add(name)
        except Exception:
            continue
    return materials


def list_available_materials(exports_dir: Path | str | None = None) -> list[dict]:
    """
    返回所有可用于对比图的材料列表（参考等级 + 历史仿真导出）。
    供前端材料选择器使用。
    """
    refs = load_reference_materials()
    sims = load_simulation_materials(exports_dir)
    result: list[dict] = []

    for mat in refs:
        b800_rd = b_at_h(mat.rd, 800) if mat.rd else None
        b800_td = b_at_h(mat.td, 800) if mat.td else None
        result.append({
            'name':          mat.name,
            'short_name':    mat.short_name,
            'source':        'reference',
            'source_file':   mat.source_file,
            'B800_RD':       round(b800_rd, 4) if b800_rd else None,
            'B800_TD':       round(b800_td, 4) if b800_td else None,
            'thickness_mm':  mat.thickness_mm,
            'has_loss_pts':  bool(mat.loss_points),
        })
    for mat in sims:
        b800_rd = b_at_h(mat.rd, 800) if mat.rd else None
        b800_td = b_at_h(mat.td, 800) if mat.td else None
        result.append({
            'name':          mat.name,
            'short_name':    mat.short_name,
            'source':        'simulation',
            'source_file':   mat.source_file,
            'B800_RD':       round(b800_rd, 4) if b800_rd else None,
            'B800_TD':       round(b800_td, 4) if b800_td else None,
            'thickness_mm':  mat.thickness_mm,
            'has_loss_pts':  False,
        })
    return result


# ── Curve interpolation ────────────────────────────────────────────────────

def clean_curve(points: list[tuple[float, float]]) -> tuple[np.ndarray, np.ndarray]:
    """Remove duplicate H values and enforce nondecreasing B."""
    h_values: list[float] = []
    b_values: list[float] = []
    for h, b in sorted(points, key=lambda p: p[0]):
        if h_values and math.isclose(h, h_values[-1], rel_tol=0, abs_tol=1e-12):
            b_values[-1] = max(b_values[-1], b)
        else:
            h_values.append(float(h))
            b_values.append(float(b))
    b_arr = np.maximum.accumulate(np.array(b_values, dtype=float))
    return np.array(h_values, dtype=float), b_arr


def curve_eval(points: list[tuple[float, float]], h_query: Iterable[float]) -> np.ndarray:
    h, b = clean_curve(points)
    hq   = np.array(list(h_query), dtype=float)
    interp = PchipInterpolator(h, b, extrapolate=False)
    out = interp(np.clip(hq, h[0], h[-1]))
    out = np.where(hq <= h[0], b[0], out)
    out = np.where(hq >= h[-1], b[-1], out)
    return np.array(out, dtype=float)


def derivative_eval(points: list[tuple[float, float]], h_query: Iterable[float]) -> np.ndarray:
    h, b = clean_curve(points)
    hq   = np.array(list(h_query), dtype=float)
    interp = PchipInterpolator(h, b, extrapolate=False)
    deriv  = interp.derivative()(np.clip(hq, h[0], h[-1]))
    return np.maximum(np.array(deriv, dtype=float), 0.0)


def b_at_h(points: list[tuple[float, float]], h_query: float) -> float:
    return float(curve_eval(points, [h_query])[0])


def h_at_b(points: list[tuple[float, float]], b_query: float) -> float | None:
    h, b = clean_curve(points)
    if b_query < b[0] or b_query > b[-1]:
        return None
    unique_b: list[float] = []
    unique_h: list[float] = []
    for hi, bi in zip(h, b):
        if unique_b and math.isclose(bi, unique_b[-1], rel_tol=0, abs_tol=1e-12):
            unique_h[-1] = min(unique_h[-1], hi)
        else:
            unique_b.append(float(bi))
            unique_h.append(float(hi))
    return float(np.interp(b_query, unique_b, unique_h))


# ── Parameter extraction ───────────────────────────────────────────────────

def curve_params(mat: Material, direction: str, points: list[tuple[float, float]]) -> dict[str, object]:
    """Extract macroscopic parameters from a single BH curve (first-quadrant only)."""
    h, b = clean_curve(points)
    positive_h = h[h > 0]
    positive_b = b[h > 0]
    first_h = float(positive_h[0]) if len(positive_h) else float('nan')
    first_b = float(positive_b[0]) if len(positive_b) else float('nan')
    h_eval = np.logspace(math.log10(max(first_h, 1e-6)), math.log10(h[-1]), 1000)
    b_eval = curve_eval(points, h_eval)
    d_eval = derivative_eval(points, h_eval)
    mu_app  = b_eval / (MU0 * h_eval)
    mu_diff = d_eval / MU0

    row: dict[str, object] = {
        'material': mat.name,
        'direction': direction,
        'source_file': mat.source_file,
        'thickness_mm': mat.thickness_mm,
        'density_kg_m3': mat.density_kg_m3,
        'conductivity_s_m': mat.conductivity_s_m,
        'point_count': len(points),
        'first_nonzero_H_A_m': first_h,
        'first_nonzero_B_T': first_b,
        'mu_r_initial_secant': first_b / (MU0 * first_h) if first_h > 0 else None,
        'mu_r_app_max': float(np.nanmax(mu_app)),
        'mu_r_inc_max': float(np.nanmax(mu_diff)),
        'mu_r_app_at_800': b_at_h(points, 800) / (MU0 * 800),
        'Bmax_at_highest_H_T': float(b[-1]),
        'Hmax_A_m': float(h[-1]),
        'remanence_Br_T': None,
        'coercivity_Hc_A_m': None,
        'hysteresis_params_note': 'Unavailable: first-quadrant normal BH curve is not a hysteresis loop.',
    }
    for hq in H_KEY:
        row[f'B_at_{hq}_A_m_T'] = b_at_h(points, hq)
    for bq in B_TARGETS:
        row[f'H_at_{str(bq).replace(".", "p")}_T_A_m'] = h_at_b(points, bq)
    return row


def classical_kc_mass(mat: Material) -> float | None:
    """Classical eddy-current Kc for Pm = Kc*f^2*B^2 in W/kg."""
    if mat.thickness_m is None or mat.density_kg_m3 <= 0:
        return None
    return (math.pi ** 2) * mat.conductivity_s_m * mat.thickness_m ** 2 / (6.0 * mat.density_kg_m3)


def estimate_core_loss(mat: Material) -> dict[str, object]:
    """
    Estimate Bertotti-style coefficients from available measured loss points.

    Model per unit mass: P = Kh*f*B^2 + Kc*f^2*B^2 + Ke*f^1.5*B^1.5
    Kc is from the classical lamination formula.
    Kh/Ke from non-negative least squares when ≥2 loss points exist.
    """
    kc     = classical_kc_mass(mat)
    points = mat.loss_points
    base = {
        'material': mat.name,
        'thickness_mm': mat.thickness_mm,
        'density_kg_m3': mat.density_kg_m3,
        'conductivity_s_m': mat.conductivity_s_m,
        'existing_amat_kh': mat.core_loss_kh_existing,
        'existing_amat_kc': mat.core_loss_kc_existing,
        'existing_amat_ke': mat.core_loss_ke_existing,
        'Kc_classical_mass': kc,
        'Kc_classical_volumetric': kc * mat.density_kg_m3 if kc is not None else None,
        'Kh_fit_mass': None,
        'Ke_fit_mass': None,
        'Kh_fit_volumetric': None,
        'Ke_fit_volumetric': None,
        'fit_rmse_w_kg': None,
        'fit_max_abs_error_w_kg': None,
        'loss_points': '; '.join(
            f'B={p.b_t:g}T f={p.f_hz:g}Hz P={p.p_w_kg:g}W/kg ({p.source})'
            for p in points
        ),
        'method': 'not_identifiable_from_BH_only',
        'fit_warning': 'Need measured core-loss points vs B and f; BH curve alone is insufficient.',
    }
    if kc is None:
        base['fit_warning'] = 'Missing lamination thickness or density; Kc not calculated.'
        return base
    if not points:
        return base

    if len(points) >= 2:
        a = np.array([[p.f_hz * p.b_t ** 2, (p.f_hz * p.b_t) ** 1.5] for p in points], dtype=float)
        y = np.array([p.p_w_kg - kc * p.f_hz ** 2 * p.b_t ** 2 for p in points], dtype=float)
        kh, ke = nnls(a, y)[0]
        predicted = np.array([
            kh * p.f_hz * p.b_t ** 2
            + kc * p.f_hz ** 2 * p.b_t ** 2
            + ke * (p.f_hz * p.b_t) ** 1.5
            for p in points
        ], dtype=float)
        observed  = np.array([p.p_w_kg for p in points], dtype=float)
        rmse      = float(np.sqrt(np.mean((predicted - observed) ** 2)))
        max_error = float(np.max(np.abs(predicted - observed)))
        freqs   = {p.f_hz for p in points}
        fluxes  = {p.b_t  for p in points}
        warning_bits = []
        if len(freqs)  < 2: warning_bits.append('Only one frequency; Kh/Ke split is weak.')
        if len(fluxes) < 2: warning_bits.append('Only one B level; B-exponent assumptions dominate.')
        if ke <= 1e-12:      warning_bits.append('Ke fitted to zero by NNLS.')
        base.update({
            'Kh_fit_mass': float(kh),
            'Ke_fit_mass': float(ke),
            'Kh_fit_volumetric': float(kh * mat.density_kg_m3),
            'Ke_fit_volumetric': float(ke * mat.density_kg_m3),
            'fit_rmse_w_kg': rmse,
            'fit_max_abs_error_w_kg': max_error,
            'method': 'non-negative Bertotti fit with Kc from classical lamination; B-exponent fixed to 2',
            'fit_warning': ' '.join(warning_bits),
        })
        return base

    # Single loss point
    pt = points[0]
    residual   = pt.p_w_kg - kc * pt.f_hz ** 2 * pt.b_t ** 2
    kh_equiv   = residual / (pt.f_hz * pt.b_t ** 2)
    base.update({
        'Kh_fit_mass': float(kh_equiv),
        'Ke_fit_mass': 0.0,
        'Kh_fit_volumetric': float(kh_equiv * mat.density_kg_m3),
        'Ke_fit_volumetric': 0.0,
        'method': 'one-point equivalent: Kc classical, Ke forced to 0, B-exponent=2',
        'fit_warning': 'Single loss point: Kh/Ke cannot be split; Kh is an equivalent residual coefficient.',
    })
    return base


# ── Plotting ───────────────────────────────────────────────────────────────

# 12 visually distinct colors (ColorBrewer Set1 + Dark2 blend)
_PALETTE = [
    '#e41a1c',  # red
    '#377eb8',  # blue
    '#4daf4a',  # green
    '#984ea3',  # purple
    '#ff7f00',  # orange
    '#a65628',  # brown
    '#17becf',  # cyan
    '#e7298a',  # magenta
    '#1b9e77',  # dark teal
    '#7570b3',  # periwinkle
    '#66a61e',  # olive green
    '#e6ab02',  # gold
]

# Marker positions: indices in H_PLOT nearest to these H values (log-spaced, visible range)
_MARK_IDX = [int(np.searchsorted(H_PLOT, h)) for h in [5, 40, 400, 4000]]

# One marker shape per grade; strips vendor prefixes and _sim/_pred suffixes
_GRADE_MARKERS  = ['o', 's', '^', 'D', 'v', 'P', 'X', 'h', '8', 'p']
_GRADE_KEY_RE   = re.compile(r'^(?:Baosteel_|IEC_)?(.+?)(?:_(?:sim|pred))?(?:_\d{8,})?$', re.I)


def _grade_key(short_name: str) -> str:
    m = _GRADE_KEY_RE.match(short_name)
    return (m.group(1) if m else short_name).upper()


def _marker_map(materials: list[Material]) -> dict[str, str]:
    grade_to_mk: dict[str, str] = {}
    idx = 0
    result: dict[str, str] = {}
    for mat in materials:
        key = _grade_key(mat.short_name)
        if key not in grade_to_mk:
            grade_to_mk[key] = _GRADE_MARKERS[idx % len(_GRADE_MARKERS)]
            idx += 1
        result[mat.name] = grade_to_mk[key]
    return result


def _color_map(materials: list[Material], highlight_name: str | None = None) -> dict[str, tuple]:
    """Assign colors. Highlighted material gets bright orange; references get 12-color distinct palette."""
    colors: dict[str, tuple] = {}
    ref_idx = 0
    for mat in materials:
        if mat.name == highlight_name:
            colors[mat.name] = (0.976, 0.451, 0.086, 1.0)  # orange
        else:
            hex_c = _PALETTE[ref_idx % len(_PALETTE)]
            r, g, b = int(hex_c[1:3], 16)/255, int(hex_c[3:5], 16)/255, int(hex_c[5:7], 16)/255
            colors[mat.name] = (r, g, b, 1.0)
            ref_idx += 1
    return colors



def make_all_bh_plot(
    materials: list[Material],
    save_path: Path,
    highlight_name: str | None = None,
) -> None:
    """All-grades B-H overlay (RD solid / TD dashed). Highlighted material drawn last."""
    colors  = _color_map(materials, highlight_name)
    markers = _marker_map(materials)
    fig, ax = plt.subplots(figsize=(15, 8.5))

    refs     = [m for m in materials if m.name != highlight_name]
    sim_mats = [m for m in materials if m.name == highlight_name]

    for mat in refs + sim_mats:
        b_rd = curve_eval(mat.rd, H_DENSE)[1:]
        b_td = curve_eval(mat.td, H_DENSE)[1:]
        color  = colors[mat.name]
        mk     = markers[mat.name]
        is_sim = (mat.name == highlight_name)
        lw_rd  = 2.8 if is_sim else 1.8
        lw_td  = 2.0 if is_sim else 1.2
        ms     = 7   if is_sim else 5
        zo     = 10  if is_sim else 1
        label  = f'★ {mat.short_name}' if is_sim else mat.short_name
        # RD solid: filled marker; TD dashed: no marker (avoid clutter)
        ax.semilogx(H_PLOT, b_rd, color=color, lw=lw_rd, zorder=zo, label=label,
                    marker=mk, markersize=ms, markevery=_MARK_IDX, markeredgewidth=0)
        ax.semilogx(H_PLOT, b_td, color=color, lw=lw_td, ls='--', alpha=0.80, zorder=zo)

    ax.axhline(2.03, color='0.35', ls=':', lw=1.2, label='Js approx. 2.03 T')
    ax.axvline(800,  color='0.15', ls=':', lw=1.0, alpha=0.6)
    ax.text(830, 0.05, 'H=800 A/m', rotation=90, color='0.25', fontsize=9)
    title_note = f'  (★ = Sim: {highlight_name})' if highlight_name else ''
    ax.set_title(
        f'GO Steel BH Curves (RD solid / TD dashed){title_note}',
        fontsize=13, weight='bold'
    )
    ax.set_xlabel('Magnetic field strength H (A/m)', fontsize=11)
    ax.set_ylabel('Magnetic flux density B (T)', fontsize=11)
    ax.set_xlim(1, 50_000)
    ax.set_ylim(0, 2.1)
    ax.tick_params(labelsize=10)
    ax.grid(True, which='both', ls=':', alpha=0.35)
    ax.legend(loc='lower right', fontsize=13, framealpha=0.9, edgecolor='0.7', ncol=2)
    fig.tight_layout()
    fig.savefig(save_path, dpi=170)
    plt.close(fig)


def make_rd_analysis_plot(
    materials: list[Material],
    save_path: Path,
    highlight_name: str | None = None,
) -> None:
    """RD comparison (full log range + knee-region zoom with B800 markers)."""
    colors  = _color_map(materials, highlight_name)
    markers = _marker_map(materials)
    fig, axes = plt.subplots(1, 2, figsize=(16, 7.5))
    ax, ax2 = axes
    h_zoom = np.linspace(5, 2000, 800)

    refs     = [m for m in materials if m.name != highlight_name]
    sim_mats = [m for m in materials if m.name == highlight_name]

    for mat in refs + sim_mats:
        color  = colors[mat.name]
        mk     = markers[mat.name]
        is_sim = (mat.name == highlight_name)
        lw     = 2.8 if is_sim else 1.8
        ms     = 7   if is_sim else 5
        zo     = 10  if is_sim else 1
        label  = f'★ {mat.short_name}' if is_sim else mat.short_name
        b_rd   = curve_eval(mat.rd, H_DENSE)[1:]
        ax.semilogx(H_PLOT, b_rd, color=color, lw=lw, label=label, zorder=zo,
                    marker=mk, markersize=ms, markevery=_MARK_IDX, markeredgewidth=0)
        ax2.plot(h_zoom, curve_eval(mat.rd, h_zoom), color=color, lw=lw, label=label, zorder=zo)
        sc_ms = 80 if is_sim else 30
        sc_mk = '*' if is_sim else mk
        ax2.scatter([800], [b_at_h(mat.rd, 800)], color=color, s=sc_ms, zorder=zo + 1, marker=sc_mk)

    for axis in axes:
        axis.grid(True, ls=':', alpha=0.35)
        axis.axvline(800, color='0.2', ls=':', lw=1.0, alpha=0.55)
        axis.tick_params(labelsize=10)
    ax.axhline(2.03, color='0.4', ls=':', lw=1.0, alpha=0.65)
    ax.set_title('Rolling Direction BH Curves (log H)', fontsize=11)
    ax.set_xlabel('H (A/m)', fontsize=11)
    ax.set_ylabel('B (T)', fontsize=11)
    ax.set_xlim(1, 50_000)
    ax.set_ylim(0, 2.1)
    ax2.set_title('Knee Region and B800 Markers', fontsize=11)
    ax2.set_xlabel('H (A/m)', fontsize=11)
    ax2.set_ylabel('B (T)', fontsize=11)
    ax2.set_xlim(5, 2000)
    ax2.set_ylim(1.35, 2.04)
    ax.legend(loc='lower right', fontsize=13, framealpha=0.9, edgecolor='0.7')
    ax2.legend(loc='lower right', fontsize=13, framealpha=0.9, edgecolor='0.7')
    fig.suptitle('GO Steel — Rolling Direction Analysis', fontsize=13, weight='bold')
    fig.tight_layout()
    fig.savefig(save_path, dpi=170)
    plt.close(fig)


def make_td_anisotropy_plot(
    materials: list[Material],
    save_path: Path,
    highlight_name: str | None = None,
) -> None:
    """TD curves + anisotropy heatmap (B_TD/B_RD at each H_KEY)."""
    colors  = _color_map(materials, highlight_name)
    markers = _marker_map(materials)
    fig, axes = plt.subplots(1, 2, figsize=(16, 7.5), gridspec_kw={'width_ratios': [1.0, 1.3]})
    ax, ax2 = axes

    refs     = [m for m in materials if m.name != highlight_name]
    sim_mats = [m for m in materials if m.name == highlight_name]

    for mat in refs + sim_mats:
        color  = colors[mat.name]
        mk     = markers[mat.name]
        is_sim = (mat.name == highlight_name)
        lw     = 2.8 if is_sim else 1.8
        ms     = 7   if is_sim else 5
        zo     = 10  if is_sim else 1
        label  = f'★ {mat.short_name}' if is_sim else mat.short_name
        b_td   = curve_eval(mat.td, H_DENSE)[1:]
        ax.semilogx(H_PLOT, b_td, color=color, lw=lw, label=label, zorder=zo,
                    marker=mk, markersize=ms, markevery=_MARK_IDX, markeredgewidth=0)

    ax.axhline(2.03, color='0.4', ls=':', lw=1.0, alpha=0.65)
    ax.set_title('Transverse Direction BH Curves', fontsize=11)
    ax.set_xlabel('H (A/m)', fontsize=11)
    ax.set_ylabel('B (T)', fontsize=11)
    ax.set_xlim(1, 50_000)
    ax.set_ylim(0, 2.1)
    ax.tick_params(labelsize=10)
    ax.grid(True, which='both', ls=':', alpha=0.35)

    all_mats = refs + sim_mats
    ratios: list[list[float]] = []
    y_labels: list[str] = []
    for mat in all_mats:
        row: list[float] = []
        for hq in H_KEY:
            brd = b_at_h(mat.rd, hq)
            btd = b_at_h(mat.td, hq)
            row.append(btd / brd if brd > 0 else float('nan'))
        ratios.append(row)
        y_labels.append(('★ ' if mat.name == highlight_name else '') + mat.short_name)

    ratio_arr = np.array(ratios, dtype=float)
    image = ax2.imshow(ratio_arr, aspect='auto', cmap='viridis', vmin=0.0, vmax=1.05)
    ax2.set_title('Anisotropy Ratio B_TD / B_RD', fontsize=11)
    ax2.set_xticks(np.arange(len(H_KEY)), [str(h) for h in H_KEY], fontsize=10)
    ax2.set_yticks(np.arange(len(y_labels)), y_labels, fontsize=10)
    ax2.set_xlabel('H (A/m)', fontsize=11)
    for i in range(ratio_arr.shape[0]):
        for j in range(ratio_arr.shape[1]):
            val = ratio_arr[i, j]
            if not math.isfinite(val):
                continue
            text_color = 'white' if val < 0.55 else 'black'
            weight = 'bold' if y_labels[i].startswith('★') else 'normal'
            ax2.text(j, i, f'{val:.2f}', ha='center', va='center',
                     fontsize=9, color=text_color, weight=weight)
    cbar = fig.colorbar(image, ax=ax2, fraction=0.046, pad=0.04)
    cbar.set_label('B_TD / B_RD', fontsize=10)
    cbar.ax.tick_params(labelsize=9)
    fig.suptitle('GO Steel — Transverse Direction Analysis', fontsize=13, weight='bold')
    ax.legend(loc='lower right', fontsize=13, framealpha=0.9, edgecolor='0.7')
    fig.tight_layout()
    fig.savefig(save_path, dpi=170)
    plt.close(fig)


# ── CSV / JSON utilities (same as original) ────────────────────────────────

def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        return
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def format_optional(value: object, precision: int = 1) -> str:
    if value is None:
        return 'n/a'
    try:
        val = float(value)
    except (TypeError, ValueError):
        return str(value)
    if not math.isfinite(val):
        return 'n/a'
    return f'{val:.{precision}f}'


# ── Main integration entry point ───────────────────────────────────────────

def analyze_bh_pair(
    name: str,
    rd_H: list,
    rd_B: list,
    td_H: list,
    td_B: list,
    save_dir: str | Path,
    thickness_mm: float = 0.35,
    density_kg_m3: float = DEFAULT_DENSITY,
    conductivity_s_m: float = DEFAULT_CONDUCTIVITY,
    include_names: list[str] | None = None,
    raw_rd_H: list | None = None,
    raw_rd_B: list | None = None,
    raw_td_H: list | None = None,
    raw_td_B: list | None = None,
) -> dict:
    """
    Analyze a simulated RD/TD B-H pair against selected comparison materials.

    include_names: if provided, only materials whose name is in this list will
    appear in the comparison plots (supports both reference and simulation exports).
    None means all reference grades are included; no simulation exports (default).

    Returns dict with:
      metrics_rd, metrics_td — curve_params() output for each direction
      core_loss              — estimate_core_loss() output
      plots                  — {bh_all, rd_analysis, td_aniso} → absolute file paths
      csv_path, json_path    — analysis outputs
      n_reference_materials  — how many reference grades were loaded
      n_simulation_materials — how many simulation exports were included
    """
    save_dir = Path(save_dir)
    save_dir.mkdir(parents=True, exist_ok=True)

    def _points(xs: list | None, ys: list | None) -> list[tuple[float, float]]:
        if xs is None or ys is None:
            return []
        pts: list[tuple[float, float]] = []
        for h, b in zip(xs, ys):
            try:
                hf = float(h)
                bf = float(b)
            except (TypeError, ValueError):
                continue
            if hf > 0 and math.isfinite(hf) and math.isfinite(bf):
                pts.append((hf, bf))
        return pts

    rd_pts = _points(rd_H, rd_B)
    td_pts = _points(td_H, td_B)

    sim_mat = Material(
        name=name,
        source_file='simulation',
        rd=rd_pts,
        td=td_pts,
        conductivity_s_m=conductivity_s_m,
        density_kg_m3=density_kg_m3,
        thickness_m=thickness_mm * 1e-3,
    )

    refs = load_reference_materials()
    sims = load_simulation_materials(save_dir)  # exports from same dir
    raw_mats: list[Material] = []

    raw_rd_pts = _points(raw_rd_H, raw_rd_B)
    raw_td_pts = _points(raw_td_H, raw_td_B)
    if len(raw_rd_pts) >= 2 and len(raw_td_pts) >= 2:
        raw_mats.append(Material(
            name=f'{name}_raw_before_correction',
            source_file='raw_before_correction',
            rd=raw_rd_pts,
            td=raw_td_pts,
            conductivity_s_m=conductivity_s_m,
            density_kg_m3=density_kg_m3,
            thickness_m=thickness_mm * 1e-3,
        ))

    if include_names is not None:
        inc = set(include_names)
        refs = [m for m in refs if m.name in inc]
        sims = [m for m in sims if m.name in inc and m.name != name]
    else:
        sims = []  # default: no simulation exports in plot (current material only)

    all_mats = refs + sims + raw_mats + [sim_mat]

    plot_all = save_dir / f'{name}_bh_all.png'
    plot_rd  = save_dir / f'{name}_bh_rd.png'
    plot_td  = save_dir / f'{name}_bh_td.png'

    make_all_bh_plot(all_mats, plot_all, highlight_name=name)
    make_rd_analysis_plot(all_mats, plot_rd, highlight_name=name)
    make_td_anisotropy_plot(all_mats, plot_td, highlight_name=name)

    metrics_rd = curve_params(sim_mat, 'RD', rd_pts)
    metrics_td = curve_params(sim_mat, 'TD', td_pts)
    core_loss  = estimate_core_loss(sim_mat)

    csv_path = save_dir / f'{name}_analysis.csv'
    _write_csv(csv_path, [metrics_rd, metrics_td])

    json_path = save_dir / f'{name}_analysis.json'
    json_payload = {
        'material': name,
        'notes': {
            'bh_curve_scope': 'First-quadrant normal BH curves from MuMax3 grain simulation + physics calibration.',
            'remanence_coercivity': 'Unavailable from normal BH curve alone; requires a full hysteresis loop.',
            'core_loss': (
                'Kh/Ke fitted by non-negative Bertotti model if measured loss points are present; '
                'Kc from classical lamination formula (requires thickness + conductivity). '
                'Without measured points, coefficients are not identifiable from BH alone.'
            ),
            'reference_grades': f'{len(refs)} GO steel grades from go_steel_data/output/',
            'raw_before_correction_overlay': bool(raw_mats),
        },
        'metrics_RD': metrics_rd,
        'metrics_TD': metrics_td,
        'core_loss': core_loss,
    }
    json_path.write_text(json.dumps(json_payload, indent=2, ensure_ascii=False), encoding='utf-8')

    return {
        'metrics_rd': metrics_rd,
        'metrics_td': metrics_td,
        'core_loss': core_loss,
        'plots': {
            'bh_all':      str(plot_all),
            'rd_analysis': str(plot_rd),
            'td_aniso':    str(plot_td),
        },
        'csv_path':  str(csv_path),
        'json_path': str(json_path),
        'n_reference_materials':  len(refs),
        'n_simulation_materials': len(sims),
        'n_raw_overlay_materials': len(raw_mats),
    }


# ── Standalone mode (processes all go_steel_data/output/ reference grades) ─

def _main_standalone() -> None:
    """Run analysis on all reference grades in go_steel_data/output/."""
    output_dir = _GODATA_OUTPUT
    if not output_dir.is_dir():
        raise SystemExit(f'Reference data not found: {output_dir}')
    materials = load_reference_materials(output_dir)
    if not materials:
        raise SystemExit(f'No GO_Steel materials found in {output_dir}')

    make_all_bh_plot(materials, output_dir / 'all_go_BH_all_grades.png')
    make_rd_analysis_plot(materials, output_dir / 'all_go_BH_RD_analysis.png')
    make_td_anisotropy_plot(materials, output_dir / 'all_go_TD_anisotropy.png')

    param_rows: list[dict[str, object]] = []
    for mat in materials:
        param_rows.append(curve_params(mat, 'RD', mat.rd))
        param_rows.append(curve_params(mat, 'TD', mat.td))
    loss_rows = [estimate_core_loss(mat) for mat in materials]

    _write_csv(output_dir / 'all_go_macro_parameters.csv',   param_rows)
    _write_csv(output_dir / 'all_go_core_loss_estimates.csv', loss_rows)

    json_payload = {
        'notes': {
            'bh_curve_scope': 'First-quadrant normal BH curves only.',
            'remanence_coercivity': 'Unavailable without a full hysteresis loop.',
            'core_loss': 'Kh/Kc/Ke not identifiable from BH alone; estimates use available loss metadata where present.',
        },
        'materials': [mat.name for mat in materials],
        'macro_parameters': param_rows,
        'core_loss_estimates': loss_rows,
    }
    (output_dir / 'all_go_analysis.json').write_text(
        json.dumps(json_payload, indent=2, ensure_ascii=False), encoding='utf-8'
    )

    print(f'Parsed {len(materials)} unique GO materials.')
    for name in ['all_go_BH_all_grades.png', 'all_go_BH_RD_analysis.png',
                 'all_go_TD_anisotropy.png', 'all_go_macro_parameters.csv',
                 'all_go_core_loss_estimates.csv', 'all_go_analysis.json']:
        print(f'  {output_dir}/{name}')


if __name__ == '__main__':
    _main_standalone()
