"""Real package handoff, immutable snapshots and exclusion from native queues."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask
import test_material_library as fixtures
PROJECT=fixtures.PROJECT
from modules.calibrated_motor_preparation import CalibratedMotorPreparation
from modules.motor_workbench import MotorWorkbench
from modules.workbench_routes import create_workbench


class CalibratedPreparationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.MaterialLibraryTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        fixtures.MaterialLibraryTests.tearDownClass.__func__(cls)
    def setUp(self):
        fixtures.MaterialLibraryTests.setUp(self)
        self.motor=MotorWorkbench(self.root,self.store/'motor',PROJECT)
        self.template=self.motor.template
        self.template.parent.mkdir(parents=True)
        self.template.write_text(''.join("$begin 'GeometryPart'\nName='GOES_V3_%02d'\nMaterialValue='\"old\"'\nPartCoordinateSystem=%d\n$end 'GeometryPart'\n"%(i,i+1) for i in range(1,49))+"OriginalRotorStatorCoreLoss='preserved'\n")
        self.prep=CalibratedMotorPreparation(self.library,self.motor,self.store/'calibrated_motor')

    def test_snapshot_same_material_banks_and_no_queue_or_native_dispatch(self):
        before=self.template.read_bytes()
        with patch('subprocess.Popen',side_effect=AssertionError('no native')):
            result=self.prep.prepare(dict(calibration=self.cal,key=self.key,cores=2))
        self.assertEqual(result['effective_bank_sha256'],self.library.detail(self.cal,self.key)['effective_bank_sha256'])
        self.assertEqual(result['excluded_grades'],['B30P105'])
        self.assertFalse(result['efficiency_enabled']);self.assertFalse(result['native_submission_allowed'])
        self.assertFalse(result['native_import_completed']);self.assertEqual(self.motor.jobs(),[])
        self.assertEqual(self.template.read_bytes(),before)
        folder=self.prep.path(result['id'])
        self.assertEqual((folder/'package/metadata.json').read_bytes(),(folder/'package/material.metadata.json').read_bytes())
        source=self.base/self.pred
        saved=json.loads((source/'prediction.json').read_text())
        self.assertEqual((folder/'package/material.amat').read_bytes(),(source/saved['AMAT_file']).read_bytes())
        self.assertEqual((folder/'motor.aedt').read_bytes(), before.replace(b'MaterialValue=\'"old"\'', ('MaterialValue=\'"'+result['material_name']+'"\'').encode()))
        self.assertTrue(self.base.resolve().is_relative_to(self.store.resolve()))
        shutil.rmtree(self.base)  # temp fixture: the saved preparation must survive missing original.
        self.assertEqual(CalibratedMotorPreparation(self.library,self.motor,self.store/'calibrated_motor').get(result['id'])['material_name'],result['material_name'])

    def test_tampered_snapshot_invalid_and_invalid_parameters_leave_no_job(self):
        for value in [None,{},dict(calibration=self.cal,key=self.key,cores=True),dict(calibration=self.cal,key='../bank.json')]:
            with self.assertRaises((ValueError,FileNotFoundError)):self.prep.prepare(value)
        self.assertEqual(list(self.prep.storage.iterdir()),[])
        result=self.prep.prepare(dict(calibration=self.cal,key=self.key))
        p=self.prep.path(result['id'])/'motor.aedt';p.write_bytes(p.read_bytes()+b'\n')
        with self.assertRaisesRegex(ValueError,'改变'):self.prep.get(result['id'])
        self.assertEqual(self.prep.records()[0]['status'],'invalid')
        with self.assertRaises(ValueError):self.prep.download('../outside','motor.aedt')

    def test_cpu_routes_prepare_reopen_download_and_never_submit(self):
        import os
        app=Flask('calmotor_cpu')
        with patch.dict(os.environ,{'MAGSIM_CPU_ONLY':'1','MAGSIM_AUTO_RESUME_QUEUE':'0'}):
            app.register_blueprint(create_workbench(PROJECT,self.store,self.root))
        client=app.test_client()
        response=client.post('/api/workbench/calibrated-motor',json=dict(calibration=self.cal,key=self.key))
        self.assertEqual(response.status_code,200,response.json)
        artifact=response.json['id']
        self.assertEqual(client.get('/api/workbench/calibrated-motor').json[0]['id'],artifact)
        self.assertEqual(client.get('/api/workbench/calibrated-motor/'+artifact).json['excluded_grades'],['B30P105'])
        for name in ['motor.aedt','package.zip','manifest.json']:
            r=client.get('/api/workbench/calibrated-motor/'+artifact+'/files/'+name)
            self.assertEqual(r.status_code,200);r.close()
        self.assertEqual(client.post('/api/workbench/calibrated-motor/'+artifact+'/submit').status_code,423)
        self.assertEqual(client.get('/api/workbench/motor/jobs').json,[])

    def test_preparation_cannot_claim_import_or_efficiency(self):
        result=self.prep.prepare(dict(calibration=self.cal,key=self.key))
        p=self.prep.path(result['id'])/'manifest.json'
        result['efficiency_enabled']=True;p.write_text(json.dumps(result))
        with self.assertRaisesRegex(ValueError,'执行范围'):self.prep.get(result['id'])


if __name__=='__main__':
    unittest.main()
