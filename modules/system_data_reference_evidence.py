"""Attach verified original-table and loss records without changing their claims."""
import io
import json
import zipfile

from modules.system_data import PACK_ID, SHA256


MAX_REFERENCE_PACKS = 12
REFERENCE_PACKS = {
    'external_bh_validation.zip': ('magnetization_table', 'external_bh_report.json', {'external_bh_validation_v1'}, None),
    'measured_loop_loss.zip': ('ordered_loop', 'loop_loss_report.json', {'measured_loop_loss_v1'}, 'original/cycle.csv'),
    'measured_loop_workbook.zip': ('loop_workbook', 'loop_workbook_report.json',
                                  {'measured_loop_workbook_v1'}, 'original/workbook.xlsx'),
    'frequency_loss_validation.zip': ('frequency_holdout', 'frequency_loss_report.json',
                                      {'frequency_loss_validation_v1', 'frequency_loss_validation_v2',
                                       'frequency_loss_validation_v3_cycles'},
                                      'original/loss_table.csv'),
}


def validate_reference_selection(selected):
    if not isinstance(selected, list) or len(selected) > MAX_REFERENCE_PACKS:
        raise ValueError('每包至多选择 12 份曲线与损耗记录')
    for item in selected:
        if (not isinstance(item, dict) or set(item) != {'id', 'sha256'}
                or not isinstance(item['id'], str) or not PACK_ID.fullmatch(item['id'])
                or not isinstance(item['sha256'], str) or not SHA256.fullmatch(item['sha256'])):
            raise ValueError('曲线与损耗记录需绑定登记编号和完整 SHA-256')
    if len({item['id'] for item in selected}) != len(selected):
        raise ValueError('同一曲线或损耗记录不可重复选择')
    return selected


def collect_reference_package(store, item):
    record, blob = store.read(item['id'])
    if record['sha256'] != item['sha256']:
        raise ValueError('曲线或损耗记录已变化，请刷新后重新选择')
    file_name = record.get('source_filename')
    spec = REFERENCE_PACKS.get(file_name) if isinstance(file_name, str) else None
    if spec is None:
        raise ValueError('只支持已保存的原表核对、回线核算、回线工作簿和多频复核记录')
    kind, report_path, versions, one_original = spec
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            report = json.loads(archive.read(report_path))
            manifest = json.loads(archive.read('manifest.json'))['files']
            if report['version'] not in versions:
                raise ValueError('曲线或损耗记录版本不符')
            source = report['source']
            if not all(isinstance(source.get(key), str) and source[key].strip()
                       for key in ('source_kind', 'source_reference')):
                raise ValueError('曲线或损耗记录缺少来源声明')
            if one_original:
                inputs = {one_original: report['original']}
            else:
                if not report['inputs'] or set(report['inputs']) - {'RD', 'TD'}:
                    raise ValueError('磁化曲线原表方向不符')
                inputs = {f'original/{key}.csv': value for key, value in report['inputs'].items()}
            originals = {}
            for path, meta in inputs.items():
                if (manifest[path] != meta['sha256'] or archive.getinfo(path).file_size != meta['bytes']
                        or (not one_original and meta.get('archive_path') != path)):
                    raise ValueError('原表声明的哈希、路径或字节数与归档文件不同')
                originals[path] = dict(sha256=meta['sha256'], bytes=meta['bytes'])
            derived = {}
            if report['version'] == 'frequency_loss_validation_v3_cycles':
                cycles = report['cycle_series']['cycles']
                if not 3 <= len(cycles) <= MAX_REFERENCE_PACKS or len({row['id'] for row in cycles}) != len(cycles):
                    raise ValueError('多频记录的原回线数量或身份不符')
                for row in cycles:
                    if (not PACK_ID.fullmatch(row['id']) or row['path'] != f'source_cycles/{row["id"]}.zip'
                            or manifest[row['path']] != row['sha256'] or not SHA256.fullmatch(row['original_csv_sha256'])):
                        raise ValueError('多频记录的原回线绑定不符')
                derived = dict(derivation='saved_loop_integrals', source_cycles=[
                    {key: row[key] for key in ('id', 'path', 'sha256', 'original_csv_sha256')} for row in cycles])
    except (KeyError, TypeError, AttributeError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError('曲线或损耗记录缺少可追溯的原表及报告') from error
    return dict(id=record['id'], path=f'reference_evidence/{record["id"]}.zip', kind=kind, source_filename=file_name,
                sha256=record['sha256'], bytes=record['bytes'], report_path=report_path, report_version=report['version'],
                declared_source_kind=source['source_kind'], source_reference=source['source_reference'],
                declared_specimen_id=source.get('specimen_id'), declared_grade=source.get('reported_grade'),
                declared_direction=source.get('direction'), originals=originals,
                joined_to_other_results=False, **derived), blob
