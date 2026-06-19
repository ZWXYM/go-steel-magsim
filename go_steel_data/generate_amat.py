#!/usr/bin/env python3
"""
Generate Ansys Maxwell-compatible .amat material files for GO electrical steel.

Format: AEDT scripting format ($begin/$end blocks) — the only format Maxwell
can import directly via File > Import Materials.

Permeability model: Anisotropic nonlinear
  component1 = Rolling Direction  (RD, X-axis)
  component2 = Transverse Direction (TD, Y-axis)
  component3 = Normal Direction  (ND, Z-axis) — linear mu_r = 1000

Core loss: Electrical Steel model (Kh/Kc/Ke placeholders — update with
           manufacturer loss coefficients for production use).

Usage:
  python generate_amat.py         # also runs generate_bh_data.py first
  Then in Maxwell: File > Import > Import Material Library > select .amat
"""
import os
import sys
import subprocess

# Run BH data generator first if checkpoints have changed
SCRIPT_DIR = os.path.dirname(__file__)
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "output")

# ──────────────────────────────────────────────────────────────────────────────
# Grade definitions  (must match generate_bh_data.py)
# ──────────────────────────────────────────────────────────────────────────────
GRADE_META = {
    "B23R075": ("Domain-refined Hi-B", "23QG080", 0.23),
    "B27R090": ("Hi-B",                "27QG090", 0.27),
    "B27R095": ("Conventional GO",     "27QG095", 0.27),
    "B30P105": ("Conventional GO",     "30QG105", 0.30),
}

# Corrected, monotonic checkpoints (identical to generate_bh_data.py)
CP = {
    "B23R075_RD": [
        (0,0),(5,0.15),(8,0.40),(10,0.65),(12,0.90),(15,1.20),(18,1.42),
        (20,1.52),(23,1.60),(26,1.66),(30,1.70),(36,1.75),(42,1.80),(50,1.84),
        (60,1.87),(80,1.90),(100,1.92),(150,1.936),(200,1.948),(300,1.962),
        (500,1.963),(800,1.965),(1000,1.967),(2000,1.978),(5000,2.000),
        (10000,2.010),(50000,2.030),
    ],
    "B23R075_TD": [
        (0,0),(30,0.15),(50,0.35),(80,0.60),(100,0.80),(130,1.00),(160,1.15),
        (200,1.28),(250,1.40),(300,1.48),(400,1.56),(500,1.62),(600,1.66),
        (800,1.70),(1000,1.74),(1500,1.78),(2000,1.82),(3000,1.86),
        (5000,1.90),(10000,1.94),(50000,1.97),
    ],
    "B27R090_RD": [
        (0,0),(5,0.13),(8,0.38),(10,0.62),(12,0.87),(15,1.18),(18,1.40),
        (20,1.50),(23,1.58),(26,1.64),(30,1.68),(35,1.72),(38,1.74),(42,1.78),
        (50,1.83),(60,1.86),(80,1.89),(100,1.91),(150,1.932),(200,1.945),
        (300,1.946),(500,1.948),(800,1.950),(1000,1.952),(2000,1.975),
        (5000,1.995),(10000,2.005),(50000,2.030),
    ],
    "B27R090_TD": [
        (0,0),(30,0.12),(50,0.30),(80,0.55),(100,0.75),(130,0.95),(160,1.10),
        (200,1.24),(250,1.36),(300,1.45),(400,1.53),(500,1.59),(600,1.63),
        (800,1.68),(1000,1.72),(1500,1.76),(2000,1.80),(3000,1.85),
        (5000,1.89),(10000,1.935),(50000,1.97),
    ],
    "B27R095_RD": [
        (0,0),(5,0.10),(8,0.30),(10,0.52),(12,0.78),(15,1.10),(18,1.33),
        (20,1.44),(23,1.52),(26,1.59),(30,1.65),(35,1.68),(42,1.70),(45,1.73),
        (50,1.78),(60,1.83),(80,1.87),(100,1.89),(150,1.910),(200,1.912),
        (300,1.915),(500,1.920),(800,1.925),(1000,1.940),(2000,1.960),
        (5000,1.985),(10000,1.998),(50000,2.020),
    ],
    "B27R095_TD": [
        (0,0),(30,0.10),(50,0.25),(80,0.48),(100,0.68),(130,0.88),(160,1.03),
        (200,1.18),(250,1.30),(300,1.39),(400,1.48),(500,1.55),(600,1.60),
        (800,1.65),(1000,1.69),(1500,1.73),(2000,1.78),(3000,1.83),
        (5000,1.87),(10000,1.92),(50000,1.96),
    ],
    "B30P105_RD": [
        (0,0),(5,0.08),(8,0.25),(10,0.42),(12,0.65),(15,0.95),(18,1.18),
        (20,1.30),(23,1.40),(26,1.48),(30,1.55),(35,1.62),(40,1.67),(45,1.71),
        (50,1.74),(55,1.77),(60,1.79),(80,1.84),(100,1.87),(150,1.890),
        (200,1.891),(300,1.893),(500,1.896),(800,1.900),(1000,1.930),(2000,1.950),
        (5000,1.975),(10000,1.990),(50000,2.010),
    ],
    "B30P105_TD": [
        (0,0),(30,0.08),(50,0.20),(80,0.42),(100,0.60),(130,0.80),(160,0.95),
        (200,1.10),(250,1.23),(300,1.32),(400,1.42),(500,1.50),(600,1.55),
        (800,1.60),(1000,1.64),(1500,1.70),(2000,1.75),(3000,1.81),
        (5000,1.85),(10000,1.90),(50000,1.95),
    ],
}


