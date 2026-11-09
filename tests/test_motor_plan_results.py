"""Complete-plan exports bind raw CSV, original analysis and unchanged criteria."""
import io,json,os,unittest,zipfile
from pathlib import Path
from unittest.mock import patch
from flask import Flask
import test_optimization_plans as fixtures
from test_workbench import csv_fixture
from modules.motor_workbench import collect_metrics,write_json
from modules.motor_queue import QueueLease
from modules.motor_plan_results import MotorPlanResults
from modules.system_data import json_bytes,sha256,verify_package
from modules.workbench_routes import create_workbench


class MotorPlanResultTests(unittest.TestCase):
    def setUp(self):
        fixtures.OptimizationPlanTests.setUp(self)
        csv_fixture(self.folder/'case_02')
        write_json(self.folder/'case_02/result.json',collect_metrics(self.folder/'case_02','v8_2_cycle1'))
        manifest=json.loads((self.folder/'manifest.json').read_bytes());manifest['cases'].append(dict(id='case_02',material='SYNTHETIC second'))
        write_json(self.folder/'manifest.json',manifest)
        state=json.loads((self.folder/'state.json').read_bytes());state['cases'].append(dict(id='case_02',material='SYNTHETIC second',status='completed'))
        write_json(self.folder/'state.json',state)
        self.plan=self.plans.prepare(dict(job_id=self.job,reference_material='grade',config=dict(min_ripple_reduction_pct=30)))
        self.manager=MotorPlanResults(self.plans,self.storage)
        self.preview=self.manager.preview(self.plan['id']);self.assertTrue(self.preview['available'])
        self.request=dict(source_binding_sha256=self.preview['source_binding_sha256'])
        self.before={p:p.read_bytes() for parent in (self.motor.storage,self.optimization.storage) for p in parent.rglob('*') if p.is_file()}

    def prepare(self):return self.manager.prepare(self.plan['id'],self.request)

    def test_single_plan_collects_original_csv_all_cases_reference_and_zero_winner(self):
        with patch.object(self.optimization,'analyze',side_effect=AssertionError('No new score')),patch.object(self.motor,'submit',side_effect=AssertionError('No solve')):
            value=self.prepare()
        self.assertTrue(value['created']);self.assertIsNone(value['summary']['best_case']);self.assertEqual(value['summary']['meets_limits_count'],0)
        self.assertEqual(value['summary']['materials'],['grade','SYNTHETIC second']);self.assertEqual(value['summary']['row_count'],2)
        self.assertEqual(value['summary']['config'],self.plan['config']);self.assertEqual(value['summary']['reference_case'],'case_01')
        package,blob=self.manager.packages.read(value['package_id']);self.assertEqual(verify_package(blob)['member_count'],11)
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            self.assertEqual(z.read('plan/plan.json'),(self.plans.path(self.plan['id'])/'plan.json').read_bytes())
            for n in ('analysis.json','analysis.md','ranking.csv','analysis_bundle.zip'):
                self.assertEqual(z.read('analysis/'+n),self.optimization.artifact(self.plan['analysis_id'],n).read_bytes())
            with zipfile.ZipFile(io.BytesIO(z.read('motor_dataset.zip'))) as native:
                self.assertEqual(json.loads(native.read('dataset.json'))['source_hashes'],self.optimization.dataset(self.job)['source_hashes'])
                raw=[n for n in native.namelist() if n.startswith('sources/') and n.endswith('.csv')]
                self.assertEqual(len(raw),6)
                for n in raw:self.assertEqual(native.read(n),(self.folder/n.removeprefix('sources/')).read_bytes())
        for p,b in self.before.items():self.assertEqual(p.read_bytes(),b,p)

    def test_restart_reopens_identical_registered_pack_without_recollecting_or_scoring(self):
        first=self.prepare();restarted=MotorPlanResults(self.plans,self.storage)
        with patch('modules.motor_plan_results.dataset_bundle',side_effect=AssertionError('No duplicate collection')):
            second=restarted.prepare(self.plan['id'],self.request)
        self.assertFalse(second['created']);self.assertEqual(first['package_id'],second['package_id'])
        self.assertEqual(len(self.manager.packages.records()),1);self.assertEqual(len(list(self.optimization.storage.glob('opt_*'))),1)

    def test_unfinished_or_failed_plan_has_no_result_export_and_no_dispatch(self):
        path=self.plans.path(self.plan['id'])/'state.json';state=json.loads(path.read_bytes())
        for status in ('prepared','watching','needs_attention'):
            write_json(path,dict(state,status=status));self.assertFalse(self.manager.preview(self.plan['id'])['available'])
            with self.assertRaisesRegex(ValueError,'自动筛选完成'):self.prepare()
        self.assertFalse(self.manager.storage.exists());self.assertFalse(self.manager.packages.storage.exists())

    def test_changed_raw_csv_cannot_be_exported_with_old_analysis(self):
        csv=next((self.folder/'case_02/reports').glob('*.csv'));csv.write_bytes(csv.read_bytes()+b'\n')
        self.assertFalse(self.manager.preview(self.plan['id'])['available'])
        with self.assertRaisesRegex(ValueError,'CSV'):self.prepare()
        self.assertFalse(self.manager.packages.records())

    def test_changed_valid_plan_config_or_reference_cannot_mix_original_analysis(self):
        p=self.plans.path(self.plan['id'])/'plan.json';s=p.with_name('state.json');original=p.read_bytes();state=json.loads(s.read_bytes())
        for update in ({'reference_case_id':'case_02','reference_material':'SYNTHETIC second'}, {'config':dict(self.plan['config'],ripple_weight=.9)}):
            data=json.loads(original);data.update(update);write_json(p,data);write_json(s,dict(state,plan_sha256=sha256(p.read_bytes())))
            with self.assertRaisesRegex(ValueError,'同任务基准'):self.prepare()
        self.assertFalse(self.manager.packages.records())

    def test_corrupt_analysis_bundle_or_package_is_explicit_not_rebuilt(self):
        first=self.prepare();folder=self.manager.packages.folder(first['package_id']);(folder/'package.zip').write_bytes((folder/'package.zip').read_bytes()+b'changed')
        with self.assertRaisesRegex(ValueError,'字节已改变'):self.manager.get(first['id'])
        with self.assertRaisesRegex(ValueError,'字节已改变'):self.prepare()
        original=self.optimization.artifact(self.plan['analysis_id'],'analysis.md');original.write_bytes(b'not matching original bundle')
        self.assertFalse(self.manager.preview(self.plan['id'])['available'])

    def test_stale_selection_lock_and_invalid_input_do_not_create_duplicate_pack(self):
        for request in ({},{'source_binding_sha256':'wrong'},{'source_binding_sha256':self.request['source_binding_sha256'],'new_config':{}}):
            with self.subTest(request=request),self.assertRaises(ValueError):self.manager.prepare(self.plan['id'],request)
        artifact='presult_'+self.request['source_binding_sha256'][:16]
        guard=QueueLease(self.manager.storage/(artifact+'.guard'));self.assertTrue(guard.acquire())
        try:
            with self.assertRaisesRegex(ValueError,'正在整理'):self.prepare()
        finally:guard.release()
        self.assertFalse(self.manager.packages.records());self.assertTrue(self.prepare()['created'])

    def test_collection_failure_is_saved_and_not_automatically_retried(self):
        with patch('modules.motor_plan_results.dataset_bundle',side_effect=ValueError('SYNTHETIC read failure')):
            with self.assertRaisesRegex(ValueError,'SYNTHETIC'):self.prepare()
        with self.assertRaisesRegex(ValueError,'此前整理未完成'):self.prepare()
        self.assertEqual(json.loads(next(self.manager.storage.glob('presult_*/state.json')).read_bytes())['status'],'needs_attention')
        self.assertFalse(self.manager.packages.records())

    def test_source_change_during_collection_prevents_registration(self):
        from modules.motor_plan_results import dataset_bundle
        def changing(*args):
            value=dataset_bundle(*args);path=self.optimization.artifact(self.plan['analysis_id'],'analysis.md');path.write_bytes(path.read_bytes()+b'changed');return value
        with patch('modules.motor_plan_results.dataset_bundle',side_effect=changing):
            with self.assertRaisesRegex(ValueError,'ZIP与原文件'):self.prepare()
        self.assertFalse(self.manager.packages.records())

    def test_cpu_routes_collect_once_download_and_reopen_when_source_offline(self):
        with patch.dict(os.environ,{'MAGSIM_CPU_ONLY':'1','MAGSIM_AUTO_RESUME_QUEUE':'0'}):
            app=Flask('plan result routes');app.register_blueprint(create_workbench(self.project,self.storage,self.root))
        client=app.test_client();url='/api/workbench/optimization/plans/'+self.plan['id']+'/result-package'
        preview=client.get(url);self.assertTrue(preview.json['available'])
        first=client.post(url,json=dict(source_binding_sha256=preview.json['source_binding_sha256']));self.assertEqual(first.status_code,201)
        self.assertEqual(client.post(url,json=self.request).status_code,200)
        self.assertEqual(client.post(url,json={}).status_code,400)
        value=first.json;response=client.get(value['package_url']);self.assertEqual(response.status_code,200);verify_package(response.data);response.close()
        with patch.object(MotorPlanResults,'_source',side_effect=OSError('offline')):
            self.assertEqual(client.get('/api/workbench/optimization/plan-results/'+value['id']).json['package_sha256'],value['package_sha256'])
        self.assertEqual(len(list(self.optimization.storage.glob('opt_*'))),1)


if __name__=='__main__':unittest.main()
