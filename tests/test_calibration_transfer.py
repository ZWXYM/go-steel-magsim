"""Material selection leakage, bounded extrapolation and export contracts."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT))
sys.path.insert(0,str(PROJECT/'modules'))
from modules.calibration_transfer import (TransferBank, VERSION, checked_samples,
    config, digest, load_bank, nested_validation, protocol, select_candidate, weights)
from modules.material_calibration import PHYSICS_VERSION
from modules.maxwell_exporter import export_calibrated_pair
from modules.reference_corrector import apply_reference_correction
from modules.calibration_workbench import CalibrationWorkbench, GRADES
from modules.workbench_routes import create_workbench
from flask import Flask

ROOT=PROJECT.parent if PROJECT.name=='magsim' and (PROJECT.parent/'calibration/pilot_20261003_n8/manifest.json').is_file() else PROJECT

def fixtures():
    samples=[]
    for i,fraction in enumerate((.65,.75,.85,.95)):
        curves={}
        for d in ('RD','TD'):
            delta=np.array([0.,.1,.2,.2])+(fraction-.65)*np.array([0.,.05,.1,.1])
            raw=np.array([0.,.3,.7,.8])
            curves[d]=dict(H=[0.,100.,800.,1000.],raw_B=raw.tolist(),reference_B=(raw+delta).tolist())
        samples.append(dict(grade=f'S{i}',params=dict(f_Goss=fraction,theta_0_deg=5.,halfwidth_deg=8.,Si_content=3.),curves=curves))
    return samples

class TransferContracts(unittest.TestCase):
    def test_outer_reference_cannot_change_its_selection_bank_or_prediction(self):
        original=fixtures()
        first=nested_validation(original)
        changed=copy.deepcopy(original)
        for d in ('RD','TD'):
            changed[0]['curves'][d]['reference_B']=[0.,.5,1.5,1.6]
        second=nested_validation(changed)
        for key in ('holdout_banks','outer_folds'):
            if key=='holdout_banks':
                self.assertEqual(first[key]['S0'],second[key]['S0'])
            else:
                self.assertEqual(first[key][0],second[key][0])
        for a,b in zip(first['comparisons'][:2],second['comparisons'][:2]):
            self.assertEqual(a['holdout_B'],b['holdout_B'])
            self.assertNotIn('S0',a['holdout_weights'])
        self.assertNotEqual(first['metrics'][2]['rmse_T'],second['metrics'][2]['rmse_T'])

    def test_exclude_reselects_without_reusing_full_bank_selection(self):
        samples=fixtures()
        changed=copy.deepcopy(samples)
        for d in ('RD','TD'):
            changed[0]['curves'][d]['reference_B']=[0.,.7,1.7,1.8]
        old,new=TransferBank.fit(samples),TransferBank.fit(changed)
        c=samples[0]['curves']['RD']
        one=old.correct(c['H'],c['raw_B'],samples[0]['params'],direction='RD',exclude_grades=['S0'])
        two=new.correct(c['H'],c['raw_B'],samples[0]['params'],direction='RD',exclude_grades=['S0'])
        self.assertEqual(one['B'],two['B'])
        self.assertEqual(one['selection_training_sha256'],two['selection_training_sha256'])
        self.assertNotIn('S0',one['selection_training_grades'])
        self.assertNotIn('S0',one['anchor_weights'])
        self.assertEqual(one['excluded_grades'],['S0'])

    def test_affine_reproduces_linear_residual_and_limits_far_extrapolation(self):
        samples=fixtures()[:3]
        params={**samples[0]['params'],'f_Goss':.80}
        w,domain=weights(samples,params,config('goss_ridge_0_beta_1'))
        self.assertAlmostEqual(sum(w),1.)
        self.assertAlmostEqual(sum(w[i]*s['params']['f_Goss'] for i,s in enumerate(samples)),.80)
        self.assertFalse(domain['extrapolation_limited'])
        w,domain=weights(samples,{**params,'f_Goss':4.},config('goss_ridge_0_beta_1'))
        self.assertTrue(domain['extrapolation_limited'])
        self.assertTrue(domain['outside_feature_box'])
        self.assertLessEqual(float(np.abs(w).sum()),3.+1e-12)
        self.assertAlmostEqual(sum(w),1.)

    def test_known_fit_disabled_identity_and_physical_versions(self):
        samples=fixtures()
        bank=TransferBank.fit(samples)
        sample=samples[0]
        c=sample['curves']['RD']
        fit=bank.correct(c['H'],c['raw_B'],sample['params'],direction='RD')
        np.testing.assert_allclose(fit['B'],c['reference_B'],atol=1e-12)
        self.assertEqual(fit['H'],c['H'])
        negative=[0.,-.2,.7,.8]
        identity=bank.correct(c['H'],negative,sample['params'],direction='RD',weight_cap=0)
        self.assertEqual(identity['B'],negative)
        for kwargs in ({'physics_version':'legacy'},{'texture_sampling_version':'goss_haar_iid_prefix_v2'},
                       {'weight_cap':float('nan')},{'direction':'ND'}):
            with self.assertRaises(ValueError):
                bank.correct(c['H'],c['raw_B'],sample['params'],**({'direction':'RD'}|kwargs))
        with self.assertRaises(ValueError):
            bank.correct([0.,100.,800.,2000.],c['raw_B'],sample['params'],direction='RD')

    def test_inner_partition_and_order_are_deterministic_and_whole_material(self):
        samples=fixtures()
        a=select_candidate(samples)
        self.assertEqual(a,select_candidate(samples[::-1]))
        self.assertEqual(len(a['scores']),8)
        for score in a['scores']:
            for fold in score['folds']:
                self.assertNotIn(fold['heldout'],fold['training_grades'])
                self.assertEqual(len(fold['training_grades']),3)
        with self.assertRaises(ValueError):
            nested_validation(samples[:3])
        with self.assertRaises(ValueError):
            select_candidate(samples[:2])
        with self.assertRaises(ValueError):
            checked_samples(samples+[samples[0]])

    def test_native_residual_sensitivity_matches_recorded_effective_beta(self):
        samples=fixtures()
        selection=select_candidate(samples)
        selection['candidate']='goss_ridge_0_beta_0.5'
        bank=TransferBank.fit(samples,selection=selection)
        sample=samples[0]
        c=sample['curves']['RD']
        params={**sample['params'],'f_Goss':.80}
        one=bank.correct(c['H'],c['raw_B'],params,direction='RD')
        perturbed=[v+(0 if i==0 else .02) for i,v in enumerate(c['raw_B'])]
        two=bank.correct(c['H'],perturbed,params,direction='RD')
        np.testing.assert_allclose(np.array(two['B'])-one['B'],[0,.01,.01,.01],atol=1e-12)
        self.assertEqual(one['effective_native_beta'],.5)
        self.assertEqual(one['prediction_mode'],'cross_material_transfer')
        known=bank.correct(c['H'],perturbed,sample['params'],direction='RD')
        self.assertEqual(known['effective_native_beta'],1.)
        self.assertEqual(known['prediction_mode'],'known_anchor_calibration')

    def test_loaded_model_explicit_runtime_and_native_export_agree(self):
        samples=fixtures()
        bank=TransferBank.fit(samples)
        sample=samples[1]
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'bank.json'
            path.write_text(json.dumps(bank.payload))
            rd=load_bank(path).correct(sample['curves']['RD']['H'],sample['curves']['RD']['raw_B'],sample['params'],direction='RD')
            td=bank.correct(sample['curves']['TD']['H'],sample['curves']['TD']['raw_B'],sample['params'],direction='TD')
            output=apply_reference_correction(rd['H'],rd['B_raw'],sample['params'],direction='RD',
                calibration_path=str(path),physics_version=PHYSICS_VERSION)
            np.testing.assert_allclose(output,rd['B'])
            exported=Path(export_calibrated_pair(rd,td,'TransferFixture',thickness_mm=.3,export_dir=tmp))
            metadata=json.loads(exported.with_suffix('.metadata.json').read_text())
            self.assertEqual(metadata['reference_correction_version'],VERSION)
            self.assertEqual(metadata['selection_training_sha256'],bank.payload['selection']['training_sha256'])
            self.assertEqual(metadata['core_loss_status'],'uncalibrated_zero_placeholders')
            self.assertFalse(metadata['prospective_external_validation'])
            import pandas as pd
            from modules.dataset_contract import dataset_contract
            contract={k:metadata[k] for k in ('simulation_physics_version',
                'reference_correction_version','H_axis','calibration_sha256','texture_sampling_version')}
            with self.assertRaisesRegex(ValueError,'原生质量合同'):
                dataset_contract(pd.DataFrame([contract]))
            from modules.native_quality import VERSION as QUALITY_VERSION
            screened_fixture={**contract,'native_quality_contract_version':QUALITY_VERSION,
                'native_quality_status':'numerical_screen_passed','native_quality_sha256':'0'*64,
                'strict_training_eligible':True}
            self.assertEqual(dataset_contract(pd.DataFrame([screened_fixture]))['reference_correction_version'],VERSION)
            with self.assertRaisesRegex(ValueError,'H 轴'):
                dataset_contract(pd.DataFrame([{**contract,'H_axis':'legacy_scaled'}]))
            changed=copy.deepcopy(bank.payload)
            changed['training_samples'][0]['curves']['RD']['raw_B'][1]+=.1
            path.write_text(json.dumps(changed))
            with self.assertRaisesRegex(ValueError,'source/selection'):
                load_bank(path)

    def test_real_four_sample_workbench_new_artifact_and_legacy_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager=CalibrationWorkbench(ROOT,Path(tmp))
            source=(manager.pilot/'calibration_bank.json').read_bytes()
            report=manager.calibrate(list(GRADES),VERSION)
            self.assertEqual(report['calibration_version'],VERSION)
            self.assertEqual(len(report['nested_validation']['outer_folds']),4)
            self.assertAlmostEqual(report['legacy_material_holdout']['mean_rmse_T'],.0658125792273107)
            self.assertEqual(source,(manager.pilot/'calibration_bank.json').read_bytes())
            for grade,export in report['holdout_material_exports'].items():
                payload=json.loads((manager.get(report['id'])/'holdout_banks'/f'{grade}.json').read_text())
                self.assertNotIn(grade,[s['grade'] for s in payload['training_samples']])
                self.assertTrue((manager.get(report['id'])/export['path']).is_file())
            pair=json.loads((manager.pilot/'B30P105/raw_pair.json').read_text())
            result=manager.predict(report['id'],pair)
            self.assertEqual(result['RD']['calibration_version'],VERSION)
            app=Flask('transfer_test')
            app.register_blueprint(create_workbench(PROJECT,Path(tmp),ROOT))
            response=app.test_client().post('/api/workbench/calibrate',json={'grades':list(GRADES)[:3],'calibration_version':VERSION})
            self.assertEqual(response.status_code,400)
            self.assertIn('四个',response.json['error'])

if __name__=='__main__':
    unittest.main()
