#!/usr/bin/env python3
"""
Generate Ansys Maxwell/AEDT .amat files from Baosteel GO Electrical Steel EVI.

Source PDF:
  go_steel_data/pdf/Baosteel_GO_EVI.pdf

What is digitized from the PDF:
  - RD/component1 DC B-H curves for B23R080, B20R070, and B18R065.
  - Curves are read from the DC magnetization plots on pages 6, 9, and 11
    of the printed brochure pages (PDF pages 7, 10, and 12).

Important modeling note:
  The brochure does not provide full TD/component2 B-H curves.  To keep the
  material importable as an anisotropic Maxwell material, component2 uses a
  representative GO hard-axis curve from the existing project workflow.  Treat
  component2 as an estimate and replace it with supplier data when available.

Output:
  output/GO_Steel_Baosteel_EVI.amat
  output/GO_Steel_Baosteel_B23R080.amat
  output/GO_Steel_Baosteel_B20R070.amat
  output/GO_Steel_Baosteel_B18R065.amat
  output/import_go_steel_baosteel_maxwell.py
"""

from __future__ import annotations

import io
import os


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "output")


# RD/component1 B-H checkpoints digitized from the Baosteel EVI DC B-H plots.
# H is A/m, B is tesla.  Points above the plotted range are extrapolated
# smoothly toward GO steel saturation and are marked in each material header.
CP_RD = {
    "B23R080": [
        (0, 0),
        (6, 0.316),
        (8, 0.481),
        (10, 0.695),
        (12, 0.905),
        (15, 1.153),
        (20, 1.325),
        (30, 1.480),
        (40, 1.560),
        (50, 1.611),
        (80, 1.704),
        (100, 1.739),
        (150, 1.792),
        (200, 1.824),
        (300, 1.862),
        (500, 1.899),
        (800, 1.928),
        (1000, 1.937),
        (2000, 1.955),
        (5000, 1.985),
        (10000, 2.005),
        (50000, 2.030),
    ],
    "B20R070": [
        (0, 0),
        (8, 0.332),
        (10, 0.460),
        (12, 0.638),
        (15, 0.865),
        (20, 1.150),
        (30, 1.378),
        (40, 1.487),
        (50, 1.554),
        (80, 1.664),
        (100, 1.704),
        (150, 1.763),
        (200, 1.797),
        (300, 1.837),
        (500, 1.877),
        (800, 1.904),
        (1000, 1.915),
        (2000, 1.940),
        (5000, 1.970),
        (10000, 1.995),
        (50000, 2.025),
    ],
    "B18R065": [
        (0, 0),
        (9, 0.399),
        (10, 0.455),
        (12, 0.601),
        (15, 0.803),
        (20, 1.039),
        (30, 1.256),
        (40, 1.385),
        (50, 1.482),
        (80, 1.638),
        (100, 1.693),
        (150, 1.764),
        (200, 1.802),
        (300, 1.840),
        (500, 1.877),
        (800, 1.904),
        (1000, 1.914),
        (2000, 1.940),
        (5000, 1.970),
        (10000, 1.995),
        (50000, 2.025),
    ],
}


# Representative hard-axis component for GO steel.  This is not from the
# Baosteel EVI brochure; it is used only because AEDT anisotropic materials need
# a component2 definition and the brochure gives no full TD B-H data.
CP_TD_REPRESENTATIVE = [
    (0, 0),
    (30, 0.15),
    (50, 0.35),
    (80, 0.60),
    (100, 0.80),
    (130, 1.00),
    (160, 1.15),
    (200, 1.28),
    (250, 1.40),
    (300, 1.48),
    (400, 1.56),
    (500, 1.62),
    (600, 1.66),
    (800, 1.70),
    (1000, 1.74),
    (1500, 1.78),
    (2000, 1.82),
    (3000, 1.86),
    (5000, 1.90),
    (10000, 1.94),
    (50000, 1.97),
]


# Typical magnetic properties from the table on PDF page 5.
# P values are specific total loss; J800 is magnetic polarization at 800 A/m.
GRADE_META = {
    "B23R080": {
        "family": "Domain-refined high permeability GO",
        "thickness_mm": 0.23,
        "p17_50_w_kg": 0.57,
        "p17_60_w_kg": 0.77,
        "j800_t": 1.91,
        "s17_50_va_kg": 1.86,
    },
    "B20R070": {
        "family": "Domain-refined high permeability GO",
        "thickness_mm": 0.20,
        "p17_50_w_kg": 0.51,
        "p17_60_w_kg": 0.68,
        "j800_t": 1.91,
        "s17_50_va_kg": 1.96,
    },
    "B18R065": {
        "family": "Domain-refined high permeability GO",
        "thickness_mm": 0.18,
        "p17_50_w_kg": 0.48,
        "p17_60_w_kg": 0.64,
        "j800_t": 1.91,
        "s17_50_va_kg": 1.91,
    },
}


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


