"""CPU-only process handoff checks; never import AEDT or start a solve."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import test_calibrated_motor_analysis as fixtures
from modules.motor_queue import process_identity
from modules.motor_workbench import write_json
from tools import calibrated_motor_analysis_worker as worker


class LauncherHandoffTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.folder = self.root / 'execution'; self.folder.mkdir()
        self.motor = self.root / 'motor'; self.motor.mkdir()
        self.state = dict(id='bhexec_0123456789ab', queue_token='test-token')

    def tearDown(self):
        self.temp.cleanup()

    def owner(self, **changes):
        record = dict(process_identity(), token=self.state['queue_token'], torque_execution_id=self.state['id'])
        record.update(changes); write_json(self.motor / 'queue.lock', record)
        return record

    def test_current_worker_claim_preserves_identity_and_token(self):
        owner = self.owner()
        worker.claim_queue(self.folder, self.motor, self.state)
        self.assertEqual(worker.read(self.motor / 'queue.lock'), owner)
        record = worker.read(self.folder / 'worker_identity.json')
        self.assertEqual(record['identity'], process_identity())
        self.assertTrue(record['launcher_is_self_or_verified_direct_parent'])

    def test_wrong_token_artifact_or_unknown_identity_does_not_claim(self):
        for changes in ({'token': 'wrong'}, {'torque_execution_id': 'other'}, {'create_time': None}):
            self.owner(**changes); before = (self.motor / 'queue.lock').read_bytes()
            with self.assertRaisesRegex(ValueError, '队列归属'):
                worker.claim_queue(self.folder, self.motor, self.state)
            self.assertEqual((self.motor / 'queue.lock').read_bytes(), before)
            self.assertFalse((self.folder / 'worker_identity.json').exists())

    def test_unrelated_live_pid_is_not_accepted_as_launcher(self):
        self.owner(pid=987654)
        before = (self.motor / 'queue.lock').read_bytes()
        with patch.object(worker, 'identity_status', return_value='alive'):
            with self.assertRaisesRegex(ValueError, '父进程'):
                worker.claim_queue(self.folder, self.motor, self.state)
        self.assertEqual((self.motor / 'queue.lock').read_bytes(), before)

    def test_real_python_subprocess_can_claim_only_its_own_launch_record(self):
        # On Windows sys.executable invokes the venv redirector; on other
        # platforms the direct process branch is exercised instead.
        code = """import sys,time,json,os
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from tools.calibrated_motor_analysis_worker import claim_queue,read
folder,motor=Path(sys.argv[2]),Path(sys.argv[3])
deadline=time.monotonic()+8
while not (motor/'queue.lock').exists():
 if time.monotonic()>deadline:raise RuntimeError('test handoff missing')
 time.sleep(.02)
claim_queue(folder,motor,dict(id='bhexec_0123456789ab',queue_token='test-token'))
print(json.dumps(dict(pid=os.getpid(),owner=read(motor/'queue.lock'))))
"""
        child = subprocess.Popen([sys.executable, '-X', 'utf8', '-c', code,
            str(Path(__file__).resolve().parents[1]), str(self.folder), str(self.motor)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        owner = dict(process_identity(child.pid), token='test-token', torque_execution_id=self.state['id'])
        write_json(self.motor / 'queue.lock', owner)
        output, errors = child.communicate(timeout=15)
        self.assertEqual(child.returncode, 0, errors)
        result = json.loads(output)
        self.assertEqual(result['pid'], result['owner']['pid'])
        self.assertEqual(result['owner']['token'], 'test-token')
        self.assertEqual(worker.read(self.folder / 'worker_identity.json')['launcher_identity']['pid'], child.pid)


if __name__ == '__main__':
    unittest.main()
