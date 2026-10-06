"""Material exclusion, native source binding, unchanged geometry and CSV units."""
import csv
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import numpy as np
from flask import Flask

PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT))
from modules.calibration_workbench import CalibrationWorkbench,GRADES
from modules.motor_workbench import inspect_template,collect_metrics,MotorWorkbench,write_json
from modules.workbench_routes import create_workbench

ROOT=PROJECT.parent if PROJECT.name=='magsim' and (PROJECT.parent/'calibration/pilot_20261003_n8/manifest.json').is_file() else PROJECT


def csv_fixture(folder):
    t=np.linspace(0,.01,31)
    data=[('reports/Torque Plots.csv',['Time [ms]','-Moving1.Torque [NewtonMeter]'],[(h*1000,2+.1*np.cos(i)) for i,h in enumerate(t)]),
          ('maxwell_reports/CoreLoss.csv',['Time [ns]','CoreLoss [mW]'],[(h*1e9,0 if i==0 else 1000) for i,h in enumerate(t)]),
          ('maxwell_reports/StrandedLoss.csv',['Time [s]','StrandedLoss [W]'],[(h,31 if i==0 else 0) for i,h in enumerate(t)])]
    for name,head,rows in data:
        path=folder/name
        path.parent.mkdir(parents=True,exist_ok=True)
        with path.open('w',newline='') as stream:
            writer=csv.writer(stream)
            writer.writerow(head)
            writer.writerows(rows)


