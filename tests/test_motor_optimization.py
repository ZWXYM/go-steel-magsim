import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from flask import Flask

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from modules.motor_optimization import DEFAULT_CONFIG, MotorOptimization, normalized, screen_rows
from modules.workbench_routes import create_workbench
from modules.motor_workbench import MotorWorkbench, collect_metrics, write_json
from test_workbench import csv_fixture


def row(case, torque=2, ripple=30, iron=10, eta=90, ok=True):
    return dict(case=case,material_assignment=case,T_avg_Nm=torque,K_T_ripple_pct=ripple,
                P_Fe_W=iron,eta_pct=eta,ok=ok)


class FakeMotor:
    def archived_results(self):
        return []

    def jobs(self):
        return []


class MotorOptimizationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.storage=self.root/'new_artifacts'
        self.manager=MotorOptimization(self.root,self.storage,FakeMotor())
        self.source=self.root/'motor/v8_1/outputs/v8_1_material_comparison.json'
        self.source.parent.mkdir(parents=True)
        self.data=dict(generated='original-time',baseline=row('baseline',1.8),
                       control=row('control',1.9,50,20,89),
                       rows=[row('good',2,30,18,90),row('bad',2,60,23,88,False),row('zero_score',1.8,50,20,89)],
                       ranked=[row('good')],best_case=row('good'))
        self.source.write_text(json.dumps(self.data),encoding='utf-8')

    def test_original_control_retains_baseline_torque_and_original_weighted_score(self):
        result=self.manager.analyze(dict(dataset='v8_1'))
        good=result['ranked'][0]
        self.assertEqual(good['case'],'good')
        self.assertEqual(result['torque_reference'],'original_baseline')
        self.assertEqual(result['meets_limits_count'],1)
        self.assertAlmostEqual(good['score'],37)
        self.assertAlmostEqual(good['ripple_reduction_pct'],40)
        self.assertAlmostEqual(good['iron_loss_reduction_pct'],10)
        self.assertFalse(result['final_physical_optimization_certified'])
        self.assertEqual(result['new_native_solves'],0)

    def test_source_outputs_remain_identical_and_artifacts_survive_new_instance(self):
        original=self.source.read_bytes()
        result=self.manager.analyze(dict(dataset='v8_1',config=dict(min_ripple_reduction_pct=10)))
        self.assertEqual(original,self.source.read_bytes())
        reopened=MotorOptimization(self.root,self.storage,FakeMotor())
        self.assertEqual(reopened.history()[0]['id'],result['id'])
        with zipfile.ZipFile(reopened.artifact(result['id'],'analysis_bundle.zip')) as bundle:
            self.assertEqual(set(bundle.namelist()),{'analysis.json','ranking.csv','analysis.md'})
            stored=json.loads(bundle.read('analysis.json'))
            self.assertEqual(stored['dataset']['source_hashes'],result['dataset']['source_hashes'])

    def test_changed_configuration_changes_selection_without_new_native_work(self):
        strict=self.manager.analyze(dict(dataset='v8_1',config=dict(min_ripple_reduction_pct=50)))
        self.assertIsNone(strict['best_case'])
        loose=self.manager.analyze(dict(dataset='v8_1',config=dict(min_ripple_reduction_pct=0)))
        zero=next(r for r in loose['ranked'] if r['case']=='zero_score')
        self.assertEqual(zero['score'],0)
        self.assertTrue(zero['meets_limits'])
        self.assertEqual(loose['meets_limits_count'],2)

    def test_failed_and_incomplete_rows_retained_without_score(self):
        ref=normalized(row('ref',2,50,20,89),True)
        missing=normalized(dict(case='missing'),False)
        failed=normalized(row('failed',ok=False))
        values=screen_rows([missing,failed],ref,ref,DEFAULT_CONFIG)
        self.assertEqual({r['case'] for r in values},{'missing','failed'})
        self.assertTrue(all(r['score'] is None and not r['meets_limits'] for r in values))

    def test_references_cannot_cross_datasets_or_use_zero_loss(self):
        with self.assertRaises(ValueError):
            self.manager.analyze(dict(dataset='v8_1',reference='other_version_case'))
        ref=normalized(row('zero',iron=0),True)
        with self.assertRaises(ValueError):
            screen_rows([],ref,ref,DEFAULT_CONFIG)
        with self.assertRaises(ValueError):
            self.manager.dataset('../../outside')

    def test_invalid_numeric_or_unknown_configuration_writes_no_artifacts(self):
        configs=[dict(ripple_weight=-1),dict(ripple_weight=True),dict(ripple_weight=float('nan')),
                 dict(iron_loss_weight=float('inf')),dict(min_torque_change_pct=1001),
                 dict(ripple_weight=0,iron_loss_weight=0,efficiency_weight=0),dict(unknown=1)]
        for config in configs:
            with self.subTest(config=config),self.assertRaises(ValueError):
                self.manager.analyze(dict(dataset='v8_1',config=config))
        self.assertFalse(self.storage.exists())

    def test_workflow_preserves_original_rank_order_and_download_whitelist(self):
        stage=next(s for s in self.manager.workflow()['stages'] if s['id']=='v8_1')
        self.assertEqual(stage['ranked'],self.data['ranked'])
        self.assertEqual(stage['generated'],'original-time')
        self.assertEqual(self.manager.source_file('v8_1',self.source.name),self.source)
        for source,name in [('unknown',self.source.name),('v8_1','motor.aedt'),('v8_1','../../outside.json')]:
            with self.assertRaises(ValueError):
                self.manager.source_file(source,name)

    def test_archive_report_only_and_abnormal_rows_are_visible_but_not_scored(self):
        class Archive(FakeMotor):
            def archived_results(self):
                return [dict(case='verified',csv_verified=True,metrics=dict(accepted=True),display_metrics=row('verified'),files={}),
                        dict(case='only_summary',csv_verified=False,display_metrics=row('only_summary'),reason='CSV missing',files={}),
                        dict(case='abnormal',csv_verified=True,metrics=dict(accepted=False),display_metrics=row('abnormal'),files={})]
        self.manager.motor=Archive()
        dataset=self.manager.dataset('v8_2_archive')
        self.assertEqual(len(dataset['rows']),3)
        self.assertEqual([r['case'] for r in dataset['references']],['verified'])
        result=self.manager.analyze(dict(dataset='v8_2_archive',config=dict(min_ripple_reduction_pct=0)))
        self.assertEqual(result['best_case'],'verified')
        self.assertEqual(len(result['ranked']),3)

    def test_active_queue_cannot_be_ranked(self):
        self.manager.motor.state=lambda _:dict(status='running')
        with self.assertRaisesRegex(ValueError,'扫描完成'):
            self.manager.dataset('scan_123456789abc')

    def finished_job(self):
        motor=MotorWorkbench(self.root,self.root/'motor_jobs',Path(__file__).resolve().parents[1])
        self.manager.motor=motor
        job='scan_123456789abc'
        folder=motor.path(job)
        case=folder/'case_01'
        csv_fixture(case)
        result=collect_metrics(case,'v8_2_cycle1')
        write_json(case/'result.json',result)
        write_json(folder/'state.json',dict(id=job,status='completed'))
        write_json(folder/'manifest.json',dict(id=job,model_version='new_frozen_template',measurement_protocol='v8_2_cycle1',
                  cases=[dict(id='case_01',material='fixture')]))
        return job,case

    def test_completed_job_recomputes_official_metrics_and_binds_protocol(self):
        job,case=self.finished_job()
        dataset=self.manager.dataset(job)
        self.assertEqual(dataset['measurement_protocol'],'v8_2_cycle1')
        self.assertEqual(dataset['rows'][0]['input_source'],'official_CSV_verified')
        result=json.loads((case/'result.json').read_text())
        result['metric_protocol']='periodic_cycle3_v1'
        write_json(case/'result.json',result)
        with self.assertRaisesRegex(ValueError,'测量口径'):
            self.manager.dataset(job)

    def test_changed_official_csv_or_saved_metrics_stop_scoring(self):
        job,case=self.finished_job()
        result=json.loads((case/'result.json').read_text())
        result['T_avg_Nm']+=.1
        write_json(case/'result.json',result)
        with self.assertRaisesRegex(ValueError,'官方 CSV 不一致'):
            self.manager.dataset(job)
        write_json(case/'result.json',collect_metrics(case,'v8_2_cycle1'))
        csv_path=case/'reports/Torque Plots.csv'
        csv_path.write_bytes(csv_path.read_bytes()+b'\n')
        with self.assertRaisesRegex(ValueError,'哈希改变'):
            self.manager.dataset(job)

    def test_download_rejects_native_and_unrelated_files(self):
        result=self.manager.analyze(dict(dataset='v8_1'))
        for artifact,name in [(result['id'],'motor.aedt'),('../elsewhere','analysis.json'),(result['id'],'../analysis.json')]:
            with self.assertRaises(ValueError):
                self.manager.artifact(artifact,name)

    def test_cpu_routes_allow_analysis_while_native_submit_stays_disabled(self):
        project=Path(__file__).resolve().parents[1]
        with patch.dict(os.environ,{'MAGSIM_CPU_ONLY':'1','MAGSIM_AUTO_RESUME_QUEUE':'0'}):
            app=Flask(__name__,template_folder=str(project/'templates'))
            app.register_blueprint(create_workbench(project,self.storage,self.root))
            client=app.test_client()
            self.assertEqual(client.get('/motor-optimization').status_code,200)
            response=client.post('/api/workbench/optimization/analyze',json=dict(dataset='v8_1'))
            self.assertEqual(response.status_code,200)
            result=response.get_json()
            download=client.get(result['files']['analysis_bundle.zip'])
            self.assertEqual(download.status_code,200)
            download.close()
            self.assertEqual(client.post('/api/workbench/motor/jobs/scan_123456789abc/submit',json={}).status_code,423)
            self.assertEqual(client.get('/api/workbench/optimization/sources/v8_1/motor.aedt').status_code,400)


if __name__=='__main__':
    unittest.main()
