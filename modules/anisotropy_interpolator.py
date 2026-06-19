"""
Elliptic permeability interpolation for GO electrical steel directions.

The model uses calibrated RD/TD B-H curves to generate a 0-180 degree
directional response for visualization and analysis. It does not alter raw
MuMax3 grain outputs and it is not exported into Maxwell .amat files.
"""
from __future__ import annotations

import numpy as np

from physics_calibrator import MU0, calibrate_bh_curve

STANDARD_H_POINTS = [100, 200, 500, 1000, 2000, 3000, 5000, 7500]
DEFAULT_ANGLES_DEG = list(range(0, 181, 5))
MODEL_NAME = "elliptic_permeability_polar_model"


def _interp_to_grid(curve: dict, h_grid, direction: str) -> tuple[np.ndarray, dict]:
    guarded = calibrate_bh_curve(
        curve.get("H", []), curve.get("B", []), direction=direction,
        source=curve.get("source", "anisotropy_interpolator")
    )
    h = np.asarray(guarded["H"], dtype=float)
    b = np.asarray(guarded["B"], dtype=float)
    h_grid = np.asarray(h_grid, dtype=float)
    b_grid = np.interp(h_grid, h, b, left=b[0], right=b[-1])
    return b_grid, guarded["report"]


def _summary_for_curve(angle, h_grid, b_grid, mu_r):
    def at_h(values, h):
        return float(np.interp(float(h), h_grid, values))

    return {
        "angle_deg": float(angle),
        "B800_T": at_h(b_grid, 800),
        "B1000_T": at_h(b_grid, 1000),
        "B2000_T": at_h(b_grid, 2000),
        "mu800": at_h(mu_r, 800),
        "mu_max": float(np.nanmax(mu_r)) if len(mu_r) else 1.0,
    }


def _transfer_matrix_for_angle(angle: float) -> dict:
    theta = np.radians(float(angle))
    c = float(np.cos(theta))
    s = float(np.sin(theta))
    return {
        "angle_deg": float(angle),
        "rotation_matrix": [
            [c, -s],
            [s, c],
        ],
        "rd_td_projection_weights": [c * c, s * s],
        "basis": "RD/TD",
        "note": "weights describe axis projection; B(theta) is computed by the elliptic permeability equation",
    }


def interpolate_full_direction(
    rd_curve: dict,
    td_curve: dict,
    *,
    angles_deg: list[int | float] | None = None,
    H_points: list[int | float] | None = None,
    max_B: float = 2.25,
) -> dict:
    """
    Generate full-direction B-H and mu_r curves from calibrated RD/TD curves.
    """
    h_grid = np.asarray(H_points or STANDARD_H_POINTS, dtype=float)
    angles = [float(a) for a in (angles_deg or DEFAULT_ANGLES_DEG)]

    rd_b, rd_report = _interp_to_grid(rd_curve, h_grid, "RD")
    td_b, td_report = _interp_to_grid(td_curve, h_grid, "TD")

    h_safe = np.maximum(h_grid, 1e-9)
    mu_rd = np.maximum(rd_b / (MU0 * h_safe), 1.0)
    mu_td = np.maximum(td_b / (MU0 * h_safe), 1.0)

    curves = {}
    summary = []
    curve_reports = {}
    transfer_matrices = {}
    eps = 1e-12

    for angle in angles:
        theta = np.radians(angle)
        denom = np.sqrt((mu_td * np.cos(theta)) ** 2 + (mu_rd * np.sin(theta)) ** 2)
        denom = np.maximum(denom, eps)
        mu_theta = (mu_rd * mu_td) / denom
        b_theta = MU0 * mu_theta * h_grid

        calibrated = calibrate_bh_curve(
            h_grid, b_theta, direction=f"{angle:g}deg",
            source=MODEL_NAME, max_B=max_B
        )
        b_cal = np.asarray(calibrated["B"], dtype=float)
        mu_cal = np.maximum(b_cal / (MU0 * h_safe), 1.0)
        key = str(int(angle)) if float(angle).is_integer() else f"{angle:g}"
        curves[key] = {
            "H": [float(v) for v in h_grid],
            "B": [float(v) for v in b_cal],
            "mu_r": [float(v) for v in mu_cal],
        }
        summary.append(_summary_for_curve(angle, h_grid, b_cal, mu_cal))
        curve_reports[key] = calibrated["report"]
        transfer_matrices[key] = _transfer_matrix_for_angle(angle)

    return {
        "model": MODEL_NAME,
        "transfer_matrix_model": "RD/TD rotation-projection matrix plus elliptic permeability scalar transfer",
        "H_points": [float(v) for v in h_grid],
        "angles_deg": angles,
        "curves": curves,
        "summary": summary,
        "transfer_matrices": transfer_matrices,
        "calibration_report": {
            "RD": rd_report,
            "TD": td_report,
            "interpolated_curves": curve_reports,
        },
    }
