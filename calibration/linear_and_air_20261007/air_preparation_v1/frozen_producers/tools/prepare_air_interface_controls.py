"""Freeze a CPU-checked air-interface hypothesis; this command never solves."""
import argparse
import json
import shutil
import sys
from pathlib import Path
PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT))
from modules.maxwell_air_interface import make_protocol
from modules.maxwell_material_transport import digest
from modules.motor_workbench import write_json


def prepare(root, output):
    if output.exists() or not output.is_relative_to(root):
        raise ValueError('A new workspace preparation directory is required')
    source=root/'calibration/diagnostics/nonlinear_field_20261007/ready_v2/saved_geometry_input.json'
    record=json.loads(source.read_text())[1]
    protocol=make_protocol(record)
    names=('modules/maxwell_air_interface.py','modules/maxwell_interface_controls.py','tools/prepare_air_interface_controls.py')
    protocol.update(source_geometry_sha256=digest(source),source_geometry=source.relative_to(root).as_posix(),
        source_object=record['object'],producer_sha256={p:digest(PROJECT/p) for p in names})
    output.mkdir(parents=True)
    write_json(output/'protocol.json',protocol);write_json(output/'source_CS_record.json',record)
    for name in names:
        path=output/'frozen_producers'/name;path.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(PROJECT/name,path)
    return protocol


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,required=True);p.add_argument('--output-dir',type=Path,required=True)
    a=p.parse_args();value=prepare(a.root.resolve(),a.output_dir.resolve())
    print(json.dumps({k:value[k] for k in ('protocol','status','maximum_native_attempts','new_field_solves','native_execution_entrypoint_available')}))
