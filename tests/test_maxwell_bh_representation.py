import sys
import unittest
from copy import deepcopy
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'modules'))
from modules.maxwell_bh_representation import MU0, diagnostic_curve, make_protocol, evaluate, verify_material
from modules.maxwell_exporter import generate_amat_content


def source():
    h=[0.,1.,2.,3.,4.,5.,6.,7.,8.,9.,10.,15.,20.,25.,30.,50.,60.,80.,100.,200.,500.,1000.,2000.,5000.,20000.,50000.]
    return dict(material_name='Source', curves=dict(RD=dict(H=h,B=[.075*x for x in h]),
        TD=dict(H=h,B=[.005*x for x in h]),ND_mu_r=1000.,conductivity_S_per_m=2e6,
        mass_density_kg_per_m3=7650.,core_loss_equiv_cut_depth='0.23mm'))


class BHRepresentation(unittest.TestCase):
    def test_source_knots_preserved_and_transform_explicit(self):
        original=source(); before=deepcopy(original); protocol=make_protocol(original)
        self.assertEqual(original,before); self.assertEqual(protocol['maximum_solve_attempts'],2)
        self.assertEqual(protocol['GPUs'],0); self.assertFalse(protocol['retry_allowed'])
        curve=protocol['cases'][0]['material']['curves']['RD']
        self.assertEqual(curve['H'],original['curves']['RD']['H'])
        self.assertEqual(curve['B'][12],1.5); self.assertNotEqual(curve['B'][-1],original['curves']['RD']['B'][-1])

    def test_tail_is_vacuum_and_working_segment_has_source_secant(self):
        curve,mu=diagnostic_curve(source()['curves']['RD'])
        slopes=np.diff(curve['B'])/np.diff(curve['H'])
        np.testing.assert_allclose(slopes[:15],MU0*mu,rtol=1e-12)
        np.testing.assert_allclose(slopes[15:],MU0,rtol=1e-10)

    def test_bad_source_knots_and_subvacuum_working_slope_rejected(self):
        curve=source()['curves']['RD']; curve['H'][12]=19.
        with self.assertRaises(ValueError):diagnostic_curve(curve)
        curve=source()['curves']['RD'];curve['B']=[MU0*.5*h for h in curve['H']]
        with self.assertRaises(ValueError):diagnostic_curve(curve)

    def test_wrong_energy_or_unconverged_native_report_vetoes_fields(self):
        case=make_protocol(source())['cases'][0]
        B=np.tile(case['target_B_vector_T'],(9,1));H=np.tile([20.,0.,0.],(9,1))
        convergence=dict(adaptive_converged=True,passes=[dict(energy_J=case['expected_integrated_energy_J'])])
        self.assertTrue(evaluate(case,B,H,convergence)['passed'])
        convergence['passes'][0]['energy_J']*=1.001
        self.assertFalse(evaluate(case,B,H,convergence)['passed'])
        convergence['passes'][0]['energy_J']=case['expected_integrated_energy_J'];convergence['adaptive_converged']=False
        self.assertFalse(evaluate(case,B,H,convergence)['passed'])

    def test_half_B_H_energy_not_used_outside_constant_mu_segment(self):
        case=make_protocol(source())['cases'][0]; B=np.tile([case['linear_working_B_limit_T']*1.001,0,0],(9,1))
        result=evaluate(case,B,np.tile([20.,0.,0.],(9,1)),dict(adaptive_converged=True,passes=[dict(energy_J=case['expected_integrated_energy_J'])]))
        self.assertFalse(result['checks']['analytic_working_segment']);self.assertFalse(result['passed'])

    def test_saved_units_swaps_and_tail_corruption_rejected(self):
        case=make_protocol(source())['cases'][1]; c=case['material']['curves']
        text=generate_amat_content(case['material']['material_name'],c['RD']['H'],c['RD']['B'],c['TD']['H'],c['TD']['B'],
            conductivity=0.,core_loss_override={'kh':0.,'kc':0.,'ke':0.})
        # This fixture represents native double serialization, not AMAT's
        # rounded text export. The new import route sends NAME:Point doubles.
        from modules.motor_model_audit import one
        for direction,component in (('RD','component1'),('TD','component2')):
            block=one(text,component);coordinates=one(block,'BHCoordinates')
            import re
            pairs=np.column_stack([c[direction]['H'],c[direction]['B']]).ravel()
            full='Points['+str(len(pairs))+': '+', '.join(format(v,'.17g') for v in pairs)+']'
            text=text.replace(coordinates,re.sub(r'Points\[\d+:\s*[^\]]+\]',full,coordinates))
        self.assertTrue(verify_material(text,case)['analytic_normal_BH_saved'])
        with self.assertRaises(ValueError):verify_material(text.replace('A_per_meter','kA_per_meter'),case)
        swapped=text.replace("'component1'","'TEMP'").replace("'component2'","'component1'").replace("'TEMP'","'component2'")
        with self.assertRaises(ValueError):verify_material(swapped,case)


if __name__=='__main__':unittest.main()
