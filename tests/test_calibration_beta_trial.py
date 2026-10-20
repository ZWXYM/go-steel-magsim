"""CPU trial remains source-bound, training-only and separate from deployment."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from modules.calibration_transfer import TransferBank, nested_validation
from tools.run_calibration_beta_trial import Budget, acceptance, evaluate, protocol, run, select
from test_calibration_transfer import fixtures


class BetaTrialTests(unittest.TestCase):
    def saved(self, samples):
        return dict(nested_validation(samples),id='fixture',bank_sha256=TransferBank.fit(samples).bank_sha256)

    def test_every_outer_reference_is_excluded_from_selection_and_prediction(self):
        samples=fixtures();saved=self.saved(samples)
        original=evaluate(samples,saved,Budget())
        for index,target in enumerate(samples):
            changed=copy.deepcopy(samples);report=copy.deepcopy(saved)
            for direction in ('RD','TD'):
                values=[v+.17 for v in changed[index]['curves'][direction]['reference_B']]
                changed[index]['curves'][direction]['reference_B']=values
                next(c for c in report['comparisons'] if (c['grade'],c['direction'])==(target['grade'],direction))['reference_B']=values
            other=evaluate(changed,report,Budget())
            self.assertEqual(original['outer_folds'][index],other['outer_folds'][index])
            for before,after in zip(original['curves'][2*index:2*index+2],other['curves'][2*index:2*index+2]):
                self.assertEqual(before['B_trial'],after['B_trial'])
                self.assertNotIn(target['grade'],before['weights'])
            for score in original['outer_folds'][index]['selection']['scores']:
                for fold in score['folds']:
                    self.assertNotIn(target['grade'],fold['training_grades'])
                    self.assertNotIn(fold['grade'],fold['training_grades'])

    def test_protocol_is_a_copy_and_budget_stops_before_extra_evaluation(self):
        plan=protocol();self.assertEqual(len(plan['candidates']),11)
        self.assertTrue(all(c['native_beta']>0 for c in plan['candidates']))
        plan['candidates'][0]['native_beta']=99
        self.assertEqual(protocol()['candidates'][0]['native_beta'],1)
        budget=Budget(dict(protocol(),max_prediction_evaluations=1))
        budget.consume()
        with self.assertRaisesRegex(ValueError,'budget'):budget.consume()
        self.assertEqual(budget.evaluations,1)

    def test_average_improvement_does_not_override_failed_pointwise_gate(self):
        summaries=dict(baseline_saved_nested=dict(mean_rmse_T=.044,max_abs_B800_error_T=.106,max_direction_RMSE_T=.094),
            trial_nested=dict(mean_rmse_T=.031,max_abs_B800_error_T=.060,max_direction_RMSE_T=.059,B800_all_below_0_05T=False))
        rows=[dict(method='baseline_saved_nested',grade='A',direction='RD',rmse_T=.05),
              dict(method='trial_nested',grade='A',direction='RD',rmse_T=.04)]
        gates,_=acceptance(summaries,rows)
        self.assertTrue(gates['mean_RMSE_improvement_at_least_10pct'])
        self.assertFalse(gates['all_B800_below_0_05T'])
        self.assertFalse(all(gates.values()))

    def test_run_writes_only_new_trial_outputs_and_never_replaces_bank(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory);samples=fixtures();bank=TransferBank.fit(samples);saved=self.saved(samples)
            path=base/'bank.json';path.write_text(json.dumps(bank.payload),encoding='utf8')
            (base/'report.json').write_text(json.dumps(saved),encoding='utf8')
            before={p.name:p.read_bytes() for p in base.iterdir() if p.is_file()}
            output=base/'trial';result=run(path,output)
            self.assertEqual(before,{p.name:p.read_bytes() for p in base.iterdir() if p.is_file()})
            self.assertFalse(result['default_prediction_changed']);self.assertFalse(result['automatic_deployment'])
            self.assertEqual(result['prediction_evaluations'],340)
            self.assertTrue((output/'frozen_protocol.json').is_file());self.assertTrue((output/'metrics.csv').is_file())
            self.assertFalse((output/'bank.json').exists());self.assertFalse(list(output.glob('*.amat')))
            evidence=(output/'results.json').read_bytes()
            with self.assertRaisesRegex(ValueError,'new output'):run(path,output)
            self.assertEqual(evidence,(output/'results.json').read_bytes())

    def test_incompatible_source_report_is_not_evaluated(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory);bank=TransferBank.fit(fixtures());path=base/'bank.json'
            path.write_text(json.dumps(bank.payload),encoding='utf8')
            (base/'report.json').write_text(json.dumps(dict(bank_sha256='wrong')),encoding='utf8')
            with self.assertRaisesRegex(ValueError,'matching'):run(path,base/'trial')
            self.assertFalse((base/'trial/results.json').exists())

    def test_required_material_count_and_finite_references_are_enforced(self):
        with self.assertRaisesRegex(ValueError,'three'):select(fixtures()[:2],Budget())
        with self.assertRaisesRegex(ValueError,'four'):evaluate(fixtures()[:3],{},Budget())
        samples=fixtures();samples[0]['curves']['RD']['reference_B'][1]=float('nan')
        with self.assertRaisesRegex(ValueError,'Non-finite'):evaluate(samples,{},Budget())


if __name__=='__main__':unittest.main()
