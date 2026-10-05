"""CPU-only audit of saved V8_2 models and same-case official results."""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT))
from modules.motor_model_audit import audit_project,comparison_status,VERSION
from modules.motor_workbench import collect_metrics,write_json
from modules.material_calibration import file_hash


def review(root,output):
    root,output=Path(root).resolve(),Path(output).resolve()
    if output.exists() or output.is_relative_to(root/'motor') or output.is_relative_to(root/'magsim/data/workbench'):
        raise ValueError('审计必须使用全新独立目录')
    entries=[]
    files=set()
    jobs=root/'magsim/data/workbench/motor'
    template=root/'motor/v8_2/cases/nippon_steel_23zh90/motorcad_full_v8_1_nippon_steel_23zh90.aedt'
    entries.append(dict(id='template',project=template))
    for manifest_path in sorted(jobs.glob('scan_*/manifest.json')):
        manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
        files.update((manifest_path,manifest_path.parent/'state.json'))
        for case in manifest['cases']:
            folder=manifest_path.parent/case['id']
            result=folder/'result.json'
            record=json.loads(result.read_text(encoding='utf-8')) if result.exists() else None
            entries.append(dict(id=manifest['id']+'_'+case['id'],project=folder/'motor.aedt',folder=folder,
                record=record,measurement=manifest.get('measurement_protocol','v8_2_cycle1'),
                expected_project_sha256=record['solved_project_sha256'] if record else case['project_sha256']))
    for folder in sorted((root/'motor/v8_2/cases').glob('*')):
        record_path=folder/'final_resolved_official_report.json'
        if not record_path.exists():
            continue
        project=list(folder.glob('*.aedt'))
        if len(project)!=1:
            raise ValueError('历史算例项目不唯一: '+folder.name)
        entries.append(dict(id='archive_'+folder.name,project=project[0],folder=folder,
            record=json.loads(record_path.read_text(encoding='utf-8')),measurement='v8_2_cycle1',archive=True))
    for entry in entries:
        files.add(entry['project'])
        if 'folder' in entry:
            folder=entry['folder']
            files.update(p for p in folder.glob('*.json'))
            for sub in ('reports','maxwell_reports','final_resolved_reports','final_resolved_maxwell_reports'):
                files.update((folder/sub).glob('*.csv'))
    before={p.relative_to(root).as_posix():file_hash(p) for p in sorted(files)}
    output.mkdir(parents=True)
    write_json(output/'source_before.json',before)
    results=[]
    for entry in entries:
        audit=audit_project(entry['project'])
        record=entry.get('record')
        model=dict(id=entry['id'],project_source=entry['project'].relative_to(root).as_posix(),audit=audit,
            project_matches_recorded_sha256=audit['project_sha256']==entry['expected_project_sha256'] if 'expected_project_sha256' in entry else None,
            numerical_metrics_unchanged=True,model_changed=False)
        evidence=output/'cases'/entry['id']
        evidence.mkdir(parents=True)
        write_json(evidence/'model_audit.json',model)
        if record:
            source_reports='final_resolved_reports' if entry.get('archive') else 'reports'
            source_losses='final_resolved_maxwell_reports' if entry.get('archive') else 'maxwell_reports'
            if (entry['folder']/source_reports/'Torque Plots.csv').is_file():
                for src,dst in ((source_reports,'reports'),(source_losses,'maxwell_reports')):
                    (evidence/dst).mkdir()
                    for p in (entry['folder']/src).glob('*.csv'):
                        shutil.copy2(p,evidence/dst/p.name)
                computed=collect_metrics(evidence,entry['measurement'])
                expected=record['thesis_metrics'] if entry.get('archive') else record
                errors={k:abs(computed[k]-expected[k]) for k in ('T_avg_Nm','K_T_ripple_pct','P_Fe_W','P_Cu_W')}
                model.update(metrics=computed,recorded_metric_deltas=errors,
                    numeric_acceptance_before=record.get('accepted'),
                    comparison_eligibility=comparison_status(dict(computed,model_physics_audit=audit)))
                # Trapezoid means are a separate endpoint-weight diagnostic.
                # Preserve official CSVs, old values, and all acceptance gates.
                import numpy as np
                t=np.array(computed['time_ms'])/1000
                torque=np.array(computed['torque_Nm'])
                tmean=float(np.trapz(torque,t)/(t[-1]-t[0]))
                model['endpoint_weight_diagnostic']=dict(time_integral_T_avg_Nm=tmean,
                    frozen_arithmetic_T_avg_Nm=computed['T_avg_Nm'],difference_Nm=tmean-computed['T_avg_Nm'],
                    replaces_metric=False)
            else:
                model['csv_review']='matching final version missing; no fallback to another report'
            write_json(evidence/'model_audit.json',model)
        results.append(model)
    after={p:file_hash(root/p) for p in before}
    if after!=before:
        raise ValueError('审计期间原始来源发生变化')
    write_json(output/'source_integrity.json',dict(status='passed',source_files_checked=len(before),unchanged=True,new_Maxwell_solves=0))
    summary=dict(version=VERSION,projects_checked=len(results),model_audits=results,
        same_version_CSV_cases=sum('metrics' in r for r in results),new_Maxwell_solves=0,
        all_original_sources_preserved=True,project_complete=False,
        conclusions=['Object CS assignments are separate from directional constitutive law',
            'Missing GOES core-loss coverage prevents complete material-loss comparison',
            'Single cycle numerical pass is not periodic or mesh validation'])
    write_json(output/'review.json',summary)
    return summary


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    result=review(args.root,args.output)
    print(json.dumps({k:v for k,v in result.items() if k!='model_audits'},ensure_ascii=False,indent=2))
