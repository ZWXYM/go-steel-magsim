#!/usr/bin/env python3
"""
Generate PyAEDT import script for GO steel BH curves.

Usage:
  python3 generate_pyaedt_import.py > import_go_steel_materials.py
  Then run: python3 import_go_steel_materials.py
"""

import os, csv, json
import numpy as np
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")

GRADE_META = {
    "B23R075": ("Domain-refined Hi-B, 0.23mm", "23QG080", 0.23),
    "B27R090": ("Hi-B, 0.27mm", "27QG090", 0.27),
    "B27R095": ("Conventional GO, 0.27mm", "27QG095", 0.27),
    "B30P105": ("Conventional GO, 0.30mm", "30QG105", 0.30),
}

# Maxwell AMAT file header
AMAT_HEADER = '''<?xml version="1.0" encoding="UTF-8"?>
<Material xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <MatProperty name="Name" fullname="name" type="string">{mat_name}</MatProperty>
  <MatProperty name="CoordinateSystemType" fullname="coordinate system type" type="choice">Cartesian</MatProperty>
  <MatProperty name="BulkOrSurface" fullname="bulk or surface" type="choice">Bulk</MatProperty>
'''

def load_csv(filepath):
    """Load BH CSV, returning (H_array, B_array)."""
    h_vals, b_vals = [], []
    with open(filepath) as f:
        for line in f:
            if line.startswith('#'):
                continue
            parts = line.strip().split(',')
            if len(parts) >= 2:
                try:
                    h_vals.append(float(parts[0]))
                    b_vals.append(float(parts[1]))
                except ValueError:
                    continue
    return np.array(h_vals), np.array(b_vals)


def write_amat_file(grade, metadata, rd_h, rd_b, td_h, td_b, nd_value=1000.0):
    """Write a Maxwell .amat material file with anisotropic BH curves."""
    desc, cn_name, thickness = metadata
    mat_name = f"GO_Steel_{grade}"
    filepath = os.path.join(OUTPUT_DIR, f"{mat_name}.amat")

    with open(filepath, 'w') as f:
        f.write(AMAT_HEADER.format(mat_name=mat_name))
        f.write(f'  <MatProperty name="MassDensity" fullname="mass density" type="float" unit="kg_per_m3">7650</MatProperty>\n\n')

        # --- RD direction (X-axis) ---
        f.write('  <!-- Rolling Direction (X) - Nonlinear BH -->\n')
        f.write('  <MatProperty name="BH_Data_X" fullname="BH data X" type="dataset">\n')
        for h, b in zip(rd_h, rd_b):
            f.write(f'    <DataPoint X="{h:.6f}" Y="{b:.6f}"/>\n')
        f.write('  </MatProperty>\n\n')

        # --- TD direction (Y-axis) ---
        f.write('  <!-- Transverse Direction (Y) - Nonlinear BH -->\n')
        f.write('  <MatProperty name="BH_Data_Y" fullname="BH data Y" type="dataset">\n')
        for h, b in zip(td_h, td_b):
            f.write(f'    <DataPoint X="{h:.6f}" Y="{b:.6f}"/>\n')
        f.write('  </MatProperty>\n\n')

        # --- ND direction (Z-axis) - simple linear mu_r ---
        f.write('  <!-- Normal Direction (Z) - Linear permeability -->\n')
        f.write(f'  <MatProperty name="Permeability_Z" fullname="relative permeability Z" type="float">{nd_value}</MatProperty>\n\n')

        # --- Core loss (placeholder -- use manufacturer data) ---
        f.write('  <!-- Core loss -- placeholder, update with actual values -->\n')
        f.write('  <MatProperty name="CoreLossModel" fullname="core loss model" type="choice">Electrical Steel</MatProperty>\n')
        f.write(f'  <MatProperty name="Thickness" fullname="thickness" type="float" unit="mm">{thickness}</MatProperty>\n')
        f.write('  <MatProperty name="Conductivity" fullname="conductivity" type="float" unit="siemens_per_m">2000000</MatProperty>\n')

        f.write('</Material>\n')

    return filepath, mat_name


