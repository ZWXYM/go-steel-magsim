import sys
import unittest
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from modules.calibration_domain import assess_parameter_support


def params(x, y):
    return dict(f_Goss=x*.5, theta_0_deg=y*10, halfwidth_deg=2., Si_content=3.)


class JointParameterSupport(unittest.TestCase):
    def test_marginal_box_does_not_imply_joint_support_for_collinear_anchors(self):
        rows=[dict(grade=str(i), params=params(i, i)) for i in (0., 1.)]
        result=assess_parameter_support(rows, params(.2, .8))
        self.assertTrue(result['inside_marginal_parameter_box'])
        self.assertFalse(result['inside_joint_parameter_support'])
        self.assertEqual(result['affine_rank'], 1)
        self.assertAlmostEqual(result['scaled_distance_to_convex_support'], .6/np.sqrt(2))

    def test_convex_triangle_handles_boundary_and_outside_on_same_affine_span(self):
        rows=[dict(grade=str(i), params=params(*point)) for i, point in enumerate(((0, 0), (1, 0), (0, 1)))]
        self.assertTrue(assess_parameter_support(rows, params(.2, .3))['inside_joint_parameter_support'])
        result=assess_parameter_support(rows, params(.8, .8))
        self.assertFalse(result['inside_joint_parameter_support'])
        self.assertAlmostEqual(result['scaled_distance_to_affine_span'], 0.)
        self.assertAlmostEqual(result['scaled_distance_to_convex_support'], .6/np.sqrt(2))

    def test_duplicates_exact_anchor_and_permutation_are_stable(self):
        rows=[dict(grade='A', params=params(0, 0)), dict(grade='B', params=params(1, 1)), dict(grade='A', params=params(0, 0))]
        result=assess_parameter_support(rows, params(0, 0))
        self.assertEqual(result, assess_parameter_support(rows[::-1], params(0, 0)))
        self.assertEqual(result['unique_parameter_count'], 2)
        self.assertEqual(result['matched_anchor_grades'], ['A'])
        self.assertFalse(result['independent_material_accuracy_verified'])

    def test_single_anchor_and_nonfinite_parameters_are_explicit(self):
        rows=[dict(grade='A', params=params(0, 0))]
        self.assertEqual(assess_parameter_support(rows, params(0, 0))['affine_rank'], 0)
        self.assertFalse(assess_parameter_support(rows, params(0, 1))['inside_joint_parameter_support'])
        with self.assertRaises(ValueError):assess_parameter_support(rows, params(float('nan'), 0))

    def test_large_bank_is_not_silently_labelled_inside_joint_support(self):
        rows=[dict(grade=str(i), params=params(i, i)) for i in range(13)]
        result=assess_parameter_support(rows, params(2, 2))
        self.assertFalse(result['joint_support_evaluated'])
        self.assertIsNone(result['inside_joint_parameter_support'])


if __name__ == '__main__':unittest.main()
