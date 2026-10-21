"""The unified entry never kills or silently reuses another workspace/version."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from flask import Flask

PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT))
from modules.system_runtime import (runtime_identity,same_runtime,choose_endpoint,source_manifest,port_is_free)
from tools.start_workbench import (choose_endpoint_with_hint,save_endpoint_hint,endpoint_hint_path)
from modules.workbench_routes import create_workbench


class RuntimeEntry(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)

    def identity(self):
        return runtime_identity(PROJECT,self.root,self.root/'records',cpu_only=True,auto_resume_queue=False)

    def test_frontend_asset_changes_enter_startup_identity(self):
        project=self.root/'program'
        asset=project/'static/js/waveforms.js'
        asset.parent.mkdir(parents=True)
        asset.write_text('version one',encoding='utf-8')
        files,first=source_manifest(project)
        self.assertIn('static/js/waveforms.js',files)
        asset.write_text('version two',encoding='utf-8')
        self.assertNotEqual(first,source_manifest(project)[1])

    def test_torque_worker_changes_enter_startup_identity(self):
        project=self.root/'program';worker=project/'tools/calibrated_motor_analysis_worker.py'
        worker.parent.mkdir(parents=True);worker.write_text('VERSION=1',encoding='utf8')
        files,first=source_manifest(project)
        self.assertIn('tools/calibrated_motor_analysis_worker.py',files)
        worker.write_text('VERSION=2',encoding='utf8')
        self.assertNotEqual(first,source_manifest(project)[1])

    def test_batch_worker_changes_enter_startup_identity(self):
        project=self.root/'program';worker=project/'tools/material_motor_batch_worker.py'
        worker.parent.mkdir(parents=True);worker.write_text('VERSION=1',encoding='utf8')
        files,first=source_manifest(project)
        self.assertIn('tools/material_motor_batch_worker.py',files)
        worker.write_text('VERSION=2',encoding='utf8')
        self.assertNotEqual(first,source_manifest(project)[1])

    def server(self,identity):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header('Content-Type','application/json')
                self.end_headers()
                self.wfile.write(json.dumps(identity).encode())
            def log_message(self,*args):
                pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server.server_port

    def test_reuse_requires_source_root_storage_and_resource_mode(self):
        expected=self.identity()
        self.assertTrue(same_runtime({**expected,'process_id':999},expected))
        for key,value in [('schema','old'),('source_fingerprint','old'),('root_path','other'),
            ('project_path','other'),('storage_path','other'),('cpu_only',False),('auto_resume_queue',True)]:
            self.assertFalse(same_runtime({**expected,key:value},expected),key)
        self.assertFalse(same_runtime({'samples':[1,2,3,4]},expected))

    def test_real_listener_is_reused_without_starting_another_server(self):
        expected=self.identity()
        port=self.server(expected)
        result=choose_endpoint(port,expected)
        self.assertTrue(result['reuse'])
        self.assertEqual(result['port'],port)

    def test_other_listener_is_preserved_and_another_port_selected(self):
        expected=self.identity()
        port=self.server({**expected,'cpu_only':False})
        with patch('modules.system_runtime.port_is_free',side_effect=[False,True]):
            result=choose_endpoint(port,expected)
        self.assertFalse(result['reuse'])
        self.assertEqual(result['port'],port+1)
        self.assertEqual(result['skipped_ports'],[port])
        with self.assertRaisesRegex(ValueError,'原进程保留'):
            choose_endpoint(port,expected,fallback=False)

    def test_code_edit_changes_identity_but_results_and_cache_do_not(self):
        project=self.root/'project'
        (project/'modules').mkdir(parents=True)
        (project/'modules/example.py').write_text('VERSION=1')
        first=runtime_identity(project,self.root,self.root/'records')
        (project/'data').mkdir()
        (project/'data/result.json').write_text('{}')
        self.assertEqual(first['source_fingerprint'],runtime_identity(project,self.root,self.root/'records')['source_fingerprint'])
        (project/'modules/example.py').write_text('VERSION=2')
        self.assertNotEqual(first['source_fingerprint'],runtime_identity(project,self.root,self.root/'records')['source_fingerprint'])
        self.assertEqual(len(source_manifest(project)[0]),1)

    def test_more_than_twenty_old_versions_still_find_a_free_port(self):
        expected=self.identity()
        with patch('modules.system_runtime.port_is_free',side_effect=[False]*20+[True]), \
                patch('modules.system_runtime.read_listener_identity',return_value=None):
            result=choose_endpoint(5001,expected)
        self.assertEqual(result['port'],5021)
        self.assertFalse(result['reuse'])
        self.assertEqual(result['skipped_ports'],list(range(5001,5021)))

    def test_matching_version_after_twenty_old_listeners_is_reused(self):
        expected=self.identity()
        with patch('modules.system_runtime.port_is_free',return_value=False), \
                patch('modules.system_runtime.read_listener_identity',side_effect=[None]*20+[expected]):
            result=choose_endpoint(5001,expected)
        self.assertEqual(result['port'],5021)
        self.assertTrue(result['reuse'])

    def test_invalid_port_and_exhausted_budget_are_explicit(self):
        for port in (0,65536,'5001'):
            with self.assertRaises(ValueError):
                choose_endpoint(port,self.identity())
        with patch('modules.system_runtime.port_is_free',return_value=False),patch('modules.system_runtime.read_listener_identity',return_value=None):
            with self.assertRaises(ValueError):
                choose_endpoint(5001,self.identity(),max_ports=2)

    def test_cli_reuses_matching_cpu_server_without_native_import(self):
        expected=self.identity()
        port=self.server(expected)
        proc=subprocess.run([sys.executable,'-X','utf8',str(PROJECT/'tools/start_workbench.py'),
            '--cpu-only','--port',str(port),'--root',str(self.root),'--storage',str(self.root/'records')],
            env={**os.environ,'MAGSIM_AUTO_RESUME_QUEUE':'0'},capture_output=True,text=True,timeout=20)
        self.assertEqual(proc.returncode,0,proc.stderr)
        self.assertIn('Reusing the same source version',proc.stdout)
        self.assertIn('/system',proc.stdout)
        self.assertFalse((self.root/'records').exists())

    def hint_identity(self):
        project=self.root/'program';project.mkdir(exist_ok=True)
        return project,runtime_identity(project,self.root,self.root/'records',cpu_only=True,auto_resume_queue=False)

    def test_matching_hint_reuses_later_server_despite_an_earlier_free_port(self):
        project,expected=self.hint_identity();self.assertTrue(save_endpoint_hint(project,expected,5005))
        before=endpoint_hint_path(project).read_bytes()
        with patch('tools.start_workbench.read_listener_identity',return_value=expected) as read, \
                patch('tools.start_workbench.choose_endpoint') as fallback:
            result=choose_endpoint_with_hint(project,5001,expected)
        self.assertEqual(result,dict(port=5005,reuse=True,skipped_ports=[]));read.assert_called_once_with(5005);fallback.assert_not_called()
        self.assertEqual(before,endpoint_hint_path(project).read_bytes());self.assertFalse((self.root/'records').exists())
        self.assertEqual(expected['source_fingerprint'],runtime_identity(project,self.root,self.root/'records')['source_fingerprint'])

    def test_hint_never_substitutes_another_source_workspace_storage_or_mode(self):
        project,expected=self.hint_identity();save_endpoint_hint(project,expected,5005)
        for key,value in [('schema','old'),('source_fingerprint','old'),('root_path','other'),
                ('project_path','other'),('storage_path','other'),('cpu_only',False),('auto_resume_queue',True)]:
            with self.subTest(key=key),patch('tools.start_workbench.read_listener_identity',return_value={**expected,key:value}), \
                    patch('tools.start_workbench.choose_endpoint',return_value=dict(port=5001,reuse=False,skipped_ports=[])) as fallback:
                self.assertFalse(choose_endpoint_with_hint(project,5001,expected)['reuse']);fallback.assert_called_once()

    def test_stale_or_missing_hint_falls_back_without_creating_a_record_directory(self):
        project,expected=self.hint_identity()
        with patch('tools.start_workbench.choose_endpoint',return_value=dict(port=5001,reuse=False,skipped_ports=[])) as fallback:
            self.assertEqual(choose_endpoint_with_hint(project,5001,expected)['port'],5001);fallback.assert_called_once()
        self.assertFalse(endpoint_hint_path(project).exists())
        save_endpoint_hint(project,expected,5005)
        with patch('tools.start_workbench.read_listener_identity',return_value=None), \
                patch('tools.start_workbench.choose_endpoint',return_value=dict(port=5001,reuse=False,skipped_ports=[])) as fallback:
            self.assertFalse(choose_endpoint_with_hint(project,5001,expected)['reuse']);fallback.assert_called_once()
        self.assertFalse((self.root/'records').exists())

    def test_malformed_hints_and_no_fallback_preserve_explicit_port_semantics(self):
        project,expected=self.hint_identity();save_endpoint_hint(project,expected,5005)
        original=json.loads(endpoint_hint_path(project).read_text());key=next(iter(original['entries']))
        for entry in ([5005],None,dict(port=True),dict(port='5005'),dict(port=70000),dict(port=4999)):
            data={**original,'entries':{key:entry}};endpoint_hint_path(project).write_text(json.dumps(data),encoding='utf8')
            with self.subTest(entry=entry),patch('tools.start_workbench.read_listener_identity') as read, \
                    patch('tools.start_workbench.choose_endpoint',return_value=dict(port=5001,reuse=False,skipped_ports=[])):
                self.assertFalse(choose_endpoint_with_hint(project,5001,expected)['reuse']);read.assert_not_called()
        endpoint_hint_path(project).write_text(json.dumps(original),encoding='utf8')
        with patch('tools.start_workbench.read_listener_identity') as read, \
                patch('tools.start_workbench.choose_endpoint',return_value=dict(port=5001,reuse=False,skipped_ports=[])) as fallback:
            choose_endpoint_with_hint(project,5001,expected,fallback=False);read.assert_not_called();fallback.assert_called_once_with(5001,expected,fallback=False)
        endpoint_hint_path(project).write_text('broken JSON',encoding='utf8')
        with patch('tools.start_workbench.choose_endpoint',return_value=dict(port=5001,reuse=False,skipped_ports=[])):
            self.assertFalse(choose_endpoint_with_hint(project,5001,expected)['reuse'])

    def test_cache_is_bounded_and_write_failure_does_not_block_launch_selection(self):
        project,expected=self.hint_identity()
        for i in range(40):self.assertTrue(save_endpoint_hint(project,{**expected,'root_path':str(self.root/str(i))},5001+i))
        path=endpoint_hint_path(project);self.assertEqual(len(json.loads(path.read_text())['entries']),32)
        self.assertEqual(len(list(path.parent.glob('*'))),1)
        before=path.read_bytes()
        with patch('tools.start_workbench.Path.mkdir',side_effect=OSError('cache unavailable')):
            self.assertFalse(save_endpoint_hint(project,expected,5001))
        self.assertEqual(before,path.read_bytes())

    def test_cli_hint_reuses_existing_server_after_gap_without_importing_app(self):
        project=self.root/'standalone program'
        for name in ('tools/start_workbench.py','modules/system_runtime.py'):
            dest=project/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(PROJECT/name,dest)
        expected=runtime_identity(project,self.root,self.root/'records',cpu_only=True,auto_resume_queue=False)
        port=self.server(expected);gap=next((p for p in range(max(1,port-8),port) if port_is_free(p)),None)
        if gap is None:self.skipTest('No earlier free port for the bounded local fixture')
        self.assertTrue(save_endpoint_hint(project,expected,port));before=endpoint_hint_path(project).read_bytes()
        result=subprocess.run([sys.executable,'-X','utf8',str(project/'tools/start_workbench.py'),
            '--cpu-only','--port',str(gap),'--root',str(self.root),'--storage',str(self.root/'records')],
            env={**os.environ,'MAGSIM_AUTO_RESUME_QUEUE':'0'},capture_output=True,text=True,encoding='utf8',timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr);self.assertIn('Reusing the same source version',result.stdout)
        self.assertIn('127.0.0.1:'+str(port)+'/system',result.stdout)
        self.assertFalse((project/'app.py').exists());self.assertFalse((self.root/'records').exists())
        self.assertEqual(before,endpoint_hint_path(project).read_bytes())

    def test_runtime_snapshot_and_manual_recovery_startup_policy(self):
        with patch.dict('os.environ',{'MAGSIM_CPU_ONLY':'0','MAGSIM_AUTO_RESUME_QUEUE':'0'}):
            with patch('modules.workbench_routes.MotorWorkbench.jobs',return_value=[{'status':'queued'}]),patch('modules.workbench_routes.MotorWorkbench.start_queue_monitor') as monitor:
                app=Flask('runtime',template_folder=str(PROJECT/'templates'))
                app.register_blueprint(create_workbench(PROJECT,self.root/'records',self.root))
                client=app.test_client()
                identity=client.get('/api/workbench/runtime').json
                self.assertFalse(identity['cpu_only'])
                self.assertFalse(identity['auto_resume_queue'])
                monitor.assert_not_called()
                page=client.get('/system')
                self.assertEqual(page.status_code,200)
                self.assertIn('/workbench#motor',page.text)
                self.assertIn('/workbench#cal',page.text)
                self.assertEqual(identity['source_fingerprint'],client.get('/api/workbench/runtime').json['source_fingerprint'])


if __name__=='__main__':
    unittest.main()
