"""Study choices preserve original scores, default failures and one saved application."""
import copy,io,json,tempfile,unittest,zipfile
from pathlib import Path
from unittest.mock import patch
from flask import Flask,jsonify
import test_motor_constraint_studies as fixtures
from modules.motor_constraint_studies import MotorConstraintStudies
from modules.motor_optimization import MotorOptimization,DEFAULT_CONFIG
from modules.motor_queue import QueueLease
from modules.motor_study_applications import MotorStudyApplications
from modules.system_data import json_bytes,sha256


class MotorStudyApplicationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='study application ');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        ref=fixtures.case('reference',2,50,10,90);candidate=fixtures.case('candidate',1.995,30,9,90.1)
        self.data=dict(id='v8_2_bound_archive',version='SYNTHETIC_TEST_ONLY',measurement_protocol='v8_2_cycle1',
            scope='synthetic software fixture',rows=[ref,candidate,fixtures.case('bad',-2,200,None,None,False)],
            references=[ref,candidate],source_hashes={'synthetic.csv':'a'*64},default_reference='reference')
        self.optimizer=MotorOptimization(self.root,self.root/'optimization',None)
        self.optimizer.dataset=lambda _:copy.deepcopy(self.data)
        self.studies=MotorConstraintStudies(self.optimizer,self.root/'studies')
        self.study=self.studies.run(dict(dataset=self.data['id'],reference='reference',source_hashes_sha256=sha256(json_bytes(self.data['source_hashes']))))
        self.actions=MotorStudyApplications(self.studies,self.root/'applications')
        self.preview=self.actions.preview(self.study['id'])
        self.selection=dict(configuration_id='c30',source_binding_sha256=self.preview['source_binding_sha256'])
        self.original={p.name:p.read_bytes() for p in self.studies.path(self.study['id']).iterdir()}

    def apply(self,selection=None):return self.actions.apply(self.study['id'],selection or self.selection)

    def test_selected_configuration_creates_original_analysis_and_complete_reopenable_bundle(self):
        defaults=copy.deepcopy(DEFAULT_CONFIG)
        with patch('subprocess.Popen',side_effect=AssertionError('No native work')):record=self.apply()
        chosen=next(row for row in self.study['configurations'] if row['id']=='c30')
        report=json.loads(self.optimizer.artifact(record['analysis_id'],'analysis.json').read_bytes())
        self.assertEqual(report['ranked'],chosen['ranked']);self.assertEqual(record['config'],chosen['config'])
        self.assertEqual(record['meets_limits_count'],1);self.assertEqual(record['best_case'],'candidate')
        self.assertFalse(record['automatic_native_submission']);self.assertEqual(record['new_native_solves'],0)
        self.assertEqual(DEFAULT_CONFIG,defaults);self.assertEqual(self.original,{p.name:p.read_bytes() for p in self.studies.path(self.study['id']).iterdir()})
        with zipfile.ZipFile(self.actions.download(record['id'],'application_bundle.zip')) as archive:
            self.assertEqual(len(archive.namelist()),8)
            for name,digest in record['files'].items():self.assertEqual(sha256(archive.read(name)),digest)
            with zipfile.ZipFile(io.BytesIO(archive.read('study_bundle.zip'))) as study:
                self.assertEqual(self.original,{name:study.read(name) for name in study.namelist()})
        self.assertEqual(self.actions.get(record['id'])['analysis_id'],report['id'])

    def test_default_configuration_still_saves_zero_candidates(self):
        result=self.apply(dict(self.selection,configuration_id='c33'))
        self.assertTrue(result['is_original_default']);self.assertEqual(result['meets_limits_count'],0)
        self.assertIsNone(result['best_case']);self.assertEqual(result['original_default_candidate_count'],0)

    def test_repeated_request_reuses_saved_record_without_rescoring_or_current_dataset(self):
        with patch.object(self.optimizer,'analyze',wraps=self.optimizer.analyze) as analyze:
            one=self.apply();self.optimizer.dataset=lambda _:(_ for _ in ()).throw(OSError('offline'))
            two=self.apply();self.assertEqual(analyze.call_count,1)
        self.assertEqual(one['id'],two['id']);self.assertFalse(two['created'])
        self.assertEqual(len(list(self.optimizer.storage.glob('opt_*'))),1)
        self.assertFalse(self.actions.preview(self.study['id'])['source_matches'])
        self.assertEqual(self.actions.get(one['id'])['analysis_id'],one['analysis_id'])

    def test_source_metrics_changed_even_with_same_hash_dictionary_refuses_new_analysis(self):
        self.data['rows'][1]['metrics']['T_avg_Nm']=2.2
        self.assertFalse(self.actions.preview(self.study['id'])['source_matches'])
        with self.assertRaisesRegex(ValueError,'来源与当前数据不一致'):self.apply()
        self.assertEqual(list(self.optimizer.storage.glob('opt_*')),[])

    def test_bad_selection_and_stale_binding_do_not_create_analysis(self):
        for selection in ({},dict(self.selection,configuration_id='c99'),dict(self.selection,source_binding_sha256='old'),dict(self.selection,config={})):
            with self.subTest(selection=selection),self.assertRaises(ValueError):self.actions.apply(self.study['id'],selection)
        with self.assertRaises(ValueError):self.actions.get('../outside')
        self.assertEqual(list(self.optimizer.storage.glob('opt_*')),[])

    def test_wrong_scoring_result_is_retained_as_failed_without_automatic_retry(self):
        original=self.optimizer.analyze
        def mismatch(data):
            report=original(data);report['ranked'][0]['score']=999;return report
        with patch.object(self.optimizer,'analyze',side_effect=mismatch) as analyze:
            with self.assertRaisesRegex(ValueError,'不一致'):self.apply()
            with self.assertRaisesRegex(ValueError,'未完成'):self.apply()
            self.assertEqual(analyze.call_count,1)
        state=json.loads(next(self.actions.storage.glob('sapply_*/state.json')).read_bytes())
        self.assertEqual(state['status'],'needs_attention');self.assertFalse(state['automatic_retry'])
        self.assertTrue(self.optimizer.artifact(state['analysis_id'],'analysis.json').exists())

    def test_process_lock_blocks_duplicate_work_and_releases_on_exit(self):
        selected=dict(study_id=self.study['id'],**self.selection)
        identity='sapply_'+sha256(json_bytes(selected))[:16]
        lease=QueueLease(self.actions.storage/(identity+'.guard'));self.assertTrue(lease.acquire())
        try:
            with self.assertRaisesRegex(ValueError,'正在保存'):self.apply()
            self.assertEqual(list(self.optimizer.storage.glob('opt_*')),[])
        finally:lease.release()
        self.assertEqual(self.apply()['meets_limits_count'],1)

    def test_saved_application_tampering_is_detected_and_never_rescored(self):
        result=self.apply();metadata=self.actions.path(result['id'])/'application.json';before=metadata.read_bytes()
        changed=json.loads(before);changed['best_case']='invented winner';metadata.write_bytes(json_bytes(changed))
        with self.assertRaisesRegex(ValueError,'摘要与原研究'):self.actions.get(result['id'])
        metadata.write_bytes(before)
        (self.actions.path(result['id'])/'ranking.csv').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'已改变'):self.actions.get(result['id'])
        with patch.object(self.optimizer,'analyze',side_effect=AssertionError('Do not repeat')):
            with self.assertRaisesRegex(ValueError,'已改变'):self.apply()

    def test_real_routes_create_once_reopen_download_and_keep_missing_source_explicit(self):
        from modules.motor_study_applications import create_study_application_routes
        app=Flask('study actions');app.register_blueprint(create_study_application_routes(self.actions))
        @app.errorhandler(ValueError)
        def error(exc):return jsonify(error=str(exc)),400
        client=app.test_client();base='/api/workbench/optimization/sensitivity/'+self.study['id']+'/applications'
        self.assertTrue(client.get(base).json['source_matches'])
        first=client.post(base,json=self.selection);self.assertEqual(first.status_code,201)
        self.assertEqual(client.post(base,json=self.selection).status_code,200)
        saved='/api/workbench/optimization/study-applications/'+first.json['id']
        self.assertEqual(client.get(saved).json['configuration_id'],'c30')
        with client.get(saved+'/files/application_bundle.zip') as response:
            self.assertEqual(response.status_code,200);self.assertTrue(zipfile.is_zipfile(io.BytesIO(response.data)))
        self.assertEqual(client.post(base,json={}).status_code,400)


if __name__=='__main__':unittest.main()
