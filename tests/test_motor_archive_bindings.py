"""Explicit same-report source bindings never replace the original final archive."""
import copy
import csv
import io
import json
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from modules.material_calibration import file_hash
from modules.motor_archive_bindings import BoundMotorArchive,VERSION,TOLERANCES,DATASET_ID
from modules.motor_dataset_export import dataset_bundle
from modules.motor_optimization import MotorOptimization
from modules.motor_workbench import MotorWorkbench,collect_metrics
from test_workbench import csv_fixture

PROJECT=Path(__file__).resolve().parents[1]


class MotorArchiveBindingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='bound archive ');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.caseid='synthetic_case';self.case=self.root/'motor/v8_2/cases'/self.caseid;self.case.mkdir(parents=True)
        self.original=self.root/'original_fixture';csv_fixture(self.original)
        for folder,name,column in [('reports','Drive Current Plots.csv','Ia [A]'),('maxwell_reports','SolidLoss.csv','SolidLoss [W]'),('maxwell_reports','input_voltage.csv','InputVoltage [V]')]:
            with (self.original/folder/name).open('w',newline='') as stream:
                writer=csv.writer(stream);writer.writerow(['Time [ms]',column]);writer.writerows((i/3,0) for i in range(31))
        self.metrics=collect_metrics(self.original)
        for source,target in [('reports','clean_resolved2_reports'),('maxwell_reports','clean_resolved2_maxwell_reports')]:
            shutil.copytree(self.original/source,self.case/target)
        self.report=dict(ok=True,project=str(self.case/'synthetic_project.aedt'),solve={'ok':True},
            thesis_metrics={key:self.metrics[key] for key in TOLERANCES})
        self.write_report(self.report)
        self.contract=self.root/'bindings.json';self.write_contract()
        self.motor=MotorWorkbench(self.root,self.root/'motor_storage',PROJECT)
        self.reader=BoundMotorArchive(self.root,self.motor,self.contract)

    def write_report(self,value):
        blob=json.dumps(value,indent=2).encode('utf8')
        for name in ('final_resolved_official_report.json','clean_resolved2_official_report.json'):(self.case/name).write_bytes(blob)

    def write_contract(self):
        names=['final_resolved_official_report.json','clean_resolved2_official_report.json',
            *[p.relative_to(self.case).as_posix() for folder in ('clean_resolved2_reports','clean_resolved2_maxwell_reports') for p in (self.case/folder).glob('*.csv')]]
        self.alias=dict(case=self.caseid,source_prefix='clean_resolved2',project_name='synthetic_project.aedt',
            final_report='final_resolved_official_report.json',source_report='clean_resolved2_official_report.json',metric_tolerances=TOLERANCES,
            files={name:dict(sha256=file_hash(self.case/name),bytes=(self.case/name).stat().st_size) for name in names})
        self.payload=dict(schema='explicit_identical_report_archive_bindings_v1',version=VERSION,aliases=[self.alias])
        self.contract.write_text(json.dumps(self.payload),encoding='utf8')

    def test_bound_version_recovers_raw_csv_without_mutating_missing_final_or_original_record(self):
        before=self.motor.archived_results();self.assertFalse(before[0]['csv_verified'])
        files={p:p.read_bytes() for p in self.case.rglob('*') if p.is_file()}
        with patch('subprocess.Popen',side_effect=AssertionError('source recovery starts no native work')):
            recovered=self.reader.records()[0]
        self.assertTrue(recovered['csv_verified']);self.assertTrue(recovered['metrics']['accepted'])
        self.assertEqual(recovered['actual_csv_prefix'],'clean_resolved2');self.assertEqual(recovered['collection_version'],VERSION)
        self.assertTrue(recovered['report_binding']['reports_byte_identical']);self.assertFalse(recovered['metrics']['comparison_eligibility']['eligible'])
        self.assertEqual(recovered['metrics']['T_avg_Nm'],self.metrics['T_avg_Nm']);self.assertEqual(self.motor.archived_results(),before)
        self.assertFalse((self.case/'final_resolved_reports').exists());self.assertEqual(files,{p:p.read_bytes() for p in files})

    def test_missing_bound_csv_never_falls_back_to_another_available_prefix(self):
        shutil.copytree(self.case/'clean_resolved2_reports',self.case/'resolved_reports')
        (self.case/'clean_resolved2_reports/Torque Plots.csv').unlink()
        row=self.reader.records()[0];self.assertFalse(row['csv_verified']);self.assertIn('已改变或缺失',row['reason'])
        with self.assertRaises(ValueError):self.reader.source_file(self.caseid,'resolved_reports/Torque Plots.csv')

    def test_report_byte_identity_is_required_even_when_metrics_and_registered_hashes_match(self):
        path=self.case/'clean_resolved2_official_report.json';path.write_bytes(path.read_bytes()+b'\n')
        self.write_contract();row=self.reader.records()[0]
        self.assertFalse(row['csv_verified']);self.assertIn('不是相同字节',row['reason'])

    def test_report_must_belong_to_the_selected_native_case_not_only_have_matching_numbers(self):
        wrong=dict(self.report,project=str(self.root/'another_case/synthetic_project.aedt'))
        self.write_report(wrong);self.write_contract();row=self.reader.records()[0]
        self.assertFalse(row['csv_verified']);self.assertIn('不属于该工程',row['reason'])

    def test_csv_changes_and_arithmetic_disagreement_are_rejected_without_rewriting_summary(self):
        self.write_report(dict(self.report,thesis_metrics=dict(self.report['thesis_metrics'],T_avg_Nm=9)))
        self.write_contract();before=(self.case/'final_resolved_official_report.json').read_bytes()
        row=self.reader.records()[0];self.assertFalse(row['csv_verified']);self.assertIn('同报告指标不符',row['reason'])
        self.assertEqual((self.case/'final_resolved_official_report.json').read_bytes(),before)
        (self.case/'clean_resolved2_reports/Torque Plots.csv').write_bytes(b'changed')
        row=self.reader.records()[0];self.assertFalse(row['csv_verified']);self.assertIn('已改变或缺失',row['reason'])

    def test_contract_cannot_expand_paths_or_relax_original_rounding_tolerances(self):
        for change in ({'source_prefix':'resolved'},{'project_name':'../private.aedt'},{'metric_tolerances':dict(TOLERANCES,T_avg_Nm=1.)}):
            data=copy.deepcopy(self.payload);data['aliases'][0].update(change);self.contract.write_text(json.dumps(data),encoding='utf8')
            with self.subTest(change=change),self.assertRaises(ValueError):self.reader.contract()

    def test_contract_change_while_reading_is_not_given_a_verified_label(self):
        def mutate(*_):
            self.contract.write_text(self.contract.read_text()+'\n',encoding='utf8');return copy.deepcopy(self.metrics)
        with patch('modules.motor_archive_bindings.collect_metrics',side_effect=mutate):row=self.reader.records()[0]
        self.assertFalse(row['csv_verified']);self.assertIn('合同在读取期间变化',row['reason'])

    def test_nonfinite_report_metric_is_not_accepted_by_a_nan_comparison(self):
        self.write_report(dict(self.report,thesis_metrics=dict(self.report['thesis_metrics'],P_Fe_W=float('nan'))))
        self.write_contract();self.assertFalse(self.reader.records()[0]['csv_verified'])

    def test_new_dataset_has_its_own_identity_and_preserves_actual_prefix_in_downloaded_sources(self):
        manager=MotorOptimization(self.root,self.root/'optimization',self.motor);manager.bound_archive=self.reader
        original=manager.dataset('v8_2_archive');self.assertFalse(original['rows'][0]['input_accepted'])
        data=manager.dataset(DATASET_ID);self.assertTrue(data['rows'][0]['input_accepted'])
        self.assertEqual(data['version'],VERSION);self.assertEqual(data['rows'][0]['actual_csv_prefix'],'clean_resolved2')
        self.assertEqual(data['data_usage']['native_csv_accepted_count'],1)
        self.assertTrue(data['data_usage']['same_cases_not_new_independent_samples'])
        with zipfile.ZipFile(dataset_bundle(manager,DATASET_ID)) as archive:
            self.assertEqual(archive.read('sources/bound_archive/'+self.caseid+'/clean_resolved2_reports/Torque Plots.csv'),(self.case/'clean_resolved2_reports/Torque Plots.csv').read_bytes())
            self.assertEqual(archive.read('sources/bound_archive/'+self.caseid+'/_binding_contract.json'),self.contract.read_bytes())
            snapshot=json.loads(archive.read('source_snapshot.json'));self.assertEqual(snapshot['native_csv_accepted_count'],1)
            self.assertFalse(any('/final_resolved_reports/' in name for name in archive.namelist()))
        self.assertEqual(manager.dataset('v8_2_archive'),original)

    def test_bad_optional_contract_does_not_break_original_dataset_catalog_or_values(self):
        manager=MotorOptimization(self.root,self.root/'optimization',self.motor);manager.bound_archive=self.reader
        before=manager.dataset('v8_2_archive');self.contract.write_bytes(b'{broken')
        ids=[row['id'] for row in manager.datasets()];self.assertIn('v8_2_archive',ids);self.assertIn(DATASET_ID,ids)
        self.assertEqual(manager.dataset('v8_2_archive'),before)
        with self.assertRaises(ValueError):manager.dataset(DATASET_ID)


if __name__=='__main__':unittest.main()
