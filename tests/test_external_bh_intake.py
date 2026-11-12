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
ALTERNATE = 'cal_abcdef123456'
GRADE = 'B23R075'
RD = b'H_A_per_m,B_T\n20,1.0\n800,1.9\n1200,1.95\n'
TD = b'H_A_per_m,J_T\n800,1.1\n400,0.8\n10,0.1\n'


class SavedMaterials:
    def __init__(self, storage):
        self.storage = storage
        self.report = self.add_report(CALIBRATION, 'a' * 64)
        self.heldout_training = ['B27R090']

    def add_report(self, artifact, bank):
        folder = self.storage / artifact
        folder.mkdir(parents=True)
        report = folder / 'report.json'
        report.write_bytes(json_bytes(dict(
            id=artifact, H_axis='physical_A_per_m', calibration_version='fixture',
            bank_sha256=bank, known_material_exports={GRADE: {}}, holdout_material_exports={GRADE: {}})))
        return report

    def detail(self, artifact, key):
        assert artifact in (CALIBRATION, ALTERNATE) and key in ('fit:' + GRADE, 'holdout:' + GRADE)
        fit = key.startswith('fit:')
        primary = artifact == CALIBRATION
        parent, heldout = ('a', 'b') if primary else ('e', 'f')
        field = [10, 800, 1000] if primary else [30, 800, 900]
        return dict(
            key=key, evidence_scope='same_material_fit' if fit else 'whole_material_exclusion',
            training_grades=[GRADE, 'B27R090'] if fit else self.heldout_training, excluded_grades=[] if fit else [GRADE],
            parent_bank_sha256=parent * 64, effective_bank_sha256=(parent if fit else heldout) * 64,
            files={'source_report.json': {'sha256': sha256((self.storage / artifact / 'report.json').read_bytes())},
                   'material.amat': {'sha256': ('c' if fit else 'd') * 64}},
            curves={direction: {'H': field, 'B': values}
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
            self.assertEqual(report['comparison']['RD']['field_weighted']['bands']['up_to_800']['H_range_A_per_m'], [20,800])
            self.assertAlmostEqual(report['comparison']['RD']['B800']['fit_error_T'], .01)
            self.assertFalse(report['specimen_independence_verified'])
        self.library.report.write_bytes(b'{}')
        page = self.client.get(result.location).get_data(as_text=True)
        self.assertIn('流程联调', page)
        self.assertIn('B800', page)
        self.assertIn('按物理 H 区间计权', page)
        self.assertIn('原表未注明牌号', page)
        self.assertIn('/api/workbench/system-data/packages/' + package + '/download', page)

    def test_stale_or_leaky_models_cannot_create_a_comparison(self):
        stale = self.selection.rsplit('|', 1)[0] + '|' + 'f' * 64
        self.assertEqual(self._post_pair(selection=stale).status_code, 400)
        self.library.heldout_training.append(GRADE)
        self.assertEqual(self._post_pair().status_code, 400)
        self.assertEqual(self.store.records(), [])

    def test_declared_different_grade_can_be_archived_but_not_compared(self):
        mismatch = self._post_pair(reported_grade='H110-0.27')
        self.assertEqual(mismatch.status_code, 400)
        self.assertIn('原表标注牌号与所选模型不同', mismatch.get_data(as_text=True))
        self.assertEqual(self.store.records(), [])
        archived = self._post_pair(reported_grade='H110-0.27', mode='archive')
        self.assertEqual(archived.status_code, 303)
        report = self.intake.read(archived.location.rsplit('/',1)[1])[1]
        self.assertEqual(report['source']['reported_grade'], 'H110-0.27')
        self.assertEqual(report['comparison'], {})
        matched = self._post_pair(reported_grade='b23r075')
        self.assertEqual(matched.status_code, 303)
        compared = self.intake.read(matched.location.rsplit('/',1)[1])[1]
        self.assertEqual(compared['declared_grade_status'], 'matches_selected')
        self.assertFalse(compared['specimen_independence_verified'])

    def test_two_versions_use_common_rows_and_preserve_old_result(self):
        first = self._post_pair()
        first_id = first.location.rsplit('/', 1)[1]
        _, first_report = self.intake.read(first_id)
        alternate = self.library.add_report(ALTERNATE, 'e' * 64)
        selection = '|'.join((ALTERNATE, sha256(alternate.read_bytes())))
        result = self._post_pair(secondary_selection=selection)
        self.assertEqual(result.status_code, 303)
        artifact = result.location.rsplit('/', 1)[1]
        _, report = self.intake.read(artifact)
        self.assertEqual(report['comparison_model']['calibration_id'], ALTERNATE)
        self.assertEqual(report['comparison_model']['grade'], GRADE)
        self.assertEqual(report['comparison']['RD']['overlap_H_range_A_per_m'], [30, 900])
        self.assertEqual(report['comparison']['RD']['compared_points'], 1)
        self.assertEqual(set(report['comparison']['RD']['metrics']), {'fit', 'holdout', 'secondary_fit', 'secondary_holdout'})
        self.assertEqual(set(report['comparison']['RD']['field_weighted']['bands']['common']['metrics']),
                         {'fit','holdout','secondary_fit','secondary_holdout'})
        self.assertAlmostEqual(report['comparison']['RD']['metrics']['fit']['rmse_T'], .01)
        self.assertAlmostEqual(report['comparison']['RD']['metrics']['secondary_holdout']['rmse_T'], .1)
        self.assertIn(ALTERNATE, self.client.get(result.location).get_data(as_text=True))
        self.assertEqual(self.intake.read(first_id)[1], first_report)
        self.assertEqual(first_report['comparison']['RD']['compared_points'], 2)
        self.assertEqual(self._post_pair(secondary_selection=CALIBRATION+'|'+'a'*64).status_code, 400)

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
