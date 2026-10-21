"""Saved-state reports remain read-only and isolate malformed records/providers."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from flask import Flask
from modules.system_report import SystemReport,report_bytes,create_system_report_routes


class SystemReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)/'storage'
        self.report=SystemReport(self.root,lambda:dict(project_path='project',source_fingerprint='abc',source_matches_startup=True,restart_required=False,cpu_only=False),lambda:dict(available=True,scope='TCP only'))

    def write(self,name,value):
        p=self.root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(value,ensure_ascii=False),encoding='utf8');return p

    def group(self,key):return next(g for g in self.report.snapshot()['record_groups'] if g['key']==key)

    def test_saved_statuses_counts_and_raw_files_are_preserved(self):
        a=self.write('motor/scan_a/state.json',dict(id='scan_a',status='completed',cases=[{},{}]));b=self.write('calibrated_motor_batches/bhbatch_a/state.json',dict(status='failed',items=[{}],error='synthetic failure'));before={p:p.read_bytes() for p in (a,b)}
        report=self.report.snapshot('http://local/');self.assertEqual(report['web_address'],'http://local/')
        self.assertEqual(self.group('motor')['records'][0]['cases_count'],2);self.assertEqual(self.group('batches')['records'][0]['status'],'failed')
        self.assertEqual(before,{p:p.read_bytes() for p in (a,b)})

    def test_one_corrupt_record_does_not_hide_valid_records(self):
        a=self.write('motor/scan_a/state.json',dict(status='completed'));b=self.write('motor/scan_b/state.json',{});b.write_bytes(b'{broken')
        group=self.group('motor');self.assertEqual(group['record_count'],2);self.assertEqual(group['readable_count'],1);bad=group['records'][1]
        self.assertFalse(bad['readable']);self.assertEqual(bad['state_file_sha256'],hashlib.sha256(b.read_bytes()).hexdigest());self.assertIn('read_error',bad)

    def test_missing_storage_is_not_created(self):
        self.assertFalse(self.root.exists());report=self.report.snapshot();self.assertFalse(self.root.exists())
        self.assertTrue(all(g['record_count']==0 for g in report['record_groups']))

    def test_oversized_record_is_bounded_and_not_given_a_full_file_hash(self):
        p=self.write('motor/scan_a/state.json',{});p.write_bytes(b'x'*33)
        with patch('modules.system_report.MAX_BYTES',32):row=self.group('motor')['records'][0]
        self.assertFalse(row['readable']);self.assertEqual(row['bytes_read'],33);self.assertIsNone(row['state_file_sha256'])

    def test_unicode_and_nonfinite_input_have_explicit_states(self):
        self.write('motor/scan_a/state.json',dict(status='已保存',error='测试错误'))
        p=self.write('motor/scan_b/state.json',{});p.write_text('{"status": NaN}',encoding='utf8')
        rows=self.group('motor')['records'];self.assertEqual(rows[0]['status'],'已保存');self.assertFalse(rows[1]['readable']);self.assertIn('非有限',rows[1]['read_error'])

    def test_provider_failures_do_not_block_saved_state_report(self):
        self.report.runtime=lambda:(_ for _ in ()).throw(OSError('synthetic unavailable'))
        report=self.report.snapshot();self.assertFalse(report['runtime']['ok']);self.assertTrue(report['license_TCP']['ok']);self.assertEqual(len(report['record_groups']),8)

    def test_report_includes_selected_fields_without_loading_native_cache(self):
        self.write('motor/scan_a/state.json',dict(id='scan_a',status='failed',private_data='not included',raw_log='not included'))
        self.write('motor/scan_a/cache/state.json',dict(id='not enumerated'))
        self.report.license_status=lambda:dict(available=True,checked_servers=['not included'],source='configured')
        report=self.report.snapshot();self.assertNotIn('not included',json.dumps(report));self.assertEqual(self.group('motor')['record_count'],1)

    def test_summary_hash_binds_the_complete_display_snapshot(self):
        report=self.report.snapshot();digest=report.pop('report_sha256');self.assertEqual(digest,hashlib.sha256(report_bytes(report)).hexdigest())

    def test_limit_is_explicit_without_removing_saved_records(self):
        for name in ('a','b'):self.write('motor/scan_'+name+'/state.json',dict(status='completed'))
        with patch('modules.system_report.MAX_RECORDS',1):group=self.group('motor')
        self.assertTrue(group['limit_reached']);self.assertEqual(group['record_count'],1);self.assertEqual(len(list(self.root.glob('motor/*/state.json'))),2)

    def test_http_routes_are_get_only_and_show_the_report_entry(self):
        app=Flask(__name__,template_folder=str(Path(__file__).resolve().parents[1]/'templates'));app.register_blueprint(create_system_report_routes(self.report))
        with app.test_client() as client:
            response=client.get('/api/workbench/system-report');self.assertEqual(response.status_code,200);self.assertEqual(response.json['web_address'],'http://localhost/')
            self.assertEqual(client.post('/api/workbench/system-report',json={}).status_code,405);self.assertIn(b'/static/js/system_report.js',client.get('/system-report').data)
