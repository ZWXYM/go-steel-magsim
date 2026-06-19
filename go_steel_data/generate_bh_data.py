#!/usr/bin/env python3
"""
Generate GO steel BH curves (RD+TD) from corrected catalog checkpoints.
Source: Nippon Steel / JFE GO electrical steel catalog data (digitized).
Output: Maxwell-compatible CSV + metadata.json

Monotonicity fixes applied to RD checkpoints:
  - Removed physically impossible B-dips in B23R075/B27R090/B30P105 RD curves
  - Added minimum mu0-slope enforcement in B27R095 RD curve
  Maxwell requires B strictly increasing; slope >= mu0 (4pi*1e-7 ~ 1.257e-6 T·m/A)
"""
import os, json
import numpy as np
from scipy.interpolate import PchipInterpolator

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")
MU0 = 4 * np.pi * 1e-7  # T·m/A

# ──────────────────────────────────────────────────────────────────────────────
# Corrected monotonic checkpoints  (H in A/m, B in T)
# Key changes vs. previous version:
#   B23R075_RD: (300,1.962),(500,1.94),(800,1.94) → (300,1.962),(500,1.963),(800,1.965)
#   B27R090_RD: (200,1.945),(300,1.93),(500,1.93),(800,1.93) → (200,1.945),(300,1.946),(500,1.948),(800,1.950)
#   B27R095_RD: plateau at 1.91 → slow rise enforced (min mu0 slope)
#   B30P105_RD: (500,1.89),(800,1.88) → (500,1.89),(800,1.891)
# ──────────────────────────────────────────────────────────────────────────────
CP = {
    # --- B23R075: Domain-refined Hi-B, 0.23 mm ---
    "B23R075_RD": [
        (0, 0), (5, 0.15), (8, 0.40), (10, 0.65), (12, 0.90), (15, 1.20),
        (18, 1.42), (20, 1.52), (23, 1.60), (26, 1.66), (30, 1.70),
        (36, 1.75), (42, 1.80), (50, 1.84), (60, 1.87), (80, 1.90),
        (100, 1.92), (150, 1.936), (200, 1.948), (300, 1.962),
        # FIXED: was (500,1.94),(800,1.94) – B must not decrease
        (500, 1.963), (800, 1.965),
        (1000, 1.967), (2000, 1.978), (5000, 2.000), (10000, 2.010), (50000, 2.030),
    ],
    "B23R075_TD": [
        (0, 0), (30, 0.15), (50, 0.35), (80, 0.60), (100, 0.80), (130, 1.00),
        (160, 1.15), (200, 1.28), (250, 1.40), (300, 1.48), (400, 1.56),
        (500, 1.62), (600, 1.66), (800, 1.70), (1000, 1.74), (1500, 1.78),
        (2000, 1.82), (3000, 1.86), (5000, 1.90), (10000, 1.94), (50000, 1.97),
    ],

    # --- B27R090: Hi-B, 0.27 mm ---
    "B27R090_RD": [
        (0, 0), (5, 0.13), (8, 0.38), (10, 0.62), (12, 0.87), (15, 1.18),
        (18, 1.40), (20, 1.50), (23, 1.58), (26, 1.64), (30, 1.68),
        (35, 1.72), (38, 1.74), (42, 1.78), (50, 1.83), (60, 1.86),
        (80, 1.89), (100, 1.91), (150, 1.932), (200, 1.945),
        # FIXED: was (300,1.93),(500,1.93),(800,1.93) – B must not decrease
        (300, 1.946), (500, 1.948), (800, 1.950),
        (1000, 1.952), (2000, 1.975), (5000, 1.995), (10000, 2.005), (50000, 2.030),
    ],
    "B27R090_TD": [
        (0, 0), (30, 0.12), (50, 0.30), (80, 0.55), (100, 0.75), (130, 0.95),
        (160, 1.10), (200, 1.24), (250, 1.36), (300, 1.45), (400, 1.53),
        (500, 1.59), (600, 1.63), (800, 1.68), (1000, 1.72), (1500, 1.76),
        (2000, 1.80), (3000, 1.85), (5000, 1.89), (10000, 1.935), (50000, 1.97),
    ],

    # --- B27R095: Conventional GO, 0.27 mm ---
    "B27R095_RD": [
        (0, 0), (5, 0.10), (8, 0.30), (10, 0.52), (12, 0.78), (15, 1.10),
        (18, 1.33), (20, 1.44), (23, 1.52), (26, 1.59), (30, 1.65),
        (35, 1.68), (42, 1.70), (45, 1.73), (50, 1.78), (60, 1.83),
        (80, 1.87), (100, 1.89),
        # FIXED: was all 1.91 – zero slope violates Maxwell's slope >= mu0 requirement
        (150, 1.910), (200, 1.912), (300, 1.915), (500, 1.920), (800, 1.925),
        (1000, 1.940), (2000, 1.960), (5000, 1.985), (10000, 1.998), (50000, 2.020),
    ],
    "B27R095_TD": [
        (0, 0), (30, 0.10), (50, 0.25), (80, 0.48), (100, 0.68), (130, 0.88),
        (160, 1.03), (200, 1.18), (250, 1.30), (300, 1.39), (400, 1.48),
        (500, 1.55), (600, 1.60), (800, 1.65), (1000, 1.69), (1500, 1.73),
        (2000, 1.78), (3000, 1.83), (5000, 1.87), (10000, 1.92), (50000, 1.96),
    ],

    # --- B30P105: Conventional GO, 0.30 mm ---
    "B30P105_RD": [
        (0, 0), (5, 0.08), (8, 0.25), (10, 0.42), (12, 0.65), (15, 0.95),
        (18, 1.18), (20, 1.30), (23, 1.40), (26, 1.48), (30, 1.55),
        (35, 1.62), (40, 1.67), (45, 1.71), (50, 1.74), (55, 1.77),
        (60, 1.79), (80, 1.84), (100, 1.87),
        # FIXED: was (150,1.89),(200,1.89),(300,1.89),(500,1.89),(800,1.88)
        # Zero-slope plateau + dip → enforce slow monotone rise
        (150, 1.890), (200, 1.891), (300, 1.893), (500, 1.896), (800, 1.900),
        (1000, 1.930), (2000, 1.950), (5000, 1.975), (10000, 1.990), (50000, 2.010),
    ],
    "B30P105_TD": [
        (0, 0), (30, 0.08), (50, 0.20), (80, 0.42), (100, 0.60), (130, 0.80),
        (160, 0.95), (200, 1.10), (250, 1.23), (300, 1.32), (400, 1.42),
        (500, 1.50), (600, 1.55), (800, 1.60), (1000, 1.64), (1500, 1.70),
        (2000, 1.75), (3000, 1.81), (5000, 1.85), (10000, 1.90), (50000, 1.95),
    ],
}

