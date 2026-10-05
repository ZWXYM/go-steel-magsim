import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT))
from modules.motor_phase_diagnostics import phase_pair,read_channels
from modules.motor_workbench import collect_metrics
from tools.audit_motor_phase import audit,FILES
from modules.material_calibration import file_hash


class MotorPhaseDiagnostics(unittest.TestCase):
    def test_exact_periodic_current_has_zero_difference_and_boundary_count_is_explicit(self):
        t=np.linspace(0,.03,91)
        y=np.cos(np.arange(91)*2*np.pi/30)
        report=phase_pair(t,y)
        self.assertLess(report['max_abs_phase_delta'],1e-14)
        self.assertEqual(len(report['delta_values']),31)
        self.assertLess(report['parseval_absolute_error'],1e-27)

    def test_single_interior_bad_phase_survives_fft_and_shift_diagnostics(self):
        t=np.linspace(0,.03,91)
        y=np.ones(91)
        y[75]+=.25
        report=phase_pair(t,y)
        self.assertEqual(report['max_abs_phase_delta'],.25)
        self.assertEqual(report['max_abs_interior_phase_delta'],.25)
        self.assertEqual(report['worst_phase_index'],15)
        self.assertEqual(report['second_worst_time_ms'],25.)
        self.assertLess(report['parseval_absolute_error'],1e-15)
        self.assertTrue(report['shifted_result_is_not_acceptance'])

    def test_timing_shift_is_detected_without_replacing_original_residual(self):
        t=np.linspace(0,.03,91)
        base=np.sin(np.arange(30)*2*np.pi/30)
        y=np.zeros(91)
        y[30:60]=base
        y[60:90]=np.roll(base,2)
        y[90]=y[60]
        report=phase_pair(t,y)
        self.assertEqual(report['best_cyclic_shift_samples'],-2)
        self.assertGreater(report['max_abs_phase_delta'],.3)
        self.assertLess(report['best_shift_rms'],1e-14)

    def test_wrong_grid_and_missing_channel_unit_are_rejected(self):
        with self.assertRaisesRegex(ValueError,'91-point'):
            phase_pair(np.linspace(0,.03,90),np.ones(90))
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'reports/Torque Plots.csv'
            p.parent.mkdir()
            with p.open('w',newline='') as stream:
                writer=csv.writer(stream)
                writer.writerow(['Time [ns]','-Moving1.Torque'])
                writer.writerows(zip(np.linspace(0,3e7,91),np.ones(91)))
            with self.assertRaisesRegex(ValueError,'unit declaration'):
                read_channels(Path(tmp))

    def test_CPU_scan_audit_handles_windows_hash_paths_and_does_not_mutate_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'scan'
            source.mkdir()
            cases=[dict(id=f'case_{i:02d}',material=f'synthetic_{i}') for i in (1,2)]
            (source/'manifest.json').write_text(json.dumps(dict(id='synthetic_scan',measurement_protocol='periodic_cycle3_v1',cases=cases)))
            (source/'state.json').write_text(json.dumps(dict(status='completed_with_failures')))
            t=np.linspace(0,.03,91)
            torque=np.ones(91)*2
            torque[75]+=.25
            current=[np.sin(2*np.pi*t/.01+i*2*np.pi/3) for i in range(3)]
            for case in cases:
                folder=source/case['id']
                reports=[('reports/Torque Plots.csv',['-Moving1.Torque [NewtonMeter]','TorqueDQ []'],[torque,torque]),
                    ('reports/Drive Current Plots.csv',[f'InputCurrent(phase{i}) [mA]' for i in range(3)],current)]
                reports.extend((f'maxwell_reports/{name}.csv',[f'{name} [mW]'],[np.ones(91)*1000])
                    for name in ('CoreLoss','StrandedLoss','SolidLoss'))
                for name,labels,columns in reports:
                    path=folder/name
                    path.parent.mkdir(parents=True,exist_ok=True)
                    with path.open('w',newline='') as stream:
                        writer=csv.writer(stream)
                        writer.writerow(['Time [ns]',*labels])
                        writer.writerows(zip(t*1e9,*columns))
                result=collect_metrics(folder,'periodic_cycle3_v1')
                result['csv_hashes']={k.replace('/','\\'):v for k,v in result['csv_hashes'].items()}
                (folder/'result.json').write_text(json.dumps(result))
            before={str(p.relative_to(source)):file_hash(p) for p in source.rglob('*') if p.is_file()}
            report=audit(source,Path(tmp)/'audit')
            after={str(p.relative_to(source)):file_hash(p) for p in source.rglob('*') if p.is_file()}
            self.assertEqual(before,after)
            self.assertTrue(report['source_unchanged'])
            self.assertEqual(report['new_Maxwell_solves'],0)
            self.assertFalse(report['cases'][0]['existing_acceptance'])
            self.assertFalse(report['cases'][0]['channels']['TorqueDQ']['unit_is_physically_confirmed'])
            with self.assertRaisesRegex(ValueError,'new directory'):
                audit(source,Path(tmp)/'audit')


if __name__=='__main__':
    unittest.main()
