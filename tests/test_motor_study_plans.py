"""Source-bound original plan preparation never submits or replaces old results."""
import copy,io,json,os,unittest,zipfile
from pathlib import Path
from unittest.mock import patch
from flask import Flask,jsonify
import test_motor_study_applications as fixtures
from modules.motor_workbench import MotorWorkbench,write_json
from modules.optimization_plans import OptimizationPlans
from modules.motor_study_plans import MotorStudyPlans,create_study_plan_routes
from modules.motor_queue import QueueLease
from modules.motor_model_audit import LOSS_POLICY
from modules.system_data import json_bytes,sha256

PROJECT=Path(__file__).resolve().parents[1]


class MotorStudyPlanTests(unittest.TestCase):
    def setUp(self):
        fixtures.MotorStudyApplicationTests.setUp(self)
        self.application=self.actions.apply(self.study['id'],self.selection)
        self.storage=self.root/'task_storage';self.motor=MotorWorkbench(self.root,self.storage/'motor',PROJECT)
        self.optimizer.motor=self.motor;self.plans=OptimizationPlans(self.optimizer)
        self.index=self.root/'motor/v8_1/manifest.json'
        write_json(self.index,dict(cases=[dict(case='reference',material='SYNTHETIC M6'),dict(case='candidate',material='SYNTHETIC GO')]))
        self.motor.template.parent.mkdir(parents=True)
        self.motor.template.write_text(''.join("$begin 'GeometryPart'\nName='GOES_V3_%02d'\nMaterialValue='\"old\"'\nPartCoordinateSystem=%d\n$end 'GeometryPart'\n"%(i,i+1) for i in range(1,49)),encoding='utf8')
        def catalog():return dict(available=True,materials=['SYNTHETIC M6','SYNTHETIC GO'],template='SYNTHETIC software template',
            template_sha256=sha256(self.motor.template.read_bytes()),model_physics_audit={'synthetic_fixture':True},
            loss_scope_policy=LOSS_POLICY,license={'available':False,'reason':'SYNTHETIC no license checkout'})
        self.catalog=patch.object(self.motor,'catalog',side_effect=catalog);self.catalog.start();self.addCleanup(self.catalog.stop)
        self.manager=MotorStudyPlans(self.actions,self.plans,self.storage)
        self.preview=self.manager.preview(self.application['id']);self.assertTrue(self.preview['available'])
        self.request=dict(source_binding_sha256=self.preview['source_binding_sha256'],cases=['candidate'],cores=2)

    def prepare(self,selection=None):return self.manager.prepare(self.application['id'],selection or self.request)

    def test_exact_original_mapping_same_task_reference_config_and_registered_source_pack(self):
        source=self.motor.template.read_bytes()
        with patch.object(self.motor,'submit',side_effect=AssertionError('Never submit')),patch('subprocess.Popen',side_effect=AssertionError('No native process')):
            record=self.prepare()
        plan=self.plans.get(record['plan_id']);state=self.motor.state(record['job_id'])
        self.assertEqual(plan['materials'],['SYNTHETIC M6','SYNTHETIC GO']);self.assertEqual(plan['reference_case_id'],'case_01')
        self.assertEqual(plan['config'],self.application['config']);self.assertEqual(plan['measurement_protocol'],'v8_2_cycle1')
        self.assertEqual(plan['status'],'prepared');self.assertEqual(state['status'],'prepared');self.assertEqual(state['total'],2)
        self.assertEqual(self.motor.template.read_bytes(),source);self.assertFalse(record['native_submission_during_preparation'])
        package,blob=self.manager.packages.read(record['package_id']);self.assertTrue(package['private_only'])
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            self.assertEqual(len(archive.namelist()),11)
            self.assertEqual(archive.read('plan/plan.json'),(self.plans.path(record['plan_id'])/'plan.json').read_bytes())
            self.assertEqual(archive.read('scan/manifest.json'),(self.motor.path(record['job_id'])/'manifest.json').read_bytes())
            with zipfile.ZipFile(io.BytesIO(archive.read('source_application.zip'))) as application:
                self.assertEqual(json.loads(application.read('application.json'))['id'],self.application['id'])

    def test_repeat_reopens_same_plan_without_creating_another_scan(self):
        with patch.object(self.plans,'prepare',wraps=self.plans.prepare) as prepare:
            first=self.prepare();second=self.prepare();self.assertEqual(prepare.call_count,1)
        self.assertEqual(first['plan_id'],second['plan_id']);self.assertFalse(second['created'])
        self.assertEqual(len(self.motor.jobs()),1);self.assertEqual(len(self.plans.records()),1)

    def test_zero_candidate_application_cannot_prepare_only_a_baseline(self):
        default=self.actions.apply(self.study['id'],dict(self.selection,configuration_id='c33'))
        preview=self.manager.preview(default['id']);self.assertFalse(preview['available'])
        with self.assertRaisesRegex(ValueError,'没有其他可行候选'):self.manager.prepare(default['id'],self.request)
        self.assertEqual(self.motor.jobs(),[])

    def test_invalid_or_duplicate_candidates_and_bool_cores_leave_no_job(self):
        for update in ({'cases':[]},{'cases':['candidate','candidate']},{'cases':['reference']},{'cases':['invented']},
                       {'cores':True},{'cores':0},{'cores':33},{'new_reference':'candidate'}):
            with self.subTest(update=update),self.assertRaises(ValueError):self.prepare(dict(self.request,**update))
        self.assertEqual(self.motor.jobs(),[])

    def test_changed_template_or_mapping_requires_new_explicit_selection(self):
        for file in (self.motor.template,self.index):
            old=file.read_bytes()
            try:
                file.write_bytes(old+b'\n')
                with self.assertRaisesRegex(ValueError,'来源或模板已变化'):self.prepare()
            finally:file.write_bytes(old)
        self.assertEqual(self.motor.jobs(),[])

    def test_ambiguous_or_missing_material_mapping_is_explicit(self):
        original=self.index.read_bytes()
        for cases in ([dict(case='reference',material='SYNTHETIC M6')],
                      [dict(case='reference',material='SYNTHETIC M6'),dict(case='candidate',material='SYNTHETIC GO'),dict(case='candidate',material='SYNTHETIC GO')]):
            try:
                write_json(self.index,dict(cases=cases));preview=self.manager.preview(self.application['id']);self.assertFalse(preview['available']);self.assertIn('唯一',preview['reason'])
            finally:self.index.write_bytes(original)
        self.assertEqual(self.motor.jobs(),[])

    def test_process_lease_prevents_duplicate_job_preparation(self):
        selected=dict(application_id=self.application['id'],**self.request)
        identity='splan_'+sha256(json_bytes(selected))[:16]
        lease=QueueLease(self.manager.storage/(identity+'.guard'));self.assertTrue(lease.acquire())
        try:
            with self.assertRaisesRegex(ValueError,'正在准备'):self.prepare()
            self.assertEqual(self.motor.jobs(),[])
        finally:lease.release()
        self.assertTrue(self.prepare()['created'])

    def test_failure_after_original_plan_is_retained_without_automatic_retry(self):
        original=self.plans.prepare;before=self.index.read_bytes()
        def changed(data):
            value=original(data);self.index.write_bytes(before+b'\n');return value
        try:
            with patch.object(self.plans,'prepare',side_effect=changed):
                with self.assertRaisesRegex(ValueError,'准备期间来源'):self.prepare()
        finally:self.index.write_bytes(before)
        with self.assertRaisesRegex(ValueError,'此前准备未完成'):self.prepare()
        state=json.loads(next(self.manager.storage.glob('splan_*/state.json')).read_bytes())
        self.assertEqual(state['status'],'needs_attention');self.assertIsNotNone(state['plan_id'])
        self.assertEqual(len(self.motor.jobs()),1);self.assertEqual(len(self.plans.records()),1)

    def test_saved_pack_tampering_is_detected_without_new_work(self):
        result=self.prepare();package=self.manager.packages.folder(result['package_id'])/'package.zip'
        package.write_bytes(package.read_bytes()+b'changed')
        with self.assertRaisesRegex(ValueError,'字节已改变'):self.manager.get(result['id'])
        with patch.object(self.plans,'prepare',side_effect=AssertionError('No repeat')):
            with self.assertRaisesRegex(ValueError,'字节已改变'):self.prepare()

    def test_routes_create_once_reopen_live_status_and_keep_source_pack_readable(self):
        app=Flask('study plan routes');app.register_blueprint(create_study_plan_routes(self.manager))
        @app.errorhandler(ValueError)
        def error(exc):return jsonify(error=str(exc)),400
        client=app.test_client();base='/api/workbench/optimization/study-applications/'+self.application['id']+'/scan-plan'
        self.assertTrue(client.get(base).json['available']);first=client.post(base,json=self.request);self.assertEqual(first.status_code,201)
        self.assertEqual(client.post(base,json=self.request).status_code,200)
        saved='/api/workbench/optimization/study-plans/'+first.json['id'];self.assertEqual(client.get(saved).json['current_plan_status'],'prepared')
        with patch.object(self.plans,'get',side_effect=OSError('original storage temporarily unavailable')):
            value=client.get(saved).json;self.assertEqual(value['current_plan_status'],'unavailable');self.assertIn('temporarily',value['current_plan_error'])
        self.assertEqual(len(self.manager.packages.records()),1);self.assertEqual(client.post(base,json={}).status_code,400)


if __name__=='__main__':unittest.main()
