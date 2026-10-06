"""Audit unchanged physical-H source segments on CPU; never fit or solve."""
import argparse
import json
import sys
from pathlib import Path
import numpy as np
PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT))
from modules.maxwell_material_transport import load_contract,digest
from modules.motor_workbench import write_json


def audit(root,output):
    if output.exists() or not output.is_relative_to(root):
        raise ValueError('New workspace analysis directory required')
    folder=root/'calibration/generalization_20261005/final/cal_65e506f6e207/materials'
    files=sorted(folder.glob('*.amat'))
    if len(files)!=4:raise ValueError('Frozen four-material package required')
    rows=[];before={}
    for path in files:
        contract=load_contract(path)
        before[path.relative_to(root).as_posix()]=digest(path)
        before[path.with_suffix('.metadata.json').relative_to(root).as_posix()]=digest(path.with_suffix('.metadata.json'))
        for direction in ('RD','TD'):
            curve=contract['curves'][direction];h=np.array(curve['H']);b=np.array(curve['B'])
            slope=np.diff(b)/np.diff(h);mu=slope/(4e-7*np.pi)
            if np.any(slope<=0):raise ValueError('Inverse source curve not strictly single-valued')
            secant=b[1:]/((4e-7*np.pi)*h[1:])
            changes=np.diff(slope)
            B20=float(np.interp(20.,h,b));H20=float(np.interp(B20,b,h))
            rows.append(dict(material=contract['material_name'],direction=direction,point_count=len(h),
                H_A_per_m=h.tolist(),B_T=b.tolist(),segment_dB_dH_H_per_m=slope.tolist(),
                segment_differential_mu_r=mu.tolist(),node_secant_mu_r=secant.tolist(),
                minimum_differential_mu_r=float(mu.min()),maximum_differential_mu_r=float(mu.max()),
                slope_increase_count=int(np.sum(changes>1e-14)),slope_decrease_count=int(np.sum(changes<-1e-14)),
                positive_segment_slope_all=True,source_B_at_H20_T=B20,piecewise_inverse_H_at_B20_A_per_m=H20,
                all_original_points_retained=True,smoothing_applied=False,H_rescaled=False,
                interpretation='Source segment calculation only; not Maxwell internal interpolation or nonlinear residual history'))
    if not all(digest(root/p)==h for p,h in before.items()):raise ValueError('Frozen material changed')
    output.mkdir(parents=True)
    report=dict(version='fixed_H_source_segment_audit_v1',producer_sha256=digest(Path(__file__)),
                source_sha256=before,curves=rows,new_native_solves=0,new_training_runs=0,
                native_interpolation_verified=False,solver_failure_cause_identified=False)
    write_json(output/'report.json',report)
    (output/'frozen_producer.py').write_bytes(Path(__file__).read_bytes())
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True);p.add_argument('--output-dir',type=Path,required=True)
    a=p.parse_args();r=audit(a.root.resolve(),a.output_dir.resolve())
    print(json.dumps([dict(material=c['material'],direction=c['direction'],min_mu=c['minimum_differential_mu_r'],max_mu=c['maximum_differential_mu_r'],inverse_H20=c['piecewise_inverse_H_at_B20_A_per_m']) for c in r['curves']],indent=2))
