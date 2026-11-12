import csv
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from flask import Flask

from modules.frequency_loss_validation import (
    FrequencyLossValidation, create_frequency_loss_routes, evaluate_frequency_holdout, read_frequency_table,
)
from modules.system_data import WorkpackStore


def sample_table(heldout_loss=None, heldout_u='.02', *, varied_identity=None):
    text = io.StringIO(newline='')
    writer = csv.writer(text)
    writer.writerow(('frequency_Hz', 'peak_B_T', 'volume_loss_W_per_m3', 'standard_u_W_per_m3',
                     'configuration', 'sample_label', 'direction', 'table_variant'))
    for f in (400, 50, 100, 1000, 200):
        x = f / 100
        value = .32*x+.21*x**1.5+.14*x**2
        source = ['specimen-1', 'RD', 'primary']
        if f == 100 and varied_identity is not None:
            source[varied_identity] += '-other'
        writer.writerow((f, 1.5, heldout_loss if f == 100 and heldout_loss else value,
                         heldout_u if f == 100 else x*.02, 'A' if f < 200 else 'B', *source))
    return text.getvalue().encode('utf-8-sig')


class FrequencyLossTest(unittest.TestCase):
    def test_analytic_law_and_holdout_values_do_not_train_model(self):
        [baseline] = evaluate_frequency_holdout(read_frequency_table(sample_table()), 100)
        [changed] = evaluate_frequency_holdout(read_frequency_table(sample_table(4, '.0001')), 100)
        [missing] = evaluate_frequency_holdout(read_frequency_table(sample_table(4, '')), 100)
        self.assertAlmostEqual(baseline['estimates']['nonnegative_fit']['predicted'], .67, places=11)
        for row in (changed, missing):
            self.assertEqual(row['basis_coefficients'], baseline['basis_coefficients'])
            self.assertEqual(row['weighting'], 'inverse_standard_u')
            self.assertAlmostEqual(row['estimates']['nonnegative_fit']['signed_error'], .67-4, places=11)
        self.assertEqual(baseline['training_frequencies_Hz'], [50, 200, 400, 1000])
        self.assertNotIn(baseline['holdout_source_row'], baseline['training_source_rows'])

    def test_quadratic_loss_is_linear_per_cycle_without_uncertainty(self):
        text = 'frequency_Hz,peak_J_T,specific_loss_W_per_kg\n'
        text += ''.join(f'{f},1.7,{(f/100)**2}\n' for f in (50, 100, 200, 400, 1000))
        [result] = evaluate_frequency_holdout(read_frequency_table(text.encode()), 100)
        self.assertEqual(result['weighting'], 'equal')
        self.assertEqual(result['missing_training_standard_u'], 4)
        self.assertAlmostEqual(result['estimates']['bracket_cycle_linear']['predicted'], 1.)
        self.assertAlmostEqual(result['estimates']['linear_lowest']['predicted'], .5)

    def test_rejects_extrapolation_mixed_identifiers_and_ambiguous_units(self):
        for invalid_target in (50, 300, 1000):
            with self.subTest(invalid_target=invalid_target), self.assertRaisesRegex(ValueError, '两侧各有至少一档训练频率'):
                evaluate_frequency_holdout(read_frequency_table(sample_table()), invalid_target)
        for varied in (0, 1, 2):
            with self.subTest(varied=varied), self.assertRaisesRegex(ValueError, '一个试样、方向和统计版本'):
                read_frequency_table(sample_table(varied_identity=varied))
        with self.assertRaisesRegex(ValueError, '不确定度列应与损耗列对应'):
            read_frequency_table(sample_table().replace(b'standard_u_W_per_m3', b'standard_u_W_per_kg'))
        with self.assertRaisesRegex(ValueError, '一种峰值量'):
            read_frequency_table(b'frequency_Hz,peak_J_T,peak_B_T,specific_loss_W_per_kg\n100,1,1,2\n')

    def test_three_frequencies_only_support_bracketed_interpolation(self):
        header = 'frequency_Hz,peak_J_T,specific_loss_W_per_kg,standard_u_W_per_kg\n'
        # Peak 1.5 has the usual 50/60/100 Hz source minimum, while peak 1.7 has five rates.
        losses = [(1.5, (50,60,100)), (1.7, (50,60,100,200,400))]
        original = (header + ''.join(f'{f},{peak},{(f/100)**2},.01\n' for peak, rates in losses for f in rates)).encode()
        sparse, dense = evaluate_frequency_holdout(read_frequency_table(original), 60)
        self.assertEqual(sparse['basis_status'], 'insufficient_training_frequencies')
        self.assertIsNone(sparse['basis_coefficients'])
        self.assertNotIn('nonnegative_fit', sparse['estimates'])
        self.assertAlmostEqual(sparse['estimates']['bracket_cycle_linear']['predicted'], .36)
        self.assertAlmostEqual(sparse['estimates']['linear_lowest']['predicted'], .30)
        self.assertEqual(sparse['training_frequencies_Hz'], [50, 100])
        self.assertEqual(dense['basis_status'], 'fit')
        self.assertAlmostEqual(dense['estimates']['nonnegative_fit']['predicted'], .36)

    def test_form_reopens_raw_upload_and_retains_configuration_and_units(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        store = WorkpackStore(Path(temporary.name))
        app = Flask(__name__, template_folder=str(Path(__file__).resolve().parents[1]/'templates'))
        app.register_blueprint(create_frequency_loss_routes(FrequencyLossValidation(store)))
        client = app.test_client()
        original = sample_table()

        def submit(direction):
            return client.post('/frequency-loss-validation', data=dict(source_reference='可解析验证用序列',
                               holdout_frequency_Hz='100', source_kind='demonstration', direction=direction,
                               one_series='yes', loss_table=(io.BytesIO(original), 'test.csv')))

        self.assertEqual(submit('TD').status_code, 400)
        self.assertEqual(store.records(), [])
        response = submit('unspecified')
        self.assertEqual(response.status_code, 303)
        self.assertEqual(submit('RD').location, response.location)
        page = client.get(response.location).get_data(as_text=True)
        self.assertIn('配置随记录变化：A、B', page)
        self.assertIn('W/m³', page)
        _, blob = store.read(response.location.rsplit('/', 1)[1])
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            self.assertEqual(archive.read('original/loss_table.csv'), original)
            report = json.loads(archive.read('frequency_loss_report.json'))
            self.assertEqual(report['source']['direction'], 'RD')
            self.assertEqual(report['table']['amplitude'], 'B')
            self.assertEqual([row['frequency_Hz'] for row in report['rows']], [400, 50, 100, 1000, 200])
            self.assertEqual([row['configuration'] for row in report['rows']], ['B', 'A', 'A', 'B', 'B'])
            self.assertEqual([row['frequency_Hz'] for row in report['rows'] if row['role'] == 'holdout'], [100])


if __name__ == '__main__':
    unittest.main()
