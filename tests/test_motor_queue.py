"""Interrupt recovery never repeats a solve or changes archived reports."""
import csv
import json
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
import zipfile
from pathlib import Path
from unittest.mock import patch,Mock

PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT))
from modules.motor_queue import QueueLease,process_identity,identity_status,require_native_pid,native_transport_setting,configure_native_transport,bind_new_native_session,owned_rot_moniker_matches,OwnedNativeStopClient,connect_owned_stop_client
from modules.motor_workbench import MotorWorkbench,write_json,collect_metrics,acceptance_reason
from test_workbench import csv_fixture


class ExactStopAttachment(unittest.TestCase):
    def test_exact_moniker_version_and_pid_are_not_prefix_or_global_matches(self):
        for n in ['ElectronicsDesktop.2025.1.0:123','!Ansoft.ElectronicsDesktop.2025.1.0:123']:
            self.assertTrue(owned_rot_moniker_matches(n,'2025.1',123))
        for n in ['ElectronicsDesktop.2025.1.0:1234','ElectronicsDesktop.2024.1.0:123','other.ElectronicsDesktop.2025.1.0:123','ElectronicsDesktop.2025.1.0:999']:
            self.assertFalse(owned_rot_moniker_matches(n,'2025.1',123))
        for pid in [True,0,-1,'123']:
            with self.assertRaises(ValueError):owned_rot_moniker_matches('','2025.1',pid)

    def test_stop_only_adapter_rechecks_native_pid_and_create_time_without_closing(self):
        api=Mock();api.GetProcessID.return_value=123;api.StopSimulations.return_value='stopped';identity={'pid':123,'create_time':456.}
        adapter=OwnedNativeStopClient(api,identity,'exact')
        with patch('modules.motor_queue.identity_status',return_value='alive'):
            self.assertEqual(adapter.stop_simulations(),'stopped');api.StopSimulations.assert_called_once_with(True)
            api.GetProcessID.return_value=999
            with self.assertRaises(ValueError):adapter.stop_simulations()
        api.GetProcessID.return_value=123
        with patch('modules.motor_queue.identity_status',return_value='dead'):
            with self.assertRaises(ValueError):adapter.stop_simulations()
        self.assertEqual(api.StopSimulations.call_count,1);api.QuitApplication.assert_not_called()

    def test_dead_identity_never_attempts_a_native_connection(self):
        with patch('modules.motor_queue.identity_status',return_value='dead'):
            with self.assertRaisesRegex(ValueError,'身份不可确认'):connect_owned_stop_client({'pid':123,'create_time':456.},'2025.1')

    def test_connection_selects_only_exact_moniker_and_never_global_dispatch(self):
        api=Mock();api.GetProcessID.return_value=123;selected=Mock();selected.GetDisplayName.return_value='ElectronicsDesktop.2025.1.0:123'
        other=Mock();other.GetDisplayName.return_value='ElectronicsDesktop.2025.1.0:999';table=Mock();table.EnumRunning.return_value=[other,selected]
        com=SimpleNamespace(CoInitialize=Mock(),CreateBindCtx=Mock(return_value='ctx'),GetRunningObjectTable=Mock(return_value=table),IID_IDispatch='dispatch',com_error=RuntimeError)
        client=SimpleNamespace(Dispatch=Mock(return_value=api));shim=SimpleNamespace(win32_client=client)
        with patch('modules.motor_queue.identity_status',return_value='alive'),patch('modules.motor_queue.os.name','nt'),patch.dict(sys.modules,{'pythoncom':com,'ansys.aedt.core.internal.clr_module':shim}):
            result=connect_owned_stop_client({'pid':123,'create_time':456.},'2025.1')
        table.GetObject.assert_called_once_with(selected);self.assertEqual(result.aedt_process_id,123);api.StopSimulations.assert_not_called()
        self.assertNotEqual(client.Dispatch.call_args.args[0],'Ansoft.ElectronicsDesktop')

    def test_missing_or_ambiguous_target_never_binds_an_unrelated_object(self):
        matching=Mock();matching.GetDisplayName.return_value='ElectronicsDesktop.2025.1.0:123';table=Mock()
        com=SimpleNamespace(CoInitialize=Mock(),CreateBindCtx=Mock(),GetRunningObjectTable=Mock(return_value=table),com_error=RuntimeError)
        shim=SimpleNamespace(win32_client=Mock())
        with patch('modules.motor_queue.identity_status',return_value='alive'),patch('modules.motor_queue.os.name','nt'),patch.dict(sys.modules,{'pythoncom':com,'ansys.aedt.core.internal.clr_module':shim}):
            for entries in [[],[matching,matching]]:
                table.EnumRunning.return_value=entries
                with self.assertRaisesRegex(ValueError,'缺失或不唯一'):connect_owned_stop_client({'pid':123,'create_time':456.},'2025.1')
        table.GetObject.assert_not_called();shim.win32_client.Dispatch.assert_not_called()


