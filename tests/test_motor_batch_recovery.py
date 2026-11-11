import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from modules.motor_batch_recovery import JOURNAL, settle_interruption
from modules.motor_workbench import write_json


class MotorBatchRecoveryTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.storage = Path(scratch.name).resolve()
        batches = self.storage/'calibrated_motor_batches'
        executions = self.storage/'calibrated_motor_execution'
        self.batch = batches/'bhbatch_aaaaaaaaaaaa'
        self.execution = executions/'bhexec_bbbbbbbbbbbb'
        motor = self.storage/'motor'
        self.reservation = batches/'active_controller.json'
        self.queue = motor/'queue.lock'
        self.desktop = dict(pid=4005, create_time=1005.)
        controller = dict(batch_id=self.batch.name, token='batch-token', identity=dict(pid=4001,create_time=1001.),
                          launcher_identity=dict(pid=4002,create_time=1002.))
        launcher, worker = dict(pid=4003,create_time=1003.), dict(pid=4004,create_time=1004.)
        write_json(self.batch/'controller.json', controller)
        write_json(self.reservation, controller)
        write_json(self.queue, dict(worker,token='queue-token',torque_execution_id=self.execution.name))
        write_json(self.batch/'manifest.json', dict(id=self.batch.name))
        write_json(self.execution/'manifest.json', dict(id=self.execution.name))
        write_json(self.batch/'state.json', dict(id=self.batch.name,status='running',controller_token=controller['token'],
            current_item='item_02',new_execution_ids=[self.execution.name],items=[
            dict(id='item_01',status='reused_result',records={}),
            dict(id='item_02',status='analyzing',records={'execution':{'id':self.execution.name}})]))
        write_json(self.execution/'state.json',dict(id=self.execution.name,status='needs_attention',phase='failed',
            queue_token='queue-token',error='Native session lost',new_Maxwell_solves=0))
        write_json(self.execution/'dispatch.json',dict(identity=launcher))
        write_json(self.execution/'worker_identity.json',dict(identity=worker,launcher_identity=launcher,
            launcher_is_self_or_verified_direct_parent=True))
        write_json(self.execution/'desktop_session.json',dict(identity=self.desktop,dedicated_session=True,
            closed=False,native_PID_verified=True))
        self.partial = self.execution/'case/motor.aedt.lock'
        self.partial.parent.mkdir()
        self.partial.write_bytes(b'Original native lock in failed copy')
        self.manager = SimpleNamespace(storage=batches,path=lambda name:batches/name,
            get=lambda _: self.batch_status(),chain=SimpleNamespace(
                preparation=SimpleNamespace(motor=SimpleNamespace(storage=motor)),
                execution=SimpleNamespace(storage=executions,path=lambda name:executions/name),
                native=SimpleNamespace(storage=self.storage/'calibrated_motor_native')))
        self.addCleanup(patch.stopall)
        patch('modules.motor_queue.identity_status',return_value='dead').start()
        patch('subprocess.Popen',side_effect=AssertionError('Recovery must not launch')).start()

    def batch_status(self):
        record = json.loads((self.batch/'state.json').read_bytes())
        if record['status']=='running':record['status']='needs_attention'
        return record

    def test_live_or_unknown_desktop_preserves_state_and_owners(self):
        for status in ('alive','unknown'):
            with self.subTest(status=status), patch('modules.motor_batch_recovery.identity_status',
                    side_effect=lambda identity: status if identity==self.desktop else 'dead'):
                with self.assertRaisesRegex(ValueError,'活动或身份未确认'):
                    settle_interruption(self.manager,self.batch.name)
                self.assertTrue(self.queue.exists())
                self.assertTrue(self.reservation.exists())
                self.assertFalse((self.batch/JOURNAL).exists())

    def test_dead_owners_are_archived_and_repeated_request_is_idempotent(self):
        queue, batch = self.queue.read_bytes(), self.reservation.read_bytes()
        original = (self.execution/'state.json').read_bytes()
        with patch('modules.motor_batch_recovery.identity_status',return_value='dead'):
            result=settle_interruption(self.manager,self.batch.name)
            self.assertEqual(settle_interruption(self.manager,self.batch.name),result)
        self.assertEqual(result['status'],'completed_with_failures')
        self.assertEqual([item['status'] for item in result['items']],['reused_result','failed'])
        self.assertFalse(self.queue.exists())
        self.assertFalse(self.reservation.exists())
        self.assertEqual((self.batch/'interrupted_queue_owner.json').read_bytes(),queue)
        self.assertEqual((self.batch/'interrupted_batch_owner.json').read_bytes(),batch)
        receipt=json.loads((self.batch/JOURNAL).read_bytes())
        self.assertEqual(receipt['original_execution_state'].encode(),original)
        self.assertEqual(receipt['native_commands'],0)
        self.assertEqual(self.partial.read_bytes(),b'Original native lock in failed copy')


if __name__=='__main__':
    unittest.main()
