import csv
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from flask import Flask

from modules.external_bh_intake import ExternalBHIntake, create_external_bh_routes
from modules.system_data import WorkpackStore, json_bytes, sha256


CALIBRATION = 'cal_123456789abc'
GRADE = 'B23R075'
RD = b'H_A_per_m,B_T\n20,1.0\n800,1.9\n1200,1.95\n'
TD = b'H_A_per_m,J_T\n800,1.1\n400,0.8\n10,0.1\n'


class SavedMaterials:
    def __init__(self, storage):
        self.storage = storage
        folder = storage / CALIBRATION
        folder.mkdir(parents=True)
        self.report = folder / 'report.json'
        self.report.write_bytes(json_bytes(dict(
            id=CALIBRATION, H_axis='physical_A_per_m', calibration_version='fixture',
            bank_sha256='a' * 64, known_material_exports={GRADE: {}}, holdout_material_exports={GRADE: {}})))
        self.heldout_training = ['B27R090']

    def detail(self, artifact, key):
        assert artifact == CALIBRATION and key in ('fit:' + GRADE, 'holdout:' + GRADE)
        fit = key.startswith('fit:')
        return dict(
            key=key, evidence_scope='same_material_fit' if fit else 'whole_material_exclusion',
            training_grades=[GRADE, 'B27R090'] if fit else self.heldout_training, excluded_grades=[] if fit else [GRADE],
            parent_bank_sha256='a' * 64, effective_bank_sha256=('a' if fit else 'b') * 64,
            files={'source_report.json': {'sha256': sha256(self.report.read_bytes())},
                   'material.amat': {'sha256': ('c' if fit else 'd') * 64}},
            curves={direction: {'H': [10, 800, 1000], 'B': values}
                    for direction, values in (('RD', [0.1, 1.91 if fit else 1.8, 2.0]),
                                               ('TD', [0.05, 1.2, 1.3]))})


class ExternalBHIntakeTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.library = SavedMaterials(root / 'calibration')
        self.store = WorkpackStore(root / 'system_data')
        self.intake = ExternalBHIntake(self.library, self.store)
        templates = Path(__file__).resolve().parents[1] / 'templates'
        app = Flask(__name__, template_folder=str(templates))
        app.register_blueprint(create_external_bh_routes(self.intake))
        self.client = app.test_client()
        self.selection = self.intake.choices()[0]['options'][0]['value']

    def _post_pair(self, **fields):
        data = dict(selection=self.selection, source_reference='试验流程联调样例', source_kind='demonstration',
                    curve_kind='normal_peak', paired='yes', mode='compare',
                    RD=(io.BytesIO(RD), 'RD.csv'), TD=(io.BytesIO(TD), 'TD.csv'))
        data.update(fields)
        return self.client.post('/external-bh-validation', data=data)

    def test_pair_archives_exact_originals_and_reopens_saved_metrics(self):
        result = self._post_pair()
        self.assertEqual(result.status_code, 303)
        self.assertEqual(self._post_pair().location, result.location)
        package = result.location.rsplit('/', 1)[1]
        record, raw = self.store.read(package)
        self.assertEqual(record['source_filename'], 'external_bh_validation.zip')
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            self.assertEqual(archive.read('original/RD.csv'), RD)
            self.assertEqual(archive.read('original/TD.csv'), TD)
            report = json.loads(archive.read('external_bh_report.json'))
            rows = list(csv.DictReader(io.StringIO(archive.read('comparison/RD.csv').decode('utf-8-sig'))))
            self.assertEqual(rows[-1]['in_comparison'], 'False')
            self.assertEqual(rows[-1]['fit_B_T'], '')
            self.assertEqual(report['model']['models']['holdout']['excluded_grades'], [GRADE])
            self.assertEqual(report['comparison']['RD']['compared_points'], 2)
            self.assertAlmostEqual(report['comparison']['RD']['B800']['fit_error_T'], .01)
            self.assertFalse(report['specimen_independence_verified'])
        self.library.report.write_bytes(b'{}')
        page = self.client.get(result.location).get_data(as_text=True)
        self.assertIn('流程联调', page)
        self.assertIn('B800', page)
        self.assertIn('/api/workbench/system-data/packages/' + package + '/download', page)

    def test_stale_or_leaky_models_cannot_create_a_comparison(self):
        stale = self.selection.rsplit('|', 1)[0] + '|' + 'f' * 64
        self.assertEqual(self._post_pair(selection=stale).status_code, 400)
        self.library.heldout_training.append(GRADE)
        self.assertEqual(self._post_pair().status_code, 400)
        self.assertEqual(self.store.records(), [])

    def test_archive_accepts_one_original_without_physical_axis_claim(self):
        raw = b'H,unknown\n12,0.18\n'
        result = self.client.post('/external-bh-validation', data=dict(
            source_reference='未注明方向的资料', mode='archive', RD=(io.BytesIO(raw), 'input.csv')))
        self.assertEqual(result.status_code, 303)
        artifact = result.location.rsplit('/', 1)[1]
        _, report = self.intake.read(artifact)
        self.assertEqual(report['comparison'], {})
        self.assertFalse(report['source']['direction_and_pairing_declared'])
        self.assertNotIn('H_axis', report)
        self.assertEqual(self._post_pair(paired='').status_code, 400)
        self.assertIn('原表归档', self.client.get(result.location).get_data(as_text=True))


if __name__ == '__main__':
    unittest.main()
