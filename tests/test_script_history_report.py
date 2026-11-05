"""Problem reports include metadata from this workspace without reviving workers."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import app as web
from modules.script_job_history import ScriptJobHistory
from modules.system_report import SystemReport,report_bytes


class ScriptHistoryReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.owner=dict(id='original_A',pid=123,started_utc='saved')
        self.history=ScriptJobHistory(self.root/'A/data/script_jobs',self.owner)

    def save(self,job='job_A',status='running',**extra):
        value=dict(id=job,revision=2,status=status,done=status=='completed',owner=self.owner,
                   script='original.bat',workspace_origin=dict(path='original directory',token='a'*64),
                   recent_lines=['PRIVATE_STDOUT'],environment='PRIVATE_ENV',**extra)
        self.history.save(value)

    def report(self,provider=None):
        return SystemReport(self.root/'storage',lambda:{},lambda:{},provider or (lambda workspace:self.history.report_snapshot()))

    def test_empty_history_is_reported_without_creating_directories(self):
        value=self.history.report_snapshot()
        self.assertEqual(value['record_count'],0);self.assertFalse(self.history.root.exists())

    def test_saved_running_owner_and_origin_are_preserved_without_liveness_claim(self):
        self.save();value=self.history.report_snapshot();row=value['records'][0]
        self.assertEqual(row['status'],'running');self.assertFalse(row['done'])
        self.assertEqual(row['owner'],self.owner);self.assertEqual(row['workspace_origin']['path'],'original directory')
        self.assertEqual(row['worker_state'],'not_checked');self.assertFalse(row['can_stop_here'])

    def test_report_omits_stdout_and_extra_metadata_and_never_reads_log_files(self):
        self.save();log=self.history.root/'job_A/stdout.raw';log.write_bytes(b'PRIVATE_STDOUT')
        before={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in self.history.root.rglob('*') if p.is_file()}
        with patch.object(ScriptJobHistory,'view',side_effect=AssertionError('must not inspect logs')),patch.object(ScriptJobHistory,'log_path',side_effect=AssertionError('must not open logs')):
            value=self.history.report_snapshot()
        self.assertNotIn('PRIVATE',json.dumps(value))
        self.assertEqual(before,{p:hashlib.sha256(p.read_bytes()).hexdigest() for p in self.history.root.rglob('*') if p.is_file()})

    def test_bad_record_isolated_and_original_state_unchanged(self):
        self.save(status='completed');bad=self.history.root/'job_B';bad.mkdir();p=bad/'state.json';p.write_text('{bad');before=p.read_bytes()
        value=self.history.report_snapshot();self.assertEqual((value['record_count'],value['readable_count']),(2,1))
        self.assertIn('read_error',value['records'][1]);self.assertEqual(before,p.read_bytes())

    def test_stop_attention_error_is_reported_without_exposing_control_internals(self):
        self.save(status='needs_attention',process_control_error='SYNTHETIC_REMAINING_CHILD',stop_control=dict(private_control='PRIVATE_CONTROL'))
        value=self.history.report_snapshot();self.assertEqual(value['records'][0]['process_control_error'],'SYNTHETIC_REMAINING_CHILD')
        self.assertNotIn('PRIVATE_CONTROL',json.dumps(value))

    def test_record_limit_is_visible_and_does_not_remove_history(self):
        self.save();self.save('job_B')
        with patch('modules.script_job_history.MAX_RECORDS',1):value=self.history.report_snapshot()
        self.assertTrue(value['limit_reached']);self.assertEqual(value['record_count'],1)
        self.assertEqual(len(list(self.history.root.glob('*/state.json'))),2)

    def test_provider_failure_does_not_hide_workbench_groups(self):
        report=self.report(lambda workspace:(_ for _ in ()).throw(OSError('SYNTHETIC_UNAVAILABLE'))).snapshot()
        self.assertFalse(report['script_history']['ok']);self.assertIn('SYNTHETIC_UNAVAILABLE',report['script_history']['error'])
        self.assertEqual(len(report['record_groups']),8)

    def test_wrong_provider_schema_is_an_explicit_read_error(self):
        report=self.report(lambda workspace:dict(schema='wrong',records=[])).snapshot()
        self.assertFalse(report['script_history']['ok'])

    def test_report_digest_binds_script_metadata(self):
        self.save();report=self.report().snapshot();digest=report.pop('report_sha256')
        self.assertEqual(digest,hashlib.sha256(report_bytes(report)).hexdigest())
        report['script_history']['value']['records'][0]['status']='completed'
        self.assertNotEqual(digest,hashlib.sha256(report_bytes(report)).hexdigest())

    def test_workspace_captured_before_slower_providers_cannot_switch_history(self):
        requested=[];a=str(self.root/'A');b=str(self.root/'B');state={'cwd':a}
        report=self.report(lambda workspace:(requested.append(workspace) or dict(schema='script_job_report_v1',records=[])))
        report.license_status=lambda:(state.update(cwd=b) or {})
        with patch('os.getcwd',side_effect=lambda:state['cwd']):value=report.snapshot()
        self.assertEqual(value['workspace_at_capture'],a);self.assertEqual(requested,[a])

    def test_http_snapshot_uses_current_workspace_and_never_starts_jobs(self):
        self.save();other=self.root/'B';other.mkdir()
        with patch.dict(web._script_journals,clear=True),patch.object(web,'_start_job') as start,patch.object(web.subprocess,'Popen') as launch:
            with web.app.test_client() as client:
                with patch('os.getcwd',return_value=str(self.root/'A')):
                    a=client.get('/api/workbench/system-report');self.assertEqual(a.status_code,200)
                with patch('os.getcwd',return_value=str(other)):
                    b=client.get('/api/workbench/system-report');self.assertEqual(b.status_code,200)
                    self.assertEqual(client.post('/api/workbench/system-report',json={}).status_code,405)
            self.assertEqual(start.call_count,0);self.assertEqual(launch.call_count,0)
        first=a.json['script_history']['value'];second=b.json['script_history']['value']
        self.assertEqual(first['records'][0]['owner'],self.owner);self.assertEqual(second['record_count'],0)
        self.assertNotEqual(first['history_root'],second['history_root']);self.assertFalse((other/'data/script_jobs').exists())


if __name__=='__main__':unittest.main()
