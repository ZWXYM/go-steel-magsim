import sys
import unittest
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from modules.maxwell_air_interface import manufactured_fields, make_protocol, make_execution_protocol, classify_edges, evaluate_fields, verify_air_material, verify_explicit_object_axes
from test_maxwell_interface_controls import CS


class AirInterface(unittest.TestCase):
    def test_saved_secondary_unit_vector_preserves_direction_without_assuming_meters(self):
        text="$begin 'ObjectCSParameters'\nDrivenByXAxis=true\nReverseXAxis=false\nReverseYAxis=false\n$begin 'xAxis'\nDirectionType='AbsoluteDirection'\nxDirection='1mm'\nyDirection='0meter'\nzDirection='0meter'\n$end 'xAxis'\n$begin 'yAxis'\nDirectionType='AbsoluteDirection'\nxDirection='0'\nyDirection='1'\nzDirection='0'\n$end 'yAxis'\n$end 'ObjectCSParameters'\n"
        r=verify_explicit_object_axes(text,np.eye(3))
        self.assertEqual(r['scopes']['yAxis'],'native_normalized_unitless_direction')
        self.assertFalse(r['legacy_motor_CS_reinterpreted'])
        with self.assertRaises(ValueError):verify_explicit_object_axes(text.replace("yDirection='1'","yDirection='2'"),np.eye(3))
        with self.assertRaises(ValueError):verify_explicit_object_axes(text.replace("yDirection='0meter'","yDirection='1'"),np.eye(3))
    def test_omitted_scalars_require_unmodified_system_vacuum_identity(self):
        text="$begin 'vacuum'\nLibrary='Materials'\nLibLocation='SysLibrary'\nModSinceLib=false\n$end 'vacuum'\n"
        r=verify_air_material(text)
        self.assertEqual(r['omitted_default_fields'],['permeability','conductivity'])
        with self.assertRaises(ValueError):verify_air_material(text.replace('ModSinceLib=false','ModSinceLib=true'))
        with self.assertRaises(ValueError):verify_air_material(text.replace("$end 'vacuum'","permeability='2'\n$end 'vacuum'"))
        explicit=text.replace("$end 'vacuum'","permeability='1'\nconductivity='0'\n$end 'vacuum'")
        self.assertEqual(verify_air_material(explicit)['omitted_default_fields'],[])
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

    def test_native_topology_excludes_shared_edges_without_assuming_ids(self):
        edges=[]
        for start,end in ((-10.,0.),(0.,10.)):
            vertices=[[start,-10.,0.],[end,-10.,0.],[end,10.,0.],[start,10.,0.]]
            for i in range(4):
                edges.append(dict(id=20+len(edges)*3,vertices_mm=[vertices[i],vertices[(i+1)%4]]))
        topology=classify_edges(edges[::-1])
        self.assertEqual(len(topology['exterior_edge_ids']),6)
        self.assertEqual(len(topology['excluded_shared_edge_ids']),2)
        self.assertFalse(set(topology['exterior_edge_ids'])&set(topology['excluded_shared_edge_ids']))
        edges[0]['vertices_mm'][0][0]=-11.
        with self.assertRaises(ValueError):classify_edges(edges)

    def test_manufactured_fields_and_energy_acceptance_requires_real_convergence(self):
        p=make_execution_protocol(CS());case=p['cases'][0]
        mat=np.tile(case['expected']['material_B_T'],(9,1));air=np.tile(case['expected']['air_B_T'],(9,1))
        conv=dict(adaptive_converged=True,passes=[dict(energy_J=case['expected']['expected_total_energy_J'])])
        result=evaluate_fields(case,mat,air,conv,p['planned_gates'])
        self.assertTrue(result['control_success']);self.assertFalse(result['native_H_used_for_B_or_energy_gate'])
        conv['adaptive_converged']=False
        self.assertFalse(evaluate_fields(case,mat,air,conv,p['planned_gates'])['control_success'])
        with self.assertRaises(ValueError):evaluate_fields(case,mat[:8],air,conv,p['planned_gates'])

    def test_negative_control_cannot_succeed_by_missing_or_matching_data(self):
        p=make_execution_protocol(CS());case=p['cases'][-1]
        mat=np.tile(case['expected']['material_B_T'],(9,1));air=np.tile(case['expected']['air_B_T'],(9,1))
        conv=dict(adaptive_converged=True,passes=[dict(energy_J=case['expected']['expected_total_energy_J'])])
        self.assertFalse(evaluate_fields(case,mat,air,conv,p['planned_gates'])['control_success'])
        mat[0,0]+=.002
        self.assertTrue(evaluate_fields(case,mat,air,conv,p['planned_gates'])['control_success'])
        conv['adaptive_converged']=False
        self.assertFalse(evaluate_fields(case,mat,air,conv,p['planned_gates'])['control_success'])


if __name__=='__main__':unittest.main()
