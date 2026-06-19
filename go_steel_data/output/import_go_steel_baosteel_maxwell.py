#!/usr/bin/env python3
"""Import Baosteel GO EVI materials into Maxwell via PyAEDT."""
import os
from ansys.aedt.core import Maxwell2d, Maxwell3d

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
AMAT_FILE = os.path.join(BASE_DIR, "GO_Steel_Baosteel_EVI.amat")

def main():
    use_2d = True
    project = "Baosteel_GO_EVI_Project"
    design = "MaterialImport"
    app_cls = Maxwell2d if use_2d else Maxwell3d
    app = app_cls(projectname=project, designname=design,
                  specified_version="2024.2", non_graphical=False)
    imported = app.materials.import_materials_from_file(AMAT_FILE)
    print(f"Imported from {AMAT_FILE}")
    if imported:
        for mat in imported:
            print(f"  - {mat.name}")
    else:
        print("No materials imported; check AEDT version and AMAT path.")
    app.save_project()
    return app

if __name__ == "__main__":
    desktop = main()
    input("Press Enter to close Maxwell...")
    desktop.close_desktop()