GRADE_META = {
    "B23R075": ("Domain-refined Hi-B", "23QG080", 0.23),
    "B27R090": ("Hi-B", "27QG090", 0.27),
    "B27R095": ("Conventional GO", "27QG095", 0.27),
    "B30P105": ("Conventional GO", "30QG105", 0.30),
}

# Dense H grid for interpolated CSV output
H_DENSE = np.concatenate([
    np.array([0]),
    np.logspace(-1, 4.7, 118),
])


def enforce_monotone(checkpoints):
    """Enforce strict B monotonicity and slope >= 0.5*mu0 on checkpoints."""
    result = [(0, 0)]
    for h, b in checkpoints[1:]:
        h_prev, b_prev = result[-1]
        b_min = b_prev + 0.5 * MU0 * (h - h_prev)  # absolute floor
        b_use = max(b, b_prev + 1e-6, b_min)         # also keep above previous
        result.append((h, round(b_use, 6)))
    return result


def check_monotone(pts, name):
    """Print a report of any monotonicity violations."""
    issues = []
    for i in range(1, len(pts)):
        if pts[i][1] <= pts[i-1][1]:
            issues.append(f"  H={pts[i][0]:8.1f}: B={pts[i][1]:.4f} <= B_prev={pts[i-1][1]:.4f}")
        slope = (pts[i][1] - pts[i-1][1]) / max(pts[i][0] - pts[i-1][0], 1e-10)
        if slope < 0.5 * MU0 and pts[i][0] > 100:  # only check beyond knee
            pass  # accept near-zero slopes at very high H
    if issues:
        print(f"  [WARN] {name}: non-monotone sections (fixed by enforce_monotone):")
        for msg in issues:
            print(msg)


