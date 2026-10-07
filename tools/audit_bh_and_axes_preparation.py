"""Audit and package unsolved diagnostic preparations; never call AEDT."""
import argparse
import json
import shutil
import sys
from pathlib import Path
PROJECT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(PROJECT))
from modules.maxwell_bh_representation import make_protocol
from modules.motor_explicit_axes import candidates
from modules.maxwell_material_transport import digest,load_contract
from modules.motor_queue import identity_status
from modules.motor_workbench import write_json

BH='calibration/diagnostics/bh_representation_20261007/native_v1'
AXES='calibration/diagnostics/motor_explicit_axes_20261007/native_v1'


def audit(root,output):
    if output.exists() or not output.is_relative_to(root):raise ValueError('Fresh workspace evidence directory required')
    bh=root/BH;axes=root/AXES;protocol=json.loads((bh/'protocol.json').read_text())
    source=load_contract(next((bh/'source_materials').glob('*.amat')))
    expected=make_protocol(source)
    if any(protocol[k]!=v for k,v in expected.items()):raise ValueError('BH protocol differs from source-derived analytic transform')
    motor=json.loads((axes/'protocol.json').read_text())
    geometry=root/'calibration/diagnostics/nonlinear_field_20261007/ready_v2'
    plan=candidates(json.loads((geometry/'saved_geometry_input.json').read_text()),
        json.loads((geometry/'private_native/geometry/vertices.json').read_text()))
    if plan!=motor['candidates']:raise ValueError('48 candidate plan changed')
    verified={};sessions=[];assets={}
    for study in (bh,axes):
        p=json.loads((study/'protocol.json').read_text())
        for name,h in p['producer_sha256'].items():
            if digest(study/'frozen_producers'/name)!=h:raise ValueError('Frozen producer changed')
        old=json.loads((study/'source_before.json').read_text()) if (study/'source_before.json').exists() else p['source_sha256']
        for path,h in old.items():
            if digest(root/path)!=h:raise ValueError('Old source changed: '+path)
        verified.update(old)
        assets.update({f.relative_to(root).as_posix():digest(f) for f in study.rglob('*') if f.is_file()})
        if list(study.rglob('solve_started.json')) or list(study.rglob('solve_completed.json')):
            raise ValueError('This preparation-failure audit cannot certify any actual solve')
        for f in study.rglob('desktop_session.json'):
            record=json.loads(f.read_text())
            if not record['dedicated_session'] or identity_status(record['identity'])=='alive':
                raise ValueError('Owned native session is still alive')
            sessions.append(record['identity'])
    # No API return, normal process exit, or TCP-license probe is promoted
    # into physical validation, successful CS saving, or solver availability.
    bh_summary=json.loads((bh/'summary.json').read_text());failure=json.loads((axes/'failure.json').read_text())
    if bh_summary['new_field_solves']!=0 or json.loads((axes/'state.json').read_text())['status']!='failed_no_retry':
        raise ValueError('Unexpected diagnostic outcome')
    output.mkdir(parents=True)
    for study,label in ((bh,'bh_representation_native_v1'),(axes,'motor_axes_native_v1')):
        dest=output/label;dest.mkdir();shutil.copytree(study/'frozen_producers',dest/'frozen_producers')
        for name in ('protocol.json','state.json','source_before.json','summary.json'):
            if (study/name).exists():shutil.copy2(study/name,dest/name)
        if (study/'source_materials').exists():shutil.copytree(study/'source_materials',dest/'source_materials')
    review=dict(status='CPU_preparations_verified_native_initialization_failed',new_field_solves=0,new_motor_solves=0,
        new_MuMax_solves=0,cumulative_Maxwell_completed=39,cumulative_motor_solves=4,cumulative_MuMax_success=680,
        analytic_BH_transform_verified=True,source_reference_BH_unchanged=True,
        BH_native_material_import_verified=False,BH_native_field_response_verified=False,
        BH_timeout_stage='Maxwell2d initialization; last native log is project creation',
        BH_cases_not_dispatched=[c['case_id'] for c in protocol['cases'] if not (bh/'private_native'/c['case_id']).exists()],
        motor_CPU_candidate_count=len(plan),motor_native_saved_candidate_count=0,motor_native_error=failure['error'],
        motor_native_last_error='Script macro error: Access to the requested resource is not permitted.',
        motor_failure_stage='Maxwell2d design activation; before geometry reads, CS creation or validation',
        no_native_material_accuracy_inference=True,all_48_physical_direction_verified=False,
        real_material_field_verified=False,loss_calibration_verified=False,motor_ranking_eligible=False,
        dedicated_sessions_released=len(sessions),native_initialization_cause='unknown; raw logs preserved privately')
    write_json(output/'reviewed_summary.json',review)
    manifest={p.relative_to(output).as_posix():digest(p) for p in output.rglob('*') if p.is_file()}
    write_json(output/'evidence_sha256.json',manifest)
    return dict(**review,old_source_sha256=verified,private_asset_sha256=assets,
        thin_evidence_sha256=manifest,dedicated_session_identities=sessions)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--receipt',type=Path,required=True)
    a=p.parse_args()
    if a.receipt.exists():raise ValueError('Do not overwrite receipts')
    result=audit(a.root.resolve(),a.output.resolve());write_json(a.receipt,result)
    print(json.dumps({k:v for k,v in result.items() if 'sha256' not in k and 'identities' not in k},ensure_ascii=False,indent=2))
