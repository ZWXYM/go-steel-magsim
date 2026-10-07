import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'modules'))
from test_calibration_transfer import fixtures
from modules.calibration_prediction import predict_pair,resolve_prediction_bank
from modules.calibration_transfer import TransferBank,legacy_bank,load_bank
from modules.maxwell_exporter import export_calibrated_pair
from modules.material_calibration import PHYSICS_VERSION


def raw(sample):
    return dict(material_id=sample['grade'],params=sample['params'],simulation_physics_version=PHYSICS_VERSION,
        **{d:dict(H=sample['curves'][d]['H'],B=sample['curves'][d]['raw_B']) for d in ('RD','TD')})


class PredictionScope(unittest.TestCase):
    def test_both_directions_and_top_level_identify_actual_bank_and_refit_once(self):
        samples=fixtures();bank=TransferBank.fit(samples)
        with patch('modules.calibration_prediction.TransferBank.fit',wraps=TransferBank.fit) as refit:
            result=predict_pair(raw(samples[0]),bank,['S0'],include_bank=True)
            self.assertEqual(refit.call_count,1)
        actual=TransferBank(result['_effective_bank_payload'])
        self.assertEqual(result['calibration_sha256'],actual.bank_sha256)
        self.assertNotEqual(result['calibration_sha256'],bank.bank_sha256)
        self.assertEqual(result['source_calibration_sha256'],bank.bank_sha256)
        for d in ('RD','TD'):
            self.assertEqual(result[d]['calibration_sha256'],result['calibration_sha256'])
            self.assertNotIn('S0',result[d]['anchor_weights'])
            self.assertNotIn('S0',result[d]['selection_training_grades'])
        self.assertNotIn('S0',result['parameter_support']['training_grades'])

    def test_excluded_reference_changes_parent_only_not_selection_curve_or_effective_identity(self):
        samples=fixtures();changed=copy.deepcopy(samples)
        for d in ('RD','TD'):changed[0]['curves'][d]['reference_B']=[0.,.7,1.7,1.8]
        a=predict_pair(raw(samples[0]),TransferBank.fit(samples),['S0'])
        b=predict_pair(raw(samples[0]),TransferBank.fit(changed),['S0'])
        self.assertNotEqual(a['source_calibration_sha256'],b['source_calibration_sha256'])
        self.assertEqual(a['calibration_sha256'],b['calibration_sha256'])
        self.assertEqual(a['parameter_support'],b['parameter_support'])
        for d in ('RD','TD'):
            for key in ('B','transfer_candidate','selection_training_sha256'):self.assertEqual(a[d][key],b[d][key])

    def test_no_exclusion_keeps_curves_and_known_anchor_mode_identical(self):
        samples=fixtures();bank=TransferBank.fit(samples);result=predict_pair(raw(samples[0]),bank)
        for d in ('RD','TD'):
            old=bank.correct(samples[0]['curves'][d]['H'],samples[0]['curves'][d]['raw_B'],samples[0]['params'],direction=d)
            self.assertEqual(result[d]['B'],old['B']);self.assertEqual(result[d]['prediction_mode'],'known_anchor_calibration')
        self.assertEqual(result['calibration_sha256'],bank.bank_sha256)

    def test_invalid_or_insufficient_exclusion_is_not_silently_ignored(self):
        bank=TransferBank.fit(fixtures())
        for bad in ('S0',['unknown'],[True],['S0','S1']):
            with self.assertRaises(ValueError):resolve_prediction_bank(bank,bad)

    def test_legacy_omits_complete_material_and_preserves_original_bank(self):
        samples=fixtures();bank=legacy_bank(samples,'legacy_multi_peak_importance_v1');before=copy.deepcopy(bank.payload)
        result=predict_pair(raw(samples[0]),bank,['S0'],include_bank=True)
        self.assertEqual(bank.payload,before)
        self.assertEqual({r['grade'] for r in result['_effective_bank_payload']['anchors']},{'S1','S2','S3'})
        self.assertNotEqual(result['calibration_sha256'],bank.bank_sha256)

    def test_export_scope_matches_effective_bank_and_rejects_direction_or_membership_conflict(self):
        samples=fixtures();result=predict_pair(raw(samples[0]),TransferBank.fit(samples),['S0'])
        with tempfile.TemporaryDirectory() as folder:
            path=Path(export_calibrated_pair(result['RD'],result['TD'],'Scoped',thickness_mm=.23,export_dir=folder))
            metadata=json.loads(path.with_suffix('.metadata.json').read_text())
            self.assertEqual(metadata['calibration_sha256'],result['calibration_sha256'])
            self.assertEqual(metadata['prediction_scope'],result['prediction_scope'])
            td=copy.deepcopy(result['TD']);td['prediction_scope']['excluded_grades']=[]
            with self.assertRaisesRegex(ValueError,'scope'):export_calibrated_pair(result['RD'],td,'Mismatch',thickness_mm=.23,export_dir=folder)
            rd,td=copy.deepcopy(result['RD']),copy.deepcopy(result['TD'])
            rd['prediction_scope']['effective_training_grades'].append('S0');td['prediction_scope']=rd['prediction_scope']
            with self.assertRaisesRegex(ValueError,'scope'):export_calibrated_pair(rd,td,'Leak',thickness_mm=.23,export_dir=folder)

    def test_cli_refuses_existing_output_without_overwriting_files(self):
        samples=fixtures();bank=TransferBank.fit(samples)
        tool=Path(__file__).resolve().parents[1]/'tools/predict_calibrated_material.py'
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp);(p/'raw.json').write_text(json.dumps(raw(samples[0])));(p/'bank.json').write_text(json.dumps(bank.payload))
            output=p/'old';output.mkdir();sentinel=output/'keep.txt';sentinel.write_text('old result')
            proc=subprocess.run([sys.executable,'-X','utf8',str(tool),'--bank',str(p/'bank.json'),'--raw-pair',str(p/'raw.json'),
                '--output-dir',str(output)],capture_output=True,text=True)
            self.assertNotEqual(proc.returncode,0);self.assertIn('must be new',proc.stderr)
            self.assertEqual(sentinel.read_text(),'old result');self.assertEqual(list(output.iterdir()),[sentinel])

    def test_browser_exclusion_exports_real_bank_and_filters_training_quality(self):
        from flask import Flask
        from modules.calibration_workbench import CalibrationWorkbench,GRADES
        from modules.calibration_transfer import VERSION,digest
        from modules.workbench_routes import create_workbench
        project=Path(__file__).resolve().parents[1]
        root=project.parent if project.name=='magsim' else project
        with tempfile.TemporaryDirectory() as folder:
            store=Path(folder);manager=CalibrationWorkbench(root,store/'calibration')
            report=manager.calibrate(list(GRADES),VERSION)
            pair=json.loads((manager.pilot/'B30P105/raw_pair.json').read_text())
            app=Flask('scope_fixture')
            with patch.dict('os.environ',{'MAGSIM_CPU_ONLY':'1'}):
                app.register_blueprint(create_workbench(project,store,root))
            client=app.test_client()
            url='/api/workbench/calibrations/'+report['id']+'/predict-excluded'
            response=client.post(url,json=dict(raw_pair=pair,exclude_grades=['B30P105']))
            self.assertEqual(response.status_code,200,response.json)
            result=response.json;out=manager.get(report['id'])/result['id']
            actual=load_bank(out/'effective_bank.json')
            self.assertEqual(actual.bank_sha256,result['calibration_sha256'])
            self.assertNotIn('B30P105',result['parameter_support']['training_grades'])
            expected=digest({g:q for g,q in report['native_quality'].items() if g!='B30P105'})
            self.assertEqual(result['RD']['calibration_native_quality_sha256'],expected)
            metadata=json.loads((out/result['AMAT_file']).with_suffix('.metadata.json').read_text())
            self.assertEqual(metadata['prediction_scope'],result['prediction_scope'])
            for excluded in ([],['unknown'],['B30P105','B23R075']):
                self.assertEqual(client.post(url,json=dict(raw_pair=pair,exclude_grades=excluded)).status_code,400)


if __name__=='__main__':unittest.main()
