import math
import unittest

from modules.external_bh_validation import compare_direction, field_weighted_errors, read_measurement_csv


class ExternalBHValidationTest(unittest.TestCase):
    def test_common_measured_rows_and_physical_b800(self):
        original = ('编号,H [kA/m],J [mT]\nA,2,1900\nA,1,1700\nA,0.6,1400\nA,0.1,500\n')
        measurement = read_measurement_csv(original.encode('utf-8-sig'), 'RD')
        fit = {'H': [200, 600, 1600], 'B': [0.6, 1.5, 2.0]}
        heldout = {'H': [500, 800, 2000], 'B': [1.1, 1.7, 2.1]}
        result = compare_direction(measurement, fit, heldout)
        self.assertEqual(result['overlap_H_range_A_per_m'], [500, 1600])
        self.assertEqual(result['compared_points'], 2)
        self.assertEqual(result['source_order'], 'descending')
        self.assertEqual([row['in_comparison'] for row in result['rows']], [False, True, True, False])
        self.assertIsNone(result['rows'][0]['holdout_B_T'])
        self.assertIsNone(result['rows'][3]['fit_B_T'])
        mu0 = 4e-7 * math.pi
        fit_errors = [1.7 - (1.7 + 1000 * mu0), 1.5 - (1.4 + 600 * mu0)]
        self.assertAlmostEqual(result['metrics']['fit']['rmse_T'], math.sqrt(sum(v*v for v in fit_errors)/2))
        self.assertAlmostEqual(result['metrics']['fit']['mae_T'], sum(map(abs, fit_errors))/2)
        self.assertFalse(result['B800']['at_original_point'])
        self.assertAlmostEqual(result['B800']['input_B_T'], 1.55 + 800 * mu0)
        self.assertAlmostEqual(result['B800']['fit_B_T'], 1.6)
        self.assertAlmostEqual(result['B800']['holdout_B_T'], 1.7)

    def test_no_extrapolation_or_synthetic_zero(self):
        measurement = read_measurement_csv(b'H_A_per_m,B_mT\n12,320\n20,480\n', 'TD')
        fit = {'H': [30, 1000], 'B': [0.8, 2]}
        heldout = {'H': [0, 1000], 'B': [0, 2]}
        result = compare_direction(measurement, fit, heldout)
        self.assertIsNone(result['overlap_H_range_A_per_m'])
        self.assertIsNone(result['B800'])
        self.assertEqual(result['metrics'], {'fit': None, 'holdout': None})
        self.assertEqual([row['input_B_T'] for row in result['rows']], [0.32, 0.48])

    def test_catalog_comments_keep_source_units_and_line_numbers(self):
        measurement = read_measurement_csv(
            b'# source\n# H (A/m), B (T)\n# origin\n10,0.8\n20,1.1\n', 'RD')
        self.assertEqual(measurement['H_column'], 'H (A/m)')
        self.assertEqual([row['source_row'] for row in measurement['points']], [4, 5])
        self.assertEqual([row['input_B_T'] for row in measurement['points']], [0.8, 1.1])

    def test_field_weighting_detects_model_kink_between_original_points(self):
        measured = read_measurement_csv(b'H_A_per_m,B_T\n800,0\n0,0\n', 'RD')
        fitted = {'H':[0,400,800], 'B':[0,2,0]}
        heldout = {'H':[0,800], 'B':[0,0]}
        self.assertEqual(compare_direction(measured, fitted, heldout)['metrics']['fit']['rmse_T'], 0)
        bands = field_weighted_errors(measured, {'fit':fitted,'holdout':heldout})['bands']
        low = bands['up_to_800']
        self.assertEqual(low['H_range_A_per_m'], [0,800])
        self.assertEqual(low['largest_original_gap_A_per_m'], 800)
        self.assertAlmostEqual(low['metrics']['fit']['rmse_T'], 2/math.sqrt(3))
        self.assertAlmostEqual(low['metrics']['fit']['mae_T'], 1)
        self.assertIsNone(bands['from_800']['metrics']['fit'])

    def test_field_integral_accounts_for_sign_change_and_common_clipping(self):
        measured = read_measurement_csv(b'H_A_per_m,B_T\n100,0\n1200,0\n', 'TD')
        models = {'a':{'H':[200,1100], 'B':[-1,2]}, 'b':{'H':[200,1100], 'B':[0,0]}}
        bands = field_weighted_errors(measured, models)['bands']
        common = bands['common']
        self.assertEqual(common['H_range_A_per_m'], [200,1100])
        self.assertAlmostEqual(common['metrics']['a']['rmse_T'], 1)
        self.assertAlmostEqual(common['metrics']['a']['mae_T'], 5/6)
        self.assertEqual(bands['up_to_800']['H_range_A_per_m'], [200,800])
        self.assertEqual(bands['from_800']['span_A_per_m'], 300)
        models['b']['H'] = [1100, 2000]
        self.assertEqual(field_weighted_errors(measured, models)['bands']['common']['span_A_per_m'], 0)

    def test_unknown_units_ambiguous_ordinate_and_loop_rejected(self):
        bad = (b'H,B_T\n10,1\n20,2\n',
               b'H_A_per_m,B_T,J_T\n10,1,1\n20,2,2\n',
               b'H_A_per_m,J_T\n10,1\n20,2\n15,1\n',
               b'H_A_per_m,J_T\n10,1\n20,nan\n')
        for original in bad:
            with self.subTest(original=original), self.assertRaises(ValueError):
                read_measurement_csv(original, 'RD')


if __name__ == '__main__':
    unittest.main()
