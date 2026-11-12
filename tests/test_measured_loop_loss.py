import csv
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from flask import Flask

from modules.external_bh_validation import read_bh_points, read_measurement_csv
from modules.measured_loop_loss import MeasuredLoopLoss, create_measured_loop_routes, integrate_loop
from modules.system_data import WorkpackStore


HEADER = b'H_kA_per_m,J_mT\n'
SAMPLES = [b'0.4,0\n', b'0,1200\n', b'-0.4,0\n', b'0,-1200\n', b'0.4,0\n']
LOOP = HEADER + b''.join(SAMPLES)


class MeasuredLoopLossTest(unittest.TestCase):
    def test_polygon_area_signed_reverse_and_mass_conversion(self):
        normal, segments = integrate_loop(read_bh_points(LOOP, '回线'), 50, 8000)
        reverse, _ = integrate_loop(read_bh_points(HEADER+b''.join(reversed(SAMPLES)), '回线'), 50, 8000)
        self.assertAlmostEqual(normal['signed_HdJ_J_m3'], 960)
        self.assertAlmostEqual(normal['signed_HdB_J_m3'], 960)
        self.assertAlmostEqual(reverse['signed_HdJ_J_m3'], -960)
        self.assertEqual(normal['volume_loss_W_m3'], reverse['volume_loss_W_m3'])
        self.assertEqual(normal['mass_loss_W_kg'], 6)
        self.assertEqual(normal['closing_energy_J_m3'], 0)
        self.assertTrue(segments[-1]['closing_segment'])
        self.assertEqual(segments[-1]['next_source_row'], 2)

    def test_loop_and_magnetization_entry_points_require_their_own_sampling_order(self):
        with self.assertRaisesRegex(ValueError, '非负 H'):
            read_measurement_csv(LOOP, 'RD')
        monotone = read_bh_points(b'H_A_per_m,B_T\n0,0\n10,.1\n20,1\n', '回线')
        with self.assertRaisesRegex(ValueError, '上升和下降'):
            integrate_loop(monotone, 50)
        partial_endpoint = read_bh_points(HEADER+b''.join(SAMPLES[:-1]), '回线')
        stats, _ = integrate_loop(partial_endpoint, 50)
        self.assertAlmostEqual(stats['closing_energy_J_m3'], 240)
        self.assertAlmostEqual(stats['closing_absolute_energy_fraction'], .25)

    def test_form_reopens_exact_upload_and_keeps_missing_density_empty(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        store = WorkpackStore(Path(temporary.name))
        app = Flask(__name__, template_folder=str(Path(__file__).resolve().parents[1]/'templates'))
        app.register_blueprint(create_measured_loop_routes(MeasuredLoopLoss(store)))
        client = app.test_client()

        def submit(**extra):
            return client.post('/measured-loop-loss', data=dict(source_reference='闭合多边形联调',
                               frequency_hz='50', single_cycle='yes', cycle=(io.BytesIO(LOOP), 'cycle.csv'), **extra))

        self.assertEqual(submit(density_kg_m3='8000').status_code, 400)
        self.assertEqual(store.records(), [])
        response = submit()
        self.assertEqual(response.status_code, 303)
        self.assertEqual(submit().location, response.location)
        _, blob = store.read(response.location.rsplit('/', 1)[1])
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            self.assertEqual(archive.read('original/cycle.csv'), LOOP)
            report = json.loads(archive.read('loop_loss_report.json'))
            self.assertIsNone(report['result']['mass_loss_W_kg'])
            self.assertFalse(report['measurement_conditions_verified'])
            rows = list(csv.DictReader(io.StringIO(archive.read('ordered_segments.csv').decode('utf-8-sig'))))
            self.assertEqual([int(row['source_row']) for row in rows], [2, 3, 4, 5, 6])
        self.assertIn('密度未提供', client.get(response.location).get_data(as_text=True))


    def test_declared_decimal_comma_input_keeps_original_line_numbers_and_bytes(self):
        source = b" 400,0;0,0\r\n\r\n0,0;1,2\r\n-400,0;0,0\r\n0,0;-1,2\r\n"
        with tempfile.TemporaryDirectory() as temporary:
            store = WorkpackStore(Path(temporary))
            service = MeasuredLoopLoss(store)
            form = dict(source_reference='发布者注明 H 为 A/m，B 为 T', frequency_hz='50', single_cycle='yes')
            with self.assertRaisesRegex(ValueError, '标注'):
                service.save(form, 'original.csv', source)
            declared = dict(form, input_format='decimal_comma_hb')
            with self.assertRaisesRegex(ValueError, '第 3 行'):
                service.save(declared, 'wrong.csv', source.replace(b'0,0;1,2', b'0,0;1.2'))
            self.assertEqual(store.records(), [])
            record = service.save(declared, 'original.csv', source)
            _, saved = store.read(record['id'])
            with zipfile.ZipFile(io.BytesIO(saved)) as archive:
                self.assertEqual(archive.read('original/cycle.csv'), source)
                summary = json.loads(archive.read('loop_loss_report.json'))
                self.assertEqual(summary['source']['input_format'], 'decimal_comma_hb')
                self.assertAlmostEqual(summary['result']['energy_loss_J_m3_per_cycle'], 960)
                rows = list(csv.DictReader(io.StringIO(archive.read('ordered_segments.csv').decode('utf-8-sig'))))
                self.assertEqual([int(row['source_row']) for row in rows], [1,3,4,5])
                self.assertEqual(rows[-1]['next_source_row'], '1')


if __name__ == '__main__':
    unittest.main()
