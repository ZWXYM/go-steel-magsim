import json
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT/'modules'))
from modules.maxwell_exporter import generate_amat_content
from modules.maxwell_material_transport import compatible_import_copy, directional_curves, load_contract, native_arguments, verify_saved_material


def fixture(directory, legacy=False):
    path = Path(directory)/'TransportFixture.amat'
    content = generate_amat_content('TransportFixture', [0, 10, 100], [0, 1.1, 1.5],
        [0, 10, 100], [0, .1, .7], core_loss_override={'kh':0, 'kc':0, 'ke':0})
    if legacy:
        content = content.replace("set('Electromagnetic')", "'set'('Electromagnetic')")
        content = content.replace("\t'permittivity'='1'", "\t$begin 'ModifierData'\n\t$begin 'ThermalModifierData'\n\t'modifier_data'='no_modifier'\n\t$end 'ThermalModifierData'\n\t$end 'ModifierData'\n\t'permittivity'='1'")
    path.write_text(content, encoding='utf-8')
    path.with_suffix('.metadata.json').write_text(json.dumps(dict(material_name='TransportFixture',
        status='experimental_BH_only', H_axis='physical_A_per_m', H_scale=1.,
        core_loss_status='uncalibrated_zero_placeholders', exported_directions=['RD','TD'],
        ND_status='scalar_prior_1000_not_measured', calibration_sha256='fixture',
        reference_correction_version='fixture')), encoding='utf-8')
    return path


class MaterialTransport(unittest.TestCase):
    def test_new_export_omits_unsupported_empty_modifier_and_quoted_set(self):
        with tempfile.TemporaryDirectory() as tmp:
            text = fixture(tmp).read_text()
            self.assertIn("set('Electromagnetic')", text)
            self.assertNotIn("'set'(", text)
            self.assertNotIn("$begin 'ModifierData'", text)
            self.assertIn("DimUnits('', '')", text)
            self.assertNotIn("'DimUnits'(", text)

    def test_legacy_normalization_keeps_curves_and_source_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = fixture(tmp, legacy=True)
            source = path.read_bytes(), path.with_suffix('.metadata.json').read_bytes()
            converted, old = compatible_import_copy(path, Path(tmp)/'new')
            self.assertEqual(load_contract(converted)['curves'], old['curves'])
            self.assertEqual(source, (path.read_bytes(), path.with_suffix('.metadata.json').read_bytes()))
            self.assertFalse(verify_saved_material(converted.read_text(), old)['directional_field_verified'])
            with self.assertRaisesRegex(ValueError, 'must be new'):
                compatible_import_copy(path, Path(tmp)/'new')

    def test_swapped_components_rejected_even_with_same_material_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = fixture(tmp)
            text = path.read_text().replace("'component1'", "'TEMP'").replace("'component2'", "'component1'").replace("'TEMP'", "'component2'")
            with self.assertRaisesRegex(ValueError, 'changed RD B'):
                verify_saved_material(text, load_contract(path))

    def test_import_copy_has_no_internal_empty_property_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = fixture(tmp, legacy=True)
            converted, _ = compatible_import_copy(original, Path(tmp)/'new')
            self.assertTrue(all(line.strip() for line in converted.read_text().splitlines()))

    def test_native_api_uses_individual_point_arrays_without_empty_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            contract = load_contract(fixture(tmp))
            payload = native_arguments(contract)
            def visit(array):
                yield array
                for value in array:
                    if isinstance(value, list):
                        yield from visit(value)
            arrays = list(visit(payload))
            self.assertFalse(any(a and a[0] in ('NAME:', 'NAME:Points') for a in arrays))
            points = [a[1:] for a in arrays if a and a[0]=='NAME:Point']
            self.assertEqual(points, [[0.,0.],[10.,1.1],[100.,1.5],[0.,0.],[10.,.1],[100.,.7]])

    def test_native_lowercase_normal_keeps_same_physical_curve_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = fixture(tmp)
            saved = path.read_text().replace("'Normal'", "'normal'")
            self.assertTrue(verify_saved_material(saved, load_contract(path))['serialized_transport_verified'])

    def test_native_cut_depth_formatting_keeps_units_but_changed_value_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = fixture(tmp)
            saved = path.read_text().replace('0.00035meter', '0.35mm')
            self.assertTrue(verify_saved_material(saved, load_contract(path))['serialized_transport_verified'])
            with self.assertRaisesRegex(ValueError, 'cut_depth_m'):
                verify_saved_material(saved.replace('0.35mm', '0.36mm'), load_contract(path))

    def test_unit_and_intrinsic_curve_changes_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            text = fixture(tmp).read_text()
            for replaced in (text.replace('A_per_meter', 'kA_per_meter'), text.replace("'Normal'", "'Intrinsic'")):
                with self.assertRaisesRegex(ValueError, 'normal'):
                    directional_curves(replaced, 'TransportFixture')

    def test_native_count_origin_and_nonfinite_values_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            text = fixture(tmp).read_text()
            for changed in (text.replace('Points[6:', 'Points[8:', 1),
                    text.replace('0, 0, 10,', '0, 0.1, 10,', 1),
                    text.replace('10, 1.1', '10, nan', 1)):
                with self.assertRaises(ValueError):
                    directional_curves(changed, 'TransportFixture')

    def test_nontrivial_modifier_is_not_silently_discarded(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = fixture(tmp, legacy=True)
            path.write_text(path.read_text().replace('no_modifier', 'thermal_modifier_data'))
            with self.assertRaisesRegex(ValueError, 'Nontrivial'):
                compatible_import_copy(path, Path(tmp)/'new')

    def test_loss_estimate_or_wrong_nd_prior_cannot_pass_bh_only_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            text = fixture(tmp).read_text()
            with self.assertRaisesRegex(ValueError, 'loss estimates'):
                directional_curves(text.replace("'core_loss_kh'='0.000000e+00'", "'core_loss_kh'='1e-6'"), 'TransportFixture')
            path = fixture(tmp)
            path.write_text(text.replace("'component3'='1000'", "'component3'='2000'"))
            with self.assertRaisesRegex(ValueError, 'ND prior differs'):
                load_contract(path)

    def test_metadata_h_axis_and_exported_directions_are_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = fixture(tmp)
            metadata = json.loads(path.with_suffix('.metadata.json').read_text())
            for field, wrong in (('H_axis','simulation_field'), ('H_scale',2), ('exported_directions',['TD','RD'])):
                changed = dict(metadata, **{field:wrong})
                path.with_suffix('.metadata.json').write_text(json.dumps(changed))
                with self.assertRaisesRegex(ValueError, 'metadata'):
                    load_contract(path)

    def test_tensor_transport_does_not_grant_motor_comparison_eligibility(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = fixture(tmp)
            report = verify_saved_material(path.read_text(), load_contract(path))
            self.assertTrue(report['serialized_transport_verified'])
            self.assertFalse(report['motor_ranking_eligible'])
            self.assertFalse(report['loss_calibration_verified'])


if __name__ == '__main__':
    unittest.main()
