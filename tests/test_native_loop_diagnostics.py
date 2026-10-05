import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from modules.material_calibration import MU0
from modules.native_loop_diagnostics import audit_table, initial_state_script, MSAT, VOLUME
from tools.run_calibration_pilot import H_GRID
from tools.probe_loop_stability import table_metrics
from tools.prepare_initial_state_audit import prepare, study_protocol
from tools.decompose_zero_state_audit import decompose

ROOT = PROJECT.parent if PROJECT.name == 'magsim' else PROJECT


def table_fixture(path, angle=0, duplicate=False):
    direction = np.array([np.cos(np.deg2rad(angle)), np.sin(np.deg2rad(angle)), 0.])
    signed = np.r_[-H_GRID[:0:-1], H_GRID]
    rows = []
    for branch, hs in ((-1, signed[::-1]), (1, signed)):
        for h in hs:
            m = .99*np.sign(h) if h else -.5*branch
            values = [0., *(m*direction), *(MU0*h*direction), 100.*VOLUME, branch, 1e-7]
            if duplicate:
                values.extend(m*direction)
            rows.append(values)
    header = 't (s)\tmx ()\tmy ()\tmz ()\tB_extx (T)\tB_exty (T)\tB_extz (T)\tE_total (J)\tbranch ()\tmaxTorque (T)'
    if duplicate:
        header += '\tmx ()\tmy ()\tmz ()'
    np.savetxt(path, rows, delimiter='\t', header=header, comments='')


