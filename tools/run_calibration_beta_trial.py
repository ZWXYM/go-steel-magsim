"""Frozen CPU-only residual-shrinkage development trial; never deploy a bank."""
from __future__ import annotations
import argparse
import copy
import csv
import json
import sys
import time
from pathlib import Path
import numpy as np

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from modules.calibration_transfer import (CANDIDATES, TransferBank, checked_samples, digest,
    error_metrics, load_bank, prediction, protocol as existing_protocol)
from modules.material_calibration import file_hash, guard_B

VERSION = 'native_residual_beta_quarter_trial_v1'


def protocol():
    candidates=copy.deepcopy(list(CANDIDATES))+[
        dict(id=f'trial_goss_ridge_{r:g}_beta_0.25',kind='goss_ridge',native_beta=.25,ridge=r)
        for r in (1.,.1,0.)]
    if len(candidates)!=11:raise ValueError('Original candidate registry changed; create a new trial protocol')
    return dict(version=VERSION,candidates=candidates,outer='leave one whole material, RD and TD together',
        inner='leave one whole material from the remaining three',
        selection_loss='mean(RMSE_H_ge_100_T + abs(B800_error_T)); equal materials and directions',
        candidate_design_context='Retrospective development using four previously inspected processed references; no new independent validation',
        unchanged_rules=dict(f_Goss_scale=.1,max_signed_weight_L1=3.,guard='monotone_J_Msat_prior',physical_H=True),
        reference_only_control='separate saved control, never a selectable candidate',
        acceptance=dict(relative_mean_RMSE_improvement=.1,worst_B800_not_increased=True,
            worst_direction_RMSE_not_increased=True,max_single_direction_RMSE_regression_T=.01,all_abs_B800_below_T=.05),
        max_prediction_evaluations=420,max_CPU_wall_seconds=60,new_native_solves=0,new_ML_training=0,
        new_saved_predictions=0,automatic_deployment=False,prospective_external_validation=False)


class Budget:
    def __init__(self,plan=None):
        self.plan=plan or protocol();self.started=time.perf_counter();self.evaluations=0
    def consume(self):
        if self.evaluations>=self.plan['max_prediction_evaluations'] or time.perf_counter()-self.started>self.plan['max_CPU_wall_seconds']:
            raise ValueError('Frozen CPU trial budget reached; partial output preserved')
        self.evaluations+=1


def predict(training,H,raw,params,direction,candidate,budget):
    budget.consume()
    proposed,w,domain=prediction(training,H,raw,params,direction,candidate)
    b,guard=guard_B(H,proposed)
    return b.tolist(),dict(weights={s['grade']:float(v) for s,v in zip(training,w)},domain=domain,guard=guard)


def select(training,budget):
    """Only training samples enter selection; there is no outer-target argument."""
    training=checked_samples(training)
    if len(training)!=3:raise ValueError('This frozen trial requires three inner-selection materials')
    scores=[]
    for candidate in budget.plan['candidates']:
        folds=[]
        for target in training:
            eligible=[s for s in training if s['grade']!=target['grade']]
            for direction in ('RD','TD'):
                c=target['curves'][direction]
                b,_=predict(eligible,c['H'],c['raw_B'],target['params'],direction,candidate,budget)
                folds.append(dict(grade=target['grade'],direction=direction,
                    training_grades=[s['grade'] for s in eligible],**error_metrics(c['H'],b,c['reference_B'])))
        scores.append(dict(candidate=candidate['id'],loss_T=float(np.mean([
            m['rmse_T']+abs(m['B800_error_T']) for m in folds])),folds=folds))
    best=min(range(len(scores)),key=lambda i:(scores[i]['loss_T'],i))
    return copy.deepcopy(budget.plan['candidates'][best]),dict(candidate=scores[best]['candidate'],
        training_grades=[s['grade'] for s in training],training_sha256=digest(training),scores=scores)


def acceptance(summaries,rows):
    base,trial=summaries['baseline_saved_nested'],summaries['trial_nested']
    regression=max(next(r['rmse_T'] for r in rows if r['method']=='trial_nested' and
        (r['grade'],r['direction'])==(b['grade'],b['direction']))-b['rmse_T']
        for b in rows if b['method']=='baseline_saved_nested')
    gates=dict(mean_RMSE_improvement_at_least_10pct=trial['mean_rmse_T']<=.9*base['mean_rmse_T'],
        worst_B800_not_increased=trial['max_abs_B800_error_T']<=base['max_abs_B800_error_T'],
        worst_direction_RMSE_not_increased=trial['max_direction_RMSE_T']<=base['max_direction_RMSE_T'],
        max_single_direction_regression_at_most_0_01T=regression<=.01,
        all_B800_below_0_05T=trial['B800_all_below_0_05T'])
    return gates,regression


