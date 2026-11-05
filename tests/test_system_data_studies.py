"""Saved studies join private workpacks without changing scores or source scope."""
import copy
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from modules.system_data import json_bytes, sha256
from modules.system_data_builder import SystemDataBuilder


class SavedStudyWorkpackTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='saved study workpack ')
        self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        def case(name,torque,ripple,iron):
            return dict(case=name,material='SYNTHETIC '+name,input_accepted=True,input_source='official_CSV_verified',
                metrics=dict(T_avg_Nm=torque,K_T_ripple_pct=ripple,P_Fe_W=iron,eta_estimate_pct=90.))
        baseline=case('baseline',2,50,10)
        self.dataset=dict(id='v8_2_bound_archive',version='SYNTHETIC_TEST_ONLY',measurement_protocol='v8_2_cycle1',
            scope='synthetic fixture',rows=[baseline,case('candidate',1.995,30,9)],references=[baseline],source_hashes={'synthetic.csv':'a'*64})
        optimizer=SimpleNamespace(dataset=lambda _:copy.deepcopy(self.dataset))
        self.builder=SystemDataBuilder(self.root,None,optimizer,None,None)
        self.reader=self.builder.constraint_exports;self.studies=self.reader.studies
        self.report=self.studies.run(dict(dataset=self.dataset['id'],reference='baseline',
            source_hashes_sha256=self.studies.inputs(self.dataset['id'])['source_hashes_sha256']))
        self.selection=dict(calibration=None,datasets=[],batch=None,
            constraint_study=dict(id=self.report['id'],source_binding_sha256=self.reader.catalog()[0]['source_binding_sha256']))

    def test_exact_saved_study_enters_registered_pack_without_scoring_or_native_process(self):
        folder=self.studies.path(self.report['id']);before={p.name:p.read_bytes() for p in folder.iterdir()}
        with patch.object(self.studies,'run',side_effect=AssertionError('No re-scoring')),patch('subprocess.Popen',side_effect=AssertionError('No solving')):
            record=self.builder.build(self.selection)
        _,blob=self.builder.store.read(record['id'])
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            index=json.loads(archive.read('data_index.json'))['constraint_study']
            self.assertEqual(index['configuration_count'],48);self.assertEqual(index['original_default_candidate_count'],0)
            self.assertEqual(index['new_analyses'],0);self.assertFalse(index['final_physical_optimization_certified'])
            for name,data in before.items():self.assertEqual(archive.read('constraint_studies/'+self.report['id']+'/'+name),data)
            self.assertEqual(json.loads(archive.read('selection.json')),self.selection)
        self.assertEqual(before,{p.name:p.read_bytes() for p in folder.iterdir()})

    def test_changed_complete_record_rejects_previous_selection_before_registration(self):
        folder=self.studies.path(self.report['id'])
        (folder/'README.md').write_bytes(b'changed description')
        manifest=json.loads((folder/'manifest.json').read_bytes());manifest['files']['README.md']=sha256(b'changed description')
        (folder/'manifest.json').write_bytes(json_bytes(manifest))
        with self.assertRaisesRegex(ValueError,'文件已变化'):self.builder.build(self.selection)
        self.assertFalse(self.builder.store.storage.exists())

    def test_same_dataset_different_source_cannot_be_combined_with_saved_study(self):
        changed=copy.deepcopy(self.dataset);changed['source_hashes']={'synthetic.csv':'b'*64}
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,'w') as archive:
            archive.writestr('dataset.json',json_bytes(changed))
            archive.writestr('source_snapshot.json',json_bytes(dict(row_count=2,saved_accepted_count=2,native_csv_accepted_count=2,data_usage={})))
        selection=copy.deepcopy(self.selection);selection['datasets']=[dict(id=changed['id'],source_hashes_sha256=sha256(json_bytes(changed['source_hashes'])))]
        with patch('modules.system_data_builder.dataset_bundle',return_value=stream):
            with self.assertRaisesRegex(ValueError,'来源不同'):self.builder.build(selection)
        self.assertFalse(self.builder.store.storage.exists())

    def test_record_change_between_reads_is_rejected_and_old_data_remains(self):
        original=self.reader.read(self.report['id']);changed=copy.deepcopy(original)
        changed[1]['README.md']=b'new contents'
        with patch.object(self.reader,'read',side_effect=[original,changed]):
            with self.assertRaisesRegex(ValueError,'打包期间变化'):self.reader.collect(self.selection['constraint_study'])
        self.assertEqual(original,self.reader.read(self.report['id']))

    def test_corrupt_record_is_isolated_and_never_substituted(self):
        broken=self.studies.path('csens_ffffffffffff');broken.mkdir();(broken/'study.json').write_bytes(b'broken')
        rows=self.reader.catalog();self.assertEqual(sum(row['readable'] for row in rows),1);self.assertEqual(len(rows),2)
        invalid=next(row for row in rows if not row['readable']);self.assertEqual(invalid['id'],broken.name)
        with self.assertRaises(OSError):self.reader.collect(dict(id=broken.name,source_binding_sha256='a'*64))

    def test_selection_requires_explicit_record_and_binding(self):
        for selected in ({'id':self.report['id']},{'id':'../private','source_binding_sha256':'a'},None):
            with self.subTest(selection=selected),self.assertRaises(ValueError):self.reader.collect(selected)


if __name__=='__main__':unittest.main()
