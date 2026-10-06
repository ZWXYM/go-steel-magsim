import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from modules.maxwell_native_convergence import read_convergence

TEXT='''Completed : 2
Maximum : 6
Minimum : 2
Target : (0.1, 0.1)
Current : (0.01, 0.02)
Pass|Triangles|Total Energy (J)|Energy Error (%)|Delta Energy (%)|
1|100|0.001|0.02|N/A|
2|130|0.001001|0.01|0.02|
'''

class NativeConvergence(unittest.TestCase):
    def read(self,text):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'official.txt';path.write_text(text)
            return read_convergence(path,.1)

    def test_actual_converged_history_passes(self):
        self.assertTrue(self.read(TEXT)['adaptive_converged'])

    def test_successful_solve_with_excess_energy_error_is_rejected(self):
        text=TEXT.replace('(0.01, 0.02)','(0.01, 50)').replace('|0.01|0.02|','|0.01|50|')
        self.assertFalse(self.read(text)['adaptive_converged'])

    def test_changed_target_missing_pass_and_nonfinite_are_rejected(self):
        for text in (TEXT.replace('(0.1, 0.1)','(1, 1)'),TEXT.replace('2|130|0.001001|0.01|0.02|',''),TEXT.replace('(0.01, 0.02)','(nan, 0.02)')):
            with self.assertRaises(ValueError):
                self.read(text)

    def test_disagreeing_summary_cannot_hide_bad_last_pass(self):
        with self.assertRaises(ValueError):
            self.read(TEXT.replace('|0.01|0.02|','|0.01|1.2|'))

if __name__=='__main__':
    unittest.main()
