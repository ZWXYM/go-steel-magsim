"""Immutable package registration and version-separated, read-only inventories."""
import copy
import io
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask
from modules.system_data import WorkpackStore, SystemData, create_system_data_routes, json_bytes, sha256, verify_package

PROJECT = Path(__file__).resolve().parents[1]


def package(files=None, extra=None):
    files = files or {'README.md': '原材料和失败数据保留。'.encode('utf8'), 'curves.csv': b'H_A_per_m,B_T\r\n20,1.5\r\n'}
    manifest = dict(version='system_data_workpack_v2', private_only=True, files={key:sha256(value) for key,value in files.items()})
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as archive:
        for key,value in files.items(): archive.writestr(key,value)
        for key,value in (extra or {}).items(): archive.writestr(key,value)
        archive.writestr('manifest.json',json_bytes(manifest))
    return out.getvalue()


class SystemDataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='system data ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = WorkpackStore(self.root/'storage/system_data')
        self.source = self.root/'原数据包.zip'
        self.source.write_bytes(package())
        self.rows = [dict(input_accepted=True,input_source='official_CSV_verified'),
                     dict(input_accepted=False,input_source='report_only')]
        self.saved = dict(id='v8_1',version='V8_1_original_saved',measurement_protocol='original_processed_metrics',
                          scope='historical estimates',rows=copy.deepcopy(self.rows),source_hashes={})
        self.scan = dict(self.saved,id='scan_aaaaaaaaaaaa',version='V8_2_object_CS_loss_scope_v2',
                         measurement_protocol='v8_2_cycle1',scope='official CSV engineering scope')
        data = {row['id']:row for row in (self.saved,self.scan)}
        self.optimization = SimpleNamespace(datasets=lambda:[dict(id=key,title=key) for key in data],
                                            dataset=lambda key:data[key])
        self.data = SystemData(self.root/'storage',self.optimization)

    def test_original_kept_and_copy_reopens_after_original_location_changes_without_processes(self):
        before=self.source.read_bytes()
        with patch('subprocess.Popen',side_effect=AssertionError('No native work during data download')):
            record=self.store.register(self.source,'系统数据V2','冻结数据；不是实时重算。')
            self.assertEqual(self.source.read_bytes(),before)
            moved=self.source.with_name('原文件仍保留.zip');self.source.rename(moved)
            reopened,blob=WorkpackStore(self.store.storage).read(record['id'])
            self.assertEqual(blob,before);self.assertEqual(reopened,record)
            self.assertTrue(record['integrity_verified']);self.assertEqual(record['content_validation'],'manifest_integrity_only')
            self.assertTrue(record['private_only']);self.assertEqual(record['member_count'],3)
            self.assertEqual(moved.read_bytes(),before)

    def test_same_bytes_are_idempotent_and_original_title_and_metadata_are_not_replaced(self):
        first=self.store.register(self.source,'first')
        before={p.relative_to(self.store.storage).as_posix():p.read_bytes() for p in self.store.storage.rglob('*') if p.is_file()}
        again=self.store.register(self.source,'changed title')
        self.assertEqual(first,again)
        self.assertEqual(before,{p.relative_to(self.store.storage).as_posix():p.read_bytes() for p in self.store.storage.rglob('*') if p.is_file()})

    def test_changed_member_unlisted_file_and_duplicate_windows_paths_are_rejected(self):
        for blob in (package(extra={'unlisted.csv':b'no hash'}), package({'a.csv':b'a','A.csv':b'b'}),
                     package({'../outside.csv':b'a'}), package({'folder\\outside.csv':b'a'})):
            with self.subTest(blob=sha256(blob)),self.assertRaises(ValueError):verify_package(blob)
        out=io.BytesIO()
        with zipfile.ZipFile(out,'w') as archive:
            archive.writestr('curves.csv',b'changed')
            archive.writestr('manifest.json',json_bytes(dict(version='system_data_workpack_v2',private_only=True,files={'curves.csv':sha256(b'original')})))
        with self.assertRaisesRegex(ValueError,'哈希不符'):verify_package(out.getvalue())
        self.assertFalse(self.store.storage.exists())

    def test_nonzip_bad_manifest_and_oversize_do_not_create_registered_storage(self):
        for blob in (b'not a zip',package(extra={'not_in_manifest.txt':b'bad'})):
            with self.assertRaises(ValueError):verify_package(blob)
        with patch('modules.system_data.MAX_PACKAGE_BYTES',10):
            with self.assertRaisesRegex(ValueError,'大小限制'):self.store.register(self.source,'oversize')
        self.assertFalse(self.store.storage.exists())

    def test_corrupted_package_is_not_downloaded_and_one_bad_record_does_not_hide_valid_one(self):
        record=self.store.register(self.source,'good')
        bad=self.store.storage/'pack_ffffffffffffffff';bad.mkdir();(bad/'record.json').write_bytes(b'{broken')
        rows=self.store.records();self.assertEqual(sum(row['readable'] for row in rows),1)
        self.assertTrue(any(row.get('error') for row in rows))
        (self.store.folder(record['id'])/'package.zip').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'字节已改变'):self.store.read(record['id'])
        self.assertEqual(sum(row['readable'] for row in self.store.records()),0)

    def test_wrong_record_identity_and_arbitrary_paths_never_resolve_other_packages(self):
        record=self.store.register(self.source,'good')
        for artifact in ('../private','pack_aaaaaaaaaaaaaaaa/../../outside','no-package'):
            with self.assertRaises(ValueError):self.store.read(artifact)
        path=self.store.folder(record['id'])/'record.json'
        changed=dict(record,id='pack_aaaaaaaaaaaaaaaa');path.write_bytes(json_bytes(changed))
        with self.assertRaisesRegex(ValueError,'身份不符'):self.store.read(record['id'])

    def test_reading_empty_storage_does_not_create_it(self):
        before=self.source.read_bytes();snapshot=self.data.snapshot()
        self.assertEqual(snapshot['packages'],[]);self.assertEqual(snapshot['calibrations'],[])
        self.assertFalse((self.root/'storage').exists());self.assertEqual(before,self.source.read_bytes())

    def test_live_inventory_keeps_model_protocol_and_estimate_rows_separate_without_new_analysis(self):
        originals=copy.deepcopy((self.saved,self.scan))
        with patch('subprocess.Popen',side_effect=AssertionError('inventory must be read-only')):
            snapshot=self.data.snapshot()
        self.assertEqual(snapshot['counts'],dict(dataset_count=2,row_count=4,original_accepted_count=2,native_csv_accepted_count=1,complete=True))
        rows=snapshot['datasets'];self.assertEqual([row['data_usage']['native_csv_accepted_count'] for row in rows],[0,1])
        self.assertEqual([row['measurement_protocol'] for row in rows],['original_processed_metrics','v8_2_cycle1'])
        self.assertEqual((self.saved,self.scan),originals);self.assertEqual(snapshot['new_native_solves'],0)

    def test_unreadable_dataset_and_calibration_are_explicit_while_other_data_remains_usable(self):
        self.scan['id']='wrong dataset'
        folder=self.root/'storage/calibration/cal_bbbbbbbbbbbb';folder.mkdir(parents=True)
        (folder/'report.json').write_bytes(b'{broken')
        snapshot=self.data.snapshot();self.assertFalse(snapshot['counts']['complete'])
        self.assertEqual(snapshot['counts']['dataset_count'],1);self.assertEqual(snapshot['counts']['row_count'],2)
        self.assertFalse(snapshot['calibrations'][0]['readable']);self.assertFalse(snapshot['datasets'][1]['readable'])
        self.assertTrue(snapshot['datasets'][0]['readable'])

    def test_calibration_fit_and_whole_material_holdout_remain_distinct_and_original_report_unchanged(self):
        report=dict(id='cal_cccccccccccc',created_utc='2026-11-06',selected_grades=['B23R075','B27R090'],bank_sha256='a'*64,
            calibration_version='test_only',H_axis='physical_A_per_m',calibration_fit={'mean_rmse_T':.01},
            material_holdout={'mean_rmse_T':.04},evidence_status='processed reference',independently_validated=False)
        folder=self.root/'storage/calibration'/report['id'];folder.mkdir(parents=True);path=folder/'report.json';path.write_bytes(json_bytes(report))
        original=path.read_bytes();row=self.data.snapshot()['calibrations'][0]
        self.assertEqual(row['selected_grades'],report['selected_grades']);self.assertEqual(row['calibration_fit']['mean_rmse_T'],.01)
        self.assertEqual(row['material_holdout']['mean_rmse_T'],.04);self.assertFalse(row['independently_validated']);self.assertEqual(path.read_bytes(),original)

    def test_routes_download_exact_registered_bytes_and_live_index_without_native_environment(self):
        record=self.store.register(self.source,'系统数据V2')
        app=Flask('data routes',template_folder=str(PROJECT/'templates'))
        app.register_blueprint(create_system_data_routes(self.data))
        client=app.test_client();self.assertEqual(client.get('/system-data').status_code,200)
        before={p.relative_to(self.root).as_posix():p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        with patch('subprocess.Popen',side_effect=AssertionError('GET starts no process')):
            response=client.get('/api/workbench/system-data/packages/'+record['id']+'/download')
            self.assertEqual(response.status_code,200);self.assertEqual(response.data,self.source.read_bytes())
            self.assertIn(record['id']+'.zip',response.headers['Content-Disposition'])
            self.assertIn('no-store',response.headers['Cache-Control'])
            self.assertEqual(response.headers['ETag'],'"'+record['sha256']+'"')
            index=client.get('/api/workbench/system-data/index.json')
            self.assertEqual(index.status_code,200);self.assertEqual(index.json['packages'][0]['sha256'],record['sha256'])
            self.assertEqual(index.json['counts']['row_count'],4)
        self.assertEqual(before,{p.relative_to(self.root).as_posix():p.read_bytes() for p in self.root.rglob('*') if p.is_file()})

    def test_build_endpoint_validates_input_as_json_in_cpu_preview_without_native_dispatch(self):
        from modules.workbench_routes import create_workbench
        app=Flask('workbench data build',template_folder=str(PROJECT/'templates'))
        with patch.dict(os.environ,{'MAGSIM_CPU_ONLY':'1','MAGSIM_AUTO_RESUME_QUEUE':'0'}),patch('modules.workbench_routes.OptimizationPlans.start_monitor'):
            app.register_blueprint(create_workbench(PROJECT,self.root/'empty_storage',self.root))
        with patch('subprocess.Popen',side_effect=AssertionError('No solver dispatch')):
            response=app.test_client().post('/api/workbench/system-data/packages',json=dict(calibration=None,datasets=[],batch=None))
        self.assertEqual(response.status_code,400);self.assertIn('至少选择',response.json['error'])
        self.assertFalse((self.root/'empty_storage/system_data').exists())


if __name__=='__main__':unittest.main()
