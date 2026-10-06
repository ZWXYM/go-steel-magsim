import sys
import unittest
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from modules.maxwell_air_interface import manufactured_fields, make_protocol
from test_maxwell_interface_controls import CS


class AirInterface(unittest.TestCase):
    def test_rotated_tensor_respects_normal_B_and_tangential_H_continuity(self):
        t=.7;R=np.array([[np.cos(t),-np.sin(t),0.],[np.sin(t),np.cos(t),0.],[0.,0.,1.]])
        r=manufactured_fields(R)
        self.assertEqual(r['material_B_T'][0],r['air_B_T'][0])
        self.assertAlmostEqual(r['material_H_analytic_A_per_m'][1],0.)
        self.assertAlmostEqual(r['air_H_analytic_A_per_m'][1],0.)
        self.assertGreater(abs(r['material_B_T'][1]),.01)
        self.assertGreater(r['expected_total_energy_J'],0.)

    def test_global_tensor_degenerates_to_uniform_B_without_cross_component(self):
        r=manufactured_fields(np.eye(3))
        self.assertEqual(r['material_B_T'][1],0.)
        self.assertAlmostEqual(r['material_H_analytic_A_per_m'][0]*1000,r['air_H_analytic_A_per_m'][0])

    def test_protocol_explicitly_remains_prepared_and_excludes_internal_boundary(self):
        record=CS();origin=np.array(record['origin_m']);t=.7
        record['x_axis_absolute_m']=(origin+.001*np.array([np.cos(t),np.sin(t),0.])).tolist()
        record['y_axis_absolute_m']=(origin+.001*np.array([-np.sin(t),np.cos(t),0.])).tolist()
        p=make_protocol(record)
        self.assertFalse(p['native_execution_entrypoint_available']);self.assertEqual(p['new_field_solves'],0)
        self.assertIn('exclude shared',p['boundary_edges'])
        self.assertGreater(abs(p['cases'][-1]['negative_control_interface_Hy_analytic']),1.)
        self.assertFalse(p['cases'][-1]['should_match_manufactured_solution'])

    def test_nonphysical_frame_or_source_is_rejected(self):
        with self.assertRaises(ValueError):manufactured_fields(np.ones((3,3)))
        with self.assertRaises(ValueError):manufactured_fields(np.eye(3),float('nan'))


if __name__=='__main__':unittest.main()
