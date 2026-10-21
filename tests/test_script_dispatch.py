"""Concurrent HTTP submissions/cancellation with fake workers, never native processes."""
import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
import app as web


class ScriptDispatchTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.script=self.root/'synthetic script.bat';self.script.write_text('rem never execute\n')
        self.started=[];self.start=Mock(side_effect=self.fake_start)
        for change in (patch.object(web,'running_jobs',{}),patch.object(web,'job_queue',[]),patch.object(web,'_script_requests',{}),patch.object(web,'_start_job',self.start)):
            change.start();self.addCleanup(change.stop)
        self.client=web.app.test_client()

    def fake_start(self,job):job['status']='starting';self.started.append(job['id'])
    def post(self,**extra):return self.client.post('/api/run-script',json={'filename':str(self.script),**extra})
    def record(self,name='synthetic'):return web._make_job(name,['never-execute'])
    def concurrent(self,payloads):
        barrier=threading.Barrier(len(payloads));responses=[]
        def call(payload):
            with web.app.test_client() as client:barrier.wait();responses.append(client.post('/api/run-script',json=payload))
        threads=[threading.Thread(target=call,args=(p,)) for p in payloads]
        for t in threads:t.start()
        for t in threads:t.join(4);self.assertFalse(t.is_alive())
        return responses

    def test_invalid_payloads_and_nonfiles_do_not_schedule(self):
        for payload in (None,[],{}, {'filename':[]},{'filename':''},{'filename':str(self.root)},{'filename':str(self.script),'request_id':'bad'}):
            response=self.client.post('/api/run-script',json=payload);self.assertIn(response.status_code,(400,404));self.assertTrue(response.json['error'])
        self.assertFalse(web.running_jobs);self.start.assert_not_called()

    def test_two_concurrent_requests_start_one_and_queue_one(self):
        results=self.concurrent([{'filename':str(self.script)}]*2)
        self.assertTrue(all(r.status_code==200 for r in results));self.assertEqual(self.start.call_count,1)
        self.assertEqual(sum(r.json['queued'] for r in results),1);self.assertEqual(len(web.job_queue),1)

    def test_repeated_request_returns_original_job(self):
        first=self.post(request_id='script_request_123').json;second=self.post(request_id='script_request_123').json
        self.assertEqual(first['job_id'],second['job_id']);self.assertTrue(second['reused']);self.assertEqual(self.start.call_count,1)

    def test_concurrent_same_request_schedules_once(self):
        results=self.concurrent([{'filename':str(self.script),'request_id':'script_request_123'}]*2)
        self.assertEqual({r.json['job_id'] for r in results},{self.started[0]});self.assertEqual(self.start.call_count,1);self.assertFalse(web.job_queue)

    def test_reusing_request_for_another_script_conflicts(self):
        self.post(request_id='script_request_123');other=self.root/'other.bat';other.write_text('rem never execute')
        response=self.client.post('/api/run-script',json={'filename':str(other),'request_id':'script_request_123'})
        self.assertEqual(response.status_code,409);self.assertEqual(self.start.call_count,1)

    def test_clearing_terminal_card_does_not_resubmit_same_request(self):
        first=self.post(request_id='script_request_123').json;job=web.running_jobs[first['job_id']];job.update(done=True,status='completed')
        self.assertEqual(self.client.post('/api/jobs/clear-done').json['cleared'],1)
        second=self.post(request_id='script_request_123').json
        self.assertEqual(first['job_id'],second['job_id']);self.assertTrue(second['reused']);self.assertEqual(self.start.call_count,1)
        self.assertEqual(self.client.get('/api/jobs/'+first['job_id']+'/status').json['code'],'job_not_found')

    def test_advance_waits_for_active_job(self):
        self.post();self.post();web._advance_queue();self.assertEqual(self.start.call_count,1);self.assertEqual(len(web.job_queue),1)

    def test_advance_skips_missing_or_cancelled_and_preserves_order(self):
        cancelled=self.record('cancelled');cancelled.update(status='stopped',done=True)
        first=self.record('first');second=self.record('second')
        for j in (cancelled,first,second):web.running_jobs[j['id']]=j
        web.job_queue[:]=['missing',cancelled['id'],first['id'],second['id']]
        web._advance_queue();self.assertEqual(self.started,[first['id']]);self.assertEqual(web.job_queue,[second['id']])

    def test_cancel_queued_job_removes_it_without_stopping_worker(self):
        self.post();queued=self.post().json['job_id'];response=self.client.post('/api/jobs/'+queued+'/stop')
        self.assertTrue(response.json['success']);self.assertNotIn(queued,web.job_queue)
        self.assertTrue(web.running_jobs[queued]['done']);self.assertEqual(web.running_jobs[queued]['status'],'stopped');self.assertEqual(self.start.call_count,1)

    def test_starting_without_process_reports_not_yet_stopped(self):
        jid=self.post().json['job_id'];response=self.client.post('/api/jobs/'+jid+'/stop')
        self.assertEqual(response.status_code,409);self.assertFalse(web.running_jobs[jid]['done'])

    def test_job_status_and_list_keep_origin_and_request_identity(self):
        response=self.post(request_id='script_request_123');job=web.running_jobs[response.json['job_id']]
        self.assertEqual(job['workspace_origin']['path'],str(Path.cwd().resolve()))
        self.assertEqual(job['cmd'],web._build_cmd(str(self.script.resolve())))
        for record in (self.client.get('/api/jobs/'+job['id']+'/status').json,self.client.get('/api/jobs').json['jobs'][0]):
            self.assertEqual(record['workspace_origin'],job['workspace_origin']);self.assertEqual(record['request_id'],'script_request_123')

    def test_done_flag_does_not_hide_a_still_running_process(self):
        job=self.record();job.update(status='stopped',done=True,process=SimpleNamespace(poll=lambda:None));web.running_jobs[job['id']]=job
        self.assertIs(web._active_job(),job);self.assertEqual(self.client.post('/api/jobs/clear-done').json['cleared'],0)
        self.post();self.start.assert_not_called();self.assertEqual(len(web.job_queue),1)

    def test_stop_signals_once_and_waits_for_actual_completion(self):
        job=self.record();proc=Mock();proc.poll.return_value=None;job.update(status='running',process=proc);web.running_jobs[job['id']]=job
        for _ in range(2):self.assertTrue(self.client.post('/api/jobs/'+job['id']+'/stop').json['success'])
        (proc.terminate if os.name=='nt' else proc.send_signal).assert_called_once();self.assertEqual(job['status'],'stopping');self.assertFalse(job['done'])

    def test_failed_stop_remains_retryable(self):
        job=self.record();proc=Mock();proc.poll.return_value=None;proc.terminate.side_effect=OSError('synthetic failure');proc.send_signal.side_effect=OSError('synthetic failure');job.update(status='running',process=proc);web.running_jobs[job['id']]=job
        self.assertEqual(self.client.post('/api/jobs/'+job['id']+'/stop').status_code,500);self.assertFalse(job['stop_requested']);self.assertFalse(job['done'])

    def test_reader_uses_saved_directory_and_preserves_stopped_state(self):
        job=self.record();job['workspace_origin']['path']=str(self.root);job['stop_requested']=True;done=threading.Event()
        proc=SimpleNamespace(stdout=iter([b'Global Progress: 1/1\n']),wait=lambda:None,returncode=0)
        with patch.object(web.subprocess,'Popen',return_value=proc) as launch,patch.object(web,'_advance_queue',side_effect=done.set):
            # The production reader thread runs; only process creation is a fake.
            with patch.object(web,'_start_job',self.original_start):web._start_job(job)
            self.assertTrue(done.wait(3));self.assertEqual(launch.call_args.kwargs['cwd'],str(self.root))
        self.assertEqual(job['status'],'stopped');self.assertTrue(job['done']);self.assertEqual(job['completed'],1)

    original_start=staticmethod(web._start_job)
