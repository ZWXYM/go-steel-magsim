import csv
import io
import tempfile
import unittest
import zipfile
from pathlib import Path

from flask import Flask
from openpyxl import Workbook

from modules.measured_loop_workbook import (MeasuredLoopWorkbook, POINT_COLUMNS, RESULT_COLUMNS,
                                            create_measured_loop_workbook_routes, replay_workbook)
from modules.system_data import WorkpackStore, sha256
from modules.system_data_reference_evidence import collect_reference_package


def workbook_bytes(change=None):
    workbook = Workbook()
    results = workbook.active
    results.title = 'Results'
    results.append(RESULT_COLUMNS)
    results.append(('1', 'Epstein test', 'sample-tag', 'Sin', '1 T', 50, 1, 125, 1.4))
    results.append(('2', 'Epstein test', 'sample-tag', 'Sin', '1 T', 100, 1, 125, 2.8))
    data = workbook.create_sheet('DataPoints')
    data.append(POINT_COLUMNS)
    points = ((-100, -1), (100, 0), (40, 1), (-120, 0))
    for freq in (50, 100):
        for n, (h, b) in enumerate(points, 1):
            data.append((f'1T/{freq}Hz', n, h, b))
        data.append((None, None, None, None))
    if change:
        change(results, data)
    stream = io.BytesIO()
    workbook.save(stream)
    return stream.getvalue()


class MeasuredLoopWorkbookTest(unittest.TestCase):
    def test_grouped_replay_preserves_units_source_rows_and_download(self):
        raw = workbook_bytes()
        with tempfile.TemporaryDirectory() as temporary:
            store = WorkpackStore(Path(temporary))
            service = MeasuredLoopWorkbook(store)
            app = Flask(__name__, template_folder=str(Path(__file__).resolve().parents[1] / 'templates'))
            app.register_blueprint(create_measured_loop_workbook_routes(service))
            client = app.test_client()

            def submit():
                return client.post('/measured-loop-workbook', data=dict(
                    source_reference='source workbook', ordered_cycles='yes',
                    workbook=(io.BytesIO(raw), 'epstein.xlsx'), source_kind='published_measurement'))

            saved = submit()
            self.assertEqual(saved.status_code, 303)
            self.assertEqual(submit().location, saved.location)
            self.assertEqual(client.get(saved.location).status_code, 200)
            pack_id = saved.location.rsplit('/', 1)[1]
            record, report = service.read(pack_id)
            self.assertIsNone(report['source']['reported_grade'])
            self.assertEqual(report['source']['workbook_sample_labels'], ['sample-tag'])
            self.assertEqual(report['source']['direction'], 'unspecified')
            self.assertEqual(report['point_count'], 8)
            rows = report['cycles']
            self.assertEqual([row['H_at_positive_B_peak_A_per_m'] for row in rows], [40, 40])
            self.assertEqual([row['reported_Hmax_A_per_m'] for row in rows], [125, 125])
            self.assertEqual([row['reported_Ps_W_kg'] for row in rows], [1.4, 2.8])
            self.assertEqual([row['volume_loss_W_m3'] for row in rows], [11000, 22000])
            self.assertEqual([row['positive_peak_data_row'] for row in rows], [4, 9])
            self.assertTrue(all(row['mass_loss_W_kg'] is None for row in rows))
            meta, blob = collect_reference_package(store, dict(id=pack_id, sha256=record['sha256']))
            self.assertEqual(meta['kind'], 'loop_workbook')
            self.assertEqual(meta['originals']['original/workbook.xlsx']['sha256'], sha256(raw))
            with zipfile.ZipFile(io.BytesIO(blob)) as archive:
                self.assertEqual(archive.read('original/workbook.xlsx'), raw)
                segments = list(csv.DictReader(io.StringIO(archive.read('ordered_segments.csv').decode('utf-8-sig'))))
                self.assertEqual([(r['source_row'], r['next_source_row']) for r in segments if r['closing_segment'] == 'True'],
                                 [('5', '2'), ('10', '7')])

    def test_mismatched_groups_and_formula_values_are_rejected(self):
        def mismatch(results, data):
            data['A2'] = '1T/60Hz'

        def formula(results, data):
            data['C2'] = '=1+2'

        def duplicate(results, data):
            results['F3'] = 50

        for change, message in ((mismatch, '测量标签'), (formula, '有限数值单元格'), (duplicate, '组合重复')):
            with self.subTest(change=change.__name__), self.assertRaisesRegex(ValueError, message):
                replay_workbook(workbook_bytes(change))
