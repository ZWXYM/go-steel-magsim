"""Opt-in low-native-weight model; the saved V2 candidate protocol stays exact."""
import copy
from modules.calibration_transfer import TransferBank,CANDIDATES,protocol as base_protocol,select_from_candidates

VERSION='fixed_h_regularized_transfer_v3'
REGULARIZED_CANDIDATES=tuple(copy.deepcopy(CANDIDATES))+tuple(
    dict(id=f'trial_goss_ridge_{ridge:g}_beta_0.25',kind='goss_ridge',native_beta=.25,ridge=ridge)
    for ridge in (1.,.1,0.))


def protocol():
    value=base_protocol()
    value.update(calibration_version=VERSION,candidates=copy.deepcopy(list(REGULARIZED_CANDIDATES)),
        development_status='explicit_optional_low_native_weight_candidate',automatic_deployment=False,
        default_eligible=False,development_basis='Frozen 20261020 retrospective trial; not new external validation')
    return value


def candidate_config(identifier):
    for candidate in REGULARIZED_CANDIDATES:
        if candidate['id']==identifier:return dict(candidate)
    raise ValueError('Unknown regularized transfer candidate')


def select_training(samples):return select_from_candidates(samples,REGULARIZED_CANDIDATES,protocol())


def default_eligible(report):
    return report.get('calibration_version')!=VERSION and report.get('default_eligible',True) is not False


class RegularizedTransferBank(TransferBank):
    BANK_VERSION=VERSION
    STATUS='optional_regularized_development_candidate'
    protocol_definition=staticmethod(protocol)
    candidate_config=staticmethod(candidate_config)
    select_training=staticmethod(select_training)
