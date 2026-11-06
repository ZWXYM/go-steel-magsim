"""Optional model versions preserve whole-material isolation and old bank identity."""
import copy
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
import numpy as np
from flask import Flask

from modules.calibration_transfer import TransferBank,CANDIDATES,protocol,digest,load_bank,nested_validation
from modules.calibration_regularized import RegularizedTransferBank,VERSION,REGULARIZED_CANDIDATES,default_eligible
from modules.calibration_prediction import predict_pair,resolve_prediction_bank
from modules.calibration_workbench import CalibrationWorkbench,GRADES
from modules.material_calibration import PHYSICS_VERSION
from modules.material_library import MaterialLibrary
from modules.maxwell_exporter import export_calibrated_pair
from modules.system_data import sha256
from modules.system_data_builder import SystemDataBuilder
from modules.workbench_routes import create_workbench

PROJECT=Path(__file__).resolve().parents[1]
ROOT=PROJECT.parent if (PROJECT.parent/'calibration/pilot_20261003_n8/manifest.json').is_file() else PROJECT


def samples():
    result=[]
    for i,fraction in enumerate((.65,.75,.85,.95)):
        raw=np.array([0.,.3,.7,.8])+.01*i*np.array([0.,1.,1.,1.])
        reference=raw+.1*np.array([0.,1.,2.,2.])+.1*(fraction-.65)*np.array([0.,.5,1.,1.])
        result.append(dict(grade=f'S{i}',params=dict(f_Goss=fraction,theta_0_deg=5.,halfwidth_deg=8.,Si_content=3.),
            curves={d:dict(H=[0.,100.,800.,1000.],raw_B=raw.tolist(),reference_B=reference.tolist()) for d in ('RD','TD')}))
    return result


class RegularizedTransferTests(unittest.TestCase):
    def test_optional_pool_does_not_mutate_default_protocol_or_candidate_order(self):
        self.assertEqual(len(CANDIDATES),8);self.assertEqual(len(REGULARIZED_CANDIDATES),11)
        self.assertEqual(protocol()['calibration_version'],'fixed_h_nested_transfer_v2')
        self.assertEqual(list(REGULARIZED_CANDIDATES[:8]),list(CANDIDATES))
        self.assertFalse(RegularizedTransferBank.protocol_definition()['default_eligible'])
        self.assertFalse(RegularizedTransferBank.protocol_definition()['automatic_deployment'])
        self.assertFalse(default_eligible(dict(calibration_version=VERSION,default_eligible=True)))
        self.assertTrue(default_eligible(dict(calibration_version='fixed_h_nested_transfer_v2')))

    def test_saved_version_reloads_and_cannot_impersonate_an_old_protocol(self):
        bank=RegularizedTransferBank.fit(samples())
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'bank.json';path.write_text(json.dumps(bank.payload),encoding='utf8')
            self.assertIsInstance(load_bank(path),RegularizedTransferBank);self.assertEqual(load_bank(path).bank_sha256,bank.bank_sha256)
        with self.assertRaises(ValueError):TransferBank(bank.payload)
        bad=copy.deepcopy(bank.payload);bad['protocol_sha256']=digest(protocol())
        with self.assertRaises(ValueError):RegularizedTransferBank(bad)

    def test_outer_reference_cannot_change_its_candidate_bank_or_heldout_curves(self):
        before=samples();first=nested_validation(before,bank_type=RegularizedTransferBank)
        changed=copy.deepcopy(before)
        for direction in ('RD','TD'):changed[0]['curves'][direction]['reference_B']=[0.,.8,1.7,1.8]
        second=nested_validation(changed,bank_type=RegularizedTransferBank)
        self.assertEqual(first['holdout_banks']['S0'],second['holdout_banks']['S0'])
        for direction in ('RD','TD'):
            a=next(c for c in first['comparisons'] if c['grade']=='S0' and c['direction']==direction)
            b=next(c for c in second['comparisons'] if c['grade']=='S0' and c['direction']==direction)
            self.assertEqual(a['holdout_B'],b['holdout_B']);self.assertNotEqual(a['reference_B'],b['reference_B'])

    def test_exclusion_reselects_the_optional_class_and_never_uses_parent_selection(self):
        bank=RegularizedTransferBank.fit(samples());effective,scope=resolve_prediction_bank(bank,['S0'])
        self.assertIsInstance(effective,RegularizedTransferBank);self.assertNotEqual(effective.bank_sha256,bank.bank_sha256)
        self.assertEqual(scope['effective_training_grades'],['S1','S2','S3']);self.assertTrue(scope['selection_rerun_after_exclusion'])
        self.assertEqual(effective.payload['selection']['training_grades'],['S1','S2','S3'])
        changed=samples()
        for direction in ('RD','TD'):changed[0]['curves'][direction]['reference_B']=[0.,.5,1.5,1.6]
        second,_=resolve_prediction_bank(RegularizedTransferBank.fit(changed),['S0'])
        self.assertEqual(effective.payload,second.payload)

    def test_known_material_fit_and_physical_H_stay_exact_across_model_versions(self):
        data=samples();old=TransferBank.fit(data);new=RegularizedTransferBank.fit(data)
        for sample in data:
            for direction in ('RD','TD'):
                curve=sample['curves'][direction]
                a=old.correct(curve['H'],curve['raw_B'],sample['params'],direction=direction)
                b=new.correct(curve['H'],curve['raw_B'],sample['params'],direction=direction)
                self.assertEqual(a['H'],b['H']);self.assertEqual(a['B'],b['B']);self.assertEqual(b['effective_native_beta'],1.)

    def test_pair_export_preserves_actual_version_effective_bank_and_zero_loss_status(self):
        data=samples();bank=RegularizedTransferBank.fit(data);sample=data[0]
        pair=dict(material_id='synthetic target',simulation_physics_version=PHYSICS_VERSION,params=sample['params'],
            RD=dict(H=sample['curves']['RD']['H'],B=sample['curves']['RD']['raw_B']),
            TD=dict(H=sample['curves']['TD']['H'],B=sample['curves']['TD']['raw_B']))
        prediction=predict_pair(pair,bank,['S0'])
        with tempfile.TemporaryDirectory() as folder:
            path=Path(export_calibrated_pair(prediction['RD'],prediction['TD'],'SYNTHETIC_VARIANT',thickness_mm=.3,export_dir=folder))
            metadata=json.loads(path.with_suffix('.metadata.json').read_text())
            self.assertEqual(metadata['reference_correction_version'],VERSION);self.assertFalse(metadata['default_eligible'])
            self.assertEqual(metadata['prediction_scope']['effective_training_grades'],['S1','S2','S3'])
            self.assertEqual(metadata['core_loss_status'],'uncalibrated_zero_placeholders');self.assertEqual(metadata['H_axis'],'physical_A_per_m')
            self.assertEqual(metadata['calibration_sha256'],prediction['calibration_sha256'])


class RegularizedWorkbenchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='regularized workbench ');cls.folder=Path(cls.temp.name)
        cls.storage=cls.folder/'storage';cls.manager=CalibrationWorkbench(ROOT,cls.storage/'calibration')
        cls.base=cls.manager.calibrate(list(GRADES),'fixed_h_nested_transfer_v2')
        with patch('subprocess.Popen',side_effect=AssertionError('No native work for a calibration model')):
            cls.trial=cls.manager.calibrate(list(GRADES),VERSION)
        pair=json.loads((cls.manager.pilot/'B30P105/raw_pair.json').read_text())
        cls.prediction=cls.manager.predict(cls.trial['id'],pair,['B30P105'])

    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()

    def test_real_four_material_candidate_matches_frozen_retrospective_trial_and_retains_failed_goal(self):
        self.assertEqual(self.base['bank_sha256'],'3cffed42ece56052a0518983b2d628dad376cbd266adfd224cc7b8b284caae33')
        self.assertAlmostEqual(self.trial['material_holdout']['mean_rmse_T'],.030801268167274947,places=12)
        self.assertAlmostEqual(self.trial['original_nested_material_holdout']['mean_rmse_T'],.04404264729019529,places=12)
        self.assertAlmostEqual(self.trial['material_holdout']['max_abs_B800_error_T'],.0603564043850644,places=12)
        self.assertFalse(self.trial['development_acceptance_target_met']);self.assertFalse(self.trial['default_eligible'])
        self.assertFalse(self.trial['prospective_external_validation']);self.assertEqual(len(self.trial['metrics']),48)
        self.assertEqual(set(self.trial['holdout_material_exports']),set(GRADES))

    def test_real_excluded_prediction_matches_its_outer_fold_and_exports_the_child_version(self):
        result=self.prediction;curve=next(c for c in self.trial['comparisons'] if c['grade']=='B30P105' and c['direction']=='RD')
        self.assertEqual(result['prediction_scope']['excluded_grades'],['B30P105'])
        self.assertNotIn('B30P105',result['prediction_scope']['effective_training_grades'])
        np.testing.assert_allclose(result['RD']['B'],curve['holdout_B'],atol=1e-12,rtol=0)
        path=self.manager.get(self.trial['id'])/result['id']/'effective_bank.json'
        self.assertIsInstance(load_bank(path),RegularizedTransferBank)
        self.assertEqual(result['RD']['calibration_version'],VERSION)

    def test_library_and_calibration_catalog_keep_ordinary_records_before_optional_ones(self):
        library=MaterialLibrary(self.manager);catalog=library.catalog()['records']
        self.assertEqual(catalog[0]['id'],self.base['id']);self.assertFalse(catalog[-1]['default_eligible'])
        detail=library.detail(self.trial['id'],'holdout:B30P105')
        self.assertNotIn('B30P105',detail['training_grades'])
        app=Flask(__name__);app.register_blueprint(create_workbench(PROJECT,self.storage,ROOT))
        records=app.test_client().get('/api/workbench/calibrations').get_json()
        self.assertEqual(records[0]['id'],self.base['id']);self.assertEqual(records[-1]['id'],self.trial['id'])

    def test_selected_candidate_workpack_keeps_the_original_protocol_comparison_column(self):
        builder=SystemDataBuilder(self.storage,self.manager,None,MaterialLibrary(self.manager),None)
        report_path=self.manager.get(self.trial['id'])/'report.json'
        selected=dict(calibration=dict(id=self.trial['id'],report_sha256=sha256(report_path.read_bytes())),datasets=[],batch=None)
        record=builder.build(selected);_,blob=builder.store.read(record['id'])
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            data=json.loads(archive.read('data_index.json'));self.assertEqual(data['calibration']['metrics_rows'],48)
            self.assertEqual(data['calibration']['calibration_version'],VERSION);self.assertFalse(data['calibration']['default_eligible'])
            self.assertIn('original_protocol_holdout_B_T',archive.read('calibration/curves.csv').decode('utf-8-sig').splitlines()[0])
            self.assertEqual(archive.read('calibration/report.json'),report_path.read_bytes())

    def test_optional_protocol_rejects_fewer_than_four_materials_without_creating_a_record(self):
        before=set(self.manager.storage.iterdir())
        with self.assertRaisesRegex(ValueError,'至少需要四个'):self.manager.calibrate(list(GRADES)[:3],VERSION)
        self.assertEqual(before,set(self.manager.storage.iterdir()))


if __name__=='__main__':unittest.main()
