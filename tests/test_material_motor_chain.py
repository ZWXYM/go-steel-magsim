"""Real CPU preparation/reopen tests, with serialized fake native outcomes."""
import copy
import json
import os
import unittest
from unittest.mock import patch

from flask import Flask
import test_calibrated_motor_preparation as fixtures
import test_calibrated_motor_analysis as bh_fixtures
from modules.calibrated_motor_native import CalibratedMotorNative
from modules.calibrated_motor_analysis import CalibratedMotorAnalysis
from modules.calibrated_motor_execution import CalibratedMotorExecution
from modules.material_motor_chain import MaterialMotorChain
from modules.motor_queue import QueueLease
from modules.motor_waveforms import MotorWaveforms
from modules.motor_workbench import write_json
from modules.workbench_routes import create_workbench


class MaterialChainTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): fixtures.CalibratedPreparationTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls): fixtures.CalibratedPreparationTests.tearDownClass.__func__(cls)

    def setUp(self):
        fixtures.CalibratedPreparationTests.setUp(self)
        self.native = CalibratedMotorNative(self.prep, self.motor, self.store/'calibrated_motor_native')
        self.analysis = CalibratedMotorAnalysis(self.native, MotorWaveforms(self.motor), self.store/'calibrated_motor_analysis')
        self.execution = CalibratedMotorExecution(self.analysis, self.motor, self.store/'calibrated_motor_execution')
        self.chain = MaterialMotorChain(self.library, self.prep, self.native, self.analysis, self.execution)
        self.data = dict(calibration=self.cal, key=self.key, cores=2, measurement_protocol='v8_2_cycle1')

    def files(self):
        return {p.relative_to(self.store).as_posix(): p.read_bytes() for p in self.store.rglob('*') if p.is_file()}

    def initial(self):
        with patch('subprocess.Popen', side_effect=AssertionError('CPU chain must not dispatch')):
            return self.chain.ensure(self.data)

    def model_ready(self):
        first = self.initial()
        self.imported = self.native.get(first['records']['native_import']['id'])
        bh_fixtures.BHAnalysisTests.simulated_import(self)
        name = self.imported['material_name']
        audit = dict(local_CS_verified=True, moving_insert_count=48,
            inserts=[dict(material=name) for _ in range(48)],
            core_loss=dict(complete_insert_coverage=True, enabled_object_names=['Stator','Rotor']),
            operating_point=dict(speed_rpm=3000,poles=4))
        with patch('modules.calibrated_motor_analysis.audit_project', return_value=audit), \
             patch('modules.calibrated_motor_execution.audit_project', return_value=audit), \
             patch('subprocess.Popen', side_effect=AssertionError('CPU snapshot cannot dispatch')):
            result = self.chain.ensure(self.data)
        return result

    def test_repeated_request_reuses_exact_ids_and_file_bytes_without_dispatch(self):
        first = self.initial(); before = self.files()
        second = self.initial()
        self.assertEqual(first['records'], second['records'])
        self.assertEqual(second['created'], [])
        self.assertEqual(len(second['reused']), 3)
        self.assertEqual(self.files(), before)
        self.assertEqual(second['status'], 'awaiting_manual_import')
        self.assertFalse(second['native_dispatch_performed'])
        self.assertFalse(second['efficiency_enabled'])
        self.assertEqual(second['excluded_grades'], ['B30P105'])
        self.assertEqual(self.motor.jobs(), [])

    def test_interrupted_after_preparation_recovers_missing_stages_only(self):
        with patch.object(self.native, 'prepare', side_effect=RuntimeError('CPU interruption')):
            with self.assertRaisesRegex(RuntimeError, 'interruption'): self.chain.ensure(self.data)
        prep = self.prep.records()[0]
        result = self.initial()
        self.assertEqual(result['records']['preparation']['id'], prep['id'])
        self.assertEqual([r['stage'] for r in result['created']], ['native_import','analysis_plan'])
        self.assertEqual(len(self.prep.records()), 1)

    def test_interrupted_before_plan_does_not_duplicate_import(self):
        with patch.object(self.analysis, 'prepare', side_effect=RuntimeError('CPU interruption')):
            with self.assertRaisesRegex(RuntimeError, 'interruption'): self.chain.ensure(self.data)
        native = self.native.records()[0]
        result = self.initial()
        self.assertEqual(result['records']['native_import']['id'], native['id'])
        self.assertEqual([r['stage'] for r in result['created']], ['analysis_plan'])

    def test_os_lease_blocks_duplicate_preparation(self):
        lease = QueueLease(self.chain.lock); self.assertTrue(lease.acquire())
        try:
            with self.assertRaisesRegex(ValueError, '正在准备'): self.chain.ensure(self.data)
            self.assertEqual(self.prep.records(), [])
        finally: lease.release()

    def test_failed_native_record_is_reopened_without_new_record_or_retry(self):
        first = self.initial(); native = first['records']['native_import']['id']
        write_json(self.native.path(native)/'state.json', dict(id=native,status='failed',error='fixture failure'))
        before = self.files(); result = self.initial()
        self.assertEqual(result['status'], 'needs_review')
        self.assertEqual(result['created'], [])
        self.assertEqual(self.files(), before)

    def test_outdated_native_producer_is_not_silently_reprepared(self):
        first = self.initial(); folder = self.native.path(first['records']['native_import']['id'])
        manifest = json.loads((folder/'manifest.json').read_text())
        manifest['producer_hashes']['tools/calibrated_motor_import_worker.py'] = 'outdated'
        write_json(folder/'manifest.json', manifest)
        before = self.files(); result = self.initial()
        self.assertEqual(result['status'], 'needs_review')
        self.assertEqual(result['records']['native_import']['status'], 'ready_source_changed')
        self.assertEqual(self.files(), before)

    def test_new_protocol_reuses_material_and_import_but_creates_own_plan(self):
        first = self.initial(); data = dict(self.data,measurement_protocol='periodic_cycle3_v1')
        result = self.chain.ensure(data)
        self.assertEqual(first['records']['preparation'], result['records']['preparation'])
        self.assertEqual(first['records']['native_import'], result['records']['native_import'])
        self.assertNotEqual(first['records']['analysis_plan']['id'], result['records']['analysis_plan']['id'])
        self.assertEqual(len(self.analysis.records()), 2)

    def test_different_cores_or_template_require_separate_frozen_preparation(self):
        first = self.initial(); result = self.chain.ensure(dict(self.data,cores=1))
        self.assertNotEqual(first['records']['preparation']['id'], result['records']['preparation']['id'])
        self.template.write_bytes(self.template.read_bytes()+b'\n')
        changed = self.initial()
        self.assertNotEqual(first['records']['preparation']['id'], changed['records']['preparation']['id'])
        self.assertEqual(len(self.prep.records()), 3)

    def test_corrupt_existing_input_blocks_new_preparation(self):
        first = self.initial(); path = self.prep.path(first['records']['preparation']['id'])/'package/material.amat'
        path.write_bytes(path.read_bytes()+b'\n'); before = self.files()
        with self.assertRaisesRegex(ValueError, '未重复创建'): self.initial()
        self.assertEqual(self.files(), before)

    def test_simulated_import_copies_model_and_prepares_execution_once(self):
        result = self.model_ready(); before = self.files(); again = self.initial()
        self.assertEqual(result['status'], 'awaiting_manual_execution')
        self.assertEqual([r['stage'] for r in result['created']], ['model_snapshot','execution'])
        self.assertEqual(again['records'], result['records'])
        self.assertEqual(again['created'], [])
        self.assertEqual(self.files(), before)
        self.assertFalse((self.execution.path(result['records']['execution']['id'])/'dispatch.json').exists())

    def test_existing_failed_execution_stays_visible_without_replacement(self):
        result = self.model_ready(); artifact = result['records']['execution']['id']
        write_json(self.execution.path(artifact)/'state.json',dict(id=artifact,status='failed',error='fixture solve'))
        before = self.files(); again = self.initial()
        self.assertEqual(again['status'], 'needs_review')
        self.assertEqual(again['records']['execution']['id'], artifact)
        self.assertEqual(again['created'], [])
        self.assertEqual(self.files(), before)

    def test_verified_completed_execution_is_reused_as_result(self):
        result = self.model_ready()
        run = self.execution.get(result['records']['execution']['id'])
        run.update(status='completed',result_available=True)
        with patch.object(self.execution,'records',return_value=[run]): again = self.initial()
        self.assertEqual(again['status'],'result_available')
        self.assertEqual(again['created'],[])

    def test_later_duplicate_import_cannot_hide_existing_completed_result(self):
        result = self.model_ready()
        run = self.execution.get(result['records']['execution']['id'])
        run.update(status='completed',result_available=True)
        self.imported = self.native.prepare({'preparation': result['records']['preparation']['id']})
        bh_fixtures.BHAnalysisTests.simulated_import(self)
        with patch.object(self.execution,'records',return_value=[run]): again = self.initial()
        self.assertEqual({k:r['id'] for k,r in again['records'].items()}, {k:r['id'] for k,r in result['records'].items()})
        self.assertEqual(again['status'],'result_available')
        self.assertEqual(again['created'],[])

    def test_later_duplicate_preparation_cannot_hide_existing_completed_result(self):
        result = self.model_ready()
        run = self.execution.get(result['records']['execution']['id'])
        run.update(status='completed',result_available=True)
        extra = self.prep.prepare(dict(calibration=self.cal,key=self.key,cores=2))
        self.imported = self.native.prepare({'preparation':extra['id']})
        bh_fixtures.BHAnalysisTests.simulated_import(self)
        with patch.object(self.execution,'records',return_value=[run]): again = self.initial()
        self.assertEqual({k:r['id'] for k,r in again['records'].items()}, {k:r['id'] for k,r in result['records'].items()})
        self.assertEqual(again['created'],[])

    def test_later_plan_without_execution_cannot_hide_current_active_execution(self):
        result = self.model_ready()
        run = self.execution.get(result['records']['execution']['id'])
        run.update(status='running')
        extra = self.analysis.prepare(dict(native_import=result['records']['native_import']['id'],cores=2))
        audit = dict(local_CS_verified=True,moving_insert_count=48,
            core_loss=dict(complete_insert_coverage=True),inserts=[dict(material=self.imported['material_name']) for _ in range(48)])
        with patch('modules.calibrated_motor_analysis.audit_project',return_value=audit): self.analysis.prepare_model(extra['id'])
        with patch.object(self.execution,'records',return_value=[run]): again = self.initial()
        self.assertEqual(again['records']['analysis_plan']['id'],result['records']['analysis_plan']['id'])
        self.assertEqual(again['status'],'waiting_execution')
        self.assertEqual(again['created'],[])

    def test_child_lineage_conflict_blocks_reuse_or_new_records(self):
        self.initial(); children = copy.deepcopy(self.native.records())
        children[0]['effective_bank_sha256'] = 'another-bank'; before = self.files()
        with patch.object(self.native,'records',return_value=children):
            with self.assertRaisesRegex(ValueError,'来源不符'): self.initial()
        self.assertEqual(self.files(),before)

    def test_invalid_request_cannot_dispatch_or_create_stages(self):
        for data in (None,{},dict(self.data,cores=True),dict(self.data,cores=3),
                     dict(self.data,measurement_protocol='unknown'),dict(self.data,start=True)):
            with self.assertRaises(ValueError): self.chain.ensure(data)
        self.assertEqual(self.prep.records(),[])

    def test_cpu_http_endpoint_reuses_ids_and_does_not_enable_native_start(self):
        app = Flask('material_chain_cpu')
        with patch.dict(os.environ,{'MAGSIM_CPU_ONLY':'1','MAGSIM_AUTO_RESUME_QUEUE':'0'}):
            app.register_blueprint(create_workbench(fixtures.PROJECT,self.store,self.root))
        client = app.test_client(); url='/api/workbench/material-motor/prepare-or-reuse'
        with patch('subprocess.Popen',side_effect=AssertionError('CPU route cannot launch')):
            first=client.post(url,json=self.data); second=client.post(url,json=self.data)
            self.assertEqual(first.status_code,200,first.json)
            self.assertEqual(second.status_code,200,second.json)
            self.assertEqual(first.json['records'],second.json['records'])
            artifact=first.json['records']['native_import']['id']
            self.assertEqual(client.post('/api/workbench/calibrated-motor/native-imports/'+artifact+'/start',json={}).status_code,423)
        self.assertEqual(second.json['created'],[])


if __name__ == '__main__': unittest.main()
