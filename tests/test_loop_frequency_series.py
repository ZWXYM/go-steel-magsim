import io
import json
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from flask import Flask

from modules.frequency_loss_validation import FrequencyLossValidation, create_frequency_loss_routes
from modules.measured_loop_loss import MeasuredLoopLoss
from modules.system_data import WorkpackStore, sha256
from modules.system_data_reference_evidence import collect_reference_package
from modules.workbench_cpu_policy import install_cpu_policy


class LoopFrequencySeriesTest(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.store = WorkpackStore(Path(scratch.name))
        self.cycles = MeasuredLoopLoss(self.store)
        self.frequency = FrequencyLossValidation(self.store)
        self.form = dict(one_series='yes', quantity='J', peak_T='1.5', tolerance_T='0.005',
                         loss_basis='volume', holdout_frequency_Hz='60')

    def cycle(self, f, *, height=None, peak=1.5, quantity='J', **extra):
        h = f if height is None else height
        raw = f'H_A_per_m,{quantity}_T\n{h},0\n0,{peak}\n{-h},0\n0,{-peak}\n{h},0\n'.encode()
        fields = dict(source_reference='解析联调数据', source_kind='demonstration', specimen_id='analytic-1',
                      method='解析菱形测试', direction='RD', conditions='固定形状；场峰值随频率线性变化',
                      frequency_hz=str(f), single_cycle='yes')
        fields.update(extra)
        record = self.cycles.save(fields, f'analytic_{f}Hz.csv', raw)
        return record['id']+'|'+record['sha256'], raw

    def test_saved_cycle_integrals_feed_sparse_frequency_holdout_and_keep_originals(self):
        cycles = [self.cycle(f) for f in (100, 50, 60)]
        app = Flask(__name__, template_folder=str(Path(__file__).resolve().parents[1]/'templates'))
        app.register_blueprint(create_frequency_loss_routes(self.frequency))
        install_cpu_policy(app)
        client = app.test_client()
        self.assertIn('analytic-1', client.get('/frequency-loss-validation/loops').get_data(as_text=True))
        response = client.post('/frequency-loss-validation/loops', data=dict(self.form, loop=[row[0] for row in cycles]))
        self.assertEqual(response.status_code, 303, response.get_data(as_text=True))
        result = client.get(response.location).get_data(as_text=True)
        self.assertIn('从保存回线整理的频率表', result)
        self.assertIn('三项拟合留空', result)
        artifact = response.location.rsplit('/', 1)[1]
        record, blob = self.store.read(artifact)
        evidence, archived = collect_reference_package(self.store, dict(id=artifact, sha256=record['sha256']))
        self.assertEqual(evidence['derivation'], 'saved_loop_integrals')
        self.assertEqual(archived, blob)
        with ZipFile(io.BytesIO(blob)) as archive:
            report = json.loads(archive.read('frequency_loss_report.json'))
            rows = report['cycle_series']['cycles']
            self.assertEqual([row['frequency_Hz'] for row in rows], [50.,60.,100.])
            self.assertEqual([row['loss'] for row in rows], [7500.,10800.,30000.])
            self.assertEqual([row['standard_u'] for row in report['rows']], [None]*3)
            self.assertEqual(report['source']['source_kind'], 'demonstration')
            result, = report['evaluations']
            self.assertIsNone(result['basis_coefficients'])
            self.assertAlmostEqual(result['estimates']['bracket_cycle_linear']['predicted'], 10800.)
            originals = {row[0].split('|')[0]: row[1] for row in cycles}
            for row in rows:
                self.assertEqual(archive.read(row['path']), self.store.read(row['id'])[1])
                with ZipFile(io.BytesIO(archive.read(row['path']))) as inner:
                    self.assertEqual(inner.read('original/cycle.csv'), originals[row['id']])
                self.assertEqual(row['original_csv_sha256'], sha256(originals[row['id']]))

    def test_series_rejects_mixed_identity_peak_quantity_density_and_reused_cycles(self):
        first, second = [self.cycle(f)[0] for f in (50,60)]
        for third, message in ((self.cycle(100, direction='TD')[0], '共同测量条件不同'),
                               (self.cycle(100, method='另一个仪器')[0], '共同测量条件不同'),
                               (self.cycle(100, direction='unspecified')[0], '须声明'),
                               (self.cycle(100, peak=1.55)[0], '超出允许偏差'),
                               (self.cycle(100, quantity='B')[0], '不能混用 B 和 J'),
                               (self.cycle(60, height=65)[0], '每个频率只选一条'),
                               (self.cycle(100, height=60)[0], '不可按不同频率重复使用')):
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                self.frequency.save_cycles(self.form, [first,second,third])
        third = self.cycle(100)[0]
        with self.assertRaisesRegex(ValueError, '相同且有来源的试样密度'):
            self.frequency.save_cycles(dict(self.form,loss_basis='mass'), [first,second,third])
        self.assertFalse(any(row['source_filename']=='frequency_loss_validation.zip' for row in self.store.records()))

    def test_mass_series_uses_same_declared_density(self):
        chosen = [self.cycle(f, density_kg_m3='8000', density_reference='联调给定值')[0] for f in (50,60,100)]
        pack = self.frequency.save_cycles(dict(self.form,loss_basis='mass'), chosen)
        _, report = self.frequency.read(pack['id'])
        self.assertEqual(report['table']['loss_unit'], 'W/kg')
        self.assertEqual(report['cycle_series']['density_kg_m3'], 8000)
        self.assertAlmostEqual(report['evaluations'][0]['actual_loss'], 1.35)


if __name__ == '__main__':
    unittest.main()
