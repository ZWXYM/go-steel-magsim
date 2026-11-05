"""Durable history and API lifecycle tests; fake workers only."""
import hashlib
import io
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
import app as web
from modules.script_job_history import ScriptJobHistory,SCHEMA


class HistoryStorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)/'history'
        self.owner={'id':'fixture_A','pid':123};self.history=ScriptJobHistory(self.root,self.owner)
    def value(self,revision=1,status='queued',**extra):
        return dict(id='job_A',revision=revision,status=status,done=status in ('completed','failed','stopped'),owner=self.owner,script='synthetic.bat',workspace_origin={'path':'original_A','token':'a'*64},**extra)

    def test_missing_history_does_not_create_directories(self):
        self.assertEqual(self.history.rows()['records'],[]);self.assertFalse(self.root.exists())
    def test_original_owner_directory_status_and_revision_are_saved(self):
        self.history.save(self.value());saved=self.history.read('job_A');self.assertEqual(saved['schema'],SCHEMA);self.assertEqual(saved['owner'],self.owner);self.assertEqual(saved['workspace_origin']['path'],'original_A')
    def test_out_of_order_write_cannot_overwrite_later_state(self):
        self.history.save(self.value());gate=threading.Event();result=[]
        thread=threading.Thread(target=lambda:(gate.wait(2),result.append(self.history.save(self.value(2,'running')))));thread.start()
        self.history.save(self.value(3,'completed'));gate.set();thread.join(3);self.assertEqual(result,[False]);self.assertEqual(self.history.read('job_A')['revision'],3)
    def test_terminal_history_cannot_be_replayed(self):
        self.history.save(self.value(1,'completed'))
        with self.assertRaises(ValueError):self.history.save(self.value(2,'queued'))
    def test_new_owner_can_read_but_cannot_rewrite_or_append(self):
        self.history.save(self.value());other=ScriptJobHistory(self.root,{'id':'B'});before=(self.root/'job_A/state.json').read_bytes()
        value=self.value(2);value['owner']={'id':'B'}
        with self.assertRaises(ValueError):other.save(value)
        with self.assertRaises(ValueError):other.open_logs('job_A')
        self.assertFalse(other.view('job_A')['can_stop_here']);self.assertEqual((self.root/'job_A/state.json').read_bytes(),before)
    def test_atomic_replace_failure_keeps_previous_state(self):
        self.history.save(self.value());before=(self.root/'job_A/state.json').read_bytes()
        with patch('modules.script_job_history.os.replace',side_effect=OSError('synthetic interruption')):
            with self.assertRaises(OSError):self.history.save(self.value(2,'running'))
        self.assertEqual((self.root/'job_A/state.json').read_bytes(),before)
    def test_text_and_raw_logs_are_append_only(self):
        self.history.save(self.value());text,raw=self.history.open_logs('job_A');text.write('中文\n');raw.write(b'original\x1b[31m\n');text.close();raw.close()
        text,raw=self.history.open_logs('job_A');text.write('next\n');raw.write(b'next\n');text.close();raw.close()
        self.assertEqual(self.history.log_path('job_A','stdout.log').read_text(encoding='utf8'),'中文\nnext\n');self.assertEqual(self.history.log_path('job_A','stdout.raw').read_bytes(),b'original\x1b[31m\nnext\n')
    def test_bad_ids_and_unknown_log_names_are_rejected(self):
        for value in ('','../job','a/b','a\\b',None):
            with self.assertRaises(ValueError):self.history.folder(value)
        with self.assertRaises(ValueError):self.history.log_path('job_A','state.json')
    def test_corrupt_state_does_not_hide_other_records(self):
        self.history.save(self.value());bad=self.root/'job_B';bad.mkdir();(bad/'state.json').write_text('{bad')
        rows=self.history.rows()['records'];self.assertEqual(len(rows),2);self.assertEqual(sum('read_error' in row for row in rows),1)
    def test_nonfinite_or_wrong_identity_is_not_accepted(self):
        self.history.save(self.value());p=self.root/'job_A/state.json';p.write_text('{"x": NaN}')
        with self.assertRaises(ValueError):self.history.read('job_A')
        p.write_text(json.dumps(dict(schema=SCHEMA,id='another')))
        with self.assertRaises(ValueError):self.history.read('job_A')
    def test_bounded_state_read_and_catalog_limit_are_explicit(self):
        self.history.save(self.value());value=self.value();value['id']='job_B';self.history.save(value)
        with patch('modules.script_job_history.MAX_RECORDS',1):self.assertTrue(self.history.rows()['limit_reached'])
        with patch('modules.script_job_history.MAX_STATE_BYTES',16):
            with self.assertRaises(ValueError):self.history.read('job_A')
    def test_receipt_survives_corrupted_state_without_new_job(self):
        self.history.save(self.value(request_id='request_history_123',request_identity=['script','workspace'],total=2));(self.root/'job_A/state.json').write_text('{bad')
        receipt=self.history.receipt('request_history_123',('script','workspace'));self.assertEqual(receipt['id'],'job_A');self.assertEqual(receipt['total'],2)
    def test_request_identity_conflict_is_rejected(self):
        self.history.save(self.value(request_id='request_history_123',request_identity=['A','workspace']))
        with self.assertRaises(ValueError):self.history.receipt('request_history_123',('B','workspace'))
    def test_same_request_cannot_be_claimed_by_second_job(self):
        self.history.save(self.value(request_id='request_history_123',request_identity=['A','workspace']));other=self.value(request_id='request_history_123',request_identity=['A','workspace']);other['id']='job_B'
        with self.assertRaises(ValueError):self.history.save(other)
        self.assertFalse((self.root/'job_B/state.json').exists())
    def test_corrupt_receipt_is_preserved_and_not_replaced(self):
        self.history.save(self.value(request_id='request_history_123',request_identity=['A','workspace']));p=self.root/'_receipts/request_history_123.json';p.write_text('{bad');before=p.read_bytes()
        with self.assertRaises(ValueError):self.history.receipt('request_history_123',('A','workspace'))
        self.assertEqual(p.read_bytes(),before)


