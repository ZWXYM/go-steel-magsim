"""Physical geometry/distribution and cross-protocol label protection."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
from scipy.spatial.transform import Rotation

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from modules.texture_sampling import sample_texture, SAMPLING_VERSION, LEGACY_SAMPLING_VERSION
from modules.mx3_generator import euler_to_crystal_axes
from modules.material_calibration import CalibrationBank
from modules.dataset_contract import dataset_contract
from modules.maxwell_exporter import export_calibrated_pair
from tools.predict_calibrated_material import predict_pair
from tools.run_calibration_pilot import prepare, run_jobs
from test_material_calibration import fixture_payload


class TextureSamplingContracts(unittest.TestCase):
    def test_prefix_and_provenance_are_stable_for_N_and_global_rng(self):
        small, rows, metadata = sample_texture(.79, 6, 8, 8, 20261007)
        np.random.seed(49)
        before = copy.deepcopy(np.random.get_state())
        large, large_rows, other = sample_texture(.79, 6, 64, 8, 20261007)
        after = np.random.get_state()
        np.testing.assert_array_equal(small, large[:8])
        self.assertEqual(rows, large_rows[:8])
        self.assertEqual(metadata['distribution_sha256'], other['distribution_sha256'])
        np.testing.assert_array_equal(before[1], after[1])
        self.assertEqual(before[2:], after[2:])
        _, _, alternate = sample_texture(.79, 6, 64, 8, 20261008)
        self.assertEqual(metadata['distribution_sha256'], alternate['distribution_sha256'])

    def test_Haar_background_has_uniform_direction_moments(self):
        e, rows, _ = sample_texture(0, 0, 30000, 8, 90)
        matrices = Rotation.from_euler('ZXZ', e, degrees=True).as_matrix()
        self.assertTrue(all(r['component'] == 'haar_background' for r in rows))
        np.testing.assert_allclose(matrices.mean(axis=0), 0, atol=.015)
        np.testing.assert_allclose((matrices**2).mean(axis=0), 1/3, atol=.015)
        self.assertLess(abs(np.mean(np.cos(np.deg2rad(e[:, 1])))), .015)
        np.testing.assert_allclose(np.linalg.det(matrices), 1, atol=1e-12)

    def test_ideal_Goss_is_cubic_equivalent_011_100_and_rotates_about_ND(self):
        e, _, _ = sample_texture(1, 30, 8, 0, 3)
        axes = np.column_stack(euler_to_crystal_axes(*e[0]))
        np.testing.assert_allclose(axes[:, 0], [np.sqrt(3)/2, .5, 0], atol=1e-12)
        np.testing.assert_allclose(axes.T @ [0, 0, 1], np.array([0, 1, 1])/np.sqrt(2), atol=1e-12)

    def test_ND_rotation_transforms_entire_realization(self):
        base, _, _ = sample_texture(.7, 0, 64, 10, 3)
        turned, _, _ = sample_texture(.7, 30, 64, 10, 3)
        rotation = Rotation.from_euler('z', 30, degrees=True)
        expected = (rotation * Rotation.from_euler('ZXZ', base, degrees=True)).as_matrix()
        actual = np.array([np.column_stack(euler_to_crystal_axes(*e)) for e in turned])
        np.testing.assert_allclose(actual, expected, atol=1e-12)

    def test_invalid_parameters_fail_before_sampling(self):
        for kwargs in [dict(f_Goss=-.1), dict(f_Goss=1.1), dict(theta_0_deg=float('nan')),
                       dict(halfwidth_deg=-1), dict(halfwidth_deg=61), dict(n_grains=0),
                       dict(seed=-1), dict(seed=1.2)]:
            args = dict(f_Goss=.8, theta_0_deg=5, n_grains=8, halfwidth_deg=8, seed=2)
            args.update(kwargs)
            with self.assertRaises(ValueError):
                sample_texture(**args)

    def versioned_bank(self):
        payload = fixture_payload()
        payload['texture_sampling_version'] = SAMPLING_VERSION
        for anchor in payload['anchors']:
            anchor['texture_sampling_version'] = SAMPLING_VERSION
        return CalibrationBank(payload)

    def test_bank_rejects_protocol_mismatch_and_new_protocol_without_identity(self):
        old, new = CalibrationBank(fixture_payload()), self.versioned_bank()
        params = old.anchors[0]['params']
        for bank, sampling in [(old, SAMPLING_VERSION), (new, LEGACY_SAMPLING_VERSION)]:
            with self.assertRaisesRegex(ValueError, 'sampling versions'):
                bank.correct([0, 100, 800], [0, .5, 1.], params, direction='RD', texture_sampling_version=sampling)
        with self.assertRaisesRegex(ValueError, 'sampling versions'):
            new.correct([0, 100, 800], [0, .5, 1.], params, direction='RD')
        payload = copy.deepcopy(new.payload)
        del payload['anchors'][0]['texture_sampling_version']
        with self.assertRaisesRegex(ValueError, 'Mixed texture'):
            CalibrationBank(payload)

    def test_predict_and_native_export_transport_sampling_identity(self):
        bank = self.versioned_bank()
        raw = {'material_id': 'A', 'params': bank.anchors[0]['params'],
               'simulation_physics_version': bank.payload['physics_version'],
               'texture_sampling_version': SAMPLING_VERSION,
               'RD': {'H': [0, 100, 800], 'B': [0, .5, 1.]},
               'TD': {'H': [0, 100, 800], 'B': [0, .2, .5]}}
        result = predict_pair(raw, bank)
        self.assertEqual(result['texture_sampling_version'], SAMPLING_VERSION)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(export_calibrated_pair(result['RD'], result['TD'], 'Versioned', thickness_mm=.23, export_dir=folder))
            metadata = json.loads(path.with_suffix('.metadata.json').read_text(encoding='utf-8'))
            self.assertEqual(metadata['texture_sampling_version'], SAMPLING_VERSION)
            wrong_td = dict(result['TD'], texture_sampling_version=LEGACY_SAMPLING_VERSION)
            with self.assertRaisesRegex(ValueError, 'sampling versions'):
                export_calibrated_pair(result['RD'], wrong_td, 'Mixed', thickness_mm=.23, export_dir=folder)
        del raw['texture_sampling_version']
        with self.assertRaisesRegex(ValueError, 'sampling versions'):
            predict_pair(raw, bank)

    def test_both_trainers_reject_mixed_sampling_protocols(self):
        from modules.ml_trainer import BHPredictor
        from modules.paper_surrogate_trainer import prepare_dataset
        df = pd.DataFrame({'f_Goss': [.6, .8], 'B_0deg_H100': [.5, .6],
                           'texture_sampling_version': [LEGACY_SAMPLING_VERSION, SAMPLING_VERSION]})
        with self.assertRaisesRegex(ValueError, 'texture_sampling_version'):
            dataset_contract(df)
        with self.assertRaisesRegex(ValueError, 'texture_sampling_version'):
            dataset_contract(pd.DataFrame({'texture_sampling_version': [SAMPLING_VERSION, None]}))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'mixed.csv'
            df.to_csv(path, index=False)
            with self.assertRaisesRegex(ValueError, 'texture_sampling_version'):
                BHPredictor(model_dir=str(Path(folder) / 'models')).train(str(path))
            with self.assertRaisesRegex(ValueError, 'texture_sampling_version'):
                prepare_dataset(str(path))

    def test_prepare_freezes_prefix_and_rejects_version_or_provenance_drift(self):
        with tempfile.TemporaryDirectory() as folder:
            small, large = Path(folder)/'n2', Path(folder)/'n4'
            a, b = prepare(small, 2, 5), prepare(large, 4, 5)
            self.assertEqual(a['texture_sampling_version'], SAMPLING_VERSION)
            for ma, mb in zip(a['materials'], b['materials']):
                ea = np.loadtxt(small/ma['grade']/'orientations.csv', delimiter=',', skiprows=1)
                eb = np.loadtxt(large/mb['grade']/'orientations.csv', delimiter=',', skiprows=1)
                np.testing.assert_array_equal(ea, eb[:2])
            self.assertEqual(a['jobs'][0]['script_sha256'], b['jobs'][0]['script_sha256'])
            with self.assertRaisesRegex(ValueError, 'sampling version'):
                prepare(small, 2, 5, LEGACY_SAMPLING_VERSION)
            path = small/a['materials'][0]['grain_samples']['path']
            path.write_bytes(path.read_bytes()+b'\n')
            with self.assertRaisesRegex(ValueError, 'provenance changed'):
                prepare(small, 2, 5)

    def test_second_runner_is_rejected_before_native_execution(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/'runner.lock').write_text('other owner')
            with patch('tools.run_calibration_pilot.subprocess.run') as native:
                with self.assertRaisesRegex(ValueError, 'already locked'):
                    run_jobs(root, {})
                native.assert_not_called()
            self.assertEqual((root/'runner.lock').read_text(), 'other owner')


if __name__ == '__main__':
    unittest.main(verbosity=2)
