#!/usr/bin/env python3
"""
generate_amat_no.py — Wuhan Steel (武钢有限) Non-Oriented Electrical Steel AEDT .amat files

Data source: Wuhan Iron & Steel Co., Ltd. "Cold-rolled Non-Oriented Electrical Steel" catalog
             (Baosteel Group subsidiary)
BH data: Extracted from tabular Hmax/Bmax data tables (AC peak values, Epstein 25cm frame)
Typical properties from catalog page 014 (电磁性能典型值).

Grades covered:
  35WW230 — 0.35mm, P15/50≤2.30 W/kg (typical 2.06), J2500=1.57T, density=7600 kg/m³
  35WW250 — 0.35mm, P15/50≤2.50 W/kg (typical 2.20), J2500=1.58T, density=7600 kg/m³
  35WW270 — 0.35mm, P15/50≤2.70 W/kg (typical 2.33), J2500=1.58T, density=7600 kg/m³
  50WW250 — 0.50mm, P15/50≤2.50 W/kg (typical 2.26), J2500=1.59T, density=7600 kg/m³
  50WW270 — 0.50mm, P15/50≤2.70 W/kg (typical 2.45), J2500=1.59T, density=7600 kg/m³
  50WW470 — 0.50mm, P15/50≤4.70 W/kg (typical 3.15), J2500=1.65T, density=7650 kg/m³
  50WH470 — 0.50mm high-efficiency motor grade, J2500=1.65T, density=7700 kg/m³

Notes:
  - Non-oriented (NO) steel is isotropic: same BH curve used for RD and TD directions
    (component1 = component2 in AEDT AnisoProperty block)
  - Data is Hmax/Bmax from AC Epstein measurements; suitable as upper-envelope BH curve
  - Extrapolation added above H=10000 A/m approaching saturation (~2.0 T)
  - Conductivity estimated at 2,000,000 S/m (typical for ~3% Si NO electrical steel)
  - Import: File > Import > Import Material Library (.amat) in Ansys Maxwell/AEDT
"""
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "output")

