"""Diagnostics must preserve sources and refuse incomplete/native-hash drift."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from modules.material_calibration import MU0, file_hash
from tools.audit_reference_bounds import audit_curve
from tools.analyze_sampling_convergence import read_ensemble, prefix_metrics, analyze
from modules.texture_sampling import SAMPLING_VERSION, LEGACY_SAMPLING_VERSION
from tools.run_calibration_pilot import H_GRID, run_jobs, write_json


def native_fixture(folder):
    binary = folder / 'mumax3.exe'
    binary.write_bytes(b'fake binary, not executed')
    orientation = folder / 'G/orientations.csv'
    orientation.parent.mkdir()
    orientation.write_text('phi1,Phi,phi2\n0,45,0\n0,45,0\n')
    jobs, completed = [], []
    for i in (1, 2):
        script = folder / f'G/TD/grain_{i}.mx3'
        script.parent.mkdir(exist_ok=True)
        script.write_text('// frozen fixture')
        output = folder / f'G/TD/grain_{i}.out'
        output.mkdir()
        table = output / 'table.txt'
        rows = []
        for label, fields in [(-1, [50000, 800, 0, -800, -50000]),
                              (1, [-50000, -800, 0, 800, 50000])]:
            for h in fields:
                rows.append([0, 0, .1 * np.sign(h), 0, 0, MU0*h, 0, label])
        header = '# t (s)\tmx ()\tmy ()\tmz ()\tB_extx (T)\tB_exty (T)\tB_extz (T)\tbranch ()'
        np.savetxt(table, rows, header=header, comments='')
        job = {'grade': 'G', 'direction': 'TD', 'grain_id': i, 'angle': 90,
            'script': script.relative_to(folder).as_posix(), 'script_sha256': file_hash(script),
            'output': output.relative_to(folder).as_posix()}
        jobs.append(job)
        completed.append({**job, 'exit_code': 0, 'table_sha256': file_hash(table)})
    manifest = {'physics_version': 'cubic_sample_frame_v2', 'n_grains': 2,
        'seed': 1, 'H_grid': H_GRID.tolist(), 'jobs': jobs,
        'materials': [{'grade': 'G', 'orientations_sha256': file_hash(orientation)}]}
    write_json(folder / 'manifest.json', manifest)
    write_json(folder / 'run_status.json', {'mumax_binary_sha256': file_hash(binary), 'jobs': completed})
    return manifest, binary


class SamplingDiagnostics(unittest.TestCase):
    def test_reference_B_and_J_are_not_confused(self):
        # B may exceed mu0*Msat solely because of the vacuum-field term.
        H = np.array([0., 800., 50000.])
        B = MU0*H + np.array([0., 1.5, MU0*1.56e6])
        before = B.copy()
        result = audit_curve(H, B)
        self.assertEqual(result['points_exceeding_J_bound'], 0)
        np.testing.assert_array_equal(B, before)

    def test_reference_bound_excess_is_reported_without_repairing_input(self):
        H = np.array([0., 800., 50000.])
        B = MU0*H + np.array([0., 2., 2.])
        before = B.copy()
        result = audit_curve(H, B)
        self.assertEqual(result['points_exceeding_J_bound'], 2)
        self.assertGreater(result['max_J_excess_T'], .03)
        np.testing.assert_array_equal(B, before)

    def test_native_hash_and_complete_ensemble_are_required(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest, _ = native_fixture(root)
            values, metadata = read_ensemble(root, 'G', 'TD')
            self.assertEqual(values.shape, (2, len(H_GRID)))
            self.assertEqual(metadata['n_grains'], 2)
            status = json.loads((root / 'run_status.json').read_text())
            complete = copy.deepcopy(status)
            status['jobs'] = status['jobs'][:1]
            write_json(root / 'run_status.json', status)
            with self.assertRaisesRegex(ValueError, 'incomplete'):
                read_ensemble(root, 'G', 'TD')
            write_json(root / 'run_status.json', complete)
            table = root / manifest['jobs'][0]['output'] / 'table.txt'
            table.write_text(table.read_text() + '\n')
            with self.assertRaisesRegex(ValueError, 'table changed'):
                read_ensemble(root, 'G', 'TD')

    def test_manifest_cannot_relabel_the_script_actually_executed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest, _ = native_fixture(root)
            script = root / manifest['jobs'][0]['script']
            script.write_text('// a different simulation input')
            manifest['jobs'][0]['script_sha256'] = file_hash(script)
            write_json(root / 'manifest.json', manifest)
            with self.assertRaisesRegex(ValueError, 'Executed script hash'):
                read_ensemble(root, 'G', 'TD')

    def test_filtered_resume_does_not_execute_other_jobs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest, binary = native_fixture(root)
            manifest['jobs'].append({'grade': 'OTHER', 'direction': 'RD',
                'script': 'must_not_execute.mx3', 'output': 'must_not_create.out'})
            with patch('tools.run_calibration_pilot.subprocess.run', return_value=SimpleNamespace(stdout='version')) as invoke:
                run_jobs(root, manifest, mumax=binary, only_grade='G', only_direction='TD')
                self.assertEqual(invoke.call_count, 1)  # version query only
            self.assertFalse((root / 'must_not_create.out').exists())

    def test_native_binary_drift_is_rejected_before_resuming(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest, binary = native_fixture(root)
            binary.write_bytes(b'a different executable')
            with patch('tools.run_calibration_pilot.subprocess.run', return_value=SimpleNamespace(stdout='version')):
                with self.assertRaisesRegex(ValueError, 'binary changed'):
                    run_jobs(root, manifest, mumax=binary)

    def test_prefix_comparison_is_finite_ensemble_diagnostic(self):
        values = np.tile(np.arange(1., 5.)[:, None], (1, len(H_GRID)))
        result = prefix_metrics(values, [2, 4])
        self.assertEqual(result[0]['delta_B800_vs_largest_N_T'], -1.)
        self.assertEqual(result[1]['max_curve_delta_vs_largest_N_T'], 0.)
        self.assertIn('not_independent', result[0]['comparison_role'])
        with self.assertRaises(ValueError):
            prefix_metrics(values, [5])

    def test_new_and_legacy_sampling_cannot_be_compared_as_two_seeds(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            native_fixture(root)
            values, source = read_ensemble(root, 'G', 'TD')
            current = {**source, 'texture_sampling_version': SAMPLING_VERSION}
            # Fail the cross-protocol comparison before any bank/model use.
            with patch('tools.analyze_sampling_convergence.read_ensemble', side_effect=[(values, current),
                       (values, {**source, 'texture_sampling_version': LEGACY_SAMPLING_VERSION})]):
                with self.assertRaisesRegex(ValueError, 'different texture sampling protocols'):
                    analyze(root, 'G', 'TD', root/'analysis', baseline=root)
            self.assertFalse((root/'analysis').exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
