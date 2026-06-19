#!/usr/bin/env python3
"""
generate_amat_extra.py — Additional GO steel grades from international standards.

Adds 3 grades NOT covered by the Chinese GB/T catalog:
  1. IEC_M089_27P  — Domain-refined Hi-B, 0.27mm (IEC 60404-8-7)
                     Typical representatives: Cogent M089-27P, Voestalpine PowerCore T090-27
                     Typical B800 ≈ 1.92 T (guaranteed ≥ 1.90 T)
  2. IEC_M111_30P  — Hi-B (non-domain-refined), 0.30mm (IEC 60404-8-7)
                     Typical representatives: JFE JGH grades, Nippon HI-B 0.30mm
                     Typical B800 ≈ 1.91 T (guaranteed ≥ 1.88 T)
  3. IEC_M120_35S  — Conventional GO, 0.35mm (IEC 60404-8-7 / EN 10107)
                     Most common transformer grade globally; 29-gauge in ASTM
                     Typical representatives: AK Steel M-6 equiv., Nippon CGO 0.35mm
                     Typical B800 ≈ 1.83 T (guaranteed ≥ 1.80 T)

DATA NOTE:
  Manufacturer catalogs do not publish tabular BH curve data — they publish
  only graphical curves. These checkpoints are representative TYPICAL values
  derived from:
    • IEC 60404-8-7 guaranteed grade specifications (B800 minimums)
    • Nippon Steel catalog (D008en): μ_r=1920 → B800≈1.93T (HI-B 0.30mm);
                                      μ_r=1820 → B800≈1.83T (CGO 0.30mm)
    • General literature on GO steel magnetization curve shape
    • SMAG Handbook Version 7 (MagWeb) GO steel property overview

  Do NOT use these values as design guarantees. Obtain actual BH data from
  your material supplier for production designs.

Output: output/GO_Steel_IEC_Grades.amat  (all 3 grades in one file)
        output/GO_Steel_IEC_M089_27P.amat
        output/GO_Steel_IEC_M111_30P.amat
        output/GO_Steel_IEC_M120_35S.amat
"""
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "output")

# ── Representative typical checkpoints for IEC standard grades ───────────────
# Conventions: (H A/m, B T), must be strictly monotone, starts at (0,0)
# These represent TYPICAL values, not guaranteed minima.