# ── BH checkpoints (Hmax [A/m], Bmax [T]) ────────────────────────────────────
# Source: Wuhan Steel NO electrical steel catalog — Hmax/Bmax tables
# (0,0) added at origin; two extrapolation points added above H=10000.
CP_NO = {

    # ── 35WW230 ───────────────────────────────────────────────────────────────
    "35WW230": [
        (0,0),
        (20,0.149),(30,0.353),(40,0.573),(50,0.748),(60,0.875),
        (70,0.968),(80,1.038),(90,1.091),(100,1.132),
        (151,1.252),(201,1.307),(302,1.363),(402,1.394),
        (502,1.415),(603,1.431),(703,1.445),(804,1.457),(1004,1.476),
        (1508,1.514),(2011,1.544),(3017,1.591),(4020,1.631),
        (5026,1.664),(6026,1.694),(7034,1.720),(8040,1.744),
        (9048,1.765),(10052,1.785),
        (20000,1.808),(50000,1.840),   # extrapolation to saturation
    ],

    # ── 35WW250 ───────────────────────────────────────────────────────────────
    "35WW250": [
        (0,0),(10,0.039),
        (20,0.100),(30,0.219),(40,0.390),(50,0.562),(60,0.707),
        (70,0.822),(80,0.912),(90,0.983),(100,1.040),
        (151,1.205),(201,1.281),(302,1.353),(402,1.388),
        (502,1.412),(603,1.429),(703,1.444),(803,1.457),(1005,1.477),
        (1508,1.516),(2011,1.546),(3017,1.593),(4020,1.632),
        (5025,1.664),(6030,1.693),(7036,1.719),(8042,1.742),
        (9046,1.763),(10050,1.782),
        (20000,1.805),(50000,1.837),   # extrapolation to saturation
    ],

    # ── 35WW270 ───────────────────────────────────────────────────────────────
    "35WW270": [
        (0,0),
        (20,0.083),(30,0.175),(40,0.319),(50,0.478),(60,0.619),
        (70,0.734),(80,0.828),(90,0.904),(100,0.967),
        (151,1.157),(201,1.249),(302,1.333),(402,1.375),
        (502,1.402),(603,1.422),(703,1.437),(803,1.450),(1004,1.471),
        (1508,1.511),(2011,1.541),(3017,1.588),(4020,1.628),
        (5025,1.661),(6031,1.689),(7037,1.715),(8041,1.739),
        (9047,1.760),(10051,1.780),
        (20000,1.803),(50000,1.835),   # extrapolation to saturation
    ],

    # ── 50WW250 ───────────────────────────────────────────────────────────────
    "50WW250": [
        (0,0),
        (20,0.162),(30,0.375),(40,0.580),(50,0.740),(60,0.859),
        (70,0.949),(80,1.017),(90,1.071),(100,1.115),
        (151,1.239),(201,1.298),(302,1.356),(402,1.388),
        (502,1.410),(603,1.428),(703,1.442),(804,1.453),(1005,1.474),
        (1508,1.514),(2011,1.544),(3018,1.593),(4021,1.633),
        (5027,1.667),(6033,1.696),(7038,1.721),(8044,1.745),
        (9051,1.766),(10057,1.785),
        (20000,1.808),(50000,1.840),   # extrapolation to saturation
    ],

    # ── 50WW270 ───────────────────────────────────────────────────────────────
    "50WW270": [
        (0,0),
        (20,0.163),(30,0.360),(40,0.563),(50,0.728),(60,0.852),
        (70,0.943),(80,1.015),(90,1.070),(100,1.115),
        (151,1.242),(201,1.302),(302,1.360),(402,1.392),
        (502,1.414),(603,1.430),(703,1.444),(803,1.457),(1004,1.476),
        (1508,1.517),(2011,1.547),(3017,1.596),(4019,1.635),
        (5024,1.668),(6031,1.697),(7035,1.722),(8051,1.746),
        (9044,1.767),(10055,1.786),
        (20000,1.809),(50000,1.841),   # extrapolation to saturation
    ],

    # ── 50WW470 ───────────────────────────────────────────────────────────────
    "50WW470": [
        (0,0),
        (20,0.083),(30,0.214),(40,0.396),(50,0.564),(60,0.699),
        (70,0.807),(80,0.892),(90,0.960),(100,1.015),
        (151,1.188),(201,1.275),(302,1.365),(402,1.412),
        (502,1.442),(603,1.466),(703,1.484),(803,1.499),(1005,1.524),
        (1508,1.567),(2011,1.600),(3017,1.647),(4020,1.684),
        (5025,1.716),(6030,1.742),(7036,1.765),(8041,1.787),
        (9047,1.807),(10052,1.826),
        (20000,1.850),(50000,1.882),   # extrapolation to saturation
    ],

    # ── 50WH470 ───────────────────────────────────────────────────────────────
    # High-efficiency motor grade (WH = high-efficiency series)
    "50WH470": [
        (0,0),
        (20,0.069),(30,0.153),(40,0.282),(50,0.430),(60,0.570),
        (70,0.690),(80,0.793),(90,0.879),(100,0.952),
        (151,1.185),(201,1.294),(302,1.390),(402,1.433),
        (502,1.462),(603,1.483),(703,1.499),(804,1.513),(1004,1.537),
        (1508,1.579),(2011,1.609),(3017,1.658),(4020,1.696),
        (5026,1.727),(6031,1.754),(7036,1.778),(8043,1.800),
        (9057,1.820),(10055,1.839),
        (20000,1.863),(50000,1.896),   # extrapolation to saturation
    ],
}

