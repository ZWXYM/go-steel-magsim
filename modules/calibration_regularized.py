"""Opt-in transfer models with distinct, frozen selection protocols."""
import copy
from modules.calibration_transfer import TransferBank,CANDIDATES,protocol as base_protocol,select_from_candidates

VERSION='fixed_h_regularized_transfer_v3'
REFERENCE_DOMINANT_VERSION='fixed_h_reference_dominant_transfer_v4'
REGULARIZED_CANDIDATES=tuple(copy.deepcopy(CANDIDATES))+tuple(
    dict(id=f'trial_goss_ridge_{ridge:g}_beta_0.25',kind='goss_ridge',native_beta=.25,ridge=ridge)
    for ridge in (1.,.1,0.))
REFERENCE_DOMINANT_CANDIDATES=REGULARIZED_CANDIDATES+tuple(
    dict(id=f'reference_goss_ridge_{ridge:g}_beta_0',kind='goss_ridge',native_beta=0.,ridge=ridge)
    for ridge in (1.,.1,0.))


def protocol():
    value=base_protocol()
    value.update(calibration_version=VERSION,candidates=copy.deepcopy(list(REGULARIZED_CANDIDATES)),
        development_status='explicit_optional_low_native_weight_candidate',automatic_deployment=False,
        default_eligible=False,development_basis='Frozen 20261020 retrospective trial; not new external validation')
    return value


def candidate_config(identifier,candidates=REGULARIZED_CANDIDATES):
    for candidate in candidates:
        if candidate['id']==identifier:return dict(candidate)
    raise ValueError('Unknown regularized transfer candidate')


def select_training(samples):return select_from_candidates(samples,REGULARIZED_CANDIDATES,protocol())


def default_eligible(report):
    return report.get('calibration_version') not in (VERSION,REFERENCE_DOMINANT_VERSION) and report.get('default_eligible',True) is not False


class RegularizedTransferBank(TransferBank):
    BANK_VERSION=VERSION
    STATUS='optional_regularized_development_candidate'
    DISPLAY_LABEL='低微磁权重开发候选'
    DEVELOPMENT_STATUS='explicit_optional_regularized_candidate'
    DEVELOPMENT_TRIAL='native_residual_beta_quarter_trial_v1'
    protocol_definition=staticmethod(protocol)
    candidate_config=staticmethod(candidate_config)
    select_training=staticmethod(select_training)


class ReferenceDominantTransferBank(RegularizedTransferBank):
    BANK_VERSION=REFERENCE_DOMINANT_VERSION
    STATUS='optional_reference_dominant_development_candidate'
    DISPLAY_LABEL='参考曲线主导开发候选'
    DEVELOPMENT_STATUS='explicit_optional_reference_dominant_candidate'
    DEVELOPMENT_TRIAL='offline_zero_residual_nested_trial_v1'

    @staticmethod
    def protocol_definition():
        return dict(protocol(),calibration_version=REFERENCE_DOMINANT_VERSION,
                    candidates=copy.deepcopy(list(REFERENCE_DOMINANT_CANDIDATES)),
                    development_status=ReferenceDominantTransferBank.DEVELOPMENT_STATUS,
                    development_basis='20261111 same-reference nested development trial including beta zero')

    @staticmethod
    def candidate_config(identifier):
        return candidate_config(identifier,REFERENCE_DOMINANT_CANDIDATES)

    @staticmethod
    def select_training(samples):
        return select_from_candidates(samples,REFERENCE_DOMINANT_CANDIDATES,ReferenceDominantTransferBank.protocol_definition())


OPTIONAL_BANK_TYPES={cls.BANK_VERSION:cls for cls in (RegularizedTransferBank,ReferenceDominantTransferBank)}