def write_material_block(
    f,
    mat_name,
    rd_cp,
    td_cp,
    thickness_mm,
    density=7650,
    conductivity=2_000_000,
):
    cut_depth_m = thickness_mm / 1000.0
    f.write(f"$begin '{mat_name}'\n")
    f.write("\t'CoordinateSystemType'='Cartesian'\n")
    f.write("\t'BulkOrSurface'='Bulk'\n")
    f.write("\t$begin 'PhysicsTypes'\n")
    f.write("\t\t'set'('Electromagnetic')\n")
    f.write("\t$end 'PhysicsTypes'\n")
    f.write("\t$begin 'AttachedData'\n")
    f.write("\t$end 'AttachedData'\n")
    f.write("\t$begin 'ModifierData'\n")
    f.write("\t\t$begin 'ThermalModifierData'\n")
    f.write("\t\t\t'modifier_data'='no_modifier'\n")
    f.write("\t\t$end 'ThermalModifierData'\n")
    f.write("\t$end 'ModifierData'\n")
    f.write("\t'permittivity'='1'\n")
    f.write("\t$begin 'permeability'\n")
    f.write("\t\t'property_type'='AnisoProperty'\n")
    f.write("\t\t$begin 'component1'\n")
    write_bh_block(f, rd_cp, indent=3)
    f.write("\t\t$end 'component1'\n")
    f.write("\t\t$begin 'component2'\n")
    write_bh_block(f, td_cp, indent=3)
    f.write("\t\t$end 'component2'\n")
    f.write("\t\t'component3'='1000'\n")
    f.write("\t$end 'permeability'\n")
    f.write(f"\t'conductivity'='{conductivity}'\n")
    f.write("\t'Tref'='22cel'\n")
    f.write(f"\t'mass_density'='{density}'\n")
    f.write("\t$begin 'core_loss_type'\n")
    f.write("\t\t'property_type'='ChoiceProperty'\n")
    f.write("\t\t'Choice'='Electrical Steel'\n")
    f.write("\t$end 'core_loss_type'\n")
    f.write("\t'core_loss_kh'='0'\n")
    f.write("\t'core_loss_kc'='0'\n")
    f.write("\t'core_loss_ke'='0'\n")
    f.write("\t'core_loss_kdc'='0'\n")
    f.write(f"\t'core_loss_equiv_cut_depth'='{cut_depth_m}meter'\n")
    f.write("\t'SolveInside'='1'\n")
    f.write(f"$end '{mat_name}'\n")


def write_header(f, grade, mat_name, meta):
    f.write(f"# Maxwell material library - {mat_name}\n")
    f.write("# Source: Baosteel GO Electrical Steel EVI brochure\n")
    f.write("# Local source PDF: go_steel_data/pdf/Baosteel_GO_EVI.pdf\n")
    f.write(f"# Grade: {grade}\n")
    f.write(f"# Family: {meta['family']}\n")
    f.write(f"# Thickness: {meta['thickness_mm']} mm\n")
    f.write(
        "# Typical table values: "
        f"P17/50={meta['p17_50_w_kg']} W/kg, "
        f"P17/60={meta['p17_60_w_kg']} W/kg, "
        f"J800={meta['j800_t']} T, "
        f"S17/50={meta['s17_50_va_kg']} VA/kg\n"
    )
    f.write("# component1/RD: digitized from Baosteel EVI DC B-H plot\n")
    f.write("# component2/TD: representative hard-axis estimate, not supplier data\n")
    f.write("# component3/ND: linear mu_r=1000 placeholder\n")
    f.write("# H>1000 A/m points are extrapolated toward saturation.\n")
    f.write("# Core-loss Kh/Kc/Ke are placeholders and left at 0.\n")
    f.write("# Import in Maxwell: File > Import > Import Material Library\n\n")


def b_at_h(checkpoints, h_query):
    for i, (h, b) in enumerate(checkpoints):
        if h == h_query:
            return b
        if h > h_query:
            h0, b0 = checkpoints[i - 1]
            return b0 + (b - b0) * (h_query - h0) / (h - h0)
    return checkpoints[-1][1]


