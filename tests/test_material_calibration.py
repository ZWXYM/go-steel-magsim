"""Scientific contracts for the new fixed-H calibration protocol."""
import copy
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / 'modules'))

from modules.material_calibration import CalibrationBank, VERSION, PHYSICS_VERSION, extract_loop_midpoint
from modules.mx3_generator import generate_single_mode_script, generate_complex_mode_script, SimulationConfig
from modules.reference_corrector import apply_reference_correction
from modules.maxwell_exporter import generate_amat_content
from modules.maxwell_exporter import export_calibrated_pair
from modules.dataset_contract import dataset_contract


def fixture_payload():
    anchors = []
    for grade, fraction, delta in [('A', .6, .1), ('B', .8, .2), ('C', .9, .3)]:
        for direction in ('RD', 'TD'):
            anchors.append({'grade': grade, 'direction': direction,
                'params': {'f_Goss': fraction, 'theta_0_deg': 5.,
                           'halfwidth_deg': 8., 'Si_content': 3.},
                'H': [0., 100., 800., 1000.], 'delta_B': [0., delta, delta, delta]})
    return {'calibration_version': VERSION, 'physics_version': PHYSICS_VERSION,
            'H_axis': 'physical_A_per_m', 'anchors': anchors}


class CalibrationContracts(unittest.TestCase):
    def setUp(self):
        self.payload = fixture_payload()
        self.bank = CalibrationBank(self.payload)
        self.params = self.payload['anchors'][2]['params']
        self.H = np.array([0., 100., 800., 1000.])
        self.B = np.array([0., .4, .9, 1.])

    def test_cubic_generator_uses_sample_field_and_rotated_axes(self):
        script = generate_single_mode_script(1, 20, 45, 10, 90)
        self.assertIn('Kc1 = 3.60e+04', script)
        self.assertIn('Ku1 = 0', script)
        self.assertIn('Hx_dir := 0.0000000000', script)
        self.assertIn('Hy_dir := 1.0000000000', script)
        self.assertIn('Hz_dir := 0.0000000000', script)
        vectors = [np.array([float(v) for v in m.split(',')])
                   for m in re.findall(r'anisC[12] = vector\(([^)]+)\)', script)]
        np.testing.assert_allclose([np.linalg.norm(v) for v in vectors], [1, 1], atol=1e-9)
        self.assertAlmostEqual(float(vectors[0] @ vectors[1]), 0, places=9)
        self.assertIn(PHYSICS_VERSION, script)
        self.assertEqual(SimulationConfig.get_rve_size()[2], 1e-9)

    def test_complex_generator_same_physics(self):
        script = generate_complex_mode_script(1, 20, 45, 10, [0, 90])
        self.assertIn('Kc1 = 3.60e+04', script)
        self.assertIn('anisC1 = vector', script)
        self.assertIn('Field directions in sample frame', script)

    def test_H_axis_and_input_are_unchanged(self):
        before = self.B.copy()
        result = self.bank.correct(self.H, self.B, self.params, direction='TD')
        np.testing.assert_array_equal(result['H'], self.H)
        np.testing.assert_array_equal(self.B, before)
        self.assertEqual(result['H_scale'], 1)
        np.testing.assert_allclose(result['B'], self.B + [.0, .2, .2, .2])

    def test_disabled_is_exact_identity(self):
        negative = np.array([0, -.1, .9, 1.])
        result = self.bank.correct(self.H, negative, self.params, direction='RD', weight_cap=0)
        np.testing.assert_array_equal(result['B'], negative)

    def test_heldout_reference_cannot_influence_prediction(self):
        result = self.bank.correct(self.H, self.B, self.params, direction='RD', exclude_grades=['B'])
        changed = copy.deepcopy(self.payload)
        for anchor in changed['anchors']:
            if anchor['grade'] == 'B':
                anchor['delta_B'] = [100, 100, 100, 100]
        other = CalibrationBank(changed).correct(self.H, self.B, self.params,
                                                direction='RD', exclude_grades=['B'])
        self.assertNotIn('B', result['anchor_weights'])
        np.testing.assert_array_equal(result['B'], other['B'])

    def test_bank_replacement_changes_actual_corrector(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'bank.json'
            path.write_text(json.dumps(self.payload), encoding='utf-8')
            kwargs = dict(direction='RD', calibration_path=str(path), physics_version=PHYSICS_VERSION)
            old = apply_reference_correction(self.H, self.B, self.params, **kwargs)
            changed = copy.deepcopy(self.payload)
            changed['anchors'][2]['delta_B'] = [0., .4, .4, .4]
            path.write_text(json.dumps(changed), encoding='utf-8')
            new = apply_reference_correction(self.H, self.B, self.params, **kwargs)
            self.assertGreater(float(new[2] - old[2]), .19)

    def test_no_Si_or_physics_silent_defaults(self):
        params = {k: v for k, v in self.params.items() if k != 'Si_content'}
        with self.assertRaises(KeyError):
            self.bank.correct(self.H, self.B, params, direction='RD')
        with self.assertRaises(ValueError):
            self.bank.correct(self.H, self.B, self.params, direction='RD', physics_version='legacy')
        with self.assertRaises(ValueError):
            apply_reference_correction(self.H, self.B, self.params,
                                       calibration_path='unused.json')

    def test_out_of_H_support_and_invalid_curves_rejected(self):
        for h in ([0, 100, 800, 2000], [0, 100, 100, 1000], [0, 100, float('nan'), 1000]):
            with self.assertRaises(ValueError):
                self.bank.correct(h, self.B, self.params, direction='RD')

    def test_exclusion_of_all_anchors_is_error(self):
        with self.assertRaises(ValueError):
            self.bank.correct(self.H, self.B, self.params, direction='RD',
                              exclude_grades=['A', 'B', 'C'])

    def test_native_amat_keeps_physical_H_and_B(self):
        result = self.bank.correct(self.H, self.B, self.params, direction='RD')
        content = generate_amat_content('CalibratedFixture', result['H'], result['B'],
            result['H'], result['B'], core_loss_override={'kh': 0, 'kc': 0, 'ke': 0})
        blocks = re.findall(r'Points\[(\d+): ([^]]+)\]', content)
        self.assertEqual(len(blocks), 2)  # RD and TD; ND is a scalar prior
        for count, values in blocks:
            coords = np.array([float(v) for v in values.split(',')]).reshape(-1, 2)
            self.assertEqual(int(count), len(self.H) * 2)
            np.testing.assert_allclose(coords[:, 0], self.H, atol=1e-6)
            np.testing.assert_allclose(coords[:, 1], result['B'], atol=1e-6)

    def test_calibrated_export_preserves_metadata_and_zero_loss(self):
        with tempfile.TemporaryDirectory() as tmp:
            rd = self.bank.correct(self.H, self.B, self.params, direction='RD')
            td = self.bank.correct(self.H, self.B, self.params, direction='TD')
            path = Path(export_calibrated_pair(rd, td, 'ExperimentalFixture',
                            thickness_mm=.23, export_dir=tmp))
            meta = json.loads(path.with_suffix('.metadata.json').read_text(encoding='utf-8'))
            self.assertEqual(meta['H_axis'], 'physical_A_per_m')
            self.assertEqual(meta['calibration_sha256'], self.bank.bank_sha256)
            self.assertEqual(meta['core_loss_status'], 'uncalibrated_zero_placeholders')
            self.assertIn("'core_loss_kh'='0.000000e+00'", path.read_text(encoding='utf-8'))

    def test_mixed_physics_dataset_rejected_by_both_trainers(self):
        import pandas as pd
        from modules.ml_trainer import BHPredictor
        from modules.paper_surrogate_trainer import prepare_dataset
        df = pd.DataFrame({'f_Goss': [.6, .8], 'B_0deg_H100': [.5, .6],
            'simulation_physics_version': ['legacy_unspecified', PHYSICS_VERSION]})
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'mixed.csv'
            df.to_csv(path, index=False)
            with self.assertRaisesRegex(ValueError, 'simulation_physics_version'):
                BHPredictor(model_dir=str(Path(tmp) / 'models')).train(str(path))
            with self.assertRaisesRegex(ValueError, 'simulation_physics_version'):
                prepare_dataset(str(path))

    def test_diagnostic_fit_table_cannot_be_promoted_to_training(self):
        import pandas as pd
        with self.assertRaisesRegex(ValueError, '诊断'):
            dataset_contract(pd.DataFrame({'dataset_role': ['calibration_fit_diagnostics']}))

    def test_branch_midpoint_T_to_H_and_projection(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'table.txt'
            header = '# t (s)\tmx ()\tmy ()\tmz ()\tB_extx (T)\tB_exty (T)\tB_extz (T)\tmx ()\tmy ()\tmz ()\tE_total (J)\tbranch ()'
            rows = []
            mu0 = 4 * np.pi * 1e-7
            for branch, fields in [(-1, [1000, 100, 0, -100, -1000]),
                                   (1, [-1000, -100, 0, 100, 1000])]:
                for h in fields:
                    my = .1 * np.sign(h) + .05 * branch
                    rows.append([0, .99, my, 0, 0, h * mu0, 0, .99, my, 0, 0, branch])
            np.savetxt(path, rows, header=header, comments='')
            extracted = extract_loop_midpoint(path, [0, 100, 1000], angle_deg=90)
            np.testing.assert_allclose(extracted['B'], [0, mu0 * (100 + 156000),
                                                       mu0 * (1000 + 156000)], atol=1e-9)


if __name__ == '__main__':
    unittest.main(verbosity=2)
