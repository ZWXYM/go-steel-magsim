import sys
import unittest
import tempfile
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from modules.maxwell_interface_controls import cs_frame,expected_H,make_protocol,material_payload,evaluate,verify_material,review_energy
from modules.maxwell_material_transport import load_contract
from test_maxwell_material_transport import fixture


def CS():
    return dict(origin_m=[-.0314,.00135,0.],x_axis_absolute_m=[-.0324,.00135,0.],
        y_axis_absolute_m=[-.0314,.00035,0.],settings={'DrivenByXAxis':'false'})


class InterfaceControls(unittest.TestCase):
    def protocol(self):
        with tempfile.TemporaryDirectory() as tmp:
            source=load_contract(fixture(tmp))
            source['curves']['RD']={'H':[0.,10.,20.,100.],'B':[0.,.5,1.5,1.9]}
            return make_protocol(source,CS())

    def test_Y_driven_frame_preserves_primary_axis_and_orthogonality(self):
        record=CS();R=cs_frame(record,'direction_vector')
        np.testing.assert_allclose(R[:,1],np.array(record['y_axis_absolute_m'])/np.linalg.norm(record['y_axis_absolute_m']))
        np.testing.assert_allclose(R.T@R,np.eye(3),atol=1e-12)
        H=expected_H(R,[.025,0.,0.]);self.assertGreater(H[0],190.)
        self.assertLess(expected_H(cs_frame(record,'absolute_point'),[.025,0.,0.])[0],21.)

    def test_analytic_tensor_rotation_inverts_constitutive_relation(self):
        angle=.37;R=np.array([[np.cos(angle),-np.sin(angle),0.],[np.sin(angle),np.cos(angle),0.],[0.,0.,1.]])
        B=np.array([.025,.013,0.]);H=np.array(expected_H(R,B));mu=4e-7*np.pi*(R@np.diag([1000.,100.,1000.])@R.T)
        np.testing.assert_allclose(mu@H,B,atol=1e-14)
        with self.assertRaises(ValueError):expected_H(np.ones((3,3)),B)

    def test_spatial_boundary_freezes_global_coordinates_and_conditional_budget(self):
        protocol=self.protocol();self.assertEqual(protocol['maximum_solve_attempts'],6)
        self.assertEqual(protocol['cases'][1]['boundary_CS'],'Global')
        self.assertIn('tesla*(Y - (',protocol['cases'][1]['boundary_expression'])
        self.assertEqual(len(protocol['nonlinear_requires_passed']),3)
        self.assertFalse(protocol['retry_allowed'])

    def test_simple_native_payload_cannot_smuggle_nonlinear_components(self):
        case=self.protocol()['cases'][0];args=material_payload(case)
        mu=next(a for a in args if isinstance(a,list) and a[0]=='NAME:permeability')
        self.assertIn('component1:=',mu);self.assertFalse(any(isinstance(a,list) for a in mu))
        text="$begin 'Interface_simple_global_origin'\nCoordinateSystemType='Cartesian'\n$begin 'permeability'\nproperty_type='AnisoProperty'\ncomponent1='1000'\ncomponent2='100'\ncomponent3='1000'\n$end 'permeability'\nconductivity='0'\ncore_loss_kh='0'\ncore_loss_kc='0'\ncore_loss_ke='0'\ncore_loss_kdc='0'\n$end 'Interface_simple_global_origin'"
        self.assertTrue(verify_material(text,case)['simple_tensor_saved'])
        rounded=dict(case,mu_r=[1000.0000000000005,100.,1000.])
        self.assertTrue(verify_material(text,rounded)['simple_tensor_saved'])
        with self.assertRaises(ValueError):verify_material(text,dict(case,mu_r=[1000.00000005,100.,1000.]))
        with self.assertRaises(ValueError):verify_material(text.replace("component2='100'","component2='1000'"),case)

    def test_oblique_H_is_permitted_only_when_full_vector_matches_tensor(self):
        case=self.protocol()['cases'][2]
        B=np.tile(case['target_B_vector_T'],(9,1));H=np.tile(case['expected_H_hypotheses']['direction_vector'],(9,1))
        result=evaluate(case,B,H,{'adaptive_converged':True})
        self.assertTrue(result['passed']);self.assertEqual(result['matched_H_hypothesis'],'direction_vector')
        self.assertFalse(result['calibrated_material_field_verified'])
        self.assertFalse(result['motor_ranking_eligible'])
        self.assertFalse(evaluate(case,B,H,{'adaptive_converged':False})['passed'])

    def test_full_range_uniformity_keeps_previous_strict_relative_gate(self):
        case=self.protocol()['cases'][0];B=np.tile(case['target_B_vector_T'],(9,1));H=np.tile([20.,0.,0.],(9,1))
        H[0,0]-=.015;H[1,0]+=.015
        self.assertFalse(evaluate(case,B,H,{'adaptive_converged':True})['checks']['H_uniform'])

    def test_oblique_probe_can_reject_ignored_frame_and_includes_isotropic_control(self):
        with tempfile.TemporaryDirectory() as tmp:
            source=load_contract(fixture(tmp));record=CS();origin=np.array(record['origin_m'])
            theta=.7;x=np.array([np.cos(theta),np.sin(theta),0.]);y=np.array([-np.sin(theta),np.cos(theta),0.])
            record['x_axis_absolute_m']=(origin+.001*x).tolist();record['y_axis_absolute_m']=(origin+.001*y).tolist()
            protocol=make_protocol(source,record,'oblique_millimeter');case=protocol['cases'][0]
            self.assertEqual(protocol['maximum_solve_attempts'],4)
            self.assertIn('global_orientation_ignored',case['expected_H_hypotheses'])
            B=np.tile(case['target_B_vector_T'],(9,1));H=np.tile(case['expected_H_hypotheses']['absolute_point'],(9,1))
            result=evaluate(case,B,H,{'adaptive_converged':True})
            self.assertTrue(result['passed']);self.assertEqual(result['matched_H_hypothesis'],'absolute_point')
            self.assertEqual(protocol['cases'][-1]['mu_r'],[1000.]*3)

    def test_energy_vetoes_a_uniform_but_constitutively_wrong_export(self):
        case=self.protocol()['cases'][0]
        B=np.tile(case['target_B_vector_T'],(9,1));H=np.tile([20.,0.,0.],(9,1))
        energy=.5*np.dot(B[0],H[0])*case['side_m']**2
        convergence={'adaptive_converged':True,'passes':[{'energy_J':energy}]}
        self.assertTrue(review_energy(case,B,H,convergence)['interface_response_verified'])
        convergence['passes'][0]['energy_J']=energy*2
        self.assertTrue(evaluate(case,B,H,convergence)['passed'])
        self.assertFalse(review_energy(case,B,H,convergence)['interface_response_verified'])

    def test_nonlinear_energy_does_not_use_linear_half_BH_identity(self):
        case=self.protocol()['cases'][-1]
        result=review_energy(case,None,None,{})
        self.assertFalse(result['applicable']);self.assertFalse(result['interface_response_verified'])

    def test_explicit_vectors_keep_origin_and_freeze_diagonal_cross_term(self):
        with tempfile.TemporaryDirectory() as tmp:
            source=load_contract(fixture(tmp));record=CS();before=repr(record)
            protocol=make_protocol(source,record,'axis_vectors_millimeter')
            self.assertEqual(repr(record),before)
            self.assertEqual(protocol['maximum_solve_attempts'],4)
            for c in protocol['cases']:
                self.assertEqual(c['original_geometry_CS_record'],record)
                if c['CS_kind']=='object':
                    self.assertEqual(c['object_CS']['origin_m'],record['origin_m'])
                    R=cs_frame(c['object_CS'],'direction_vector_x_primary_legacy')
                    np.testing.assert_allclose(R,c['desired_frame_global'],atol=1e-12)
            c=protocol['cases'][2]
            self.assertEqual(c['target_B_vector_T'][0],c['target_B_vector_T'][1])

    def test_native_discovered_iteration_keys_freeze_two_unchanged_curve_controls(self):
        with tempfile.TemporaryDirectory() as tmp:
            source=load_contract(fixture(tmp));source['curves']['RD']={'H':[0.,20.,100.],'B':[0.,1.5,1.9]}
            protocol=make_protocol(source,CS(),'explicit_iterations_millimeter')
            self.assertEqual(protocol['maximum_solve_attempts'],2)
            self.assertEqual(protocol['nonlinear_requires_passed'],[])
            for c in protocol['cases']:
                self.assertTrue(c['setup']['UseNonLinearIterNum']);self.assertEqual(c['setup']['MaxIterNum'],100)
                self.assertEqual(c['material']['curves'],source['curves'])

    def test_large_mu_linear_control_has_its_own_expected_H_without_reference_relabel(self):
        with tempfile.TemporaryDirectory() as tmp:
            source=load_contract(fixture(tmp))
            source['curves']['RD']={'H':[0.,10.,20.,100.],'B':[0.,.65,1.5,1.9]}
            source['curves']['TD']={'H':[0.,10.,20.,100.],'B':[0.,.05,.15,.5]}
            p=make_protocol(source,CS(),'high_mu_millimeter')
            self.assertEqual(p['maximum_solve_attempts'],2)
            self.assertAlmostEqual(p['cases'][0]['expected_H_hypotheses']['specified_frame'][0],20.)
            self.assertAlmostEqual(p['cases'][1]['expected_H_hypotheses']['specified_frame'][0],1.5/.085)
            self.assertGreater(p['cases'][0]['mu_r'][0],50000.)


if __name__=='__main__':unittest.main()