def generate_pyaedt_script():
    """Generate a standalone Python script to import materials via PyAEDT."""
    lines = []
    lines.append('#!/usr/bin/env python3')
    lines.append('"""Import GO steel anisotropic materials into Maxwell via PyAEDT.\n')
    lines.append('Usage:  python3 import_go_steel_materials.py\n')
    lines.append('Requires: pyaedt, and a running AEDT/Maxwell session or a new one.\n')
    lines.append('"""')
    lines.append('import os')
    lines.append('')
    lines.append('# Path to this script directory (where BH CSV files live)')
    lines.append('BASE_DIR = os.path.dirname(os.path.abspath(__file__))')
    lines.append('')
    lines.append('GRADES = [')

    for grade, (desc, cn, thk) in GRADE_META.items():
        lines.append(f'    {{')
        lines.append(f'        "grade": "{grade}",')
        lines.append(f'        "cn_equiv": "{cn}",')
        lines.append(f'        "description": "{desc}",')
        lines.append(f'        "thickness_mm": {thk},')
        lines.append(f'        "rd_csv": os.path.join(BASE_DIR, "output/{grade}_RD.csv"),')
        lines.append(f'        "td_csv": os.path.join(BASE_DIR, "output/{grade}_TD.csv"),')
        lines.append(f'    }},')

    lines.append(']')
    lines.append('')
    lines.append('')
    lines.append('def load_bh_csv(filepath):')
    lines.append('    """Load BH CSV returning (H_list, B_list)."""')
    lines.append('    h_vals, b_vals = [], []')
    lines.append('    with open(filepath) as f:')
    lines.append('        for line in f:')
    lines.append('            if line.startswith("#"):')
    lines.append('                continue')
    lines.append('            parts = line.strip().split(",")')
    lines.append('            if len(parts) >= 2:')
    lines.append('                try:')
    lines.append('                    h_vals.append(float(parts[0]))')
    lines.append('                    b_vals.append(float(parts[1]))')
    lines.append('                except ValueError:')
    lines.append('                    continue')
    lines.append('    return h_vals, b_vals')
    lines.append('')
    lines.append('')
    lines.append('def import_grade(maxwell_app, grade_info):')
    lines.append('    """Import one GO steel grade with anisotropic BH curves.')
    lines.append('    ')
    lines.append('    For 2D Maxwell, defines RD as the in-plane direction.')
    lines.append('    For 3D, defines all three directions (RD, TD, ND).')
    lines.append('    """')
    lines.append('    materials = maxwell_app.materials')
    lines.append('    ')
    lines.append('    rd_h, rd_b = load_bh_csv(grade_info["rd_csv"])')
    lines.append('    td_h, td_b = load_bh_csv(grade_info["td_csv"])')
    lines.append('    ')
    lines.append('    mat_name = f"GO_Steel_{grade_info[\'grade\']}"')
    lines.append('    print(f"  Creating {mat_name}...")')
    lines.append('    ')
    lines.append('    # Add material')
    lines.append('    mat = materials.add_material(mat_name)')
    lines.append('    ')
    lines.append('    # Set RD (rolling direction) permeability as nonlinear BH curve')
    lines.append('    mat.permeability.value = [(h, b) for h, b in zip(rd_h, rd_b)]')
    lines.append('    ')
    lines.append('    # Set mass density (typical for GO steel)')
    lines.append('    mat.mass_density.value = 7650  # kg/m^3')
    lines.append('    ')
    lines.append('    # Set conductivity (typical for GO steel)')
    lines.append('    mat.conductivity.value = 2_000_000  # S/m')
    lines.append('    ')
    lines.append('    print(f"    RD BH curve: {len(rd_h)} points, B800 = {rd_b[np.argmin(np.abs(np.array(rd_h)-800))]:.3f} T")')
    lines.append('    ')
    lines.append('    return mat_name')
    lines.append('')
    lines.append('')
    lines.append('def main():')
    lines.append('    """Main: connect to Maxwell and import all grades."""')
    lines.append('    import numpy as np')
    lines.append('    from ansys.aedt.core import Maxwell3d, Maxwell2d')
    lines.append('    ')
    lines.append('    # --- Configuration: choose 2D or 3D ---')
    lines.append('    USE_2D = True  # Set to False for 3D Maxwell')
    lines.append('    PROJECT_NAME = "SynRM_GO_Steel"')
    lines.append('    DESIGN_NAME = "MaterialSetup"')
    lines.append('    ')
    lines.append('    # Launch Maxwell')
    lines.append('    if USE_2D:')
    lines.append('        maxwell = Maxwell2d(projectname=PROJECT_NAME, designname=DESIGN_NAME,')
    lines.append('                            specified_version="2024.2", non_graphical=False)')
    lines.append('    else:')
    lines.append('        maxwell = Maxwell3d(projectname=PROJECT_NAME, designname=DESIGN_NAME,')
    lines.append('                            specified_version="2024.2", non_graphical=False)')
    lines.append('    ')
    lines.append('    print(f"Connected to Maxwell {\'2D\' if USE_2D else \'3D\'}")')
    lines.append('    print(f"Project: {PROJECT_NAME}")')
    lines.append('    print()')
    lines.append('    ')
    lines.append('    imported = []')
    lines.append('    for grade_info in GRADES:')
    lines.append('        mat_name = import_grade(maxwell, grade_info)')
    lines.append('        imported.append(mat_name)')
    lines.append('        print(f"    ✓ {mat_name} ({grade_info[\'description\']})")')
    lines.append('        print()')
    lines.append('    ')
    lines.append('    print(f"\\nImported {len(imported)} materials:")')
    lines.append('    for name in imported:')
    lines.append('        print(f"  - {name}")')
    lines.append('    ')
    lines.append('    print("\\nMaterials are now available in Maxwell material library.")')
    lines.append('    print("Usage in PyAEDT:")')
    lines.append('    print(\'  maxwell.assign_material(["RotorSteelBody"], "\' + imported[0] + \'")\')')
    lines.append('    ')
    lines.append('    maxwell.save_project()')
    lines.append('    return maxwell')
    lines.append('')
    lines.append('')
    lines.append('if __name__ == "__main__":')
    lines.append('    m = main()')
    lines.append('    input("Press Enter to close Maxwell...")')
    lines.append('    m.close_desktop()')

    script_path = os.path.join(OUTPUT_DIR, "import_go_steel_materials.py")
    with open(script_path, 'w') as f:
        f.write('\n'.join(lines))
    return script_path


def main():
    """Generate AMAT files and PyAEDT import script."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    amat_files = []

    print("Generating .amat files for direct Maxwell import...")
    for grade, meta in GRADE_META.items():
        rd_h, rd_b = load_csv(os.path.join(OUTPUT_DIR, f"{grade}_RD.csv"))
        td_h, td_b = load_csv(os.path.join(OUTPUT_DIR, f"{grade}_TD.csv"))
        fpath, mname = write_amat_file(grade, meta, rd_h, rd_b, td_h, td_b)
        amat_files.append(fpath)
        print(f"  {os.path.basename(fpath)}")

    py_script = generate_pyaedt_script()
    amat_files.append(py_script)
    print(f"  {os.path.basename(py_script)}")

    print(f"\nDone. Import-ready files:")
    for f in amat_files:
        print(f"  {f}")


if __name__ == "__main__":
    main()
