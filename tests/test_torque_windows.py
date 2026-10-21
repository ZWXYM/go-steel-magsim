"""Original points, sampling windows, operating identity and exact source downloads."""
import copy
import csv
import io
import json
import math
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from flask import Flask
from modules.calibrated_motor_analysis import collect_torque,protocol,execution_model_version
from modules.torque_windows import TorqueWindows,window_statistics,create_torque_window_routes,digest


class TorqueWindowTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.batch='bhbatch_0123456789ab';self.runs={};self.paths={};items=[]
        for n,letter in enumerate('ab',1):
            run='bhexec_'+letter*12;plan='bhanalysis_'+letter*12;path=self.root/(letter+'.csv');out=io.StringIO(newline='');writer=csv.writer(out);writer.writerow(['Time [ns]','-Moving1.Torque [NewtonMeter]'])
            values=[n+i*.01 for i in range(31)]
            if n==2:values[15]=177
            for i,value in enumerate(values):writer.writerow([i*protocol('v8_2_cycle1')['stop_s']*1e9/30,value])
            path.write_bytes(out.getvalue().encode());metrics=collect_torque(path);source=dict(material_name='SYNTHETIC_'+letter,effective_bank_sha256='a'*64,excluded_grades=[],training_grades=['synthetic'],template_sha256='f'*64)
            row=dict(id='item_%02d'%n,source=source,selection=dict(calibration='cal_fixture',key='synthetic:'+letter),status='completed',result_available=True,metrics=metrics,execution_id=run,measurement_protocol='v8_2_cycle1',model_version=execution_model_version('v8_2_cycle1'),records=dict(execution={'id':run},analysis_plan={'id':plan}),analysis_url='/calibrated-motor/analysis/'+plan)
            execution=dict(id=run,analysis_plan_id=plan,result_available=True,material_name=source['material_name'],effective_bank_sha256=source['effective_bank_sha256'],excluded_grades=[],measurement_protocol=row['measurement_protocol'],requested_protocol=protocol('v8_2_cycle1'),result=dict(metrics=copy.deepcopy(metrics),model_version=row['model_version'],material_name=source['material_name'],native_configuration_verified=True,native_analyze_succeeded=True))
            self.runs[run]=execution;self.paths[run]=path;items.append(row)
        pending=copy.deepcopy(items[0]);pending.update(id='item_03',result_available=False,status='failed',error='synthetic failure');pending.pop('execution_id');pending.pop('metrics');items.append(pending)
        self.record=dict(id=self.batch,status='completed_with_failures',items=items)
        def get(artifact):
            if artifact!=self.batch:raise ValueError('unknown batch')
            return copy.deepcopy(self.record)
        def download(artifact,name):
            if name!='case/reports/Torque Plots.csv':raise ValueError('wrong source filename')
            return self.paths[artifact]
        self.manager=SimpleNamespace(get=get,records=lambda:[copy.deepcopy(self.record)],chain=SimpleNamespace(execution=SimpleNamespace(get=lambda key:copy.deepcopy(self.runs[key]),download=download)))
        self.service=TorqueWindows(self.manager)

    def test_known_interval_statistics_preserve_sign_zero_and_sample_endpoints(self):
        result=window_statistics([0,1,2,3],[-2,0,2,4],[.5,3],3000)
        self.assertEqual(result['sample_indices'],[1,2,3]);self.assertEqual(result['sampled_window_ms'],[1,3]);self.assertEqual(result['sample_mean_torque_Nm'],2);self.assertEqual(result['min_torque_Nm'],0);self.assertEqual(result['peak_to_peak_over_abs_mean_pct'],200);self.assertAlmostEqual(result['electromagnetic_mechanical_power_W'],200*math.pi)
        negative=window_statistics([0,1],[-2,-4],[0,1],1500);self.assertEqual(negative['sample_mean_torque_Nm'],-3);self.assertLess(negative['electromagnetic_mechanical_power_W'],0)

    def test_zero_mean_has_no_infinite_or_invented_ratio(self):
        result=window_statistics([0,1],[-1,1],[0,1],3000);self.assertEqual(result['sample_mean_torque_Nm'],0);self.assertIsNone(result['peak_to_peak_over_abs_mean_pct']);self.assertEqual(result['electromagnetic_mechanical_power_W'],0)

    def test_bad_inputs_and_windows_do_not_interpolate_or_default(self):
        for window in ([-1,2],[0,4],[1,1],[2,1],[.1,.9],[True,2],[0,float('nan')],[0,float('inf')],[],None):
            with self.subTest(window=window),self.assertRaises(ValueError):window_statistics([0,1,2],[0,1,2],window,3000)
        for times,values in (([0,0],[1,2]),([0,1],[1]),([0,1],[1,float('nan')]),([0,True],[1,2])):
            with self.assertRaises(ValueError):window_statistics(times,values,[0,1],3000)

    def test_default_matches_original_window_without_changing_its_metrics(self):
        before=copy.deepcopy((self.record,self.runs));payload=self.service.analyze(self.batch,dict(items=['item_01','item_02']))
        self.assertAlmostEqual(payload['records'][0]['window_statistics']['sample_mean_torque_Nm'],1.15)
        self.assertEqual(payload['records'][0]['time_ms'],self.record['items'][0]['metrics']['time_ms']);self.assertFalse(payload['original_acceptance_changed']);self.assertFalse(payload['optimization_ranking_enabled']);self.assertFalse(payload['efficiency_enabled']);self.assertEqual((self.record,self.runs),before)

    def test_cutting_an_anomalous_point_does_not_change_original_acceptance(self):
        end=self.record['items'][0]['metrics']['time_ms'][10];payload=self.service.analyze(self.batch,dict(items=['item_02'],window_ms=[0,end]));record=payload['records'][0]
        self.assertLess(record['window_statistics']['max_torque_Nm'],10);self.assertEqual(max(record['torque_Nm']),177);self.assertFalse(record['original_metrics']['torque_numerical_acceptance']);self.assertNotIn('torque_numerical_acceptance',record['window_statistics']);self.assertTrue(record['original_findings'])

    def test_unavailable_duplicate_unknown_and_cross_batch_items_are_rejected(self):
        for items in ([],['item_03'],['unknown'],['item_01','item_01'],['item_01']*7):
            with self.subTest(items=items),self.assertRaises(ValueError):self.service.analyze(self.batch,dict(items=items))
        with self.assertRaises(ValueError):self.service.analyze('bhbatch_aaaaaaaaaaaa',dict(items=['item_01']))

    def test_source_identity_and_all_saved_metrics_must_match(self):
        self.runs[self.record['items'][0]['execution_id']]['result']['metrics']['T_min_Nm']=-99
        with self.assertRaises(ValueError):self.service.analyze(self.batch,dict(items=['item_01']))

    def test_comparison_requires_same_template_model_protocol_speed_and_grid(self):
        original=copy.deepcopy((self.record,self.runs))
        for key in ('template','model','protocol','speed','time'):
            self.record,self.runs=copy.deepcopy(original);row=self.record['items'][1];execution=self.runs[row['execution_id']]
            if key=='template':row['source']['template_sha256']='e'*64
            if key=='model':row['model_version']=execution['result']['model_version']='other_model'
            if key=='protocol':row['measurement_protocol']=execution['measurement_protocol']='other_protocol'
            if key=='speed':execution['requested_protocol']['speed_rpm']=1500
            if key=='time':row['metrics']['time_ms'][10]+=.01;execution['result']['metrics']=copy.deepcopy(row['metrics'])
            with self.subTest(key=key),self.assertRaises(ValueError):self.service.analyze(self.batch,dict(items=['item_01','item_02']))

    def test_snapshot_is_stable_and_export_keeps_every_original_byte_and_point(self):
        data=dict(items=['item_02','item_01'],window_ms=[.5,2]);payload=self.service.analyze(self.batch,data);self.assertEqual(payload['snapshot_sha256'],self.service.analyze(self.batch,data)['snapshot_sha256'])
        with zipfile.ZipFile(self.service.export(self.batch,dict(data,snapshot_sha256=payload['snapshot_sha256']))) as archive:
            manifest=json.loads(archive.read('manifest.json'))
            for name,sha in manifest['files'].items():self.assertEqual(digest(archive.read(name)),sha)
            for row in payload['records']:self.assertEqual(archive.read('raw/'+row['item']+'/Torque Plots.csv'),self.paths[row['execution_id']].read_bytes())
            points=list(csv.DictReader(io.StringIO(archive.read('all_original_points.csv').decode('utf-8-sig'))));self.assertEqual(len(points),62);self.assertEqual(float(points[15]['torque_Nm']),177);self.assertEqual([row['item'] for row in json.loads(archive.read('analysis.json'))['records']],['item_02','item_01'])
            summary=list(csv.DictReader(io.StringIO(archive.read('summary.csv').decode('utf-8-sig'))));self.assertEqual(summary[0]['original_numerical_acceptance'],'False')

    def test_export_does_not_silently_use_a_changed_selection_window_or_source(self):
        data=dict(items=['item_01'],window_ms=[0,2]);payload=self.service.analyze(self.batch,data)
        for changed in (dict(items=['item_02'],window_ms=[0,2]),dict(items=['item_01'],window_ms=[0,3])):
            with self.assertRaises(ValueError):self.service.export(self.batch,dict(changed,snapshot_sha256=payload['snapshot_sha256']))
        with self.assertRaises(ValueError):self.service.export(self.batch,data)
        self.paths[self.record['items'][0]['execution_id']].write_bytes(b'changed source')
        with self.assertRaises(ValueError):self.service.export(self.batch,dict(data,snapshot_sha256=payload['snapshot_sha256']))

    def test_catalog_keeps_unavailable_and_invalid_batches_visible(self):
        self.manager.records=lambda:[self.record,dict(id='bhbatch_aaaaaaaaaaaa',status='invalid',error='synthetic source error')];data=self.service.catalog();self.assertEqual(len(data['batches']),2);self.assertFalse(data['batches'][0]['items'][2]['available']);self.assertEqual(data['batches'][1]['error'],'synthetic source error')

    def test_download_snapshot_also_binds_the_loaded_statistics_program(self):
        data=dict(items=['item_01']);result=self.service.analyze(self.batch,data)
        with patch('modules.torque_windows.LOADED_PRODUCER_SHA','b'*64):
            with self.assertRaises(ValueError):self.service.export(self.batch,dict(data,snapshot_sha256=result['snapshot_sha256']))

    def test_read_only_HTTP_analysis_export_and_input_errors(self):
        app=Flask(__name__);app.register_blueprint(create_torque_window_routes(self.service));client=app.test_client();response=client.get('/api/workbench/torque-windows/'+self.batch+'/analyze?items=item_01&start_ms=0&stop_ms=2');self.assertEqual(response.status_code,200);self.assertEqual(response.headers['Cache-Control'],'no-store');payload=response.json
        exported=client.get('/api/workbench/torque-windows/'+self.batch+'/export?items=item_01&start_ms=0&stop_ms=2&snapshot='+payload['snapshot_sha256']);self.assertEqual(exported.status_code,200);self.assertTrue(zipfile.is_zipfile(io.BytesIO(exported.data)));exported.close()
        for query in ('items=item_01&start_ms=0','items=item_01&start_ms=NaN&stop_ms=2','items=item_01&items=item_02','items=item_01&unknown=1'):
            invalid=client.get('/api/workbench/torque-windows/'+self.batch+'/analyze?'+query);self.assertEqual(invalid.status_code,400);self.assertTrue(invalid.json['error'])


if __name__=='__main__':unittest.main()
