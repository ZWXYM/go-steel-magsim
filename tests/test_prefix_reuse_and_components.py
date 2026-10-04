"""Native reuse must bind actual input/output and preserve root identity."""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from modules.material_calibration import MU0, file_hash
from tools.run_calibration_pilot import prepare, write_json
from tools.analyze_sampling_convergence import read_ensemble
from tools.reuse_native_prefix import reuse
from tools.analyze_component_variation import decompose, symmetric_difference
from tools.screen_expanded_sampling import screen_values
from tools.run_calibration_pilot import H_GRID


def setup_source(root):
    source, target = root/'n4', root/'n8'
    manifest = prepare(source, 4, 123)
    prepare(target, 8, 123)
    complete = []
    for job in manifest['jobs']:
        if job['grade'] != 'B30P105':
            continue
        output = source/job['output']
        output.mkdir(parents=True)
        rows = []
        for label, fields in [(-1, [50000, 800, 0, -800, -50000]),
                              (1, [-50000, -800, 0, 800, 50000])]:
            for h in fields:
                rows.append([0, .1*np.sign(h), 0, 0, MU0*h, 0, 0, label]
                            if job['direction'] == 'RD' else
                            [0, 0, .1*np.sign(h), 0, 0, MU0*h, 0, label])
        table = output/'table.txt'
        np.savetxt(table, rows, header='# t (s)\tmx ()\tmy ()\tmz ()\tB_extx (T)\tB_exty (T)\tB_extz (T)\tbranch ()', comments='')
        complete.append(dict(**job, exit_code=0, elapsed_seconds=2,
                             table_sha256=file_hash(table), execution_kind='native'))
    write_json(source/'run_status.json', dict(mumax_binary_sha256='frozen-binary', jobs=complete))
    return source, target, manifest


class PrefixReuse(unittest.TestCase):
    def test_copy_binds_bytes_preserves_source_and_resume_is_idempotent(self):
        with tempfile.TemporaryDirectory() as folder, contextlib.redirect_stdout(io.StringIO()):
            source, target, _ = setup_source(Path(folder))
            before = {p.relative_to(source): file_hash(p) for p in source.rglob('*') if p.is_file()}
            result = reuse(source, target, 'B30P105', ['RD', 'TD'])
            self.assertEqual(result['added_imported_jobs'], 8)
            self.assertEqual(result['newly_executed_native_solves'], 0)
            self.assertFalse(result['native_execution_performed'])
            status = json.loads((target/'run_status.json').read_text())
            for job in status['jobs']:
                self.assertEqual(file_hash(target/job['output']/'table.txt'), job['table_sha256'])
                self.assertEqual(job['root_native_origin']['table_sha256'], job['table_sha256'])
                self.assertEqual(job['execution_kind'], 'imported_native_prefix')
            status_before = file_hash(target/'run_status.json')
            self.assertEqual(reuse(source, target, 'B30P105', ['RD', 'TD'])['added_imported_jobs'], 0)
            self.assertEqual(file_hash(target/'run_status.json'), status_before)
            self.assertEqual(before, {p.relative_to(source): file_hash(p) for p in source.rglob('*') if p.is_file()})
            self.assertFalse((target/'runner.lock').exists())
            # A half-filled N8 remains incomplete; import cannot make N4 count as N8.
            with self.assertRaisesRegex(ValueError, 'incomplete'):
                read_ensemble(target, 'B30P105', 'TD')

    def test_changed_native_bytes_or_executed_script_rejected_before_copy(self):
        for field in ('table', 'executed_script'):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as folder:
                source, target, manifest = setup_source(Path(folder))
                if field == 'table':
                    job = next(j for j in manifest['jobs'] if j['grade'] == 'B30P105')
                    table = source/job['output']/'table.txt'
                    table.write_bytes(table.read_bytes()+b'\n')
                else:
                    status = json.loads((source/'run_status.json').read_text())
                    status['jobs'][0]['script_sha256'] = 'bad'
                    write_json(source/'run_status.json', status)
                with self.assertRaises(ValueError):
                    reuse(source, target, 'B30P105', ['RD', 'TD'])
                self.assertFalse((target/'run_status.json').exists())

    def test_prefix_identity_binary_and_lock_drift_are_rejected(self):
        for field in ('seed', 'script', 'binary', 'lock', 'source_lock'):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as folder:
                source, target, _ = setup_source(Path(folder))
                if field == 'seed':
                    path = target/'manifest.json'
                    manifest = json.loads(path.read_text())
                    manifest['seed'] += 1
                    write_json(path, manifest)
                elif field == 'script':
                    manifest = json.loads((target/'manifest.json').read_text())
                    job = next(j for j in manifest['jobs'] if j['grade'] == 'B30P105')
                    (target/job['script']).write_text('changed input')
                elif field == 'binary':
                    write_json(target/'run_status.json', dict(mumax_binary_sha256='other', jobs=[]))
                else:
                    ((source if field == 'source_lock' else target)/'runner.lock').write_text('other owner')
                with self.assertRaises(ValueError):
                    reuse(source, target, 'B30P105', ['RD', 'TD'])
                if field.endswith('lock'):
                    self.assertEqual(((source if field == 'source_lock' else target)/'runner.lock').read_text(), 'other owner')
                self.assertFalse(any((target/'B30P105').rglob('table.txt')))

    def test_same_or_nested_directory_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for other in (root, root/'child'):
                with self.assertRaisesRegex(ValueError, 'non-nested'):
                    reuse(root, other, 'G', ['TD'])


