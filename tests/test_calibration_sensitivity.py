"""Frozen-predictor sensitivity: pairing, no leakage/tuning, immutable evidence."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / 'tests'))
from test_calibration_transfer import fixtures
from modules.calibration_transfer import TransferBank
from modules.calibration_sensitivity import checked_ensembles, paired_draws, audit_fold, protocol
from tools.audit_calibration_native_sensitivity import run

ROOT = PROJECT.parent if PROJECT.name == 'magsim' else PROJECT
ARTIFACT = ROOT / 'calibration/generalization_20261005'
if (ARTIFACT / 'final').is_dir():
    ARTIFACT = ARTIFACT / 'final'
ARTIFACT = ARTIFACT / 'cal_65e506f6e207'


def sensitivity_fixture():
    samples = fixtures()
    ensembles = {}
    for i, s in enumerate(samples):
        item = dict(H=s['curves']['RD']['H'])
        for d in ('RD', 'TD'):
            raw = np.array(s['curves'][d]['raw_B'])
            perturb = np.array([0., .02, .04, .06]) * (i + 1)
            a = np.array([raw - perturb, raw + perturb])
            item[d] = dict(grain_ids=[1, 2], orientations_sha256=str(i), guarded_B=a, midpoint_B=a)
        ensembles[s['grade']] = item
    return samples, checked_ensembles(ensembles)


class SensitivityContracts(unittest.TestCase):
    def test_pairing_determinism_and_material_order(self):
        _, e = sensitivity_fixture()
        a = paired_draws(e, count=32, seed=16)
        b = paired_draws(dict(reversed(list(e.items()))), count=32, seed=16)
        for g in e:
            np.testing.assert_array_equal(a[g], b[g])
            rd = e[g]['RD']['guarded_B'][a[g]].mean(axis=1)
            td = e[g]['TD']['guarded_B'][a[g]].mean(axis=1)
            np.testing.assert_array_equal(rd, td)
        self.assertFalse(np.array_equal(a['S0'], a['S1']))
        self.assertFalse(protocol()['calibrated_material_confidence_interval'])

    def test_outer_reference_only_changes_scores_and_never_reselects(self):
        s, e = sensitivity_fixture()
        bank = TransferBank.fit(s[1:])
        draws = paired_draws(e, count=16)
        changed = copy.deepcopy(s[0])
        changed['curves']['RD']['reference_B'] = [0., .7, 1.4, 1.6]
        with patch('modules.calibration_transfer.select_candidate', side_effect=AssertionError('No re-selection')):
            a = audit_fold(s[0], bank, e, draws)
            b = audit_fold(changed, bank, e, draws)
        for first, second in zip(a, b):
            self.assertEqual({k: v for k, v in first.items() if k != 'scores'},
                {k: v for k, v in second.items() if k != 'scores'})
        self.assertNotEqual(a[0]['scores'], b[0]['scores'])
        self.assertNotIn('S0', a[0]['training_grades'])

    def test_zero_beta_has_no_native_sensitivity(self):
        s, e = sensitivity_fixture()
        bank = TransferBank.fit(s[1:])
        bank.candidate = dict(id='diagnostic_zero', kind='mean', native_beta=0., ridge=0.)
        result = audit_fold(s[0], bank, e, paired_draws(e, count=32))
        for r in result:
            for mode in r['sensitivity'].values():
                self.assertAlmostEqual(mode['B800_span_T'], 0.)
                self.assertAlmostEqual(mode['max_curve_span_T'], 0.)
            self.assertEqual(r['native_component_B_T'], [0.] * 4)

    def test_native_decomposition_and_query_only_range_are_analytic(self):
        s, e = sensitivity_fixture()
        bank = TransferBank.fit(s[1:])
        bank.candidate = dict(id='diagnostic_half', kind='mean', native_beta=.5, ridge=0.)
        draws = {g: np.array([[0, 0], [1, 1], [0, 1], [1, 0]]) for g in e}
        result = audit_fold(s[0], bank, e, draws)[0]
        perturb = np.array([-.04, .04, 0., 0.]) * .5
        self.assertAlmostEqual(result['sensitivity']['query_only']['B800_span_T'],
            float(np.quantile(perturb, .975) - np.quantile(perturb, .025)))
        proposal = np.array(result['reference_interpolation_B_T']) + result['native_component_B_T']
        np.testing.assert_allclose(proposal, result['frozen_prediction_B_T'], atol=1e-12)

    def test_pairing_finite_complete_h_and_stale_aggregate_gates(self):
        s, e = sensitivity_fixture()
        for mutate in (
                lambda x: x['S0']['TD'].update(orientations_sha256='different'),
                lambda x: x['S0']['TD'].update(grain_ids=[2, 1]),
                lambda x: x['S0']['TD']['guarded_B'].__setitem__((0, 1), np.nan),
                lambda x: x['S0']['H'].__setitem__(1, 99.)):
            changed = copy.deepcopy(e)
            mutate(changed)
            with self.assertRaises(ValueError):
                checked_ensembles(changed)
        bank = TransferBank.fit(s[1:])
        changed = copy.deepcopy(e)
        changed['S1']['RD']['guarded_B'][:, 1] += .1
        with self.assertRaisesRegex(ValueError, 'aggregate'):
            audit_fold(s[0], bank, changed, paired_draws(e, count=16))
        with self.assertRaisesRegex(ValueError, 'Held-out'):
            audit_fold(s[1], bank, e, paired_draws(e, count=16))
        draws = paired_draws(e, count=16)
        draws['S1'][0, 0] = 2
        with self.assertRaisesRegex(ValueError, 'indices'):
            audit_fold(s[0], bank, e, draws)

    def test_final_guard_zero_span_does_not_hide_pre_guard_sensitivity(self):
        s, e = sensitivity_fixture()
        for sample in s:
            for d in ('RD', 'TD'):
                sample['curves'][d]['reference_B'] = [0., 2.3, 2.4, 2.5]
        bank = TransferBank.fit(s[1:])
        bank.candidate = dict(id='diagnostic_half', kind='mean', native_beta=.5, ridge=0.)
        row = audit_fold(s[0], bank, e, paired_draws(e, count=32))[0]
        q = row['sensitivity']['query_only']
        self.assertEqual(q['B800_span_T'], 0.)
        self.assertGreater(q['pre_final_guard_B800_span_T'], .01)
        self.assertGreater(q['max_imposed_final_guard_change_T'], .1)

    def test_real_frozen_artifact_reproduced_without_changing_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'new_diagnostic'
            report = run(ROOT, ARTIFACT, output)
            self.assertAlmostEqual(report['summary']['scores']['frozen_native_v2']['mean_rmse_T'],
                .04404264729019529)
            self.assertEqual(len(report['folds']), 8)
            integrity = json.loads((output / 'source_integrity.json').read_text())
            self.assertTrue(integrity['unchanged'])
            self.assertEqual(integrity['source_file_counts']['pilot'], 525)
            for row in report['folds']:
                self.assertNotIn(row['grade'], row['training_grades'])
            with self.assertRaisesRegex(ValueError, 'new output'):
                run(ROOT, ARTIFACT, output)
            with self.assertRaisesRegex(ValueError, 'outside'):
                run(ROOT, ARTIFACT, ARTIFACT / 'forbidden_new_child')


if __name__ == '__main__':
    unittest.main()
