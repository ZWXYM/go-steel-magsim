"""Saved prediction collection retains actual banks, inputs and immutable bytes."""
import io,json,unittest,zipfile
from unittest.mock import patch
import test_calibration_regularized as fixtures
from modules.material_library import MaterialLibrary
from modules.system_data_builder import SystemDataBuilder


class SavedPredictionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):fixtures.RegularizedWorkbenchTests.setUpClass.__func__(cls)
    @classmethod
    def tearDownClass(cls):fixtures.RegularizedWorkbenchTests.tearDownClass.__func__(cls)
    def setUp(self):
        self.library=MaterialLibrary(self.manager)
        self.builder=SystemDataBuilder(self.storage,self.manager,None,self.library,None)
        self.exports=self.builder.prediction_exports
        self.row=self.exports.catalog()[0]
        self.selection={k:self.row[k] for k in ('calibration_id','id','source_binding_sha256')}

    def test_prediction_only_workpack_keeps_input_child_parent_and_no_recomputation(self):
        with patch('subprocess.Popen',side_effect=AssertionError('No process for saved data')):
            result=self.builder.build(dict(calibration=None,datasets=[],batch=None,predictions=[self.selection]))
        _,blob=self.builder.store.read(result['id'])
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            self.assertEqual(len(archive.namelist()),5)
            index=json.loads(archive.read('data_index.json'));saved=index['saved_predictions'][0]
            self.assertEqual(saved['excluded_grades'],['B30P105']);self.assertFalse(saved['efficiency_enabled'])
            self.assertNotEqual(saved['parent_bank_sha256'],saved['effective_bank_sha256'])
            self.assertEqual(index['new_predictions'],0);self.assertIsNone(index['calibration'])
            with zipfile.ZipFile(io.BytesIO(archive.read('predictions/'+saved['calibration_id']+'/'+saved['id']+'.zip'))) as nested:
                folder=self.manager.get(saved['calibration_id'])/saved['id']
                self.assertEqual(nested.read('input_raw_pair.json'),(folder/'input_raw_pair.json').read_bytes())
                self.assertEqual(nested.read('effective_bank.json'),(folder/'effective_bank.json').read_bytes())

    def test_duplicate_malformed_or_excessive_selection_does_not_register(self):
        before=self.builder.store.records()
        for rows in ([self.selection]*2,[self.selection]*17,[dict(self.selection,id='../escape')],{},[None]):
            with self.assertRaises((ValueError,TypeError)):
                self.builder.build(dict(calibration=None,datasets=[],batch=None,predictions=rows))
        self.assertEqual(before,self.builder.store.records())

    def test_stale_source_binding_is_rejected(self):
        report=self.manager.get(self.trial['id'])/'report.json';old=report.read_bytes()
        try:
            report.write_bytes(old+b'\n')
            with self.assertRaisesRegex(ValueError,'来源已变化'):self.exports.collect(self.selection)
        finally:report.write_bytes(old)

    def test_source_changed_during_collection_is_rejected(self):
        report=self.manager.get(self.trial['id'])/'report.json';old=report.read_bytes();original=self.library.bundle
        def mutate(*args):
            bundle=original(*args);report.write_bytes(old+b'\n');return bundle
        try:
            with patch.object(self.library,'bundle',side_effect=mutate):
                with self.assertRaisesRegex(ValueError,'打包期间变化'):self.exports.collect(self.selection)
        finally:report.write_bytes(old)

    def test_catalog_retains_broken_record_without_a_selectable_binding(self):
        path=self.manager.get(self.trial['id'])/self.prediction['id']/'input_raw_pair.json';old=path.read_bytes()
        try:
            path.write_bytes(b'{}')
            row=self.exports.catalog()[0];self.assertFalse(row['readable']);self.assertIn('error',row)
            self.assertNotIn('source_binding_sha256',row)
        finally:path.write_bytes(old)


if __name__=='__main__':unittest.main()
