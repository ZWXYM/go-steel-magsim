"""Derived summaries preserve source CSV/JSON bytes and missing-value meaning."""
import copy
import csv
import io
import json
import math
import unittest
import zipfile
from types import SimpleNamespace
from unittest.mock import patch

from modules.material_batch_reports import (report_bundle, summary_markdown, torque_findings,
    metrics_nonpositive_points, material_inputs, result_counts, verified_operating_point,
    operating_points, operating_points_csv)
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

    def test_result_counts_separate_reuse_and_saved_acceptance(self):
        record=self.record();self.assertEqual(result_counts(record),dict(reused=1,passed=0,failed=0,unknown=1))
        record['items'][0]['metrics']['torque_numerical_acceptance']=False
        record['items'][1].update(result_available=True,status='completed',metrics=dict(torque_numerical_acceptance=True))
        self.assertEqual(result_counts(record),dict(reused=1,passed=1,failed=1,unknown=0))

    def identity_fixture(self):
        record=self.record()
        for row in record['items']:
            row['source'].update(parent_bank_sha256='parent',training_grades=['B23R075'],AMAT_sha256='amat',metadata_sha256='meta')
        details=[dict(row['source'],source_material_id='CUSTOM_'+str(i),display_name='CUSTOM_'+str(i),
            input_params=dict(f_Goss=.855,theta_0_deg=4.5,halfwidth_deg=6.5,Si_content=3.1),
            input_parameter_source='保存的建模参数',input_identity_scope='saved_model_parameters_not_measurement_certification',
            files={'material.amat':dict(sha256='amat'),'metadata.json':dict(sha256='meta')}) for i,row in enumerate(record['items'])]
        return record,details

    def test_verified_identity_is_exported_without_mutating_original_batch(self):
        record,details=self.identity_fixture();original=json.dumps(record)
        manager=SimpleNamespace(chain=SimpleNamespace(library=object()))
        with patch('modules.material_batch_reports.MaterialInputView') as view:
            view.return_value.detail.side_effect=details
            identities=material_inputs(manager,record)
        self.assertEqual(identities['entries'][0]['source_material_id'],'CUSTOM_0')
        self.assertEqual(identities['entries'][0]['input_params']['Si_content'],3.1)
        self.assertEqual(identities['entries'][0]['identity_status'],'verified_saved_input')
        self.assertIn('CUSTOM_0',summary_markdown(record,identities))
        self.assertEqual(json.dumps(record),original)
        package=self.package();manager.bundle=lambda artifact:package
        with patch('modules.material_batch_reports.material_inputs',return_value=identities):
            result=report_bundle(manager,'test')
        with zipfile.ZipFile(result) as archive:
            mapping=json.loads(archive.read('material_inputs.json'));self.assertEqual(mapping,identities)
            rows=list(csv.DictReader(io.StringIO(archive.read('material_inputs.csv').decode('utf-8-sig'))))
            self.assertEqual(rows[0]['source_material_id'],'CUSTOM_0');self.assertEqual(rows[0]['f_Goss'],'0.855')

    def test_identity_mismatch_never_attaches_unverified_parameters(self):
        record,details=self.identity_fixture();manager=SimpleNamespace(chain=SimpleNamespace(library=object()))
        for mutate in (lambda d:d.update(effective_bank_sha256='changed'),lambda d:d['files']['material.amat'].update(sha256='changed')):
            changed=copy.deepcopy(details);mutate(changed[0])
            with patch('modules.material_batch_reports.MaterialInputView') as view:
                view.return_value.detail.side_effect=changed
                identities=material_inputs(manager,record)
            self.assertEqual(identities['entries'][0]['identity_status'],'unavailable')
            self.assertIsNone(identities['entries'][0]['input_params']);self.assertIsNone(identities['entries'][0]['source_material_id'])
            self.assertEqual(identities['entries'][1]['identity_status'],'verified_saved_input')
            self.assertIn('不符',identities['entries'][0]['identity_error'])

    def test_old_package_missing_identity_still_downloads_original_data(self):
        package=self.package();manager=SimpleNamespace(bundle=lambda artifact:package)
        with zipfile.ZipFile(report_bundle(manager,'test')) as archive:
            identities=json.loads(archive.read('material_inputs.json'))
            self.assertTrue(all(e['identity_status']=='unavailable' and e['source_material_id'] is None for e in identities['entries']))
            self.assertEqual(archive.read('item_01/Torque Plots.csv'),b'Time,Torque\r\n0,0\r\n')
            self.assertIn('不补写参数',archive.read('summary.md').decode())

    def power_fixture(self):
        record=self.record();item=record['items'][0]
        item.update(execution_id='bhexec_aaaaaaaaaaaa',measurement_protocol=record['measurement_protocol'],
            records=dict(execution=dict(id='bhexec_aaaaaaaaaaaa'),analysis_plan=dict(id='bhanalysis_bbbbbbbbbbbb')))
        item['metrics']['torque_csv_sha256']='a'*64
        execution=dict(id=item['execution_id'],analysis_plan_id='bhanalysis_bbbbbbbbbbbb',result_available=True,
            material_name=item['source']['material_name'],effective_bank_sha256=item['source']['effective_bank_sha256'],
            excluded_grades=list(item['source']['excluded_grades']),measurement_protocol=item['measurement_protocol'],
            requested_protocol=dict(speed_rpm=3000),result=dict(material_name=item['source']['material_name'],
                model_version=item['model_version'],native_configuration_verified=True,native_analyze_succeeded=True,
                metrics=copy.deepcopy(item['metrics'])))
        return record,execution

    def test_power_uses_verified_saved_speed_and_preserves_zero_and_signed_torque(self):
        record,execution=self.power_fixture();item=record['items'][0];before=copy.deepcopy((record,execution))
        point=verified_operating_point(item,execution)
        self.assertEqual(point,dict(speed_rpm=3000,electromagnetic_mechanical_power_W=0))
        self.assertEqual((record,execution),before)
        for torque in (-2,2):
            item['metrics']['T_avg_Nm']=execution['result']['metrics']['T_avg_Nm']=torque
            execution['requested_protocol']['speed_rpm']=1500
            self.assertAlmostEqual(verified_operating_point(item,execution)['electromagnetic_mechanical_power_W'],torque*50*math.pi)

    def test_missing_nonfinite_or_unverified_operating_point_never_uses_default_speed(self):
        for speed in (None,0,-1,True,'3000',float('nan'),float('inf')):
            record,execution=self.power_fixture();execution['requested_protocol']['speed_rpm']=speed
            with self.subTest(speed=speed),self.assertRaises(ValueError):verified_operating_point(record['items'][0],execution)
        for key in ('native_configuration_verified','native_analyze_succeeded'):
            record,execution=self.power_fixture();execution['result'][key]=False
            with self.subTest(key=key),self.assertRaises(ValueError):verified_operating_point(record['items'][0],execution)
        record,execution=self.power_fixture();execution['requested_protocol']={}
        with self.assertRaises(ValueError):verified_operating_point(record['items'][0],execution)
        for torque in (True,'2',float('nan'),float('inf')):
            record,execution=self.power_fixture();record['items'][0]['metrics']['T_avg_Nm']=execution['result']['metrics']['T_avg_Nm']=torque
            with self.subTest(torque=torque),self.assertRaises(ValueError):verified_operating_point(record['items'][0],execution)

    def test_power_rejects_other_execution_plan_material_bank_scope_protocol_model_and_csv(self):
        mutations=(lambda e:e.update(id='bhexec_cccccccccccc'),lambda e:e.update(analysis_plan_id='another'),
            lambda e:e.update(material_name='other'),lambda e:e.update(effective_bank_sha256='other'),
            lambda e:e.update(excluded_grades=[]),lambda e:e.update(measurement_protocol='other'),
            lambda e:e['result'].update(model_version='other'),lambda e:e['result'].update(material_name='other'),
            lambda e:e['result']['metrics'].update(torque_csv_sha256='b'*64),
            lambda e:e['result']['metrics'].update(T_avg_Nm=1))
        for mutate in mutations:
            record,execution=self.power_fixture();mutate(execution)
            with self.subTest(mutation=mutate),self.assertRaises(ValueError):verified_operating_point(record['items'][0],execution)

    def test_operating_point_reader_skips_pending_and_leaves_unreadable_values_blank(self):
        record,execution=self.power_fixture();record['items'][1]['metrics']=dict(T_avg_Nm=999)
        reads=[]
        def read(key):reads.append(key);raise OSError('record unavailable')
        manager=SimpleNamespace(chain=SimpleNamespace(execution=SimpleNamespace(get=read)))
        before=copy.deepcopy(record);points=operating_points(manager,record)
        self.assertEqual(reads,[execution['id']]);self.assertEqual(points['entries'][0]['status'],'unverified')
        self.assertIsNone(points['entries'][0]['speed_rpm']);self.assertIsNone(points['entries'][0]['electromagnetic_mechanical_power_W'])
        self.assertIsNone(points['entries'][1]['T_avg_Nm']);self.assertEqual(points['entries'][1]['status'],'unavailable')
        rows=list(csv.DictReader(io.StringIO(operating_points_csv(points).decode('utf-8-sig'))))
        self.assertEqual(rows[0]['speed_rpm'],'');self.assertEqual(rows[0]['electromagnetic_mechanical_power_W'],'')
        self.assertEqual(record,before);self.assertEqual(points['new_native_solves'],0)

    def test_power_csv_and_json_add_to_report_without_changing_original_member_bytes(self):
        record,execution=self.power_fixture();execution['result']['metrics']['T_avg_Nm']=record['items'][0]['metrics']['T_avg_Nm']=-2
        record['items'][0]['metrics']['torque_numerical_acceptance']=False
        with patch.object(self,'record',return_value=record):source=self.package()
        source_bytes=source.getvalue();manager=SimpleNamespace(bundle=lambda artifact:io.BytesIO(source_bytes),
            chain=SimpleNamespace(execution=SimpleNamespace(get=lambda key:execution)))
        with zipfile.ZipFile(source) as original,zipfile.ZipFile(report_bundle(manager,'test')) as report:
            for name in ('batch.json','summary.csv','item_01/Torque Plots.csv'):
                self.assertEqual(report.read(name),original.read(name))
            points=json.loads(report.read('operating_points.json'));self.assertFalse(points['efficiency_enabled'])
            row=points['entries'][0];self.assertEqual(row['speed_rpm'],3000)
            self.assertAlmostEqual(row['electromagnetic_mechanical_power_W'],-200*math.pi)
            self.assertIs(row['torque_numerical_acceptance'],False)
            csv_rows=list(csv.DictReader(io.StringIO(report.read('operating_points.csv').decode('utf-8-sig'))))
            self.assertAlmostEqual(float(csv_rows[0]['electromagnetic_mechanical_power_W']),-200*math.pi)
            self.assertEqual(csv_rows[1]['electromagnetic_mechanical_power_W'],'')
            self.assertIn('不是铭牌额定功率',report.read('summary.md').decode())
            manifest=json.loads(report.read('manifest.json'));self.assertEqual(manifest['version'],'calibrated_batch_summary_v4')
            for name,digest in manifest['files'].items():self.assertEqual(sha(report.read(name)),digest)
        self.assertEqual(source.getvalue(),source_bytes)


if __name__=='__main__':unittest.main()
