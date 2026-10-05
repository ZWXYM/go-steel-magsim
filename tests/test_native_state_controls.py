import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from modules.material_calibration import MU0, extract_loop_midpoint, file_hash
from modules.native_loop_diagnostics import MSAT, VOLUME
from modules.native_state_controls import audit_control
from tools.run_calibration_pilot import H_GRID
from tools.run_initial_state_controls import prepare, execute, analyze, snapshot

ROOT = PROJECT.parent if PROJECT.name == 'magsim' else PROJECT
PREPARED = ROOT / ('calibration/diagnostics/initial_state_cpu_20261005'
    if PROJECT.name == 'magsim' else 'calibration/initial_state_cpu_20261005')


def fixture(path, condition, angle=0., torque=1e-7):
    direction = np.array([np.cos(np.deg2rad(angle)), np.sin(np.deg2rad(angle)), 0.])
    signed = np.r_[-H_GRID[:0:-1], H_GRID]
    schedules = ((-1, signed[::-1]), (1, signed)) if condition == 'strict_major_loop' else ((-2, H_GRID), (2, H_GRID))
    rows = []
    for phase, schedule in schedules:
        for h in schedule:
            m = .99*np.sign(h) if h else -.25*phase
            rows.append([0., *(m*direction), *(MU0*h*direction), 80*VOLUME, phase, torque])
    header = 't (s)\tmx ()\tmy ()\tmz ()\tB_extx (T)\tB_exty (T)\tB_extz (T)\tE_total (J)\tstate_phase ()\tmaxTorque (T)'
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savetxt(path, rows, delimiter='\t', header=header, comments='')


