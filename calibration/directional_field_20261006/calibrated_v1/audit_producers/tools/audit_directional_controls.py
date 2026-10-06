"""Recheck native field/convergence exports and create a limited public record."""
import argparse
import json
import shutil
import sys
from pathlib import Path

PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT))
from modules.maxwell_material_transport import digest, blocks, verify_saved_material
from modules.maxwell_directional_controls import read_vector_field, evaluate_case, evaluate_suite
from modules.maxwell_native_convergence import read_convergence
from modules.motor_workbench import write_json
from modules.motor_queue import identity_status


def audit(root,study,output):
    if output.exists() or not output.is_relative_to(root):
        raise ValueError('Evidence output must be a new workspace directory')
    protocol=json.loads((study/'protocol.json').read_text())
    for name,sha in protocol['producer_sha256'].items():
        if digest(study/'frozen_producers'/name)!=sha:
            raise ValueError('Frozen native producer differs')
    sources=json.loads((study/'source_before.json').read_text())
    if not all(digest(root/p)==h for p,h in sources.items()):
        raise ValueError('Old source modified')
    results=[]
    for case in protocol['cases']:
        folder=study/'private_native'/case['case_id']
        session=json.loads((folder/'desktop_session.json').read_text())
        if not session['dedicated_session'] or identity_status(session['identity'])!='dead':
            raise ValueError('Owned native session not released')
        B=read_vector_field(folder/'B.fld',protocol['sample_points_m'])
        H=read_vector_field(folder/'H.fld',protocol['sample_points_m'])
        result=evaluate_case(protocol,case,B,H)
        result['native_convergence']=read_convergence(folder/'convergence.txt',protocol['setup']['PercentError'])
        raw=json.loads((folder/'result.json').read_text())
        if any(raw[k]!=result[k] for k in ('checks','mean_B_vector_T','mean_H_vector_A_per_m','H_parallel_A_per_m','passed')):
            raise ValueError('Independent CPU field reproduction differs')
        project=folder/'control.aedt'
        if digest(project)!=raw['saved_project_sha256']:
            raise ValueError('Solved native project changed')
        text=project.read_text(encoding='utf-8-sig')
        verify_saved_material(text,case['material'])
        results.append(result)
    reviewed=evaluate_suite(protocol,results)
    reviewed.update(new_field_solves=len(results),new_motor_solves=0,
        old_source_files_unchanged=len(sources),native_owned_sessions_released=len(results),
        material_holdout_metrics_unchanged=True,
        interpretation='Interface and numerical diagnostics only; no new measured samples or motor ranking')
    output.mkdir(parents=True)
    for filename in ('protocol.json','state.json','summary.json','source_before.json'):
        shutil.copy2(study/filename,output/filename)
    for tree in ('source_materials','frozen_producers'):
        shutil.copytree(study/tree,output/tree)
    for case in protocol['cases']:
        source=study/'private_native'/case['case_id'];dest=output/'cases'/case['case_id'];dest.mkdir(parents=True)
        for filename in ('B.fld','H.fld','convergence.txt','preflight.json','result.json','solve_started.json','solve_completed.json'):
            shutil.copy2(source/filename,dest/filename)
        text=(source/'control.aedt').read_text(encoding='utf-8-sig')
        sections=[case['material']['material_name'],'ModelSetup','BoundarySetup','AnalysisSetup']
        pieces=[]
        for section in sections:
            body=blocks(text,section)
            if len(body)!=1:
                raise ValueError('Ambiguous saved native definition')
            pieces.append("$begin '"+section+"'\n"+body[0]+"\n$end '"+section+"'\n")
        (dest/'saved_native_definitions.txt').write_text('\n'.join(pieces),encoding='utf-8',newline='\n')
    producers=('modules/maxwell_directional_controls.py','modules/maxwell_native_convergence.py','tools/audit_directional_controls.py')
    write_json(output/'audit_producer_sha256.json',{p:digest(PROJECT/p) for p in producers})
    for name in producers:
        target=output/'audit_producers'/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(PROJECT/name,target)
    write_json(output/'reviewed_summary.json',reviewed)
    return reviewed


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--study-dir',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args()
    result=audit(args.root.resolve(),args.study_dir.resolve(),args.output_dir.resolve())
    print(json.dumps({k:v for k,v in result.items() if k!='cases'},ensure_ascii=False,indent=2))
