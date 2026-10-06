"""CPU reproduce frozen independent B controls and export thin evidence only."""
import argparse
import json
import shutil
import sys
from pathlib import Path
import numpy as np
PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT))
from modules.maxwell_air_interface import evaluate_fields,classify_edges
from modules.maxwell_interface_controls import verify_material, MU0
from modules.maxwell_directional_controls import read_vector_field
from modules.maxwell_native_convergence import read_convergence
from modules.maxwell_material_transport import digest,blocks
from modules.motor_model_audit import one,value
from modules.motor_workbench import write_json
from modules.motor_queue import identity_status
from tools.run_air_interface_controls import material_case


def audit(root,study,output):
    if output.exists() or not study.is_relative_to(root) or not output.is_relative_to(root):
        raise ValueError('A new workspace evidence directory is required')
    protocol=json.loads((study/'protocol.json').read_text())
    record=json.loads((study/'source_CS_record.json').read_text())
    summary=json.loads((study/'summary.json').read_text())
    before=json.loads((study/'source_before.json').read_text())
    if not all(digest(root/p)==h for p,h in before.items()):raise ValueError('Old source changed')
    for p,h in protocol['producer_sha256'].items():
        if digest(study/'frozen_producers'/p)!=h:raise ValueError('Frozen producer changed')
    results=[];released=0;incomplete=[]
    for case in protocol['cases']:
        folder=study/'private_native'/case['case_id']
        if not folder.exists():
            if case['case_id'] not in summary['skipped_not_dispatched']:raise ValueError('Missing dispatch')
            incomplete.append(dict(case_id=case['case_id'],status='skipped_not_dispatched'));continue
        session=json.loads((folder/'desktop_session.json').read_text())
        if not session['dedicated_session'] or identity_status(session['identity'])!='dead':
            raise ValueError('Owned session still alive')
        released+=1
        if not (folder/'result.json').exists():
            fail=json.loads((folder/'failure.json').read_text())
            incomplete.append(dict(case_id=case['case_id'],status='native_failure',error=fail['error'],
                                   solve_started=(folder/'solve_started.json').exists(),solve_completed=(folder/'solve_completed.json').exists()))
            continue
        preflight=json.loads((folder/'preflight.json').read_text())
        if preflight['validation']!=1 or preflight['internal_boundary_assigned']:
            raise ValueError('Preflight or shared interface differs')
        if classify_edges(preflight['native_edges'])!=preflight['topology']:
            raise ValueError('Native edge geometry differs')
        if set(preflight['topology']['exterior_edge_ids'])&set(preflight['topology']['excluded_shared_edge_ids']):
            raise ValueError('Shared interface constrained')
        text=(folder/'control.aedt').read_text(encoding='utf-8-sig')
        verify_material(text,material_case(case,record))
        if value(one(text,'Maxwell2DModel'),'ModelDepth')!='1meter':raise ValueError('Saved model depth differs')
        if protocol['boundary_expression'] not in one(text,'BoundarySetup'):raise ValueError('Boundary expression differs')
        raw=json.loads((folder/'result.json').read_text())
        if digest(folder/'control.aedt')!=raw['saved_project_sha256']:raise ValueError('Solved project changed')
        points=case['material_sample_points_m']+case['air_sample_points_m']
        B=read_vector_field(folder/'B.fld',points);H=read_vector_field(folder/'H.fld',points)
        conv=read_convergence(folder/'convergence.txt',protocol['adaptive_percent_error'])
        result=evaluate_fields(case,B[:9],B[9:],conv,protocol['planned_gates'])
        if any(raw[k]!=v for k,v in result.items()):raise ValueError('CPU reproduction differs')
        result['exported_native_H_diagnostic_only']=dict(material_mean_A_per_m=H[:9].mean(axis=0).tolist(),air_mean_A_per_m=H[9:].mean(axis=0).tolist())
        if result['exported_native_H_diagnostic_only']!=raw['exported_native_H_diagnostic_only']:raise ValueError('Native H diagnostic changed')
        physical=np.asarray(case['actual_planned_frame_global'])
        nu_rotated=physical@np.diag(1/(MU0*np.array([1000.,100.,1000.])))@physical.T
        nu_global=np.diag(1/(MU0*np.array([1000.,100.,1000.])))
        diagnostic={}
        for name,nu in (('planned_material_frame',nu_rotated),('global_diagonal_tensor',nu_global)):
            prediction=B[:9]@nu.T
            diagnostic[name+'_relative_error']=float(np.max(np.linalg.norm(H[:9]-prediction,axis=1)/np.linalg.norm(prediction,axis=1)))
        result['native_H_constitutive_comparison_diagnostic_only']=diagnostic
        result['native_H_comparison_scope']='Analytic candidate comparison only; raw exported H retained, never replaced or used to certify B/energy controls'
        results.append(result)
    if len(results)!=summary['new_field_solves']:raise ValueError('Completed field count differs')
    all_pass=len(results)==3 and all(r['control_success'] for r in results)
    if summary['linear_air_independent_B_controls_verified']!=all_pass:raise ValueError('Control summary differs')
    reviewed=dict(protocol=protocol['protocol'],cases=results,incomplete_cases=incomplete,
        new_field_solves=len(results),dedicated_sessions_released=released,
        cumulative_Maxwell_completed=summary['cumulative_Maxwell_completed'],new_motor_solves=0,new_MuMax_solves=0,
        old_source_files_unchanged=len(before),linear_air_independent_B_controls_verified=all_pass,
        full_direction_H_export_interface_verified=False,real_material_field_verified=False,
        all_48_motor_CS_verified=False,loss_calibration_verified=False,motor_ranking_eligible=False,
        interpretation='Independent native B and official total energy only; native H kept separately. Constant linear direction controls do not certify real BH, loss, or all motor CS.')
    output.mkdir(parents=True)
    for name in ('protocol.json','summary.json','state.json','source_before.json','source_CS_record.json'):
        shutil.copy2(study/name,output/name)
    shutil.copytree(study/'frozen_producers',output/'frozen_producers')
    for case in protocol['cases']:
        src=study/'private_native'/case['case_id'];dest=output/'cases'/case['case_id'];dest.mkdir(parents=True)
        if not (src/'result.json').exists():
            write_json(dest/'failure_status.json',next(r for r in incomplete if r['case_id']==case['case_id']));continue
        for name in ('preflight.json','result.json','B.fld','H.fld','points.pts','convergence.txt','solve_started.json','solve_completed.json','native_messages.json'):
            shutil.copy2(src/name,dest/name)
        text=(src/'control.aedt').read_text(encoding='utf-8-sig');pieces=[]
        for section in (case['material_name'],'vacuum','ModelSetup','BoundarySetup','AnalysisSetup'):
            bodies=blocks(text,section)
            if len(bodies)!=1:raise ValueError('Ambiguous saved section '+section)
            pieces.append("$begin '"+section+"'\n"+bodies[0]+"\n$end '"+section+"'\n")
        (dest/'saved_native_definitions.txt').write_text('\n'.join(pieces),encoding='utf-8',newline='\n')
        write_json(dest/'saved_design_settings.json',dict(model_depth='1meter',geometry_mode=value(one(text,'Maxwell2DModel'),'GeometryMode')))
    names=('modules/maxwell_air_interface.py','tools/audit_air_interface_controls.py')
    write_json(output/'audit_producer_sha256.json',{p:digest(PROJECT/p) for p in names})
    for name in names:
        dest=output/'audit_producers'/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(PROJECT/name,dest)
    write_json(output/'reviewed_summary.json',reviewed)
    return reviewed


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('root','study-dir','output-dir'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();result=audit(a.root.resolve(),a.study_dir.resolve(),a.output_dir.resolve())
    print(json.dumps({k:v for k,v in result.items() if k not in ('cases','incomplete_cases')},indent=2))
