"""Version mismatch does not dispatch, kill a process or hide existing work."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from flask import Flask,jsonify

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from modules.runtime_source_guard import install_source_guard,runtime_view,starts_new_work
from modules.system_runtime import runtime_identity


class SourceGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        (self.root/'modules').mkdir();(self.root/'app.py').write_text('# source v1')
        self.snapshot=runtime_identity(self.root,self.root,self.root/'storage',cpu_only=False,auto_resume_queue=False)
        self.app=Flask('source_guard');install_source_guard(self.app,self.snapshot);self.calls=[]
        @self.app.route('/api/workbench/calibrated-motor/executions/x/start',methods=['POST'])
        def start():self.calls.append('start');return jsonify(started=True)
        @self.app.route('/api/workbench/calibrated-motor/batches/x/pause',methods=['POST'])
        def pause():self.calls.append('pause');return jsonify(paused=True)
        @self.app.route('/api/workbench/calibrated-motor/batches',methods=['POST'])
        def prepare():self.calls.append('prepare');return jsonify(prepared=True)
        @self.app.route('/api/workbench/motor/jobs',methods=['GET'])
        def saved():return jsonify(records=['existing'])
        self.client=self.app.test_client()

    def test_unchanged_source_starts_and_snapshot_identity_remains_intact(self):
        info=runtime_view(self.snapshot);self.assertFalse(info['restart_required'])
        self.assertEqual(info['source_fingerprint'],self.snapshot['source_fingerprint'])
        self.assertEqual(self.client.post('/api/workbench/calibrated-motor/executions/x/start').status_code,200)
        self.assertEqual(self.calls,['start'])

    def test_changed_source_blocks_before_handler_but_allows_read_pause_and_cpu_prepare(self):
        (self.root/'modules/new.py').write_text('# new code')
        info=runtime_view(self.snapshot);self.assertTrue(info['restart_required'])
        self.assertNotEqual(info['source_fingerprint'],info['current_source_fingerprint'])
        response=self.client.post('/api/workbench/calibrated-motor/executions/x/start')
        self.assertEqual(response.status_code,409);self.assertEqual(response.json['code'],'source_version_changed')
        self.assertEqual(self.calls,[]);self.assertEqual(self.client.get('/api/workbench/motor/jobs').json,{'records':['existing']})
        self.assertEqual(self.client.post('/api/workbench/calibrated-motor/batches/x/pause').status_code,200)
        self.assertEqual(self.client.post('/api/workbench/calibrated-motor/batches').status_code,200)
        self.assertEqual(self.calls,['pause','prepare'])

    def test_unreadable_source_does_not_dispatch_or_replace_startup_version(self):
        with patch('modules.runtime_source_guard.source_manifest',side_effect=OSError('fixture unreadable')):
            info=runtime_view(self.snapshot);self.assertTrue(info['restart_required']);self.assertIn('unreadable',info['source_read_error'])
            self.assertEqual(self.client.post('/api/workbench/calibrated-motor/executions/x/start').status_code,409)
        self.assertEqual(self.calls,[])

    def test_newly_loaded_matching_version_can_start_without_closing_previous_app(self):
        (self.root/'app.py').write_text('# source v2')
        newer=runtime_identity(self.root,self.root,self.root/'storage',cpu_only=False,auto_resume_queue=False)
        self.assertTrue(runtime_view(self.snapshot)['restart_required']);self.assertFalse(runtime_view(newer)['restart_required'])
        self.assertEqual(self.client.get('/api/workbench/motor/jobs').status_code,200)

    def test_workbench_runtime_and_resources_expose_staleness_and_guard_real_route(self):
        import os
        from modules.workbench_routes import create_workbench
        from modules.calibrated_motor_execution import CalibratedMotorExecution
        app=Flask('guard_workbench')
        with patch.dict(os.environ,{'MAGSIM_CPU_ONLY':'0','MAGSIM_AUTO_RESUME_QUEUE':'0'}):
            app.register_blueprint(create_workbench(self.root,self.root/'workbench_storage',self.root))
        c=app.test_client();self.assertFalse(c.get('/api/workbench/runtime').json['restart_required'])
        (self.root/'app.py').write_text('# source v3')
        self.assertTrue(c.get('/api/workbench/runtime').json['restart_required'])
        self.assertFalse(c.get('/api/workbench/resources').json['native_submission_allowed'])
        with patch.object(CalibratedMotorExecution,'start',side_effect=AssertionError('must not dispatch')) as start:
            self.assertEqual(c.post('/api/workbench/calibrated-motor/executions/x/start',json={}).status_code,409)
            start.assert_not_called()
        self.assertEqual(c.post('/api/workbench/calibrated-motor/batches',json={}).status_code,400)

    def test_native_paths_include_queue_training_and_pipeline_but_exclude_stop_and_reports(self):
        for path in ['/api/ml/train','/api/run-script','/api/pipeline/start','/api/pipeline/x/resume',
                     '/api/workbench/motor/queue/recover','/api/workbench/motor/jobs/x/submit',
                     '/api/workbench/calibrated-motor/batches/x/resume','/api/workbench/optimization/plans/x/submit']:
            self.assertTrue(starts_new_work(path),path)
        for path in ['/api/jobs/x/stop','/api/pipeline/x/terminate','/api/workbench/motor/jobs/x/export',
                     '/api/workbench/calibrated-motor/batches/x/pause','/api/workbench/calibrate']:
            self.assertFalse(starts_new_work(path),path)


if __name__=='__main__':unittest.main()
