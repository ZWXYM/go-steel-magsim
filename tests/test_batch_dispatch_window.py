"""Short submission lock and persistent batch ownership; no native programs."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import test_calibrated_motor_preparation as fixtures
from modules.motor_queue import DispatchWindow, QueueLease, process_identity
from modules.motor_workbench import write_json


class BatchDispatchWindowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)

    def tearDown(self): self.temp.cleanup()

    def test_unreserved_submissions_use_short_os_lease(self):
        with DispatchWindow(self.root):
            other = QueueLease(self.root/'native_dispatch.guard')
            self.assertFalse(other.acquire())
        with DispatchWindow(self.root): pass

    def test_only_exact_alive_controller_can_dispatch(self):
        window = DispatchWindow(self.root)
        write_json(window.reservation,dict(batch_id='bhbatch_0123456789ab',identity=process_identity()))
        with window: pass
        record = json.loads(window.reservation.read_text()); record['identity']['pid'] = 987654
        write_json(window.reservation,record)
        for status in ('alive','dead','unknown'):
            with patch('modules.motor_queue.identity_status',return_value=status):
                with self.assertRaisesRegex(ValueError,'批量任务占用'):
                    with DispatchWindow(self.root): pass

    def test_dead_controller_requires_explicit_matching_batch_recovery(self):
        window = DispatchWindow(self.root)
        write_json(window.reservation,dict(batch_id='bhbatch_0123456789ab',identity=dict(pid=987654,create_time=0)))
        with patch('modules.motor_queue.identity_status',return_value='dead'):
            with DispatchWindow(self.root,recover_batch='bhbatch_0123456789ab'): pass
            with self.assertRaises(ValueError):
                with DispatchWindow(self.root,recover_batch='another'): pass

    def test_bad_reservation_does_not_default_to_unreserved(self):
        window = DispatchWindow(self.root); window.reservation.parent.mkdir(parents=True)
        window.reservation.write_text('{bad json')
        with self.assertRaises(ValueError):
            with window: pass
        window.reservation.write_text(json.dumps(dict(identity=dict(pid=987654,create_time=0))))
        with patch('modules.motor_queue.identity_status',return_value='dead'):
            with self.assertRaises(ValueError):
                with window: pass


if __name__=='__main__': unittest.main()
