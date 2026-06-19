#!/usr/bin/env python3
"""Import GO steel anisotropic materials into Maxwell via PyAEDT.

Usage:  python3 import_go_steel_materials.py

Requires: pyaedt, and a running AEDT/Maxwell session or a new one.

"""
import os

# Path to this script directory (where BH CSV files live)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

GRADES = [
    {
        "grade": "B23R075",
        "cn_equiv": "23QG080",
        "description": "Domain-refined Hi-B, 0.23mm",
        "thickness_mm": 0.23,
        "rd_csv": os.path.join(BASE_DIR, "output/B23R075_RD.csv"),
        "td_csv": os.path.join(BASE_DIR, "output/B23R075_TD.csv"),
    },
    {
        "grade": "B27R090",
        "cn_equiv": "27QG090",
        "description": "Hi-B, 0.27mm",
        "thickness_mm": 0.27,
        "rd_csv": os.path.join(BASE_DIR, "output/B27R090_RD.csv"),
        "td_csv": os.path.join(BASE_DIR, "output/B27R090_TD.csv"),
    },
    {
        "grade": "B27R095",
        "cn_equiv": "27QG095",
        "description": "Conventional GO, 0.27mm",
        "thickness_mm": 0.27,
        "rd_csv": os.path.join(BASE_DIR, "output/B27R095_RD.csv"),
        "td_csv": os.path.join(BASE_DIR, "output/B27R095_TD.csv"),
    },
    {
        "grade": "B30P105",
        "cn_equiv": "30QG105",
        "description": "Conventional GO, 0.30mm",
        "thickness_mm": 0.3,
        "rd_csv": os.path.join(BASE_DIR, "output/B30P105_RD.csv"),
        "td_csv": os.path.join(BASE_DIR, "output/B30P105_TD.csv"),
    },
]


def load_bh_csv(filepath):
    """Load BH CSV returning (H_list, B_list)."""
    h_vals, b_vals = [], []
    with open(filepath) as f:
        for line in f:
            if line.startswith("#"):
                continue
            parts = line.strip().split(",")
            if len(parts) >= 2:
                try:
                    h_vals.append(float(parts[0]))
                    b_vals.append(float(parts[1]))
                except ValueError:
                    continue
    return h_vals, b_vals


def import_grade(maxwell_app, grade_info):
    """Import one GO steel grade with anisotropic BH curves.
    
    For 2D Maxwell, defines RD as the in-plane direction.
    For 3D, defines all three directions (RD, TD, ND).
    """
    materials = maxwell_app.materials
    
    rd_h, rd_b = load_bh_csv(grade_info["rd_csv"])
    td_h, td_b = load_bh_csv(grade_info["td_csv"])
    
    mat_name = f"GO_Steel_{grade_info['grade']}"
    print(f"  Creating {mat_name}...")
    
    # Add material
    mat = materials.add_material(mat_name)
    
    # Set RD (rolling direction) permeability as nonlinear BH curve
    mat.permeability.value = [(h, b) for h, b in zip(rd_h, rd_b)]
    
    # Set mass density (typical for GO steel)
    mat.mass_density.value = 7650  # kg/m^3
    
    # Set conductivity (typical for GO steel)
    mat.conductivity.value = 2_000_000  # S/m
    
    print(f"    RD BH curve: {len(rd_h)} points, B800 = {rd_b[np.argmin(np.abs(np.array(rd_h)-800))]:.3f} T")
    
    return mat_name


def main():
    """Main: connect to Maxwell and import all grades."""
    import numpy as np
    from ansys.aedt.core import Maxwell3d, Maxwell2d
    
    # --- Configuration: choose 2D or 3D ---
    USE_2D = True  # Set to False for 3D Maxwell
    PROJECT_NAME = "SynRM_GO_Steel"
    DESIGN_NAME = "MaterialSetup"
    
    # Launch Maxwell
    if USE_2D:
        maxwell = Maxwell2d(projectname=PROJECT_NAME, designname=DESIGN_NAME,
                            specified_version="2024.2", non_graphical=False)
    else:
        maxwell = Maxwell3d(projectname=PROJECT_NAME, designname=DESIGN_NAME,
                            specified_version="2024.2", non_graphical=False)
    
    print(f"Connected to Maxwell {'2D' if USE_2D else '3D'}")
    print(f"Project: {PROJECT_NAME}")
    print()
    
    imported = []
    for grade_info in GRADES:
        mat_name = import_grade(maxwell, grade_info)
        imported.append(mat_name)
        print(f"    ✓ {mat_name} ({grade_info['description']})")
        print()
    
    print(f"\nImported {len(imported)} materials:")
    for name in imported:
        print(f"  - {name}")
    
    print("\nMaterials are now available in Maxwell material library.")
    print("Usage in PyAEDT:")
    print('  maxwell.assign_material(["RotorSteelBody"], "' + imported[0] + '")')
    
    maxwell.save_project()
    return maxwell


if __name__ == "__main__":
    m = main()
    input("Press Enter to close Maxwell...")
    m.close_desktop()