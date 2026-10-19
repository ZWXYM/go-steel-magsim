"""CPU preparation and synthetic process adapters; no real GPU/native solvers."""
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import os
from flask import Flask
import numpy as np
PROJECT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(PROJECT))
from modules.native_material_runs import NativeMaterialRuns,validate_recipe,verify_run,read
from modules.native_pair_exports import NativePairExports
from modules.texture_sampling import SAMPLING_VERSION
from modules.calibration_transfer import TransferBank
from modules.material_calibration import file_hash,MU0
from modules.motor_queue import DispatchWindow,process_identity
from modules.motor_workbench import write_json
from test_calibration_transfer import fixtures
from tools.run_calibration_pilot import H_GRID,run_jobs
from modules.workbench_routes import create_workbench
spec=importlib.util.spec_from_file_location('native_material_worker',PROJECT/'tools/native_material_run_worker.py')
worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)


class NativeRuns(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template=tempfile.TemporaryDirectory();root=Path(cls.template.name)
        bank=TransferBank.fit(fixtures(),SAMPLING_VERSION)
        cls.calibration='cal_aaaaaaaaaaaa';folder=root/'calibration'/cls.calibration;folder.mkdir(parents=True)
        write_json(folder/'bank.json',bank.payload);write_json(folder/'report.json',dict(bank_sha256=bank.bank_sha256))
        motor=SimpleNamespace(project=PROJECT,root=root,queue_status=lambda:dict(active_ownership=[],queued=0))
        cal=SimpleNamespace(get=lambda artifact:folder)
        manager=NativeMaterialRuns(cal,motor,NativePairExports(root,root/'storage/native_pair_exports'),root/'storage/native_material_runs')
        cls.recipe=dict(calibration_id=cls.calibration,material_id='SYNTHETIC_CPU',params=dict(f_Goss=.855,theta_0_deg=4.5,halfwidth_deg=6.5,Si_content=3.1),thickness_mm=.27,n_grains=1,seed=42)
        cls.record=manager.prepare(cls.recipe)
        if cls.record['status']!='ready':raise AssertionError(cls.record)

    @classmethod
    def tearDownClass(cls):cls.template.cleanup()

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.storage=self.root/'storage';shutil.copytree(Path(self.template.name)/'storage',self.storage)
        bank_folder=Path(self.template.name)/'calibration'/self.calibration
        self.motor=SimpleNamespace(project=PROJECT,root=self.root,queue_status=lambda:dict(active_ownership=[],queued=0))
        self.manager=NativeMaterialRuns(SimpleNamespace(get=lambda _:bank_folder),self.motor,NativePairExports(self.root,self.storage/'native_pair_exports'),self.storage/'native_material_runs')
        self.artifact=self.record['id'];self.folder=self.manager.path(self.artifact)
        self.binary=self.root/'fake_mumax.exe';self.binary.write_bytes(b'cpu_test_fixture_not_executable')

    def start_fixture(self):
        with patch('tools.run_calibration_pilot.resolve_mumax',return_value=self.binary),patch('modules.native_material_runs.psutil.process_iter',return_value=[]),patch('modules.native_material_runs.subprocess.Popen',return_value=SimpleNamespace(pid=987654)) as launch:
            record=self.manager.start(self.artifact);self.assertEqual(launch.call_count,1)
        return record['queue_token']

    def fake_job(self,run,manifest,**kwargs):
        self.assertEqual(kwargs['max_jobs'],1);self.assertGreater(kwargs['job_timeout_s'],0);self.assertLessEqual(kwargs['job_timeout_s'],20)
        status=read(run/'run_status.json') if (run/'run_status.json').exists() else dict(mumax_binary_sha256=file_hash(self.binary),jobs=[])
        done={j['script'] for j in status['jobs'] if j['exit_code']==0};job=next(j for j in manifest['jobs'] if j['script'] not in done)
        output=run/job['output'];output.mkdir(parents=True)
        direction=np.array([np.cos(np.deg2rad(job['angle'])),np.sin(np.deg2rad(job['angle'])),0]);rows=[]
        for branch in [-1,1]:
            grid=np.r_[-H_GRID[:0:-1],H_GRID];grid=grid[::-1] if branch==-1 else grid
            for h in grid:rows.append([0,*(direction*np.tanh(h/1000)),*(direction*h*MU0),branch])
        np.savetxt(output/'table.txt',rows,delimiter='\t',header='t (s)\tmx ()\tmy ()\tmz ()\tBx (T)\tBy (T)\tBz (T)\tbranch ()')
        status['jobs'].append(dict(job,exit_code=0,table_sha256=file_hash(output/'table.txt')))
        write_json(run/'run_status.json',status)

    def test_actual_isolated_cpu_preparation_freezes_two_scripts_without_native_outputs(self):
        m=verify_run(self.folder,PROJECT,dispatch=True);run=read(self.folder/'run/manifest.json')
        self.assertEqual(len(run['jobs']),2);self.assertEqual(run['texture_sampling_version'],SAMPLING_VERSION)
        self.assertFalse((self.folder/'run/run_status.json').exists());self.assertEqual(m['recipe'],self.recipe)
        self.assertEqual(self.manager.get(self.artifact)['new_MuMax_solves'],0)

    def test_parameter_errors_and_oversized_budget_are_rejected(self):
        for change in [dict(n_grains=9),dict(n_grains=True),dict(seed=-1),dict(thickness_mm=float('nan')),dict(material_id='../outside')]:
            with self.subTest(change=change),self.assertRaises(ValueError):validate_recipe(dict(self.recipe,**change))

    def test_changed_producer_or_frozen_input_blocks_start_and_preserves_record(self):
        manifest=read(self.folder/'manifest.json');manifest['producer_hashes']['modules/mx3_generator.py']='changed';write_json(self.folder/'manifest.json',manifest)
        self.assertFalse(self.manager.get(self.artifact)['producer_compatible'])
        with self.assertRaisesRegex(ValueError,'更新'):self.manager.start(self.artifact)
        self.assertEqual(read(self.folder/'state.json')['status'],'ready')

    def test_completed_mocked_run_exports_raw_pair_without_prediction(self):
        token=self.start_fixture()
        with patch.object(worker,'run_jobs',side_effect=self.fake_job):worker.execute(self.folder,token)
        record=self.manager.get(self.artifact);self.assertEqual(record['status'],'completed');self.assertEqual(record['new_MuMax_solves'],2)
        self.assertIn('native_pair=',record['prediction_url']);self.assertIn('calibration='+self.calibration,record['prediction_url'])
        pair=self.manager.exports.get(record['export_id'])['raw_pair'];self.assertEqual(pair['native_quality']['status'],'unassessed')
        self.assertFalse(list((self.storage/'calibration').glob('**/pred_*')))

    def test_pause_resume_keeps_completed_grain_and_accumulates_budget(self):
        token=self.start_fixture()
        def pause_after_first(*args,**kwargs):
            self.fake_job(*args,**kwargs);write_json(self.folder/'pause.request',dict(request=True))
        with patch.object(worker,'run_jobs',side_effect=pause_after_first):worker.execute(self.folder,token)
        first=self.manager.get(self.artifact);self.assertEqual(first['status'],'paused');self.assertEqual(first['completed_jobs'],1)
        digest=file_hash(self.folder/'run/run_status.json');token=self.start_fixture()
        self.assertEqual(file_hash(self.folder/'run/run_status.json'),digest)
        with patch.object(worker,'run_jobs',side_effect=self.fake_job) as run:worker.execute(self.folder,token)
        self.assertEqual(run.call_count,1);record=self.manager.get(self.artifact);self.assertEqual(record['status'],'completed')
        self.assertGreaterEqual(record['elapsed_seconds'],first['elapsed_seconds'])

    def test_exhausted_budget_does_not_create_new_launch_or_resume(self):
        write_json(self.folder/'state.json',dict(status='paused',elapsed_seconds=120,new_MuMax_solves=1))
        with self.assertRaisesRegex(ValueError,'预算'):self.manager.start(self.artifact)
        self.assertFalse((self.folder/'launches').exists())

    def test_failure_is_preserved_and_not_retried(self):
        token=self.start_fixture()
        with patch.object(worker,'run_jobs',side_effect=RuntimeError('fixture solve failed')),self.assertRaises(RuntimeError):worker.execute(self.folder,token)
        self.assertEqual(self.manager.get(self.artifact)['status'],'failed');self.assertTrue((self.folder/'failure.json').exists())
        with self.assertRaises(ValueError):self.manager.start(self.artifact)

    def test_active_material_run_blocks_other_native_dispatch_without_killing_anything(self):
        write_json(self.folder/'state.json',dict(status='running',new_MuMax_solves=0))
        with self.assertRaisesRegex(ValueError,'MuMax'):
            with DispatchWindow(self.storage):pass

    def test_existing_mumax_process_blocks_new_dispatch(self):
        process=SimpleNamespace(info={'name':'mumax3.exe'})
        with patch('modules.native_material_runs.psutil.process_iter',return_value=[process]),self.assertRaisesRegex(ValueError,'已有MuMax'):
            self.manager.start(self.artifact)
        self.assertEqual(read(self.folder/'state.json')['status'],'ready')

    def test_owned_child_timeout_is_recorded_and_remaining_jobs_are_not_run(self):
        run=self.folder/'run';manifest=read(run/'manifest.json');calls=[]
        def fake_process(argv,**kwargs):
            calls.append(argv);self.assertIn('timeout',kwargs)
            if argv[-1]=='-v':return SimpleNamespace(stdout='synthetic fixture')
            raise subprocess.TimeoutExpired(argv,kwargs['timeout'])
        with patch('tools.run_calibration_pilot.subprocess.run',side_effect=fake_process),self.assertRaises(RuntimeError):
            run_jobs(run,manifest,mumax=self.binary,job_timeout_s=1)
        status=read(run/'run_status.json');self.assertEqual(len(status['jobs']),1);self.assertEqual(status['jobs'][0]['exit_code'],124)
        self.assertEqual(len(calls),2);self.assertFalse((run/'runner.lock').exists())

    def test_cpu_inspection_of_dead_worker_preserves_original_state_and_never_retries(self):
        state=dict(status='running',queue_token='a'*32,worker_identity=dict(pid=987654,create_time=0),new_MuMax_solves=1)
        write_json(self.folder/'state.json',state)
        with patch('modules.native_material_runs.psutil.process_iter',return_value=[]):record=self.manager.reconcile(self.artifact)
        self.assertEqual(record['status'],'failed');self.assertEqual(record['new_MuMax_solves'],1)
        self.assertEqual(read(self.folder/'reconciliation.json')['previous_state'],state)
        self.assertFalse((self.folder/'run/run_status.json').exists())

    def test_cpu_inspection_refuses_alive_worker_or_any_active_mumax(self):
        write_json(self.folder/'state.json',dict(status='running',queue_token='a'*32,worker_identity=process_identity()))
        with self.assertRaises(ValueError):self.manager.reconcile(self.artifact)
        write_json(self.folder/'state.json',dict(status='starting',queue_token='a'*32))
        with patch('modules.native_material_runs.psutil.process_iter',return_value=[SimpleNamespace(info={'name':'mumax3.exe'})]),self.assertRaises(ValueError):self.manager.reconcile(self.artifact)
        self.assertEqual(read(self.folder/'state.json')['status'],'starting')

    def test_cpu_mode_allows_inspection_but_never_native_start(self):
        app=Flask(__name__)
        with patch.dict(os.environ,{'MAGSIM_CPU_ONLY':'1','MAGSIM_AUTO_RESUME_QUEUE':'0'}):
            app.register_blueprint(create_workbench(PROJECT,self.storage,self.root))
        client=app.test_client()
        self.assertEqual(client.post('/api/workbench/native-material-runs/'+self.artifact+'/start',json={}).status_code,423)
        self.assertEqual(client.post('/api/workbench/native-material-runs/'+self.artifact+'/reconcile',json={}).status_code,200)
        self.assertEqual(client.get('/api/workbench/native-material-runs/'+self.artifact).json['status'],'ready')


if __name__=='__main__':unittest.main()
