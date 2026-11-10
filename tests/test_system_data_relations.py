import copy,unittest
from modules.system_data_relations import batch_calibration_relation


def item(calibration='cal_aaaaaaaaaaaa',parent='a'*64,effective='a'*64):
    return dict(id='item_01',selection=dict(calibration=calibration,key='fit:SYNTHETIC'),
        source=dict(parent_bank_sha256=parent,effective_bank_sha256=effective,excluded_grades=[]))


class DataRelationTests(unittest.TestCase):
    def setUp(self):self.selected=dict(id='cal_aaaaaaaaaaaa',bank_sha256='a'*64)

    def test_same_record_and_parent_bank_is_explicit_without_claiming_a_new_solve(self):
        value=batch_calibration_relation(dict(items=[item()]),self.selected)
        self.assertEqual(value['relationship'],'same_record_and_parent_bank');self.assertTrue(value['all_effective_banks_match_selected']);self.assertEqual(value['new_native_solves'],0)

    def test_excluded_child_bank_is_separate_even_when_parent_record_matches(self):
        row=item(effective='b'*64);row['source']['excluded_grades']=['SYNTHETIC'];row['selection']['key']='prediction:pred_cccccccccccc'
        value=batch_calibration_relation(dict(items=[row]),self.selected)
        self.assertEqual(value['relationship'],'same_record_and_parent_bank');self.assertFalse(value['all_effective_banks_match_selected'])
        self.assertEqual(value['items'][0]['excluded_grades'],['SYNTHETIC']);self.assertEqual(value['items'][0]['material_key'],row['selection']['key'])

    def test_different_record_or_mixed_records_cannot_look_like_selected_calibration_runs(self):
        for rows in ([item('cal_bbbbbbbbbbbb',parent='b'*64,effective='b'*64)], [item(),item('cal_bbbbbbbbbbbb',parent='b'*64,effective='b'*64)]):
            value=batch_calibration_relation(dict(items=rows),self.selected)
            self.assertEqual(value['relationship'],'different_or_mixed_sources');self.assertIn('不是按所选校准重新求解',value['note'])
            self.assertIn('cal_bbbbbbbbbbbb',value['source_calibration_ids'])

    def test_legacy_missing_source_stays_unknown_without_filling_in_selected_record(self):
        for record in ({},{'items':[]},{'items':[{'id':'old','metrics':{'torque_numerical_acceptance':False}}]},{'items':[item(parent='bad')]}):
            value=batch_calibration_relation(record,self.selected);self.assertEqual(value['relationship'],'unknown');self.assertFalse(value['source_fields_complete']);self.assertIsNone(value['all_effective_banks_match_selected'])

    def test_unselected_calibration_still_lists_batch_sources_and_leaves_inputs_unchanged(self):
        record=dict(items=[item()]);before=copy.deepcopy(record);value=batch_calibration_relation(record)
        self.assertEqual(value['relationship'],'not_selected');self.assertEqual(value['source_calibration_ids'],['cal_aaaaaaaaaaaa']);self.assertEqual(record,before)


if __name__=='__main__':unittest.main()
