"""CPU persistence, source binding and batch admission. No real native calls."""
import copy
import io
import json
import os
import unittest
import zipfile
from unittest.mock import patch
from flask import Flask

import test_material_motor_chain as fixtures
from modules.material_motor_batches import MaterialMotorBatches, read
from modules.motor_queue import DispatchWindow, process_identity
from modules.motor_workbench import write_json
from modules.material_library import sha
from modules.workbench_routes import create_workbench


class MaterialBatchTests(unittest.TestCase):
    initial=fixtures.MaterialChainTests.initial
    @classmethod
    def setUpClass(cls):fixtures.MaterialChainTests.setUpClass.__func__(cls)
    @classmethod
    def tearDownClass(cls):fixtures.MaterialChainTests.tearDownClass.__func__(cls)
    def setUp(self):
        fixtures.MaterialChainTests.setUp(self)
        self.batches=MaterialMotorBatches(self.chain,self.store/'calibrated_motor_batches')
        self.request=dict(materials=[dict(calibration=self.cal,key=self.key)],cores=2,measurement_protocol='v8_2_cycle1')

    def prepare(self):
        with patch('subprocess.Popen',side_effect=AssertionError('CPU preparation cannot launch')):
            return self.batches.prepare(self.request)

    def test_two_materials_prepare_sources_and_budget_without_native(self):
        self.request['materials'].append(dict(calibration=self.cal,key='fit:B23R075'))
        batch=self.prepare()
        self.assertEqual(batch['totals']['materials'],2)
        self.assertEqual(batch['budget']['max_new_imports'],2)
        self.assertEqual(batch['budget']['max_new_motor_solves'],2)
        self.assertEqual(batch['budget']['gpus'],0)
        self.assertEqual(batch['status'],'prepared')
        self.assertEqual(len(self.prep.records()),2)
        self.assertFalse((self.batches.storage/'active_controller.json').exists())
        self.assertFalse(batch['efficiency_enabled'])
        self.assertEqual(self.motor.jobs(),[])

    def test_repeat_batch_reopens_same_identity_and_does_not_duplicate_children(self):
        first=self.prepare();before=fixtures.MaterialChainTests.files(self)
        second=self.prepare()
        self.assertEqual(first['id'],second['id'])
        self.assertTrue(second['existing_batch_reused'])
        self.assertEqual(before,fixtures.MaterialChainTests.files(self))
        self.assertEqual(len(self.batches.records()),1)

    def test_invalid_selections_settings_and_duplicate_materials_are_rejected(self):
        for request in (None,{},dict(materials=[]),dict(self.request,materials=self.request['materials']*2),
                        dict(self.request,cores=3),dict(self.request,cores=True),
                        dict(self.request,measurement_protocol='unknown'),dict(self.request,start=True)):
            with self.assertRaises(ValueError):self.batches.prepare(request)
        self.assertEqual(self.batches.records(),[])

    def test_budget_or_source_tamper_stops_reopen(self):
        batch=self.prepare();path=self.batches.path(batch['id'])/'manifest.json';original=read(path)
        for corrupt in ('budget','source'):
            manifest=copy.deepcopy(original)
            if corrupt=='budget':manifest['budget']['gpus']=1
            else:manifest['items'][0]['source']['metadata_sha256']='changed'
            write_json(path,manifest)
            with self.assertRaises(ValueError):self.batches.get(batch['id'])
        write_json(path,original)

    def test_cursor_cannot_switch_frozen_native_or_plan(self):
        batch=self.prepare();path=self.batches.path(batch['id'])/'state.json';state=read(path)
        state['items'][0]['records']['native_import']['id']='calimport_0123456789ab';write_json(path,state)
        with self.assertRaisesRegex(ValueError,'原记录ID改变'):self.batches.get(batch['id'])

    def test_read_and_bundle_do_not_create_stages_or_dispatch(self):
        batch=self.prepare();before=fixtures.MaterialChainTests.files(self)
        with patch('subprocess.Popen',side_effect=AssertionError('read cannot launch')):
            reopened=MaterialMotorBatches(self.chain,self.batches.storage).get(batch['id'])
            with zipfile.ZipFile(self.batches.bundle(batch['id'])) as archive:
                self.assertEqual(set(archive.namelist()),{'batch.json','summary.csv','manifest.json'})
                manifest=json.loads(archive.read('manifest.json'))
                for name,digest in manifest['files'].items():self.assertEqual(sha(archive.read(name)),digest)
        self.assertEqual(reopened['totals']['actual_torque_results'],0)
        self.assertEqual(before,fixtures.MaterialChainTests.files(self))

    def test_cpu_mode_rejects_start_without_native_trace(self):
        batch=self.prepare();before=fixtures.MaterialChainTests.files(self)
        with patch.dict(os.environ,{'MAGSIM_CPU_ONLY':'1'}),patch('subprocess.Popen') as launch:
            with self.assertRaisesRegex(ValueError,'CPU预览'):self.batches.start(batch['id'])
        launch.assert_not_called();self.assertEqual(before,fixtures.MaterialChainTests.files(self))

    def test_controller_launch_once_binds_reservation_without_starting_native(self):
        batch=self.prepare();folder=self.batches.path(batch['id'])
        with patch('modules.material_motor_batches.subprocess.Popen') as launch:
            launch.return_value.pid=os.getpid();record=self.batches.start(batch['id'])
            self.assertEqual(record['status'],'starting')
            with self.assertRaises(ValueError):self.batches.start(batch['id'])
        self.assertEqual(launch.call_count,1)
        owner=read(self.batches.storage/'active_controller.json')
        self.assertEqual(owner['batch_id'],batch['id'])
        self.assertEqual(owner['identity'],process_identity())
        self.assertFalse((self.native.path(batch['items'][0]['records']['native_import']['id'])/'dispatch.json').exists())
        self.assertEqual(read(folder/'state.json')['new_execution_ids'],[])

    def test_launch_failure_is_visible_and_preserves_cpu_plan(self):
        batch=self.prepare()
        with patch('modules.material_motor_batches.subprocess.Popen',side_effect=OSError('mock unavailable')):
            with self.assertRaisesRegex(ValueError,'下发未确认'):self.batches.start(batch['id'])
        self.assertEqual(self.batches.get(batch['id'])['status'],'needs_attention')
        self.assertFalse((self.batches.storage/'active_controller.json').exists())

    def test_dead_controller_can_only_be_explicitly_resumed_and_old_owner_is_kept(self):
        batch=self.prepare();folder=self.batches.path(batch['id'])
        with patch('modules.material_motor_batches.subprocess.Popen') as launch:
            launch.return_value.pid=os.getpid();self.batches.start(batch['id'])
        owner=read(folder/'controller.json');owner['identity']=dict(pid=987654,create_time=0)
        write_json(folder/'controller.json',owner);write_json(self.batches.storage/'active_controller.json',owner)
        self.assertEqual(self.batches.get(batch['id'])['status'],'needs_attention')
        with patch('modules.material_motor_batches.subprocess.Popen') as launch:
            launch.return_value.pid=os.getpid()
            with self.assertRaises(ValueError):self.batches.start(batch['id'])
            resumed=self.batches.start(batch['id'],resume=True)
        self.assertEqual(resumed['status'],'starting')
        self.assertEqual(len(list((folder/'controller_history').glob('*.json'))),2)

    def test_batch_reservation_blocks_native_torque_and_library_scan_submissions(self):
        self.prepare();reservation=DispatchWindow(self.store).reservation
        write_json(reservation,dict(batch_id='bhbatch_0123456789ab',identity=dict(pid=987654,create_time=0)))
        with patch('modules.motor_queue.identity_status',return_value='alive'),patch('subprocess.Popen') as launch:
            for action in (lambda:self.native.start('calimport_0123456789ab'),
                           lambda:self.execution.start('bhexec_0123456789ab'),
                           lambda:self.motor.submit('scan_0123456789ab')):
                with self.assertRaisesRegex(ValueError,'批量任务占用'):action()
        launch.assert_not_called()

    def test_later_saved_execution_is_discovered_without_changing_batch_cursor(self):
        batch=self.prepare();snapshot=fixtures.MaterialChainTests.model_ready(self)
        run=self.execution.get(snapshot['records']['execution']['id'])
        run.update(status='completed',result_available=True,result=dict(model_version='fixture_BH',
            metrics=dict(T_avg_Nm=0,T_min_Nm=0,T_max_Nm=0,K_T_ripple_pct=None)))
        path=self.batches.path(batch['id'])/'state.json';before=path.read_bytes()
        with patch.object(self.execution,'records',return_value=[run]):
            reopened=self.batches.get(batch['id'])
        self.assertEqual(reopened['totals']['actual_torque_results'],1)
        self.assertEqual(reopened['items'][0]['metrics']['T_avg_Nm'],0)
        self.assertIsNone(reopened['items'][0]['metrics']['K_T_ripple_pct'])
        self.assertEqual(path.read_bytes(),before)

    def test_pause_prepared_batch_is_cpu_only_and_preserves_stage_ids(self):
        batch=self.prepare();before=batch['items'][0]['records']
        with patch('subprocess.Popen') as launch:paused=self.batches.pause(batch['id'])
        launch.assert_not_called();self.assertEqual(paused['status'],'paused')
        self.assertTrue(paused['pause_requested']);self.assertEqual(paused['items'][0]['records'],before)

    def test_pause_request_for_running_controller_does_not_close_or_kill(self):
        batch=self.prepare()
        with patch('modules.material_motor_batches.subprocess.Popen') as launch:
            launch.return_value.pid=os.getpid();self.batches.start(batch['id'])
        with patch('subprocess.Popen') as launch:paused=self.batches.pause(batch['id'])
        launch.assert_not_called();self.assertEqual(paused['status'],'starting')
        self.assertTrue(paused['pause_requested'])

    def test_cpu_http_prepare_reopen_bundle_and_pause_but_start_resume_blocked(self):
        app=Flask('batch_cpu')
        with patch.dict(os.environ,{'MAGSIM_CPU_ONLY':'1','MAGSIM_AUTO_RESUME_QUEUE':'0'}):
            app.register_blueprint(create_workbench(self.motor.project,self.store,self.root))
        c=app.test_client();url='/api/workbench/calibrated-motor/batches'
        with patch('subprocess.Popen',side_effect=AssertionError('CPU route cannot launch')):
            first=c.post(url,json=self.request);self.assertEqual(first.status_code,200,first.json)
            second=c.post(url,json=self.request);self.assertEqual(second.json['id'],first.json['id'])
            base=url+'/'+first.json['id']
            self.assertEqual(c.get(base).status_code,200)
            self.assertEqual(c.post(base+'/start',json={}).status_code,423)
            self.assertEqual(c.post(base+'/pause',json={}).json['status'],'paused')
            self.assertEqual(c.post(base+'/resume',json={}).status_code,423)
            response=c.get(base+'/bundle');self.assertEqual(response.status_code,200);response.close()
            response=c.get('/calibrated-motor/batches');self.assertEqual(response.status_code,200);response.close()
            before=fixtures.MaterialChainTests.files(self)
            comparison=c.get(base+'/comparison?baseline=item_01')
            self.assertEqual(comparison.status_code,200,comparison.json)
            self.assertEqual(comparison.json['baseline_item'],'item_01')
            self.assertTrue(all(e['comparison_status']=='not_comparable' for e in comparison.json['entries']))
            response=c.get(base+'/comparison?baseline=item_01&format=csv')
            self.assertEqual(response.status_code,200);self.assertIn(b'delta_T_avg_Nm',response.data);response.close()
            response=c.get(base+'/comparison?baseline=item_01&format=zip')
            self.assertEqual(response.status_code,200)
            with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
                self.assertEqual(json.loads(archive.read('manifest.json'))['baseline_item'],'item_01')
                self.assertIn('saved_batch_report/batch.json',archive.namelist())
            response.close()
            for query in ('','?baseline=unknown','?baseline=item_01&format=exe','?baseline=item_01&format=csv&format=json','?baseline=item_01&other=1'):
                self.assertEqual(c.get(base+'/comparison'+query).status_code,400,query)
            self.assertEqual(before,fixtures.MaterialChainTests.files(self))


if __name__=='__main__':unittest.main()
