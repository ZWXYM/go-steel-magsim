"""Synthetic frozen-table fixtures exercise CPU web export, never a solver."""
import hashlib
import json
import os
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
from modules.native_pair_exports import NativePairExports
from modules.workbench_routes import create_workbench
from modules.material_calibration import MU0,file_hash
from tools.run_calibration_pilot import H_GRID


def fixture(root):
    source=root/'calibration/fixture_labelled';source.mkdir(parents=True)
    grade='fixture_A';(source/grade).mkdir();(source/grade/'orientations.csv').write_text('0,0,0\n')
    jobs=[];completed=[]
    for direction,angle in [('RD',0),('TD',90)]:
        script=f'{grade}/{direction}.mx3';output=f'{grade}/{direction}.out';(source/output).mkdir()
        (source/script).write_text('// Physics version: cubic_sample_frame_v2\nMsat = 1.56e+06\ntableaddvar(branch, "branch", "")\n')
        rows=[]
        for label in [-1,1]:
            grid=np.r_[-H_GRID[:0:-1],H_GRID];grid=grid[::-1] if label==-1 else grid
            for h in grid:
                m=np.tanh(h/1000);d=np.array([np.cos(np.deg2rad(angle)),np.sin(np.deg2rad(angle)),0])
                rows.append([0,*(d*m),*(d*h*MU0),label])
        np.savetxt(source/output/'table.txt',rows,delimiter='\t',header='t (s)\tmx ()\tmy ()\tmz ()\tBx (T)\tBy (T)\tBz (T)\tbranch ()')
        jobs.append(dict(grade=grade,direction=direction,angle=angle,grain_id=1,script=script,output=output,script_sha256=file_hash(source/script)))
        completed.append(dict(script=script,exit_code=0,script_sha256=file_hash(source/script),table_sha256=file_hash(source/output/'table.txt')))
    material=dict(grade=grade,params=dict(f_Goss=.8,theta_0_deg=5,halfwidth_deg=8,Si_content=3),thickness_mm=.3,orientations_sha256=file_hash(source/grade/'orientations.csv'))
    (source/'manifest.json').write_text(json.dumps(dict(physics_version='cubic_sample_frame_v2',H_axis='physical_A_per_m',H_grid=H_GRID.tolist(),n_grains=1,seed=1,materials=[material],jobs=jobs)))
    (source/'run_status.json').write_text(json.dumps(dict(jobs=completed,mumax_binary_sha256='synthetic_fixture_not_native')))
    return source


