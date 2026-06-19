#!/usr/bin/env python3
"""
Import GO steel anisotropic materials into Maxwell via PyAEDT.

Requirements:
  pip install pyaedt
  Ansys Electronics Desktop (AEDT) must be installed.

Usage:
  python import_go_steel_maxwell.py
"""
import os
from ansys.aedt.core import Maxwell3d, Maxwell2d

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
AMAT_FILE = os.path.join(BASE_DIR, "GO_Steel_All_Grades.amat")

def main():
    # --- Choose 2D or 3D simulation type ---
    USE_2D = True
    PROJECT  = "GO_Steel_Project"
    DESIGN   = "MaterialTest"

    if USE_2D:
        app = Maxwell2d(projectname=PROJECT, designname=DESIGN,
                        specified_version="2024.2", non_graphical=False)
    else:
        app = Maxwell3d(projectname=PROJECT, designname=DESIGN,
                        specified_version="2024.2", non_graphical=False)

    mode = "2D" if USE_2D else "3D"
    print(f"Connected to Maxwell {mode}")
    print(f"Importing from: {AMAT_FILE}")

    # Import all materials from the combined .amat file
    imported = app.materials.import_materials_from_file(AMAT_FILE)

    if imported:
        print(f"Successfully imported {len(imported)} material(s):")
        for mat in imported:
            print(f"  - {mat.name}")
    else:
        print("Import failed. Check AMAT file path.")

    app.save_project()
    return app

if __name__ == "__main__":
    m = main()
    input("Press Enter to close Maxwell...")
    m.close_desktop()
