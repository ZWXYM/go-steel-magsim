"""Fixed constraint configurations are not new materials or native experiments."""
import copy
import csv
import io
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask
from modules.motor_constraint_studies import MotorConstraintStudies,FILES
from modules.motor_optimization import DEFAULT_CONFIG
from modules.system_data import sha256

PROJECT=Path(__file__).resolve().parents[1]


def case(name,torque,ripple,iron,eta,accepted=True):
    return dict(case=name,material='SYNTHETIC '+name,input_source='official_CSV_verified',input_accepted=accepted,
        metrics=dict(T_avg_Nm=torque,K_T_ripple_pct=ripple,P_Fe_W=iron,eta_estimate_pct=eta))


class MotorConstraintStudyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='constraint study ');self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        baseline=case('reference',2,50,10,90);candidate=case('candidate',1.995,30,9,90.1);bad=case('bad',-2,200,None,None,False)
        self.data=dict(id='v8_2_bound_archive',version='SYNTHETIC_TEST_ONLY',measurement_protocol='v8_2_cycle1',scope='synthetic test fixture',
            rows=[baseline,candidate,bad],references=[baseline,candidate],source_hashes={'synthetic.csv':'a'*64},default_reference='reference')
        self.optimizer=SimpleNamespace(dataset=lambda _:copy.deepcopy(self.data),analyze=lambda _:(_ for _ in ()).throw(AssertionError('Do not create original optimization records')))
        self.studies=MotorConstraintStudies(self.optimizer,self.root/'studies')
        self.selection=dict(dataset=self.data['id'],reference='reference',source_hashes_sha256=self.studies.inputs(self.data['id'])['source_hashes_sha256'])

    def test_fixed_grid_matches_hand_computed_tradeoff_and_preserves_default_and_rejected_cases(self):
        before=copy.deepcopy(self.data);defaults=copy.deepcopy(DEFAULT_CONFIG)
        with patch('subprocess.Popen',side_effect=AssertionError('No native dispatch')):result=self.studies.run(self.selection)
        self.assertEqual(result['configuration_count'],48);self.assertEqual(result['configurations_with_feasible_case'],36)
        self.assertEqual(result['original_default_candidate_count'],0);self.assertEqual(result['source_case_count'],3)
        first=result['configurations'][0];candidate=next(row for row in first['ranked'] if row['case']=='candidate')
        self.assertAlmostEqual(candidate['score'],28);self.assertTrue(candidate['meets_limits'])
        self.assertEqual(result['new_independent_materials'],0);self.assertEqual(result['new_native_solves'],0)
        self.assertEqual(before,self.data);self.assertEqual(defaults,DEFAULT_CONFIG)
        with zipfile.ZipFile(self.studies.download(result['id'],'study_bundle.zip')) as archive:
            import json
            manifest=json.loads(archive.read('manifest.json'));self.assertEqual(set(archive.namelist()),set(FILES)|{'manifest.json'})
            for name,digest in manifest['files'].items():self.assertEqual(sha256(archive.read(name)),digest)
            configs=list(csv.DictReader(io.StringIO(archive.read('configurations.csv').decode('utf-8-sig'))));self.assertEqual(len(configs),48)
            rows=list(csv.DictReader(io.StringIO(archive.read('case_details.csv').decode('utf-8-sig'))));self.assertEqual(len(rows),144)
            bad=[row for row in rows if row['case']=='bad'];self.assertEqual(len(bad),48)
            self.assertTrue(all(row['T_avg_Nm']=='-2' and row['score']=='' and row['meets_limits']=='False' for row in bad))

    def test_saved_record_reopens_and_downloads_without_current_dataset_or_new_scoring(self):
        result=self.studies.run(self.selection);before={p:p.read_bytes() for p in self.studies.path(result['id']).iterdir()}
        self.optimizer.dataset=lambda _:(_ for _ in ()).throw(OSError('resource currently unavailable'))
        reopened=MotorConstraintStudies(self.optimizer,self.studies.storage)
        self.assertEqual(reopened.get(result['id']),result);self.assertEqual(reopened.records()[0]['id'],result['id'])
        self.assertEqual(before,{p:p.read_bytes() for p in before})
        self.assertIn('配置组数不是实验样本数',reopened.download(result['id'],'README.md').read().decode('utf8'))

    def test_v81_control_keeps_original_baseline_torque_reference_and_estimate_scope(self):
        control=case('original_control',1.9,50,10,89);baseline=case('original_baseline',1.8,50,10,89)
        self.data.update(id='v8_1',rows=[case('candidate',1.85,20,9,90)],references=[control,baseline])
        selection=dict(dataset='v8_1',reference='original_control',source_hashes_sha256=self.studies.inputs('v8_1')['source_hashes_sha256'])
        result=self.studies.run(selection);self.assertEqual(result['torque_reference'],'original_baseline')
        self.assertEqual(result['original_default_candidate_count'],1);self.assertEqual(result['data_usage']['native_csv_accepted_count'],0)
        self.assertEqual(result['data_usage']['kind'],'historical_processed_estimate')

    def test_old_selection_and_source_change_during_calculation_cannot_create_mixed_study(self):
        with self.assertRaisesRegex(ValueError,'来源已变化'):self.studies.run(dict(self.selection,source_hashes_sha256='old'))
        changed=copy.deepcopy(self.data);changed['source_hashes']['synthetic.csv']='b'*64
        with patch.object(self.optimizer,'dataset',side_effect=[copy.deepcopy(self.data),changed]):
            with self.assertRaisesRegex(ValueError,'计算期间变化'):self.studies.run(self.selection)
        self.assertFalse(self.studies.storage.exists())

    def test_unknown_reference_extra_axes_empty_cases_and_paths_do_not_silently_fall_back(self):
        for selection in ({},dict(self.selection,reference='unknown'),dict(self.selection,axes={'arbitrary':[0]})):
            with self.subTest(selection=selection),self.assertRaises(ValueError):self.studies.run(selection)
        self.data['rows']=[]
        with self.assertRaisesRegex(ValueError,'没有可用于'):self.studies.run(self.selection)
        for artifact in ('../private','opt_aaaaaaaaaaaa','csens_aaaaaaaaaaaa/../../outside'):
            with self.assertRaises(ValueError):self.studies.get(artifact)
        self.assertFalse(self.studies.storage.exists())

    def test_tampered_saved_table_is_explicit_and_does_not_hide_other_studies(self):
        one=self.studies.run(self.selection);two=self.studies.run(self.selection)
        (self.studies.path(one['id'])/'configurations.csv').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'已改变'):self.studies.get(one['id'])
        rows=self.studies.records();self.assertEqual(sum(row['readable'] for row in rows),1)
        self.assertEqual(next(row for row in rows if row['readable'])['id'],two['id'])
        with self.assertRaisesRegex(ValueError,'未知敏感性'):self.studies.download(two['id'],'../private')

    def test_routes_create_cpu_study_and_download_with_source_bound_identity(self):
        from modules.workbench_routes import create_workbench
        app=Flask('constraint routes',template_folder=str(PROJECT/'templates'))
        with patch.dict(os.environ,{'MAGSIM_CPU_ONLY':'1','MAGSIM_AUTO_RESUME_QUEUE':'0'}),patch('modules.workbench_routes.OptimizationPlans.start_monitor'),patch('modules.workbench_routes.MotorConstraintStudies',return_value=self.studies):
            app.register_blueprint(create_workbench(PROJECT,self.root/'workbench',self.root))
        client=app.test_client();self.assertEqual(client.get('/motor-sensitivity').status_code,200)
        response=client.post('/api/workbench/optimization/sensitivity',json=self.selection)
        self.assertEqual(response.status_code,201);artifact=response.json['id']
        self.assertEqual(client.get('/api/workbench/optimization/sensitivity').json[0]['id'],artifact)
        file=client.get('/api/workbench/optimization/sensitivity/'+artifact+'/files/configurations.csv')
        self.assertEqual(file.status_code,200);self.assertTrue(file.data.startswith(b'\xef\xbb\xbf'))
        self.assertEqual(client.post('/api/workbench/optimization/sensitivity',json={}).status_code,400)


if __name__=='__main__':unittest.main()
