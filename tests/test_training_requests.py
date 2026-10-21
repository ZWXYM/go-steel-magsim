"""Reject invalid training requests before creating an orphan task or worker."""
import os
import threading
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
import app as web


class TrainingRequests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.dataset=self.root/'synthetic.csv';self.dataset.write_text('not used for fitting')
        self.cwd=Path.cwd();self.addCleanup(os.chdir,self.cwd)
        self.jobs={};self.schedule=Mock()
        for change in (patch.object(web,'tasks',self.jobs),patch.object(web,'run_task',self.schedule)):
            change.start();self.addCleanup(change.stop)

    def post(self,data,route='/api/ml/train'):
        with web.app.test_client() as client:return client.post(route,json=data)

    def rejected(self,data,route='/api/ml/train'):
        response=self.post(data,route);self.assertEqual(response.status_code,400);self.assertTrue(response.is_json);self.assertTrue(response.json['error']);self.assertEqual(self.jobs,{});self.schedule.assert_not_called()

    def test_invalid_fraction_creates_no_running_task(self):
        for value in ('invalid','NaN','Infinity',None,0,1,-.1,True,[]):
            with self.subTest(value=value):self.rejected(dict(dataset_path=str(self.dataset),test_size=value))

    def test_non_object_payloads_return_recoverable_JSON(self):
        for data in (None,[],['a'],'text'):
            for route in ('/api/ml/train','/api/ml/paper-train'):
                with self.subTest(data=data,route=route):self.rejected(data,route)

    def test_missing_directory_and_non_string_dataset_are_not_jobs(self):
        for value in ('',str(self.root),str(self.root/'missing.csv'),[],42):
            for route in ('/api/ml/train','/api/ml/paper-train'):
                with self.subTest(value=value,route=route):self.rejected(dict(dataset_path=value),route)

    def test_unknown_single_model_is_rejected_before_task_creation(self):
        for value in ('made_up',None,[]):self.rejected(dict(dataset_path=str(self.dataset),model_type=value))

    def test_parameter_collections_must_have_the_expected_shape(self):
        for key,value in [('xgb_params',[]),('extra_trees_params','bad')]:
            for route in ('/api/ml/train','/api/ml/paper-train'):self.rejected(dict(dataset_path=str(self.dataset),**{key:value}),route)
        for value in ('extra_trees',None,[3]):self.rejected(dict(dataset_path=str(self.dataset),candidate_models=value),'/api/ml/paper-train')

    def test_paper_fraction_and_unhashable_preset_are_rejected(self):
        for extra in ({'test_size':'NaN'},{'test_size':0},{'preset':['lite']},{'output_dir':[]}):self.rejected(dict(dataset_path=str(self.dataset),**extra),'/api/ml/paper-train')

    def test_valid_single_request_keeps_options_and_freezes_input_path(self):
        os.chdir(self.root);response=self.post(dict(dataset_path='synthetic.csv',model_type='extra_trees',test_size='.25',xgb_params={'n_estimators':2}))
        self.assertEqual(response.status_code,200);self.assertEqual(len(self.jobs),1);self.assertIs(self.schedule.call_args.args[1],web._train_ml_model);self.assertEqual(self.schedule.call_args.args[2],str(self.dataset));self.assertEqual(self.schedule.call_args.kwargs,dict(model_type='extra_trees',test_size=.25,xgb_params={'n_estimators':2}))

    def test_valid_paper_request_freezes_input_and_output_before_the_worker(self):
        os.chdir(self.root)
        with patch('paper_surrogate_trainer.PaperSurrogateTrainer') as factory:
            factory.return_value.run.return_value={'model_id':'mock_complete'}
            response=self.post(dict(dataset_path='synthetic.csv',output_dir='saved_models',preset='smoke',candidate_models=['extra_trees'],extra_trees_params={'n_estimators':2}),'/api/ml/paper-train')
            self.assertEqual(response.status_code,200);self.assertEqual(len(self.jobs),1);work=self.schedule.call_args.args[1];os.chdir(self.cwd);self.assertEqual(work(),{'model_id':'mock_complete'})
            self.assertEqual(factory.call_args.kwargs['output_dir'],str(self.root/'saved_models'));self.assertEqual(factory.return_value.run.call_args.args[0],str(self.dataset));self.assertEqual(factory.call_args.kwargs['candidate_models'],['extra_trees'])

    def test_missing_paper_options_keep_the_existing_default_preset(self):
        response=self.post(dict(dataset_path=str(self.dataset)),'/api/ml/paper-train');self.assertEqual(response.status_code,200);self.assertEqual(response.json['preset'],'lite');self.assertEqual(len(self.jobs),1)

    def test_concurrent_repeated_request_schedules_one_worker(self):
        payload=dict(dataset_path=str(self.dataset),model_type='extra_trees',request_id='same_training_request_123');barrier=threading.Barrier(2);out=[]
        def send():barrier.wait();out.append(self.post(payload).json)
        threads=[threading.Thread(target=send) for _ in range(2)]
        for thread in threads:thread.start()
        for thread in threads:thread.join(5);self.assertFalse(thread.is_alive())
        self.assertEqual(len(out),2);self.assertEqual(out[0]['task_id'],out[1]['task_id']);self.assertEqual({value['reused'] for value in out},{False,True});self.assertEqual(self.schedule.call_count,1);self.assertEqual(len(self.jobs),1)

    def test_repeated_selection_request_and_changed_input_conflict(self):
        payload=dict(dataset_path=str(self.dataset),preset='smoke',request_id='selection_request_123');first=self.post(payload,'/api/ml/paper-train');second=self.post(payload,'/api/ml/paper-train');self.assertEqual(first.json['task_id'],second.json['task_id']);self.assertTrue(second.json['reused']);self.assertEqual(self.schedule.call_count,1)
        changed=self.post(dict(payload,preset='lite'),'/api/ml/paper-train');self.assertEqual(changed.status_code,409);self.assertEqual(self.schedule.call_count,1)

    def test_invalid_request_identifier_is_not_a_task(self):
        for value in ('short','bad/request/name',[],42):self.rejected(dict(dataset_path=str(self.dataset),request_id=value))

    def test_task_status_and_list_expose_the_request_identity_for_read_only_recovery(self):
        request_id='recover_training_request_123';created=self.post(dict(dataset_path=str(self.dataset),request_id=request_id)).json
        with web.app.test_client() as client:
            details=client.get('/api/tasks/'+created['task_id']).json;listing=client.get('/api/tasks').json
        self.assertEqual(details['training_request_id'],request_id);self.assertEqual(listing['tasks'][0]['training_request_id'],request_id);self.assertEqual(self.schedule.call_count,1)


if __name__=='__main__':unittest.main()
