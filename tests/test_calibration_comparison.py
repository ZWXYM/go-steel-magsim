import copy
import io
from pathlib import Path
import tempfile
import unittest
import zipfile

from modules.calibration_comparison import compare_calibrations, comparison_bundle, comparison_csv
from modules.calibration_transfer import error_metrics
from modules.system_data import json_bytes, sha256, verify_package


class CalibrationComparisonTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.storage = Path(temp.name)
        self.left = self.report('cal_aaaaaaaaaaaa', .1)
        self.right = self.report('cal_bbbbbbbbbbbb', .05)

    @staticmethod
    def report(artifact, holdout_offset):
        grades = ['B23R075','B27R095']
        report = dict(id=artifact,H_axis='physical_A_per_m',selected_grades=grades,
                      source_manifest_sha256='f'*64,source_bank_sha256='a'*64,physics_version='physics',
                      texture_sampling_version='texture',source_native_evidence={},evidence_status='reference',
                      calibration_version=artifact,bank_sha256='b'*64,comparisons=[],metrics=[],
                      nested_validation={'outer_folds':[
                          {'heldout':grade,'selection':{'training_grades':[other for other in grades if other!=grade]}}
                          for grade in grades]})
        h = [0,100,800,1000,5000]
        ref = [0,.5,1.,1.2,1.6]
        held = [0,*(b+holdout_offset for b in ref[1:])]
        for grade in grades:
            for direction in ('RD','TD'):
                report['comparisons'].append(dict(grade=grade,direction=direction,H=h,raw_B=ref,reference_B=ref,
                                                  corrected_B=ref,holdout_B=held))
                for method, curve in [('calibration_fit',ref),('nested_material_holdout',held)]:
                    report['metrics'].append(dict(grade=grade,direction=direction,method=method,**error_metrics(h,curve,ref)))
        return report

    def save(self, report):
        blob = json_bytes(report)
        folder = self.storage/report['id']
        folder.mkdir(exist_ok=True)
        (folder/'report.json').write_bytes(blob)
        return sha256(blob)

    def compare(self, right=None):
        if right is None:right=self.right
        return compare_calibrations(self.storage,self.left['id'],right['id'],self.save(self.left),self.save(right))

    def test_paired_holdout_delta_and_exact_original_report_archive(self):
        result, raw = self.compare()
        self.assertEqual(result['baseline']['fit_mean_rmse_T'],0)
        self.assertEqual(result['improved_holdout_directions'],4)
        self.assertAlmostEqual(result['rows'][0]['holdout_delta_rmse_T'],-.05)
        self.assertAlmostEqual(result['candidate']['material_holdout_max_abs_B800_error_T'],.05)
        blob = comparison_bundle(result,raw).getvalue()
        self.assertEqual(blob,comparison_bundle(result,raw).getvalue())
        self.assertEqual(verify_package(blob)['member_count'],5)
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            self.assertEqual(archive.read('candidate/report.json'),json_bytes(self.right))
            self.assertEqual(archive.read('comparison.csv'),comparison_csv(result))

    def test_different_reference_or_leaked_holdout_training_is_refused(self):
        bad_reference=copy.deepcopy(self.right)
        bad_reference['comparisons'][0]['reference_B'][2]=1.1
        with self.assertRaisesRegex(ValueError,'参考曲线'):
            self.compare(bad_reference)
        bad_fold=copy.deepcopy(self.right)
        fold=bad_fold['nested_validation']['outer_folds'][0]
        fold['selection']['training_grades']=[fold['heldout']]
        with self.assertRaisesRegex(ValueError,'留出训练集'):
            self.compare(bad_fold)
        with self.assertRaisesRegex(ValueError,'报告已变化'):
            compare_calibrations(self.storage,self.left['id'],self.right['id'],self.save(self.left),'f'*64)


if __name__ == '__main__':
    unittest.main()
