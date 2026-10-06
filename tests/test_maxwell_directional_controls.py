import sys
import tempfile
import unittest
from pathlib import Path
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from modules.maxwell_directional_controls import make_protocol, transformed_contract, read_vector_field, evaluate_case, evaluate_suite


def source():
    return dict(material_name='Fixture',curves=dict(
        RD=dict(H=[0.,10.,20.,50.],B=[0.,.5,1.5,1.9]),
        TD=dict(H=[0.,100.,300.,600.],B=[0.,.5,1.4,1.7])))


class DirectionalControls(unittest.TestCase):
    def test_transforms_keep_source_and_isotropic_two_components(self):
        original = source()
        swapped = transformed_contract(original,'swapped')
        self.assertEqual(swapped['curves']['RD'],original['curves']['TD'])
        isotropic = transformed_contract(original,'isotropic_rd')
        self.assertEqual(isotropic['curves']['RD'],isotropic['curves']['TD'])
        self.assertNotEqual(original['curves']['RD'],original['curves']['TD'])
        self.assertFalse('diagnostic_transform' in original)

    def results(self,protocol):
        results=[]
        for case in protocol['cases']:
            B,H=np.zeros((9,3)),np.zeros((9,3))
            axis=0 if case['global_B_axis']=='x' else 1
            B[:,axis]=protocol['target_B_T']
            H[:,axis]=400 if case['expected_source_direction']=='TD' else 20
            result=evaluate_case(protocol,case,B,H)
            result['native_convergence']={'adaptive_converged':True}
            results.append(result)
        return results

    def test_full_rotation_swap_isotropic_and_global_axis_matrix_passes(self):
        protocol=make_protocol(source())
        report=evaluate_suite(protocol,self.results(protocol))
        self.assertTrue(report['axis_aligned_field_verified'])
        self.assertFalse(report['motor_geometry_direction_verified'])
        self.assertFalse(report['motor_ranking_eligible'])

    def test_ignored_CS_rotation_fails_frozen_source_knot_and_equivalence(self):
        protocol=make_protocol(source())
        results=self.results(protocol)
        B,H=np.zeros((9,3)),np.zeros((9,3))
        B[:,1]=1.5
        H[:,1]=400
        results[1]=evaluate_case(protocol,protocol['cases'][1],B,H)
        self.assertFalse(evaluate_suite(protocol,results)['passed'])

    def test_transverse_field_and_missing_sample_fail(self):
        protocol=make_protocol(source())
        B,H=np.zeros((9,3)),np.zeros((9,3))
        B[:,1]=1.5;H[:,1]=400;B[:,0]=.01
        self.assertFalse(evaluate_case(protocol,protocol['cases'][0],B,H)['passed'])
        with self.assertRaises(ValueError):
            evaluate_case(protocol,protocol['cases'][0],B[:8],H[:8])

    def test_field_file_preserves_coordinates_count_and_finiteness(self):
        protocol=make_protocol(source())
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'B.fld'
            text='Unit=Meter\n'+'\n'.join(' '.join(map(str,p+[0,1.5,0])) for p in protocol['sample_points_m'])
            path.write_text(text)
            self.assertEqual(read_vector_field(path,protocol['sample_points_m']).shape,(9,3))
            path.write_text(text.replace('1.5','nan',1))
            with self.assertRaises(ValueError):
                read_vector_field(path,protocol['sample_points_m'])

    def test_missing_reordered_cases_cannot_pass(self):
        protocol=make_protocol(source());results=self.results(protocol)
        for bad in (results[:-1],results[::-1],results+[results[0]]):
            with self.assertRaises(ValueError):
                evaluate_suite(protocol,bad)

    def test_missing_or_failed_native_convergence_never_certifies_field(self):
        protocol=make_protocol(source());results=self.results(protocol)
        results[0].pop('native_convergence')
        self.assertFalse(evaluate_suite(protocol,results)['passed'])
        results[0]['native_convergence']={'adaptive_converged':False}
        self.assertFalse(evaluate_suite(protocol,results)['passed'])

    def test_linear_sanity_is_two_controls_and_never_measured_calibration_proof(self):
        protocol=make_protocol(source(),linear_sanity=True)
        self.assertEqual(len(protocol['cases']),2)
        results=[]
        for case in protocol['cases']:
            B,H=np.zeros((9,3)),np.zeros((9,3))
            B[:,1]=protocol['target_B_T']
            H[:,1]=200 if case['expected_source_direction']=='TD' else 20
            result=evaluate_case(protocol,case,B,H)
            result['native_convergence']={'adaptive_converged':True}
            results.append(result)
        self.assertTrue(evaluate_suite(protocol,results)['passed'])
        self.assertFalse(evaluate_suite(protocol,results)['calibrated_material_field_verified'])


if __name__=='__main__':
    unittest.main()
