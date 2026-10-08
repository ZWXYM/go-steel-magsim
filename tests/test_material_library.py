"""Saved-output reuse, effective bank isolation, corrupt entries and CPU downloads."""
import copy
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from flask import Flask
PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from modules.calibration_transfer import VERSION
from modules.calibration_workbench import CalibrationWorkbench, GRADES
from modules.material_library import MaterialLibrary, sha
from modules.workbench_routes import create_workbench


class MaterialLibraryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.seed = tempfile.TemporaryDirectory()
        root = PROJECT.parent if PROJECT.name == 'magsim' else PROJECT
        manager = CalibrationWorkbench(root, Path(cls.seed.name) / 'calibration')
        report = manager.calibrate(list(GRADES), VERSION)
        pair = json.loads((manager.pilot / 'B30P105/raw_pair.json').read_text())
        prediction = manager.predict(report['id'], pair, ['B30P105'])
        cls.cal, cls.pred = report['id'], prediction['id']
        cls.all_pred = manager.predict(report['id'], pair)['id']

    @classmethod
    def tearDownClass(cls):
        cls.seed.cleanup()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = self.root / 'storage'
        shutil.copytree(self.seed.name, self.store)
        self.manager = CalibrationWorkbench(self.root, self.store / 'calibration')
        self.library = MaterialLibrary(self.manager)
        self.base = self.manager.get(self.cal)
        self.key = 'prediction:' + self.pred

    def test_reopen_catalog_scope_and_original_bytes_no_refitting(self):
        before = {str(p): sha(p.read_bytes()) for p in self.store.rglob('*') if p.is_file()}
        with patch('modules.calibration_transfer.TransferBank.fit', side_effect=AssertionError('no refit')), \
                patch.object(CalibrationWorkbench, 'predict', side_effect=AssertionError('no prediction')):
            catalog = self.library.catalog()
            self.assertEqual(len(catalog['records'][0]['entries']), 10)
            self.assertTrue(all(e['available'] for e in catalog['records'][0]['entries']))
            detail = MaterialLibrary(self.manager).detail(self.cal, self.key)
            self.assertNotEqual(detail['parent_bank_sha256'], detail['effective_bank_sha256'])
            self.assertNotIn('B30P105', detail['training_grades'])
            self.assertEqual(detail['excluded_grades'], ['B30P105'])
            self.assertFalse(detail['motor_ranking_eligible'])
            self.assertEqual(detail['H_scale'], 1)
            with zipfile.ZipFile(self.library.bundle(self.cal, self.key)) as z:
                saved = json.loads((self.base / self.pred / 'prediction.json').read_text())
                self.assertEqual(z.read('material.amat'), (self.base / self.pred / saved['AMAT_file']).read_bytes())
                self.assertEqual(json.loads(z.read('manifest.json'))['new_predictions'], 0)
                self.assertEqual(json.loads(z.read('effective_bank.json')), json.loads((self.base / self.pred / 'effective_bank.json').read_text()))
                self.assertIn('H_A_per_m', z.read('curves.csv').decode('utf-8-sig'))
        self.assertEqual(before, {str(p): sha(p.read_bytes()) for p in self.store.rglob('*') if p.is_file()})

    def test_fit_and_holdout_separate_curves_and_evidence(self):
        fit = self.library.detail(self.cal, 'fit:B30P105')
        held = self.library.detail(self.cal, 'holdout:B30P105')
        self.assertEqual(fit['evidence_scope'], 'same_material_fit')
        self.assertEqual(fit['parent_bank_sha256'], fit['effective_bank_sha256'])
        self.assertEqual(held['evidence_scope'], 'whole_material_exclusion')
        self.assertNotEqual(fit['curves']['RD']['B'], held['curves']['RD']['B'])
        self.assertFalse(held['independently_validated'])
        self.assertEqual(held['original_export_sha256'], held['files']['material.amat']['sha256'])

    def test_prediction_without_exclusion_is_not_labeled_holdout(self):
        detail = self.library.detail(self.cal, 'prediction:' + self.all_pred)
        self.assertEqual(detail['evidence_scope'], 'all_materials_prediction')
        self.assertEqual(detail['excluded_grades'], [])
        self.assertEqual(detail['parent_bank_sha256'], detail['effective_bank_sha256'])

    def test_corrupt_record_does_not_hide_healthy_materials(self):
        bad = self.manager.get('cal_123456789abc');bad.mkdir();(bad/'report.json').write_text('{')
        (self.base / self.pred / 'effective_bank.json').write_text('{')
        catalog = self.library.catalog()
        row = next(r for r in catalog['records'] if r['id'] == self.cal)
        self.assertEqual(sum(e['available'] for e in row['entries']), 9)
        self.assertFalse(next(e for e in row['entries'] if e['key'] == self.key)['available'])
        self.assertIsNotNone(next(r for r in catalog['records'] if r['id'] != self.cal)['error'])

    def test_changed_original_hash_and_unbound_prediction_curves_rejected(self):
        fit = self.library.detail(self.cal, 'fit:B23R075')
        p = self.base / fit['files']['material.amat']['file'];p.write_bytes(p.read_bytes() + b'\n')
        with self.assertRaisesRegex(ValueError, '原导出哈希'):
            self.library.detail(self.cal, 'fit:B23R075')
        p = self.base / self.pred / 'prediction.json';saved = json.loads(p.read_text());saved['RD']['B'][3] += .02
        p.write_text(json.dumps(saved))
        with self.assertRaisesRegex(ValueError, '保存的物理 H'):
            self.library.detail(self.cal, self.key)

    def test_raw_input_or_excluded_weights_tamper_rejected(self):
        raw = self.base / self.pred / 'input_raw_pair.json';before = raw.read_bytes();raw.write_bytes(before+b'\n')
        with self.assertRaisesRegex(ValueError, '原生输入哈希'):
            self.library.detail(self.cal, self.key)
        raw.write_bytes(before)
        saved = json.loads((self.base / self.pred / 'prediction.json').read_text())
        p = (self.base / self.pred / saved['AMAT_file']).with_suffix('.metadata.json')
        metadata = json.loads(p.read_text());metadata['RD_anchor_weights']['B30P105'] = 0
        p.write_text(json.dumps(metadata))
        with self.assertRaisesRegex(ValueError, '权重包含排除'):
            self.library.detail(self.cal, self.key)

    def test_effective_bank_cannot_be_replaced_with_parent(self):
        shutil.copy2(self.base / 'bank.json', self.base / self.pred / 'effective_bank.json')
        with self.assertRaisesRegex(ValueError, '实际子库'):
            self.library.detail(self.cal, self.key)

    def test_legacy_missing_scope_visible_and_paths_rejected(self):
        p = self.base / self.pred / 'prediction.json';saved = json.loads(p.read_text());del saved['prediction_scope'];p.write_text(json.dumps(saved))
        with self.assertRaisesRegex(ValueError, '旧预测'):
            self.library.detail(self.cal, self.key)
        for artifact, key in [('../outside', self.key), (self.cal, 'fit:../outside'), (self.cal, 'prediction:pred_xyz')]:
            with self.assertRaises(ValueError):
                self.library.detail(artifact, key)
        with self.assertRaisesRegex(ValueError, '未知材料包'):
            self.library.download(self.cal, 'fit:B23R075', '../bank.json')

    def test_cpu_routes_download_and_submission_block(self):
        app = Flask('materials_cpu', template_folder=str(PROJECT / 'templates'), static_folder=str(PROJECT / 'static'))
        with patch.dict(os.environ, {'MAGSIM_CPU_ONLY':'1', 'MAGSIM_AUTO_RESUME_QUEUE':'0'}):
            app.register_blueprint(create_workbench(PROJECT, self.store, self.root))
        client = app.test_client()
        self.assertEqual(client.get('/material-library').status_code, 200)
        self.assertEqual(client.get('/api/workbench/material-library').status_code, 200)
        url = '/api/workbench/material-library/' + self.cal + '/' + self.key
        self.assertEqual(client.get(url).json['excluded_grades'], ['B30P105'])
        for tail in ['/bundle', '/files/material.amat', '/files/metadata.json', '/files/curves.csv']:
            response = client.get(url + tail);self.assertEqual(response.status_code, 200);response.close()
        self.assertEqual(client.get(url + '/files/unlisted.txt').status_code, 400)
        self.assertEqual(client.post('/api/workbench/motor/jobs/scan_123456789abc/submit').status_code, 423)


if __name__ == '__main__':
    unittest.main()