class Workbench(unittest.TestCase):
    def test_material_fit_holdout_and_source_files_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager=CalibrationWorkbench(ROOT,Path(tmp))
            bank=(manager.pilot/'calibration_bank.json').read_bytes()
            report=manager.calibrate(list(GRADES))
            self.assertLess(report['calibration_fit']['max_abs_B800_error_T'],.05)
            self.assertAlmostEqual(report['calibration_fit']['mean_rmse_T'],.010740501742069954)
            self.assertAlmostEqual(report['material_holdout']['mean_rmse_T'],.0658125792273107)
            for c in report['comparisons']:
                self.assertNotIn(c['grade'],c['holdout_weights'])
            self.assertEqual(bank,(manager.pilot/'calibration_bank.json').read_bytes())
            with zipfile.ZipFile(manager.get(report['id'])/report['bundle_file']) as bundle:
                self.assertEqual(len([n for n in bundle.namelist() if n.endswith('.amat')]),4)
                self.assertIn('curves.csv',bundle.namelist())
                self.assertEqual(json.loads(bundle.read('bank.json'))['physics_version'],report['physics_version'])
            pair=json.loads((manager.pilot/'B30P105/raw_pair.json').read_text())
            result=manager.predict(report['id'],pair)
            self.assertTrue((manager.get(report['id'])/result['id']/result['AMAT_file']).exists())
            self.assertTrue(result['parameter_support']['inside_joint_parameter_support'])
            self.assertIn('B30P105',result['parameter_support']['matched_anchor_grades'])
            metadata=json.loads((manager.get(report['id'])/result['id']/result['AMAT_file']).with_suffix('.metadata.json').read_text())
            self.assertEqual(metadata['parameter_support'],result['parameter_support'])
            self.assertFalse(metadata['parameter_support']['independent_material_accuracy_verified'])
            changed={**pair,'simulation_physics_version':'legacy_uniaxial'}
            with self.assertRaisesRegex(ValueError,'物理版本'):
                manager.predict(report['id'],changed)
            changed={**pair,'texture_sampling_version':'goss_haar_iid_prefix_v2'}
            with self.assertRaisesRegex(ValueError,'采样协议'):
                manager.predict(report['id'],changed)
            changed={**pair,'material_id':'unknown_new_material'}
            with self.assertRaisesRegex(ValueError,'采样协议'):
                manager.predict(report['id'],changed)

    def test_single_grade_and_path_traversal_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager=CalibrationWorkbench(ROOT,Path(tmp))
            with self.assertRaises(ValueError):
                manager.calibrate([GRADES[0]])
            with self.assertRaises(ValueError):
                manager.get('../motor')
            app=Flask('fixture')
            app.register_blueprint(create_workbench(PROJECT,Path(tmp),ROOT))
            client=app.test_client()
            self.assertEqual(client.get('/api/workbench/samples').status_code,200)
            self.assertEqual(client.post('/api/workbench/calibrate',json={'grades':[GRADES[0]]}).status_code,400)
            self.assertIn(client.get('/api/workbench/files/calibration/../../README.md').status_code,(400,404))
            self.assertEqual(client.post('/api/workbench/motor/jobs',json={'materials':['malicious_material\"']}).status_code,400)

    def test_patch_changes_only_material_of_48_objects_and_requires_local_CS(self):
        source='\n'.join(f"$begin 'GeometryPart'\nName='GOES_V3_{i:02d}'\nPartCoordinateSystem={i+2}\nMaterialValue='\"old\"'\nX='{i}mm'\n$end 'GeometryPart'" for i in range(1,49))
        patched,quality=inspect_template(source,'new')
        self.assertEqual(quality['object_count'],48)
        self.assertEqual(patched.replace('MaterialValue=\'"new"\'','MaterialValue=\'"old"\''),source)
        with self.assertRaisesRegex(ValueError,'局部坐标系'):
            inspect_template(source.replace('PartCoordinateSystem=3','PartCoordinateSystem=1',1))
        with self.assertRaisesRegex(ValueError,'48'):
            inspect_template(source.split("$begin 'GeometryPart'",1)[0])

    def test_official_csv_units_time_grid_and_loss_conventions(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            csv_fixture(p)
            result=collect_metrics(p)
            self.assertTrue(result['accepted'])
            self.assertAlmostEqual(result['P_Fe_W'],1.)
            self.assertAlmostEqual(result['P_Cu_W'],1.)
            self.assertAlmostEqual(result['time_ms'][-1],10.)
            loss=p/'maxwell_reports/CoreLoss.csv'
            # Use a guaranteed change even if float serialization differs.
            with loss.open() as stream:
                rows=list(csv.reader(stream))
            rows[2][0]=str(float(rows[2][0])+10000)
            with loss.open('w',newline='') as stream:
                csv.writer(stream).writerows(rows)
            with self.assertRaisesRegex(ValueError,'时间网格'):
                collect_metrics(p)

    def test_prepare_does_not_solve_or_mutate_template_and_duplicate_submit_rejected(self):
        if not (ROOT/'motor').is_dir():
            self.skipTest('Private motor archive is intentionally absent from public clone')
        with tempfile.TemporaryDirectory() as tmp:
            manager=MotorWorkbench(ROOT,Path(tmp),PROJECT)
            original=manager.template.read_bytes()
            with patch('modules.motor_workbench.subprocess.run') as proc:
                state=manager.prepare(['AK Steel - M-6 Carlite'])
                proc.assert_not_called()
                self.assertEqual(original,manager.template.read_bytes())
                with patch('modules.motor_workbench.threading.Thread'),patch('modules.motor_workbench.license_status',return_value={'available':True}):
                    self.assertEqual(manager.submit(state['id'])['status'],'queued')
                    with self.assertRaisesRegex(ValueError,'已提交'):
                        manager.submit(state['id'])
            self.assertFalse((manager.path(state['id'])/'case_01/motor.aedtresults').exists())

    def test_license_failure_blocks_submit_without_mutating_prepared_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager=MotorWorkbench(ROOT,Path(tmp),PROJECT)
            write_json(manager.path('scan_0123456789ab')/'state.json',dict(id='scan_0123456789ab',status='prepared'))
            with patch('modules.motor_workbench.Path.is_file',return_value=True),patch('modules.motor_workbench.license_status',return_value={'available':False,'reason':'许可证未连接'}):
                with self.assertRaisesRegex(ValueError,'许可证'):
                    manager.submit('scan_0123456789ab')
            self.assertEqual(manager.state('scan_0123456789ab')['status'],'prepared')

    def test_periodic_protocol_preserves_startup_but_requires_full_cycles_and_stability(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            t=np.linspace(0,.03,91)
            raw=2+.02*np.cos(np.arange(91)*2*np.pi/30)
            raw[1]=-200 # Full raw CSV remains; predeclared third cycle excludes startup.
            for name,label,data in [('reports/Torque Plots.csv','-Moving1.Torque [NewtonMeter]',raw),
                ('maxwell_reports/CoreLoss.csv','CoreLoss [W]',np.ones(91)*10),
                ('maxwell_reports/StrandedLoss.csv','StrandedLoss [W]',np.ones(91)*50)]:
                file=p/name
                file.parent.mkdir(parents=True,exist_ok=True)
                with file.open('w',newline='') as stream:
                    writer=csv.writer(stream)
                    writer.writerow(['Time [s]',label])
                    writer.writerows(zip(t,data))
            source=(p/'reports/Torque Plots.csv').read_bytes()
            result=collect_metrics(p,'periodic_cycle3_v1')
            self.assertTrue(result['accepted'])
            self.assertEqual(result['source_n_samples'],91)
            self.assertEqual(result['selected_window_ms'],[20.,30.])
            self.assertEqual(source,(p/'reports/Torque Plots.csv').read_bytes())
            with self.assertRaises(ValueError):
                collect_metrics(p,'v8_2_cycle1')
            with (p/'reports/Torque Plots.csv').open() as stream:
                rows=list(csv.reader(stream))
            rows[76][1]='2.5' # Still physically plausible, but not periodic.
            with (p/'reports/Torque Plots.csv').open('w',newline='') as stream:
                csv.writer(stream).writerows(rows)
            self.assertFalse(collect_metrics(p,'periodic_cycle3_v1')['accepted'])


if __name__=='__main__':
    unittest.main()