class NativeExports(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.source=fixture(self.root);self.storage=self.root/'saved'
        self.manager=NativePairExports(self.root,self.storage)

    def request(self):
        row=self.manager.catalog()['sources'][0]
        return dict(source_id=row['id'],manifest_sha256=row['manifest_sha256'],grade='fixture_A')

    def test_catalog_is_readonly_and_metadata_counts_are_not_certification(self):
        catalog=self.manager.catalog();self.assertEqual(len(catalog['sources']),1)
        self.assertEqual(catalog['sources'][0]['recorded_successes'],2)
        self.assertIn('核对',catalog['sources'][0]['readiness']);self.assertFalse(self.storage.exists())

    def test_export_reopen_and_download_preserve_sources_and_need_no_refit(self):
        before={p: p.read_bytes() for p in self.source.rglob('*') if p.is_file()}
        record=self.manager.prepare(self.request());reopened=NativePairExports(self.root,self.storage).get(record['id'])
        self.assertEqual(record,reopened);self.assertEqual(record['raw_pair']['native_quality']['status'],'unassessed')
        self.assertEqual(record['new_native_solves'],0)
        with zipfile.ZipFile(self.manager.bundle(record['id'])) as z:
            self.assertEqual(set(z.namelist()),{'raw_pair.json','source_manifest.json','manifest.json'})
            self.assertEqual(z.read('raw_pair.json'),self.manager.download(record['id'],'raw_pair.json').read_bytes())
        for path,blob in before.items():self.assertEqual(path.read_bytes(),blob)

    def test_invalid_source_grade_path_and_stale_manifest_create_no_export(self):
        data=self.request()
        for changed in [dict(data,source_id='../outside'),dict(data,grade='other'),dict(data,manifest_sha256='changed'),dict(path=str(self.source),grade='fixture_A')]:
            with self.subTest(changed=changed),self.assertRaises(ValueError):self.manager.prepare(changed)
        self.assertFalse(self.storage.exists())

    def test_changed_script_is_rejected_before_export_files_are_written(self):
        request=self.request();(self.source/'fixture_A/RD.mx3').write_text('old script')
        with self.assertRaises(ValueError):self.manager.prepare(request)
        self.assertFalse(list(self.storage.glob('nativepair_*')))

    def test_tampered_saved_pair_cannot_be_loaded_or_downloaded(self):
        record=self.manager.prepare(self.request());(self.manager.path(record['id'])/'raw_pair.json').write_text('{}')
        with self.assertRaisesRegex(ValueError,'发生变化'):self.manager.get(record['id'])
        with self.assertRaises(ValueError):self.manager.bundle(record['id'])
        self.assertIn('error',self.manager.records()[0])

    def test_download_whitelist_and_record_paths(self):
        record=self.manager.prepare(self.request())
        with self.assertRaises(ValueError):self.manager.download(record['id'],'../outside')
        with self.assertRaises(ValueError):self.manager.get('../../outside')

    def test_empty_public_resource_directory_is_usable(self):
        self.assertEqual(NativePairExports(self.root/'empty',self.storage).catalog()['sources'],[])

    def test_malformed_candidate_status_is_reported_without_breaking_catalog(self):
        (self.source/'run_status.json').write_text('[]')
        result=self.manager.catalog();self.assertFalse(result['sources']);self.assertEqual(len(result['issues']),1)

    def test_bad_saved_file_mapping_is_isolated_and_valid_exports_remain_usable(self):
        good=self.manager.prepare(self.request());good_file=self.manager.download(good['id'],'raw_pair.json').read_bytes()
        for index,files in enumerate((None,[],['raw_pair.json','source_manifest.json'],1)):
            artifact='nativepair_'+format(index,'012x');folder=self.manager.path(artifact);folder.mkdir()
            (folder/'manifest.json').write_text(json.dumps(dict(id=artifact,files=files)))
            with self.subTest(files=files),self.assertRaises(ValueError):self.manager.get(artifact)
        before={p:p.read_bytes() for p in self.storage.rglob('*') if p.is_file()}
        rows=self.manager.records();self.assertEqual(len(rows),5);self.assertEqual(sum('error' in row for row in rows),4)
        self.assertEqual(self.manager.get(good['id'])['raw_pair']['material_id'],'fixture_A')
        self.assertEqual(self.manager.download(good['id'],'raw_pair.json').read_bytes(),good_file)
        self.assertEqual(before,{p:p.read_bytes() for p in self.storage.rglob('*') if p.is_file()})

    def test_hash_valid_wrong_pair_payload_is_not_advertised_as_usable(self):
        good=self.manager.prepare(self.request())
        for payload in (None,[],{'material_id':'WRONG_MATERIAL'}):
            other=self.manager.prepare(self.request());folder=self.manager.path(other['id']);record=json.loads((folder/'manifest.json').read_text())
            (folder/'raw_pair.json').write_text(json.dumps(payload));record['files']['raw_pair.json']=file_hash(folder/'raw_pair.json');(folder/'manifest.json').write_text(json.dumps(record))
            with self.subTest(payload=payload),self.assertRaises(ValueError):self.manager.get(other['id'])
        rows=self.manager.records();self.assertEqual(sum('error' in row for row in rows),3)
        self.assertTrue(any(row['id']==good['id'] and 'error' not in row for row in rows))

    def test_mixed_saved_record_web_list_and_download_remain_available(self):
        storage=self.root/'web_storage';pairs=NativePairExports(self.root,storage/'native_pair_exports');good=pairs.prepare(self.request())
        artifact='nativepair_000000000000';folder=pairs.path(artifact);folder.mkdir();bad=folder/'manifest.json';bad.write_text(json.dumps(dict(id=artifact,files=None)));before=bad.read_bytes()
        app=Flask(__name__)
        with patch.dict(os.environ,{'MAGSIM_CPU_ONLY':'1','MAGSIM_AUTO_RESUME_QUEUE':'0'}):app.register_blueprint(create_workbench(PROJECT,storage,self.root))
        client=app.test_client();response=client.get('/api/workbench/native-pairs');self.assertEqual(response.status_code,200);rows=response.json
        self.assertEqual(len(rows),2);self.assertTrue(next(row for row in rows if row['id']==artifact)['error']);self.assertNotIn('error',next(row for row in rows if row['id']==good['id']))
        self.assertEqual(client.get('/api/workbench/native-pairs/'+artifact).status_code,400)
        self.assertEqual(client.get('/api/workbench/native-pairs/'+good['id']+'/files/raw_pair.json').data,pairs.download(good['id'],'raw_pair.json').read_bytes());self.assertEqual(bad.read_bytes(),before)

    def test_cpu_web_export_and_reopen_bundle_routes(self):
        app=Flask(__name__,template_folder=str(PROJECT/'templates'))
        with patch.dict(os.environ,{'MAGSIM_CPU_ONLY':'1','MAGSIM_AUTO_RESUME_QUEUE':'0'}):
            app.register_blueprint(create_workbench(PROJECT,self.root/'web_storage',self.root))
        client=app.test_client()
        source=client.get('/api/workbench/native-pair-sources').json['sources'][0]
        response=client.post('/api/workbench/native-pairs',json=dict(source_id=source['id'],manifest_sha256=source['manifest_sha256'],grade='fixture_A'))
        self.assertEqual(response.status_code,200);artifact=response.json['id']
        self.assertEqual(client.get('/api/workbench/native-pairs/'+artifact).json['raw_pair']['material_id'],'fixture_A')
        self.assertEqual(client.get('/api/workbench/native-pairs/'+artifact+'/bundle').status_code,200)
        self.assertEqual(client.post('/api/workbench/native-pairs',json={'path':'C:/outside'}).status_code,400)


if __name__=='__main__':unittest.main()
