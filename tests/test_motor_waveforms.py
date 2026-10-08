"""Official units, version isolation, missing cases and unmodified source downloads."""
import csv
import io
import json
import os
import shutil
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
from modules.motor_waveforms import MotorWaveforms
from modules.motor_workbench import MotorWorkbench,collect_metrics,write_json
from modules.workbench_routes import create_workbench
from test_workbench import csv_fixture


class MotorWaveformTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.storage=self.root/'storage'
        self.motor=MotorWorkbench(self.root,self.storage/'motor',PROJECT)
        self.manager=MotorWaveforms(self.motor)
        self.job='scan_123456789abc'
        self.case=self.make_job(self.job)
        self.selection=dict(selection=[dict(dataset=self.job,case='case_01')])

    def make_job(self,job,version='test_v8_2',protocol='v8_2_cycle1'):
        folder=self.motor.path(job)
        case=folder/'case_01'
        csv_fixture(case)
        if protocol=='periodic_cycle3_v1':
            time=np.linspace(0,.03,91)
            for name,head,rows in [
                ('reports/Torque Plots.csv',['Time [ms]','-Moving1.Torque [NewtonMeter]'],[(t*1000,2+.02*np.cos(i*2*np.pi/30)) for i,t in enumerate(time)]),
                ('maxwell_reports/CoreLoss.csv',['Time [ns]','CoreLoss [mW]'],[(t*1e9,0 if i==0 else 1000) for i,t in enumerate(time)]),
                ('maxwell_reports/StrandedLoss.csv',['Time [s]','StrandedLoss [W]'],[(t,1) for t in time])]:
                with (case/name).open('w',newline='') as stream:
                    writer=csv.writer(stream);writer.writerow(head);writer.writerows(rows)
        write_json(case/'result.json',collect_metrics(case,protocol))
        write_json(folder/'state.json',dict(id=job,status='completed',created_utc=job))
        write_json(folder/'manifest.json',dict(id=job,model_version=version,measurement_protocol=protocol,
                   cases=[dict(id='case_01',material='fixture')]))
        return case

    def test_raw_units_loss_start_row_and_metrics_unchanged(self):
        result,_=self.manager.compare(self.selection)
        r=result['records'][0]
        self.assertAlmostEqual(r['time_ms'][-1],10)
        self.assertEqual(r['series']['core_loss'][0],0)
        self.assertEqual(r['series']['core_loss'][1],1)
        self.assertEqual(r['series']['copper_loss'][0],31)
        self.assertAlmostEqual(r['metrics']['P_Fe_W'],1)
        self.assertAlmostEqual(r['metrics']['P_Cu_W'],1)
        self.assertNotIn('solid_loss',r['series'])
        self.assertEqual(result['new_native_solves'],0)

    def test_export_original_bytes_reopen_and_csv_source_binding(self):
        before={str(p):p.read_bytes() for p in self.case.rglob('*') if p.is_file()}
        reopened=MotorWaveforms(MotorWorkbench(self.root,self.storage/'motor',PROJECT))
        with zipfile.ZipFile(reopened.export(self.selection)) as z:
            self.assertEqual(z.read(self.job+'/case_01/CoreLoss.csv'),(self.case/'maxwell_reports/CoreLoss.csv').read_bytes())
            payload=json.loads(z.read('comparison.json'))
            self.assertFalse(payload['raw_results_changed'])
            self.assertEqual(len(list(csv.DictReader(io.StringIO(z.read('comparison.csv').decode('utf-8-sig'))))),31)
            self.assertTrue(all('C:\\' not in n for n in z.namelist()))
        self.assertEqual(before,{str(p):p.read_bytes() for p in self.case.rglob('*') if p.is_file()})

    def test_changed_csv_or_metric_cannot_be_plotted(self):
        f=self.case/'maxwell_reports/CoreLoss.csv'
        original=f.read_bytes();f.write_bytes(original+b'\n')
        with self.assertRaisesRegex(ValueError,'哈希'):
            self.manager.compare(self.selection)
        f.write_bytes(original)
        p=self.case/'result.json';r=json.loads(p.read_text());r['T_avg_Nm']+=.01;write_json(p,r)
        with self.assertRaisesRegex(ValueError,'指标不符'):
            self.manager.compare(self.selection)

    def test_protocol_model_and_running_job_cannot_mix(self):
        for version,protocol in [('other_model','v8_2_cycle1'),('test_v8_2','periodic_cycle3_v1')]:
            self.make_job('scan_abcdef123456',version,protocol)
            with self.assertRaisesRegex(ValueError,'模型版本和测量协议'):
                self.manager.compare(dict(selection=self.selection['selection']+[dict(dataset='scan_abcdef123456',case='case_01')]))
        write_json(self.motor.path(self.job)/'state.json',dict(id=self.job,status='running'))
        with self.assertRaisesRegex(ValueError,'扫描结束'):
            self.manager.compare(self.selection)

    def test_periodic_whole_raw_curve_and_metric_window_preserved(self):
        job='scan_abcdef123456';self.make_job(job,protocol='periodic_cycle3_v1')
        result,_=self.manager.compare(dict(selection=[dict(dataset=job,case='case_01')]))
        r=result['records'][0]
        self.assertEqual(len(r['time_ms']),91)
        np.testing.assert_allclose(r['selected_window_ms'],[20,30])
        self.assertEqual(len(r['metrics']['time_ms']),31)
        self.assertTrue(r['metrics']['periodic_stability']['passed'])

    def test_failed_missing_cases_visible_without_fabricated_curve(self):
        f=self.motor.path(self.job)/'manifest.json';m=json.loads(f.read_text());m['cases'].append(dict(id='case_02',material='failed'));write_json(f,m)
        rows=self.manager.catalog()['datasets'][0]['cases']
        self.assertFalse(rows[1]['available'])
        self.assertIn('完整官方结果',rows[1]['reason'])
        with self.assertRaisesRegex(ValueError,'没有完整官方结果'):
            self.manager.compare(dict(selection=[dict(dataset=self.job,case='case_02')]))

    def test_invalid_selection_and_path_traversal_rejected(self):
        invalid=[None,{},dict(selection=[]),dict(selection=self.selection['selection']*2),
            dict(selection=[dict(dataset='v8_1',case='case_01')]),dict(selection=[dict(dataset=self.job,case='../case_01')]),
            dict(selection=[dict(dataset=self.job,case=1)]),dict(selection=self.selection['selection'],extra=True)]
        for data in invalid:
            with self.subTest(data=data),self.assertRaises(ValueError):self.manager.compare(data)

    def test_legacy_alias_only_explicit_old_manifest(self):
        f=self.motor.path(self.job)/'manifest.json';m=json.loads(f.read_text());del m['measurement_protocol'];write_json(f,m)
        f=self.case/'result.json';r=json.loads(f.read_text());r['metric_protocol']='v8_2_torque_all_core_drop_first_copper_all_v1';write_json(f,r)
        payload,_=self.manager.compare(self.selection)
        self.assertEqual(payload['measurement_protocol'],'v8_2_cycle1')
        self.assertIsNotNone(payload['records'][0]['legacy_protocol_alias'])
        m['measurement_protocol']='v8_2_cycle1';write_json(self.motor.path(self.job)/'manifest.json',m)
        with self.assertRaisesRegex(ValueError,'协议不同'):self.manager.compare(self.selection)

    def test_archive_links_are_final_only_and_report_missing_remains_visible(self):
        parent=self.root/'motor/v8_2/cases/archive_one'
        shutil.copytree(self.case/'reports',parent/'final_resolved_reports')
        shutil.copytree(self.case/'maxwell_reports',parent/'final_resolved_maxwell_reports')
        computed=collect_metrics(self.case);write_json(parent/'final_resolved_official_report.json',dict(thesis_metrics=dict(computed,eta_pct=computed['eta_estimate_pct'])))
        missing=parent.parent/'missing';write_json(missing/'final_resolved_official_report.json',dict(thesis_metrics={}))
        archived=self.manager.catalog()['datasets'][0]['cases']
        self.assertEqual(len(archived),2)
        self.assertFalse(archived[1]['available'])
        payload,_=self.manager.compare(dict(selection=[dict(dataset='v8_2_archive',case='archive_one')]))
        links=payload['records'][0]['files']
        self.assertIn('final_resolved_reports/Torque%20Plots.csv',links['torque'])
        self.assertIn('final_resolved_maxwell_reports/CoreLoss.csv',links['core_loss'])
        with self.assertRaises(ValueError):self.manager.compare(dict(selection=[dict(dataset='v8_2_archive',case='missing')]))

    def test_read_changed_between_metric_verification_and_waveform_read_rejected(self):
        original=self.manager._paths
        def changed(*args):
            paths,record=original(*args)
            f=paths['core_loss'];f.write_text(f.read_text()+'\n')
            return paths,record
        with patch.object(self.manager,'_paths',changed),self.assertRaisesRegex(ValueError,'源哈希不符'):
            self.manager.compare(self.selection)

    def test_cpu_routes_download_waveforms_without_native_submission(self):
        app=Flask('waveform_cpu',template_folder=str(PROJECT/'templates'),static_folder=str(PROJECT/'static'))
        with patch.dict(os.environ,{'MAGSIM_CPU_ONLY':'1','MAGSIM_AUTO_RESUME_QUEUE':'0'}):
            app.register_blueprint(create_workbench(PROJECT,self.storage,self.root))
            client=app.test_client()
            self.assertEqual(client.get('/motor-waveforms').status_code,200)
            asset=client.get('/static/js/motor_waveforms.js')
            self.assertEqual(asset.status_code,200)
            asset.close()
            self.assertEqual(client.get('/api/workbench/waveforms/catalog').status_code,200)
            self.assertEqual(client.post('/api/workbench/waveforms/compare',json=self.selection).status_code,200)
            response=client.post('/api/workbench/waveforms/export',json=self.selection)
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.mimetype,'application/zip')
            self.assertEqual(client.post('/api/workbench/motor/jobs/'+self.job+'/submit').status_code,423)


if __name__=='__main__':unittest.main()
