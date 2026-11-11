import tempfile
import unittest
from pathlib import Path

from modules.catalog_motor_loss import OBJECT_REPORT, REPORT, collect_catalog_losses, loss_contract
from modules.material_loss_reference import MaterialLossReference


class CatalogMotorLossTests(unittest.TestCase):
    def test_same_grade_catalog_binding_allows_a_saved_zero_component(self):
        project = Path(__file__).resolve().parents[1]
        raw, _ = MaterialLossReference(project).snapshot()
        grade = 'B30P105'
        metadata = dict(native_quality={'material_id': grade}, prediction_mode='known_anchor_calibration',
                        excluded_grades=[], RD_anchor_weights={grade: 1}, TD_anchor_weights={grade: 1}, thickness_mm=.30)
        material = dict(curves=dict(mass_density_kg_per_m3=7650, conductivity_S_per_m=2e6,
                                    core_loss_equiv_cut_depth_m=.00030))
        frozen = loss_contract(raw, metadata, material)
        self.assertEqual(frozen['coefficients']['Ke'], 0)
        self.assertGreater(frozen['coefficients']['Kh'], 0)
        self.assertTrue(frozen['frequency_extrapolated'])
        with self.assertRaisesRegex(ValueError, '同牌号'):
            loss_contract(raw, dict(metadata, native_quality={'material_id':'B27R095'}), material)
        with self.assertRaisesRegex(ValueError, '片厚'):
            loss_contract(raw, dict(metadata, thickness_mm=.27), material)

    def test_official_loss_units_closure_and_time_grid(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            (folder/REPORT).parent.mkdir(parents=True)
            (folder/REPORT).write_text('Time [ns],CoreLoss [mW],StrandedLoss [W],SolidLoss [kW]\n'
                                       '0,0,2,0\n5000000,4000,2,0\n10000000,4000,2,0\n', encoding='utf8')
            (folder/OBJECT_REPORT).write_text('Time [ms],CoreLoss(Insert) [W],CoreLoss(Stator) [W],CoreLoss(Rotor_1) [W]\n'
                                              '0,0,0,0\n5,1,2,1\n10,2,1,1\n', encoding='utf8')
            scope = dict(core_loss_objects=['Stator','Rotor_1','Insert'], inserts=['Insert'])
            status = dict(status='exported')
            record = collect_catalog_losses(folder,[0.,5.,10.],scope,status)
            self.assertEqual(record['time_weighted_mean_W'], dict(CoreLoss=3.,StrandedLoss=2.,SolidLoss=0.))
            self.assertEqual(record['core_loss_breakdown']['insert_mean_W'], 1.)
            self.assertTrue(record['core_loss_breakdown']['closure_passed'])
            self.assertFalse(record['efficiency_enabled'])
            self.assertEqual(set(record['csv_hashes']), {REPORT, OBJECT_REPORT})
            with self.assertRaisesRegex(ValueError, '时间网格'):
                collect_catalog_losses(folder,[0.,5.,9.],scope,status)


if __name__ == '__main__':
    unittest.main()
