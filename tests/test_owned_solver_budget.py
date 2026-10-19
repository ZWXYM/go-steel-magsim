"""Owned-session stop safety; these tests never launch AEDT or a solver."""
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock,patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools import calibrated_motor_analysis_worker as worker
from modules.calibrated_motor_execution import BUDGET_STOP_POLICY
from modules.motor_workbench import write_json


class OwnedBudgetTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.folder=self.root/'calibrated_motor_execution/bhexec_aaaaaaaaaaaa';self.folder.mkdir(parents=True)
        (self.folder/'case').mkdir();(self.root/'motor').mkdir();self.identity=dict(pid=123,create_time=456.0)
        self.manifest=dict(id=self.folder.name,aedt_version='2025.1',budget_stop_policy=BUDGET_STOP_POLICY)
        self.state=dict(id=self.folder.name,phase='solving',queue_token='fixture-token')
        self.deadline=time.monotonic()-1
        self.watch=dict(execution_id=self.folder.name,queue_token='fixture-token',policy=BUDGET_STOP_POLICY,
            deadline_monotonic=self.deadline,start_monotonic=self.deadline-1200)
        write_json(self.folder/'manifest.json',self.manifest);write_json(self.folder/'state.json',self.state)
        write_json(self.folder/'budget_watch.json',self.watch)
        write_json(self.folder/'desktop_session.json',dict(dedicated_session=True,closed=False,identity=self.identity))
        write_json(self.root/'motor/queue.lock',dict(torque_execution_id=self.folder.name,token='fixture-token'))
        self.desktop=MagicMock();self.desktop.aedt_process_id=123;self.desktop.odesktop.GetProjectList.return_value=['motor']
        self.desktop.odesktop.GetActiveProject.return_value.GetPath.return_value=str(self.folder/'case')
        self.desktop.stop_simulations.return_value='stopped';self.factory=MagicMock(return_value=self.desktop)

    def call(self):
        with patch.object(worker,'identity_status',return_value='alive'):
            return worker.stop_owned_at_budget(self.folder,self.factory)

    def test_exact_owned_scope_stops_once_and_never_closes_or_kills(self):
        r=self.call();self.assertEqual(r['native_return'],'stopped');self.assertTrue(r['stop_attempted'])
        self.assertFalse(r['close_or_kill_requested']);self.desktop.stop_simulations.assert_called_once_with(clean_stop=True)
        self.desktop.release_desktop.assert_not_called();self.desktop.close_desktop.assert_not_called()
        self.assertFalse(self.factory.call_args.kwargs['new_desktop']);self.assertFalse(self.factory.call_args.kwargs['close_on_exit'])
        self.call();self.desktop.stop_simulations.assert_called_once();self.factory.assert_called_once()

    def test_native_false_return_is_not_recorded_as_confirmed_stop(self):
        self.desktop.stop_simulations.return_value=False
        with self.assertRaisesRegex(RuntimeError,'未确认'):self.call()
        data=json.loads((self.folder/'budget_stop.json').read_text())
        self.assertEqual(data['status'],'stop_request_unconfirmed');self.assertTrue(data['stop_attempted'])
        self.desktop.close_desktop.assert_not_called()

    def test_budget_not_elapsed_cannot_connect_or_stop(self):
        self.watch.update(deadline_monotonic=time.monotonic()+100,start_monotonic=time.monotonic()+100-1200)
        # Use the exact same deadline-derived start to avoid a test clock rounding gap.
        self.watch['start_monotonic']=self.watch['deadline_monotonic']-1200;write_json(self.folder/'budget_watch.json',self.watch)
        with self.assertRaisesRegex(ValueError,'尚未到达'):self.call()
        self.factory.assert_not_called()

    def test_unknown_identity_does_not_connect(self):
        with patch.object(worker,'identity_status',return_value='unknown'):
            with self.assertRaisesRegex(ValueError,'身份无法确认'):worker.stop_owned_at_budget(self.folder,self.factory)
        self.factory.assert_not_called()

    def test_wrong_queue_token_does_not_connect(self):
        write_json(self.root/'motor/queue.lock',dict(torque_execution_id=self.folder.name,token='another'))
        with self.assertRaisesRegex(ValueError,'归属不符'):self.call()
        self.factory.assert_not_called()

    def test_other_project_or_wrong_path_is_never_stopped(self):
        self.desktop.odesktop.GetProjectList.return_value=['motor','user_project']
        with self.assertRaisesRegex(ValueError,'非本案工程'):self.call()
        self.desktop.odesktop.GetProjectList.return_value=['motor']
        self.desktop.odesktop.GetActiveProject.return_value.GetPath.return_value=str(self.root/'user_project')
        with self.assertRaisesRegex(ValueError,'路径不是本次'):self.call()
        self.desktop.stop_simulations.assert_not_called();self.desktop.close_desktop.assert_not_called()

    def test_wrong_attached_pid_is_never_stopped(self):
        self.desktop.aedt_process_id=999
        with self.assertRaisesRegex(ValueError,'不是原专用会话'):self.call()
        self.desktop.stop_simulations.assert_not_called();self.desktop.release_desktop.assert_not_called()

    def test_completed_or_closed_job_never_connects(self):
        write_json(self.folder/'result.json',{})
        self.assertEqual(self.call()['status'],'already_finished');self.factory.assert_not_called()
        (self.folder/'result.json').rename(self.folder/'kept_result.json')
        write_json(self.folder/'desktop_session.json',dict(dedicated_session=True,closed=True,identity=self.identity))
        self.assertEqual(self.call()['status'],'already_closed');self.factory.assert_not_called()

    def test_expired_watcher_requests_once_and_preserves_an_existing_stop_intent(self):
        def stopped(folder):
            record=dict(execution_id=self.folder.name,identity=self.identity,stop_attempted=True,status='clean_stop_requested')
            write_json(folder/'budget_stop.json',record);return record
        with patch.object(worker,'verify_inputs',return_value=self.manifest),\
             patch.object(worker,'stop_owned_at_budget',side_effect=stopped) as stop:
            self.assertEqual(worker.watch_budget(self.folder),0);self.assertEqual(worker.watch_budget(self.folder),0)
            stop.assert_called_once();self.assertTrue((self.folder/'attention.json').exists())

    def test_finished_watcher_exits_without_stop_or_attention(self):
        self.watch['deadline_monotonic']=time.monotonic()+100;write_json(self.folder/'budget_watch.json',self.watch)
        self.state['phase']='finished';write_json(self.folder/'state.json',self.state)
        with patch.object(worker,'verify_inputs',return_value=self.manifest),patch.object(worker,'stop_owned_at_budget') as stop:
            self.assertEqual(worker.watch_budget(self.folder),0);stop.assert_not_called()
        self.assertFalse((self.folder/'attention.json').exists())

    def test_watcher_failure_is_visible_and_does_not_claim_stop_confirmation(self):
        with patch.object(worker,'verify_inputs',return_value=self.manifest),\
             patch.object(worker,'stop_owned_at_budget',side_effect=RuntimeError('fixture RPC unavailable')):
            self.assertEqual(worker.watch_budget(self.folder),1)
        failure=json.loads((self.folder/'budget_watch_failure.json').read_text())
        self.assertTrue(failure['stop_not_confirmed']);self.assertIn('RPC unavailable',failure['error'])
        self.assertFalse((self.folder/'budget_stop.json').exists())

    def test_watcher_launch_is_an_independent_hidden_cpu_child(self):
        (self.folder/'budget_watch.json').rename(self.folder/'initial_watch.kept')
        def started(*args,**kwargs):
            write_json(self.folder/'budget_watch_ready.json',dict(execution_id=self.folder.name,
                queue_token='fixture-token',identity=self.identity))
            return SimpleNamespace(pid=999,poll=lambda:None)
        with patch.object(worker.subprocess,'Popen',side_effect=started) as launch,\
             patch.object(worker,'process_identity',return_value=self.identity),patch.object(worker,'identity_status',return_value='alive'):
            worker.launch_budget_watch(self.folder,time.monotonic()+100)
        command=launch.call_args.args[0];self.assertIn('--watch-budget',command)
        self.assertEqual(launch.call_args.kwargs['cwd'],self.folder)
        data=json.loads((self.folder/'budget_watch.json').read_text());self.assertEqual(data['policy'],BUDGET_STOP_POLICY)
        self.assertAlmostEqual(data['deadline_monotonic']-data['start_monotonic'],1200)


if __name__=='__main__':unittest.main()
