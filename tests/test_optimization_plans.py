import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from modules.motor_workbench import MotorWorkbench,collect_metrics,write_json
from modules.motor_optimization import MotorOptimization
from modules.optimization_plans import OptimizationPlans
from modules.workbench_routes import create_workbench
from test_workbench import csv_fixture


class OptimizationPlanTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.project=Path(__file__).resolve().parents[1]
        self.storage=self.root/'storage'
        self.motor=MotorWorkbench(self.root,self.storage/'motor',self.project)
        self.optimization=MotorOptimization(self.root,self.storage/'optimization',self.motor)
        self.plans=OptimizationPlans(self.optimization)
        self.job='scan_123456789abc'
        self.folder=self.motor.path(self.job)
        csv_fixture(self.folder/'case_01')
        write_json(self.folder/'case_01/result.json',collect_metrics(self.folder/'case_01','v8_2_cycle1'))
        write_json(self.folder/'manifest.json',dict(id=self.job,model_version='fixture',measurement_protocol='v8_2_cycle1',cores=4,
                  cases=[dict(id='case_01',material='grade')]))
        write_json(self.folder/'state.json',dict(id=self.job,status='completed',cases=[dict(id='case_01',material='grade',status='completed')]))
        self.config=dict(min_ripple_reduction_pct=0)

    def test_existing_completed_job_automatic_analysis_is_idempotent_and_never_submits(self):
        state_bytes=(self.folder/'state.json').read_bytes()
        with patch.object(self.motor,'submit',side_effect=AssertionError('No native dispatch')):
            plan=self.plans.prepare(dict(job_id=self.job,reference_material='grade',config=self.config))
            self.assertEqual(plan['status'],'completed')
            again=self.plans.refresh(plan['id'])
        self.assertEqual(again['analysis_id'],plan['analysis_id'])
        self.assertEqual(plan['best_case'],'case_01')
        self.assertEqual(len(list(self.optimization.storage.glob('opt_*'))),1)
        self.assertEqual(state_bytes,(self.folder/'state.json').read_bytes())

    def test_restart_can_finish_waiting_analysis_without_native_dispatch(self):
        state=json.loads((self.folder/'state.json').read_text())
        state['status']='running'
        write_json(self.folder/'state.json',state)
        with patch.object(self.plans,'start_monitor'):
            plan=self.plans.prepare(dict(job_id=self.job,config=self.config))
        self.assertEqual(plan['status'],'watching')
        state['status']='completed'
        write_json(self.folder/'state.json',state)
        restarted=OptimizationPlans(self.optimization)
        with patch.object(self.motor,'submit',side_effect=AssertionError('Never resubmit on restart')):
            final=restarted.refresh(plan['id'])
        self.assertEqual(final['status'],'completed')

    def test_changed_frozen_manifest_requires_attention_without_score(self):
        state=json.loads((self.folder/'state.json').read_text());state['status']='running'
        write_json(self.folder/'state.json',state)
        with patch.object(self.plans,'start_monitor'):
            plan=self.plans.prepare(dict(job_id=self.job,config=self.config))
        state['status']='completed';write_json(self.folder/'state.json',state)
        manifest=self.folder/'manifest.json';manifest.write_bytes(manifest.read_bytes()+b'\n')
        final=self.plans.refresh(plan['id'])
        self.assertEqual(final['status'],'needs_attention')
        self.assertIn('任务定义',final['error'])
        self.assertFalse(list(self.optimization.storage.glob('opt_*')))

    def test_invalid_config_or_reference_does_not_create_plan_or_dispatch(self):
        for data in [dict(job_id=self.job,config={'ripple_weight':-1}),dict(job_id=self.job,reference_material='other'),dict(materials=['grade'],reference_material='other')]:
            with patch.object(self.motor,'prepare',side_effect=AssertionError('No job on invalid inputs')):
                with self.assertRaises(ValueError):
                    self.plans.prepare(data)
        self.assertFalse(self.plans.storage.exists())

    def test_plan_configuration_tampering_cannot_change_score(self):
        plan=self.plans.prepare(dict(job_id=self.job,config=self.config))
        path=self.plans.path(plan['id'])/'plan.json'
        record=json.loads(path.read_text());record['config']['ripple_weight']=100
        write_json(path,record)
        with self.assertRaisesRegex(ValueError,'配置已改变'):
            self.plans.get(plan['id'])

    def test_cpu_route_can_bind_and_analyze_but_cannot_submit_plan(self):
        with patch.dict(os.environ,{'MAGSIM_CPU_ONLY':'1','MAGSIM_AUTO_RESUME_QUEUE':'0'}):
            app=Flask(__name__)
            app.register_blueprint(create_workbench(self.project,self.storage,self.root))
            client=app.test_client()
            response=client.post('/api/workbench/optimization/plans',json=dict(job_id=self.job,config=self.config))
            self.assertEqual(response.status_code,200)
            plan=response.get_json()
            self.assertEqual(plan['status'],'completed')
            self.assertEqual(client.post(f"/api/workbench/optimization/plans/{plan['id']}/submit",json={}).status_code,423)
            self.assertEqual(client.post(f"/api/workbench/optimization/plans/{plan['id']}/refresh",json={}).status_code,200)

    def test_corrupt_plan_does_not_block_other_records_or_start_native_tasks(self):
        plan=self.plans.prepare(dict(job_id=self.job,config=self.config))
        bad=self.plans.path('plan_abcdef123456')
        write_json(bad/'state.json',dict(status='watching'))
        (bad/'plan.json').write_text('{broken')
        records={p['id']:p for p in self.plans.records()}
        self.assertEqual(records[plan['id']]['status'],'completed')
        self.assertFalse(records[bad.name]['source_readable'])
        with patch.object(self.motor,'submit',side_effect=AssertionError('Never dispatch')):
            self.plans.start_monitor()
        self.assertFalse(self.plans.monitor_active)


if __name__=='__main__':
    unittest.main()
