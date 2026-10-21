"""Threaded prediction/export identity, failed-load rollback and isolated training publication."""
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import app as web


class StubPredictor:
    def __init__(self, model_dir, hooks):
        self.model_dir = Path(model_dir); self.hooks = hooks
        self.metadata = {'model_id':'A'}

    def load(self, model_id):
        self.metadata = {'model_id':model_id}
        if model_id == 'broken': raise ValueError('incomplete saved model')

    def predict_bh(self, params):
        if self.hooks.get('predict'): self.hooks['predict'](self)
        name = self.metadata['model_id']
        return dict(actual=name, RD={'H':[0,800],'B':[0,{'A':1,'B':2,'trained':3}[name]]},
                    H_axis='legacy_unspecified' if name=='B' else 'physical_A_per_m')

    def train(self, dataset, **kwargs):
        self.metadata = {'model_id':'partial_training_state'}
        if self.hooks.get('train'): self.hooks['train'](self)
        self.metadata = {'model_id':'trained'}
        return {'model_id':'trained'}


class ModelSelectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.hooks = {}; self.active = StubPredictor(self.root/'models',self.hooks)
        for change in [patch.object(web,'_ml_predictor',self.active),patch.object(web,'_new_ml_predictor',side_effect=lambda folder:StubPredictor(folder,self.hooks))]:
            change.start();self.addCleanup(change.stop)

    @staticmethod
    def call(model_id=None, path='/api/ml/predict', **extra):
        with web.app.test_client() as client:
            response=client.post(path,json=dict(model_id=model_id,params={},**extra))
            result=dict(status=response.status_code,data=response.json if response.is_json else response.data)
            response.close(); return result

    def concurrent(self, export=False):
        entered=threading.Event(); release=threading.Event(); second_done=threading.Event(); out={}
        def predict(model):
            if model.metadata['model_id']=='A': entered.set(); self.assertTrue(release.wait(4))
        self.hooks['predict']=predict
        def request(name):
            out[name]=self.call(name,'/api/analyze/export-maxwell-from-prediction' if export and name=='A' else '/api/ml/predict',mat_name='fixture')
            if name=='B': second_done.set()
        first=threading.Thread(target=request,args=('A',));second=threading.Thread(target=request,args=('B',))
        first.start()
        try:
            self.assertTrue(entered.wait(4));second.start()
            self.assertFalse(second_done.wait(.1),'another selection must wait for the current inference snapshot')
        finally: release.set()
        first.join(5);second.join(5);self.assertFalse(first.is_alive());self.assertFalse(second.is_alive())
        return out

    def test_concurrent_predictions_keep_requested_curve_and_H_contract(self):
        out=self.concurrent()
        for name in ('A','B'):
            self.assertEqual(out[name]['status'],200);self.assertEqual(out[name]['data']['actual'],name)
            self.assertEqual(out[name]['data']['model_id'],name)
        self.assertEqual(out['A']['data']['RD']['B'],[0,1]);self.assertEqual(out['B']['data']['RD']['B'],[0,2])
        self.assertEqual(out['A']['data']['H_axis'],'physical_A_per_m');self.assertEqual(out['B']['data']['H_axis'],'legacy_unspecified')

    def exporter(self):
        captured=[]
        def export(result, **kwargs):
            captured.append(result);path=self.root/'only_test_export.amat';path.write_text(result['model_id']+' '+result['actual'],encoding='utf8');return str(path)
        fake=Mock();fake.export_from_prediction.side_effect=export;change=patch.object(web,'_get_mx',return_value=fake);change.start();self.addCleanup(change.stop);return captured

    def test_prediction_export_cannot_take_another_requests_model(self):
        captured=self.exporter();out=self.concurrent(export=True)
        self.assertEqual(out['A']['status'],200);self.assertEqual(out['A']['data'],b'A A')
        self.assertEqual(captured[0]['model_id'],'A');self.assertEqual(captured[0]['H_axis'],'physical_A_per_m')
        self.assertEqual(out['B']['data']['actual'],'B')

    def test_failed_partial_load_preserves_the_previous_default(self):
        self.assertEqual(self.call('broken')['status'],500)
        self.assertIs(web._get_ml(),self.active)
        result=self.call();self.assertEqual(result['status'],200);self.assertEqual(result['data']['model_id'],'A');self.assertEqual(result['data']['actual'],'A')

    def test_failed_inference_preserves_default_and_releases_selection_lock(self):
        def fail(model):
            if model.metadata['model_id']=='B':raise ValueError('prediction failure')
        self.hooks['predict']=fail;self.assertEqual(self.call('B')['status'],500)
        self.assertEqual(self.call()['data']['model_id'],'A')
        self.hooks.clear();self.assertEqual(self.call('B')['data']['model_id'],'B')

    def test_background_training_is_private_until_success_without_blocking_predictions(self):
        entered=threading.Event();release=threading.Event();out={}
        def train(model):entered.set();self.assertTrue(release.wait(4))
        self.hooks['train']=train
        worker=threading.Thread(target=lambda:out.update(web._train_ml_model('unused_fixture.csv')));worker.start()
        try:
            self.assertTrue(entered.wait(4));self.assertIs(web._get_ml(),self.active)
            result=self.call('B');self.assertEqual(result['status'],200);self.assertEqual(result['data']['actual'],'B')
        finally:release.set()
        worker.join(5);self.assertFalse(worker.is_alive());self.assertEqual(out['model_id'],'trained')
        self.assertEqual(self.call()['data']['actual'],'trained')

    def test_failed_training_never_publishes_partial_state(self):
        self.hooks['train']=Mock(side_effect=ValueError('training failed'))
        with self.assertRaisesRegex(ValueError,'training failed'):web._train_ml_model('fixture')
        self.assertIs(web._get_ml(),self.active);self.assertEqual(self.call()['data']['actual'],'A')

    def test_train_route_schedules_isolated_entrypoint_without_running_training(self):
        dataset=self.root/'fixture.csv';dataset.write_text('unused fixture',encoding='utf8')
        with patch.object(web,'tasks',{}),patch.object(web,'run_task') as schedule:
            with web.app.test_client() as client:response=client.post('/api/ml/train',json=dict(dataset_path=str(dataset),model_type='extra_trees'))
            self.assertEqual(response.status_code,200);self.assertIn(response.json['task_id'],web.tasks)
            self.assertEqual(schedule.call_args.args[0],response.json['task_id'])
            self.assertIs(schedule.call_args.args[1],web._train_ml_model)
            self.assertEqual(schedule.call_args.args[2],str(dataset));self.assertEqual(schedule.call_args.kwargs['model_type'],'extra_trees')
        self.assertIs(web._get_ml(),self.active)

    def test_implicit_export_records_the_actual_successful_model_instead_of_null(self):
        captured=self.exporter();self.assertEqual(self.call('B')['status'],200)
        result=self.call(path='/api/analyze/export-maxwell-from-prediction',mat_name='fixture')
        self.assertEqual(result['status'],200);self.assertEqual(captured[0]['model_id'],'B');self.assertEqual(captured[0]['actual'],'B')


if __name__=='__main__':unittest.main()