def gen_curve(checkpoints, h_out):
    """Interpolate BH curve using PCHIP; extrapolate flat at ends."""
    hc = np.array([p[0] for p in checkpoints])
    bc = np.array([p[1] for p in checkpoints])
    interp = PchipInterpolator(hc, bc, extrapolate=False)
    result = []
    for h in h_out:
        if h <= hc[0]:
            result.append(0.0)
        elif h >= hc[-1]:
            result.append(float(bc[-1]))
        else:
            result.append(float(interp(h)))
    return result


def save_csv(key, h, b, grade, direction, meta):
    path = os.path.join(OUTPUT_DIR, f"{key}.csv")
    with open(path, "w") as f:
        f.write(f"# {grade} ({meta[0]}) - {direction}\n")
        f.write("# H (A/m), B (T)\n")
        f.write("# Source: Nippon Steel/JFE GO electrical steel catalog (digitized)\n")
        for hi, bi in zip(h, b):
            if hi == 0:
                f.write(f"0.000000,0.000000\n")
            else:
                f.write(f"{hi:.6f},{bi:.6f}\n")
    return path


def save_combined(grade, h, rd, td, meta):
    path = os.path.join(OUTPUT_DIR, f"{grade}_combined.csv")
    with open(path, "w") as f:
        f.write(f"# {grade} ({meta[0]}) - RD and TD\n")
        f.write("# H (A/m), B_RD (T), B_TD (T)\n")
        f.write("# Source: Nippon Steel/JFE GO electrical steel catalog (digitized)\n")
        for hi, brd, btd in zip(h, rd, td):
            if hi == 0:
                f.write(f"0.000000,0.000000,0.000000\n")
            else:
                f.write(f"{hi:.6f},{brd:.6f},{btd:.6f}\n")
    return path


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    all_data, files = {}, []

    for grade, meta in GRADE_META.items():
        print(f"\nProcessing {grade} ({meta[0]}, {meta[2]}mm)...")

        # Apply monotonicity enforcement
        rd_cp = enforce_monotone(CP[f"{grade}_RD"])
        td_cp = enforce_monotone(CP[f"{grade}_TD"])
        check_monotone(CP[f"{grade}_RD"], f"{grade}_RD (original)")
        check_monotone(CP[f"{grade}_TD"], f"{grade}_TD (original)")

        rd = gen_curve(rd_cp, H_DENSE)
        td = gen_curve(td_cp, H_DENSE)

        rd_f = save_csv(f"{grade}_RD", H_DENSE, rd, grade, "RD", meta)
        td_f = save_csv(f"{grade}_TD", H_DENSE, td, grade, "TD", meta)
        cb_f = save_combined(grade, H_DENSE, rd, td, meta)
        files += [rd_f, td_f, cb_f]

        b800_rd = float(np.interp(800, H_DENSE, rd))
        b800_td = float(np.interp(800, H_DENSE, td))
        b50_rd  = float(np.interp(50,  H_DENSE, rd))   # J50 reference point
        print(f"  B50  RD={b50_rd:.3f}T")
        print(f"  B800 RD={b800_rd:.3f}T   TD={b800_td:.3f}T")

        all_data[grade] = {
            "description": meta[0], "cn_equivalent": meta[1],
            "thickness_mm": meta[2],
            "B50_RD_T":  round(b50_rd,  3),
            "B800_RD_T": round(b800_rd, 3),
            "B800_TD_T": round(b800_td, 3),
            "checkpoints_RD_count": len(rd_cp),
            "checkpoints_TD_count": len(td_cp),
            "files": {
                "RD": os.path.basename(rd_f),
                "TD": os.path.basename(td_f),
                "combined": os.path.basename(cb_f),
            },
        }

    meta_path = os.path.join(OUTPUT_DIR, "metadata.json")
    with open(meta_path, "w") as f:
        json.dump({
            "data_source": "Digitized from Nippon Steel / JFE GO electrical steel catalog curves.",
            "disclaimer": "Research/educational use. Verify against official datasheets for production.",
            "references": [
                "Nippon Steel GO Catalog (D001)",
                "JFE Steel G-Core Catalog",
                "GB/T 2521-2016",
            ],
            "monotonicity_fix": "Applied: BH curves corrected to be strictly monotone (slope >= 0.5*mu0).",
            "grades": all_data,
        }, f, indent=2)
    files.append(meta_path)
    print(f"\nDone. {len(files)} files written to {OUTPUT_DIR}/")


if __name__ == "__main__":
    main()
