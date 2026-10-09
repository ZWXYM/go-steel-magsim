"""CPU/mocked worker lifecycle only: none of these tests launches or solves AEDT."""
import copy
import csv
import json
import os
import shutil
import subprocess
import sys
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock,patch

from flask import Flask
import test_calibrated_motor_analysis as fixtures
from modules.calibrated_motor_execution import CalibratedMotorExecution,BUDGET,read,verify_inputs
from modules.material_library import sha
from modules.motor_queue import QueueLease,process_identity,identity_status
from modules.motor_workbench import write_json
from modules.workbench_routes import create_workbench
from tools import calibrated_motor_analysis_worker as worker

class ExecutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):fixtures.BHAnalysisTests.setUpClass.__func__(cls)
    @classmethod
    def tearDownClass(cls):fixtures.BHAnalysisTests.tearDownClass.__func__(cls)
    def setUp(self):
        fixtures.BHAnalysisTests.setUp(self)
        self.exe=CalibratedMotorExecution(self.analysis,self.motor,self.store/'calibrated_motor_execution')

    def audit(self,cycles=1):
        return dict(local_CS_verified=True,moving_insert_count=48,
            inserts=[dict(material=self.plan['material_name']) for _ in range(48)],
            core_loss=dict(complete_insert_coverage=True,enabled_object_names=['Rotor','Stator']+['GOES_'+str(i) for i in range(48)]),
            operating_point=dict(speed_rpm=3000,poles=4,cycles=str(cycles),points_per_cycle='30'),
            setup=dict(StopTime='.03s' if cycles==3 else '.01s',TimeStep='.000333333333333s',UseAdaptiveTimeStep='false',
                AutoDetectSteadyState='false',FastReachSteadyState='false'))

    def ready(self,measurement='v8_2_cycle1'):
        self.plan=self.analysis.prepare(dict(native_import=self.imported['id'],measurement_protocol=measurement))
        fixtures.BHAnalysisTests.simulated_import(self)
        with patch('modules.calibrated_motor_analysis.audit_project',return_value=self.audit()):self.analysis.prepare_model(self.plan['id'])
        with patch('modules.calibrated_motor_execution.audit_project',return_value=self.audit()):return self.exe.prepare({'analysis_plan':self.plan['id']})

    def mock_app(self):
        app=MagicMock();variables={}
        app.__setitem__.side_effect=variables.__setitem__
        app.get_evaluated_value.side_effect=lambda key:float(variables[key])
        setup=SimpleNamespace(props={},update=MagicMock(return_value=True));app.get_setup.return_value=setup
        app.validate_simple.return_value=1;app.analyze_setup.return_value=True
        return app

    def export(self,app,name,expressions,path):
        self.assertEqual(expressions,['-Moving1.Torque'])
        raw=fixtures.BHAnalysisTests.fixture_csv(self,self.measurement).read_bytes()
        path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)

    def simulated_start(self,record):
        folder=self.exe.path(record['id']);state=read(folder/'state.json');state.update(status='starting',queue_token='test-token')
        write_json(folder/'state.json',state);write_json(self.motor.storage/'queue.lock',dict(process_identity(),token='test-token'))
        return folder

    def fake_run(self,record,app=None):
        self.measurement=record['measurement_protocol'];folder=self.simulated_start(record);desktop=MagicMock();desktop.release_desktop.return_value=True
        app=app or self.mock_app();cycles=record['requested_protocol']['cycles']
        with patch.object(worker,'require_configured_license_connection',return_value={'TCP':True}),\
             patch.object(worker,'create_session',return_value=(desktop,process_identity())),\
             patch.object(worker,'open_app',return_value=app),\
             patch.object(worker,'verify_model',return_value=self.audit(cycles)),patch.object(worker,'export_report',side_effect=self.export):
            state=worker.run(folder,self.motor.storage,self.native.storage)
        return folder,state,app,desktop

    def test_unimported_plan_cannot_prepare_execution(self):
        plan=self.analysis.prepare({'native_import':self.imported['id']})
        with patch('subprocess.Popen') as launch:
            with self.assertRaisesRegex(ValueError,'实际导入'):self.exe.prepare({'analysis_plan':plan['id']})
        launch.assert_not_called();self.assertEqual(list(self.exe.storage.iterdir()),[]);self.assertEqual(self.motor.jobs(),[])

    def test_execution_freezes_model_lineage_budget_and_source_remains_unchanged(self):
        record=self.ready('periodic_cycle3_v1');folder=self.exe.path(record['id'])
        verify_inputs(folder,self.motor.project,dispatch=True)
        self.assertEqual(record['budget'],BUDGET);self.assertEqual(record['excluded_grades'],['B30P105'])
        self.assertEqual(record['effective_bank_sha256'],self.plan['effective_bank_sha256'])
        self.assertFalse(record['result_available']);self.assertFalse(record['efficiency_enabled'])
        self.assertEqual((folder/'inputs/motor.aedt').read_bytes(),(self.analysis.path(self.plan['id'])/'model/motor.aedt').read_bytes())
        self.assertEqual(self.motor.jobs(),[]);self.assertFalse((folder/'case').exists())
        self.assertEqual(CalibratedMotorExecution(self.analysis,self.motor,self.exe.storage).get(record['id'])['status'],'prepared')

    def test_tamper_or_producer_changes_stop_before_launch(self):
        record=self.ready();folder=self.exe.path(record['id']);p=folder/'inputs/motor.aedt';p.write_bytes(p.read_bytes()+b'\n')
        with patch('subprocess.Popen') as launch:
            with self.assertRaisesRegex(ValueError,'冻结输入改变'):self.exe.start(record['id'])
        launch.assert_not_called()
        record=self.ready();folder=self.exe.path(record['id']);manifest=read(folder/'manifest.json')
        manifest['producer_hashes']['tools/calibrated_motor_analysis_worker.py']='changed';write_json(folder/'manifest.json',manifest)
        self.assertEqual(self.exe.get(record['id'])['status'],'prepared_source_changed')
        with patch('subprocess.Popen') as launch:
            with self.assertRaisesRegex(ValueError,'冻结程序已改变'):self.exe.start(record['id'])
        launch.assert_not_called()

    def test_unavailable_license_preserves_prepared_without_execution_trace(self):
        record=self.ready();folder=self.exe.path(record['id']);before=(folder/'state.json').read_bytes()
        with patch('modules.calibrated_motor_execution.require_configured_license_connection',side_effect=ValueError('unreachable')),patch('subprocess.Popen') as launch:
            with self.assertRaisesRegex(ValueError,'未下发'):self.exe.start(record['id'])
        launch.assert_not_called();self.assertEqual((folder/'state.json').read_bytes(),before)
        self.assertFalse((self.motor.storage/'queue.lock').exists())
        for name in ['case','worker.log','dispatch.json']:self.assertFalse((folder/name).exists())

    def test_launch_once_does_not_requeue_scan_or_repeat_failed_record(self):
        record=self.ready();python=self.motor.root/'.runtime/aedt/Scripts/python.exe';python.parent.mkdir(parents=True);python.write_bytes(b'fixture')
        with patch('modules.calibrated_motor_execution.require_configured_license_connection',return_value={'TCP':True}),\
             patch('modules.calibrated_motor_execution.subprocess.Popen') as launch,patch('modules.calibrated_motor_execution.threading.Thread'):
            launch.return_value.pid=os.getpid();self.assertEqual(self.exe.start(record['id'])['status'],'starting')
            with self.assertRaisesRegex(ValueError,'重复执行'):self.exe.start(record['id'])
        self.assertEqual(launch.call_count,1);self.assertEqual(self.motor.jobs(),[])
        state=read(self.exe.path(record['id'])/'state.json');state['status']='failed';write_json(self.exe.path(record['id'])/'state.json',state)
        with self.assertRaisesRegex(ValueError,'重复执行'):self.exe.start(record['id'])

    def test_unknown_or_queued_scan_and_import_lease_block_submission(self):
        record=self.ready()
        with patch.object(self.motor,'jobs',return_value=[{'id':'scan_abc','status':'queued'}]),patch('subprocess.Popen') as launch:
            with self.assertRaisesRegex(ValueError,'扫描队列'):self.exe.start(record['id'])
        launch.assert_not_called()
        folder=self.exe.path(record['id']);state=read(folder/'state.json');state['status']='needs_attention';write_json(folder/'state.json',state)
        with self.assertRaisesRegex(ValueError,'暂不下发'):self.exe.ensure_idle()

    def test_timeout_and_dead_worker_are_read_only_attention_not_restart(self):
        record=self.ready();folder=self.exe.path(record['id']);state=read(folder/'state.json');state['status']='running';write_json(folder/'state.json',state)
        before=(folder/'state.json').read_bytes();proc=MagicMock();proc.wait.side_effect=subprocess.TimeoutExpired('fixture',1200)
        self.exe._watch(proc,folder)
        self.assertEqual(self.exe.get(record['id'])['status'],'needs_attention');self.assertEqual((folder/'state.json').read_bytes(),before)
        proc.kill.assert_not_called();proc.terminate.assert_not_called()

    def test_busy_scan_os_lease_prevents_launch_and_preserves_state(self):
        record=self.ready();folder=self.exe.path(record['id']);before=(folder/'state.json').read_bytes()
        python=self.motor.root/'.runtime/aedt/Scripts/python.exe';python.parent.mkdir(parents=True);python.write_bytes(b'fixture')
        guard=QueueLease(self.motor.storage/'queue.guard');self.assertTrue(guard.acquire())
        try:
            with patch('modules.calibrated_motor_execution.require_configured_license_connection',return_value={'TCP':True}),patch('subprocess.Popen') as launch:
                with self.assertRaisesRegex(ValueError,'仍在执行'):self.exe.start(record['id'])
            launch.assert_not_called();self.assertEqual((folder/'state.json').read_bytes(),before)
        finally:guard.release()

    def test_wrong_saved_time_or_losses_prevents_analyze(self):
        record=self.ready();folder=self.exe.path(record['id']);project=folder/'inputs/motor.aedt';app=self.mock_app()
        after=self.audit();after['setup']['TimeStep']='1ms'
        with patch.object(worker,'verify_model',side_effect=[self.audit(),after]):
            with self.assertRaisesRegex(ValueError,'实际保存周期'):worker.configure(app,project,record,folder)
        app.analyze_setup.assert_not_called();app.validate_simple.assert_not_called()

    def test_mocked_full_worker_collects_signed_csv_no_losses_and_reopens(self):
        record=self.ready('periodic_cycle3_v1');initial=(self.exe.path(record['id'])/'inputs/motor.aedt').read_bytes()
        folder,state,app,desktop=self.fake_run(record)
        self.assertEqual(state['status'],'completed');self.assertTrue((folder/'queue_owner_released.json').exists())
        app.analyze_setup.assert_called_once_with('Setup1',cores=2,gpus=0,use_auto_settings=False,revert_to_initial_mesh=False)
        desktop.release_desktop.assert_called_once_with(close_projects=True,close_on_exit=True)
        result=self.exe.get(record['id']);self.assertTrue(result['result_available'])
        self.assertEqual(result['result']['metrics']['n_samples'],31);self.assertEqual(result['result']['metrics']['source_n_samples'],91)
        self.assertIsNone(result['result']['metrics']['P_Fe_W']);self.assertIsNone(result['result']['metrics']['eta_estimate_pct'])
        self.assertEqual((folder/'inputs/motor.aedt').read_bytes(),initial);self.assertFalse((folder/'case/maxwell_reports').exists())
        self.assertEqual(self.exe.download(record['id'],'case/reports/Torque Plots.csv').read_bytes(),fixtures.BHAnalysisTests.fixture_csv(self,'periodic_cycle3_v1').read_bytes())
        with (folder/'summary.csv').open(encoding='utf-8-sig') as stream:self.assertNotIn('eta_estimate_pct',csv.DictReader(stream).fieldnames)
        self.assertEqual(self.motor.jobs(),[])

    def test_failed_solve_keeps_failure_and_never_collects_result(self):
        record=self.ready();app=self.mock_app();app.analyze_setup.return_value=False
        folder,state,app,desktop=self.fake_run(record,app)
        self.assertEqual(state['status'],'failed');self.assertTrue(read(folder/'failure.json')['solve_attempted'])
        self.assertFalse((folder/'result.json').exists());self.assertFalse(self.exe.get(record['id'])['result_available'])
        desktop.release_desktop.assert_called_once()
        with self.assertRaisesRegex(ValueError,'禁止重提'):worker.run(folder,self.motor.storage,self.native.storage)

    def test_late_solve_is_explicit_and_does_not_hide_budget_attention(self):
        record=self.ready();folder=self.exe.path(record['id']);app=self.mock_app()
        app.analyze_setup.side_effect=lambda *args,**kwargs:(write_json(folder/'attention.json',dict(error='fixture elapsed budget')) or True)
        folder,state,_,_=self.fake_run(record,app)
        self.assertEqual(state['status'],'completed_after_budget');self.assertTrue(self.exe.get(record['id'])['result']['budget_exceeded'])
        self.assertTrue((folder/'attention.json').exists())

    def test_budget_marker_prevents_late_initial_solve(self):
        record=self.ready();folder=self.exe.path(record['id']);app=self.mock_app();write_json(folder/'attention.json',dict(error='fixture budget'))
        with self.assertRaises(TimeoutError):worker.execute(app,folder/'inputs/motor.aedt',record,folder,time.monotonic()+1200,lambda phase:None)
        app.analyze_setup.assert_not_called()

    def test_mocked_app_initialization_failure_releases_only_owned_session(self):
        record=self.ready();folder=self.simulated_start(record);desktop=MagicMock();desktop.release_desktop.return_value=True
        with patch.object(worker,'require_configured_license_connection',return_value={'TCP':True}),\
             patch.object(worker,'create_session',return_value=(desktop,process_identity())),\
             patch.object(worker,'open_app',side_effect=RuntimeError('mock initialization failure')):
            result=worker.run(folder,self.motor.storage,self.native.storage)
        self.assertEqual(result['status'],'failed');self.assertFalse(read(folder/'failure.json')['solve_attempted'])
        desktop.release_desktop.assert_called_once_with(close_projects=True,close_on_exit=True)
        self.assertTrue((folder/'queue_owner_released.json').exists());self.assertFalse(self.exe.get(record['id'])['result_available'])

    def test_changed_official_result_is_not_served(self):
        record=self.ready();folder,state,_,_=self.fake_run(record);path=folder/'case/reports/Torque Plots.csv';path.write_bytes(path.read_bytes()+b'\n')
        with self.assertRaisesRegex(ValueError,'官方转矩CSV哈希改变'):self.exe.get(record['id'])

    def test_existing_desktop_pid_is_not_claimed_or_closed(self):
        record=self.ready();folder=self.exe.path(record['id']);desktop=MagicMock();desktop.aedt_process_id=os.getpid()
        with patch.dict(sys.modules,{'ansys.aedt.core':SimpleNamespace(Desktop=MagicMock(return_value=desktop))}):
            with self.assertRaisesRegex(ValueError,'已有会话'):worker.create_session(folder/'inputs/motor.aedt',record,folder)
        desktop.release_desktop.assert_not_called();self.assertTrue((folder/'desktop_unowned.json').exists())

    def test_cpu_routes_prepare_allowed_start_blocked_and_original_plan_unchanged(self):
        record=self.ready();app=Flask('bh_execution_cpu')
        with patch.dict(os.environ,{'MAGSIM_CPU_ONLY':'1','MAGSIM_AUTO_RESUME_QUEUE':'0'}):
            app.register_blueprint(create_workbench(self.motor.project,self.store,self.root))
        c=app.test_client();url='/api/workbench/calibrated-motor/executions';old=(self.analysis.path(self.plan['id'])/'state.json').read_bytes()
        with patch('modules.calibrated_motor_execution.audit_project',return_value=self.audit()):
            response=c.post(url,json={'analysis_plan':self.plan['id']})
        self.assertEqual(response.status_code,200,response.json);artifact=response.json['id']
        self.assertEqual(c.get(url+'/'+artifact).json['status'],'prepared')
        with patch('subprocess.Popen') as launch:self.assertEqual(c.post(url+'/'+artifact+'/start',json={}).status_code,423)
        launch.assert_not_called();self.assertEqual((self.analysis.path(self.plan['id'])/'state.json').read_bytes(),old)
        response=c.get(url+'/'+artifact+'/bundle');self.assertEqual(response.status_code,200);response.close()
        self.assertEqual(c.get('/api/workbench/motor/jobs').json,[])

if __name__=='__main__':unittest.main()
