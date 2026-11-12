import unittest

from modules.external_bh_validation import read_measurement_csv
from modules.measured_curve_candidate import build_measured_candidate


class MeasuredCurveCandidateTest(unittest.TestCase):
    def test_descending_source_retains_endpoints_and_only_moves_violating_blocks(self):
        raw = b'H_A_per_m,B_T\n180,1.1\n160,1\n140,.4\n120,.6\n100,.2\n'
        measured = read_measurement_csv(raw, 'TD')
        stats, nodes, rows = build_measured_candidate(measured, max_nodes=3)
        self.assertEqual(stats['H_range_A_per_m'], [100,180])
        self.assertEqual([r['source_row'] for r in rows], [6,5,4,3,2])
        self.assertEqual([r['source_B_T'] for r in rows], [.2,.6,.4,1.,1.1])
        self.assertEqual([r['monotone_B_T'] for r in rows], [.2,.5,.5,1.,1.1])
        self.assertEqual([r['H_A_per_m'] for r in nodes], [100,140,180])
        self.assertEqual(stats['altered_source_points'], 2)
        self.assertAlmostEqual(stats['max_abs_monotone_shift_T'], .1)
        self.assertAlmostEqual(stats['max_abs_candidate_point_error_T'], .25)


if __name__ == '__main__':
    unittest.main()
