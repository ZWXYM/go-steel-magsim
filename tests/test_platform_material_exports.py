"""Source-file preservation, legacy/augmentation identity, stale links and read-only routes."""
import csv
import io
import json
import hashlib
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from flask import Flask
from modules.platform_material_exports import PlatformMaterialExports
from modules.workbench_routes import create_workbench

PROJECT=Path(__file__).resolve().parents[1]


class PlatformExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.folder=self.root/'exports';self.folder.mkdir();self.library=PlatformMaterialExports(self.folder)
        (self.folder/'A.amat').write_bytes(b'RAW_A');(self.folder/'A.metadata.json').write_text(json.dumps(dict(material_name='Same name',source_model_id='model_A',prediction_params={'f_Goss':.8},H_axis='legacy_unspecified')),encoding='utf8')
        (self.folder/'Pred_unknown.amat').write_bytes(b'RAW_B');aug=self.folder/'augmented';aug.mkdir();(aug/'C_aug001.amat').write_bytes(b'RAW_C');(aug/'augmented_metadata.json').write_text(json.dumps([dict(aug_name='C_aug001',source='C',alpha_B_RD=1.02)]),encoding='utf8');(self.folder/'orphan.metadata.json').write_text('{"material_name":"orphan"}',encoding='utf8')

    def rows(self):return {r['relative_file']:r for r in self.library.catalog()['records']}
    def hashes(self):return {p.relative_to(self.folder).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in self.folder.rglob('*') if p.is_file()}

    def test_catalog_keeps_legacy_unknown_and_exact_augmentation_source_separate(self):
        rows=self.rows();self.assertEqual(len(rows),4);self.assertEqual(rows['A.amat']['model_id'],'model_A');self.assertEqual(rows['A.amat']['prediction_params'],{'f_Goss':.8})
        unknown=rows['Pred_unknown.amat'];self.assertEqual(unknown['metadata_kind'],'missing');self.assertIsNone(unknown['model_id']);self.assertIsNone(unknown['H_axis'])
        aug=rows['augmented/C_aug001.amat'];self.assertEqual(aug['metadata_kind'],'directory_record');self.assertEqual(aug['augmentation_source'],'C');self.assertIsNone(aug['prediction_params']);self.assertFalse(rows['orphan.metadata.json']['available'])

    def test_bundle_preserves_original_material_and_directory_metadata_bytes(self):
        before=self.hashes();row=self.rows()['augmented/C_aug001.amat']
        with zipfile.ZipFile(self.library.bundle(row['id'])) as z:
            self.assertEqual(z.read('material.amat'),b'RAW_C');self.assertEqual(z.read('metadata.json'),(self.folder/'augmented/augmented_metadata.json').read_bytes())
            manifest=json.loads(z.read('manifest.json'))
            for name,digest in manifest['files'].items():self.assertEqual(hashlib.sha256(z.read(name)).hexdigest(),digest)
            self.assertEqual(json.loads(z.read('entry.json'))['metadata']['aug_name'],'C_aug001')
        self.assertEqual(self.hashes(),before)

    def test_bad_metadata_does_not_hide_material_or_other_records(self):
        (self.folder/'A.metadata.json').write_bytes(b'broken JSON');rows=self.rows();row=rows['A.amat'];self.assertTrue(row['available']);self.assertEqual(row['metadata_kind'],'unreadable');self.assertIn('metadata.json',row['files']);self.assertEqual(self.library.download(row['id'],'metadata.json')[0],b'broken JSON');self.assertEqual(len(rows),4)

    def test_nonfinite_or_wrong_type_metadata_is_not_used_as_parameters(self):
        for text in ('[1,2]','{"prediction_params":{"f":NaN}}'):
            (self.folder/'A.metadata.json').write_text(text,encoding='utf8');row=self.rows()['A.amat'];self.assertEqual(row['metadata_kind'],'unreadable');self.assertIsNone(row['prediction_params'])

    def test_changed_material_or_metadata_rejects_old_identity(self):
        old=self.rows()['A.amat']['id'];(self.folder/'A.amat').write_bytes(b'NEW_A')
        with self.assertRaisesRegex(ValueError,'已变化'):self.library.find(old)
        fresh=self.rows()['A.amat']['id'];(self.folder/'A.metadata.json').write_text('{}',encoding='utf8')
        with self.assertRaises(ValueError):self.library.find(fresh)

    def test_workspace_switch_cannot_rebind_an_existing_link(self):
        current=[self.folder];library=PlatformMaterialExports(lambda:current[0]);old=library.catalog()['records'][0]['id'];other=self.root/'other';other.mkdir();(other/'A.amat').write_bytes(b'RAW_A');current[0]=other
        with self.assertRaises(ValueError):library.find(old)
        self.assertNotEqual(library.catalog()['records'][0]['id'],old)

    def test_read_errors_are_per_record_and_outside_paths_cannot_be_downloaded(self):
        original=self.library._read
        def read(path,base):
            if path.name=='A.amat':raise PermissionError('fixture unreadable')
            return original(path,base)
        with patch.object(self.library,'_read',side_effect=read):
            rows=self.rows();self.assertFalse(rows['A.amat']['available']);self.assertIn('unreadable',rows['A.amat']['error']);self.assertTrue(rows['Pred_unknown.amat']['available'])
        outside=self.root/'outside.amat';outside.write_bytes(b'OUTSIDE')
        with self.assertRaises(ValueError):self.library._read(outside,self.folder)

    def test_CSV_is_one_snapshot_and_preserves_blank_unknowns(self):
        catalog=self.library.catalog();rows=list(csv.DictReader(io.StringIO(self.library.csv(catalog['snapshot_sha256']).decode('utf-8-sig'))));self.assertEqual(len(rows),4)
        unknown=next(r for r in rows if r['relative_file']=='Pred_unknown.amat');self.assertEqual(unknown['model_id'],'');self.assertEqual(unknown['prediction_params'],'')
        (self.folder/'New.amat').write_bytes(b'NEW')
        with self.assertRaisesRegex(ValueError,'已变化'):self.library.csv(catalog['snapshot_sha256'])

    def test_missing_directory_and_orphan_metadata_are_readable_without_creating_files(self):
        missing=self.root/'missing';self.assertEqual(PlatformMaterialExports(missing).catalog()['records'],[]);self.assertFalse(missing.exists())
        row=self.rows()['orphan.metadata.json'];self.assertEqual(self.library.download(row['id'],'metadata.json')[0],b'{"material_name":"orphan"}')
        with self.assertRaises(ValueError):self.library.download(row['id'],'material.amat')

    def client(self):
        app=Flask('exports',template_folder=str(PROJECT/'templates'))
        with patch.dict(os.environ,{'MAGSIM_CPU_ONLY':'1','MAGSIM_AUTO_RESUME_QUEUE':'0'}),patch('modules.workbench_routes.PlatformMaterialExports',return_value=self.library),patch('modules.workbench_routes.OptimizationPlans.start_monitor'):
            app.register_blueprint(create_workbench(PROJECT,self.root/'records',self.root))
        return app.test_client()

    def test_GET_routes_and_bytes_do_not_predict_dispatch_or_rewrite(self):
        client=self.client();before=self.hashes();data=client.get('/api/workbench/platform-material-exports').json;self.assertEqual(len(data['records']),4);row=next(r for r in data['records'] if r['relative_file']=='A.amat')
        self.assertEqual(client.get('/platform-material-exports').status_code,200);self.assertEqual(client.get(row['detail_url']).json['id'],row['id'])
        for key,url in row['urls'].items():response=client.get(url);self.assertEqual(response.status_code,200);self.assertEqual(hashlib.sha256(response.data).hexdigest(),row['files'][key]['sha256']);response.close()
        response=client.get(row['bundle_url']);self.assertEqual(response.status_code,200);response.close();response=client.get('/api/workbench/platform-material-exports/catalog.csv?snapshot='+data['snapshot_sha256']);self.assertEqual(response.status_code,200);response.close();self.assertEqual(before,self.hashes())

    def test_HTTP_bad_ID_path_or_ambiguous_snapshot_never_reads_another_file(self):
        client=self.client();self.assertEqual(client.get('/api/workbench/platform-material-exports/invalid').status_code,400)
        row=self.rows()['A.amat'];prefix=row['detail_url'];self.assertEqual(client.get(prefix+'/files/AGENTS.md').status_code,400)
        self.assertEqual(client.get('/api/workbench/platform-material-exports/catalog.csv?snapshot=a&snapshot=b').status_code,400)
        self.assertIn(client.post('/api/workbench/platform-material-exports',json={}).status_code,(405,423))


if __name__=='__main__':unittest.main()
