"""Real HTTP lifecycle and background I/O in owned temporary workspaces only."""
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import app as web


class WorkspaceBindingTests(unittest.TestCase):
    def setUp(self):
        self.original=Path.cwd();self.temp=tempfile.TemporaryDirectory()
        self.a=Path(self.temp.name)/'中文A';self.b=Path(self.temp.name)/'B'
        self.a.mkdir();self.b.mkdir();os.chdir(self.a)
        self.patches=[patch.object(web,name,value) for name,value in dict(tasks={},running_jobs={},job_queue=[],_workspace_requests={},_ml_predictor=object(),_dataset_builder=object(),_pipeline_runner=None).items()]
        for p in self.patches:p.start()
        self.client=web.app.test_client()

    def tearDown(self):
        os.chdir(self.original)
        for p in reversed(self.patches):p.stop()
        self.temp.cleanup()

    def switch(self,**kw):return self.client.post('/api/workspace',json={'path':str(self.b)},**kw)

    def test_background_output_stays_in_original_directory(self):
        entered=threading.Event();release=threading.Event()
        def writer():
            entered.set();release.wait(5);Path('result.txt').write_text('synthetic')
        tid=web.create_task('synthetic');web.run_task(tid,writer)
        try:
            self.assertTrue(entered.wait(2));self.assertEqual(self.switch().status_code,409)
        finally:release.set()
        deadline=time.monotonic()+3
        while web.tasks[tid]['status']=='running' and time.monotonic()<deadline:time.sleep(.01)
        self.assertEqual(web.tasks[tid]['status'],'completed')
        self.assertTrue((self.a/'result.txt').is_file());self.assertFalse((self.b/'result.txt').exists())

    def test_idle_switch_clears_caches_and_preserves_files(self):
        (self.a/'keep.txt').write_text('original');response=self.switch()
        self.assertEqual(response.status_code,200);self.assertTrue(response.json['changed'])
        self.assertEqual(Path.cwd(),self.b)
        for name in ('_ml_predictor','_dataset_builder','_pipeline_runner'):self.assertIsNone(getattr(web,name))
        self.assertEqual((self.a/'keep.txt').read_text(),'original')

    def test_same_directory_preserves_cache_even_when_busy(self):
        cache=web._ml_predictor;web.tasks['t']={'id':'t','status':'running'}
        r=self.client.post('/api/workspace',json={'path':str(self.a)})
        self.assertFalse(r.json['changed']);self.assertIs(web._ml_predictor,cache)

    def test_native_jobs_and_pending_queue_block(self):
        for status in ('queued','pending','starting','running','stopping'):
            web.running_jobs['j']={'status':status};self.assertEqual(self.switch().status_code,409)
        web.running_jobs.clear();web.job_queue.append('pending');self.assertEqual(self.switch().status_code,409)

    def test_live_pipeline_blocks(self):
        for state in ({'status':'waiting_sim'},{'status':'completed','_thread':SimpleNamespace(is_alive=lambda:True)}):
            web._pipeline_runner=SimpleNamespace(pipelines={'p':state});self.assertEqual(self.switch().status_code,409)

    def test_completed_workers_allow_switch(self):
        web.tasks['t']={'id':'t','status':'completed'};web.running_jobs['j']={'status':'completed'}
        web._pipeline_runner=SimpleNamespace(pipelines={'p':{'status':'completed'}})
        self.assertEqual(self.switch().status_code,200)

    def test_inflight_relative_request_blocks_switch(self):
        entered=threading.Event();release=threading.Event();results=[]
        endpoint=web.app.url_map.bind('localhost').match('/api/dataset/list')[0]
        def reader():entered.set();release.wait(5);return web.jsonify(workspace=os.getcwd())
        def call():
            with web.app.test_client() as client:results.append(client.get('/api/dataset/list').json)
        with patch.dict(web.app.view_functions,{endpoint:reader}):
            thread=threading.Thread(target=call);thread.start()
            try:self.assertTrue(entered.wait(2));self.assertEqual(self.switch().status_code,409)
            finally:release.set();thread.join(3)
        self.assertEqual(results[0]['workspace'],str(self.a));self.assertFalse(web._workspace_requests)

    def test_old_page_rejected_and_chinese_path_has_ascii_token(self):
        token=self.client.get('/api/workspace').json['workspace_token']
        self.assertRegex(token,r'^[a-f0-9]{64}$');self.assertEqual(self.switch(headers={'X-Magsim-Workspace':token}).status_code,200)
        self.assertEqual(self.client.get('/api/dataset/list',headers={'X-Magsim-Workspace':token}).status_code,409)
        self.assertEqual(self.switch(headers={'X-Magsim-Workspace':token}).status_code,409)
        self.assertFalse(web._workspace_requests)

    def test_invalid_requests_preserve_directory_and_cache(self):
        cache=web._ml_predictor
        for value in (None,[],{}, {'path':5},{'path':''},{'path':str(self.b/'missing')}):
            self.assertEqual(self.client.post('/api/workspace',json=value).status_code,400)
        self.assertEqual(Path.cwd(),self.a);self.assertIs(web._ml_predictor,cache)

    def test_error_response_releases_request_lease(self):
        endpoint=web.app.url_map.bind('localhost').match('/api/dataset/list')[0]
        with patch.dict(web.app.view_functions,{endpoint:lambda:('failed',500)}):
            self.assertEqual(self.client.get('/api/dataset/list').status_code,500)
        self.assertFalse(web._workspace_requests);self.assertEqual(self.switch().status_code,200)

    def test_concurrent_changes_use_original_page_token(self):
        token=self.client.get('/api/workspace').json['workspace_token'];results=[]
        c=Path(self.temp.name)/'C';c.mkdir();barrier=threading.Barrier(2)
        def call(path):
            with web.app.test_client() as client:
                barrier.wait();results.append(client.post('/api/workspace',json={'path':str(path)},headers={'X-Magsim-Workspace':token}).status_code)
        threads=[threading.Thread(target=call,args=(p,)) for p in (self.b,c)]
        for t in threads:t.start()
        for t in threads:t.join(3)
        self.assertEqual(sorted(results),[200,409])

    def test_correct_token_read_and_absolute_workbench_exclusion(self):
        token=self.client.get('/api/workspace').json['workspace_token']
        endpoint=web.app.url_map.bind('localhost').match('/api/dataset/list')[0]
        with patch.dict(web.app.view_functions,{endpoint:lambda:web.jsonify(workspace=os.getcwd())}):
            self.assertEqual(self.client.get('/api/dataset/list',headers={'X-Magsim-Workspace':token}).status_code,200)
        self.assertNotEqual(self.client.get('/api/workbench/not-a-route',headers={'X-Magsim-Workspace':'old'}).status_code,409)
