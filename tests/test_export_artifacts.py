"""HTTP export isolation using the real formatter, without native work or proxy fitting."""
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch

import app as web
import maxwell_exporter as real


def prediction(name='A'):
    b=[0,.8,1.2,1.4] if name=='A' else [0,1.1,1.8,2.0]
    return dict(model_id=name,RD=dict(H=[0,100,800,2000],B=b),TD=dict(H=[0,100,800,2000],B=[v*.8 for v in b]),
                Hc_reference_Am=20,H_axis='legacy_unspecified',params_used=dict(f_Goss=.8 if name=='A' else .2,Si_content=3))


class ExportArtifactTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)

    def test_concurrent_same_named_prediction_downloads_keep_their_own_bytes_and_metadata(self):
        saved=threading.Event();second=threading.Event();expected={};paths={};out={};folder=self.root
        class Exporter:
            @staticmethod
            def export_from_prediction(result,**kwargs):
                name=result['model_id'];path=Path(real.export_from_prediction(result,export_dir=str(folder),**kwargs));paths[name]=path;expected[name]=path.read_bytes()
                if name=='A':saved.set();self.assertTrue(second.wait(5))
                else:second.set()
                return str(path)
        def request(name):
            with web.app.test_client() as client:
                response=client.post('/api/analyze/export-maxwell-from-prediction',json=dict(model_id=name,params={},mat_name='SAME_MATERIAL'))
                out[name]=dict(status=response.status_code,content=response.data,artifact=response.headers.get('X-Material-Artifact'));response.close()
        with patch.object(web,'_predict_selected_bh',side_effect=lambda params,name:prediction(name)),patch.object(web,'_get_mx',return_value=Exporter):
            a=threading.Thread(target=request,args=('A',));a.start();self.assertTrue(saved.wait(5));b=threading.Thread(target=request,args=('B',));b.start();a.join(8);b.join(8)
        self.assertFalse(a.is_alive());self.assertFalse(b.is_alive());self.assertNotEqual(paths['A'],paths['B'])
        for name in ('A','B'):
            self.assertEqual(out[name]['status'],200);self.assertEqual(out[name]['content'],expected[name]);self.assertEqual(out[name]['artifact'],paths[name].name)
            self.assertIn(b"$begin 'SAME_MATERIAL'",out[name]['content'])
            meta=json.loads(paths[name].with_suffix('.metadata.json').read_text());self.assertEqual(meta['source_model_id'],name);self.assertEqual(meta['material_name'],'SAME_MATERIAL');self.assertEqual(meta['prediction_params'],prediction(name)['params_used'])

    def test_manual_HTTP_exports_preserve_old_file_and_logical_name(self):
        (self.root/'SAME.amat').write_bytes(b'OLD_AMAT');(self.root/'SAME.metadata.json').write_bytes(b'OLD_META');folder=self.root
        class Exporter:
            @staticmethod
            def export_from_bh_curves(*args,**kwargs):return real.export_from_bh_curves(*args,export_dir=str(folder),**kwargs)
        body=dict(H_RD=[0,100,800,2000],B_RD=[0,.8,1.2,1.4],mat_name='SAME');artifacts=[]
        with patch.object(web,'_get_mx',return_value=Exporter),web.app.test_client() as client:
            for _ in range(2):
                response=client.post('/api/analyze/export-maxwell',json=body);self.assertEqual(response.status_code,200);name=response.headers['X-Material-Artifact'];artifacts.append(name)
                self.assertEqual((self.root/name).read_bytes(),response.data);self.assertIn('SAME.amat',response.headers['Content-Disposition']);response.close()
        self.assertNotEqual(*artifacts);self.assertEqual((self.root/'SAME.amat').read_bytes(),b'OLD_AMAT');self.assertEqual((self.root/'SAME.metadata.json').read_bytes(),b'OLD_META')
        for name in artifacts:self.assertEqual(json.loads((self.root/name).with_suffix('.metadata.json').read_text())['material_name'],'SAME')

    def test_optional_storage_name_does_not_change_material_body_or_script_default_path(self):
        result=prediction();default=Path(real.export_from_prediction(result,'LOGICAL',export_dir=str(self.root/'default')))
        separate=Path(real.export_from_prediction(result,'LOGICAL',export_dir=str(self.root/'separate'),artifact_name='request_123'))
        self.assertEqual(default.name,'LOGICAL.amat');self.assertEqual(separate.name,'request_123.amat');self.assertEqual(default.read_bytes(),separate.read_bytes())
        self.assertEqual(json.loads(separate.with_suffix('.metadata.json').read_text())['source_model_id'],'A')

    def test_existing_artifact_or_orphan_metadata_is_never_overwritten(self):
        path=Path(real.save_amat_file('old','logical',str(self.root),{'source':'old'},artifact_name='fixed'));before={p.name:p.read_bytes() for p in self.root.iterdir()}
        with self.assertRaises(FileExistsError):real.save_amat_file('new','logical',str(self.root),{'source':'new'},artifact_name='fixed')
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.root.iterdir()})
        orphan=self.root/'orphan.metadata.json';orphan.write_bytes(b'old orphan')
        with self.assertRaises(FileExistsError):real.save_amat_file('new','logical',str(self.root),{},artifact_name='orphan')
        self.assertEqual(orphan.read_bytes(),b'old orphan');self.assertFalse((self.root/'orphan.amat').exists())

    def test_exclusive_creation_has_one_winner_even_with_colliding_request_id(self):
        barrier=threading.Barrier(2);out={}
        def write(name):
            barrier.wait()
            try:real.save_amat_file(name,'logical',str(self.root),{'source':name},artifact_name='collision');out[name]='saved'
            except FileExistsError:out[name]='preserved'
        a=threading.Thread(target=write,args=('A',));b=threading.Thread(target=write,args=('B',));a.start();b.start();a.join(5);b.join(5)
        self.assertCountEqual(out.values(),['saved','preserved']);winner=next(k for k,v in out.items() if v=='saved')
        self.assertEqual((self.root/'collision.amat').read_text(),winner);self.assertEqual(json.loads((self.root/'collision.metadata.json').read_text())['source'],winner)

    def test_storage_name_cannot_escape_the_requested_export_directory(self):
        for name in ('../outside','a/b','a\\b','name.amat','',False,'中文'):
            with self.assertRaises(ValueError):real.save_amat_file('new','logical',str(self.root),{},artifact_name=name)
        self.assertEqual(list(self.root.iterdir()),[])

    def test_default_material_names_differ_at_the_same_clock_and_explicit_names_are_preserved(self):
        clock=Mock();clock.now.return_value.strftime.return_value='20261021_000000'
        ids=[SimpleNamespace(hex='1'*32),SimpleNamespace(hex='2'*32),SimpleNamespace(hex='3'*32),SimpleNamespace(hex='4'*32)]
        with patch.object(web,'datetime',clock),patch.object(web.uuid,'uuid4',side_effect=ids):
            a,afile=web._http_material_names('GO_Pred',None);b,bfile=web._http_material_names('GO_Pred',None)
            logical,one=web._http_material_names('GO_Pred','用户材料');same,two=web._http_material_names('GO_Pred','用户材料')
        self.assertNotEqual(a,b);self.assertNotEqual(afile,bfile);self.assertEqual(logical,same);self.assertEqual(logical,'用户材料');self.assertNotEqual(one,two)
        self.assertRegex(one,r'^[A-Za-z0-9_-]+$')

    def test_export_history_contains_both_new_artifacts_and_legacy_defaults(self):
        real.save_amat_file('old','legacy',str(self.root))
        real.export_from_prediction(prediction('A'),'same',export_dir=str(self.root),artifact_name='a')
        real.export_from_prediction(prediction('B'),'same',export_dir=str(self.root),artifact_name='b')
        entries=real.list_exports(str(self.root));self.assertEqual({e['name'] for e in entries},{'legacy.amat','a.amat','b.amat'})
        self.assertTrue(all(e['metadata_path'] for e in entries if e['name']!='legacy.amat'))

    def test_HTTP_export_from_a_different_workspace_uses_the_saved_absolute_path(self):
        old=Path.cwd();workspace=self.root/'workspace';workspace.mkdir()
        try:
            os.chdir(workspace)
            with patch.object(web,'_get_mx',return_value=real),web.app.test_client() as client:
                response=client.post('/api/analyze/export-maxwell',json=dict(H_RD=[0,100,800,2000],B_RD=[0,.8,1.2,1.4],mat_name='WORKSPACE_TEST'))
                self.assertEqual(response.status_code,200);artifact=response.headers['X-Material-Artifact'];path=workspace/'data/exports'/artifact
                self.assertEqual(path.read_bytes(),response.data);meta=json.loads(path.with_suffix('.metadata.json').read_text());self.assertEqual(Path(meta['amat_path']),path)
                response.close()
        finally:os.chdir(old)


if __name__=='__main__':unittest.main()