class NativeStateControls(unittest.TestCase):
    def test_strict_signed_schedule_agrees_with_legacy_extractor(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'table.txt'
            fixture(path, 'strict_major_loop', angle=90.)
            before = file_hash(path)
            result = audit_control(path, H_GRID, angle_deg=90., condition='strict_major_loop')
            derived = Path(tmp) / 'branch_table.txt'
            derived.write_text(path.read_text().replace('state_phase ()', 'branch ()'))
            old = extract_loop_midpoint(derived, H_GRID, angle_deg=90.)
            np.testing.assert_allclose(result['guarded_midpoint_B_T'], old['B'], rtol=0, atol=1e-12)
            self.assertAlmostEqual(result['states'][0]['native_H0_T'], .25*MU0*MSAT)
            self.assertAlmostEqual(result['states'][0]['max_energy_density_J_per_m3'], 80.)
            self.assertTrue(result['numerical_screen_passed'])
            self.assertFalse(result['strict_training_eligible'])
            self.assertEqual(before, file_hash(path))

    def test_transverse_pair_preserves_two_zero_states_and_has_no_major_loop_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'table.txt'
            fixture(path, 'zero_transverse_pair', angle=35.)
            result = audit_control(path, H_GRID, angle_deg=35., condition='zero_transverse_pair')
            self.assertEqual(result['rows'], 52)
            self.assertAlmostEqual(result['max_state_separation_T'], MU0*MSAT)
            self.assertAlmostEqual(result['states'][0]['native_H0_T'], .5*MU0*MSAT)
            self.assertFalse(result['initializations_are_certified_demagnetization'])
            self.assertNotIn('numerical_screen_passed', result)
            with self.assertRaisesRegex(ValueError, 'state-phase'):
                audit_control(path, H_GRID, angle_deg=35., condition='strict_major_loop')

    def test_rejects_direction_missing_torque_and_incomplete_state_schedule(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'table.txt'
            fixture(path, 'zero_transverse_pair')
            with self.assertRaisesRegex(ValueError, 'direction'):
                audit_control(path, H_GRID, angle_deg=90., condition='zero_transverse_pair')
            lines = path.read_text().splitlines()
            path.write_text('\n'.join(lines[:-1]))
            with self.assertRaisesRegex(ValueError, 'state-phase'):
                audit_control(path, H_GRID, angle_deg=0., condition='zero_transverse_pair')
            fixture(path, 'zero_transverse_pair')
            path.write_text('\n'.join('\t'.join(line.split('\t')[:-1]) for line in path.read_text().splitlines()))
            with self.assertRaisesRegex(ValueError, 'Missing column maxTorque'):
                audit_control(path, H_GRID, angle_deg=0., condition='zero_transverse_pair')

    def test_residual_torque_failure_is_not_hidden_by_good_endpoint_and_inversion(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'table.txt'
            fixture(path, 'strict_major_loop', torque=2e-4)
            result = audit_control(path, H_GRID, angle_deg=0., condition='strict_major_loop')
            self.assertTrue(result['endpoint_gate_passed'])
            self.assertTrue(result['inversion_gate_passed'])
            self.assertFalse(result['torque_gate_passed'])
            self.assertFalse(result['numerical_screen_passed'])

    def test_prepare_is_CPU_only_freezes_four_scripts_and_keeps_prior_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / 'fake_binary.exe'
            binary.write_bytes(b'Unit-test fixture, never execute')
            run = Path(tmp) / 'study'
            before = snapshot(PREPARED)
            with patch('subprocess.run', side_effect=AssertionError('Preparation launched native')):
                manifest = prepare(ROOT, PREPARED, run, mumax=binary)
            self.assertEqual(len(manifest['jobs']), 4)
            self.assertEqual(before, snapshot(PREPARED))
            for job in manifest['jobs']:
                self.assertEqual(job['grain_id'], 1)
                self.assertEqual(file_hash(run/job['script']), job['script_sha256'])
            self.assertFalse((run/'run_status.json').exists())
            with self.assertRaisesRegex(ValueError, 'new output'):
                prepare(ROOT, PREPARED, run, mumax=binary)
            with self.assertRaisesRegex(ValueError, 'Timeout'):
                prepare(ROOT, PREPARED, Path(tmp)/'oversize', mumax=binary, timeout=121)

    def test_bounded_timeout_preserves_partial_table_stops_budget_and_cannot_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / 'fake_binary.exe'
            binary.write_bytes(b'Unit-test fixture, never execute')
            run = Path(tmp) / 'study'
            manifest = prepare(ROOT, PREPARED, run, mumax=binary)
            def timeout(args, **kwargs):
                table = Path(args[args.index('-o')+1]) / 'table.txt'
                table.parent.mkdir(parents=True)
                table.write_text('partial diagnostic, not a completed table')
                raise subprocess.TimeoutExpired(args, kwargs['timeout'])
            with patch('subprocess.run', side_effect=timeout) as native:
                status = execute(run, manifest, mumax=binary)
                self.assertEqual(native.call_count, 1)
            self.assertFalse((run/'runner.lock').exists())
            self.assertEqual(status['jobs'][0]['exit_code'], -124)
            self.assertTrue((run/manifest['jobs'][0]['output']/'table.txt').exists())
            result = analyze(run)
            self.assertEqual(len(result['unattempted_jobs']), 3)
            self.assertEqual(result['complete_controls'], 0)
            with patch('subprocess.run', side_effect=AssertionError('Old output rerun')):
                with self.assertRaisesRegex(ValueError, 'Never retry'):
                    execute(run, manifest, mumax=binary)
            self.assertFalse((run/'runner.lock').exists())

    def test_native_runtime_analysis_reverifies_raw_table_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / 'fake_binary.exe'
            binary.write_bytes(b'Unit-test fixture, never execute')
            run = Path(tmp) / 'study'
            manifest = prepare(ROOT, PREPARED, run, mumax=binary)
            def synthetic_native(args, **kwargs):
                output = Path(args[args.index('-o')+1])
                job = next(j for j in manifest['jobs'] if run/j['output'] == output)
                fixture(output/'table.txt', job['condition'], job['angle'])
                return subprocess.CompletedProcess(args, 0)
            with patch('subprocess.run', side_effect=synthetic_native):
                status = execute(run, manifest, mumax=binary)
            self.assertEqual(len(status['jobs']), 4)
            result = analyze(run)
            self.assertEqual(result['complete_controls'], 4)
            table = run/manifest['jobs'][0]['output']/'table.txt'
            table.write_text(table.read_text()+'\n')
            with self.assertRaisesRegex(ValueError, 'table changed'):
                analyze(run)


if __name__ == '__main__':
    unittest.main()
