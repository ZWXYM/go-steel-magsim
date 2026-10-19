"""Serial stage transitions with mocked native boundaries, never actual AEDT."""
import copy
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock,patch

import test_material_motor_chain as fixtures
from modules.material_motor_chain import MaterialMotorChain
from tools import material_motor_batch_worker as worker


class BatchWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.native=MagicMock();self.execution=MagicMock();self.analysis=MagicMock()
        self.chain=SimpleNamespace(native=self.native,execution=self.execution,analysis=self.analysis,pick=MaterialMotorChain.pick)
        self.records=dict(native_import=dict(id='calimport_0123456789ab',status='ready'),
            analysis_plan=dict(id='bhanalysis_0123456789ab',status='ready_for_model_preparation'))
        self.manager=SimpleNamespace(chain=self.chain,verify_producers=MagicMock(),
            item_records=lambda *args:self.records)
        self.item=dict(id='item_01',source={})
        self.cursor=dict(id='item_01',status='pending',records={k:dict(v) for k,v in self.records.items()})
        self.state=dict(new_import_ids=[],new_execution_ids=[])
        self.manifest=dict(budget=dict(max_new_imports=1,max_new_motor_solves=1))
        self.saves=[];self.trace=[]
        self.native.start.side_effect=self.imported
        self.native.get.side_effect=lambda *args:self.records['native_import']
        self.native.path.return_value=self.root/'native';self.native.path.return_value.mkdir()
        self.analysis.prepare_model.side_effect=self.snapshot
        self.execution.prepare.side_effect=self.prepared
        self.execution.start.side_effect=self.completed
        self.execution.records.return_value=[]
        self.execution.get.side_effect=lambda *args:self.records['execution']
        self.execution.path.return_value=self.root/'execution';self.execution.path.return_value.mkdir()

    def tearDown(self):self.temp.cleanup()
    def imported(self,*args):self.trace.append('import');self.records['native_import']['status']='imported_not_solved'
    def snapshot(self,*args):self.trace.append('snapshot');self.records['analysis_plan']['status']='model_snapshot_ready_not_submitted'
    def prepared(self,*args):
        self.trace.append('prepare_execution');self.records['execution']=dict(id='bhexec_0123456789ab',status='prepared',result_available=False)
        return self.records['execution']
    def completed(self,*args):
        self.trace.append('analyze');self.records['execution'].update(status='completed',result_available=True)
    def save(self):self.saves.append(copy.deepcopy((self.state,self.cursor)))
    def advance(self):
        with patch.object(worker,'wait_resources'):
            worker.advance_item(self.manager,self.manifest,self.state,self.item,self.cursor,time.monotonic()+3,self.save)

    def test_fresh_item_imports_then_copies_then_analyzes_once(self):
        self.advance()
        self.assertEqual(self.trace,['import','snapshot','prepare_execution','analyze'])
        self.assertEqual(self.cursor['status'],'completed')
        self.assertEqual(len(self.state['new_import_ids']),1);self.assertEqual(len(self.state['new_execution_ids']),1)
        self.advance();self.assertEqual(self.trace,['import','snapshot','prepare_execution','analyze'])

    def test_existing_completed_result_is_bound_and_reused_without_native_calls(self):
        self.imported();self.snapshot();self.prepared();self.completed();self.trace=[]
        self.advance()
        self.assertEqual(self.cursor['status'],'reused_result');self.assertEqual(self.trace,[])
        self.native.start.assert_not_called();self.execution.start.assert_not_called()

    def test_old_failed_import_is_kept_and_not_retried(self):
        self.records['native_import'].update(status='failed',error='existing failure')
        self.advance();self.assertEqual(self.cursor['status'],'blocked')
        self.assertEqual(self.cursor['error'],'existing failure')
        self.native.start.assert_not_called();self.execution.start.assert_not_called()

    def test_existing_failed_execution_is_kept_without_another_execution(self):
        self.imported();self.snapshot();self.prepared();self.trace=[]
        self.records['execution'].update(status='failed',error='MFS failure')
        self.advance();self.assertEqual(self.cursor['status'],'failed')
        self.execution.start.assert_not_called()

    def test_budget_cap_or_changed_program_stops_before_dispatch(self):
        self.manifest['budget']['max_new_imports']=0
        with self.assertRaisesRegex(ValueError,'预算'):self.advance()
        self.native.start.assert_not_called()
        self.manager.verify_producers.side_effect=ValueError('source changed')
        with self.assertRaisesRegex(ValueError,'source changed'):self.advance()
        self.native.start.assert_not_called()

    def test_unreachable_license_without_dispatch_trace_can_resume_explicitly_later(self):
        self.native.start.side_effect=ValueError('license unreachable')
        with self.assertRaisesRegex(ValueError,'license unreachable'):self.advance()
        self.assertEqual(self.state['new_import_ids'],[])
        self.native.start.side_effect=self.imported
        self.advance();self.assertEqual(self.cursor['status'],'completed')

    def test_launch_error_with_trace_is_not_cleared_or_retried(self):
        (self.native.path.return_value/'worker.log').write_text('launch attempted')
        self.native.start.side_effect=ValueError('unconfirmed launch')
        with self.assertRaisesRegex(ValueError,'unconfirmed launch'):self.advance()
        self.assertEqual(len(self.state['new_import_ids']),1)
        with self.assertRaisesRegex(ValueError,'重复执行'):self.advance()
        self.assertEqual(self.native.start.call_count,1)

    def test_execution_attention_or_overall_budget_preserves_scene(self):
        self.imported();self.snapshot();self.prepared();self.trace=[]
        self.records['execution']['status']='needs_attention'
        with self.assertRaisesRegex(ValueError,'需要检查'):self.advance()
        self.execution.start.assert_not_called()
        with self.assertRaises(TimeoutError):
            worker.advance_item(self.manager,self.manifest,self.state,self.item,self.cursor,time.monotonic()-1,self.save)
        self.execution.start.assert_not_called()

    def test_import_already_attempted_is_not_started_again(self):
        self.state['new_import_ids'].append(self.records['native_import']['id'])
        with self.assertRaisesRegex(ValueError,'重复执行'):self.advance()
        self.native.start.assert_not_called()

    def test_pause_stops_before_new_import_without_killing_a_process(self):
        self.manager.current_folder=self.root;(self.root/'pause_requested.json').write_text('{}')
        with self.assertRaises(worker.PauseRequested):self.advance()
        self.native.start.assert_not_called();self.execution.start.assert_not_called()
        self.assertEqual(self.state['new_import_ids'],[])

    def test_pause_after_actual_result_collects_it_without_another_analysis(self):
        self.imported();self.snapshot();self.prepared();self.completed();self.trace=[]
        self.manager.current_folder=self.root;(self.root/'pause_requested.json').write_text('{}')
        self.advance();self.assertEqual(self.cursor['status'],'reused_result')
        self.execution.start.assert_not_called();self.assertEqual(self.trace,[])


if __name__=='__main__':unittest.main()
