"""Saved optimizer reports keep original criteria, null winners and source plans."""
import copy
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from modules.optimization_plans import OptimizationPlans
from modules.system_data import json_bytes,sha256
from modules.system_data_optimization import SavedOptimizationExports,FILES


class SavedOptimizationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='original optimization ');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.artifact='opt_aaaaaaaaaaaa';self.folder=self.root/self.artifact;self.folder.mkdir()
        self.config=dict(min_ripple_reduction_pct=30.,max_iron_loss_increase_pct=0.,min_torque_change_pct=0.,ripple_weight=.6,iron_loss_weight=.3,efficiency_weight=10.)
        self.dataset=dict(id='scan_bbbbbbbbbbbb',version='V8_2_object_CS_loss_scope_v2',measurement_protocol='v8_2_cycle1',
            rows=[dict(case='case_01',input_accepted=True,input_source='official_CSV_verified')],source_hashes={'case_01/Torque.csv':'1'*64})
        self.report=dict(id=self.artifact,created_utc='2026-11-06T00:00:00Z',dataset=self.dataset,reference='case_01',torque_reference='case_01',
            config=self.config,meets_limits_count=0,best_case=None,ranked=[dict(case='case_01',meets_limits=False,score=0.,reasons=['未达到原改善阈值'])])
        self.write_report()
        self.optimizer=SimpleNamespace(storage=self.root,motor=None,artifact=lambda artifact,name:self.root/artifact/name)
        self.plans=OptimizationPlans(self.optimizer);self.planid='plan_cccccccccccc';self.planfolder=self.plans.path(self.planid);self.planfolder.mkdir(parents=True)
        plan=dict(id=self.planid,job_id=self.dataset['id'],reference_case_id='case_01',config=self.config)
        (self.planfolder/'plan.json').write_bytes(json_bytes(plan))
        state=dict(status='completed',analysis_id=self.artifact,plan_sha256=sha256((self.planfolder/'plan.json').read_bytes()))
        (self.planfolder/'state.json').write_bytes(json_bytes(state))
        self.reader=SavedOptimizationExports(self.optimizer,self.plans)

    def write_report(self):
        (self.folder/'analysis.json').write_bytes(json_bytes(self.report))
        (self.folder/'ranking.csv').write_bytes(b'case,meets_limits,score\r\ncase_01,False,0\r\n')
        (self.folder/'analysis.md').write_bytes('原始筛选：无满足候选；权重不改。'.encode('utf8'))
        with zipfile.ZipFile(self.folder/'analysis_bundle.zip','w',zipfile.ZIP_DEFLATED) as archive:
            for name in FILES:
                if name!='analysis_bundle.zip':archive.writestr(name,(self.folder/name).read_bytes())

    def test_original_bytes_and_no_winner_keep_criteria_and_linked_frozen_plan_without_scoring(self):
        originals={name:(self.folder/name).read_bytes() for name in FILES}
        record=self.reader.catalog()[0];self.assertEqual(record['meets_limits_count'],0);self.assertIsNone(record['best_case'])
        with patch('subprocess.Popen',side_effect=AssertionError('no native dispatch')):
            index,files=self.reader.collect(dict(id=self.artifact,source_binding_sha256=record['source_binding_sha256']))
        self.assertEqual(index['config'],self.config);self.assertEqual(index['reference'],'case_01');self.assertIsNone(index['best_case'])
        self.assertEqual(index['related_plans'][0]['id'],self.planid);self.assertEqual(index['new_analyses'],0)
        self.assertFalse(index['final_physical_optimization_certified'])
        for name,blob in originals.items():self.assertEqual(files['analysis/'+name],blob);self.assertEqual((self.folder/name).read_bytes(),blob)
        self.assertEqual(files['plans/'+self.planid+'/plan.json'],(self.planfolder/'plan.json').read_bytes())

    def test_old_v81_report_stays_exact_and_gets_usage_sidecar_without_changing_ranked_values(self):
        self.report['dataset']['id']='v8_1';self.report['dataset']['version']='V8_1_original_saved'
        self.write_report();self.reader.plans=None
        old=(self.folder/'analysis.json').read_bytes();_,_,binding=self.reader.read(self.artifact)
        index,files=self.reader.collect(dict(id=self.artifact,source_binding_sha256=binding))
        self.assertEqual(files['analysis/analysis.json'],old);self.assertEqual(index['data_usage']['native_csv_accepted_count'],0)
        self.assertEqual(json.loads(files['analysis/data_usage.json'])['kind'],'historical_processed_estimate')
        self.assertIn('铁损估算',json.loads(files['analysis/data_usage.json'])['note'])

    def test_changed_complete_saved_analysis_is_not_exported_under_previous_selection(self):
        _,_,binding=self.reader.read(self.artifact);self.report['config']=dict(self.config,ripple_weight=.5);self.write_report()
        with self.assertRaisesRegex(ValueError,'文件已变化'):self.reader.collect(dict(id=self.artifact,source_binding_sha256=binding))

    def test_individually_changed_text_or_duplicate_zip_cannot_hide_under_original_bundle(self):
        (self.folder/'analysis.md').write_bytes(b'new unbound text')
        with self.assertRaisesRegex(ValueError,'ZIP与原文件不一致'):self.reader.read(self.artifact)
        self.write_report();self.report['meets_limits_count']=1;self.write_report()
        with self.assertRaisesRegex(ValueError,'行状态不符'):self.reader.read(self.artifact)

    def test_linked_plan_cannot_change_reference_or_source_dataset(self):
        _,_,binding=self.reader.read(self.artifact)
        plan=json.loads((self.planfolder/'plan.json').read_bytes());plan['reference_case_id']='other_case'
        (self.planfolder/'plan.json').write_bytes(json_bytes(plan))
        state=json.loads((self.planfolder/'state.json').read_bytes());state['plan_sha256']=sha256((self.planfolder/'plan.json').read_bytes())
        (self.planfolder/'state.json').write_bytes(json_bytes(state))
        with self.assertRaisesRegex(ValueError,'基准或原配置不符'):self.reader.collect(dict(id=self.artifact,source_binding_sha256=binding))

    def test_corrupt_unrelated_records_do_not_hide_readable_analysis_or_claim_all_plans_readable(self):
        broken=self.root/'opt_dddddddddddd';broken.mkdir();(broken/'analysis.json').write_bytes(b'broken')
        catalog=self.reader.catalog();self.assertEqual(sum(row['readable'] for row in catalog),1);self.assertEqual(len(catalog),2)
        bad=self.plans.path('plan_eeeeeeeeeeee');bad.mkdir();(bad/'state.json').write_bytes(b'broken')
        _,_,binding=self.reader.read(self.artifact);index,_=self.reader.collect(dict(id=self.artifact,source_binding_sha256=binding))
        self.assertEqual(index['plan_records_with_unreadable_association'],['plan_eeeeeeeeeeee'])
        self.assertEqual(len(index['related_plans']),1)

    def test_unsafe_analysis_ids_and_missing_binding_are_not_replaced_with_another_result(self):
        for selected in ({'id':'../private','source_binding_sha256':'a'}, {'id':self.artifact}, {'id':self.artifact,'source_binding_sha256':'changed'}):
            with self.subTest(selected=selected),self.assertRaises(ValueError):self.reader.collect(selected)


if __name__=='__main__':unittest.main()
