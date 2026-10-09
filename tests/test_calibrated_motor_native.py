"""Native handoff snapshots, license gating and real saved-curve verification."""
import copy
import json
import os
import subprocess
import unittest
from unittest.mock import MagicMock, patch

from flask import Flask
import test_calibrated_motor_preparation as fixtures
from test_maxwell_material_transport import fixture
from modules.calibrated_motor_native import CalibratedMotorNative, verify_inputs
from modules.maxwell_material_transport import load_contract
from modules.motor_workbench import write_json
from modules.workbench_routes import create_workbench
from tools.calibrated_motor_import_worker import import_and_preflight


class CalibratedNativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): fixtures.CalibratedPreparationTests.setUpClass.__func__(cls)
    @classmethod
    def tearDownClass(cls): fixtures.CalibratedPreparationTests.tearDownClass.__func__(cls)
    def setUp(self):
        fixtures.CalibratedPreparationTests.setUp(self)
        self.parent=self.prep.prepare(dict(calibration=self.cal,key=self.key))
        self.native=CalibratedMotorNative(self.prep,self.motor,self.store/'calibrated_motor_native')

    def prepare(self):
        return self.native.prepare(dict(preparation=self.parent['id']))

    def test_child_freezes_inputs_banks_and_parent_unchanged_reopens(self):
        old={p:p.read_bytes() for p in self.prep.path(self.parent['id']).rglob('*') if p.is_file()}
        with patch('subprocess.Popen',side_effect=AssertionError('CPU preparation cannot dispatch')):
            result=self.prepare()
        folder=self.native.path(result['id'])
        manifest=verify_inputs(folder,self.motor.project)
        self.assertEqual(manifest['excluded_grades'],['B30P105'])
        self.assertEqual(manifest['effective_bank_sha256'],self.parent['effective_bank_sha256'])
        self.assertEqual(manifest['material_contract'],self.parent['material_contract'])
        self.assertEqual(result['status'],'ready');self.assertFalse(result['efficiency_enabled'])
        self.assertEqual(old,{p:p.read_bytes() for p in old})
        self.assertEqual(self.motor.jobs(),[])
        self.assertEqual(CalibratedMotorNative(self.prep,self.motor,self.native.storage).get(result['id'])['status'],'ready')
        with self.assertRaises(ValueError):self.native.download(result['id'],'motor.aedt')

    def test_unavailable_license_preserves_ready_no_native_trace(self):
        result=self.prepare();folder=self.native.path(result['id'])
        with patch('modules.calibrated_motor_native.require_configured_license_connection',side_effect=ValueError('unreachable')),patch('subprocess.Popen') as launch:
            with self.assertRaisesRegex(ValueError,'许可服务无法连接'):self.native.start(result['id'])
        launch.assert_not_called()
        self.assertEqual(self.native.get(result['id'])['status'],'ready')
        for name in ('dispatch.json','motor.aedt','worker.log'):self.assertFalse((folder/name).exists())

    def test_tamper_and_source_changes_block_dispatch_before_native(self):
        result=self.prepare();folder=self.native.path(result['id'])
        p=folder/'inputs/material.amat';p.write_bytes(p.read_bytes()+b'\n')
        with patch('subprocess.Popen') as launch:
            with self.assertRaisesRegex(ValueError,'输入改变'):self.native.start(result['id'])
        launch.assert_not_called();self.assertEqual(self.native.records()[0]['status'],'invalid')
        result=self.prepare();folder=self.native.path(result['id'])
        manifest=json.loads((folder/'manifest.json').read_text());manifest['producer_hashes']['modules/motor_model_audit.py']='changed';write_json(folder/'manifest.json',manifest)
        with self.assertRaisesRegex(ValueError,'程序已改变'):self.native.start(result['id'])

    def test_launch_once_and_never_restart_or_requeue(self):
        result=self.prepare();python=self.motor.root/'.runtime/aedt/Scripts/python.exe'
        python.parent.mkdir(parents=True);python.write_bytes(b'fixture')
        with patch('modules.calibrated_motor_native.require_configured_license_connection',return_value={'TCP':True}),patch('modules.calibrated_motor_native.subprocess.Popen') as launch,patch('modules.calibrated_motor_native.threading.Thread'):
            launch.return_value.pid=os.getpid()
            self.assertEqual(self.native.start(result['id'])['status'],'starting')
            with self.assertRaisesRegex(ValueError,'重复执行'):self.native.start(result['id'])
        self.assertEqual(launch.call_count,1);self.assertEqual(self.motor.jobs(),[])
        folder=self.native.path(result['id']);state=json.loads((folder/'state.json').read_text());state.update(status='failed');write_json(folder/'state.json',state)
        with self.assertRaisesRegex(ValueError,'重复执行'):self.native.start(result['id'])

    def test_cpu_route_prepare_and_start_blocked(self):
        app=Flask('calimport_cpu')
        with patch.dict(os.environ,{'MAGSIM_CPU_ONLY':'1','MAGSIM_AUTO_RESUME_QUEUE':'0'}):
            app.register_blueprint(create_workbench(fixtures.PROJECT,self.store,self.root))
        c=app.test_client();url='/api/workbench/calibrated-motor/native-imports'
        response=c.post(url,json={'preparation':self.parent['id']});self.assertEqual(response.status_code,200,response.json)
        artifact=response.json['id'];self.assertEqual(c.get(url+'/'+artifact).json['status'],'ready')
        with patch('subprocess.Popen') as launch:self.assertEqual(c.post(url+'/'+artifact+'/start',json={}).status_code,423)
        launch.assert_not_called();self.assertEqual(c.get('/api/workbench/motor/jobs').json,[])
        response=c.get(url+'/'+artifact+'/files/manifest.json');self.assertEqual(response.status_code,200);response.close()
        self.assertEqual(c.get(url+'/'+artifact+'/files/motor.aedt').status_code,400)

    def test_timeout_preserves_process_and_marks_attention(self):
        result=self.prepare();folder=self.native.path(result['id'])
        state=json.loads((folder/'state.json').read_text());state.update(status='running');write_json(folder/'state.json',state)
        proc=MagicMock();proc.wait.side_effect=subprocess.TimeoutExpired('fixture',240)
        self.native._watch(proc,folder)
        proc.terminate.assert_not_called();proc.kill.assert_not_called()
        self.assertEqual(self.native.get(result['id'])['status'],'needs_attention')
        self.assertEqual(json.loads((folder/'state.json').read_text())['status'],'running')
        # Late worker cleanup owns the final state and supersedes the marker.
        state.update(status='failed');write_json(folder/'state.json',state)
        self.assertEqual(self.native.get(result['id'])['status'],'failed')

    def test_changed_producer_visible_as_unexecutable_ready_record(self):
        result=self.prepare();folder=self.native.path(result['id'])
        manifest=json.loads((folder/'manifest.json').read_text())
        manifest['producer_hashes']['modules/motor_model_audit.py']='old-version';write_json(folder/'manifest.json',manifest)
        reopened=self.native.get(result['id']);self.assertEqual(reopened['status'],'ready_source_changed')
        self.assertFalse(reopened['dispatch_compatible'])
        self.assertEqual(json.loads((folder/'state.json').read_text())['status'],'ready')

    def app_fixture(self):
        folder=self.root/'native_mock';folder.mkdir();path=fixture(folder);contract=load_contract(path)
        before=dict(local_CS_verified=True,moving_insert_count=48,
            inserts=[dict(name='GOES_V3_%02d'%i,material='old') for i in range(1,49)],
            core_loss=dict(enabled_object_names=['Rotor','Stator'],complete_insert_coverage=False))
        after=copy.deepcopy(before)
        for row in after['inserts']:row['material']=contract['material_name']
        after['core_loss'].update(enabled_object_names=['Rotor','Stator']+[r['name'] for r in before['inserts']],complete_insert_coverage=True)
        app=MagicMock();app.materials.odefinition_manager.GetProjectMaterialNames.side_effect=[[],[contract['material_name']]]
        objects={}
        for row in before['inserts']:
            obj=MagicMock();obj.part_coordinate_system=row['name']+'_CS';objects[row['name']]=obj
        app.modeler.__getitem__.side_effect=objects.__getitem__
        app.set_core_losses.return_value=True;app.validate_simple.return_value=1
        return folder,path,contract,before,after,app

    def test_preflight_saved_points_objects_and_loss_scope_no_analyze(self):
        folder,path,contract,before,after,app=self.app_fixture()
        with patch('tools.calibrated_motor_import_worker.audit_project',side_effect=[before,after]):
            result=import_and_preflight(app,path,contract,folder)
        self.assertEqual(result['material']['curve_points'],{'RD':3,'TD':3})
        self.assertEqual(result['material_assignment_count'],48);self.assertEqual(result['new_native_solves'],0)
        self.assertFalse(result['directional_field_verified']);self.assertFalse(result['efficiency_enabled'])
        self.assertEqual(app.set_core_losses.call_args.args[0],after['core_loss']['enabled_object_names'])
        app.analyze.assert_not_called();app.analyze_setup.assert_not_called()

    def test_preflight_missing_original_losses_or_wrong_saved_curve_rejected(self):
        folder,path,contract,before,after,app=self.app_fixture()
        after['core_loss']['enabled_object_names'].remove('Rotor')
        with patch('tools.calibrated_motor_import_worker.audit_project',side_effect=[before,after]):
            with self.assertRaisesRegex(ValueError,'覆盖不符'):import_and_preflight(app,path,contract,folder)
        app.validate_simple.assert_not_called();app.analyze.assert_not_called()
        app.materials.odefinition_manager.GetProjectMaterialNames.side_effect=[[],[contract['material_name']]]
        contract=copy.deepcopy(contract);contract['curves']['RD']['B'][1]=2
        with patch('tools.calibrated_motor_import_worker.audit_project',return_value=before):
            with self.assertRaisesRegex(ValueError,'changed RD B'):import_and_preflight(app,path,contract,folder)


if __name__=='__main__': unittest.main()
