"""Executable resolution must work without the author's Windows directory."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.run_calibration_pilot import resolve_mumax


class RuntimeResolution(unittest.TestCase):
    def test_explicit_path_has_priority(self):
        with tempfile.TemporaryDirectory() as folder:
            binary = Path(folder) / 'mumax3'
            binary.write_bytes(b'fixture, not executed')
            with patch.dict(os.environ, {'MUMAX3_EXE': 'missing'}):
                self.assertEqual(resolve_mumax(binary), binary.resolve())

    def test_environment_path(self):
        with tempfile.TemporaryDirectory() as folder:
            binary = Path(folder) / 'mumax3'
            binary.write_bytes(b'fixture, not executed')
            with patch.dict(os.environ, {'MUMAX3_EXE': str(binary)}):
                self.assertEqual(resolve_mumax(), binary.resolve())

    def test_invalid_explicit_path_fails_without_fallback(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(FileNotFoundError):
                resolve_mumax(Path(folder) / 'missing')


if __name__ == '__main__':
    unittest.main(verbosity=2)
