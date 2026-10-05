import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT))
from tools.run_torque_stop_probe import prepare,execute,analyze,telemetry,CASES
from tools.run_initial_state_controls import snapshot
from tools.run_calibration_pilot import H_GRID
from modules.material_calibration import file_hash
from modules.native_state_controls import audit_control
from test_native_state_controls import fixture

ROOT=PROJECT.parent if PROJECT.name=='magsim' else PROJECT


class TorqueStopProbe(unittest.TestCase):
    def prepared(self,tmp):
        binary=Path(tmp)/'fake.exe'
        binary.write_bytes(b'Never execute test binary')
        run=Path(tmp)/'probe'
        with patch('tools.run_torque_stop_probe.file_hash',side_effect=lambda p:
                json.loads((ROOT/'calibration/pilot_20261003_n8/run_status.json').read_text())['mumax_binary_sha256']
                if Path(p)==binary else file_hash(p)):
            manifest=prepare(ROOT,run,mumax=binary)
        # Synthetic execution tests use their own explicit binary identity.
        manifest['mumax_binary_sha256']=file_hash(binary)
        (run/'manifest.json').write_text(json.dumps(manifest,indent=2))
        return run,binary,manifest

    def test_prepare_selects_all_four_predeclared_cases_without_native_or_source_mutation(self):
        before=snapshot(ROOT/'calibration/pilot_20261003_n8')
        with tempfile.TemporaryDirectory() as tmp, patch('subprocess.run',side_effect=AssertionError('CPU preparation launched native')):
            run,binary,manifest=self.prepared(tmp)
            self.assertEqual(len(manifest['jobs']),8)
            self.assertEqual({(j['grade'],j['direction'],j['grain_id']) for j in manifest['jobs']},set(CASES))
            self.assertEqual([j['condition'] for j in manifest['jobs']],['strict_dm_1e7']*4+['tight_dm_1e8']*4)
            self.assertEqual(manifest['timeout_seconds_per_job'],60)
            self.assertEqual(before,snapshot(ROOT/'calibration/pilot_20261003_n8'))
            for j in manifest['jobs']:
                source=(run/'source_cases'/j['case_id']/'original.mx3').read_text()
                script=(run/j['script']).read_text()
                prefix=source.split('// Pre-saturation: Start from saturated state')[0]
                self.assertTrue(script.startswith(prefix))
                self.assertIn('tableadd(LastErr)',script)
                self.assertNotIn('relax()',script)

    def test_dM_stop_pass_does_not_imply_torque_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            table=Path(tmp)/'table.txt'
            fixture(table,'strict_major_loop',torque=4e-5)
            lines=table.read_text().splitlines()
            table.write_text(lines[0]+'\tLastErr ()\n'+'\n'.join(s+'\t5e-8' for s in lines[1:])+'\n')
            self.assertTrue(telemetry(table,1e-7)['all_recorded_dM_below_stop'])
            self.assertFalse(audit_control(table,H_GRID,angle_deg=0,condition='strict_major_loop')['torque_gate_passed'])
            self.assertFalse(telemetry(table,1e-8)['all_recorded_dM_below_stop'])

    def test_timeout_keeps_partial_output_and_never_runs_remaining_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            run,binary,manifest=self.prepared(tmp)
            with patch('subprocess.run',side_effect=subprocess.TimeoutExpired('owned-child',60)) as native:
                status=execute(run,manifest,mumax=binary)
            self.assertEqual(native.call_count,1)
            self.assertEqual(status['jobs'][0]['status'],'timed_out')
            self.assertEqual(len(analyze(run)['unattempted_jobs']),7)
            self.assertFalse((run/'runner.lock').exists())
            with self.assertRaisesRegex(ValueError,'Never retry'):
                execute(run,manifest,mumax=binary)

    def test_changed_frozen_script_is_rejected_before_native_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            run,binary,manifest=self.prepared(tmp)
            script=run/manifest['jobs'][0]['script']
            script.write_text(script.read_text()+'\n')
            with patch('subprocess.run',side_effect=AssertionError('Changed script executed')):
                with self.assertRaisesRegex(ValueError,'Frozen script'):
                    execute(run,manifest,mumax=binary)


if __name__=='__main__':
    unittest.main()