class ComponentIdentities(unittest.TestCase):
    def test_full_curve_failure_cannot_hide_behind_H800(self):
        values = np.zeros((128, len(H_GRID)))
        values[:64, -1] = .2
        limits = dict(abs_B800_prefix_T=.02, max_curve_prefix_T=.05)
        result = screen_values(values, 64, limits)
        self.assertTrue(result['B800_prefix_pass'])
        self.assertFalse(result['whole_curve_prefix_pass'])
        self.assertAlmostEqual(result['max_curve_prefix_delta_T'], .1)

    def test_mean_and_population_variance_close_with_unbalanced_counts(self):
        values = np.array([[1., 2.], [2., 4.], [3., 6.], [.1, .2], [.3, .4]])
        result = decompose(values, ['goss']*3+['haar_background']*2, .79)
        np.testing.assert_allclose(result['realized_mean'], result['fixed_prior_diagnostic']+result['count_term'], atol=1e-14)
        np.testing.assert_allclose(values.var(axis=0), result['within_goss_population_variance']
            +result['within_background_population_variance']+result['between_component_population_variance'], atol=1e-14)
        self.assertEqual(result['goss_count'], 3)

    def test_seed_difference_is_exact_and_preserves_sign(self):
        a = decompose(np.array([[1., 2.], [3., 4.], [0., 1.], [2., 3.]]), ['goss']*2+['haar_background']*2, .79)
        b = decompose(np.array([[2., 1.], [4., 3.], [6., 5.], [-1., 0.], [1., 2.]]), ['goss']*3+['haar_background']*2, .79)
        terms = symmetric_difference(a, b)
        np.testing.assert_allclose(terms['delta'], terms['count']+terms['within_goss']+terms['within_background'], atol=1e-14)
        reverse = symmetric_difference(b, a)
        for key in terms:
            np.testing.assert_allclose(reverse[key], -terms[key])

    def test_inadequate_or_unknown_components_are_not_imputed(self):
        for labels in (['goss']*4, ['goss']*3+['haar_background'], ['goss']*2+['unknown']*2):
            with self.assertRaises(ValueError):
                decompose(np.ones((4, 2)), labels, .79)


if __name__ == '__main__':
    unittest.main(verbosity=2)