def check_monotone(checkpoints, name):
    issues = []
    for i in range(1, len(checkpoints)):
        h0, b0 = checkpoints[i - 1]
        h1, b1 = checkpoints[i]
        if h1 <= h0:
            issues.append(f"H not increasing at index {i}: {h0} -> {h1}")
        if b1 <= b0:
            issues.append(f"B not increasing at H={h1}: {b0} -> {b1}")
    if issues:
        raise ValueError(f"{name} is not strictly monotone: " + "; ".join(issues))


def generate_pyaedt_script():
    script_path = os.path.join(OUTPUT_DIR, "import_go_steel_baosteel_maxwell.py")
    with open(script_path, "w", encoding="utf-8") as f:
        f.write("#!/usr/bin/env python3\n")
        f.write('"""Import Baosteel GO EVI materials into Maxwell via PyAEDT."""\n')
        f.write("import os\n")
        f.write("from ansys.aedt.core import Maxwell2d, Maxwell3d\n\n")
        f.write("BASE_DIR = os.path.dirname(os.path.abspath(__file__))\n")
        f.write('AMAT_FILE = os.path.join(BASE_DIR, "GO_Steel_Baosteel_EVI.amat")\n\n')
        f.write("def main():\n")
        f.write("    use_2d = True\n")
        f.write('    project = "Baosteel_GO_EVI_Project"\n')
        f.write('    design = "MaterialImport"\n')
        f.write("    app_cls = Maxwell2d if use_2d else Maxwell3d\n")
        f.write("    app = app_cls(projectname=project, designname=design,\n")
        f.write('                  specified_version="2024.2", non_graphical=False)\n')
        f.write("    imported = app.materials.import_materials_from_file(AMAT_FILE)\n")
        f.write('    print(f"Imported from {AMAT_FILE}")\n')
        f.write("    if imported:\n")
        f.write("        for mat in imported:\n")
        f.write('            print(f"  - {mat.name}")\n')
        f.write("    else:\n")
        f.write('        print("No materials imported; check AEDT version and AMAT path.")\n')
        f.write("    app.save_project()\n")
        f.write("    return app\n\n")
        f.write('if __name__ == "__main__":\n')
        f.write("    desktop = main()\n")
        f.write('    input("Press Enter to close Maxwell...")\n')
        f.write("    desktop.close_desktop()\n")
    return script_path


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    all_blocks = []
    individual_files = []

    print("Generating Baosteel GO EVI Maxwell .amat files...")
    for grade, meta in GRADE_META.items():
        mat_name = f"GO_Steel_Baosteel_{grade}"
        rd_cp = CP_RD[grade]
        td_cp = CP_TD_REPRESENTATIVE

        check_monotone(rd_cp, f"{grade}_RD")
        check_monotone(td_cp, "representative_TD")

        fpath = os.path.join(OUTPUT_DIR, f"{mat_name}.amat")
        with open(fpath, "w", encoding="utf-8") as f:
            write_header(f, grade, mat_name, meta)
            write_material_block(f, mat_name, rd_cp, td_cp, meta["thickness_mm"])
        individual_files.append(fpath)

        buf = io.StringIO()
        write_material_block(buf, mat_name, rd_cp, td_cp, meta["thickness_mm"])
        all_blocks.append((mat_name, buf.getvalue()))

        b800 = b_at_h(rd_cp, 800)
        delta = b800 - meta["j800_t"]
        print(
            f"  {mat_name}: RD pts={len(rd_cp)}, "
            f"B800={b800:.3f} T, table J800={meta['j800_t']:.3f} T, "
            f"delta={delta:+.3f} T"
        )

    combined_path = os.path.join(OUTPUT_DIR, "GO_Steel_Baosteel_EVI.amat")
    with open(combined_path, "w", encoding="utf-8") as f:
        f.write("# Maxwell material library - Baosteel GO Electrical Steel EVI\n")
        f.write("# Includes B23R080, B20R070, B18R065.\n")
        f.write("# RD curves are digitized from Baosteel EVI DC B-H plots.\n")
        f.write("# TD curve is a representative estimate because the source PDF lacks TD B-H tables.\n\n")
        for mat_name, block in all_blocks:
            f.write(block)
            f.write("\n")

    import_script = generate_pyaedt_script()

    print(f"\n  Combined: {os.path.basename(combined_path)}")
    print(f"  Individual: {len(individual_files)} files")
    print(f"  PyAEDT import script: {os.path.basename(import_script)}")
    print("Done.")


if __name__ == "__main__":
    main()
