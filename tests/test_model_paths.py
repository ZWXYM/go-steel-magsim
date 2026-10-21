"""Model IDs never name parent folders; deletion and inference publish one consistent state."""
import json
import os
import pickle
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
import app as web
from ml_trainer import BHPredictor


class ModelPaths(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name).resolve();self.before=Path.cwd();self.addCleanup(os.chdir,self.before);os.chdir(self.root)
        self.predictor=BHPredictor(str(self.root/'data/models'));self.outside=self.root/'outside';self.outside.mkdir();(self.outside/'sentinel.txt').write_text('preserve')

    def model(self,name='A',paper=False):
        folder=self.root/'data'/('paper_models' if paper else 'models')/name;folder.mkdir(parents=True);(folder/'model.pkl').write_bytes(pickle.dumps({'synthetic_storage_sentinel':True}));(folder/'config.json').write_text(json.dumps(dict(model_id=name,feature_cols=['f_Goss'],target_cols=['B_0deg_H100'],bh_reference_corrected=False)));return folder

    def test_invalid_ids_cannot_load_or_schedule_any_removal(self):
        with patch('shutil.rmtree') as remove,patch('pickle.load') as load:
            for model_id in ('','.', '..','../outside',r'..\outside',str(self.outside),'C:\\outside',None,42):
                with self.subTest(model_id=model_id):
                    with self.assertRaises(ValueError):self.predictor.delete_model(model_id)
                    with self.assertRaises(ValueError):self.predictor.load(model_id)
            remove.assert_not_called();load.assert_not_called()

    def test_valid_removal_names_exactly_one_model_directory(self):
        folder=self.model();other=self.model('B')
        with patch('shutil.rmtree') as remove:self.assertTrue(self.predictor.delete_model('A'));self.assertEqual(Path(remove.call_args.args[0]).resolve(),folder.resolve());self.assertEqual(remove.call_count,1)
        self.assertTrue(other.is_dir());self.assertEqual((self.outside/'sentinel.txt').read_text(),'preserve')

    def test_candidate_models_keep_the_existing_fallback_directory(self):
        folder=self.model('paper_fixture',paper=True)
        with patch('shutil.rmtree') as remove:self.assertTrue(self.predictor.delete_model('paper_fixture'));self.assertEqual(Path(remove.call_args.args[0]).resolve(),folder.resolve());self.assertFalse(self.predictor.delete_model('missing'));self.assertEqual(remove.call_count,1)

    def test_non_directory_does_not_become_a_model(self):
        file=self.predictor.model_dir/'not_a_directory';file.write_text('preserve')
        with patch('shutil.rmtree') as remove:self.assertFalse(self.predictor.delete_model('not_a_directory'));remove.assert_not_called()
        with self.assertRaises(FileNotFoundError):self.predictor.load('not_a_directory')

    def test_directory_alias_cannot_redirect_model_io(self):
        alias=self.predictor.model_dir/'alias'
        self.assertTrue(alias.parent.resolve().is_relative_to(self.root));self.assertTrue(self.outside.resolve().is_relative_to(self.root))
        try:alias.symlink_to(self.outside,target_is_directory=True)
        except OSError:
            if os.name!='nt':self.skipTest('controlled directory alias cannot be created')
            done=subprocess.run(['cmd','/c','mklink','/J',str(alias),str(self.outside)],capture_output=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            if done.returncode:self.skipTest('controlled directory junction cannot be created')
        with patch('shutil.rmtree') as remove:
            with self.assertRaises(ValueError):self.predictor.delete_model('alias')
            with self.assertRaises(ValueError):self.predictor.load('alias')
            remove.assert_not_called()
        self.assertEqual((self.outside/'sentinel.txt').read_text(),'preserve')

    def test_removed_artifact_cannot_be_used_from_memory(self):
        folder=self.model();self.predictor.load('A');archived=self.root/'preserved_A';self.assertTrue(folder.resolve().is_relative_to(self.root));self.assertTrue(archived.resolve().is_relative_to(self.root));folder.rename(archived)
        with self.assertRaisesRegex(FileNotFoundError,'移除|不存在'):self.predictor.predict_bh({'f_Goss':.7})
        self.assertTrue((archived/'model.pkl').exists())

    def test_successful_instance_removal_clears_its_loaded_model(self):
        self.model();self.predictor.load('A')
        with patch('shutil.rmtree'):self.assertTrue(self.predictor.delete_model('A'))
        self.assertEqual(self.predictor.metadata,{})
        with self.assertRaises(RuntimeError):self.predictor.predict_bh({'f_Goss':.7})

    def test_failed_removal_keeps_the_loaded_instance(self):
        self.model();self.predictor.load('A');before=self.predictor.metadata.copy()
        with patch('shutil.rmtree',side_effect=OSError('locked file')):
            with self.assertRaises(OSError):self.predictor.delete_model('A')
        self.assertEqual(self.predictor.metadata,before)


class ModelDeletionAPI(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.available={'A','B'};self.hook=None;self.deleted=threading.Event();outer=self
        class Predictor:
            def __init__(self,folder):self.model_dir=Path(folder);self.metadata={}
            def load(self,name):
                if name not in outer.available:raise FileNotFoundError(name)
                self.metadata={'model_id':name}
            def delete_model(self,name):
                if name=='..':raise ValueError('invalid model ID')
                if name=='locked':raise OSError('locked file')
                outer.deleted.set()
                if name in outer.available:outer.available.remove(name);return True
                return False
            def predict_bh(self,params):
                if not self.metadata:raise ValueError('model not loaded')
                if outer.hook:outer.hook(self)
                return dict(actual=self.metadata['model_id'],RD={'H':[0,1],'B':[0,1]})
        self.Predictor=Predictor;self.active=Predictor(self.tmp.name);self.active.load('A')
        for change in (patch.object(web,'_ml_predictor',self.active),patch.object(web,'_new_ml_predictor',side_effect=Predictor)):
            change.start();self.addCleanup(change.stop)

    def delete(self,name):
        with web.app.test_client() as client:return client.delete('/api/ml/models/'+name)
    def predict(self,name=None):
        with web.app.test_client() as client:return client.post('/api/ml/predict',json=dict(model_id=name,params={}))

    def test_current_model_removal_clears_the_default_but_other_models_still_load(self):
        self.assertTrue(self.delete('A').json['success']);self.assertNotEqual(web._get_ml(),self.active);self.assertEqual(self.predict().status_code,500);self.assertEqual(self.predict('B').json['model_id'],'B')

    def test_other_or_missing_model_does_not_reset_the_current_one(self):
        self.assertTrue(self.delete('B').json['success']);self.assertFalse(self.delete('missing').json['success']);self.assertIs(web._get_ml(),self.active);self.assertEqual(self.predict().json['model_id'],'A')

    def test_invalid_id_is_a_structured_error_without_changing_the_default(self):
        response=self.delete('..');self.assertEqual(response.status_code,400);self.assertTrue(response.json['error']);self.assertIs(web._get_ml(),self.active)

    def test_removal_failure_keeps_the_default_and_exposes_the_error(self):
        response=self.delete('locked');self.assertEqual(response.status_code,500);self.assertIn('locked',response.json['error']);self.assertIs(web._get_ml(),self.active)

    def test_inference_and_removal_share_the_selection_lock(self):
        entered=threading.Event();release=threading.Event();out={}
        def hook(model):entered.set();self.assertTrue(release.wait(4))
        self.hook=hook;inference=threading.Thread(target=lambda:out.update(prediction=self.predict()));removal=threading.Thread(target=lambda:out.update(deletion=self.delete('A')));inference.start()
        try:self.assertTrue(entered.wait(4));removal.start();self.assertFalse(self.deleted.wait(.1))
        finally:release.set()
        inference.join(5);removal.join(5);self.assertFalse(inference.is_alive());self.assertFalse(removal.is_alive());self.assertEqual(out['prediction'].json['model_id'],'A');self.assertTrue(out['deletion'].json['success']);self.assertEqual(self.predict().status_code,500)


if __name__=='__main__':unittest.main()