CP_EXTRA = {

    # ── IEC_M089_27P ─────────────────────────────────────────────────────────
    # Domain-refined Hi-B, 0.27mm
    # IEC grade: W17/50 ≤ 0.89 W/kg, B800 ≥ 1.90T (typical ~1.92T RD)
    # Slightly softer knee than our B27R090 (Nippon HI-B, premium grade);
    # represents European/standard grade that just meets IEC M089-27P.
    "IEC_M089_27P_RD": [
        (0,0),(5,0.11),(8,0.34),(10,0.57),(12,0.83),(15,1.14),
        (18,1.37),(20,1.47),(23,1.55),(26,1.61),(30,1.65),
        (35,1.69),(38,1.72),(42,1.76),(50,1.80),(60,1.84),
        (80,1.87),(100,1.89),
        (150,1.904),(200,1.910),(300,1.913),(500,1.916),(800,1.920),
        (1000,1.922),(2000,1.948),(5000,1.972),(10000,1.988),(50000,2.015),
    ],
    "IEC_M089_27P_TD": [
        (0,0),(30,0.10),(50,0.24),(80,0.46),(100,0.65),(130,0.85),
        (160,1.00),(200,1.15),(250,1.27),(300,1.36),(400,1.45),
        (500,1.52),(600,1.57),(800,1.62),(1000,1.66),(1500,1.71),
        (2000,1.76),(3000,1.81),(5000,1.86),(10000,1.91),(50000,1.96),
    ],

    # ── IEC_M111_30P ─────────────────────────────────────────────────────────
    # Hi-B (non-domain-refined), 0.30mm
    # IEC grade: W17/50 ≤ 1.11 W/kg, B800 ≥ 1.88T (typical ~1.91T RD)
    # Comparable to Nippon HI-B 0.30mm (μ_r=1920 → B800≈1.929T from catalog)
    # and JFE JGH grades.
    "IEC_M111_30P_RD": [
        (0,0),(5,0.09),(8,0.27),(10,0.46),(12,0.70),(15,1.02),
        (18,1.24),(20,1.36),(23,1.45),(26,1.52),(30,1.57),
        (35,1.62),(40,1.66),(45,1.70),(50,1.73),(60,1.78),
        (80,1.83),(100,1.86),
        (150,1.890),(200,1.898),(300,1.901),(500,1.904),(800,1.908),
        (1000,1.912),(2000,1.938),(5000,1.964),(10000,1.982),(50000,2.008),
    ],
    "IEC_M111_30P_TD": [
        (0,0),(30,0.09),(50,0.22),(80,0.44),(100,0.62),(130,0.82),
        (160,0.97),(200,1.12),(250,1.24),(300,1.33),(400,1.43),
        (500,1.50),(600,1.55),(800,1.60),(1000,1.64),(1500,1.69),
        (2000,1.74),(3000,1.79),(5000,1.84),(10000,1.89),(50000,1.94),
    ],

    # ── IEC_M120_35S ─────────────────────────────────────────────────────────
    # Conventional GO, 0.35mm (most common thickness for distribution transformers)
    # IEC grade: W15/50 ≤ 1.20 W/kg, B800 ≥ 1.80T (typical ~1.83T RD)
    # Nippon catalog reference: CGO 0.30mm μ_r=1820 → B800=1.829T
    # 0.35mm conventional is slightly softer → typical B800 ~1.83T
    # Equivalent to: ASTM M-6 (29-gauge), Nippon CGO 0.35mm, JFE JG-0.35mm
    "IEC_M120_35S_RD": [
        (0,0),(5,0.07),(8,0.20),(10,0.35),(12,0.57),(15,0.87),
        (18,1.10),(20,1.23),(23,1.33),(26,1.42),(30,1.50),
        (35,1.55),(40,1.60),(45,1.64),(50,1.67),(55,1.70),
        (60,1.73),(80,1.79),(100,1.82),
        (150,1.831),(200,1.835),(300,1.840),(500,1.844),(800,1.849),
        (1000,1.862),(2000,1.895),(5000,1.930),(10000,1.955),(50000,1.988),
    ],
    "IEC_M120_35S_TD": [
        (0,0),(40,0.07),(60,0.17),(100,0.39),(130,0.57),(160,0.75),
        (200,0.91),(250,1.05),(300,1.15),(400,1.27),(500,1.35),
        (600,1.41),(800,1.47),(1000,1.52),(1500,1.58),
        (2000,1.63),(3000,1.69),(5000,1.75),(10000,1.81),(50000,1.87),
    ],
}

GRADE_META_EXTRA = {
    # name → (description, iec_grade, thickness_mm, conductivity_S_m)
    "IEC_M089_27P": (
        "Domain-refined Hi-B 0.27mm (IEC M089-27P typical values)",
        "M089-27P",
        0.27,
        2_000_000,
    ),
    "IEC_M111_30P": (
        "Hi-B non-domain-refined 0.30mm (IEC M111-30P typical values)",
        "M111-30P",
        0.30,
        2_000_000,
    ),
    "IEC_M120_35S": (
        "Conventional GO 0.35mm (IEC M120-35S typical values)",
        "M120-35S",
        0.35,
        2_000_000,
    ),
}


# ── AEDT .amat writers (same format as generate_amat.py) ────────────────────

def fmt_points(checkpoints):
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


