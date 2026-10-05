"""Physical coverage, sign convention and ranking must fail conservatively."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch

PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT))
from modules.motor_model_audit import blocks,inspect_model,material_audit,comparison_status,LOSS_POLICY
from modules.motor_workbench import collect_metrics,write_scan_summary,write_json
from tools.motor_scan_worker import configure_physics
from test_workbench import csv_fixture


def fixture(full_loss=False,tensor=False):
    objects=[]
    coordinates=[]
    for i in range(1,49):
        name=f'GOES_V3_{i:02d}'
        objects.append(f"$begin 'GeometryPart'\n$begin 'Attributes'\nName='{name}'\nPartCoordinateSystem={i+1000}\nMaterialValue='\"M6\"'\nSolveInside=true\n$end 'Attributes'\nParentPartID={i+2000}\n$end 'GeometryPart'")
        coordinates.append(f"$begin 'Operation'\nOperationType='CreateObjectCoordinateSystem'\nID={i+1000}\nName='{name}_CS'\n$end 'Operation'")
    for name,obj_id in [('Stator',6),('Rotor_1',735)]:
        objects.append(f"$begin 'GeometryPart'\nName='{name}'\nParentPartID={obj_id}\n$end 'GeometryPart'")
    ids=[6,735]+(list(range(2001,2049)) if full_loss else [])
    perm="property_type='AnisoProperty'\ncomponent1='1'\ncomponent2='2'\ncomponent3='3'" if tensor else "property_type='nonlinear'\n$begin 'BHCoordinates'\nPoints[6: 0, 0, 1, 1, 2, 1.1]\n$end 'BHCoordinates'"
    material=f"$begin 'Materials'\n$begin 'M6'\n$begin 'permeability'\n{perm}\n$end 'permeability'\n$begin 'core_loss_type'\nChoice='Power Ferrite'\n$end 'core_loss_type'\ncore_loss_cm='1'\ncore_loss_x='2'\ncore_loss_y='2'\n$end 'M6'\n$end 'Materials'"
    bound=f"$begin 'GlobalBoundData'\nCoreLossObjectIDs[{len(ids)}: "+', '.join(map(str,ids))+"]\n$end 'GlobalBoundData'"
    moving="$begin 'Moving1'\nObjects("+', '.join(map(str,range(2001,2049)))+")\n$end 'Moving1'"
    variables="VariableProp('MachineRPM', 'UD', '', '3000rpm')\nVariableProp('NumPoles', 'UD', '', '4')\nVariableProp('NumTorqueCycles', 'UD', '', '3')\nVariableProp('NumTorquePointsPerCycle', 'UD', '', '30')"
    return '\n'.join(objects+["$begin 'CoordinateSystems'",*coordinates,"$end 'CoordinateSystems'",material,bound,moving,variables])


class ModelAudit(unittest.TestCase):
    def test_balanced_blocks_and_partial_or_mismatched_project_rejected(self):
        self.assertEqual(len(blocks(fixture(),'GeometryPart')),50)
        with self.assertRaisesRegex(ValueError,'不完整'):
            blocks("$begin 'Materials'",'Materials')
        with self.assertRaisesRegex(ValueError,'结构'):
            blocks("$begin 'A'\n$end 'B'",'A')

    def test_object_coordinate_systems_do_not_enable_directional_law_or_losses(self):
        audit=inspect_model(fixture())
        self.assertTrue(audit['local_CS_verified'])
        self.assertEqual(audit['moving_insert_count'],48)
        self.assertEqual(audit['core_loss']['insert_enabled_count'],0)
        self.assertEqual(audit['core_loss']['enabled_object_names'],['Stator','Rotor_1'])
        self.assertFalse(audit['directional_constitutive_verified'])
        self.assertEqual(audit['materials'][0]['scalar_BH_curve_count'],1)
        self.assertEqual(audit['operating_point']['electrical_frequency_Hz'],100)

    def test_misbound_CS_missing_loss_and_unloaded_library_remain_unverified(self):
        audit=inspect_model(fixture().replace("Name='GOES_V3_01_CS'","Name='wrong_CS'"))
        self.assertFalse(audit['local_CS_verified'])
        missing=material_audit(fixture(),'not_embedded')
        self.assertFalse(missing['embedded'])
        self.assertFalse(missing['loss_definition_present'])

    def test_tensor_declaration_alone_does_not_prove_RD_TD_import(self):
        material=material_audit(fixture(tensor=True),'M6')
        self.assertTrue(material['tensor_declared'])
        self.assertFalse(material['directional_constitutive_verified'])
        self.assertFalse(material['calibrated_RD_TD_import_verified'])

    def test_zero_placeholder_loss_coefficients_are_not_a_loss_definition(self):
        text=fixture().replace("core_loss_cm='1'","core_loss_cm='0'")
        self.assertFalse(material_audit(text,'M6')['loss_definition_present'])

    def test_native_preflight_preserves_original_loss_objects_and_verifies_saved_coverage(self):
        before,after=inspect_model(fixture()),inspect_model(fixture(full_loss=True))
        app=Mock(set_core_losses=Mock(return_value=True))
        case={'quality':{'object_names':[o['name'] for o in before['inserts']]}}
        with tempfile.TemporaryDirectory() as tmp,patch('tools.motor_scan_worker.audit_project',side_effect=[before,after]):
            result=configure_physics(app,Path(tmp)/'motor.aedt',case,{'loss_scope_policy':LOSS_POLICY,'measurement_protocol':'periodic_cycle3_v1'},Path(tmp))
            self.assertEqual(result['core_loss']['insert_enabled_count'],48)
            names=app.set_core_losses.call_args.args[0]
            self.assertEqual(len(names),50)
            self.assertEqual(names[:2],['Stator','Rotor_1'])
            self.assertTrue(json.loads((Path(tmp)/'model_physics_audit.json').read_text())['original_enabled_objects_preserved'])

    def test_preflight_wrong_speed_or_missing_definition_stops_before_loss_mutation(self):
        case={'quality':{'object_names':[]}}
        for kind in ('speed','definition'):
            before=inspect_model(fixture())
            if kind=='speed':
                before['operating_point']['speed_rpm']=1500
            else:
                before['materials'][0]['loss_definition_present']=False
            app=Mock()
            with tempfile.TemporaryDirectory() as tmp,patch('tools.motor_scan_worker.audit_project',return_value=before):
                with self.assertRaises(ValueError):
                    configure_physics(app,Path(tmp)/'motor.aedt',case,{'loss_scope_policy':LOSS_POLICY,'measurement_protocol':'periodic_cycle3_v1'},Path(tmp))
            app.set_core_losses.assert_not_called()

    def test_single_cycle_pass_cannot_rank_and_summary_preserves_numeric_acceptance(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)
            manifest=dict(id='fixture',model_version='fixture',template_sha256='0'*64,cases=[])
            write_json(folder/'manifest.json',manifest)
            result=dict(material='M6',case_id='case_01',accepted=True,metric_protocol='v8_2_cycle1',T_avg_Nm=2,P_Fe_W=1)
            status=comparison_status(result)
            self.assertFalse(status['eligible'])
            self.assertIn('稳态未验证',status['reasons'])
            summary=write_scan_summary(folder,manifest,[result])
            self.assertEqual(summary['accepted'],1)
            self.assertEqual(summary['ranking_eligible'],0)
            self.assertIsNone(summary['best_torque'])
            self.assertTrue(summary['results'][0]['accepted'])

    def test_inconsistent_torque_sign_rejected_before_metrics(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)
            csv_fixture(folder)
            file=folder/'reports/Torque Plots.csv'
            file.write_bytes(file.read_bytes().replace(b'-Moving1.Torque',b'Moving1.Torque'))
            with self.assertRaisesRegex(ValueError,'转矩符号'):
                collect_metrics(folder)

    def test_efficiency_tampering_rejected_when_official_csvs_are_unchanged(self):
        from modules.motor_workbench import MotorWorkbench
        from test_motor_queue import QueueRecovery
        fixture_case=QueueRecovery()
        fixture_case.setUp()
        self.addCleanup(fixture_case.doCleanups)
        manager=fixture_case.manager
        state=manager.prepare(['M6'],measurement='v8_2_cycle1')
        folder=manager.path(state['id'])/'case_01'
        csv_fixture(folder)
        result=collect_metrics(folder)
        result.update(material='M6',case_id='case_01',eta_estimate_pct=99.)
        write_json(folder/'result.json',result)
        fixture_case.state(state,status='completed')
        with self.assertRaisesRegex(ValueError,'eta_estimate_pct'):
            manager.export_summary(state['id'])

    def test_stale_loss_policy_submit_does_not_mutate_prepared_state(self):
        from test_motor_queue import QueueRecovery
        fixture_case=QueueRecovery()
        fixture_case.setUp()
        self.addCleanup(fixture_case.doCleanups)
        manager=fixture_case.manager
        state=manager.prepare(['M6'])
        folder=manager.path(state['id'])
        manifest=json.loads((folder/'manifest.json').read_text())
        manifest.pop('loss_scope_policy')
        write_json(folder/'manifest.json',manifest)
        original=(folder/'state.json').read_bytes()
        with patch('modules.motor_workbench.Path.is_file',return_value=True):
            with self.assertRaisesRegex(ValueError,'旧插片损耗协议'):
                manager.submit(state['id'])
        self.assertEqual((folder/'state.json').read_bytes(),original)


if __name__=='__main__':
    unittest.main()
