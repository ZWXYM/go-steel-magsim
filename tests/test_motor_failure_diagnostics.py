import copy
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from modules.motor_failure_diagnostics import diagnose_job,append_export_diagnostics
from modules.motor_workbench import MotorWorkbench,write_json


class NativeFailureDiagnostics(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.motor=MotorWorkbench(self.root,self.root/'tasks',self.root)
        self.job=dict(id='scan_123456789abc',status='completed_with_failures',cases=[dict(id='case_02',material='grade',status='failed',error='Maxwell failed')])
        self.base=self.motor.path(self.job['id'])
        write_json(self.base/'state.json',self.job)
        self.source=self.base/'case_02/desktop_failure_messages.json'

    def messages(self,text):
        write_json(self.source,dict(messages=['[info] unchanged',text]))

    def test_factorization_classification_keeps_state_and_messages_unchanged(self):
        self.messages('[error] Time step at 0.0036667 sec: MFS Solver failed: Find Factorization fail')
        state_bytes=(self.base/'state.json').read_bytes()
        source_bytes=self.source.read_bytes()
        original=copy.deepcopy(self.job)
        value=diagnose_job(self.motor,self.job)
        self.assertEqual(value['cases'][0]['native_failure']['kind'],'solver_factorization_failure')
        self.assertIn('0.0036667',value['native_failure_diagnostics'][0]['messages'][0])
        self.assertFalse(value['native_failure_diagnostics'][0]['automatic_retry'])
        self.assertEqual(self.job,original)
        self.assertEqual(state_bytes,(self.base/'state.json').read_bytes())
        self.assertEqual(source_bytes,self.source.read_bytes())

    def test_other_error_categories_and_missing_or_malformed_sources(self):
        for message,kind in [('[error] License checkout failed','license_checkout_failure'),('[error] Out of memory','solver_memory_failure'),('[error] TAU mesh failed','mesh_generation_failure'),('[error] Other error','native_solver_failure')]:
            self.messages(message)
            self.assertEqual(diagnose_job(self.motor,self.job)['native_failure_diagnostics'][0]['kind'],kind)
        for payload in ('{broken','[]','null','{"messages":"not a list"}'):
            self.source.write_text(payload)
            self.assertEqual(diagnose_job(self.motor,self.job)['native_failure_diagnostics'],[])

    def test_only_failed_case_inside_task_can_expose_messages(self):
        self.messages('[error] actual error')
        for case_id,status in [('../outside','failed'),('case_02','completed'),('case_x','failed')]:
            value=copy.deepcopy(self.job)
            value['cases'][0].update(id=case_id,status=status)
            self.assertEqual(diagnose_job(self.motor,value)['native_failure_diagnostics'],[])

    def test_append_adds_derived_file_to_new_export_without_changing_raw_producers(self):
        self.messages('[error] MFS Solver failed: Find Factorization fail')
        output=self.base/'exports/summary_abcdef123456'
        output.mkdir(parents=True)
        with zipfile.ZipFile(output/'reports_bundle.zip','x') as bundle:
            bundle.writestr('summary.csv','original summary')
        original_state=(self.base/'state.json').read_bytes()
        original_messages=self.source.read_bytes()
        result=dict(job_id=self.job['id'],files={'reports_bundle.zip':'exports/summary_abcdef123456/reports_bundle.zip'})
        value=append_export_diagnostics(self.motor,result)
        self.assertIn('native_failures.json',value['files'])
        with zipfile.ZipFile(output/'reports_bundle.zip') as bundle:
            self.assertEqual(bundle.read('summary.csv'),b'original summary')
            review=json.loads(bundle.read('native_failures.json'))
        self.assertEqual(review['failures'][0]['kind'],'solver_factorization_failure')
        self.assertFalse(review['raw_results_changed'])
        self.assertEqual(original_state,(self.base/'state.json').read_bytes())
        self.assertEqual(original_messages,self.source.read_bytes())
        with self.assertRaisesRegex(ValueError,'已附加'):
            append_export_diagnostics(self.motor,result)

    def test_append_rejects_raw_result_directory(self):
        result=dict(job_id=self.job['id'],files={'reports_bundle.zip':'reports_bundle.zip'})
        with self.assertRaisesRegex(ValueError,'新汇总目录'):
            append_export_diagnostics(self.motor,result)


if __name__=='__main__':
    unittest.main()
