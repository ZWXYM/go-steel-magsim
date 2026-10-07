import sys
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from modules.maxwell_native_readiness import require_configured_license_connection


class NativeReadiness(unittest.TestCase):
    def test_unreachable_and_unknown_service_block_before_native_dispatch(self):
        for record in ({'available':False},{},{'available':'true'}):
            with patch('modules.maxwell_native_readiness.license_status',return_value=record):
                with self.assertRaisesRegex(ValueError,'no native session dispatched'):
                    require_configured_license_connection()

    def test_connected_port_never_certifies_checkout_or_design_access(self):
        with patch('modules.maxwell_native_readiness.license_status',return_value={'available':True}):
            result=require_configured_license_connection()
        self.assertFalse(result['native_license_checkout_verified'])
        self.assertFalse(result['native_design_access_verified'])


if __name__=='__main__':unittest.main()
