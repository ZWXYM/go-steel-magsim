"""Small-sample residual transfer with whole-material nested selection.

The f_Goss slope is a conditional engineering proxy, not an independently
identified composition/texture effect. No outer reference enters selection.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import numpy as np

from modules.material_calibration import (CalibrationBank, PHYSICS_VERSION,
    VERSION as LEGACY_VERSION, features, guard_B, validated_curve)
from modules.texture_sampling import LEGACY_SAMPLING_VERSION, SAMPLING_VERSION

VERSION = 'fixed_h_nested_transfer_v2'
# Ordered before evaluation; exact ties prefer earlier, simpler candidates.
CANDIDATES = (
    dict(id='legacy_idw',kind='idw',native_beta=1.,ridge=0.),
    dict(id='mean_delta',kind='mean',native_beta=1.,ridge=0.),
    *tuple(dict(id=f'goss_ridge_{ridge:g}_beta_{beta:g}',kind='goss_ridge',
                native_beta=beta,ridge=ridge)
           for ridge in (1.,.1,0.) for beta in (1.,.5)),
)

def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),
        allow_nan=False).encode()).hexdigest()

def protocol():
    return dict(calibration_version=VERSION,candidates=copy.deepcopy(list(CANDIDATES)),
        selection='inner_leave_one_whole_material_out; shared RD/TD candidate',
        selection_loss='equal-material/equal-direction mean(RMSE_H>=100_T + abs(B800_error_T))',
        metric_windows='legacy unchanged: RMSE H>=100; low 100<=H<=1000; high H>=5000 A/m',
        minimum_outer_materials=4,minimum_selection_materials=3,
        f_Goss_scale=.1,max_signed_weight_L1=3.,
        exact_known_anchor='all four material parameters match; use its residual',
        correction='beta*B_native + sum(w*(B_reference - beta*B_native_anchor))',
        physical_H_unchanged=True,guard='existing monotone_J_Msat_prior',
        outer_target_use='metrics only after training-only selection and prediction',
        reference_only_control='same outer split; legacy IDW with beta=0; never a candidate',
        limitations=['four reused processed references; no new prospective external batch',
            'collinear reported texture parameters; independent Si/texture effects unidentified',
            'no calibrated hysteresis or loss; native n8 sampling remains unconverged'])

def checked_samples(samples):
    result=copy.deepcopy(sorted(samples,key=lambda s:s['grade']))
    grades=[s['grade'] for s in result]
    if len(grades)<2 or len(set(grades))!=len(grades):
        raise ValueError('Transfer requires at least two distinct material samples')
    for sample in result:
        features(sample['params'])
        for d in ('RD','TD'):
            c=sample['curves'][d]
            h,_=validated_curve(c['H'],c['raw_B'])
            validated_curve(h,c['reference_B'])
            if h[0]>800 or h[-1]<800:
                raise ValueError('Training reference must support B800')
    return result

def config(identifier):
    for candidate in CANDIDATES:
        if candidate['id']==identifier:
            return dict(candidate)
    raise ValueError('Unknown frozen transfer candidate')

def weights(samples,params,candidate):
    X=np.array([features(s['params']) for s in samples])
    p=features(params)
    dist=np.linalg.norm(X-p,axis=1)
    exact=dist<1e-12
    limited=False
    if np.any(exact):
        w=exact/exact.sum()
    elif candidate['kind']=='idw':
        w=1/np.maximum(dist,1e-12)
        w/=w.sum()
    elif candidate['kind']=='mean':
        w=np.full(len(samples),1/len(samples))
    else:
        x=np.array([s['params']['f_Goss']/.1 for s in samples])
        z=x-x.mean()
        denominator=float(z@z)+candidate['ridge']
        mean=np.full(len(samples),1/len(samples))
        w=mean if denominator<1e-14 else mean+(params['f_Goss']/.1-x.mean())*z/denominator
        if np.abs(w).sum()>3.:
            # Shrink to the intercept rather than silently allowing unbounded
            # signed extrapolation. The fixed cap is never fit to outer errors.
            low,high=0.,1.
            for _ in range(50):
                mid=(low+high)/2
                if np.abs(mean+mid*(w-mean)).sum()>3.:
                    high=mid
                else:
                    low=mid
            w=mean+low*(w-mean)
            limited=True
    return w,dict(outside_feature_box=bool(np.any(p<X.min(axis=0)-1e-12)
        or np.any(p>X.max(axis=0)+1e-12)),signed_weight_L1=float(np.abs(w).sum()),
        extrapolation_limited=limited,nearest_scaled_parameter_distance=float(dist.min()),
        composition_effect_identified=False)

def prediction(samples,H,B_raw,params,direction,candidate):
    h,b=validated_curve(H,B_raw)
    if direction not in ('RD','TD'):
        raise ValueError('Invalid direction')
    lower=max(s['curves'][direction]['H'][0] for s in samples)
    upper=min(s['curves'][direction]['H'][-1] for s in samples)
    if h[0]<lower or h[-1]>upper:
        raise ValueError('H outside transfer bank support')
    w,domain=weights(samples,params,candidate)
    beta=candidate['native_beta']
    residual=sum(v*(np.interp(h,s['curves'][direction]['H'],s['curves'][direction]['reference_B'])
        -beta*np.interp(h,s['curves'][direction]['H'],s['curves'][direction]['raw_B']))
        for v,s in zip(w,samples))
    proposed=beta*b+residual
    # Exact anchors still represent calibration, never an independent test.
    if any(np.linalg.norm(features(s['params'])-features(params))<1e-12 for s in samples):
        proposed=b+sum(v*(np.interp(h,s['curves'][direction]['H'],s['curves'][direction]['reference_B'])
            -np.interp(h,s['curves'][direction]['H'],s['curves'][direction]['raw_B']))
            for v,s in zip(w,samples))
    return proposed,w,domain

def error_metrics(H,B,reference):
    h,p=validated_curve(H,B)
    _,r=validated_curve(h,reference)
    error=p-r
    rmse=lambda mask:float(np.sqrt(np.mean(error[mask]**2))) if np.any(mask) else None
    return dict(rmse_T=rmse(h>=100),mae_T=float(np.mean(np.abs(error[h>=100]))),
        low_field_rmse_T=rmse((h>=100)&(h<=1000)),high_field_rmse_T=rmse(h>=5000),
        B800_error_T=float(np.interp(800,h,error)),B1000_error_T=float(np.interp(1000,h,error)))

def select_candidate(training_samples):
    """This interface accepts training samples ONLY, not an outer test sample."""
    samples=checked_samples(training_samples)
    if len(samples)<3:
        raise ValueError('内层材料选参至少需要三个训练材料（外层检验至少四个）')
    scores=[]
    for candidate in CANDIDATES:
        folds=[]
        for target in samples:
            training=[s for s in samples if s['grade']!=target['grade']]
            for d in ('RD','TD'):
                c=target['curves'][d]
                p,_,_=prediction(training,c['H'],c['raw_B'],target['params'],d,candidate)
                b,_=guard_B(c['H'],p)
                m=error_metrics(c['H'],b,c['reference_B'])
                folds.append(dict(heldout=target['grade'],direction=d,
                    training_grades=[s['grade'] for s in training],**m))
        loss=float(np.mean([f['rmse_T']+abs(f['B800_error_T']) for f in folds]))
        scores.append(dict(candidate=candidate['id'],selection_loss_T=loss,folds=folds))
    best=min(range(len(scores)),key=lambda i:(scores[i]['selection_loss_T'],i))
    return dict(candidate=scores[best]['candidate'],training_grades=[s['grade'] for s in samples],
        training_sha256=digest(samples),protocol_sha256=digest(protocol()),scores=scores)

class TransferBank:
    def __init__(self,payload):
        if (payload.get('calibration_version')!=VERSION or payload.get('physics_version')!=PHYSICS_VERSION
                or payload.get('H_axis')!='physical_A_per_m' or payload.get('protocol_sha256')!=digest(protocol())):
            raise ValueError('Unsupported transfer protocol/physics/H axis')
        self.payload=copy.deepcopy(payload)
        self.samples=checked_samples(payload['training_samples'])
        self.candidate=config(payload['selection']['candidate'])
        if payload['selection']['training_sha256']!=digest(self.samples):
            raise ValueError('Transfer training source/selection mismatch')
        if (payload['selection']['training_grades']!=[s['grade'] for s in self.samples]
                or payload['selection']['protocol_sha256']!=digest(protocol())):
            raise ValueError('Transfer selection membership/protocol mismatch')
        self.texture_sampling_version=payload['texture_sampling_version']
        if self.texture_sampling_version not in (LEGACY_SAMPLING_VERSION,SAMPLING_VERSION):
            raise ValueError('Unsupported texture sampling version')

    @classmethod
    def fit(cls,samples,texture_sampling_version=LEGACY_SAMPLING_VERSION,selection=None):
        samples=checked_samples(samples)
        selection=selection or select_candidate(samples)
        return cls(dict(calibration_version=VERSION,physics_version=PHYSICS_VERSION,
            texture_sampling_version=texture_sampling_version,H_axis='physical_A_per_m',
            status='experimental_nested_material_cv',protocol_sha256=digest(protocol()),
            training_samples=samples,selection=selection))

    @property
    def bank_sha256(self):
        return digest(self.payload)

    def correct(self,H,B_sim,params,*,direction,exclude_grades=(),weight_cap=1.,
                physics_version=PHYSICS_VERSION,texture_sampling_version=LEGACY_SAMPLING_VERSION):
        if physics_version!=PHYSICS_VERSION or texture_sampling_version!=self.texture_sampling_version:
            raise ValueError('Raw physics/sampling and transfer bank differ')
        if not 0<=weight_cap<=1:
            raise ValueError('weight_cap must be in [0, 1]')
        if exclude_grades:
            # A full-bank selected config has seen excluded references during
            # inner CV. Re-select on eligible samples; do not reuse that config.
            eligible=[s for s in self.samples if s['grade'] not in set(exclude_grades)]
            fold=TransferBank.fit(eligible,self.texture_sampling_version)
            result=fold.correct(H,B_sim,params,direction=direction,weight_cap=weight_cap,
                physics_version=physics_version,texture_sampling_version=texture_sampling_version)
            result['excluded_grades']=sorted(set(exclude_grades))
            return result
        h,b=validated_curve(H,B_sim)
        proposed,w,domain=prediction(self.samples,h,b,params,direction,self.candidate)
        exact=any(np.linalg.norm(features(s['params'])-features(params))<1e-12 for s in self.samples)
        guarded,guard=guard_B(h,b+weight_cap*(proposed-b))
        if weight_cap==0:
            guarded=b.copy()
        return dict(H=h.tolist(),B=guarded.tolist(),B_raw=b.tolist(),delta_B=(proposed-b).tolist(),
            calibration_version=VERSION,physics_version=physics_version,H_axis='physical_A_per_m',H_scale=1.,
            calibration_sha256=self.bank_sha256,texture_sampling_version=self.texture_sampling_version,
            anchor_weights={s['grade']:float(v) for s,v in zip(self.samples,w)},excluded_grades=[],
            transfer_candidate=self.candidate['id'],native_beta=self.candidate['native_beta'],
            effective_native_beta=1. if exact else self.candidate['native_beta'],
            prediction_mode='known_anchor_calibration' if exact else 'cross_material_transfer',
            selection_training_grades=self.payload['selection']['training_grades'],
            selection_training_sha256=self.payload['selection']['training_sha256'],
            protocol_sha256=digest(protocol()),guard=guard,status=self.payload['status'],**domain)

def load_bank(path):
    payload=json.loads(Path(path).read_text(encoding='utf-8'))
    return TransferBank(payload) if payload.get('calibration_version')==VERSION else CalibrationBank(payload)

def legacy_bank(samples,sampling):
    anchors=[]
    for s in samples:
        for d in ('RD','TD'):
            c=s['curves'][d]
            anchors.append(dict(grade=s['grade'],direction=d,params=s['params'],H=c['H'],
                delta_B=(np.array(c['reference_B'])-c['raw_B']).tolist(),texture_sampling_version=sampling))
    return CalibrationBank(dict(calibration_version=LEGACY_VERSION,physics_version=PHYSICS_VERSION,
        H_axis='physical_A_per_m',texture_sampling_version=sampling,anchors=anchors))

def nested_validation(samples,sampling=LEGACY_SAMPLING_VERSION):
    samples=checked_samples(samples)
    if len(samples)<4:
        raise ValueError('嵌套材料留出至少需要四个材料；少材料请使用原协议')
    rows,curves,folds,banks=[],[],[],{}
    for target in samples:
        training=[s for s in samples if s['grade']!=target['grade']]
        selection=select_candidate(training)
        bank=TransferBank.fit(training,sampling,selection)
        baseline=legacy_bank(training,sampling)
        folds.append(dict(heldout=target['grade'],selection=selection,bank_sha256=bank.bank_sha256))
        banks[target['grade']]=bank.payload
        for d in ('RD','TD'):
            c=target['curves'][d]
            held=bank.correct(c['H'],c['raw_B'],target['params'],direction=d,texture_sampling_version=sampling)
            old=baseline.correct(c['H'],c['raw_B'],target['params'],direction=d,texture_sampling_version=sampling)
            control,_,_=prediction(training,c['H'],c['raw_B'],target['params'],d,
                dict(kind='idw',native_beta=0.,ridge=0.))
            control,_=guard_B(c['H'],control)
            for name,b in (('raw',c['raw_B']),('legacy_material_holdout',old['B']),
                           ('nested_material_holdout',held['B']),('reference_only_control',control)):
                rows.append(dict(grade=target['grade'],direction=d,method=name,
                    selected_candidate=selection['candidate'] if name=='nested_material_holdout' else '',
                    **error_metrics(c['H'],b,c['reference_B'])))
            curves.append(dict(grade=target['grade'],direction=d,H=c['H'],raw_B=c['raw_B'],
                reference_B=c['reference_B'],legacy_holdout_B=old['B'],holdout_B=held['B'],
                reference_only_B=control.tolist(),holdout_weights=held['anchor_weights'],
                selected_candidate=selection['candidate'],domain={k:held[k] for k in
                    ('outside_feature_box','extrapolation_limited','signed_weight_L1')}))
    summaries={}
    for method in ('raw','legacy_material_holdout','nested_material_holdout','reference_only_control'):
        values=[r for r in rows if r['method']==method]
        summaries[method]=dict(mean_rmse_T=float(np.mean([r['rmse_T'] for r in values])),
            max_abs_B800_error_T=max(abs(r['B800_error_T']) for r in values),
            B800_0_05T_pass_all=all(abs(r['B800_error_T'])<.05 for r in values))
    return dict(protocol=protocol(),protocol_sha256=digest(protocol()),metrics=rows,comparisons=curves,
        outer_folds=folds,holdout_banks=banks,summaries=summaries,
        independently_validated=False,prospective_external_validation=False)
