"""One saved motor dataset per download, with original files and no solver."""
import copy
import csv
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from modules.motor_dataset_export import dataset_bundle,digest


class MotorDatasetExport(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='motor data ')
        self.addCleanup(self.temp.cleanup)
        self.folder=Path(self.temp.name)
        self.source=self.folder/'original.csv';self.source.write_bytes(b'Time [ms],Torque\r\n0,-2\r\n')
        rows=[dict(case='one',material='M6',input_accepted=True,metrics=dict(T_avg_Nm=2,K_T_ripple_pct=30,P_Fe_W=1,eta_estimate_pct=90),input_source='official_CSV_verified'),
            dict(case='bad',material='another',input_accepted=False,metrics=dict(T_avg_Nm=-2,K_T_ripple_pct=None,P_Fe_W=None,eta_estimate_pct=None),input_source='report_only',input_error='missing CSV')]
        self.data=dict(id='v8_2_archive',version='V8_2_final_resolved',measurement_protocol='v8_2_cycle1',scope='saved engineering scope',
            rows=rows,references=[copy.deepcopy(rows[0])],source_hashes={'archive/one/Torque.csv':digest(self.source.read_bytes())})
        self.manager=SimpleNamespace(dataset=lambda _:self.data,motor=SimpleNamespace(archive_file=lambda case,name:self.source))

    def test_all_rows_and_reference_choices_and_accepted_subset_keep_original_meaning(self):
        before=copy.deepcopy(self.data);original=self.source.read_bytes()
        with patch('subprocess.Popen',side_effect=AssertionError('download must not start a process')):
            with zipfile.ZipFile(dataset_bundle(self.manager,self.data['id'])) as archive:
                rows=list(csv.DictReader(io.StringIO(archive.read('metrics.csv').decode('utf-8-sig'))))
                accepted=list(csv.DictReader(io.StringIO(archive.read('accepted_metrics.csv').decode('utf-8-sig'))))
                self.assertEqual([r['case'] for r in rows],['one','bad']);self.assertEqual(rows[1]['T_avg_Nm'],'-2')
                self.assertEqual(rows[1]['K_T_ripple_pct'],'');self.assertEqual([r['case'] for r in accepted],['one'])
                self.assertEqual(rows[1]['input_source'],'report_only');self.assertEqual(rows[1]['input_error'],'missing CSV')
                self.assertEqual(archive.read('sources/archive/one/Torque.csv'),original)
                self.assertIn('不能另算作独立实验',archive.read('README.md').decode())
                self.assertEqual(json.loads(archive.read('dataset.json')),self.data)
                snapshot=json.loads(archive.read('source_snapshot.json'))
                self.assertEqual(snapshot['row_count'],2);self.assertEqual(snapshot['reference_choice_count'],1)
                self.assertEqual(snapshot['new_analyses'],0)
                manifest=json.loads(archive.read('manifest.json'))
                self.assertEqual(set(archive.namelist())-{'manifest.json'},set(manifest['files']))
                for name,sha in manifest['files'].items():self.assertEqual(digest(archive.read(name)),sha)
        self.assertEqual(self.data,before);self.assertEqual(self.source.read_bytes(),original)

    def test_saved_v81_input_summary_is_packaged_without_claiming_missing_official_csv(self):
        self.data.update(id='v8_1',version='original',measurement_protocol='original_processed_metrics',
            source_hashes={'motor/v8_1/outputs/v8_1_material_comparison.json':digest(self.source.read_bytes())})
        self.manager.source_file=lambda source,name:self.source
        with zipfile.ZipFile(dataset_bundle(self.manager,'v8_1')) as archive:
            self.assertEqual(archive.read('sources/motor/v8_1/outputs/v8_1_material_comparison.json'),self.source.read_bytes())
            self.assertIn('不从其他版本补齐',archive.read('README.md').decode())

    def test_scan_sources_stay_in_selected_task_and_native_files_remain_outside_bundle(self):
        source=self.folder/'case/Torque.csv';source.parent.mkdir();source.write_bytes(self.source.read_bytes())
        native=self.folder/'case/motor.aedt';native.write_bytes(b'private native project')
        self.data.update(id='scan_0123456789ab',source_hashes={'case\\Torque.csv':digest(source.read_bytes()),'case/motor.aedt':digest(native.read_bytes())})
        self.manager.motor=SimpleNamespace(path=lambda _:self.folder)
        with zipfile.ZipFile(dataset_bundle(self.manager,self.data['id'])) as archive:
            self.assertIn('sources/case/Torque.csv',archive.namelist());self.assertNotIn('sources/case/motor.aedt',archive.namelist())
            snapshot=json.loads(archive.read('source_snapshot.json'));omitted=next(r for r in snapshot['sources'] if not r['included'])
            self.assertEqual(omitted['sha256'],digest(native.read_bytes()));self.assertIn('outside',omitted['omission_reason'])
            self.assertEqual(snapshot['sources'][0]['source'],'case\\Torque.csv')

    def test_scan_path_aliases_cannot_make_duplicate_extraction_members(self):
        source=self.folder/'case/Torque.csv';source.parent.mkdir();source.write_bytes(self.source.read_bytes())
        self.data.update(id='scan_0123456789ab',source_hashes={key:digest(source.read_bytes()) for key in ('case/Torque.csv','case\\Torque.csv')})
        self.manager.motor=SimpleNamespace(path=lambda _:self.folder)
        with self.assertRaisesRegex(ValueError,'成员重复'):dataset_bundle(self.manager,self.data['id'])

    def test_corrupted_original_cannot_be_exported_under_old_hash(self):
        self.source.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'原文件已改变'):dataset_bundle(self.manager,self.data['id'])

    def test_unsafe_source_paths_and_wrong_archive_or_v81_scope_are_rejected(self):
        for name in ('../outside.csv','C:/outside.csv','archive\\one\\Torque.csv','/absolute.csv','wrong/one/Torque.csv'):
            self.data['source_hashes']={name:digest(self.source.read_bytes())}
            with self.subTest(name=name),self.assertRaises(ValueError):dataset_bundle(self.manager,self.data['id'])
        self.data.update(id='v8_1',source_hashes={'motor/other.json':digest(self.source.read_bytes())})
        with self.assertRaisesRegex(ValueError,'固定输出'):dataset_bundle(self.manager,'v8_1')

    def test_wrong_dataset_identity_never_silently_downloads_another_version(self):
        with self.assertRaisesRegex(ValueError,'身份不符'):dataset_bundle(self.manager,'v8_1')

    def test_unknown_acceptance_is_not_in_the_accepted_subset_and_source_bytes_are_stable(self):
        self.data['rows'][0].pop('input_accepted')
        with zipfile.ZipFile(dataset_bundle(self.manager,self.data['id'])) as archive:
            self.assertEqual(list(csv.DictReader(io.StringIO(archive.read('accepted_metrics.csv').decode('utf-8-sig')))),[])
            self.assertEqual(len(list(csv.DictReader(io.StringIO(archive.read('metrics.csv').decode('utf-8-sig'))))),2)

    def test_v81_original_pass_flags_remain_but_processed_values_never_become_native_evidence(self):
        self.data.update(id='v8_1',source_hashes={'motor/v8_1/outputs/v8_1_material_comparison.json':digest(self.source.read_bytes())})
        self.manager.source_file=lambda *_:self.source
        with zipfile.ZipFile(dataset_bundle(self.manager,'v8_1')) as archive:
            self.assertEqual(len(list(csv.DictReader(io.StringIO(archive.read('accepted_metrics.csv').decode('utf-8-sig'))))),1)
            self.assertEqual(list(csv.DictReader(io.StringIO(archive.read('native_csv_metrics.csv').decode('utf-8-sig')))),[])
            usage=json.loads(archive.read('data_usage.json'))
            self.assertEqual(usage['kind'],'historical_processed_estimate');self.assertEqual(usage['native_csv_accepted_count'],0)
            self.assertIn('铁损估算',usage['note']);self.assertTrue(self.data['rows'][0]['input_accepted'])

    def test_native_subset_requires_explicit_official_source_and_original_pass(self):
        with zipfile.ZipFile(dataset_bundle(self.manager,self.data['id'])) as archive:
            native=list(csv.DictReader(io.StringIO(archive.read('native_csv_metrics.csv').decode('utf-8-sig'))))
            self.assertEqual([r['case'] for r in native],['one'])
        self.data['rows'][0].pop('input_source')
        with zipfile.ZipFile(dataset_bundle(self.manager,self.data['id'])) as archive:
            self.assertEqual(list(csv.DictReader(io.StringIO(archive.read('native_csv_metrics.csv').decode('utf-8-sig')))),[])


if __name__=='__main__':unittest.main()
