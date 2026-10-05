"""Preserve native evidence and numerical predictions while tracing quality."""
import copy
import json
import re
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
from flask import Flask

PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT))
ROOT=PROJECT.parent if PROJECT.name=='magsim' else PROJECT
from modules.native_quality import VERSION, audit_pilot, validate_quality, unassessed_quality, digest
from modules.calibration_workbench import CalibrationWorkbench,GRADES
from modules.calibration_transfer import VERSION as TRANSFER_VERSION
from modules.dataset_contract import dataset_contract
from modules.maxwell_exporter import export_calibrated_pair
from modules.material_calibration import file_hash
from modules.workbench_cpu_policy import install_cpu_policy
from modules.workbench_routes import create_workbench

PILOT=ROOT/'calibration/pilot_20261003_n8'


class NativeQualityWorkbench(unittest.TestCase):
    def test_real_screens_preserve_all_grains_and_unknown_torque(self):
        before={p.relative_to(PILOT).as_posix():file_hash(p) for p in PILOT.rglob('*') if p.is_file()}
        q=audit_pilot(PILOT,GRADES)
        self.assertEqual(len(q),4)
        groups=[d for m in q.values() for d in m['directions'].values()]
        self.assertEqual(sum(d['grains'] for d in groups),64)
        self.assertEqual(sum(len(d['endpoint_failed_grain_ids']) for d in groups),5)
        self.assertEqual(sum(len(d['inversion_failed_grain_ids']) for d in groups),18)
        self.assertEqual(sum(len(d['torque_unknown_grain_ids']) for d in groups),64)
        for quality in q.values():
            validate_quality(quality)
            self.assertFalse(quality['strict_training_eligible'])
            self.assertTrue(quality['no_grain_removed'])
        self.assertIn(8,q['B23R075']['directions']['RD']['inversion_failed_grain_ids'])
        self.assertEqual(before,{p.relative_to(PILOT).as_posix():file_hash(p) for p in PILOT.rglob('*') if p.is_file()})

    def test_changed_aggregate_or_quality_digest_is_rejected(self):
        from tools.analyze_sampling_convergence import read_ensemble
        values,source=read_ensemble(PILOT,'B23R075','RD')
        with patch('modules.native_quality.read_ensemble',return_value=(values+.01,source)):
            with self.assertRaisesRegex(ValueError,'聚合合同'):
                audit_pilot(PILOT,['B23R075'])
        q=unassessed_quality('foreign')
        q['status']='numerical_screen_passed'
        with self.assertRaisesRegex(ValueError,'哈希'):
            validate_quality(q)

    def test_exact_known_source_rebound_and_uploaded_pass_label_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager=CalibrationWorkbench(ROOT,Path(tmp))
            pair=json.loads((PILOT/'B30P105/raw_pair.json').read_text())
            claimed=unassessed_quality()
            claimed['status']='numerical_screen_passed'
            rebound=manager.compatible_pair({**pair,'native_quality':claimed})
            self.assertTrue(rebound['native_quality']['verified_native_source'])
            self.assertFalse(rebound['native_quality']['strict_training_eligible'])
            np.testing.assert_array_equal(rebound['RD']['B'],pair['RD']['B'])
            changed=copy.deepcopy(rebound)
            changed['RD']['B'][13]+=.01
            changed['native_quality']=claimed
            q=manager.compatible_pair(changed)['native_quality']
            self.assertEqual(q['status'],'unassessed')
            self.assertFalse(q['verified_native_source'])

    def test_new_nested_artifact_freezes_quality_and_keeps_folds_and_curves(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager=CalibrationWorkbench(ROOT,Path(tmp))
            report=manager.calibrate(list(GRADES),TRANSFER_VERSION)
            directory=manager.get(report['id'])
            previous=json.loads((ROOT/'calibration/generalization_20261005'/
                ('final/' if PROJECT.name=='magsim' else '')/'cal_65e506f6e207/report.json').read_text())
            self.assertEqual(report['bank_sha256'],previous['bank_sha256'])
            self.assertAlmostEqual(report['material_holdout']['mean_rmse_T'],.04404264729019529)
            self.assertFalse(report['strict_training_eligible'])
            self.assertEqual(report['native_quality_sha256'],digest(report['native_quality']))
            for new,old in zip(report['comparisons'],previous['comparisons']):
                np.testing.assert_allclose(new['holdout_B'],old['holdout_B'],rtol=0,atol=1e-12)
            with zipfile.ZipFile(directory/report['bundle_file']) as z:
                self.assertIn('native_quality.json',z.namelist())
                self.assertEqual(json.loads(z.read('native_quality.json')),report['native_quality'])
            for grade,item in report['holdout_material_exports'].items():
                metadata=json.loads((directory/item['path']).with_suffix('.metadata.json').read_text())
                self.assertEqual(metadata['native_quality'],report['native_quality'][grade])
                self.assertEqual(metadata['calibration_native_quality_sha256'],digest({g:q for g,q in report['native_quality'].items() if g!=grade}))
                self.assertFalse(metadata['strict_training_eligible'])

    def test_foreign_prediction_cannot_promote_quality_claims_or_training_role(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager=CalibrationWorkbench(ROOT,Path(tmp))
            report=manager.calibrate(list(GRADES)[:2])
            pair=manager.compatible_pair(json.loads((PILOT/'B30P105/raw_pair.json').read_text()))
            pair['material_id']='foreign_material'
            pair['native_quality']['status']='numerical_screen_passed'
            result=manager.predict(report['id'],pair)
            self.assertEqual(result['native_quality']['status'],'unassessed')
            self.assertFalse(result['strict_training_eligible'])
            out=manager.get(report['id'])/result['id']
            metadata=json.loads((out/result['AMAT_file']).with_suffix('.metadata.json').read_text())
            self.assertEqual(metadata['native_quality'],result['native_quality'])
            self.assertEqual(metadata['native_quality_status'],'unassessed')

    def test_export_quality_mismatch_rejected_and_native_points_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager=CalibrationWorkbench(ROOT,Path(tmp))
            report=manager.calibrate(list(GRADES)[:2])
            result=manager.predict(report['id'],json.loads((PILOT/'B30P105/raw_pair.json').read_text()))
            rd,td=result['RD'],result['TD']
            first=Path(export_calibrated_pair(rd,td,'Audited',thickness_mm=.3,export_dir=tmp))
            bare=[{k:v for k,v in c.items() if k not in ('native_quality','calibration_native_quality_sha256')} for c in (rd,td)]
            second=Path(export_calibrated_pair(*bare,'Unassessed',thickness_mm=.3,export_dir=tmp))
            self.assertEqual(re.findall(r'Points\[[^]]+\]',first.read_text()),re.findall(r'Points\[[^]]+\]',second.read_text()))
            with self.assertRaisesRegex(ValueError,'different native quality'):
                export_calibrated_pair(rd,bare[1],'Mismatch',thickness_mm=.3,export_dir=tmp)

    def test_formal_dataset_rejects_missing_failed_unknown_and_diagnostic_quality(self):
        base=dict(simulation_physics_version='cubic_sample_frame_v2',reference_correction_version=TRANSFER_VERSION,
            H_axis='physical_A_per_m',calibration_sha256='fixture',texture_sampling_version='legacy_multi_peak_importance_v1')
        with self.assertRaisesRegex(ValueError,'原生质量合同'):
            dataset_contract(pd.DataFrame([base]))
        screened={**base,'native_quality_contract_version':VERSION,'native_quality_status':'numerical_screen_passed',
            'native_quality_sha256':'1'*64,'strict_training_eligible':True}
        self.assertEqual(dataset_contract(pd.DataFrame([screened]))['native_quality_audit_sha256s'],['1'*64])
        for status in ('numerical_screen_failed','convergence_unassessed','unassessed'):
            with self.assertRaisesRegex(ValueError,'未知收敛'):
                dataset_contract(pd.DataFrame([{**screened,'native_quality_status':status}]))
        with self.assertRaisesRegex(ValueError,'诊断'):
            dataset_contract(pd.DataFrame([{**screened,'strict_training_eligible':False}]))
        with self.assertRaisesRegex(ValueError,'哈希'):
            dataset_contract(pd.DataFrame([{**screened,'native_quality_sha256':'unspecified'}]))

    def test_cpu_only_blocks_native_training_resume_and_non_post_mutations(self):
        app=Flask('cpu_policy')
        install_cpu_policy(app)
        client=app.test_client()
        for route in ('/api/run','/api/train','/api/workbench/motor/queue/recover',
                      '/api/workbench/motor/jobs/scan_0123456789ab/submit',
                      '/api/workbench/motor/jobs/scan_0123456789ab/continue'):
            self.assertEqual(client.post(route).status_code,423)
        self.assertEqual(client.put('/api/workbench/calibrate').status_code,423)
        for route in ('/api/workbench/calibrate','/api/workbench/motor/jobs',
                      '/api/workbench/calibrations/cal_0123456789ab/predict',
                      '/api/workbench/motor/jobs/scan_0123456789ab/export'):
            self.assertEqual(client.post(route).status_code,404) # Filter permits route resolution.
        self.assertEqual(client.get('/api/run').status_code,404)

    def test_cpu_only_startup_suppresses_queue_monitor(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch('modules.workbench_routes.MotorWorkbench.jobs',return_value=[{'status':'queued'}]),patch('modules.workbench_routes.MotorWorkbench.start_queue_monitor') as monitor:
                with patch.dict('os.environ',{'MAGSIM_CPU_ONLY':'1'}):
                    blueprint=create_workbench(PROJECT,Path(tmp),ROOT)
                    app=Flask('cpu_registered')
                    app.register_blueprint(blueprint)
                    client=app.test_client()
                    self.assertTrue(client.get('/api/workbench/resources').json['cpu_only'])
                    self.assertEqual(client.post('/api/train').status_code,423)
                    monitor.assert_not_called()
                with patch.dict('os.environ',{'MAGSIM_CPU_ONLY':'0'}):
                    create_workbench(PROJECT,Path(tmp),ROOT)
                    monitor.assert_called_once()

    def test_cpu_evidence_cli_preserves_model_sources_and_rejects_old_output(self):
        from tools.audit_workbench_native_quality import run
        with tempfile.TemporaryDirectory() as tmp:
            output=Path(tmp)/'new_quality_evidence'
            with patch('subprocess.run',side_effect=AssertionError('CPU audit cannot launch native jobs')):
                report=run(ROOT,output)
            self.assertEqual(report['grains'],64)
            self.assertEqual(report['new_MuMax_solves'],0)
            self.assertFalse(report['model_changed'])
            self.assertTrue(json.loads((output/'source_integrity.json').read_text())['unchanged'])
            self.assertTrue(all(c['material_holdout_max_abs_delta_T']<=1e-12 for c in report['curve_identity']))
            with self.assertRaisesRegex(ValueError,'new evidence directory'):
                run(ROOT,output)


if __name__=='__main__':
    unittest.main()
