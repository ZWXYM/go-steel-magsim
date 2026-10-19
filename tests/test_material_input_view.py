"""Saved identities stay separate from AMAT names and never refit or dispatch."""
import copy
import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from flask import Flask
PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from modules.material_input_view import MaterialInputView
from modules.workbench_routes import create_workbench


class InputViewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.params = dict(f_Goss=.855, theta_0_deg=4.5, halfwidth_deg=6.5, Si_content=3.1)
        self.paths = {}
        for name, value in {
            'parent_bank.json': dict(training_samples=[dict(grade='B30P105', params=self.params)]),
            'input_raw_pair.json': dict(material_id='CUSTOM_INPUT', params=self.params),
            'prediction.json': dict(material_id='CUSTOM_INPUT', params=self.params)
        }.items():
            self.paths[name] = self.base / name
            self.paths[name].write_bytes(json.dumps(value).encode())
        files = {n: dict(sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for n,p in self.paths.items()}
        self.details = {
            'prediction:pred_aaaaaaaaaaaa': dict(kind='prediction', files=files, material_name='WB_pred_aaaaaaaaaaaa'),
            'fit:B30P105': dict(kind='fit', files=files, material_name='WB_B30P105'),
            'holdout:B30P105': dict(kind='holdout', files=files, material_name='WB_held_B30P105')}
        self.library = Mock()
        self.library._detail.side_effect = lambda artifact,key: (copy.deepcopy(self.details[key]),self.paths)
        self.library.catalog.side_effect = lambda: dict(records=[dict(id='cal_a',entries=[
            dict(key=k,kind=v['kind'],label=k.split(':')[1],available=True) for k,v in self.details.items()])],
            new_predictions=0,new_native_solves=0)
        self.view = MaterialInputView(self.library)

    def test_prediction_name_and_parameters_do_not_change_material_or_source(self):
        before = {n:p.read_bytes() for n,p in self.paths.items()}
        value = self.view.detail('cal_a','prediction:pred_aaaaaaaaaaaa')
        self.assertEqual(value['display_name'],'CUSTOM_INPUT · pred_aaaaaaaaaaaa')
        self.assertEqual(value['material_name'],'WB_pred_aaaaaaaaaaaa')
        self.assertEqual(value['source_material_id'],'CUSTOM_INPUT')
        self.assertEqual(value['input_params'],self.params)
        self.assertIn('not_measurement',value['input_identity_scope'])
        self.assertEqual(before,{n:p.read_bytes() for n,p in self.paths.items()})
        self.library.predict.assert_not_called()

    def test_fit_and_holdout_read_parent_parameters_without_relabelling_scope(self):
        for key in ('fit:B30P105','holdout:B30P105'):
            value = self.view.detail('cal_a',key)
            self.assertEqual(value['source_material_id'],'B30P105')
            self.assertEqual(value['input_params'],self.params)
            self.assertEqual(value['kind'],key.split(':')[0])

    def test_legacy_anchor_rows_must_agree_and_not_use_another_grade(self):
        parent = self.paths['parent_bank.json']
        value=dict(anchors=[dict(grade='B30P105',params=self.params),dict(grade='B30P105',params=self.params)])
        parent.write_bytes(json.dumps(value).encode())
        for detail in self.details.values():detail['files']['parent_bank.json']['sha256']=hashlib.sha256(parent.read_bytes()).hexdigest()
        self.assertEqual(self.view.detail('cal_a','fit:B30P105')['input_params'],self.params)
        value['anchors'][1]['params']={**self.params,'Si_content':3.2}
        parent.write_bytes(json.dumps(value).encode())
        for detail in self.details.values():detail['files']['parent_bank.json']['sha256']=hashlib.sha256(parent.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError,'不一致'):self.view.detail('cal_a','fit:B30P105')

    def test_changed_source_during_read_is_rejected(self):
        self.paths['input_raw_pair.json'].write_bytes(b'{}')
        with self.assertRaisesRegex(ValueError,'读取期间变化'):
            self.view.detail('cal_a','prediction:pred_aaaaaaaaaaaa')

    def test_prediction_identity_mismatch_visible_without_hiding_healthy_entries(self):
        p=self.paths['prediction.json'];p.write_bytes(json.dumps(dict(material_id='OTHER',params=self.params)).encode())
        for detail in self.details.values():detail['files']['prediction.json']['sha256']=hashlib.sha256(p.read_bytes()).hexdigest()
        catalog=self.view.catalog()['records'][0]['entries']
        self.assertFalse(catalog[0]['available'])
        self.assertIn('代号或参数不符',catalog[0]['reason'])
        self.assertTrue(catalog[1]['available'] and catalog[2]['available'])

    def test_missing_nonfinite_or_bool_parameters_are_not_fabricated(self):
        for change in ({'Si_content':None},{'Si_content':float('nan')},{'Si_content':True}):
            p={**self.params,**change}
            for name in ('input_raw_pair.json','prediction.json'):
                self.paths[name].write_bytes(json.dumps(dict(material_id='CUSTOM_INPUT',params=p)).encode())
                for detail in self.details.values():detail['files'][name]['sha256']=hashlib.sha256(self.paths[name].read_bytes()).hexdigest()
            with self.assertRaises(ValueError):self.view.detail('cal_a','prediction:pred_aaaaaaaaaaaa')

    def test_input_routes_are_get_only_and_available_in_cpu_mode(self):
        manager=Mock();manager.catalog.return_value={'records':[],'new_native_solves':0}
        manager.detail.return_value={'source_material_id':'CUSTOM_INPUT'}
        app=Flask('input_view',template_folder=str(PROJECT/'templates'))
        with patch.dict(os.environ,{'MAGSIM_CPU_ONLY':'1','MAGSIM_AUTO_RESUME_QUEUE':'0'}), \
                patch('modules.workbench_routes.MaterialInputView',return_value=manager):
            app.register_blueprint(create_workbench(PROJECT,self.base/'storage',self.base))
        client=app.test_client()
        self.assertEqual(client.get('/api/workbench/material-inputs').json['new_native_solves'],0)
        self.assertEqual(client.get('/api/workbench/material-inputs/cal_a/prediction:pred_a').json['source_material_id'],'CUSTOM_INPUT')
        self.assertEqual(client.post('/api/workbench/material-inputs',json={}).status_code,423)


if __name__ == '__main__':
    unittest.main()
