import copy,csv,io,json,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from flask import Flask
from modules.motor_data_coverage import MotorDataCoverage,create_motor_coverage_routes


def data(identity,case='grade',hashes=('a','b'),accepted=True,source='official_CSV_verified'):
    prefix=('archive/' if identity=='v8_2_archive' else 'bound_archive/' if identity=='v8_2_bound_archive' else '')+case+'/'
    return dict(id=identity,version='SYNTHETIC '+identity,measurement_protocol='SYNTHETIC protocol',scope='SYNTHETIC saved rows',
        rows=[dict(case=case,material='SYNTHETIC material',input_accepted=accepted,input_source=source,metrics=dict(T_avg_Nm=0,K_T_ripple_pct=1,P_Fe_W=0,eta_estimate_pct=None),input_error='SYNTHETIC error' if not accepted else None)],
        source_hashes={prefix+'source_'+str(i)+'.csv':h*64 for i,h in enumerate(hashes)})


class MotorCoverageTests(unittest.TestCase):
    def manager(self,*records):
        self.records={r['id']:r for r in records}
        self.originals=copy.deepcopy(self.records)
        self.optimizer=SimpleNamespace(datasets=lambda:[dict(id=r['id']) for r in records],dataset=lambda k:self.records[k])
        return MotorDataCoverage(self.optimizer)

    def test_identical_complete_csv_multisets_mark_reuse_without_merging_versions_or_metrics(self):
        manager=self.manager(data('v8_2_archive'),data('v8_2_bound_archive',case='another label'))
        value=manager.snapshot();self.assertEqual(value['counts']['saved_rows'],2);self.assertEqual(value['counts']['distinct_csv_content_sets'],1)
        self.assertEqual(value['counts']['repeated_csv_content_rows'],1);self.assertIsNone(value['counts']['independent_measured_samples'])
        self.assertEqual(value['rows'][0]['same_csv_content_records'],2);self.assertNotEqual(value['rows'][0]['model_version'],value['rows'][1]['model_version'])
        self.assertEqual(self.records,self.originals);self.assertEqual(value['new_analyses'],0)

    def test_different_or_additional_csv_content_is_not_deduplicated_as_same_result(self):
        a=data('v8_2_archive');b=data('v8_2_bound_archive',hashes=('a','c'))
        manager=self.manager(a,b);self.assertEqual(manager.snapshot()['counts']['distinct_csv_content_sets'],2)
        b['source_hashes']['another/ignored.csv']='a'*64
        # A hash from another case cannot become this case's evidence.
        self.assertEqual(manager.snapshot()['rows'][1]['csv_source_file_count'],2)

    def test_failed_rows_and_missing_csv_keep_original_state_and_no_native_acceptance(self):
        manager=self.manager(data('scan_aaaaaaaaaaaa',accepted=False),data('v8_2_bound_archive',hashes=(),source='report_only'))
        value=manager.snapshot();self.assertEqual(len(value['rows']),2);self.assertEqual(value['counts']['native_csv_accepted_rows'],0)
        self.assertEqual(value['rows'][0]['input_error'],'SYNTHETIC error');self.assertIsNone(value['rows'][1]['csv_content_group'])
        self.assertEqual(value['rows'][0]['metrics']['T_avg_Nm'],0);self.assertIsNone(value['rows'][0]['metrics']['eta_estimate_pct'])

    def test_one_unreadable_dataset_does_not_hide_valid_rows_or_claim_complete(self):
        manager=self.manager(data('v8_2_archive'),data('v8_2_bound_archive'))
        original=self.optimizer.dataset
        self.optimizer.dataset=lambda k:original(k) if k=='v8_2_archive' else (_ for _ in ()).throw(ValueError('source changed'))
        value=manager.snapshot();self.assertFalse(value['counts']['complete']);self.assertEqual(len(value['rows']),1);self.assertEqual(value['errors'][0]['dataset'],'v8_2_bound_archive')
        out=list(csv.DictReader(io.StringIO(manager.csv(value).decode('utf-8-sig'))));self.assertEqual(len(out),2);self.assertEqual(out[1]['input_error'],'source changed')

    def test_duplicate_case_or_invalid_hash_is_visible_and_not_partially_counted(self):
        a=data('v8_2_archive');a['rows'].append(copy.deepcopy(a['rows'][0]));manager=self.manager(a)
        value=manager.snapshot();self.assertFalse(value['counts']['complete']);self.assertEqual(value['rows'],[])
        a['rows'].pop();a['source_hashes']['archive/grade/bad.csv']='invalid'
        value=manager.snapshot();self.assertFalse(value['counts']['complete']);self.assertEqual(value['rows'],[])

    def test_historical_estimates_or_source_label_without_files_do_not_become_verified_csv(self):
        manager=self.manager(data('v8_1'),data('v8_2_bound_archive',hashes=()))
        value=manager.snapshot();self.assertEqual(value['counts']['original_accepted_rows'],2);self.assertEqual(value['counts']['native_csv_accepted_rows'],0)
        self.assertTrue(value['rows'][0]['historical_processed_estimate']);self.assertTrue(all(row['csv_content_group'] is None for row in value['rows']))

    def test_empty_readable_dataset_is_counted_and_bad_catalog_is_not_hidden(self):
        a=data('v8_2_archive');a['rows']=[];manager=self.manager(a);value=manager.snapshot();self.assertEqual(value['counts']['dataset_count'],1);self.assertEqual(value['counts']['saved_rows'],0)
        self.optimizer.datasets=lambda:[dict(id='../../private')]
        value=manager.snapshot();self.assertFalse(value['counts']['complete']);self.assertEqual(value['counts']['dataset_count'],0)

    def test_routes_csv_units_zero_values_and_readonly_sources(self):
        manager=self.manager(data('v8_2_archive'));app=Flask('case coverage');app.register_blueprint(create_motor_coverage_routes(manager));client=app.test_client()
        with patch('subprocess.Popen',side_effect=AssertionError('No solver')):
            response=client.get('/api/workbench/system-data/motor-coverage');self.assertEqual(response.status_code,200);self.assertTrue(response.cache_control.no_store)
            csv_response=client.get('/api/workbench/system-data/motor-coverage.csv');self.assertEqual(csv_response.status_code,200);self.assertTrue(csv_response.data.startswith(b'\xef\xbb\xbf'))
        rows=list(csv.DictReader(io.StringIO(csv_response.data.decode('utf-8-sig'))));self.assertEqual(rows[0]['T_avg_Nm'],'0');self.assertEqual(rows[0]['P_Fe_W'],'0');self.assertEqual(rows[0]['eta_estimate_pct'],'')
        self.assertEqual(self.records,self.originals);self.assertEqual(client.post('/api/workbench/system-data/motor-coverage').status_code,405)


if __name__=='__main__':unittest.main()
