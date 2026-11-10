"""Describe saved calibration identities; never infer a re-solve or change data."""
import re


def batch_calibration_relation(batch,selected=None):
    def valid(value,pattern):return value if isinstance(value,str) and re.fullmatch(pattern,value) else None
    selected=selected or {};selected_id=valid(selected.get('id'),r'cal_[a-f0-9]{12}');selected_bank=valid(selected.get('bank_sha256'),r'[a-f0-9]{64}')
    items=batch.get('items') if isinstance(batch,dict) else None;rows=[]
    for row in items if isinstance(items,list) else []:
        row=row if isinstance(row,dict) else {};selection=row.get('selection') or {};source=row.get('source') or {}
        if not isinstance(selection,dict):selection={}
        if not isinstance(source,dict):source={}
        calibration=valid(selection.get('calibration'),r'cal_[a-f0-9]{12}')
        parent=valid(source.get('parent_bank_sha256'),r'[a-f0-9]{64}');effective=valid(source.get('effective_bank_sha256'),r'[a-f0-9]{64}')
        known=bool(calibration and parent and effective)
        rows.append(dict(item=row.get('id'),calibration_id=calibration,material_key=selection.get('key'),
            parent_bank_sha256=parent,effective_bank_sha256=effective,excluded_grades=source.get('excluded_grades'),source_known=known,
            selected_record_matches=calibration==selected_id if calibration and selected_id else None,
            selected_parent_bank_matches=parent==selected_bank if parent and selected_bank else None,
            selected_effective_bank_matches=effective==selected_bank if effective and selected_bank else None))
    complete=bool(rows) and all(r['source_known'] for r in rows)
    if not selected_id:relationship='not_selected';note='未另选校准记录；批量仍保留其原材料及校准来源。'
    elif not complete or not selected_bank:relationship='unknown';note='批量来源字段不全，不能推断其与所选校准相同；原文件继续保留。'
    elif all(r['selected_record_matches'] and r['selected_parent_bank_matches'] for r in rows):
        relationship='same_record_and_parent_bank';note='批量来自所选校准记录及父库；每条有效子库、排除范围和实际材料仍分别保留。'
    else:relationship='different_or_mixed_sources';note='所选校准与电机批量来自不同或混合记录，分别归档；本批量不是按所选校准重新求解的结果。'
    return dict(schema='saved_calibration_batch_relation_v1',relationship=relationship,selected_calibration_id=selected_id,
        selected_bank_sha256=selected_bank,source_fields_complete=complete,
        source_calibration_ids=sorted({r['calibration_id'] for r in rows if r['calibration_id']}),
        all_effective_banks_match_selected=all(r['selected_effective_bank_matches'] is True for r in rows) if complete and selected_bank else None,
        items=rows,note=note,new_native_solves=0)
