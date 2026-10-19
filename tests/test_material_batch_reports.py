"""Derived summaries preserve source CSV/JSON bytes and missing-value meaning."""
import io
import json
import unittest
import zipfile
from types import SimpleNamespace

import test_material_motor_chain as fixtures
from modules.material_batch_reports import report_bundle, summary_markdown, torque_findings, metrics_nonpositive_points
from modules.material_library import sha


class BatchReportTests(unittest.TestCase):
    def record(self):
        return dict(id='bhbatch_0123456789ab',status='completed_with_failures',measurement_protocol='v8_2_cycle1',
            efficiency_enabled=False,optimization_ranking_enabled=False,
            totals=dict(materials=2,actual_torque_results=1,new_completed_motor_solves=0),items=[
                dict(id='item_01',source=dict(material_name='WB_zero',effective_bank_sha256='bank',excluded_grades=['B30P105']),
                    selection=dict(calibration='cal_a',key='prediction:pred_a'),status='reused_result',result_available=True,
                    execution_id='bhexec_a',model_version='fixture_BH_v1',metrics=dict(T_avg_Nm=0,T_min_Nm=0,T_max_Nm=0,K_T_ripple_pct=None)),
                dict(id='item_02',source=dict(material_name='WB_pending',effective_bank_sha256='bank2',excluded_grades=[]),
                    selection=dict(calibration='cal_a',key='fit:B23R075'),status='failed',result_available=False,error='native | failure')])

    def package(self,corrupt=False,unsafe=False):
        record=self.record();files={'batch.json':json.dumps(record).encode(),'summary.csv':b'original-summary\r\n',
            'item_01/Torque Plots.csv':b'Time,Torque\r\n0,0\r\n'}
        if unsafe:files['../bad']=b'bad'
        manifest=dict(files={n:sha(b) for n,b in files.items()},efficiency_enabled=False,optimization_ranking_enabled=False)
        if corrupt:files['summary.csv']=b'changed'
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,'w') as z:
            for n,b in files.items():z.writestr(n,b)
            z.writestr('manifest.json',json.dumps(manifest))
        stream.seek(0);return stream

    def test_report_preserves_original_members_and_binds_derived_text(self):
        source=self.package();before=source.getvalue();manager=SimpleNamespace(bundle=lambda artifact:io.BytesIO(before))
        result=report_bundle(manager,'bhbatch_0123456789ab')
        with zipfile.ZipFile(source) as original,zipfile.ZipFile(result) as report:
            self.assertEqual(report.read('item_01/Torque Plots.csv'),original.read('item_01/Torque Plots.csv'))
            self.assertEqual(report.read('batch.json'),original.read('batch.json'))
            self.assertEqual(report.read('summary.csv'),original.read('summary.csv'))
            self.assertEqual(report.read('source_manifest.json'),original.read('manifest.json'))
            manifest=json.loads(report.read('manifest.json'))
            for name,digest in manifest['files'].items():self.assertEqual(sha(report.read(name)),digest)
            self.assertEqual(manifest['new_native_solves'],0)
            text=report.read('summary.md').decode();self.assertIn('0.000000',text);self.assertIn('—',text)
            self.assertIn('native \\| failure',text);self.assertIn('B30P105',text);self.assertIn('prediction:pred_a',text)
            self.assertIn('未做材料排名',text)
        self.assertEqual(source.getvalue(),before)

    def test_source_hash_corruption_or_unsafe_path_is_rejected(self):
        for stream in (self.package(corrupt=True),self.package(unsafe=True)):
            with self.assertRaises(ValueError):report_bundle(SimpleNamespace(bundle=lambda artifact:stream),'test')

    def test_unavailable_result_does_not_reuse_spurious_metrics(self):
        record=self.record();record['items'][1]['metrics']=dict(T_avg_Nm=999)
        text=summary_markdown(record);self.assertNotIn('999',text);self.assertIn('failed',text)

    def test_nonfinite_real_result_is_not_summarized(self):
        record=self.record();record['items'][0]['metrics']['T_avg_Nm']=float('nan')
        with self.assertRaisesRegex(ValueError,'有限'):summary_markdown(record)

    def test_saved_negative_torque_and_ripple_failure_is_explained_without_repair(self):
        record=self.record();m=record['items'][0]['metrics'];m.update(T_avg_Nm=1.6,T_min_Nm=-4.5,T_max_Nm=2.5,
            K_T_ripple_pct=437.5,torque_numerical_acceptance=False)
        before=json.dumps(m);text=summary_markdown(record)
        self.assertIn('存在非正转矩点',text);self.assertIn('原100%上限',text)
        self.assertIn('-4.500000',text);self.assertEqual(before,json.dumps(m))
        self.assertEqual(torque_findings(dict(m,torque_numerical_acceptance=True)),[])

    def test_original_point_times_are_summarized_and_missing_arrays_remain_unknown(self):
        record=self.record();m=record['items'][0]['metrics'];m.update(time_ms=[0,1,2],torque_Nm=[0,-2,3],
            selected_window_ms=[1.5,2],torque_numerical_acceptance=True)
        before=json.dumps(m);points=metrics_nonpositive_points(m)
        self.assertEqual([p['time_ms'] for p in points],[0,1]);self.assertFalse(points[1]['within_metric_window'])
        self.assertIn('1.000000 ms / -2.000000 N·m',summary_markdown(record));self.assertEqual(before,json.dumps(m))
        self.assertIsNone(metrics_nonpositive_points({}));self.assertEqual(metrics_nonpositive_points(dict(time_ms=[0,1],torque_Nm=[1,2])),[])

    def test_incomplete_or_nonfinite_point_arrays_do_not_get_silent_counts(self):
        for metrics in [dict(time_ms=[0,1]),dict(time_ms=[0,1],torque_Nm=[1]),
                        dict(time_ms=[0,0],torque_Nm=[1,2]),dict(time_ms=[0,1],torque_Nm=[1,float('nan')])]:
            with self.assertRaises(ValueError):metrics_nonpositive_points(metrics)


if __name__=='__main__':unittest.main()