def write_material_block(f, mat_name, rd_cp, td_cp, thickness_mm,
                         conductivity=2_000_000):
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
    f.write(f"\t\t$begin 'component1'\n")
    write_bh_block(f, rd_cp, indent=3)
    f.write(f"\t\t$end 'component1'\n")
    f.write(f"\t\t$begin 'component2'\n")
    write_bh_block(f, td_cp, indent=3)
    f.write(f"\t\t$end 'component2'\n")
    f.write(f"\t\t'component3'='1000'\n")
    f.write(f"\t$end 'permeability'\n")
    f.write(f"\t'conductivity'='{conductivity}'\n")
    f.write(f"\t'Tref'='22cel'\n")
    f.write(f"\t'mass_density'='7650'\n")
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


def write_amat_header(f, mat_name, desc, iec_grade, thickness_mm):
    f.write(f"# Maxwell material library — {mat_name}\n")
    f.write(f"# Description: {desc}\n")
    f.write(f"# IEC grade: {iec_grade}\n")
    f.write(f"# Thickness: {thickness_mm} mm\n")
    f.write(f"# DATA TYPE: REPRESENTATIVE TYPICAL VALUES (not manufacturer-guaranteed)\n")
    f.write(f"# Source: IEC 60404-8-7 grade specs + Nippon Steel catalog D008en\n")
    f.write(f"# Ref:  HI-B 0.30mm   μ_r@800A/m=1920 → B800≈1.929T (Nippon D008en App.III)\n")
    f.write(f"#       CGO 0.30mm    μ_r@800A/m=1820 → B800≈1.829T (Nippon D008en App.III)\n")
    f.write(f"# Verify all values against your material supplier before production use.\n")
    f.write(f"# Import: File > Import > Import Material Library (.amat)\n")
    f.write(f"\n")


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    individual_files = []
    combined_path = os.path.join(OUTPUT_DIR, "GO_Steel_IEC_Grades.amat")

    # Write individual .amat files + collect for combined
    all_blocks = []

    for grade_key, (desc, iec_grade, thickness_mm, conductivity) in GRADE_META_EXTRA.items():
        mat_name = f"GO_Steel_{grade_key}"
        rd_cp = CP_EXTRA[f"{grade_key}_RD"]
        td_cp = CP_EXTRA[f"{grade_key}_TD"]

        # Individual file
        fpath = os.path.join(OUTPUT_DIR, f"{mat_name}.amat")
        with open(fpath, "w") as f:
            write_amat_header(f, mat_name, desc, iec_grade, thickness_mm)
            write_material_block(f, mat_name, rd_cp, td_cp, thickness_mm, conductivity)
        individual_files.append(fpath)

        # Collect for combined
        import io
        buf = io.StringIO()
        write_material_block(buf, mat_name, rd_cp, td_cp, thickness_mm, conductivity)
        all_blocks.append((mat_name, buf.getvalue()))

        # Print B800 info
        b800_rd = td_cp_interp = None
        # Simple linear interpolation for B800
        for i, (h, b) in enumerate(rd_cp):
            if h >= 800:
                h0, b0 = rd_cp[i-1]
                b800_rd = b0 + (b - b0) * (800 - h0) / (h - h0)
                break
        if b800_rd is None:
            b800_rd = rd_cp[-1][1]
        print(f"  {mat_name}: B800_RD={b800_rd:.3f}T, "
              f"RD_pts={len(rd_cp)}, TD_pts={len(td_cp)}")

    # Combined file
    with open(combined_path, "w") as f:
        f.write("# Maxwell material library — GO Steel IEC Standard Grades (3 grades)\n")
        f.write("# DATA: Representative typical values from IEC 60404-8-7 specifications\n")
        f.write("# Grades: IEC_M089_27P, IEC_M111_30P, IEC_M120_35S\n")
        f.write("# NOT manufacturer-guaranteed data — verify with your supplier.\n")
        f.write("\n")
        for mat_name, block in all_blocks:
            f.write(block)
            f.write("\n")

    print(f"\n  Combined: {combined_path}")
    print(f"  Individual: {len(individual_files)} files")
    for p in individual_files:
        print(f"    {os.path.basename(p)}")
    print(f"\nDone.")


if __name__ == "__main__":
    main()
