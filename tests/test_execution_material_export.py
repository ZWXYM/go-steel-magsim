"""Read-only saved material exports; no native desktop or solver is launched."""
import copy
import csv
import io
import json
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import test_maxwell_material_transport as fixtures
from modules.calibrated_motor_execution import CalibratedMotorExecution
from modules.execution_material_export import execution_material_exports, digest, FILES
from modules.material_motor_batches import MaterialMotorBatches
from modules.maxwell_material_transport import load_contract, equivalent_material_name


class ActualMaterialExports(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)/'bhexec_0123456789ab'
        inputs = self.folder/'inputs'
        inputs.mkdir(parents=True)
        source = fixtures.fixture(inputs)
        self.contract = load_contract(source)
        shutil.copyfile(source.with_suffix('.metadata.json'), inputs/'material.metadata.json')
        case = self.folder/'case'
        case.mkdir()
        self.project = case/'motor.aedt'
        self.source_text = source.read_text(encoding='utf8')
        self.project.write_text(fixtures.MaterialTransport().scalar_fixture(self.source_text,self.contract,'RD'), encoding='utf8')
        self.record = dict(id=self.folder.name,result_available=True,material_contract=self.contract,
            requested_protocol={'equivalent_direction':'RD'},analysis_plan_id='bhanalysis_0123456789ab',
            parent_bank_sha256='parent',effective_bank_sha256='effective',training_grades=['A'],excluded_grades=['B'],
            measurement_protocol='v8_2_cycle1_RD_equivalent_v1',input_hashes={},
            result=dict(execution_id=self.folder.name,solved_project_sha256=digest(self.project.read_bytes()),
                actual_material_name=equivalent_material_name(self.contract,'RD'),constitutive_model='scalar_direction_equivalent_v1',
                model_version='V8_2_scalar_RD_equivalent_BH_only_v1',
                metrics=dict(torque_csv_sha256='a'*64,torque_numerical_acceptance=False)))

    def test_actual_rd_and_td_nodes_units_metadata_and_source_files_are_preserved(self):
        for direction in ('RD','TD'):
            settings="\n$begin 'Setup1'\nSmoothBHCurve='true'\nMaxIterNum='100'\n$end 'Setup1'\n"
            self.project.write_text(fixtures.MaterialTransport().scalar_fixture(self.source_text,self.contract,direction)+settings,encoding='utf8')
            self.record['requested_protocol']['equivalent_direction']=direction
            self.record['requested_protocol']['solver_settings']={'SmoothBHCurve':True,'MaxIterNum':100}
            self.record['result'].update(solved_project_sha256=digest(self.project.read_bytes()),
                actual_material_name=equivalent_material_name(self.contract,direction))
            before={p:p.read_bytes() for p in self.folder.rglob('*') if p.is_file()}
            files=execution_material_exports(self.record,self.folder)
            metadata=json.loads(files['actual_material.metadata.json'])
            curve=list(csv.DictReader(io.StringIO(files['actual_material_BH.csv'].decode('utf-8-sig'))))
            self.assertEqual([float(row['H_A_per_m']) for row in curve],self.contract['curves'][direction]['H'])
            self.assertEqual([float(row['B_T']) for row in curve],self.contract['curves'][direction]['B'])
            self.assertIn(("$begin '"+metadata['material_name']+"'").encode(),files['actual_material.amat'])
            self.assertNotIn(self.contract['material_name'].encode()+b"'\n",files['actual_material.amat'])
            self.assertEqual(metadata['H_scale'],1.)
            self.assertEqual(metadata['excluded_grades'],['B'])
            self.assertFalse(metadata['efficiency_enabled'])
            self.assertFalse(metadata['torque_numerical_acceptance'])
            self.assertFalse(metadata['directional_field_verified'])
            self.assertIsNone(metadata['declared_sheet_thickness_mm'])
            self.assertEqual(metadata['native_equivalent_cut_edge_depth_m'],self.contract['curves']['core_loss_equiv_cut_depth_m'])
            for name,sha in metadata['files_sha256'].items():self.assertEqual(digest(files[name]),sha)
            self.assertEqual(files,execution_material_exports(self.record,self.folder))
            reordered=json.loads(json.dumps(self.record,sort_keys=True))
            self.assertEqual(files,execution_material_exports(reordered,self.folder))
            self.assertEqual(before,{p:p.read_bytes() for p in self.folder.rglob('*') if p.is_file()})

    def test_identity_or_missing_result_never_exports_another_cases_material(self):
        for key,value in (('id','bhexec_aaaaaaaaaaaa'),('result_available',False),('requested_protocol',{})):
            record=copy.deepcopy(self.record);record[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):execution_material_exports(record,self.folder)
        for key,value in (('execution_id','another'),('actual_material_name','another'),('constitutive_model','tensor')):
            record=copy.deepcopy(self.record);record['result'][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):execution_material_exports(record,self.folder)

    def test_changed_project_or_metadata_is_rejected_before_export(self):
        for path in (self.project,self.folder/'inputs/material.metadata.json'):
            original=path.read_bytes();path.write_bytes(original+b'\n')
            with self.subTest(path=path.name),self.assertRaises(ValueError):execution_material_exports(self.record,self.folder)
            path.write_bytes(original)

    def test_changed_actual_node_still_rejected_when_project_hash_is_rebound(self):
        text=self.project.read_text(encoding='utf8')
        self.project.write_text(self.source_text+text[len(self.source_text):].replace('10.0, 1.1','10.0, 1.2'),encoding='utf8')
        self.assertNotEqual(text,self.project.read_text(encoding='utf8'))
        self.record['result']['solved_project_sha256']=digest(self.project.read_bytes())
        with self.assertRaisesRegex(ValueError,'H/B节点'):execution_material_exports(self.record,self.folder)

    def test_requested_saved_solver_settings_must_match(self):
        self.record['requested_protocol']['solver_settings']={'SmoothBHCurve':True}
        self.project.write_text(self.project.read_text(encoding='utf8')+"\n$begin 'Setup1'\nSmoothBHCurve='false'\n$end 'Setup1'\n",encoding='utf8')
        self.record['result']['solved_project_sha256']=digest(self.project.read_bytes())
        with self.assertRaisesRegex(ValueError,'求解设置'):execution_material_exports(self.record,self.folder)

    def test_download_and_execution_bundle_expose_same_bytes_without_writing_results(self):
        for name in ('result.json','summary.csv','case/reports/Torque Plots.csv'):
            path=self.folder/name;path.parent.mkdir(exist_ok=True);path.write_bytes(b'original\r\n')
        manager=SimpleNamespace(get=lambda _:self.record,path=lambda _:self.folder)
        files=execution_material_exports(self.record,self.folder)
        for name,blob in files.items():
            self.assertEqual(CalibratedMotorExecution.download(manager,self.record['id'],name).getvalue(),blob)
        with zipfile.ZipFile(CalibratedMotorExecution.bundle(manager,self.record['id'])) as archive:
            for name,blob in files.items():self.assertEqual(archive.read(name),blob)
            self.assertEqual(archive.read('result.json'),b'original\r\n')
        with self.assertRaises(ValueError):CalibratedMotorExecution.download(manager,self.record['id'],'../case/motor.aedt')

    def test_scalar_batch_bundle_includes_actual_materials_and_hashes_but_old_tensor_does_not(self):
        torque=self.folder/'torque.csv';torque.write_bytes(b'original torque\r\n')
        exports=execution_material_exports(self.record,self.folder)
        item=dict(id='item_01',selection={'calibration':'cal','key':'fit:A'},source=dict(material_name='source',effective_bank_sha256='bank',excluded_grades=[]),
            status='completed',result_available=True,execution_id=self.record['id'],metrics={})
        record=dict(id='batch',items=[item],measurement_protocol='v8_2_cycle1_RD_equivalent_v1')
        exporter=SimpleNamespace(download=lambda *_:torque,material_exports=lambda _:exports)
        manager=SimpleNamespace(get=lambda _:record,chain=SimpleNamespace(execution=exporter))
        with patch('subprocess.Popen',side_effect=AssertionError('export must not dispatch')):
            with zipfile.ZipFile(MaterialMotorBatches.bundle(manager,'batch')) as archive:
                self.assertEqual(archive.read('item_01/Torque Plots.csv'),torque.read_bytes())
                for name,blob in exports.items():self.assertEqual(archive.read('item_01/'+name),blob)
                for name,sha in json.loads(archive.read('manifest.json'))['files'].items():self.assertEqual(digest(archive.read(name)),sha)
            record['measurement_protocol']='v8_2_cycle1'
            with zipfile.ZipFile(MaterialMotorBatches.bundle(manager,'batch')) as archive:
                self.assertFalse(any(name.endswith(FILES) for name in archive.namelist()))


if __name__=='__main__':unittest.main()
