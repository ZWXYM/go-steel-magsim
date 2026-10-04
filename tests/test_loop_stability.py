"""Loop probes must expose unhealthy raw states even when guard changes are small."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT))
from modules.material_calibration import MU0,file_hash
from tools.probe_loop_stability import (CONDITIONS, H_GRID, MSAT, VOLUME,
    probe_script,table_metrics,verify_frozen,VERSION,analyze)
from tools.run_calibration_pilot import pilot_script,write_json


def table_fixture(path, poisoned=False, torque=True, hmax=50000):
    grid=np.r_[-H_GRID[:0:-1],H_GRID]
    if hmax>50000:
        grid=np.r_[-hmax,grid,hmax]
    rows=[]
    for label,schedule in [(-1,grid[::-1]),(1,grid)]:
        for h in schedule:
            my=.99*np.tanh(h/1000.)
            mz=0.
            if poisoned and label==1:
                my=0.
                mz=1.
            row=[0,0,my,mz,0,MU0*h,0,(1e6 if mz else 100)*VOLUME,label]
            if torque:
                row.append(.2 if mz else 1e-7)
            rows.append(row)
    header='# t (s)\tmx ()\tmy ()\tmz ()\tB_extx (T)\tB_exty (T)\tB_extz (T)\tE_total (J)\tbranch ()'
    if torque:
        header+='\tmaxTorque (T)'
    np.savetxt(path,rows,header=header,comments='')


class LoopStability(unittest.TestCase):
    def test_declared_conditions_preserve_material_and_branch_history(self):
        source=pilot_script([0,45,0],90)
        for condition in CONDITIONS:
            probe=probe_script(source,condition)
            if condition['id']=='exact_repeat':
                self.assertEqual(probe,source)
                continue
            for line in source.splitlines():
                if line.startswith(('Msat =','Aex =','alpha =','Kc1 =','Kc2 =','Ku1 =','anisC','SetGrid','SetCell','Hx_dir','Hy_dir','Hz_dir')):
                    self.assertIn(line,probe)
            self.assertEqual(probe.count('tableadd(maxTorque)'),1)
            self.assertEqual('m = uniform(-Hx_dir' in probe,condition['reset'])
            self.assertIn('H = '+str(condition['hmax']),probe)
            self.assertIn('H = -'+str(condition['hmax']),probe)
            if condition['solver']=='relax':
                self.assertNotIn('minimize()',probe)
                self.assertIn('RelaxTorqueThreshold = -1',probe)
            if condition['solver']=='hybrid':
                self.assertIn('if maxTorque.Get() > 1e-5',probe)
                self.assertEqual(probe.count('fallback_count := 0.0'),1)
                self.assertEqual(probe.count('tableaddvar(fallback_count'),1)
                self.assertIn('RelaxTorqueThreshold = -1',probe)
            if condition['id']=='strict_minimize':
                self.assertIn('MinimizerStop = 1e-07',probe)
                self.assertIn('MinimizerSamples = 20',probe)

    def test_unrecognized_presaturation_is_rejected(self):
        source=pilot_script([0,45,0],90).replace('H_max := 50000.0','H_max := 30000.0')
        with self.assertRaisesRegex(ValueError,'presaturation'):
            probe_script(source,CONDITIONS[1])

    def test_raw_symmetry_units_and_high_field_vacuum_term(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'table.txt'
            table_fixture(p,hmax=200000)
            result,points=table_metrics(p)
            self.assertLess(result['max_branch_inversion_error_T'],1e-12)
            self.assertAlmostEqual(result['min_signed_endpoint_m_projection'],.99)
            self.assertAlmostEqual(result['max_energy_density_J_per_m3'],100.)
            positive=next(r for r in points if r['H_A_per_m']>199999)
            self.assertAlmostEqual(positive['B_T'],MU0*(200000+MSAT*.99))
            self.assertAlmostEqual(result['unforced_midpoint_H0_T'],0.)

    def test_small_guard_does_not_hide_failed_endpoints_or_large_torque(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'table.txt'
            table_fixture(p,poisoned=True)
            result,_=table_metrics(p)
            self.assertLess(result['max_guard_change_T'],1e-12)
            self.assertEqual(result['min_signed_endpoint_m_projection'],0.)
            self.assertGreater(result['max_branch_inversion_error_T'],1.)
            self.assertAlmostEqual(result['max_residual_torque_T'],.2)
            self.assertAlmostEqual(result['max_energy_density_J_per_m3'],1e6)

    def test_absent_telemetry_is_unknown_and_bad_schedule_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'table.txt'
            table_fixture(p,torque=False)
            result,_=table_metrics(p)
            self.assertIsNone(result['max_residual_torque_T'])
            lines=p.read_text().splitlines()
            lines[2]=lines[1]
            p.write_text('\n'.join(lines))
            with self.assertRaisesRegex(ValueError,'schedule'):
                table_metrics(p)

    def test_native_repeated_m_columns_must_agree(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'table.txt'
            table_fixture(p)
            lines=p.read_text().splitlines()
            a=np.loadtxt(p,skiprows=1)
            repeated=np.column_stack([a,a[:,1:4]])
            header=lines[0]+'\tmx ()\tmy ()\tmz ()'
            np.savetxt(p,repeated,header=header,comments='')
            self.assertLess(table_metrics(p)[0]['max_branch_inversion_error_T'],1e-12)
            repeated[0,-2]+=.01
            np.savetxt(p,repeated,header=header,comments='')
            with self.assertRaisesRegex(ValueError,'Conflicting duplicate'):
                table_metrics(p)

    def test_frozen_input_drift_and_incomplete_analysis_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            run=Path(folder)
            p=run/'original.txt'
            p.write_bytes(b'original evidence')
            manifest=dict(protocol=VERSION,frozen_files=[dict(path=p.name,sha256=file_hash(p))],
                jobs=[dict(script='probe.mx3',script_sha256='expected')],mumax_binary_sha256='frozen binary')
            p.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError,'input changed'):
                verify_frozen(run,manifest)
            p.write_bytes(b'original evidence')
            manifest['jobs']=[]
            write_json(run/'manifest.json',manifest)
            write_json(run/'run_status.json',dict(manifest_sha256='wrong',mumax_binary_sha256='frozen binary',jobs=[]))
            output=run/'analysis'
            with self.assertRaisesRegex(ValueError,'runtime contract'):
                analyze(run,output)
            self.assertFalse(output.exists())


if __name__=='__main__':
    unittest.main()
