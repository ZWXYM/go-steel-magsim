"""CPU plan lineage and torque-only output; no real native import or solve."""
import csv
import io
import json
import os
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import numpy as np
from flask import Flask
import test_calibrated_motor_native as fixtures
from modules.calibrated_motor_analysis import CalibratedMotorAnalysis,collect_torque
from modules.motor_waveforms import MotorWaveforms
from modules.motor_workbench import collect_metrics,write_json
from modules.material_library import sha
from modules.workbench_routes import create_workbench
from test_workbench import csv_fixture


class BHAnalysisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): fixtures.CalibratedNativeTests.setUpClass.__func__(cls)
    @classmethod
    def tearDownClass(cls): fixtures.CalibratedNativeTests.tearDownClass.__func__(cls)
    def setUp(self):
        fixtures.CalibratedNativeTests.setUp(self)
        self.imported=self.native.prepare({'preparation':self.parent['id']})
        self.analysis=CalibratedMotorAnalysis(self.native,MotorWaveforms(self.motor),self.store/'calibrated_motor_analysis')

    def prepare(self,measurement='v8_2_cycle1'):
        return self.analysis.prepare(dict(native_import=self.imported['id'],measurement_protocol=measurement))

    def fixture_csv(self,measurement='v8_2_cycle1',zero=False):
        folder=self.root/'official_fixture';folder.mkdir(exist_ok=True);path=folder/'torque.csv'
        count=91 if measurement=='periodic_cycle3_v1' else 31;stop=.03 if count==91 else .01
        with path.open('w',newline='') as stream:
            writer=csv.writer(stream);writer.writerow(['Time [us]','-Moving1.Torque [mNewtonMeter]'])
            for i,t in enumerate(np.linspace(0,stop,count)):writer.writerow([t*1e6,0 if zero else (2+.02*np.cos(i*2*np.pi/30))*1000])
        return path

    def simulated_import(self):
        # Temporary serialized test fixture, not a genuine AEDT project/pass.
        folder=self.native.path(self.imported['id']);data=(folder/'inputs/motor.aedt').read_bytes()+b'\n'+(folder/'inputs/material.amat').read_bytes()
        (folder/'motor.aedt').write_bytes(data)
        write_json(folder/'summary.json',dict(status='native_import_preflight_passed_not_solved',new_native_solves=0,native_validation=1,saved_project_sha256=sha(data)))
        write_json(folder/'state.json',dict(id=self.imported['id'],status='imported_not_solved'))
        return folder

    def test_plan_freezes_actual_bank_exclusion_and_no_native_dispatch(self):
        old={p:p.read_bytes() for p in self.native.path(self.imported['id']).rglob('*') if p.is_file()}
        with patch('subprocess.Popen',side_effect=AssertionError('no native')):plan=self.prepare('periodic_cycle3_v1')
        self.assertEqual(plan['status'],'awaiting_native_material_import')
        self.assertEqual(plan['excluded_grades'],['B30P105']);self.assertEqual(plan['effective_bank_sha256'],self.imported['effective_bank_sha256'])
        self.assertFalse(plan['result_available']);self.assertFalse(plan['native_submission_allowed']);self.assertFalse(plan['efficiency_enabled'])
        self.assertEqual(plan['requested_protocol']['selected_window_ms'],[20,30]);self.assertEqual(plan['requested_protocol']['source_samples'],91)
        self.assertEqual(old,{p:p.read_bytes() for p in old});self.assertEqual(self.motor.jobs(),[])
        self.assertEqual(self.analysis.get(plan['id'])['status'],'awaiting_native_material_import')
        with zipfile.ZipFile(self.analysis.bundle(plan['id'])) as archive:
            self.assertEqual(archive.read('inputs/material.amat'),(self.native.path(self.imported['id'])/'inputs/material.amat').read_bytes())
            self.assertNotIn('model/motor.aedt',archive.namelist())

    def test_not_imported_cannot_bind_model_or_claim_solved(self):
        plan=self.prepare();folder=self.analysis.path(plan['id'])
        with self.assertRaisesRegex(ValueError,'实际原生材料导入'):self.analysis.prepare_model(plan['id'])
        self.assertFalse((folder/'model').exists());self.assertFalse(self.analysis.get(plan['id'])['calibrated_material_has_been_solved'])
        manifest=json.loads((folder/'manifest.json').read_text());manifest['efficiency_enabled']=True;write_json(folder/'manifest.json',manifest)
        with self.assertRaisesRegex(ValueError,'范围不符'):self.analysis.get(plan['id'])

    def test_invalid_inputs_or_modified_material_not_accepted(self):
        for data in [None,{},dict(native_import='../outside'),dict(native_import=self.imported['id'],cores=True),dict(native_import=self.imported['id'],measurement_protocol=[])]:
            with self.assertRaises(ValueError):self.analysis.prepare(data)
        self.assertEqual(list(self.analysis.storage.iterdir()),[])
        plan=self.prepare();p=self.analysis.path(plan['id'])/'inputs/material.amat';p.write_bytes(p.read_bytes()+b'\n')
        with self.assertRaisesRegex(ValueError,'输入改变'):self.analysis.get(plan['id'])
        self.assertEqual(self.analysis.records()[0]['status'],'invalid')

    def test_simulated_import_allows_new_snapshot_and_never_overwrites(self):
        plan=self.prepare();origin=self.simulated_import();before=(origin/'motor.aedt').read_bytes()
        audit=dict(local_CS_verified=True,moving_insert_count=48,core_loss={'complete_insert_coverage':True},
                   inserts=[dict(material=plan['material_name']) for _ in range(48)])
        with patch('modules.calibrated_motor_analysis.audit_project',return_value=audit),patch('subprocess.Popen',side_effect=AssertionError('no solve')):
            result=self.analysis.prepare_model(plan['id'])
        self.assertEqual(result['status'],'model_snapshot_ready_not_submitted');self.assertFalse(result['result_available'])
        self.assertEqual((self.analysis.path(plan['id'])/'model/motor.aedt').read_bytes(),before)
        self.assertEqual((origin/'motor.aedt').read_bytes(),before)
        with self.assertRaisesRegex(ValueError,'实际原生材料导入'):self.analysis.prepare_model(plan['id'])
        p=self.analysis.path(plan['id'])/'model/motor.aedt';p.write_bytes(p.read_bytes()+b'\n')
        with self.assertRaisesRegex(ValueError,'工程改变'):self.analysis.get(plan['id'])

    def test_torque_only_units_windows_no_losses_or_efficiency(self):
        path=self.fixture_csv('periodic_cycle3_v1');before=path.read_bytes()
        result=collect_torque(path,'periodic_cycle3_v1')
        self.assertEqual(result['source_n_samples'],91);self.assertEqual(result['n_samples'],31)
        self.assertEqual(result['selected_window_ms'],[20,30]);self.assertTrue(result['periodic_torque_stability']['passed'])
        self.assertFalse(result['periodic_torque_stability']['core_loss_checked'])
        self.assertAlmostEqual(result['T_avg_Nm'],2+.02/31);self.assertIsNone(result['eta_estimate_pct']);self.assertIsNone(result['P_Fe_W'])
        self.assertEqual(path.read_bytes(),before);self.assertFalse(result['optimization_ranking_enabled'])
        zero=self.fixture_csv(zero=True);result=collect_torque(zero);self.assertIsNone(result['K_T_ripple_pct']);self.assertFalse(result['torque_numerical_acceptance'])

    def test_wrong_sign_column_partial_grid_and_protocol_rejected(self):
        path=self.fixture_csv();raw=path.read_bytes();path.write_bytes(raw.replace(b'-Moving1.Torque',b'Moving1.Torque'))
        with self.assertRaisesRegex(ValueError,'转矩符号'):collect_torque(path)
        path.write_bytes(b'\n'.join(raw.splitlines()[:-1]))
        with self.assertRaisesRegex(ValueError,'时间网格'):collect_torque(path)
        path.write_bytes(raw)
        with self.assertRaisesRegex(ValueError,'时间网格'):collect_torque(path,'periodic_cycle3_v1')

    def test_existing_reference_replays_original_metrics_and_bytes_only(self):
        job='scan_123456789abc';folder=self.motor.path(job);case=folder/'case_01';csv_fixture(case)
        original=collect_metrics(case);write_json(case/'result.json',original)
        write_json(folder/'manifest.json',dict(id=job,model_version='fixture_V8_2',measurement_protocol='v8_2_cycle1',cases=[dict(id='case_01',material='M6_reference')]))
        write_json(folder/'state.json',dict(id=job,status='completed'))
        reference=self.analysis.reference(job,'case_01')
        self.assertEqual(reference['source_scope'],'existing_library_reference_not_calibrated_material_result')
        for key in ('T_avg_Nm','T_min_Nm','T_max_Nm','K_T_ripple_pct'):self.assertEqual(reference['metrics'][key],original[key])
        self.assertIsNone(reference['metrics']['eta_estimate_pct']);self.assertFalse(reference['calibrated_material_has_been_solved'])
        self.assertEqual(self.analysis.reference_csv(job,'case_01').getvalue(),(case/'reports/Torque Plots.csv').read_bytes())
        (case/'reports/Torque Plots.csv').write_bytes((case/'reports/Torque Plots.csv').read_bytes()+b'\n')
        with self.assertRaisesRegex(ValueError,'哈希改变'):self.analysis.reference(job,'case_01')

    def test_cpu_routes_create_reopen_download_but_cannot_solve_or_bind_unimported(self):
        app=Flask('bh_cpu')
        with patch.dict(os.environ,{'MAGSIM_CPU_ONLY':'1','MAGSIM_AUTO_RESUME_QUEUE':'0'}):
            app.register_blueprint(create_workbench(fixtures.fixtures.PROJECT,self.store,self.root))
        client=app.test_client();url='/api/workbench/calibrated-motor/analysis-plans'
        r=client.post(url,json=dict(native_import=self.imported['id']));self.assertEqual(r.status_code,200,r.json);artifact=r.json['id']
        self.assertEqual(client.get(url+'/'+artifact).json['status'],'awaiting_native_material_import')
        self.assertEqual(client.post(url+'/'+artifact+'/prepare-model',json={}).status_code,400)
        with patch('subprocess.Popen') as start:self.assertEqual(client.post(url+'/'+artifact+'/start',json={}).status_code,423)
        start.assert_not_called();self.assertEqual(client.get('/api/workbench/motor/jobs').json,[])
        response=client.get(url+'/'+artifact+'/bundle');self.assertEqual(response.status_code,200);response.close()


if __name__=='__main__':unittest.main()