def evaluate(samples,saved,budget):
    samples=checked_samples(samples)
    if len(samples)!=4:raise ValueError('This trial requires four complete material samples')
    rows=[];curves=[];folds=[]
    for target in samples:
        training=[s for s in samples if s['grade']!=target['grade']]
        candidate,selection=select(training,budget)
        folds.append(dict(heldout=target['grade'],selection=selection))
        for direction in ('RD','TD'):
            c=target['curves'][direction]
            b,details=predict(training,c['H'],c['raw_B'],target['params'],direction,candidate,budget)
            matches=[r for r in saved['comparisons'] if (r['grade'],r['direction'])==(target['grade'],direction)]
            if len(matches)!=1:raise ValueError('Saved baseline membership is missing or duplicated')
            old=matches[0]
            if any(old[key]!=c[source] for key,source in [('H','H'),('raw_B','raw_B'),('reference_B','reference_B')]):
                raise ValueError('Saved baseline and source curves differ')
            for method,p in [('baseline_saved_nested',old['holdout_B']),('trial_nested',b),('reference_only_control',old['reference_only_B'])]:
                rows.append(dict(grade=target['grade'],direction=direction,method=method,**error_metrics(c['H'],p,c['reference_B'])))
            curves.append(dict(grade=target['grade'],direction=direction,H=c['H'],B_trial=b,
                B_baseline=old['holdout_B'],B_reference=c['reference_B'],candidate=candidate['id'],**details))
    # Independent counterfactual: changing the outer reference cannot change its prediction.
    target=copy.deepcopy(samples[-1]);training=[s for s in samples if s['grade']!=target['grade']]
    candidate,selection=select(training,budget);c=target['curves']['TD']
    before,_=predict(training,c['H'],c['raw_B'],target['params'],'TD',candidate,budget)
    c['reference_B']=[v+.123 for v in c['reference_B']]
    after,_=predict(training,c['H'],c['raw_B'],target['params'],'TD',candidate,budget)
    if before!=after or selection!=folds[-1]['selection']:raise ValueError('Outer reference changed prediction or selection')
    summaries={}
    for method in ('baseline_saved_nested','trial_nested','reference_only_control'):
        group=[r for r in rows if r['method']==method]
        summaries[method]=dict(mean_rmse_T=float(np.mean([r['rmse_T'] for r in group])),
            max_abs_B800_error_T=max(abs(r['B800_error_T']) for r in group),
            max_direction_RMSE_T=max(r['rmse_T'] for r in group),
            B800_all_below_0_05T=all(abs(r['B800_error_T'])<.05 for r in group))
    gates,regression=acceptance(summaries,rows)
    return dict(version=VERSION,summaries=summaries,gates=gates,all_acceptance_gates_passed=all(gates.values()),
        metrics=rows,curves=curves,outer_folds=folds,max_single_direction_RMSE_regression_T=regression,
        outer_reference_counterfactual_unchanged=True,prediction_evaluations=budget.evaluations,
        CPU_seconds=time.perf_counter()-budget.started,existing_bank_unchanged=True,
        default_prediction_changed=False,prospective_external_validation=False,new_native_solves=0,
        new_ML_training=0,new_saved_predictions=0,automatic_deployment=False)


def write_json(path,value):
    path.write_bytes(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False).encode())


def run(bank_path,output):
    bank_path=Path(bank_path).resolve();output=Path(output).resolve()
    if output.exists():raise ValueError('Use a new output directory; existing results are never overwritten')
    output.mkdir(parents=True)
    plan=protocol();write_json(output/'frozen_protocol.json',plan) # before loading target data
    budget=Budget(plan)
    report_path=bank_path.parent/'report.json'
    sources={p.name:file_hash(p) for p in (bank_path,report_path)}
    bank=load_bank(bank_path);saved=json.loads(report_path.read_text(encoding='utf-8-sig'))
    if not isinstance(bank,TransferBank) or saved.get('bank_sha256')!=bank.bank_sha256:
        raise ValueError('A matching saved nested calibration bank/report is required')
    write_json(output/'source_hashes.json',dict(calibration=saved['id'],bank_sha256=bank.bank_sha256,
        files=sources,existing_protocol_sha256=digest(existing_protocol()),program_sha256=file_hash(__file__),
        transfer_code_sha256=file_hash(PROJECT/'modules/calibration_transfer.py')))
    result=evaluate(bank.samples,saved,budget)
    if any(file_hash(p)!=sources[p.name] for p in (bank_path,report_path)):
        raise ValueError('Source changed during the trial')
    result.update(frozen_protocol_sha256=digest(plan),source_hashes_unchanged=True)
    write_json(output/'results.json',result)
    with (output/'metrics.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(result['metrics'][0]));writer.writeheader();writer.writerows(result['metrics'])
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bank',required=True,type=Path)
    parser.add_argument('--output-dir',required=True,type=Path)
    args=parser.parse_args()
    result=run(args.bank,args.output_dir)
    print(json.dumps({k:result[k] for k in ('summaries','gates','all_acceptance_gates_passed','prediction_evaluations','CPU_seconds','default_prediction_changed')},indent=2))


if __name__=='__main__':main()
