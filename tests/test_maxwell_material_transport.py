import json
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT/'modules'))
from modules.maxwell_exporter import generate_amat_content
from modules.maxwell_material_transport import (compatible_import_copy, directional_curves, load_contract, native_arguments, verify_saved_material,
    equivalent_material_name,equivalent_native_arguments,verify_saved_equivalent_material)


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
    def scalar_fixture(self,source,contract,direction):
        c=contract['curves'];curve=c[direction];values=', '.join(str(x) for pair in zip(curve['H'],curve['B']) for x in pair)
        return source+f"""
$begin '{equivalent_material_name(contract,direction)}'
conductivity='{c['conductivity_S_per_m']}'
mass_density='{c['mass_density_kg_per_m3']}'
core_loss_equiv_cut_depth='{c['core_loss_equiv_cut_depth']}'
core_loss_kh='0'
core_loss_kc='0'
core_loss_ke='0'
core_loss_kdc='0'
$begin 'permeability'
property_type='nonlinear'
BTypeForSingleCurve='Normal'
HUnit='A_per_meter'
BUnit='tesla'
IsTemperatureDependent='false'
$begin 'BHCoordinates'
Points[{2*len(curve['H'])}: {values}]
$end 'BHCoordinates'
$end 'permeability'
$end '{equivalent_material_name(contract,direction)}'
"""

    def test_equivalent_payload_uses_unchanged_source_direction_and_no_tensor(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=fixture(tmp);c=load_contract(path);before=json.dumps(c,sort_keys=True)
            for d in ('RD','TD'):
                args=equivalent_native_arguments(c,d);mu=next(v for v in args if isinstance(v,list) and v[0]=='NAME:permeability')
                self.assertEqual(mu[1:3],['property_type:=','nonlinear'])
                coordinates=next(v for v in mu if isinstance(v,list) and v[0]=='NAME:BHCoordinates')
                self.assertEqual([p[1:] for p in coordinates[2:]],[list(p) for p in zip(c['curves'][d]['H'],c['curves'][d]['B'])])
            self.assertEqual(json.dumps(c,sort_keys=True),before)

    def test_equivalent_saved_material_preserves_original_tensor_and_nodes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=fixture(tmp);c=load_contract(path)
            for d in ('RD','TD'):
                saved=self.scalar_fixture(path.read_text(),c,d);result=verify_saved_equivalent_material(saved,c,d)
                self.assertEqual(result['equivalent_direction'],d);self.assertTrue(result['source_points_unchanged'])
                self.assertEqual(result['curve_points'],3);self.assertFalse(result['directional_field_verified'])

    def test_equivalent_wrong_unit_or_intrinsic_curve_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=fixture(tmp);c=load_contract(path);saved=self.scalar_fixture(path.read_text(),c,'RD')
            for a,b in (("HUnit='A_per_meter'","HUnit='Oe'"),("BUnit='tesla'","BUnit='uGauss'"),("BTypeForSingleCurve='Normal'","BTypeForSingleCurve='Intrinsic'")):
                # Change only the appended scalar; the original source remains intact.
                original=path.read_text();changed=original+saved[len(original):].replace(a,b)
                with self.assertRaises(ValueError):verify_saved_equivalent_material(changed,c,'RD')

    def test_equivalent_changed_node_cannot_pass_as_original_curve(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=fixture(tmp);c=load_contract(path);source=path.read_text();saved=self.scalar_fixture(source,c,'RD')
            changed=source+saved[len(source):].replace('1.1','1.2')
            with self.assertRaisesRegex(ValueError,'原H/B节点'):verify_saved_equivalent_material(changed,c,'RD')

    def test_equivalent_nonzero_loss_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=fixture(tmp);c=load_contract(path);source=path.read_text();saved=self.scalar_fixture(source,c,'RD')
            changed=source+saved[len(source):].replace("core_loss_kh='0'","core_loss_kh='1'")
            with self.assertRaisesRegex(ValueError,'损耗系数'):verify_saved_equivalent_material(changed,c,'RD')

    def test_equivalent_requires_source_tensor_and_explicit_direction(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=fixture(tmp);c=load_contract(path);saved=self.scalar_fixture('',c,'RD')
            with self.assertRaises(ValueError):verify_saved_equivalent_material(saved,c,'RD')
            for direction in ('ND','rd',None):
                with self.assertRaises(ValueError):equivalent_native_arguments(c,direction)

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
