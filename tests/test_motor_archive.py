"""Archived results stay usable without inventing missing final CSV evidence."""
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
from modules.motor_workbench import MotorWorkbench,collect_metrics,write_json
from modules.workbench_routes import create_workbench


class MotorArchive(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'root'
        self.storage=Path(self.temp.name)/'storage'
        self.manager=MotorWorkbench(self.root,self.storage,PROJECT)

    def case(self,name='grade_one',csv_present=True):
        path=self.root/'motor/v8_2/cases'/name
        path.mkdir(parents=True)
        expected=dict(T_avg_Nm=1.8673,K_T_ripple_pct=18.78,P_Fe_W=17.041,P_Cu_W=49.175,eta_pct=89.86)
        if csv_present:
            times=np.linspace(0,.01,31)
            values=2+.1*np.cos(np.arange(31))
            for folder,col,data in [('reports','-Moving1.Torque [NewtonMeter]',values),
                ('maxwell_reports','CoreLoss [W]',np.ones(31)),
                ('maxwell_reports','StrandedLoss [W]',np.ones(31)*20)]:
                name='Torque Plots' if folder=='reports' else col.split(' [')[0]
                out=path/('final_resolved_'+folder)/(name+'.csv')
                out.parent.mkdir(exist_ok=True)
                with out.open('w',newline='') as stream:
                    writer=csv.writer(stream)
                    writer.writerow(['Time [s]',col])
                    writer.writerows(zip(times,data))
            # Parse the identical fixture layout, then put only its exact prefix
            # in the archive; no earlier version is acceptable as a replacement.
            import shutil
            fixture=Path(self.temp.name)/('fixture_'+path.name)
            shutil.copytree(path/'final_resolved_reports',fixture/'reports')
            shutil.copytree(path/'final_resolved_maxwell_reports',fixture/'maxwell_reports')
            expected=collect_metrics(fixture)
            expected['eta_pct']=expected['eta_estimate_pct']
        write_json(path/'final_resolved_official_report.json',dict(thesis_metrics=expected))
        return path

    def test_report_only_metrics_visible_without_csv_acceptance(self):
        path=self.case(csv_present=False)
        # Another prefix exists, but must never be used to fill missing final CSV.
        (path/'resolved_reports').mkdir()
        (path/'resolved_reports/Torque Plots.csv').write_text('wrong version')
        row=self.manager.archived_results()[0]
        self.assertEqual(row['display_metrics']['T_avg_Nm'],1.8673)
        self.assertEqual(row['display_metrics']['eta_estimate_pct'],89.86)
        self.assertFalse(row['csv_verified'])
        self.assertNotIn('metrics',row)
        self.assertEqual(row['metric_source'],'saved_report_only')
        self.assertEqual(list(row['files']),['final_resolved_official_report.json'])

    def test_verified_csv_and_corrupt_record_are_independent(self):
        self.case('good')
        broken=self.case('broken',False)
        (broken/'final_resolved_official_report.json').write_text('{invalid')
        rows={r['case']:r for r in self.manager.archived_results()}
        self.assertTrue(rows['good']['csv_verified'])
        self.assertEqual(rows['good']['metric_source'],'official_CSV_verified')
        self.assertFalse(rows['broken']['csv_verified'])
        self.assertIn('reason',rows['broken'])

    def test_mismatched_summary_remains_report_only(self):
        path=self.case()
        file=path/'final_resolved_official_report.json'
        record=json.loads(file.read_text())
        record['thesis_metrics']['T_avg_Nm']=10
        write_json(file,record)
        row=self.manager.archived_results()[0]
        self.assertFalse(row['csv_verified'])
        self.assertEqual(row['display_metrics']['T_avg_Nm'],10)
        self.assertIn('摘要不符',row['reason'])

    def test_export_contains_both_sources_and_preserves_archive(self):
        self.case('verified')
        self.case('summary_only',False)
        base=self.root/'motor/v8_2/cases'
        before={p.relative_to(base).as_posix():p.read_bytes() for p in base.rglob('*') if p.is_file()}
        with patch('subprocess.run',side_effect=AssertionError('No native solve during export')):
            result=self.manager.export_archive()
        self.assertEqual((result['case_count'],result['csv_verified_count']),(2,1))
        path=self.manager.archive_export_path(result['id'])
        with zipfile.ZipFile(path/'reports_bundle.zip') as bundle:
            self.assertIn('verified/final_resolved_reports/Torque Plots.csv',bundle.namelist())
            self.assertIn('summary_only/final_resolved_official_report.json',bundle.namelist())
            provenance=json.loads(bundle.read('provenance.json'))
            self.assertEqual(provenance['new_native_solves'],0)
            self.assertEqual(len(provenance['source_hashes']),5)
        with (path/'summary.csv').open(encoding='utf-8-sig') as stream:
            rows=list(csv.DictReader(stream))
        self.assertEqual(rows[0]['metric_source'],'saved_report_only')
        self.assertEqual(before,{p.relative_to(base).as_posix():p.read_bytes() for p in base.rglob('*') if p.is_file()})

    def test_changed_payload_stops_export(self):
        case=self.case(csv_present=False)
        original=self.manager.archive_file
        def changed(case_id,name):
            path=original(case_id,name)
            path.write_text('{}')
            return path
        with patch.object(self.manager,'archive_file',side_effect=changed):
            with self.assertRaisesRegex(ValueError,'期间改变'):
                self.manager.export_archive()
        self.assertTrue(list((self.storage/'archive_exports').glob('archive_*')))

    def test_download_whitelist_rejects_native_and_other_versions(self):
        path=self.case(csv_present=False)
        (path/'motor.aedt').write_text('private native project')
        for case_id,name in [('..','README.md'),('grade_one','../motor.aedt'),
            ('grade_one','motor.aedt'),('grade_one','resolved_reports/Torque Plots.csv')]:
            with self.assertRaises(ValueError):
                self.manager.archive_file(case_id,name)
        with self.assertRaises(ValueError):
            self.manager.archive_export_path('archive_000000000000','../state.json')

    def test_cpu_http_export_and_download_do_not_enable_native_submission(self):
        self.case(csv_present=False)
        with patch.dict('os.environ',{'MAGSIM_CPU_ONLY':'1'}):
            with patch('modules.workbench_routes.CalibrationWorkbench'):
                app=Flask('archive_http')
                app.register_blueprint(create_workbench(PROJECT,self.storage,self.root))
                client=app.test_client()
                rows=client.get('/api/workbench/motor/archive').json
                self.assertEqual(len(rows),1)
                source='/api/workbench/motor/archive/grade_one/files/final_resolved_official_report.json'
                response=client.get(source)
                self.assertEqual(response.status_code,200)
                response.close()
                result=client.post('/api/workbench/motor/archive/export',json={})
                self.assertEqual(result.status_code,200)
                for url in result.json['files'].values():
                    response=client.get(url)
                    self.assertEqual(response.status_code,200)
                    response.close()
                self.assertEqual(client.post('/api/workbench/motor/jobs/scan_000000000000/submit').status_code,423)
                self.assertEqual(client.get('/api/workbench/motor/archive/grade_one/files/motor.aedt').status_code,400)


if __name__=='__main__':
    unittest.main()
