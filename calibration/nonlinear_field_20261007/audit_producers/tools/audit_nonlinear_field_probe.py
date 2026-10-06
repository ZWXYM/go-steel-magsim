"""CPU reproduction of frozen field probes and a limited public evidence copy."""
import argparse
import json
import shutil
import sys
from pathlib import Path

PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT))
from modules.maxwell_nonlinear_controls import evaluate,verify_definition,geometry_metrics
from modules.maxwell_directional_controls import read_vector_field
from modules.maxwell_material_transport import digest,blocks
from modules.maxwell_native_convergence import read_convergence
from modules.motor_queue import identity_status
from modules.motor_workbench import write_json


def audit(root,study,output):
    if output.exists() or not output.is_relative_to(root):
        raise ValueError('A new evidence directory is required')
    protocol=json.loads((study/'protocol.json').read_text())
    summary=json.loads((study/'summary.json').read_text())
    if digest(root/protocol['geometry_source'])!=protocol['geometry_sha256']:
        raise ValueError('Original geometry source changed')
    for name,expected in protocol['producer_sha256'].items():
        if digest(study/'frozen_producers'/name)!=expected:
            raise ValueError('Frozen producer changed')
    before=json.loads((study/'source_before.json').read_text())
    if not all(digest(root/p)==h for p,h in before.items()):
        raise ValueError('Old source changed')
    records=json.loads((study/'saved_geometry_input.json').read_text())
    vertices=json.loads((study/'private_native/geometry/vertices.json').read_text())
    if len(records)!=48 or len(vertices['inserts'])!=48:
        raise ValueError('Incomplete native insert audit')
    reviewed_geometry=[]
    for record,item in zip(records,vertices['inserts']):
        if item['object']!=record['object'] or item['native_CS']!=record['CS']:
            raise ValueError('Native object identity mismatch')
        metrics={kind:geometry_metrics(record,item['native_vertices_m'],kind)
                 for kind in ('absolute_point','direction_vector')}
        if metrics!=item['hypotheses']:
            raise ValueError('Native vertex analysis differs')
        reviewed_geometry.append(metrics)
    results=[]
    incomplete=[]
    for case in protocol['cases']:
        folder=study/'private_native'/case['case_id']
        if not (folder/'result.json').exists():
            failure=json.loads((folder/'failure.json').read_text()) if (folder/'failure.json').exists() else {'error':'Not completed'}
            incomplete.append(dict(case_id=case['case_id'],error=failure['error'],field_response_available=False,
                native_validation=json.loads((folder/'preflight.json').read_text())['validation'] if (folder/'preflight.json').exists() else None))
            continue
        B=read_vector_field(folder/'B.fld',case['sample_points_m'])
        H=read_vector_field(folder/'H.fld',case['sample_points_m'])
        result=evaluate(case,B,H,read_convergence(folder/'convergence.txt',case['setup']['PercentError']))
        raw=json.loads((folder/'result.json').read_text())
        if any(raw[k]!=v for k,v in result.items()):
            raise ValueError('Independent field reproduction differs')
        project=folder/'control.aedt'
        if digest(project)!=raw['saved_project_sha256']:
            raise ValueError('Saved solved project changed')
        verify_definition(project.read_text(encoding='utf-8-sig'),case)
        results.append(result)
    for name in ['geometry']+[c['case_id'] for c in protocol['cases']]:
        session=json.loads((study/'private_native'/name/'desktop_session.json').read_text())
        if not session['dedicated_session'] or identity_status(session['identity'])!='dead':
            raise ValueError('Dedicated native session not released')
    if len(results)!=summary['new_field_solves']:
        raise ValueError('Completed solve count differs')
    cs_results=[r for r in results if r['case_id']=='translated_object_CS_linear' and r['passed']]
    interpretation=cs_results[0].get('CS_interpretation') if cs_results else None
    selected=[m[interpretation] for m in reviewed_geometry] if interpretation else []
    reviewed=dict(cases=results,incomplete_cases=incomplete,native_run_complete=summary['native_run_complete'],new_field_solves=len(results),new_motor_solves=0,new_MuMax_solves=0,
        cumulative_Maxwell_completed=summary['cumulative_Maxwell_completed'],
        passed_probe_count=sum(r['passed'] for r in results),
        native_insert_count=48,object_CS_interpretation=interpretation,
        geometry_RD_major_axis_angles_deg=[m['RD_major_axis_unsigned_angle_deg'] for m in selected],
        maximum_geometry_RD_major_axis_angle_deg=max([m['RD_major_axis_unsigned_angle_deg'] for m in selected],default=None),
        hypothetical_geometry_angles_deg={kind:[m[kind]['RD_major_axis_unsigned_angle_deg'] for m in reviewed_geometry]
            for kind in ('absolute_point','direction_vector')},
        old_source_files_unchanged=len(before),dedicated_sessions_released=7,
        calibrated_RD_TD_field_verified=False,loss_calibration_verified=False,motor_ranking_eligible=False,
        interpretation='Interface diagnostics; source-knot and adaptive gates retained; no new measured curves or motor ranking')
    output.mkdir(parents=True)
    for name in ('protocol.json','summary.json','state.json','saved_geometry_input.json','source_before.json'):
        shutil.copy2(study/name,output/name)
    for tree in ('source_materials','frozen_producers'):
        shutil.copytree(study/tree,output/tree)
    shutil.copy2(study/'private_native/geometry/vertices.json',output/'native_geometry_vertices.json')
    for case in protocol['cases']:
        source=study/'private_native'/case['case_id'];dest=output/'cases'/case['case_id'];dest.mkdir(parents=True)
        if not (source/'result.json').exists():
            failure=next(item for item in incomplete if item['case_id']==case['case_id'])
            write_json(dest/'failure_status.json',failure)
            for name in ('preflight.json','solve_started.json'):
                if (source/name).exists():shutil.copy2(source/name,dest/name)
        else:
            for name in ('B.fld','H.fld','convergence.txt','preflight.json','result.json','solve_started.json','solve_completed.json','native_messages.json'):
                shutil.copy2(source/name,dest/name)
        text=(source/'control.aedt').read_text(encoding='utf-8-sig');pieces=[]
        for section in (case['material']['material_name'],'ModelSetup','BoundarySetup','AnalysisSetup'):
            bodies=blocks(text,section)
            if len(bodies)!=1:raise ValueError('Ambiguous saved definition')
            pieces.append("$begin '"+section+"'\n"+bodies[0]+"\n$end '"+section+"'\n")
        (dest/'saved_native_definitions.txt').write_text('\n'.join(pieces),encoding='utf-8',newline='\n')
    names=('modules/maxwell_nonlinear_controls.py','tools/audit_nonlinear_field_probe.py')
    write_json(output/'audit_producer_sha256.json',{p:digest(PROJECT/p) for p in names})
    for name in names:
        dest=output/'audit_producers'/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(PROJECT/name,dest)
    write_json(output/'reviewed_summary.json',reviewed)
    return reviewed


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('root','study-dir','output-dir'):parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args();result=audit(args.root.resolve(),args.study_dir.resolve(),args.output_dir.resolve())
    print(json.dumps({k:v for k,v in result.items() if k!='cases'},ensure_ascii=False,indent=2))
