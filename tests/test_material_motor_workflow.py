"""Read-only aggregation and actual saved-fixture HTTP; no AEDT or new solve."""
import copy
import io
import json
import os
import unittest
import zipfile
from unittest.mock import MagicMock, patch

from flask import Flask
import test_calibrated_motor_analysis as fixtures
from modules.material_motor_workflow import MaterialMotorWorkflow
from modules.material_library import sha
from modules.workbench_routes import create_workbench


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.source = dict(material_name='WB_prediction', parent_bank_sha256='parent', effective_bank_sha256='held_out',
            training_grades=['B23R075','B27R090','B27R095'], excluded_grades=['B30P105'],
            H_axis='physical_A_per_m', H_scale=1, evidence_scope='whole_material_exclusion')
        self.source['files']={'material.amat':dict(sha256='original-amat'),'metadata.json':dict(sha256='original-metadata')}
        self.cal = 'cal_'+'a'*12; self.key = 'prediction:pred_'+'b'*12
        self.entry = dict(key=self.key, kind='prediction', label='pred_'+'b'*12, available=True)
        self.library = MagicMock(); self.library.catalog.return_value = dict(records=[dict(id=self.cal, entries=[self.entry])])
        self.library.detail.return_value = self.source
        self.prep = dict(self.source, id='calmotor_'+'c'*12, status='awaiting_native_material_import', calibration_id=self.cal, material_key=self.key)
        self.prep['files']={'package/material.amat':'original-amat','package/metadata.json':'original-metadata'}
        self.native = dict(self.source, id='calimport_'+'d'*12, status='ready', preparation_id=self.prep['id'])
        self.plan = dict(self.source, id='bhanalysis_'+'e'*12, status='awaiting_native_material_import', native_import_id=self.native['id'])
        self.run = dict(self.source, id='bhexec_'+'f'*12, status='prepared', analysis_plan_id=self.plan['id'], result_available=False)
        self.components = [MagicMock() for _ in range(4)]
        for component, rows in zip(self.components, [[self.prep], [self.native], [self.plan], []]): component.records.return_value=rows
        self.workflow = MaterialMotorWorkflow(self.library, *self.components)
        self.license = patch('modules.material_motor_workflow.license_status', return_value=dict(available=False, checked_servers=['1055@localhost']))
        self.license_mock = self.license.start(); self.addCleanup(self.license.stop)
        self.mode = patch.dict(os.environ, {'MAGSIM_CPU_ONLY':'0'}); self.mode.start(); self.addCleanup(self.mode.stop)

    def row(self): return self.workflow.snapshot()['materials'][0]
    def modeled(self):
        self.native['status']='imported_not_solved'; self.plan['status']='model_snapshot_ready_not_submitted'
        self.components[3].records.return_value=[self.run]
    def completed(self, status='completed'):
        self.modeled(); self.run.update(status=status, result_available=True,
            result=dict(metrics=dict(T_avg_Nm=0.,T_min_Nm=0.,T_max_Nm=0.,K_T_ripple_pct=None,torque_csv_sha256='official')))

    def test_pending_import_reuses_existing_plan_and_never_claims_result_or_dispatches(self):
        with patch('subprocess.Popen', side_effect=AssertionError('read-only')):
            value=self.workflow.snapshot()
        row=value['materials'][0]; self.assertEqual(row['next_step']['action'],'native_start')
        self.assertEqual(row['next_step']['blocked_reason'],'现有许可服务不可连接')
        self.assertIn('analysis_plan',row['branches'][0]['records']); self.assertEqual(value['totals']['actual_torque_results'],0)
        self.assertEqual(row['excluded_grades'],['B30P105']); self.assertFalse(value['source_records_changed'])
        for component in [self.library]+self.components: component.prepare.assert_not_called(); component.start.assert_not_called()

    def test_available_material_without_motor_copy_offers_existing_source_link(self):
        self.components[0].records.return_value=[]
        row=self.row(); self.assertEqual(row['next_step']['action'],'motor_prepare')
        self.assertIn('key=prediction%3A',row['material_url']); self.assertIn(self.cal,row['material_url'])

    def test_successful_import_requires_model_preparation_before_execution(self):
        self.native['status']='imported_not_solved'; self.plan['status']='ready_for_model_preparation'
        row=self.row(); self.assertEqual(row['next_step']['action'],'model_prepare')
        self.assertIn(self.plan['id'],row['next_step']['href']); self.assertFalse(row['branches'][0]['result_available'])

    def test_model_ready_without_task_requires_preparation(self):
        self.modeled(); self.components[3].records.return_value=[]
        self.assertEqual(self.row()['next_step']['action'],'execution_prepare')

    def test_cpu_preview_prepares_but_does_not_offer_native_start_as_unblocked(self):
        self.modeled(); self.license_mock.return_value=dict(available=True)
        with patch.dict(os.environ,{'MAGSIM_CPU_ONLY':'1'}): row=self.row()
        self.assertEqual(row['next_step']['action'],'execution_start'); self.assertIn('CPU',row['next_step']['blocked_reason'])

    def test_completed_result_is_from_linked_execution_and_preserves_zero(self):
        self.completed(); snapshot=self.workflow.snapshot(); branch=snapshot['materials'][0]['branches'][0]
        self.assertEqual(snapshot['totals']['actual_torque_results'],1); self.assertEqual(branch['torque']['T_avg_Nm'],0.)
        self.assertIsNone(branch['torque']['K_T_ripple_pct']); self.assertEqual(branch['next_step']['action'],'result')
        self.assertFalse(snapshot['efficiency_enabled']); self.assertFalse(snapshot['system_complete'])

    def test_foreign_bank_child_cannot_become_result_or_invite_duplicate_preparation(self):
        self.completed(); self.run['effective_bank_sha256']='wrong_bank'
        snapshot=self.workflow.snapshot(); row=snapshot['materials'][0]
        self.assertEqual(snapshot['totals']['actual_torque_results'],0); self.assertEqual(row['next_step']['action'],'review')
        self.assertTrue(any(i['id']==self.run['id'] for i in snapshot['issues']))

    def test_foreign_material_key_is_not_joined_by_material_name(self):
        self.prep['material_key']='holdout:B30P105'
        snapshot=self.workflow.snapshot(); self.assertFalse(snapshot['materials'][0]['branches'])
        self.assertTrue(snapshot['issues']); self.assertEqual(snapshot['totals']['actual_torque_results'],0)

    def test_changed_current_curve_bytes_do_not_join_old_copy_with_same_bank(self):
        self.source['files']['material.amat']['sha256']='different-amat'
        value=self.workflow.snapshot(); row=value['materials'][0]
        self.assertFalse(row['branches']); self.assertEqual(row['next_step']['action'],'review')
        self.assertTrue(value['issues']); self.assertEqual(value['totals']['actual_torque_results'],0)

    def test_stale_program_branch_is_retained_and_valid_ready_branch_recommended(self):
        self.native['status']='ready_source_changed'; replacement=dict(self.native,id='calimport_'+'1'*12,status='ready')
        self.components[1].records.return_value=[self.native,replacement]
        row=self.row(); self.assertEqual(len(row['branches']),2); self.assertEqual(row['next_step']['action'],'native_start')
        self.assertTrue(any(b['next_step']['action']=='native_reprepare' for b in row['branches']))

    def test_failed_and_late_completion_are_review_not_automatic_retry(self):
        self.modeled(); self.run['status']='failed'
        self.assertEqual(self.row()['next_step']['action'],'review')
        self.completed('completed_after_budget'); row=self.row()
        self.assertEqual(row['next_step']['action'],'review'); self.assertTrue(row['branches'][0]['result_available'])

    def test_active_task_shows_wait_and_other_source_blocks_start(self):
        self.modeled(); self.run.update(status='running',phase='solving')
        self.assertEqual(self.row()['next_step']['action'],'wait')
        self.run['status']='prepared'; other=dict(self.run,id='bhexec_'+'2'*12,analysis_plan_id='bhanalysis_'+'3'*12,status='needs_attention')
        self.components[3].records.return_value=[self.run,other]; self.license_mock.return_value=dict(available=True)
        self.assertIn('其他原生',self.row()['next_step']['blocked_reason'])

    def test_unreadable_stage_is_visible_and_does_not_imply_no_existing_record(self):
        self.components[1].records.side_effect=OSError('read error')
        value=self.workflow.snapshot(); self.assertTrue(value['materials'][0]['available'])
        self.assertEqual(value['materials'][0]['next_step']['action'],'review'); self.assertTrue(value['issues'])

    def test_unavailable_material_and_orphan_execution_remain_visible_without_result(self):
        self.entry.update(available=False,reason='metadata missing'); self.completed()
        value=self.workflow.snapshot(); self.assertEqual(value['totals']['actual_torque_results'],0)
        self.assertEqual(value['materials'][0]['next_step']['action'],'review'); self.assertTrue(value['issues'])

    def test_bundle_contains_only_progress_and_bound_csv_not_native_package(self):
        before=copy.deepcopy([self.prep,self.native,self.plan]); data=self.workflow.bundle().getvalue()
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            self.assertEqual(set(archive.namelist()),{'workflow.json','materials.csv','manifest.json'})
            manifest=json.loads(archive.read('manifest.json'))
            for name,row in manifest['files'].items(): self.assertEqual(sha(archive.read(name)),row['sha256'])
            snapshot=json.loads(archive.read('workflow.json')); self.assertTrue(snapshot['read_only'])
        self.assertEqual(before,[self.prep,self.native,self.plan])


class WorkflowAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): fixtures.BHAnalysisTests.setUpClass.__func__(cls)
    @classmethod
    def tearDownClass(cls): fixtures.BHAnalysisTests.tearDownClass.__func__(cls)
    def setUp(self): fixtures.BHAnalysisTests.setUp(self)

    def test_real_saved_inputs_get_and_download_are_read_only_in_cpu_preview(self):
        plan=self.analysis.prepare({'native_import':self.imported['id']})
        before={p:p.read_bytes() for p in self.store.rglob('*') if p.is_file()}
        app=Flask('workflow_routes')
        with patch.dict(os.environ,{'MAGSIM_CPU_ONLY':'1','MAGSIM_AUTO_RESUME_QUEUE':'0'}):
            app.register_blueprint(create_workbench(self.motor.project,self.store,self.root))
            with patch('modules.material_motor_workflow.license_status',return_value=dict(available=False)),patch('subprocess.Popen',side_effect=AssertionError('no dispatch')):
                client=app.test_client(); response=client.get('/api/workbench/material-motor/workflows')
                self.assertEqual(response.status_code,200,response.json)
                row=next(r for r in response.json['materials'] if r['calibration_id']==self.cal and r['material_key']==self.key)
                self.assertEqual(row['branches'][0]['records']['analysis_plan']['id'],plan['id'])
                self.assertEqual(row['next_step']['action'],'native_start'); self.assertTrue(response.json['resources']['cpu_only'])
                bundle=client.get('/api/workbench/material-motor/workflows/bundle'); self.assertEqual(bundle.status_code,200)
                with zipfile.ZipFile(io.BytesIO(bundle.data)) as archive:self.assertIn('materials.csv',archive.namelist())
                bundle.close(); self.assertEqual(client.post('/api/workbench/material-motor/workflows',json={}).status_code,423)
        self.assertTrue(all(p.read_bytes()==blob for p,blob in before.items())); self.assertEqual(self.motor.jobs(),[])


if __name__=='__main__': unittest.main()