class QueueRecovery(unittest.TestCase):
    def test_new_COM_session_can_bind_its_native_pid_when_client_field_is_none(self):
        desktop=Mock();desktop.aedt_process_id=None;desktop.odesktop.GetProcessID.return_value=123;desktop.odesktop.GetProjectList.return_value=[]
        identity=dict(pid=123,create_time=456.)
        with patch('modules.motor_queue.process_identity',return_value=identity) as identify:
            self.assertEqual(bind_new_native_session(desktop,{1,2}),identity)
        identify.assert_called_once_with(123);self.assertEqual(desktop.aedt_process_id,123)
        desktop.release_desktop.assert_not_called();desktop.odesktop.QuitApplication.assert_not_called()

    def test_new_grpc_session_keeps_matching_client_and_native_pid(self):
        desktop=Mock();desktop.aedt_process_id=123;desktop.odesktop.GetProcessID.return_value=123;desktop.odesktop.GetProjectList.return_value=[]
        with patch('modules.motor_queue.process_identity',return_value=dict(pid=123,create_time=456.)):
            self.assertEqual(bind_new_native_session(desktop,{1,2})['pid'],123)

    def test_new_native_session_never_adopts_existing_or_conflicting_server(self):
        for client,actual,existing in ((None,123,{123}),(124,123,set()),(123,123,{123})):
            desktop=Mock();desktop.aedt_process_id=client;desktop.odesktop.GetProcessID.return_value=actual
            with self.assertRaises(ValueError):bind_new_native_session(desktop,existing)
            self.assertEqual(desktop.aedt_process_id,client);desktop.release_desktop.assert_not_called()

    def test_new_session_with_preexisting_project_is_left_untouched(self):
        desktop=Mock();desktop.aedt_process_id=None;desktop.odesktop.GetProcessID.return_value=123;desktop.odesktop.GetProjectList.return_value=['UserProject']
        with self.assertRaisesRegex(ValueError,'未预期工程'):bind_new_native_session(desktop,set())
        self.assertIsNone(desktop.aedt_process_id);desktop.release_desktop.assert_not_called()

    def test_new_session_without_actual_server_pid_cannot_be_claimed(self):
        for actual in (None,True,'123',0):
            desktop=Mock();desktop.aedt_process_id=None;desktop.odesktop.GetProcessID.return_value=actual
            with self.assertRaises(ValueError):bind_new_native_session(desktop,set())
            self.assertIsNone(desktop.aedt_process_id)

    def test_native_transport_is_validated_before_preparing_a_task(self):
        for requested in ('auto','grpc','com'):
            with patch.dict('os.environ',{'MAGSIM_AEDT_TRANSPORT':requested}):self.assertEqual(native_transport_setting(),requested)
        with patch.dict('os.environ',{'MAGSIM_AEDT_TRANSPORT':'unknown'}):
            with self.assertRaises(ValueError):native_transport_setting()

    def test_native_transport_changes_only_this_interpreters_explicit_setting(self):
        setting=SimpleNamespace(use_grpc_api=None)
        with patch.dict(sys.modules,{'ansys.aedt.core.generic.settings':SimpleNamespace(settings=setting)}):
            configure_native_transport('auto');self.assertIsNone(setting.use_grpc_api)
            configure_native_transport('com');self.assertFalse(setting.use_grpc_api)
            configure_native_transport('grpc');self.assertTrue(setting.use_grpc_api)
            with self.assertRaises(ValueError):configure_native_transport('unknown')

    def test_native_pid_is_read_from_server(self):
        api=Mock();api.GetProcessID.return_value=123
        self.assertEqual(require_native_pid(api,123),123)
        api.GetProcessID.assert_called_once_with()

    def test_native_pid_mismatch_does_not_mutate_or_close_a_session(self):
        api=Mock();api.GetProcessID.return_value=999
        with self.assertRaisesRegex(ValueError,'实际会话PID.*期望123.*实际999'):
            require_native_pid(api,123)
        self.assertEqual([c[0] for c in api.mock_calls],['GetProcessID'])

    def test_native_pid_never_coerces_unknown_or_invalid_values(self):
        for actual in (None,True,'123',123.0,Mock()):
            api=Mock();api.GetProcessID.return_value=actual
            with self.assertRaises(ValueError):require_native_pid(api,123)
        for expected in (None,True,0,-1,'123'):
            api=Mock()
            with self.assertRaises(ValueError):require_native_pid(api,expected)
            api.GetProcessID.assert_not_called()

    def test_native_pid_query_failure_is_visible(self):
        api=Mock();api.GetProcessID.side_effect=RuntimeError('fixture unavailable')
        with self.assertRaisesRegex(ValueError,'无法从AEDT'):
            require_native_pid(api,123)

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.storage=self.root/'jobs'
        template=self.root/'motor/v8_2/cases/nippon_steel_23zh90/motorcad_full_v8_1_nippon_steel_23zh90.aedt'
        template.parent.mkdir(parents=True)
        template.write_text('\n'.join(f"$begin 'GeometryPart'\nName='GOES_V3_{i:02d}'\nPartCoordinateSystem={i+2}\nMaterialValue='\"old\"'\n$end 'GeometryPart'" for i in range(1,49)))
        write_json(self.root/'motor/v8_1/manifest.json',dict(cases=[dict(material='M6'),dict(material='GO2')]))
        self.manager=MotorWorkbench(self.root,self.storage,PROJECT)
        self.license=patch('modules.motor_workbench.license_status',return_value={'available':True})
        self.license.start()
        self.addCleanup(self.license.stop)

    def state(self,state,**fields):
        state.update(fields)
        write_json(self.manager.path(state['id'])/'state.json',state)
        return state

    def test_os_lease_excludes_other_process_and_releases_without_deleting_file(self):
        path=self.root/'guard'
        lease=QueueLease(path)
        self.assertTrue(lease.acquire())
        code="import sys; from modules.motor_queue import QueueLease; q=QueueLease(sys.argv[1]); print(q.acquire()); q.release()"
        def child():
            return subprocess.check_output([sys.executable,'-c',code,str(path)],cwd=PROJECT,text=True).strip()
        try:
            self.assertEqual(child(),'False')
        finally:
            lease.release()
        self.assertEqual(child(),'True')
        self.assertEqual(path.read_bytes(),b'0')

    def test_process_fingerprint_detects_pid_reuse_and_legacy_uncertainty(self):
        identity=process_identity()
        self.assertEqual(identity_status(identity),'alive')
        self.assertEqual(identity_status(dict(identity,create_time=identity['create_time']-1000)),'dead')
        self.assertEqual(identity_status({'pid':identity['pid']}),'unknown')
        self.assertEqual(identity_status(dict(identity,create_time=float('nan'))),'unknown')
        self.assertEqual(identity_status({'pid':0}),'unknown')

    def test_live_controller_or_orphan_worker_blocks_recovery_without_changes(self):
        state=self.manager.prepare(['M6'])
        self.state(state,status='running')
        lock=self.storage/'queue.lock'
        write_json(lock,process_identity())
        original=lock.read_bytes()
        with patch('modules.motor_workbench.active_processes',return_value=[]):
            self.assertEqual(self.manager.recover_queue()['status'],'busy')
        self.assertEqual(lock.read_bytes(),original)
        self.assertEqual(self.manager.state(state['id'])['status'],'running')
        # A dead controller still cannot reclaim a live solver's job.
        identity=process_identity()
        write_json(lock,dict(identity,create_time=identity['create_time']-1000))
        with patch('modules.motor_workbench.active_processes',return_value=[{'status':'alive','record':'worker_process'}]):
            self.assertEqual(self.manager.recover_queue()['status'],'busy')
        self.assertEqual(self.manager.state(state['id'])['status'],'running')

    def test_dead_owner_preserved_and_only_unattempted_materials_get_new_copy(self):
        state=self.manager.prepare(['M6','GO2'])
        state['cases'][0]['status']='running'
        self.state(state,status='running')
        folder=self.manager.path(state['id'])/'case_01/motor.aedtresults'
        folder.mkdir()
        (folder/'old_table.txt').write_bytes(b'original incomplete native evidence')
        identity=process_identity()
        lock=self.storage/'queue.lock'
        write_json(lock,dict(identity,create_time=identity['create_time']-1000))
        old_lock=lock.read_bytes()
        with patch('modules.motor_workbench.active_processes',return_value=[]):
            recovery=self.manager.recover_queue()
            self.assertEqual(recovery['status'],'recovered')
            saved=json.loads((self.storage/'queue_history'/(recovery['record']+'.json')).read_text())
            self.assertEqual(saved['jobs'][0]['previous_state']['status'],'running')
            child=self.manager.continue_unattempted(state['id'])
            self.assertEqual([c['material'] for c in child['cases']],['GO2'])
            self.assertEqual(child['status'],'prepared')
            self.assertEqual(child['parent_job'],state['id'])
            self.assertFalse((self.manager.path(child['id'])/'case_01/motor.aedtresults').exists())
            parent=self.manager.state(state['id'])
            parent.pop('continuation_job') # Crash after child manifest: still no duplicate.
            write_json(self.manager.path(state['id'])/'state.json',parent)
            with self.assertRaisesRegex(ValueError,'已生成'):
                self.manager.continue_unattempted(state['id'])
        self.assertIn(old_lock,[p.read_bytes() for p in (self.storage/'queue_history').glob('owner_*.json')])
        self.assertEqual((folder/'old_table.txt').read_bytes(),b'original incomplete native evidence')

    def test_changed_frozen_source_blocks_queued_execution(self):
        state=self.manager.prepare(['M6'])
        self.state(state,status='queued',queued_utc='1')
        manifest=self.manager.path(state['id'])/'manifest.json'
        value=json.loads(manifest.read_text())
        value['worker_sha256']='0'*64
        write_json(manifest,value)
        with patch('modules.motor_workbench.active_processes',return_value=[]),patch('modules.motor_workbench.subprocess.Popen') as worker:
            self.manager._drain()
            worker.assert_not_called()
        self.assertEqual(self.manager.state(state['id'])['status'],'needs_attention')
        self.assertFalse((self.manager.path(state['id'])/'worker.log').exists())

    def test_fifo_resume_runs_each_untouched_job_once_and_retires_owner(self):
        one=self.manager.prepare(['M6'])
        two=self.manager.prepare(['GO2'])
        self.state(one,status='queued',queued_utc='2')
        self.state(two,status='queued',queued_utc='1')
        seen=[]
        def worker(command,**kwargs):
            folder=kwargs['cwd']
            seen.append(folder.name)
            state=json.loads((folder/'state.json').read_text())
            self.state(state,status='completed')
            return Mock(pid=99999999,wait=Mock(return_value=0))
        with patch('modules.motor_workbench.active_processes',return_value=[]),patch('modules.motor_workbench.subprocess.Popen',side_effect=worker):
            self.manager._drain()
        self.assertEqual(seen,[two['id'],one['id']])
        self.assertFalse((self.storage/'queue.lock').exists())
        self.assertTrue((self.storage/'queue.guard').exists())
        self.assertEqual(len(list((self.storage/'queue_history').glob('owner_*.json'))),1)

    def test_monitor_waits_for_orphan_then_checks_queue_again(self):
        state=self.manager.prepare(['M6'])
        self.state(state,status='queued',queued_utc='1')
        self.manager.monitor_started=True
        with patch.object(self.manager,'recover_queue',side_effect=[{'status':'busy'},{'status':'recovered'}]) as recover,patch.object(self.manager.monitor_stop,'wait',side_effect=[False,True]):
            self.manager._watch_queue()
        self.assertEqual(recover.call_count,2)
        self.assertFalse(self.manager.monitor_started)

    def test_summary_export_includes_official_reports_and_rejects_changed_source(self):
        state=self.manager.prepare(['M6'],measurement='v8_2_cycle1')
        folder=self.manager.path(state['id'])/'case_01'
        csv_fixture(folder)
        result=collect_metrics(folder)
        result.update(case_id='case_01',material='M6',solve_successful=True)
        write_json(folder/'result.json',result)
        self.state(state,status='completed')
        original=(folder/'result.json').read_bytes()
        exported=self.manager.export_summary(state['id'])
        output=self.manager.path(state['id'])/'exports'/exported['id']
        with zipfile.ZipFile(output/'reports_bundle.zip') as bundle:
            self.assertIn('case_01/reports/Torque Plots.csv',bundle.namelist())
            self.assertFalse(any(n.endswith('.aedt') or '.aedtresults' in n for n in bundle.namelist()))
            self.assertEqual(bundle.read('case_01/result.json'),original)
        with (output/'summary.csv').open(encoding='utf-8-sig') as stream:
            row=next(csv.DictReader(stream))
        self.assertEqual(row['acceptance_reason'],'通过')
        self.assertEqual(row['measurement_protocol'],'v8_2_cycle1')
        self.assertEqual((folder/'result.json').read_bytes(),original)
        with (folder/'maxwell_reports/CoreLoss.csv').open('a') as stream:
            stream.write('changed source')
        with self.assertRaisesRegex(ValueError,'哈希改变'):
            self.manager.export_summary(state['id'])

    def test_periodic_rejection_reason_uses_frozen_threshold(self):
        result=dict(accepted=False,T_min_Nm=1,T_max_Nm=2,K_T_ripple_pct=25,P_Fe_W=25,P_Cu_W=50,
            periodic_stability=dict(passed=False,max_phase_torque_delta_Nm=.245182,
                relative_mean_torque_change=.001,relative_core_loss_change=.04,
                thresholds=dict(phase_torque_Nm=.1,mean_torque_relative=.02,core_loss_relative=.05)))
        self.assertIn('0.245182 N·m > 0.1',acceptance_reason(result))
        self.assertNotIn('铁损周期差',acceptance_reason(result))

    def test_legacy_cycle1_alias_requires_missing_manifest_protocol_and_same_csv_metrics(self):
        state=self.manager.prepare(['M6'],measurement='v8_2_cycle1')
        folder=self.manager.path(state['id'])/'case_01'
        csv_fixture(folder)
        result=collect_metrics(folder)
        result.update(case_id='case_01',material='M6',metric_protocol='v8_2_torque_all_core_drop_first_copper_all_v1')
        write_json(folder/'result.json',result)
        self.state(state,status='completed')
        with self.assertRaisesRegex(ValueError,'测量口径不同'):
            self.manager.export_summary(state['id'])
        manifest=self.manager.path(state['id'])/'manifest.json'
        value=json.loads(manifest.read_text())
        value.pop('measurement_protocol')
        write_json(manifest,value)
        exported=self.manager.export_summary(state['id'])
        provenance=json.loads((self.manager.path(state['id'])/'exports'/exported['id']/'provenance.json').read_text())
        self.assertEqual(provenance['verified_legacy_protocol_aliases']['case_01']['canonical'],'v8_2_cycle1')


if __name__=='__main__':
    unittest.main()
