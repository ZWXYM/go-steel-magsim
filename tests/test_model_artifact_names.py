"""Real tiny synthetic fits at a frozen clock must retain both saved model artifacts."""
import hashlib
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
import pandas as pd
import app  # Preserve the original flat module import setup.
import ml_trainer
import paper_surrogate_trainer


class FixedClock:
    @staticmethod
    def now():return datetime(2026,1,1,0,0,0)


class ModelArtifactNames(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.dataset=self.root/'synthetic_storage_fixture.csv'
        pd.DataFrame([dict(f_Goss=.1+i*.1,theta_0_deg=i,B_0deg_H100=.2+i*.02,B_90deg_H100=.1+i*.01,dataset_role='synthetic_storage_test') for i in range(8)]).to_csv(self.dataset,index=False)

    def check(self,module,make,run,prefix):
        folder=self.root/'models'
        with patch.object(module,'datetime',FixedClock):
            first=run(make(folder));path=folder/first['model_id'];before={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in path.iterdir() if p.is_file()}
            second=run(make(folder))
        self.assertNotEqual(first['model_id'],second['model_id']);self.assertTrue(first['model_id'].startswith(prefix));self.assertTrue(second['model_id'].startswith(prefix))
        self.assertEqual(before,{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in path.iterdir() if p.is_file()})
        for result in (first,second):self.assertEqual(json.loads((folder/result['model_id']/'config.json').read_text())['model_id'],result['model_id'])

    def test_regular_training_at_the_same_second_preserves_both_complete_models(self):
        self.check(ml_trainer,lambda folder:ml_trainer.BHPredictor(str(folder)),lambda model:model.train(str(self.dataset),model_type='ridge_poly'),'model_')

    def test_selection_training_at_the_same_second_preserves_both_complete_models(self):
        self.check(paper_surrogate_trainer,lambda folder:paper_surrogate_trainer.PaperSurrogateTrainer(output_dir=str(folder),candidate_models=['extra_trees'],extra_trees_params={'n_estimators':2,'n_jobs':1}),lambda model:model.run(str(self.dataset),min_samples=5,n_splits=2,test_size=.25),'paper_model_')


if __name__=='__main__':unittest.main()
