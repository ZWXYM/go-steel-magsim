"""Catalog provenance, saved-fit arithmetic, unit separation and read-only HTTP exports."""
import copy
import csv
import hashlib
import io
import json
import math
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from flask import Flask
from modules.baosteel_catalog_check import SOURCE as STANDARD_CHECK
from modules.frequency_loss_validation import read_frequency_table
from modules.material_loss_reference import MaterialLossReference, GRADES, reference_csv, reference_bundle
from modules.published_loss_checks import SOURCE as PUBLISHED_CHECKS
from modules.workbench_routes import create_workbench

PROJECT = Path(__file__).resolve().parents[1]
SOURCE = PROJECT / 'go_steel_data/output/four_sample_loss_reference_v1.json'


class LossReferenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name) / 'program'
        self.path = self.project / SOURCE.relative_to(PROJECT)
        self.path.parent.mkdir(parents=True); self.path.write_bytes(SOURCE.read_bytes())
        self.catalog = MaterialLossReference(self.project)

    def test_four_grades_primary_and_alternate_sources_are_kept_separate(self):
        before = self.path.read_bytes(); data = self.catalog.read()
        self.assertEqual([r['grade'] for r in data['records']], list(GRADES))
        self.assertEqual(sum(len(r['points']) for r in data['records']), 14)
        self.assertEqual(sum(len(r['alternate_catalog_points']) for r in data['records']), 4)
        b23, b90, b95, b30 = data['records']
        self.assertEqual([p['P_W_kg'] for p in b23['points']], [.56, .74])
        self.assertEqual([p['P_W_kg'] for p in b90['points']], [.63, .86, .83, 1.15])
        self.assertEqual([p['P_W_kg'] for p in b90['alternate_catalog_points']], [.64, .86])
        self.assertEqual([p['P_W_kg'] for p in b30['points']], [.73, 1, .96, 1.31])
        self.assertEqual([p['P_W_kg'] for p in b30['alternate_catalog_points']], [.74, .99])
        self.assertFalse(data['efficiency_enabled']); self.assertFalse(data['motor_assignment_performed'])
        self.assertEqual(data['snapshot_sha256'], hashlib.sha256(before).hexdigest())
        self.assertEqual(self.path.read_bytes(), before)

    def test_saved_coefficients_reproduce_original_fit_error_without_refitting(self):
        for row in self.catalog.read()['records']:
            e = row['saved_estimate']; errors = []
            for p in row['points']:
                b, f = p['B_T'], p['f_Hz']
                predicted = e['Kh_fit_mass']*f*b*b + e['Kc_classical_mass']*f*f*b*b + e['Ke_fit_mass']*(f*b)**1.5
                errors.append(predicted - p['P_W_kg'])
            self.assertAlmostEqual(math.sqrt(sum(x*x for x in errors)/len(errors)), e['fit_rmse_w_kg'], places=12)
            self.assertAlmostEqual(max(abs(x) for x in errors), e['fit_max_abs_error_w_kg'], places=12)
            self.assertEqual(row['original_amat_coefficients'], dict(Kh=0, Kc=0, Ke=0))
            self.assertTrue(e['not_independent_validation']); self.assertFalse(row['specimen_measurement'])
        self.assertEqual(self.catalog.read()['records'][3]['saved_estimate']['Ke_fit_mass'], 0)

    def test_standalone_CSV_has_roles_source_pages_and_coefficient_units(self):
        data = self.catalog.read()
        points = list(csv.DictReader(io.StringIO(reference_csv(data, 'points').decode('utf-8-sig'))))
        self.assertEqual(len(points), 18); self.assertEqual(sum(p['role']=='primary_saved_fit_input' for p in points), 14)
        self.assertTrue(all(p['specimen_measurement']=='False' for p in points))
        self.assertEqual({p['pdf_page'] for p in points}, {'5', '17'})
        estimates = list(csv.DictReader(io.StringIO(reference_csv(data, 'estimates').decode('utf-8-sig'))))
        self.assertEqual(len(estimates), 4)
        self.assertEqual(float(estimates[3]['Ke_fit_mass']), 0)
        self.assertEqual(estimates[0]['Kc_mass_unit'], 'W kg^-1 Hz^-2 T^-2')
        self.assertIn('P_W_kg', estimates[0]['formula'])
        for row in estimates:
            self.assertAlmostEqual(float(row['Kh_fit_mass'])*float(row['assumed_density_kg_m3']), float(row['Kh_fit_volumetric']), places=12)

    def test_bundle_one_snapshot_hashes_and_original_values(self):
        data = self.catalog.read()
        with zipfile.ZipFile(reference_bundle(data)) as z:
            manifest = json.loads(z.read('manifest.json'))
            self.assertEqual(set(z.namelist()), {'reference.json','reference_points.csv','saved_estimates.csv','README.txt','manifest.json'})
            for name, sha in manifest['files'].items(): self.assertEqual(hashlib.sha256(z.read(name)).hexdigest(), sha)
            self.assertEqual(json.loads(z.read('reference.json')), data)
            self.assertIn('不是本批样品独立实测', z.read('README.txt').decode())

    def test_missing_corrupt_and_misclassified_sources_fail_without_rewriting(self):
        original = json.loads(self.path.read_bytes())
        examples = []
        for key, value in [('efficiency_enabled',True), ('new_fits',1)]:
            data = copy.deepcopy(original); data[key] = value; examples.append(data)
        for key, value in [('Kh_fit_mass',float('nan')), ('Ke_fit_mass',-1), ('Kc_classical_mass',True), ('Kh_fit_volumetric',1)]:
            data = copy.deepcopy(original); data['records'][0]['saved_estimate'][key] = value; examples.append(data)
        for data in examples:
            self.path.write_text(json.dumps(data),encoding='utf8'); before=self.path.read_bytes()
            with self.assertRaises(ValueError): self.catalog.read()
            self.assertEqual(self.path.read_bytes(), before)
        self.path.write_text('{',encoding='utf8')
        with self.assertRaisesRegex(ValueError,'不可读取'): self.catalog.read()
        self.path.rename(self.path.with_suffix('.unreadable'))
        with self.assertRaisesRegex(ValueError,'不可读取'): self.catalog.read()

    def client(self):
        app = Flask('loss_reference', template_folder=str(PROJECT/'templates'))
        with patch.dict(os.environ, {'MAGSIM_CPU_ONLY':'0','MAGSIM_AUTO_RESUME_QUEUE':'0'}), patch('modules.workbench_routes.OptimizationPlans.start_monitor'):
            app.register_blueprint(create_workbench(self.project, Path(self.tmp.name)/'records',Path(self.tmp.name)))
        return app.test_client()

    def test_HTTP_formats_no_POST_or_task_writes(self):
        client = self.client(); before = {p:p.read_bytes() for p in Path(self.tmp.name).rglob('*') if p.is_file()}
        self.assertEqual(client.get('/material-loss-reference').status_code, 200)
        default = client.get('/api/workbench/material-loss-reference'); self.assertEqual(default.status_code,200)
        for form in ('json','points_csv','estimates_csv','zip'):
            response = client.get('/api/workbench/material-loss-reference?format='+form); self.addCleanup(response.close)
            self.assertEqual(response.status_code,200); self.assertIn('attachment;',response.headers['Content-Disposition'])
            if form=='json': self.assertEqual(json.loads(response.data),default.json)
            if form=='zip': self.assertEqual(json.loads(zipfile.ZipFile(io.BytesIO(response.data)).read('reference.json')),default.json)
        self.assertEqual(client.post('/api/workbench/material-loss-reference',json={}).status_code,405)
        self.assertEqual(before,{p:p.read_bytes() for p in Path(self.tmp.name).rglob('*') if p.is_file()})

    def test_HTTP_rejects_ambiguous_queries_and_missing_resource_does_not_break_home(self):
        client=self.client()
        for query in ('format=zip&format=json','format=unknown','file=../../AGENTS.md'):
            self.assertEqual(client.get('/api/workbench/material-loss-reference?'+query).status_code,400)
        self.path.rename(self.path.with_suffix('.missing'))
        self.assertEqual(client.get('/api/workbench/material-loss-reference').status_code,400)
        self.assertEqual(client.get('/system').status_code,200)

    def test_standard_comparison_is_separate_and_keeps_original_loss_snapshot(self):
        check_path=self.project/STANDARD_CHECK
        raw=(PROJECT/STANDARD_CHECK).read_bytes()
        original=self.path.read_bytes()
        check_path.write_bytes(raw)
        client=self.client()
        page=client.get('/material-loss-reference').get_data(as_text=True)
        self.assertIn('宝钢牌号典型值对照',page)
        self.assertIn('+0.0504',page)
        self.assertIn('1.544',page)
        endpoint='/api/workbench/material-loss-reference/baosteel-check'
        result=client.get(endpoint)
        self.addCleanup(result.close)
        self.assertEqual(result.status_code,200)
        self.assertEqual(result.data,raw)
        self.assertEqual(client.get(endpoint+'?file=other').status_code,400)
        self.assertEqual(client.post(endpoint,json={}).status_code,405)
        changed=json.loads(raw)
        changed['RD_B800_rows'][0]['B800_catalog_converted_T']=changed['RD_B800_rows'][0]['J800_catalog_T']
        check_path.write_text(json.dumps(changed),encoding='utf8')
        self.assertEqual(client.get(endpoint).status_code,400)
        self.assertNotIn('宝钢牌号典型值对照',client.get('/material-loss-reference').get_data(as_text=True))
        self.assertEqual(self.path.read_bytes(),original)

    def test_published_measurements_are_downloadable_with_scope_and_frequency_units(self):
        path = self.project / PUBLISHED_CHECKS
        original = (PROJECT / PUBLISHED_CHECKS).read_bytes()
        path.write_bytes(original)
        client = self.client()
        page = client.get('/material-loss-reference').get_data(as_text=True)
        self.assertIn('B30P105 在 100 Hz 的正弦损耗', page)
        self.assertIn('3.126', page)
        self.assertIn('-18.3%', page)
        self.assertIn('B27R095 在 25 A/m 直流偏磁下的损耗', page)
        base = '/api/workbench/material-loss-reference/'
        with client.get(base + 'published-checks') as response:
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data, original)
        with client.get(base + 'figure-frequency-csv') as response:
            self.assertEqual(response.status_code, 200)
            table = read_frequency_table(response.data)
        self.assertEqual(table['input_energy_unit'], 'J/kg')
        self.assertEqual(len(table['rows']), 28)
        point = next(row for row in table['rows'] if row['frequency_Hz'] == 100 and row['peak_T'] == 1.7)
        self.assertAlmostEqual(point['loss'], 3.126, places=12)
        self.assertIsNone(point['standard_u'])
        self.assertEqual(client.get(base + 'figure-frequency-csv?source=other').status_code, 400)
        path.write_bytes(original + b'\n')
        self.assertEqual(client.get(base + 'published-checks').status_code, 400)
        self.assertNotIn('同牌号发表实测对照', client.get('/material-loss-reference').get_data(as_text=True))
        self.assertEqual(client.get(base.rstrip('/')).status_code, 200)


if __name__ == '__main__': unittest.main()