# ──────────────────────────────────────────────────────────────────────────────
# AEDT .amat format writer
# ──────────────────────────────────────────────────────────────────────────────

def fmt_points(checkpoints):
    """Return the AEDT Points[N: H1,B1, H2,B2, ...] line."""
    vals = []
    for h, b in checkpoints:
        vals.append(f"{h:g}")
        vals.append(f"{b:g}")
    n = len(vals)
    return f"Points[{n}: {', '.join(vals)}]"


def write_bh_block(f, checkpoints, indent):
    """Write a nonlinear BH component block."""
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


def write_material_block(f, mat_name, rd_cp, td_cp, thickness_mm):
    """Write a single material block in AEDT .amat format."""
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

    # Anisotropic nonlinear permeability
    f.write(f"\t$begin 'permeability'\n")
    f.write(f"\t\t'property_type'='AnisoProperty'\n")
    # component1: Rolling Direction (X)
    f.write(f"\t\t$begin 'component1'\n")
    write_bh_block(f, rd_cp, indent=3)
    f.write(f"\t\t$end 'component1'\n")
    # component2: Transverse Direction (Y)
    f.write(f"\t\t$begin 'component2'\n")
    write_bh_block(f, td_cp, indent=3)
    f.write(f"\t\t$end 'component2'\n")
    # component3: Normal Direction (Z) — linear mu_r
    f.write(f"\t\t'component3'='1000'\n")
    f.write(f"\t$end 'permeability'\n")

    f.write(f"\t'conductivity'='2000000'\n")
    f.write(f"\t'Tref'='22cel'\n")
    f.write(f"\t'mass_density'='7650'\n")

    # Core loss: Electrical Steel model (placeholder coefficients)
    f.write(f"\t$begin 'core_loss_type'\n")
    f.write(f"\t\t'property_type'='ChoiceProperty'\n")
    f.write(f"\t\t'Choice'='Electrical Steel'\n")
    f.write(f"\t$end 'core_loss_type'\n")
    # Kh, Kc, Ke: set to 0 — update with manufacturer data for production use
    f.write(f"\t'core_loss_kh'='0'\n")
    f.write(f"\t'core_loss_kc'='0'\n")
    f.write(f"\t'core_loss_ke'='0'\n")
    f.write(f"\t'core_loss_kdc'='0'\n")
    # Lamination thickness as equivalent cut depth (in meters)
    cut_depth_m = thickness_mm / 1000.0
    f.write(f"\t'core_loss_equiv_cut_depth'='{cut_depth_m}meter'\n")
    f.write(f"\t'SolveInside'='1'\n")
    f.write(f"$end '{mat_name}'\n")


def write_amat_single(grade, meta, rd_cp, td_cp):
    """Write one .amat file per grade (one material per file)."""
    desc, cn, thickness = meta
    mat_name = f"GO_Steel_{grade}"
    out_path = os.path.join(OUTPUT_DIR, f"{mat_name}.amat")

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(f"# Maxwell material library — {mat_name}\n")
        f.write(f"# Grade: {grade} ({desc}), thickness={thickness}mm\n")
        f.write(f"# CN equivalent: {cn}  (GB/T 2521-2016)\n")
        f.write(f"# Source: Nippon Steel/JFE GO catalog (digitized, research use only)\n")
        f.write(f"# Permeability: anisotropic nonlinear (RD/TD BH curves, ND mu_r=1000)\n")
        f.write(f"# Core loss Kh/Kc/Ke = 0 (placeholder — update with manufacturer data)\n")
        f.write(f"# Import in Maxwell: File > Import > Import Material Library\n\n")
        write_material_block(f, mat_name, rd_cp, td_cp, thickness)

    return out_path


