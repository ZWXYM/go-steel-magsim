"""Selected data collection preserves original sources and never retries solvers."""
import copy
import csv
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from modules.material_calibration import CalibrationBank, VERSION, PHYSICS_VERSION
from modules.system_data import WorkpackStore,json_bytes,sha256
from modules.system_data_builder import SystemDataBuilder,batch_binding


def zipped(files):
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as archive:
        for name,blob in files.items():archive.writestr(name,blob)
    return out


class SystemDataBuilderTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='selected data ');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.storage=self.root/'storage';self.calid='cal_aaaaaaaaaaaa'
        self.cal=self.root/self.calid;self.cal.mkdir()
        anchors=[dict(grade='SYNTHETIC',direction=d,params=dict(f_Goss=.8,theta_0_deg=5,halfwidth_deg=8,Si_content=3),
                      H=[0,800],delta_B=[0,0]) for d in ('RD','TD')]
        bank=CalibrationBank(dict(calibration_version=VERSION,physics_version=PHYSICS_VERSION,H_axis='physical_A_per_m',anchors=anchors))
        (self.cal/'bank.json').write_bytes(json_bytes(bank.payload))
        report=dict(id=self.calid,selected_grades=['SYNTHETIC'],bank_sha256=bank.bank_sha256,H_axis='physical_A_per_m',
            calibration_fit={'mean_rmse_T':.01},material_holdout={'mean_rmse_T':.04},evidence_status='synthetic test fixture',
            known_material_exports={'SYNTHETIC':{}},holdout_material_exports={'SYNTHETIC':{}},
            comparisons=[dict(grade='SYNTHETIC',direction=d,H=[0,800],raw_B=[0,1.2],reference_B=[0,1.5],corrected_B=[0,1.49],holdout_B=[0,1.46]) for d in ('RD','TD')],
            metrics=[dict(grade='SYNTHETIC',direction=d,method='holdout',rmse_T=.04) for d in ('RD','TD')])
        (self.cal/'report.json').write_bytes(json_bytes(report))
        self.source=self.root/'Torque.csv';self.source.write_bytes(b'Time [ns],Torque [Nm]\r\n0,1\r\n1,2\r\n')
        self.dataset=dict(id='v8_2_archive',version='V8_2_final_resolved',measurement_protocol='v8_2_cycle1',scope='synthetic fixture only',
            rows=[dict(case='one',material='SYNTHETIC',input_accepted=True,input_source='official_CSV_verified',metrics={'T_avg_Nm':1.5}),
                  dict(case='bad',material='SYNTHETIC_BAD',input_accepted=False,input_source='report_only',metrics={'T_avg_Nm':-2})],
            references=[],source_hashes={'archive/one/Torque.csv':sha256(self.source.read_bytes())})
        self.optimizer=SimpleNamespace(dataset=lambda _:copy.deepcopy(self.dataset),motor=SimpleNamespace(archive_file=lambda *_:self.source))
        self.materials=SimpleNamespace(bundle=lambda cal,key:zipped({'synthetic_material.amat':key.encode()}))
        self.batch=self.root/'bhbatch_bbbbbbbbbbbb';self.batch.mkdir()
        (self.batch/'manifest.json').write_bytes(b'{"synthetic":true}')
        (self.batch/'state.json').write_bytes(b'{"status":"completed"}')
        self.batches=SimpleNamespace(path=lambda _:self.batch)
        self.builder=SystemDataBuilder(self.storage,SimpleNamespace(get=lambda _:self.cal),self.optimizer,self.materials,self.batches)
        self.selection=dict(calibration=dict(id=self.calid,report_sha256=sha256((self.cal/'report.json').read_bytes())),
            datasets=[dict(id=self.dataset['id'],source_hashes_sha256=sha256(json_bytes(self.dataset['source_hashes'])))],batch=None)

    def test_real_bundle_composition_keeps_source_bytes_failure_rows_fit_and_holdout_separate(self):
        before={p:p.read_bytes() for p in (self.source,self.cal/'bank.json',self.cal/'report.json')}
        with patch('subprocess.Popen',side_effect=AssertionError('No solving or training during collection')):
            record=self.builder.build(self.selection)
        saved,blob=WorkpackStore(self.storage/'system_data').read(record['id'])
        self.assertEqual(saved,record);self.assertEqual(before,{p:p.read_bytes() for p in before})
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            manifest=json.loads(archive.read('manifest.json'));index=json.loads(archive.read('data_index.json'))
            self.assertEqual(set(manifest['files']),set(archive.namelist())-{'manifest.json'})
            for name,digest in manifest['files'].items():self.assertEqual(sha256(archive.read(name)),digest)
            self.assertEqual(archive.read('calibration/report.json'),before[self.cal/'report.json'])
            self.assertEqual(index['calibration']['distinct_material_count'],1);self.assertEqual(index['calibration']['curve_rows'],4)
            curves=list(csv.DictReader(io.StringIO(archive.read('calibration/curves.csv').decode('utf-8-sig'))))
            self.assertEqual(curves[1]['calibration_fit_B_T'],'1.49');self.assertEqual(curves[1]['material_holdout_B_T'],'1.46')
            self.assertEqual(curves[1]['reference_only_B_T'],'')
            with zipfile.ZipFile(io.BytesIO(archive.read('motor_datasets/v8_2_archive.zip'))) as dataset:
                self.assertEqual(dataset.read('sources/archive/one/Torque.csv'),before[self.source])
                rows=list(csv.DictReader(io.StringIO(dataset.read('metrics.csv').decode('utf-8-sig'))))
                self.assertEqual(rows[1]['T_avg_Nm'],'-2');self.assertEqual(len(rows),2)
            self.assertEqual(index['datasets'][0]['original_accepted_count'],1)
            self.assertEqual(index['new_native_solves'],0);self.assertEqual(index['new_analyses'],0)

    def test_explicit_selection_never_silently_adds_calibration_or_other_datasets(self):
        selection=dict(calibration=None,datasets=self.selection['datasets'],batch=None)
        record=self.builder.build(selection);_,blob=self.builder.store.read(record['id'])
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            self.assertFalse(any(name.startswith('calibration/') for name in archive.namelist()))
            self.assertEqual(len([name for name in archive.namelist() if name.startswith('motor_datasets/')]),1)
            self.assertIsNone(json.loads(archive.read('data_index.json'))['calibration'])

    def test_changed_selected_report_or_source_binding_does_not_create_a_package(self):
        for field in ('report','dataset'):
            selection=copy.deepcopy(self.selection)
            if field=='report':selection['calibration']['report_sha256']='f'*64
            else:selection['datasets'][0]['source_hashes_sha256']='f'*64
            with self.subTest(field=field),self.assertRaisesRegex(ValueError,'变化'):self.builder.build(selection)
        self.assertFalse((self.storage/'system_data').exists())

    def test_sources_changing_during_material_collection_stop_registration(self):
        def mutate(cal,key):
            (self.cal/'report.json').write_bytes(b'changed while reading')
            return zipped({'synthetic.amat':b'test'})
        self.materials.bundle=mutate
        with self.assertRaisesRegex(ValueError,'打包期间变化'):self.builder.build(self.selection)
        self.assertFalse((self.storage/'system_data').exists())

    def test_invalid_empty_duplicate_or_incomplete_data_are_rejected_without_fallback(self):
        variants=[{},dict(calibration=None,datasets=[],batch=None),dict(self.selection,extra=True),
                  dict(self.selection,datasets=self.selection['datasets']*2),dict(self.selection,calibration={'id':'../private','report_sha256':'a'})]
        for selection in variants:
            with self.subTest(selection=selection),self.assertRaises(ValueError):self.builder.build(selection)
        report=json.loads((self.cal/'report.json').read_bytes());report['comparisons'].pop()
        (self.cal/'report.json').write_bytes(json_bytes(report));self.selection['calibration']['report_sha256']=sha256((self.cal/'report.json').read_bytes())
        with self.assertRaisesRegex(ValueError,'RD/TD曲线缺失'):self.builder.build(self.selection)
        self.assertFalse((self.storage/'system_data').exists())

    def test_explicit_completed_batch_preserves_rejected_case_and_no_efficiency_or_ranking(self):
        batch_record=dict(id=self.batch.name,status='completed_with_failures',items=[{'result_available':True,'metrics':{'torque_numerical_acceptance':False}}])
        selection=dict(calibration=None,datasets=[],batch=dict(id=self.batch.name,source_binding_sha256=batch_binding(self.batches,self.batch.name)))
        with patch('modules.system_data_builder.report_bundle',return_value=zipped({'batch.json':json_bytes(batch_record)})):
            record=self.builder.build(selection)
        _,blob=self.builder.store.read(record['id'])
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            index=json.loads(archive.read('data_index.json'));self.assertEqual(index['calibrated_batch']['result_count'],1)
            self.assertFalse(index['calibrated_batch']['efficiency_enabled']);self.assertFalse(index['calibrated_batch']['optimization_ranking_enabled'])
            with zipfile.ZipFile(io.BytesIO(archive.read('calibrated_motor/'+self.batch.name+'.zip'))) as nested:
                self.assertFalse(json.loads(nested.read('batch.json'))['items'][0]['metrics']['torque_numerical_acceptance'])

    def test_selected_new_calibration_and_old_batch_are_preserved_with_explicit_relation(self):
        selection=dict(self.selection,batch=dict(id=self.batch.name,source_binding_sha256=batch_binding(self.batches,self.batch.name)))
        source=dict(parent_bank_sha256='b'*64,effective_bank_sha256='b'*64,excluded_grades=[])
        batch_record=dict(id=self.batch.name,status='completed',items=[dict(id='item_01',selection=dict(calibration='cal_cccccccccccc',key='fit:SYNTHETIC'),source=source,result_available=True,metrics={'torque_numerical_acceptance':False})])
        original=zipped({'batch.json':json_bytes(batch_record)}).getvalue()
        with patch('modules.system_data_builder.report_bundle',return_value=io.BytesIO(original)):
            record=self.builder.build(selection)
        _,blob=self.builder.store.read(record['id'])
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            index=json.loads(archive.read('data_index.json'));relation=index['calibrated_batch']['calibration_relation']
            self.assertEqual(relation['relationship'],'different_or_mixed_sources')
            self.assertEqual(relation['selected_calibration_id'],self.calid);self.assertEqual(relation['source_calibration_ids'],['cal_cccccccccccc'])
            self.assertEqual(archive.read('calibrated_motor/'+self.batch.name+'.zip'),original)
            self.assertIn('不是按所选校准重新求解',record['description']);self.assertEqual(index['new_native_solves'],0)

    def test_active_batch_or_state_change_is_not_frozen_as_completed_data(self):
        selection=dict(calibration=None,datasets=[],batch=dict(id=self.batch.name,source_binding_sha256=batch_binding(self.batches,self.batch.name)))
        with patch('modules.system_data_builder.report_bundle',return_value=zipped({'batch.json':json_bytes(dict(id=self.batch.name,status='running',items=[]))})):
            with self.assertRaisesRegex(ValueError,'批量结束后'):self.builder.build(selection)
        def mutate(*_):
            (self.batch/'state.json').write_bytes(b'{"changed":true}')
            return zipped({'batch.json':json_bytes(dict(id=self.batch.name,status='completed',items=[]))})
        with patch('modules.system_data_builder.report_bundle',side_effect=mutate):
            with self.assertRaisesRegex(ValueError,'打包期间发生变化'):self.builder.build(selection)
        self.assertFalse((self.storage/'system_data').exists())

    def test_saved_screening_and_current_same_dataset_require_the_same_source_hashes(self):
        selection=dict(self.selection,optimization=dict(id='opt_cccccccccccc',source_binding_sha256='binding'))
        index=dict(id='opt_cccccccccccc',dataset=self.dataset['id'],dataset_source_hashes={'different.csv':'a'*64})
        with patch.object(self.builder.optimization_exports,'collect',return_value=(index,{'analysis/analysis.json':b'{}'})):
            with self.assertRaisesRegex(ValueError,'来源不同'):self.builder.build(selection)
        self.assertFalse((self.storage/'system_data').exists())


    def multi_batch_fixture(self):
        folders={self.batch.name:self.batch}
        for i in range(1,4):
            folder=self.root/('bhbatch_'+format(i,'012x'));folder.mkdir();folders[folder.name]=folder
            (folder/'manifest.json').write_bytes(json_bytes({'synthetic_batch':i}))
            (folder/'state.json').write_bytes(json_bytes({'status':'completed'}))
        self.batches.path=lambda artifact:folders[artifact]
        bindings=[dict(id=key,source_binding_sha256=batch_binding(self.batches,key)) for key in folders]
        records={key:dict(id=key,status='completed',measurement_protocol='fixture_protocol_'+str(i),
            items=[dict(id='item_01',result_available=True,metrics=dict(T_avg_Nm=i-1,n_samples=101 if i==1 else 31,
                source_n_samples=101 if i==1 else 31,torque_numerical_acceptance=i%2==0))]) for i,key in enumerate(folders)}
        blobs={key:zipped({'batch.json':json_bytes(record),'raw/official.csv':('synthetic,'+key).encode()}).getvalue() for key,record in records.items()}
        return folders,bindings,records,blobs

    def test_four_completed_batches_keep_separate_protocols_raw_bytes_and_primary_compatibility(self):
        folders,bindings,records,blobs=self.multi_batch_fixture()
        selection=dict(calibration=None,datasets=[],batch=bindings[0],additional_batches=bindings[1:])
        old={p:p.read_bytes() for folder in folders.values() for p in folder.iterdir()}
        with patch('modules.system_data_builder.report_bundle',side_effect=lambda service,key:io.BytesIO(blobs[key])):
            saved=self.builder.build(selection)
        _,blob=self.builder.store.read(saved['id'])
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            index=json.loads(archive.read('data_index.json'));entries=index['calibrated_batches']
            self.assertEqual([r['id'] for r in entries],list(folders));self.assertEqual(index['calibrated_batch'],entries[0])
            self.assertEqual([r['measurement_protocol'] for r in entries],[r['measurement_protocol'] for r in records.values()])
            for entry in entries:
                self.assertEqual(archive.read('calibrated_motor/'+entry['id']+'.zip'),blobs[entry['id']])
                self.assertEqual(entry['bundle_sha256'],sha256(blobs[entry['id']]))
                self.assertFalse(entry['efficiency_enabled']);self.assertFalse(entry['optimization_ranking_enabled'])
            self.assertEqual(json.loads(archive.read('selection.json')),selection)
            overview=list(csv.DictReader(io.StringIO(archive.read('calibrated_motor/overview.csv').decode('utf-8-sig'))))
            self.assertEqual([row['batch_id'] for row in overview],list(folders))
            self.assertEqual([row['T_avg_Nm'] for row in overview],['-1','0','1','2'])
            self.assertEqual([row['n_samples'] for row in overview],['31','101','31','31'])
            self.assertEqual([row['torque_numerical_acceptance'] for row in overview],['True','False','True','False'])
            self.assertEqual(index['calibrated_batch_overview']['rows'],4)
        self.assertEqual(old,{p:p.read_bytes() for p in old});self.assertIn('批量4份',saved['description'])

    def test_additional_batch_can_be_selected_without_primary_calibration_or_dataset(self):
        _,bindings,_,blobs=self.multi_batch_fixture();selection=dict(calibration=None,datasets=[],batch=None,additional_batches=[bindings[1]])
        with patch('modules.system_data_builder.report_bundle',side_effect=lambda service,key:io.BytesIO(blobs[key])):
            saved=self.builder.build(selection)
        with zipfile.ZipFile(io.BytesIO(self.builder.store.read(saved['id'])[1])) as archive:
            index=json.loads(archive.read('data_index.json'));self.assertIsNone(index['calibrated_batch'])
            self.assertEqual([row['id'] for row in index['calibrated_batches']],[bindings[1]['id']])

    def test_duplicate_invalid_or_over_limit_batch_choices_do_not_register_partial_packages(self):
        _,bindings,_,_=self.multi_batch_fixture();base=dict(calibration=None,datasets=[],batch=bindings[0])
        invalid=[None,'bad',bindings,[bindings[0]],[bindings[1],bindings[1]],
            [dict(id='../private',source_binding_sha256='a'*64)],[dict(bindings[1],source_binding_sha256='bad')],[{}]]
        with patch('modules.system_data_builder.report_bundle',side_effect=AssertionError('invalid selection must fail before reading batches')):
            for extra in invalid:
                with self.subTest(extra=extra),self.assertRaises(ValueError):self.builder.build(dict(base,additional_batches=extra))
        self.assertEqual(self.builder.store.records(),[])

    def test_change_to_earlier_batch_while_collecting_later_batch_is_rejected(self):
        folders,bindings,_,blobs=self.multi_batch_fixture();selection=dict(calibration=None,datasets=[],batch=bindings[0],additional_batches=[bindings[1]])
        def collect(service,key):
            if key==bindings[1]['id']:(folders[bindings[0]['id']]/'state.json').write_bytes(b'{"changed":true}')
            return io.BytesIO(blobs[key])
        with patch('modules.system_data_builder.report_bundle',side_effect=collect):
            with self.assertRaisesRegex(ValueError,'打包期间发生变化'):self.builder.build(selection)
        self.assertEqual(self.builder.store.records(),[])

    def test_active_or_stale_additional_batch_cannot_be_archived_as_complete(self):
        folders,bindings,records,blobs=self.multi_batch_fixture();selection=dict(calibration=None,datasets=[],batch=None,additional_batches=[bindings[1]])
        records[bindings[1]['id']]['status']='running'
        with patch('modules.system_data_builder.report_bundle',return_value=zipped({'batch.json':json_bytes(records[bindings[1]['id']])})):
            with self.assertRaisesRegex(ValueError,'结束后'):self.builder.build(selection)
        (folders[bindings[1]['id']]/'state.json').write_bytes(b'{"changed":true}')
        with patch('modules.system_data_builder.report_bundle',side_effect=AssertionError('stale binding should be rejected first')):
            with self.assertRaisesRegex(ValueError,'状态已变化'):self.builder.build(selection)
        self.assertEqual(self.builder.store.records(),[])


if __name__=='__main__':unittest.main()
