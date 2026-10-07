"""Resolve the effective whole-material bank once for both directions."""
import copy
from modules.calibration_transfer import TransferBank
from modules.calibration_domain import bank_parameter_support
from modules.material_calibration import CalibrationBank, PHYSICS_VERSION
from modules.texture_sampling import LEGACY_SAMPLING_VERSION

VERSION='whole_material_prediction_scope_v1'


def resolve_prediction_bank(bank,exclude_grades=()):
    if not isinstance(exclude_grades,(list,tuple)) or any(not isinstance(g,str) or not g for g in exclude_grades):
        raise ValueError('Excluded grades must be an explicit list of material names')
    excluded=sorted(set(exclude_grades))
    rows=bank.payload.get('training_samples',bank.payload.get('anchors',[]))
    grades=sorted({r['grade'] for r in rows})
    if set(excluded)-set(grades):raise ValueError('Excluded material is absent from this source bank')
    effective=bank
    if excluded:
        if isinstance(bank,TransferBank):
            eligible=[s for s in bank.samples if s['grade'] not in excluded]
            effective=TransferBank.fit(eligible,bank.texture_sampling_version)
        elif isinstance(bank,CalibrationBank):
            payload=copy.deepcopy(bank.payload)
            payload['anchors']=[a for a in payload['anchors'] if a['grade'] not in excluded]
            if {a['direction'] for a in payload['anchors']}!={'RD','TD'}:
                raise ValueError('Exclusion must retain both directional training anchors')
            effective=CalibrationBank(payload)
        else:raise ValueError('Unsupported prediction bank')
    eligible=effective.payload.get('training_samples',effective.payload.get('anchors',[]))
    scope=dict(version=VERSION,mode='whole_material_exclusion' if excluded else 'all_training_materials',
        excluded_grades=excluded,source_calibration_sha256=bank.bank_sha256,
        effective_calibration_sha256=effective.bank_sha256,
        effective_training_grades=sorted({r['grade'] for r in eligible}),
        selection_rerun_after_exclusion=bool(excluded and isinstance(bank,TransferBank)),
        excluded_references_used_for_selection=False,physical_H_unchanged=True,
        prospective_external_validation=False)
    return effective,scope


def predict_pair(raw_pair,bank,exclude_grades=(),*,include_bank=False):
    version=raw_pair['simulation_physics_version']
    if version!=PHYSICS_VERSION:raise ValueError('Raw simulation has an incompatible physics version')
    sampling=raw_pair.get('texture_sampling_version',LEGACY_SAMPLING_VERSION)
    effective,scope=resolve_prediction_bank(bank,exclude_grades)
    support=bank_parameter_support(effective,raw_pair['params'])
    result={}
    for direction in ('RD','TD'):
        curve=raw_pair[direction]
        corrected=effective.correct(curve['H'],curve['B'],raw_pair['params'],direction=direction,
            physics_version=version,texture_sampling_version=sampling)
        if corrected['calibration_sha256']!=effective.bank_sha256:
            raise ValueError('Direction does not identify its effective calibration bank')
        corrected.update(excluded_grades=scope['excluded_grades'],prediction_scope=copy.deepcopy(scope),
            source_calibration_sha256=scope['source_calibration_sha256'],parameter_support=copy.deepcopy(support))
        result[direction]=corrected
    result.update(material_id=raw_pair['material_id'],params=copy.deepcopy(raw_pair['params']),
        simulation_physics_version=version,texture_sampling_version=sampling,
        calibration_sha256=effective.bank_sha256,source_calibration_sha256=bank.bank_sha256,
        prediction_scope=scope,parameter_support=support)
    if include_bank:result['_effective_bank_payload']=copy.deepcopy(effective.payload)
    return result