class NativeLoopContracts(unittest.TestCase):
    def test_rd_td_projection_units_and_true_zero_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            for angle in (0., 90., 35.):
                path = Path(tmp)/f'{angle}.txt'
                table_fixture(path, angle)
                m = audit_table(path, H_GRID, angle_deg=angle)
                self.assertAlmostEqual(m['native_descending_H0_T'], .5*MU0*MSAT)
                self.assertAlmostEqual(m['native_ascending_H0_T'], -.5*MU0*MSAT)
                self.assertAlmostEqual(m['native_unforced_midpoint_H0_T'], 0.)
                self.assertAlmostEqual(m['max_energy_density_J_per_m3'], 100.)
                self.assertAlmostEqual(m['max_residual_torque_T'], 1e-7)
                self.assertLess(m['max_branch_inversion_error_T'], 1e-12)
                self.assertTrue(m['native_H0_is_not_measured_Br'])

    def test_generic_td_preserves_historical_numeric_diagnostics(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'table.txt'
            table_fixture(p, 90.)
            old,_=table_metrics(p)
            new=audit_table(p,H_GRID,angle_deg=90.)
            for key in ('max_branch_inversion_error_T','min_signed_endpoint_m_projection',
                    'max_residual_torque_T','max_energy_density_J_per_m3','B800_before_guard_T','B800_after_guard_T'):
                self.assertAlmostEqual(new[key],old[key])

    def test_missing_torque_stays_unknown_and_duplicate_conflicts_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'table.txt'
            table_fixture(p, duplicate=True)
            self.assertEqual(audit_table(p,H_GRID,angle_deg=0.)['rows'],102)
            a=np.loadtxt(p,skiprows=1)
            header=p.read_text().splitlines()[0].split('\t')
            a[0,-3] += .01
            np.savetxt(p,a,delimiter='\t',header='\t'.join(header),comments='')
            with self.assertRaisesRegex(ValueError,'Conflicting duplicate'):
                audit_table(p,H_GRID,angle_deg=0.)
            table_fixture(p)
            a=np.loadtxt(p,skiprows=1)
            header=p.read_text().splitlines()[0].split('\t')
            np.savetxt(p,a[:,:-1],delimiter='\t',header='\t'.join(header[:-1]),comments='')
            m=audit_table(p,H_GRID,angle_deg=0.)
            self.assertIsNone(m['max_residual_torque_T'])
            self.assertEqual(m['torque_status'],'not_recorded_unknown')

    def test_direction_incomplete_schedule_and_label_contracts(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'table.txt'
            table_fixture(p)
            with self.assertRaisesRegex(ValueError,'direction'):
                audit_table(p,H_GRID,angle_deg=90.)
            original=p.read_text().splitlines()
            p.write_text('\n'.join(original[:-1]))
            with self.assertRaisesRegex(ValueError,'Incomplete'):
                audit_table(p,H_GRID,angle_deg=0.)
            table_fixture(p)
            a=np.loadtxt(p,skiprows=1)
            a[a[:,8]==1,8]=2
            np.savetxt(p,a,delimiter='\t',header=original[0],comments='')
            with self.assertRaisesRegex(ValueError,'branch labels'):
                audit_table(p,H_GRID,angle_deg=0.)

    def test_cpu_prepared_controls_keep_physical_prefix_and_solver_matched(self):
        p=ROOT/'calibration/pilot_20261003_n8/B23R075/RD/scripts/grain_001.mx3'
        source=p.read_text(encoding='utf-8')
        marker='// Pre-saturation: Start from saturated state'
        prefix=source.split(marker)[0]
        for condition in study_protocol()['conditions']:
            script=initial_state_script(source,condition,H_GRID)
            self.assertTrue(script.startswith(prefix))
            self.assertIn('MinimizerStop = 1e-7\nMinimizerSamples = 20',script)
            self.assertIn('tableadd(maxTorque)',script)
            self.assertNotIn('relax()',script)
            expected=102 if condition=='strict_major_loop' else 52
            self.assertEqual(script.count('tablesave()'),expected)
            if condition=='zero_transverse_pair':
                self.assertEqual(script.count('B_ext = vector(0, 0, 0)'),2)
                self.assertIn('state_phase = -2.0',script)
                self.assertIn('state_phase = 2.0',script)
            else:
                self.assertIn('state_phase = -1.0',script)
        with self.assertRaises(ValueError):
            initial_state_script(source,'unknown',H_GRID)

    def test_real_cpu_preparation_never_launches_process_and_preserves_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)/'audit'
            with patch('subprocess.run',side_effect=AssertionError('No native execution')):
                report=prepare(ROOT,out)
            self.assertEqual(len(report['grain_details']),64)
            self.assertEqual(len(report['jobs']),16)
            self.assertEqual(set(j['status'] for j in report['jobs']),{'prepared_not_executed'})
            self.assertFalse(report['protocol']['native_execution_allowed'])
            self.assertEqual(report['new_MuMax_solves'],0)
            self.assertEqual(sum(g['unknown_torque_grains'] for g in report['summaries']),64)
            integrity=json.loads((out/'source_integrity.json').read_text())
            self.assertEqual(integrity['files_checked'],525)
            self.assertTrue(integrity['unchanged'])
            with self.assertRaises(ValueError):
                prepare(ROOT,out)
            with self.assertRaises(ValueError):
                prepare(ROOT,ROOT/'calibration/pilot_20261003_n8/forbidden_child')

    def test_zero_state_decomposition_is_an_identity_without_source_repair(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'report.json'
            group=dict(grade='S',direction='RD',grains=2,endpoint_screen_failures=1,inversion_screen_failures=1)
            grains=[dict(grade='S',direction='RD',grain_id=i+1,native_unforced_midpoint_H0_T=h,
                B800_before_guard_T=h+.002,max_residual_torque_T=None) for i,h in enumerate((0.,.977))]
            p.write_text(json.dumps(dict(summaries=[group],grain_details=grains)))
            original=p.read_bytes()
            r=decompose(p)
            self.assertAlmostEqual(r['rows'][0]['mean_B800_minus_unforced_H0_diagnostic_T'],.002)
            self.assertAlmostEqual(r['rows'][0]['grain_B800_minus_H0_std_T'],0.)
            self.assertEqual(r['rows'][0]['high_unforced_H0_grain_ids'],[2])
            self.assertTrue(r['rows'][0]['no_offset_subtraction_applied'])
            self.assertEqual(p.read_bytes(),original)


if __name__=='__main__':
    unittest.main()