# Grade metadata: description, thickness_mm, density_kg_m3
GRADE_META_NO = {
    "35WW230": ("Wuhan NO steel, 0.35mm, P15/50≤2.30 W/kg, J2500=1.57T", 0.35, 7600),
    "35WW250": ("Wuhan NO steel, 0.35mm, P15/50≤2.50 W/kg, J2500=1.58T", 0.35, 7600),
    "35WW270": ("Wuhan NO steel, 0.35mm, P15/50≤2.70 W/kg, J2500=1.58T", 0.35, 7600),
    "50WW250": ("Wuhan NO steel, 0.50mm, P15/50≤2.50 W/kg, J2500=1.59T", 0.50, 7600),
    "50WW270": ("Wuhan NO steel, 0.50mm, P15/50≤2.70 W/kg, J2500=1.59T", 0.50, 7600),
    "50WW470": ("Wuhan NO steel, 0.50mm, P15/50≤4.70 W/kg, J2500=1.65T", 0.50, 7650),
    "50WH470": ("Wuhan NO steel high-efficiency motor grade, 0.50mm, J2500=1.65T", 0.50, 7700),
}


# ── AEDT .amat format writers ─────────────────────────────────────────────────

def fmt_points(checkpoints):
    """Format (H,B) list as Maxwell Points[N: h1,b1, h2,b2, ...] string."""
    vals = []
    for h, b in checkpoints:
        vals.append(f"{h:g}")
        vals.append(f"{b:g}")
    return f"Points[{len(vals)}: {', '.join(vals)}]"


def write_bh_block(f, checkpoints, indent):
    t = "\t" * indent
    f.write(f"{t}'property_type'='nonlinear'\n")
    f.write(f"{t}'BTypeForSingleCurve'='Normal'\n")
    f.write(f"{t}'HUnit'='A_per_meter'\n")
    f.write(f"{t}'BUnit'='tesla'\n")
    f.write(f"{t}'IsTemperatureDependent'=false\n")
    f.write(f"{t}$begin 'BHCoordinates'\n")
    f.write(f"{t}\t'DimUnits'('')\n")
    f.write(f"{t}\t{fmt_points(checkpoints)}\n")
    f.write(f"{t}\t$begin 'Temperatures'\n")
    f.write(f"{t}\t$end 'Temperatures'\n")
    f.write(f"{t}$end 'BHCoordinates'\n")


def write_material_block(f, mat_name, bh_cp, thickness_mm, density,
                         conductivity=2_000_000):
    """Write one material block. For NO steel: same BH curve for component1 & component2."""
    cut_depth_m = thickness_mm / 1000.0
    f.write(f"$begin '{mat_name}'\n")
    f.write(f"\t'CoordinateSystemType'='Cartesian'\n")
    f.write(f"\t'BulkOrSurface'='Bulk'\n")
    f.write(f"\t$begin 'PhysicsTypes'\n")
    f.write(f"\t\t'set'('Electromagnetic')\n")
    f.write(f"\t$end 'PhysicsTypes'\n")
    f.write(f"\t$begin 'AttachedData'\n")
    f.write(f"\t$end 'AttachedData'\n")
    f.write(f"\t$begin 'ModifierData'\n")
    f.write(f"\t\t$begin 'ThermalModifierData'\n")
    f.write(f"\t\t\t'modifier_data'='no_modifier'\n")
    f.write(f"\t\t$end 'ThermalModifierData'\n")
    f.write(f"\t$end 'ModifierData'\n")
    f.write(f"\t'permittivity'='1'\n")
    f.write(f"\t$begin 'permeability'\n")
    f.write(f"\t\t'property_type'='AnisoProperty'\n")
    # component1 = rolling direction (RD) — for isotropic NO steel same as TD
    f.write(f"\t\t$begin 'component1'\n")
    write_bh_block(f, bh_cp, indent=3)
    f.write(f"\t\t$end 'component1'\n")
    # component2 = transverse direction (TD) — identical to RD for NO steel
    f.write(f"\t\t$begin 'component2'\n")
    write_bh_block(f, bh_cp, indent=3)
    f.write(f"\t\t$end 'component2'\n")
    # component3 = normal direction (ND) — linear, μr=1000 (Maxwell convention)
    f.write(f"\t\t'component3'='1000'\n")
    f.write(f"\t$end 'permeability'\n")
    f.write(f"\t'conductivity'='{conductivity}'\n")
    f.write(f"\t'Tref'='22cel'\n")
    f.write(f"\t'mass_density'='{density}'\n")
    f.write(f"\t$begin 'core_loss_type'\n")
    f.write(f"\t\t'property_type'='ChoiceProperty'\n")
    f.write(f"\t\t'Choice'='Electrical Steel'\n")
    f.write(f"\t$end 'core_loss_type'\n")
    f.write(f"\t'core_loss_kh'='0'\n")
    f.write(f"\t'core_loss_kc'='0'\n")
    f.write(f"\t'core_loss_ke'='0'\n")
    f.write(f"\t'core_loss_kdc'='0'\n")
    f.write(f"\t'core_loss_equiv_cut_depth'='{cut_depth_m}meter'\n")
    f.write(f"\t'SolveInside'='1'\n")
    f.write(f"$end '{mat_name}'\n")