class HistoryAPITests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.script=self.root/'synthetic.bat';self.script.write_text('rem never execute\n')
        self.history=ScriptJobHistory(self.root/'history',web._script_instance);self.start=Mock(side_effect=lambda job:job.update(status='starting'))
        for change in (patch.object(web,'running_jobs',{}),patch.object(web,'job_queue',[]),patch.object(web,'_script_requests',{}),patch.object(web,'_script_journal_for',return_value=self.history),patch.object(web,'_start_job',self.start)):
            change.start();self.addCleanup(change.stop)
        self.client=web.app.test_client()
    def submit(self):return self.client.post('/api/run-script',json=dict(filename=str(self.script),request_id='request_history_123'))
    def archive(self,status='completed'):
        result=self.submit();self.assertEqual(result.status_code,200);job=web.running_jobs[result.json['job_id']];job.update(status=status,done=status in ('completed','failed','stopped'));web._record_script_job(job,required=True);return job

    def test_fresh_registry_lists_saved_history_without_recovering_queue(self):
        job=self.archive('running');web.running_jobs.clear();web._script_requests.clear()
        result=self.client.get('/api/jobs/history').json;self.assertEqual(result['records'][0]['id'],job['id']);self.assertFalse(result['records'][0]['can_stop_here']);self.assertEqual(web.job_queue,[]);self.assertEqual(self.start.call_count,1)
    def test_status_after_restart_retains_original_nonterminal_state(self):
        job=self.archive('running');web.running_jobs.clear();response=self.client.get('/api/jobs/'+job['id']+'/status').json
        self.assertTrue(response['read_only_history']);self.assertFalse(response['done']);self.assertEqual(response['status'],'running');self.assertEqual(response['worker_state'],'not_checked')
    def test_stop_cannot_control_history_from_another_service(self):
        job=self.archive('running');web.running_jobs.clear()
        with patch.object(web.subprocess,'Popen') as launch:
            response=self.client.post('/api/jobs/'+job['id']+'/stop');self.assertEqual(response.status_code,409);self.assertEqual(response.json['code'],'history_read_only');launch.assert_not_called()
    def test_repeating_request_after_restart_returns_original_receipt(self):
        job=self.archive();web.running_jobs.clear();web._script_requests.clear();response=self.submit()
        self.assertEqual(response.json['job_id'],job['id']);self.assertTrue(response.json['read_only_history']);self.assertEqual(self.start.call_count,1)
    def test_history_query_reads_disk_even_if_live_map_contains_same_id(self):
        job=self.archive();job['status']='synthetic_memory_only';response=self.client.get('/api/jobs/'+job['id']+'/status?history=1').json
        self.assertEqual(response['status'],'completed');self.assertTrue(response['read_only_history'])
    def test_bad_saved_record_is_visible_in_catalog(self):
        bad=self.root/'history/bad_job';bad.mkdir(parents=True);(bad/'state.json').write_text('{bad');response=self.client.get('/api/jobs/history')
        self.assertEqual(response.status_code,200);self.assertIn('read_error',response.json['records'][0])
    def test_metadata_write_failure_does_not_launch_script(self):
        with patch.object(self.history,'save',side_effect=OSError('synthetic read-only storage')):
            response=self.submit();self.assertEqual(response.status_code,500);self.start.assert_not_called()
    def test_queued_cancellation_is_saved_as_terminal_history(self):
        self.submit();queued=self.client.post('/api/run-script',json=dict(filename=str(self.script),request_id='request_history_456')).json['job_id'];self.assertTrue(self.client.post('/api/jobs/'+queued+'/stop').json['success'])
        self.assertEqual(self.history.read(queued)['status'],'stopped');self.assertTrue(self.history.read(queued)['done'])
    def test_workspace_history_isolation(self):
        self.archive();other=ScriptJobHistory(self.root/'other_history',web._script_instance)
        with patch.object(web,'_script_journal_for',return_value=other):self.assertEqual(self.client.get('/api/jobs/history').json['records'],[])
    def test_complete_reader_logs_raw_stdout_and_exit_status(self):
        job=web._make_job(str(self.script),['never-execute']);web.running_jobs[job['id']]=job;done=threading.Event();raw=b'\x1b[31mGlobal Progress: 1/1\n';proc=SimpleNamespace(stdout=iter([raw]),wait=lambda:None,returncode=0,pid=12345)
        with patch.object(web.subprocess,'Popen',return_value=proc),patch.object(web,'_advance_queue',side_effect=done.set),patch.object(web,'_start_job',self.original_start):
            self.assertTrue(web._start_job(job));self.assertTrue(done.wait(4))
        saved=self.history.read(job['id']);self.assertTrue(saved['done']);self.assertEqual(saved['status'],'completed');self.assertEqual(saved['completed'],1)
        token=web._workspace_token()
        with self.client.get('/api/jobs/'+job['id']+'/files/stdout.raw?workspace_token='+token) as response:self.assertEqual(response.data,raw)
        with self.client.get('/api/jobs/'+job['id']+'/files/stdout.log?workspace_token='+token) as response:self.assertEqual(response.data,b'Global Progress: 1/1\n')
    def test_download_requires_original_workspace_token(self):
        job=self.archive();self.assertEqual(self.client.get('/api/jobs/'+job['id']+'/files/stdout.log?workspace_token=wrong').status_code,409)
    def test_stream_uses_singular_line_field_and_keeps_completed_object(self):
        job=web._make_job(str(self.script),['never-execute']);job.update(done=True,status='completed',lines=['original line']);web.running_jobs[job['id']]=job
        with self.client.get('/api/jobs/'+job['id']+'/stream') as response:raw=response.data.decode()
        frames=[json.loads(line[6:]) for line in raw.splitlines() if line.startswith('data: ')];self.assertEqual(frames[0]['line'],'original line');self.assertTrue(frames[-1]['done'])
    def test_thread_start_failure_is_recorded_without_process_launch(self):
        job=web._make_job(str(self.script),['never-execute']);fake_thread=Mock();fake_thread.start.side_effect=RuntimeError('synthetic thread failure')
        with patch.object(web.threading,'Thread',return_value=fake_thread),patch.object(web.subprocess,'Popen') as launch:
            self.assertFalse(self.original_start(job));launch.assert_not_called()
        self.assertEqual(self.history.read(job['id'])['status'],'error');self.assertTrue(job['done'])

    def test_log_write_failure_does_not_skip_process_wait(self):
        job=web._make_job(str(self.script),['never-execute']);done=threading.Event();wait=Mock();chunks=[]
        text=SimpleNamespace(write=Mock(side_effect=OSError('synthetic disk error')),flush=lambda:None,close=lambda:None)
        raw=SimpleNamespace(write=chunks.append,flush=lambda:None,close=lambda:None)
        proc=SimpleNamespace(stdout=iter([b'original stdout\n']),wait=wait,returncode=0)
        with patch.object(self.history,'open_logs',return_value=(text,raw)),patch.object(web.subprocess,'Popen',return_value=proc),patch.object(web,'_advance_queue',side_effect=done.set):
            self.assertTrue(self.original_start(job));self.assertTrue(done.wait(4))
        wait.assert_called_once();self.assertEqual(chunks,[b'original stdout\n']);self.assertEqual(job['status'],'completed');self.assertIn('日志写入失败',self.history.read(job['id'])['journal_error'])

    def test_required_revision_conflict_never_launches_process(self):
        job=web._make_job(str(self.script),['never-execute']);web._record_script_job(job,required=True)
        old=self.history.read(job['id']);old['revision']=99;self.history.save(old);job['revision']=0
        with patch.object(web.subprocess,'Popen') as launch:
            self.assertFalse(self.original_start(job));launch.assert_not_called()
        self.assertEqual(self.history.read(job['id'])['revision'],99)

    original_start=staticmethod(web._start_job)
