"""
Internal physical consistency guards for B-H curves and scalar targets.

The calibrator removes numerical artifacts such as non-finite values,
negative induction, non-monotonic B-H segments, and extreme spikes. It is
not a catalog-fitting step and does not force curves toward any external
material grade.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

MU0 = 4.0 * np.pi * 1e-7
DEFAULT_MAX_B = 2.25
MIN_SLOPE = 0.5 * MU0


def _jsonable(value: Any) -> Any:
    if isinstance(value, (np.floating, np.integer)):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def _coerce_arrays(H, B) -> tuple[np.ndarray, np.ndarray, dict]:
    h = np.asarray(H, dtype=float).reshape(-1)
    b = np.asarray(B, dtype=float).reshape(-1)
    n = min(len(h), len(b))
    h = h[:n]
    b = b[:n]

    report = {
        "input_points": int(n),
        "removed_nonfinite": 0,
        "removed_nonpositive_H": 0,
        "deduplicated_H": 0,
    }
    if n == 0:
        return h, b, report

    finite = np.isfinite(h) & np.isfinite(b)
    report["removed_nonfinite"] = int(n - finite.sum())
    h = h[finite]
    b = b[finite]

    positive_h = h > 0
    report["removed_nonpositive_H"] = int(len(h) - positive_h.sum())
    h = h[positive_h]
    b = b[positive_h]

    if len(h) == 0:
        return h, b, report

    idx = np.argsort(h)
    h = h[idx]
    b = b[idx]

    unique_h, first_idx = np.unique(h, return_index=True)
    report["deduplicated_H"] = int(len(h) - len(unique_h))
    h = unique_h
    b = b[first_idx]
    return h, b, report


def calibrate_bh_curve(H, B, *, direction=None, source=None, max_B=DEFAULT_MAX_B) -> dict:
    """
    Return a physically guarded B-H curve and a sidecar-friendly report.

    Output shape:
        {"H": [...], "B": [...], "report": {...}}
    """
    h, b, report = _coerce_arrays(H, B)
    report.update({
        "direction": direction,
        "source": source,
        "max_B": float(max_B),
        "negative_B_clipped": 0,
        "over_max_B_clipped": 0,
        "nonmonotonic_segments": 0,
        "min_slope_fixes": 0,
        "fallback_used": False,
    })

    if len(h) == 0:
        h = np.array([100.0, 200.0, 500.0, 1000.0, 2000.0, 3000.0, 5000.0, 7500.0])
        b = MU0 * h
        report["fallback_used"] = True
    else:
        report["negative_B_clipped"] = int(np.sum(b < 0))
        b = np.maximum(b, 0.0)
        report["over_max_B_clipped"] = int(np.sum(b > max_B))
        b = np.clip(b, 0.0, max_B)

    if len(b) > 1:
        report["nonmonotonic_segments"] = int(np.sum(np.diff(b) < 0))
        b = np.maximum.accumulate(b)

        fixed = 0
        for i in range(1, len(b)):
            min_allowed = b[i - 1] + MIN_SLOPE * max(h[i] - h[i - 1], 0.0)
            if b[i] + 1e-15 < min_allowed and min_allowed <= max_B:
                b[i] = min_allowed
                fixed += 1
        report["min_slope_fixes"] = int(fixed)
        b = np.maximum.accumulate(np.clip(b, 0.0, max_B))

    report["output_points"] = int(len(h))
    report["B_min"] = float(np.min(b)) if len(b) else None
    report["B_max"] = float(np.max(b)) if len(b) else None
    return {"H": h.tolist(), "B": b.tolist(), "report": _jsonable(report)}


def calibrate_scalar_targets(values: dict, *, direction=None, source=None) -> dict:
    """
    Guard scalar magnetic targets without overfitting them.
    """
    limits = {
        "Hc": (0.0, 2.0e5),
        "Mr": (0.0, 2.0e6),
        "mu_max": (1.0, 1.0e6),
        "mu_r_max_total": (1.0, 1.0e6),
        "mu_r_max_diff": (1.0, 1.0e6),
    }
    out = {}
    report = {"direction": direction, "source": source, "clipped": {}, "invalid": []}
    for key, value in (values or {}).items():
        try:
            v = float(value)
        except Exception:
            v = np.nan
        if not np.isfinite(v):
            report["invalid"].append(key)
            v = 0.0
        lo, hi = limits.get(key, (0.0, 1.0e9))
        clipped = float(np.clip(abs(v), lo, hi))
        if clipped != v:
            report["clipped"][key] = {"from": v, "to": clipped}
        out[key] = clipped
    return {"values": _jsonable(out), "report": _jsonable(report)}


def validate_grain_curve(H, B, scalars: dict) -> tuple[bool, dict]:
    """
    Validate one grain curve before material-level aggregation.
    """
    raw_h, raw_b, coerce_report = _coerce_arrays(H, B)
    report = {
        "coerce": coerce_report,
        "reasons": [],
        "calibration_relative_change": None,
        "B_at_max_H": None,
    }
    if len(raw_h) < 3:
        report["reasons"].append("too_few_points")
    if len(raw_b) and np.nanmax(raw_b) > DEFAULT_MAX_B * 1.35:
        report["reasons"].append("B_spike")

    calibrated = calibrate_bh_curve(raw_h, raw_b, source="grain_validation")
    ch = np.asarray(calibrated["H"], dtype=float)
    cb = np.asarray(calibrated["B"], dtype=float)
    report["calibration"] = calibrated["report"]
    if len(ch) and len(raw_h):
        cb_on_raw = np.interp(raw_h, ch, cb)
        scale = max(float(np.nanmax(np.abs(raw_b))), 1e-9)
        change = float(np.nanmedian(np.abs(cb_on_raw - np.clip(raw_b, 0, DEFAULT_MAX_B))) / scale)
        report["calibration_relative_change"] = change
        report["B_at_max_H"] = float(cb[-1])
        if change > 0.35:
            report["reasons"].append("large_calibration_change")

    scalar_guard = calibrate_scalar_targets(scalars or {}, source="grain_validation")
    report["scalar_report"] = scalar_guard["report"]
    return len(report["reasons"]) == 0, _jsonable(report)


def calibrate_material_pair(rd_curve: dict, td_curve: dict) -> dict:
    """
    Calibrate RD/TD curves together and report anisotropy consistency.
    """
    rd = calibrate_bh_curve(
        rd_curve.get("H", []), rd_curve.get("B", []), direction="RD",
        source=rd_curve.get("source", "material_pair")
    )
    td = calibrate_bh_curve(
        td_curve.get("H", []), td_curve.get("B", []), direction="TD",
        source=td_curve.get("source", "material_pair")
    )

    common_h = np.array(sorted(set(rd["H"]) | set(td["H"])), dtype=float)
    if len(common_h) == 0:
        anomaly_ratio = 0.0
    else:
        rd_b = np.interp(common_h, rd["H"], rd["B"])
        td_b = np.interp(common_h, td["H"], td["B"])
        anomaly_ratio = float(np.mean(rd_b + 1e-12 < td_b))

    report = {
        "RD": rd["report"],
        "TD": td["report"],
        "rd_below_td_ratio": anomaly_ratio,
        "goes_rd_not_weaker_than_td_flag": anomaly_ratio > 0.5,
    }
    return {"RD": rd, "TD": td, "report": _jsonable(report)}


def correct_bh_with_reference(H: list, B: list,
                               odf_params: dict,
                               direction: str = 'RD',
                               weight_cap: float = 1.0) -> dict:
    """
    对仿真/ML 预测 B-H 曲线施加基准参考修正（差分校正法）。

    先做物理守恒检查（calibrate_bh_curve），再调用 reference_corrector
    应用多锚点加权 δ(H) 修正。

    Args:
        H, B:       B-H 数据
        odf_params: ODF 参数字典（支持 ML 格式和锚点标准格式）
        direction:  'RD' 或 'TD'
        weight_cap: 修正力度 [0=不修正, 1=完全修正]

    Returns:
        {'H': [...], 'B': [...], 'correction_applied': bool, 'report': {...}}
    """
    from reference_corrector import apply_reference_correction
    guarded = calibrate_bh_curve(H, B, direction=direction, source='ref_corrector_input')
    H_arr = np.array(guarded['H'], dtype=float)
    B_arr = np.array(guarded['B'], dtype=float)

    if weight_cap > 0.0 and len(H_arr) > 0:
        B_corr = apply_reference_correction(H_arr, B_arr, odf_params,
                                             direction=direction,
                                             weight_cap=weight_cap)
    else:
        B_corr = B_arr.copy()

    final = calibrate_bh_curve(H_arr, B_corr, direction=direction,
                                source='ref_corrector_output')
    return {
        'H':                  final['H'],
        'B':                  final['B'],
        'correction_applied': weight_cap > 0.0,
        'report':             {'input_guard': guarded['report'],
                               'output_guard': final['report']},
    }


def write_sidecar_report(path: str | Path, payload: dict) -> str:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    data = {"generated_at": datetime.now().isoformat(), **(payload or {})}
    out.write_text(json.dumps(_jsonable(data), ensure_ascii=False, indent=2), encoding="utf-8")
    return str(out)
