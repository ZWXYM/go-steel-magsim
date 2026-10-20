"""The preview binds a frozen report to its source bank and only reads files."""
import csv
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask
from modules.calibration_beta_preview import CalibrationBetaPreview
from modules.calibration_transfer import TransferBank, digest, nested_validation
from modules.material_library import sha
from modules.workbench_routes import create_workbench
from tools.run_calibration_beta_trial import Budget, evaluate, protocol
from test_calibration_transfer import fixtures

PROJECT=Path(__file__).resolve().parents[1]


class BetaPreviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.storage=self.root/'records'
        self.artifact='cal_'+'a'*12;self.folder=self.storage/'calibration'/self.artifact
        self.folder.mkdir(parents=True)
        samples=fixtures();self.bank=TransferBank.fit(samples)
        (self.folder/'bank.json').write_text(json.dumps(self.bank.payload),encoding='utf8')
        result=evaluate(samples,nested_validation(samples),Budget())
        plan=protocol();summary={key:result[key] for key in ('version','summaries','gates','all_acceptance_gates_passed')}
        summary.update(frozen_protocol_sha256=digest(plan),B800_passed_directions=8,B800_total_directions=8)
        stream=io.StringIO(newline='');writer=csv.DictWriter(stream,fieldnames=list(result['metrics'][0]))
        writer.writeheader();writer.writerows(result['metrics'])
        self.study=self.root/'calibration/low_beta_trial_20261020';self.study.mkdir(parents=True)
        for name,value in [('summary.json',summary),('curves.json',result['curves']),
                           ('outer_selection.json',result['outer_folds']),('frozen_protocol.json',plan)]:
            (self.study/name).write_text(json.dumps(value),encoding='utf8')
        (self.study/'metrics.csv').write_bytes(stream.getvalue().encode('utf-8-sig'))
        self.hashes={p.name:sha(p.read_bytes()) for p in self.study.iterdir()}
        self.binding=patch.multiple('modules.calibration_beta_preview',EXPECTED_BANK_SHA256=self.bank.bank_sha256,
                                   EXPECTED_FILES=self.hashes)
        self.binding.start();self.addCleanup(self.binding.stop)
        self.preview=CalibrationBetaPreview(SimpleNamespace(root=self.root,get=lambda artifact:self.folder))

    def files(self):
        return {p.relative_to(self.root).as_posix():p.read_bytes() for p in self.root.rglob('*') if p.is_file()}

    def test_snapshot_keeps_all_curves_metrics_and_existing_files_unchanged(self):
        before=self.files();result=self.preview.snapshot(self.artifact)
        self.assertEqual(len(result['curves']),8);self.assertEqual(len(result['metrics']),24)
        self.assertTrue(result['read_only']);self.assertFalse(result['default_prediction_changed'])
        self.assertFalse(result['material_exports_changed']);self.assertFalse(result['automatic_deployment'])
        self.assertEqual(result['new_candidate_evaluations'],0);self.assertEqual(result['bank_sha256'],self.bank.bank_sha256)
        self.assertEqual(before,self.files())

    def test_different_valid_bank_is_not_mixed_with_the_frozen_curves(self):
        samples=fixtures();samples[0]['params']['f_Goss']+=.001
        changed=TransferBank.fit(samples)
        (self.folder/'bank.json').write_text(json.dumps(changed.payload),encoding='utf8')
        with self.assertRaisesRegex(ValueError,'来源不同'):self.preview.snapshot(self.artifact)

    def test_changed_frozen_data_is_rejected_before_display(self):
        (self.study/'curves.json').write_text('[]',encoding='utf8')
        with self.assertRaisesRegex(ValueError,'文件已改变'):self.preview.snapshot(self.artifact)

    def test_matching_file_hash_does_not_allow_wrong_H_or_reference_membership(self):
        path=self.study/'curves.json';curves=json.loads(path.read_text());curves[0]['H'][1]=101
        path.write_text(json.dumps(curves),encoding='utf8');self.hashes[path.name]=sha(path.read_bytes())
        with self.assertRaisesRegex(ValueError,'物理H'):self.preview.snapshot(self.artifact)

    def test_duplicate_metric_identity_is_not_summarized(self):
        path=self.study/'metrics.csv';rows=list(csv.reader(io.StringIO(path.read_text(encoding='utf-8-sig'))))
        rows[-1]=rows[1]
        stream=io.StringIO(newline='');csv.writer(stream).writerows(rows)
        path.write_bytes(stream.getvalue().encode('utf-8-sig'));self.hashes[path.name]=sha(path.read_bytes())
        with self.assertRaisesRegex(ValueError,'指标材料'):self.preview.snapshot(self.artifact)

    def test_gate_summary_cannot_hide_a_failed_criterion(self):
        path=self.study/'summary.json';summary=json.loads(path.read_text())
        summary['all_acceptance_gates_passed']=not all(summary['gates'].values())
        path.write_text(json.dumps(summary),encoding='utf8');self.hashes[path.name]=sha(path.read_bytes())
        with self.assertRaisesRegex(ValueError,'接受状态'):self.preview.snapshot(self.artifact)

    def test_missing_study_has_a_specific_read_only_error(self):
        empty=self.root/'empty';empty.mkdir()
        self.preview.calibration.root=empty
        with self.assertRaisesRegex(ValueError,'没有已冻结'):self.preview.snapshot(self.artifact)

    def test_CPU_HTTP_and_download_read_without_dispatch_or_new_records(self):
        app=Flask('beta_preview_routes')
        with patch.dict(os.environ,{'MAGSIM_CPU_ONLY':'1','MAGSIM_AUTO_RESUME_QUEUE':'0'}), \
                patch('modules.workbench_routes.OptimizationPlans.start_monitor'):
            app.register_blueprint(create_workbench(PROJECT,self.storage,self.root))
        before=self.files()
        with patch('subprocess.Popen',side_effect=AssertionError('no native dispatch')):
            client=app.test_client();url='/api/workbench/calibrations/'+self.artifact+'/beta-trial-preview'
            response=client.get(url);self.assertEqual(response.status_code,200,response.json)
            self.assertTrue(response.json['read_only'])
            download=client.get(url+'/download');self.addCleanup(download.close)
            self.assertEqual(download.status_code,200);self.assertIn('attachment',download.headers['Content-Disposition'])
            self.assertEqual(json.loads(download.data)['curves'],response.json['curves'])
            self.assertEqual(client.post(url,json={}).status_code,423)
        self.assertEqual(before,self.files())


if __name__=='__main__':unittest.main()
