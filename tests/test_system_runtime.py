"""The unified entry never kills or silently reuses another workspace/version."""
import json
import os
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
from modules.system_runtime import (runtime_identity,same_runtime,choose_endpoint,source_manifest)
from modules.workbench_routes import create_workbench


class RuntimeEntry(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)

    def identity(self):
        return runtime_identity(PROJECT,self.root,self.root/'records',cpu_only=True,auto_resume_queue=False)

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