def write_amat_combined():
    """Write one .amat file containing ALL four grades."""
    out_path = os.path.join(OUTPUT_DIR, "GO_Steel_All_Grades.amat")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("# Maxwell material library — GO Electrical Steel (all grades)\n")
        f.write("# Grades: B23R075, B27R090, B27R095, B30P105\n")
        f.write("# Source: Nippon Steel/JFE GO catalog (digitized, research use only)\n")
        f.write("# Permeability: anisotropic nonlinear (RD=component1, TD=component2, ND=component3 mu_r=1000)\n")
        f.write("# Core loss Kh/Kc/Ke = 0 — update with manufacturer data for production\n")
        f.write("# Import in Maxwell: File > Import > Import Material Library\n\n")
        for grade, meta in GRADE_META.items():
            desc, cn, thickness = meta
            mat_name = f"GO_Steel_{grade}"
            rd_cp = CP[f"{grade}_RD"]
            td_cp = CP[f"{grade}_TD"]
            write_material_block(f, mat_name, rd_cp, td_cp, thickness)
            f.write("\n")
    return out_path


def generate_pyaedt_script():
    """Generate a PyAEDT import script using import_materials_from_file."""
    script_path = os.path.join(OUTPUT_DIR, "import_go_steel_maxwell.py")
    with open(script_path, "w", encoding="utf-8") as f:
        f.write('#!/usr/bin/env python3\n')
        f.write('"""\n')
        f.write('Import GO steel anisotropic materials into Maxwell via PyAEDT.\n\n')
        f.write('Requirements:\n')
        f.write('  pip install pyaedt\n')
        f.write('  Ansys Electronics Desktop (AEDT) must be installed.\n\n')
        f.write('Usage:\n')
        f.write('  python import_go_steel_maxwell.py\n')
        f.write('"""\n')
        f.write('import os\n')
        f.write('from ansys.aedt.core import Maxwell3d, Maxwell2d\n\n')
        f.write('BASE_DIR = os.path.dirname(os.path.abspath(__file__))\n')
        f.write('AMAT_FILE = os.path.join(BASE_DIR, "GO_Steel_All_Grades.amat")\n\n')
        f.write('def main():\n')
        f.write('    # --- Choose 2D or 3D simulation type ---\n')
        f.write('    USE_2D = True\n')
        f.write('    PROJECT  = "GO_Steel_Project"\n')
        f.write('    DESIGN   = "MaterialTest"\n\n')
        f.write('    if USE_2D:\n')
        f.write('        app = Maxwell2d(projectname=PROJECT, designname=DESIGN,\n')
        f.write('                        specified_version="2024.2", non_graphical=False)\n')
        f.write('    else:\n')
        f.write('        app = Maxwell3d(projectname=PROJECT, designname=DESIGN,\n')
        f.write('                        specified_version="2024.2", non_graphical=False)\n\n')
        f.write('    mode = "2D" if USE_2D else "3D"\n')
        f.write('    print(f"Connected to Maxwell {mode}")\n')
        f.write('    print(f"Importing from: {AMAT_FILE}")\n\n')
        f.write('    # Import all materials from the combined .amat file\n')
        f.write('    imported = app.materials.import_materials_from_file(AMAT_FILE)\n\n')
        f.write('    if imported:\n')
        f.write('        print(f"Successfully imported {len(imported)} material(s):")\n')
        f.write('        for mat in imported:\n')
        f.write('            print(f"  - {mat.name}")\n')
        f.write('    else:\n')
        f.write('        print("Import failed. Check AMAT file path.")\n\n')
        f.write('    app.save_project()\n')
        f.write('    return app\n\n')
        f.write('if __name__ == "__main__":\n')
        f.write('    m = main()\n')
        f.write('    input("Press Enter to close Maxwell...")\n')
        f.write('    m.close_desktop()\n')
    return script_path


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print("Generating Maxwell .amat material files for GO electrical steel...")
    print("Format: AEDT scripting ($begin/$end) — anisotropic nonlinear permeability\n")

    out_files = []

    # Individual .amat per grade
    for grade, meta in GRADE_META.items():
        rd_cp = CP[f"{grade}_RD"]
        td_cp = CP[f"{grade}_TD"]
        path = write_amat_single(grade, meta, rd_cp, td_cp)
        out_files.append(path)
        print(f"  {os.path.basename(path):40s} ({len(rd_cp)} RD pts, {len(td_cp)} TD pts)")

    # Combined .amat with all grades
    combined_path = write_amat_combined()
    out_files.append(combined_path)
    print(f"  {os.path.basename(combined_path):40s} (all 4 grades combined)")

    # PyAEDT import script
    script_path = generate_pyaedt_script()
    out_files.append(script_path)
    print(f"  {os.path.basename(script_path):40s} (PyAEDT API import script)")

    print(f"\nDone. {len(out_files)} files written to:\n  {OUTPUT_DIR}")
    print("\nTo use in Maxwell:")
    print("  Method 1 (GUI):    File > Import > Import Material Library > select .amat file")
    print("  Method 2 (PyAEDT): python import_go_steel_maxwell.py")
    print("\nNotes:")
    print("  - component1 = Rolling Direction (X-axis BH curve)")
    print("  - component2 = Transverse Direction (Y-axis BH curve)")
    print("  - component3 = Normal Direction (mu_r = 1000, linear)")
    print("  - Core loss Kh/Kc/Ke are 0 — update with datasheet values")

if __name__ == "__main__":
    main()