def check_monotone(cp, name):
    """Verify strict B monotonicity; print warnings if violated."""
    issues = []
    for i in range(1, len(cp)):
        if cp[i][1] <= cp[i-1][1]:
            issues.append(f"  H={cp[i][0]}: B={cp[i][1]} <= B_prev={cp[i-1][1]}")
    if issues:
        print(f"  [WARN] {name}: non-monotone detected:")
        for msg in issues:
            print(msg)
    return len(issues) == 0


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    all_blocks = []
    individual_files = []

    for grade, (desc, thickness_mm, density) in GRADE_META_NO.items():
        mat_name = f"NO_Steel_{grade}"
        bh_cp = CP_NO[grade]

        ok = check_monotone(bh_cp, grade)
        b_at_800 = None
        for i, (h, b) in enumerate(bh_cp):
            if h >= 800:
                h0, b0 = bh_cp[i-1]
                b_at_800 = b0 + (b - b0) * (800 - h0) / (h - h0)
                break
        b_at_2500 = None
        for i, (h, b) in enumerate(bh_cp):
            if h >= 2500:
                h0, b0 = bh_cp[i-1]
                b_at_2500 = b0 + (b - b0) * (2500 - h0) / (h - h0)
                break

        print(f"  {mat_name}: pts={len(bh_cp)}, "
              f"B800={b_at_800:.3f}T, B2500={b_at_2500:.3f}T, "
              f"mono={'OK' if ok else 'WARN'}")

        # Individual .amat
        fpath = os.path.join(OUTPUT_DIR, f"{mat_name}.amat")
        with open(fpath, "w", encoding="utf-8") as f:
            f.write(f"# Maxwell material library — {mat_name}\n")
            f.write(f"# Description: {desc}\n")
            f.write(f"# Source: Wuhan Iron & Steel Co. (武钢有限) NO Electrical Steel Catalog\n")
            f.write(f"# BH data: Hmax/Bmax from Epstein frame AC measurements (25cm)\n")
            f.write(f"# Thickness: {thickness_mm} mm | Density: {density} kg/m³\n")
            f.write(f"# Note: NO steel is isotropic — RD=TD BH curve.\n")
            f.write(f"# Import: File > Import > Import Material Library (.amat)\n\n")
            write_material_block(f, mat_name, bh_cp, thickness_mm, density)
        individual_files.append(fpath)

        # Collect for combined
        import io
        buf = io.StringIO()
        write_material_block(buf, mat_name, bh_cp, thickness_mm, density)
        all_blocks.append((mat_name, buf.getvalue()))

    # Combined file
    combined_path = os.path.join(OUTPUT_DIR, "NO_Steel_Wuhan_All.amat")
    with open(combined_path, "w", encoding="utf-8") as f:
        f.write("# Maxwell material library — Wuhan NO Electrical Steel (武钢无取向电工钢)\n")
        f.write("# Source: Wuhan Iron & Steel Co., Ltd. (Baosteel Group)\n")
        f.write("# Grades: 35WW230/250/270, 50WW250/270/470, 50WH470\n")
        f.write("# BH data: Hmax/Bmax from Epstein frame AC measurements\n\n")
        for mat_name, block in all_blocks:
            f.write(block)
            f.write("\n")

    print(f"\n  Combined: {os.path.basename(combined_path)}")
    print(f"  Individual: {len(individual_files)} files")
    print("Done.")


if __name__ == "__main__":
    main()
