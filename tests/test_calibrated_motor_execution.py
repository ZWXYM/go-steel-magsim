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
from pathlib import Path
from unittest.mock import MagicMock,patch

from flask import Flask
import test_calibrated_motor_analysis as fixtures
from modules.calibrated_motor_execution import CalibratedMotorExecution,BUDGET,execution_budget,read,verify_inputs
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
        points=self.plan.get('requested_protocol',{}).get('points_per_cycle',30)
        return dict(local_CS_verified=True,moving_insert_count=48,
            inserts=[dict(name='GOES_'+str(i),material=self.plan['material_name']) for i in range(48)],
            core_loss=dict(complete_insert_coverage=True,enabled_object_names=['Rotor','Stator']+['GOES_'+str(i) for i in range(48)]),
            operating_point=dict(speed_rpm=3000,poles=4,cycles=str(cycles),points_per_cycle=str(points)),
            setup=dict(StopTime='.03s' if cycles==3 else '.01s',TimeStep=format(1/(100*points),'.12g')+'s',UseAdaptiveTimeStep='false',
                AutoDetectSteadyState='false',FastReachSteadyState='false',
                SmoothBHCurve=str(self.plan.get('requested_protocol',{}).get('solver_settings',{}).get('SmoothBHCurve',False)).lower()))

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
        write_json(folder/'state.json',state);write_json(self.motor.storage/'queue.lock',dict(process_identity(),token='test-token',torque_execution_id=record['id']))
        return folder

    def fake_run(self,record,app=None,desktop=None):
        self.measurement=record['measurement_protocol'];folder=self.simulated_start(record)
        desktop=desktop or MagicMock();desktop.release_desktop.return_value=True
        desktop.odesktop.GetProcessID.return_value=os.getpid()
        app=app or self.mock_app();cycles=record['requested_protocol']['cycles']
        with patch.object(worker,'require_configured_license_connection',return_value={'TCP':True}),\
             patch.object(worker,'create_session',return_value=(desktop,process_identity())),\
             patch.object(worker,'open_app',return_value=app),\
             patch.object(worker,'verify_solver_settings',side_effect=lambda text,requested:{k:str(v).lower() for k,v in (requested or {}).items()}),\
             patch.object(worker,'launch_budget_watch',return_value=None),\
             patch.object(worker,'verify_model',return_value=self.audit(cycles)),patch.object(worker,'export_report',side_effect=self.export):
            state=worker.run(folder,self.motor.storage,self.native.storage)
        return folder,state,app,desktop

    def test_equivalent_protocols_have_distinct_model_identity(self):
        from modules.calibrated_motor_analysis import protocol,execution_model_version
        original=protocol('v8_2_cycle1')
        self.assertNotIn('equivalent_direction',original)
        versions={execution_model_version('v8_2_cycle1')}
        for direction in ('RD','TD'):
            name='v8_2_cycle1_'+direction+'_equivalent_v1';p=protocol(name)
            self.assertEqual(p['equivalent_direction'],direction);self.assertEqual(p['source_samples'],31)
            self.assertEqual(p['requested_time_step_s'],original['requested_time_step_s'])
            versions.add(execution_model_version(name))
        self.assertEqual(len(versions),3)

    def test_open_app_requires_the_servers_actual_pid_before_configuration(self):
        app=MagicMock();app.odesktop.GetProcessID.return_value=999
        with patch.dict(sys.modules,{'ansys.aedt.core':SimpleNamespace(Maxwell2d=MagicMock(return_value=app))}):
            with self.assertRaisesRegex(ValueError,'实际会话PID'):
                worker.open_app(Path('unused.aedt'),dict(design='Motor',aedt_version='2025.1'),dict(pid=123))
        app.analyze_setup.assert_not_called();app.save_project.assert_not_called()

    def equivalent_configuration(self,direction):
        record=self.ready('v8_2_cycle1_'+direction+'_equivalent_v1');folder=self.exe.path(record['id']);app=self.mock_app()
        app.materials.odefinition_manager.GetProjectMaterialNames.return_value=[]
        with patch.object(worker,'verify_model',return_value=self.audit()) as model,\
             patch.object(worker,'verify_saved_equivalent_material',return_value={'equivalent_direction':direction,'source_points_unchanged':True}):
            result=worker.configure(app,folder/'inputs/motor.aedt',record,folder)
        name=worker.equivalent_material_name(record['material_contract'],direction)
        args=app.materials.odefinition_manager.AddMaterial.call_args.args[0]
        self.assertEqual(args,worker.equivalent_native_arguments(record['material_contract'],direction))
        for row in self.audit()['inserts']:self.assertEqual(app.modeler[row['name']].material_name,name)
        self.assertTrue(result['original_loss_selection_preserved']);self.assertTrue((folder/'equivalent_material.json').exists())
        self.assertTrue(model.call_args.kwargs['execution_material']);app.analyze_setup.assert_not_called()

    def test_RD_equivalent_configuration_changes_only_the_new_material_assignment(self):
        self.equivalent_configuration('RD')

    def test_TD_equivalent_configuration_uses_the_TD_source(self):
        self.equivalent_configuration('TD')

    def test_intrinsic_worker_passes_the_explicit_representation_and_verifies_saved_definition(self):
        record=self.ready('v8_2_cycle1_RD_equivalent_intrinsic_v1');folder=self.exe.path(record['id']);app=self.mock_app();app.materials.odefinition_manager.GetProjectMaterialNames.return_value=[]
        with patch.object(worker,'verify_model',return_value=self.audit()),patch.object(worker,'verify_solver_settings',return_value={}),patch.object(worker,'verify_saved_equivalent_material',return_value={'curve_representation':'Intrinsic'}) as verified:
            worker.configure(app,folder/'inputs/motor.aedt',record,folder)
        verified.assert_called_once_with((folder/'inputs/motor.aedt').read_text(encoding='utf-8-sig'),record['material_contract'],'RD','Intrinsic')
        args=app.materials.odefinition_manager.AddMaterial.call_args.args[0];self.assertTrue(args[0].endswith('_RD_INTRINSIC_EQ_v1'))
        mu=next(x for x in args if isinstance(x,list) and x[0]=='NAME:permeability');self.assertEqual(mu[mu.index('BTypeForSingleCurve:=')+1],'Intrinsic');app.analyze_setup.assert_not_called()

    def test_fine100_executes_and_collects_101_samples_with_its_own_budget(self):
        record=self.ready('v8_2_cycle1_RD_equivalent_intrinsic_fine100_v1')
        self.assertEqual(record['budget']['wall_seconds'],1800);self.assertEqual(BUDGET['wall_seconds'],1200)
        with patch.object(worker,'verify_saved_equivalent_material',return_value={'curve_representation':'Intrinsic'}):folder,state,app,desktop=self.fake_run(record)
        self.assertEqual(state['status'],'completed')
        self.assertIn(unittest.mock.call('NumTorquePointsPerCycle','100'),app.__setitem__.call_args_list)
        self.assertEqual(app.get_setup.return_value.props['TimeStep'],'0.0001s')
        result=read(folder/'result.json');self.assertEqual(result['metrics']['source_n_samples'],101)
        self.assertEqual(result['model_version'],'V8_2_scalar_RD_equivalent_BH_only_intrinsic_fine100_v1')
        self.assertTrue(result['metrics']['torque_numerical_acceptance']);self.assertFalse(result['efficiency_enabled'])
        self.assertEqual(execution_budget('v8_2_cycle1_RD_equivalent_intrinsic_v1'),BUDGET)

    def test_fine100_saved_sampling_and_fixed_budget_are_verified(self):
        record=self.ready('v8_2_cycle1_RD_equivalent_intrinsic_fine100_v1');folder=self.exe.path(record['id']);app=self.mock_app()
        app.materials.odefinition_manager.GetProjectMaterialNames.return_value=[]
        wrong=self.audit();wrong['operating_point']['points_per_cycle']='30'
        with patch.object(worker,'verify_model',side_effect=[self.audit(),wrong]),patch.object(worker,'verify_solver_settings',return_value={}),patch.object(worker,'verify_saved_equivalent_material',return_value={}):
            with self.assertRaisesRegex(ValueError,'实际保存周期'):worker.configure(app,folder/'inputs/motor.aedt',record,folder)
        m=read(folder/'manifest.json');m['budget']['wall_seconds']=1200;write_json(folder/'manifest.json',m)
        with self.assertRaisesRegex(ValueError,'固定预算'):verify_inputs(folder,self.exe.project)

    def test_malformed_budget_is_saved_as_failure_before_creating_a_native_session(self):
        record=self.ready();folder=self.simulated_start(record);m=read(folder/'manifest.json');m['budget']=None;write_json(folder/'manifest.json',m)
        with patch.object(worker,'create_session',side_effect=AssertionError('bad input cannot create a Desktop')) as create:
            state=worker.run(folder,self.motor.storage,self.native.storage)
        self.assertEqual(state['status'],'failed');self.assertTrue((folder/'failure.json').exists())
        self.assertFalse(read(folder/'failure.json')['solve_attempted']);self.assertFalse((folder/'case').exists());create.assert_not_called()

    def test_unsmoothed_intrinsic_result_reopens_only_with_the_requested_saved_false_flag(self):
        record=self.ready('v8_2_cycle1_RD_equivalent_intrinsic_nosmooth_v1')
        with patch.object(worker,'verify_saved_equivalent_material',return_value={'curve_representation':'Intrinsic'}):folder,state,app,desktop=self.fake_run(record)
        self.assertEqual(state['status'],'completed');self.assertFalse(app.get_setup.return_value.props['SmoothBHCurve'])
        self.assertEqual(read(folder/'result.json')['model_version'],'V8_2_scalar_RD_equivalent_BH_only_intrinsic_nosmooth_v1')
        with patch('modules.calibrated_motor_execution.verify_model',return_value=self.audit()),patch('modules.calibrated_motor_execution.verify_solver_settings',return_value={'SmoothBHCurve':'false'}),patch('modules.calibrated_motor_execution.audit_project',return_value=self.audit()):
            self.assertTrue(self.exe.get(record['id'])['result_available'])
        wrong=self.audit();wrong['setup']['SmoothBHCurve']='true'
        with patch('modules.calibrated_motor_execution.verify_model',return_value=self.audit()),patch('modules.calibrated_motor_execution.verify_solver_settings',return_value={'SmoothBHCurve':'false'}),patch('modules.calibrated_motor_execution.audit_project',return_value=wrong):
            with self.assertRaisesRegex(ValueError,'平滑诊断'):self.exe.get(record['id'])

    def test_smooth_diagnostic_preserves_legacy_protocol_and_uses_distinct_model_version(self):
        from modules.calibrated_motor_analysis import protocol,execution_model_version
        old=protocol('v8_2_cycle1')
        self.assertNotIn('solver_settings',old);self.assertEqual(old['id'],'v8_2_cycle1')
        new=protocol('v8_2_cycle1_smooth_bh_v1')
        self.assertEqual(new['solver_settings'],{'SmoothBHCurve':True})
        self.assertEqual(new['stop_s'],old['stop_s']);self.assertEqual(new['requested_time_step_s'],old['requested_time_step_s'])
        self.assertNotEqual(execution_model_version(new['id']),execution_model_version(old['id']))

    def test_smooth_configuration_requires_actual_saved_flag_before_analyze(self):
        record=self.ready('v8_2_cycle1_smooth_bh_v1');folder=self.exe.path(record['id']);app=self.mock_app()
        before=self.audit();before['setup']['SmoothBHCurve']='false';after=copy.deepcopy(before);after['setup']['SmoothBHCurve']='true'
        with patch.object(worker,'verify_model',side_effect=[before,after]),patch.object(worker,'verify_solver_settings',return_value={'SmoothBHCurve':'true'}):
            configuration=worker.configure(app,folder/'inputs/motor.aedt',record,folder)
        self.assertIs(app.get_setup.return_value.props['SmoothBHCurve'],True)
        self.assertEqual(configuration['solver_settings_requested'],{'SmoothBHCurve':True});app.analyze_setup.assert_not_called()
        with patch.object(worker,'verify_model',return_value=before),patch.object(worker,'verify_solver_settings',side_effect=ValueError('平滑设置未保存')):
            with self.assertRaisesRegex(ValueError,'平滑设置'):worker.configure(app,folder/'inputs/motor.aedt',record,folder)
        app.analyze_setup.assert_not_called()

    def test_smooth_mocked_worker_records_distinct_profile_and_reopens_with_saved_flag(self):
        record=self.ready('v8_2_cycle1_smooth_bh_v1');folder,state,app,desktop=self.fake_run(record)
        self.assertEqual(state['status'],'completed');saved=read(folder/'result.json')
        self.assertEqual(saved['model_version'],'V8_2_object_CS_BH_only_torque_smoothBH_v1')
        self.assertEqual(saved['solver_settings'],{'SmoothBHCurve':True});self.assertFalse(saved['efficiency_enabled'])
        with patch('modules.calibrated_motor_execution.audit_project',return_value=self.audit()),patch('modules.calibrated_motor_execution.verify_solver_settings',return_value={'SmoothBHCurve':'true'}):
            self.assertTrue(self.exe.get(record['id'])['result_available'])
        bad=self.audit();bad['setup']['SmoothBHCurve']='false'
        with patch('modules.calibrated_motor_execution.audit_project',return_value=bad),patch('modules.calibrated_motor_execution.verify_solver_settings',return_value={'SmoothBHCurve':'true'}):
            with self.assertRaisesRegex(ValueError,'平滑诊断'):self.exe.get(record['id'])

    def test_smooth_requested_inputs_are_frozen_and_original_plan_is_not_rewritten(self):
        legacy=self.ready();before=(self.exe.path(legacy['id'])/'manifest.json').read_bytes()
        record=self.ready('v8_2_cycle1_smooth_bh_v1');folder=self.exe.path(record['id']);m=read(folder/'manifest.json')
        self.assertEqual(m['requested_protocol']['solver_settings'],{'SmoothBHCurve':True})
        m['requested_protocol']['solver_settings']['SmoothBHCurve']=False;write_json(folder/'manifest.json',m)
        with self.assertRaisesRegex(ValueError,'固定预算或输入范围'):verify_inputs(folder,self.motor.project)
        self.assertEqual(before,(self.exe.path(legacy['id'])/'manifest.json').read_bytes())

    def test_budget_stop_context_is_readable_without_rewriting_original_failure(self):
        record=self.ready();folder=self.exe.path(record['id']);state=read(folder/'state.json')
        state.update(status='failed',error='raw RPC failure');write_json(folder/'state.json',state)
        identity=process_identity();write_json(folder/'desktop_session.json',dict(identity=identity,dedicated_session=True,closed=True))
        write_json(folder/'budget_stop.json',dict(execution_id=record['id'],identity=identity,stop_attempted=True,
            native_project=str(folder/'case/motor.aedt'),native_return='stopped'))
        before=(folder/'state.json').read_bytes();result=self.exe.get(record['id'])
        self.assertFalse(result['result_available']);self.assertIn('达到1200秒预算',result['error'])
        self.assertEqual(result['raw_error'],'raw RPC failure');self.assertEqual(before,(folder/'state.json').read_bytes())

    def test_real_cpu_watch_cli_exits_for_finished_job_without_native_session(self):
        record=self.ready();folder=self.exe.path(record['id']);state=read(folder/'state.json')
        state.update(status='failed',phase='finished',queue_token='fixture-cli-token');write_json(folder/'state.json',state)
        deadline=time.monotonic()+60
        write_json(folder/'budget_watch.json',dict(policy=worker.BUDGET_STOP_POLICY,execution_id=record['id'],
            queue_token='fixture-cli-token',deadline_monotonic=deadline,start_monotonic=deadline-1200))
        result=subprocess.run([sys.executable,'-X','utf8',str(self.motor.project/'tools/calibrated_motor_analysis_worker.py'),
            '--folder',str(folder),'--watch-budget'],capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertFalse((folder/'desktop_session.json').exists());self.assertFalse((folder/'solve_attempt.json').exists())
        self.assertFalse((folder/'budget_stop.json').exists())

    def test_budget_watcher_must_be_ready_before_native_analyze(self):
        record=self.ready();folder=self.simulated_start(record);app=self.mock_app()
        with patch.object(worker,'configure',return_value={}),\
             patch.object(worker,'launch_budget_watch',side_effect=ValueError('watcher not ready')):
            with self.assertRaisesRegex(ValueError,'not ready'):
                worker.execute(app,folder/'inputs/motor.aedt',record,folder,time.monotonic()+1200,lambda name:None)
        app.analyze_setup.assert_not_called();self.assertFalse((folder/'solve_attempt.json').exists())

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
        desktop=MagicMock();desktop.odesktop.GetMessages.return_value=['Fixture mesh failure']
        folder,state,app,desktop=self.fake_run(record,app,desktop)
        failure=read(folder/'failure.json')
        self.assertEqual(state['status'],'failed');self.assertTrue(failure['solve_attempted'])
        self.assertEqual(failure['phase'],'solving')
        self.assertEqual(failure['native_messages']['messages'],['Fixture mesh failure'])
        desktop.odesktop.GetMessages.assert_called_once_with('motor',record['design'],0)
        self.assertFalse((folder/'result.json').exists());self.assertFalse(self.exe.get(record['id'])['result_available'])
        desktop.release_desktop.assert_called_once()
        with self.assertRaisesRegex(ValueError,'禁止重提'):worker.run(folder,self.motor.storage,self.native.storage)

    def test_failed_solve_does_not_query_native_messages_after_budget_stop(self):
        record=self.ready();folder=self.exe.path(record['id']);app=self.mock_app()
        def stopped(*args,**kwargs):
            write_json(folder/'budget_stop.json',dict(status='fixture stopped'))
            return False
        app.analyze_setup.side_effect=stopped
        folder,state,_,desktop=self.fake_run(record,app)
        self.assertEqual(state['status'],'failed')
        self.assertEqual(read(folder/'failure.json')['native_messages']['status'],'skipped_at_budget')
        desktop.odesktop.GetMessages.assert_not_called()
        desktop.release_desktop.assert_called_once()

    def test_message_query_failure_keeps_the_solve_failure_and_session_cleanup(self):
        record=self.ready();app=self.mock_app();app.analyze_setup.return_value=False
        desktop=MagicMock();desktop.odesktop.GetMessages.side_effect=RuntimeError('fixture message service unavailable')
        folder,state,_,desktop=self.fake_run(record,app,desktop)
        failure=read(folder/'failure.json')
        self.assertEqual(state['status'],'failed')
        self.assertIn('Maxwell转矩分析失败',failure['error'])
        self.assertEqual(failure['native_messages']['status'],'query_failed')
        desktop.release_desktop.assert_called_once()

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
        desktop.odesktop.GetProcessID.return_value=os.getpid()
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
