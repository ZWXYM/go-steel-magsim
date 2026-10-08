"""Read saved material packages without refitting, predicting or dispatching solves."""
import csv
import hashlib
import io
import json
import re
import zipfile
from pathlib import Path
from urllib.parse import quote

import numpy as np
from modules.calibration_transfer import load_bank
from modules.maxwell_material_transport import load_contract


def sha(data):
    return hashlib.sha256(data).hexdigest()


class MaterialLibrary:
    def __init__(self, calibration):
        self.storage = calibration.storage.resolve()

    def _base(self, artifact):
        if not isinstance(artifact, str) or not re.fullmatch(r'cal_[0-9a-f]{12}', artifact):
            raise ValueError('无效校准记录')
        base = (self.storage / artifact).resolve()
        if not base.is_relative_to(self.storage) or not base.is_dir():
            raise ValueError('校准目录不存在或超出任务存储')
        return base

    @staticmethod
    def _file(base, relative):
        if not isinstance(relative, str) or Path(relative).is_absolute():
            raise ValueError('无效材料文件路径')
        path = (base / relative).resolve()
        if not path.is_relative_to(base) or not path.is_file():
            raise ValueError('材料文件缺失或超出记录目录：' + relative)
        return path

    def _read(self, base, name):
        return json.loads(self._file(base, name).read_text(encoding='utf-8'))

    @staticmethod
    def _grades(bank):
        return sorted({r['grade'] for r in bank.payload.get('training_samples', bank.payload.get('anchors', []))})

    def _keys(self, base, report):
        keys = []
        for kind, field in [('fit', 'known_material_exports'), ('holdout', 'holdout_material_exports')]:
            keys += [kind + ':' + grade for grade in report.get(field, {})]
        keys += ['prediction:' + p.name for p in sorted(base.glob('pred_*')) if p.is_dir()]
        return keys

    def catalog(self):
        records = []
        for folder in sorted(self.storage.glob('cal_*'), reverse=True):
            row = dict(id=folder.name, entries=[], error=None)
            try:
                base = self._base(folder.name)
                report = self._read(base, 'report.json')
                row.update(created_utc=report.get('created_utc'), calibration_version=report.get('calibration_version'),
                           selected_grades=report.get('selected_grades', []))
                for key in self._keys(base, report):
                    entry = dict(key=key, kind=key.split(':')[0], label=key.split(':')[-1], available=False)
                    try:
                        detail, _ = self._detail(folder.name, key)
                        entry.update(available=True, material_name=detail['material_name'],
                                     bank_sha256=detail['effective_bank_sha256'], excluded_grades=detail['excluded_grades'])
                    except (ValueError, KeyError, TypeError, OSError) as error:
                        entry['reason'] = str(error)
                    row['entries'].append(entry)
            except (ValueError, KeyError, TypeError, OSError) as error:
                row['error'] = str(error)
            records.append(row)
        return dict(records=records, new_predictions=0, new_native_solves=0, source_records_changed=False)

    def detail(self, artifact, key):
        try:
            return self._detail(artifact, key)[0]
        except (KeyError, TypeError, OSError) as error:
            raise ValueError('材料记录不可读取：' + str(error)) from error

    def _detail(self, artifact, key):
        if not isinstance(key, str) or not re.fullmatch(r'(fit|holdout):[A-Za-z0-9_-]+|prediction:pred_[0-9a-f]{12}', key):
            raise ValueError('无效材料条目')
        base = self._base(artifact)
        report = self._read(base, 'report.json')
        if key not in self._keys(base, report):
            raise ValueError('该记录没有所选材料')
        parent_path = self._file(base, 'bank.json')
        parent = load_bank(parent_path)
        if report.get('id') != artifact or parent.bank_sha256 != report['bank_sha256'] or report.get('H_axis') != 'physical_A_per_m':
            raise ValueError('父校准库哈希或物理 H 不符')
        kind, name = key.split(':')
        sources = {'source_report.json': self._file(base, 'report.json'), 'parent_bank.json': parent_path}
        excluded = []
        scope = None
        original_hash = None
        if kind == 'prediction':
            out = self._file(base, name + '/prediction.json').parent
            saved = self._read(out, 'prediction.json')
            if saved['id'] != name or saved['calibration_id'] != artifact:
                raise ValueError('预测所属记录不符')
            amat = self._file(out, saved['AMAT_file'])
            bank_path = self._file(out, 'effective_bank.json')
            bank = load_bank(bank_path)
            scope = saved.get('prediction_scope')
            if not isinstance(scope, dict):
                raise ValueError('旧预测未保存完整排除范围，请从原校准记录查看')
            excluded = scope['excluded_grades']
            if (saved.get('source_calibration_sha256') != parent.bank_sha256 or
                    saved['calibration_sha256'] != bank.bank_sha256 or
                    scope['source_calibration_sha256'] != parent.bank_sha256 or
                    scope['effective_calibration_sha256'] != bank.bank_sha256 or
                    sorted(scope['effective_training_grades']) != self._grades(bank) or
                    scope.get('physical_H_unchanged') is not True):
                raise ValueError('预测父库、实际子库或排除范围不符')
            raw = self._file(out, 'input_raw_pair.json')
            if sha(raw.read_bytes()) != saved['source_input_sha256']:
                raise ValueError('预测原生输入哈希不符')
            sources.update({'prediction.json': self._file(out, 'prediction.json'), 'input_raw_pair.json': raw})
            curves = {d: {k: saved[d][k] for k in ('H', 'B', 'B_raw')} for d in ('RD', 'TD')}
            expected_bank = saved['calibration_sha256']
        else:
            field = 'known_material_exports' if kind == 'fit' else 'holdout_material_exports'
            exported = report[field][name]
            amat = self._file(base, exported['path'])
            original_hash = exported['sha256']
            if sha(amat.read_bytes()) != original_hash:
                raise ValueError('材料文件与原导出哈希不符')
            bank_path = parent_path if kind == 'fit' else self._file(base, 'holdout_banks/' + name + '.json')
            bank = parent if kind == 'fit' else load_bank(bank_path)
            excluded = [] if kind == 'fit' else [name]
            expected_bank = parent.bank_sha256 if kind == 'fit' else exported['bank_sha256']
            if kind == 'holdout' and exported.get('excluded_grade') != name:
                raise ValueError('留出材料标记不符')
            curves = {}
            for d in ('RD', 'TD'):
                rows = [r for r in report['comparisons'] if r['grade'] == name and r['direction'] == d]
                if len(rows) != 1:
                    raise ValueError('保存曲线缺失或重复')
                r = rows[0]
                curves[d] = dict(H=r['H'], B=r['corrected_B' if kind == 'fit' else 'holdout_B'],
                                 B_raw=r['raw_B'], B_reference=r['reference_B'])
        trained = self._grades(bank)
        if (bank.bank_sha256 != expected_bank or set(excluded) & set(trained) or
                set(trained) | set(excluded) != set(self._grades(parent))):
            raise ValueError('实际训练库成员或完整材料排除不符')
        metadata_path = self._file(amat.parent, amat.with_suffix('.metadata.json').name)
        metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
        contract = load_contract(amat)
        if metadata['calibration_sha256'] != bank.bank_sha256 or sorted(metadata.get('excluded_grades', [])) != sorted(excluded):
            raise ValueError('材料 metadata 实际库或排除范围不符')
        if scope is not None and metadata.get('prediction_scope') != scope:
            raise ValueError('材料与预测范围不符')
        for d in ('RD', 'TD'):
            weights = metadata.get(d + '_anchor_weights', {})
            if set(weights) & set(excluded) or any(g not in trained and w != 0 for g, w in weights.items()):
                raise ValueError('材料权重包含排除或未知样品')
            c = curves[d]
            h, b = np.asarray(c['H'], dtype=float), np.asarray(c['B'], dtype=float)
            if h.ndim != 1 or len(h) != len(b) or not np.all(np.isfinite(h)) or not np.all(np.isfinite(b)):
                raise ValueError('保存曲线形状或数值无效')
            for field in ('B_raw', 'B_reference'):
                if field in c and (len(c[field]) != len(h) or not np.all(np.isfinite(c[field]))):
                    raise ValueError('原生或参考曲线形状或数值无效')
            points = [(0., 0.)] + [(x, y) for x, y in zip(h, b) if x > 0]
            actual = list(zip(contract['curves'][d]['H'], contract['curves'][d]['B']))
            if len(actual) != len(points) or not np.allclose(actual, points, rtol=1e-10, atol=2e-7):
                raise ValueError('AMAT 与保存的物理 H/修正 B 曲线不符')
        sources.update({'material.amat': amat, 'metadata.json': metadata_path, 'effective_bank.json': bank_path})
        source_rows = {n: dict(sha256=sha(p.read_bytes()), bytes=p.stat().st_size,
                               file=p.relative_to(base).as_posix()) for n, p in sources.items()}
        api = '/api/workbench/material-library/' + artifact + '/' + quote(key, safe='')
        detail = dict(calibration_id=artifact, key=key, kind=kind, material_name=metadata['material_name'],
                      parent_bank_sha256=parent.bank_sha256, effective_bank_sha256=bank.bank_sha256,
                      training_grades=trained, excluded_grades=excluded, prediction_scope=scope,
                      H_axis='physical_A_per_m', H_scale=1., curves=curves,
                      evidence_scope=('same_material_fit' if kind == 'fit' else
                                      'whole_material_exclusion' if excluded else 'all_materials_prediction'),
                      original_export_sha256=original_hash,
                      integrity_scope='saved_export_hash_and_curve' if original_hash else 'saved_curve_and_input_hash',
                      package_verified=True, independently_validated=False,
                      loss_status=metadata['core_loss_status'], motor_ranking_eligible=False,
                      new_predictions=0, new_native_solves=0, source_records_changed=False,
                      files=source_rows, download_urls={n: api + '/files/' + n for n in sources},
                      curves_url=api + '/files/curves.csv', bundle_url=api + '/bundle')
        return detail, sources

    @staticmethod
    def _csv(detail):
        stream = io.StringIO(newline='')
        writer = csv.writer(stream)
        writer.writerow(['direction', 'H_A_per_m', 'B_saved_T', 'B_raw_T', 'B_reference_T'])
        for d, curve in detail['curves'].items():
            for i, h in enumerate(curve['H']):
                writer.writerow([d, h, curve['B'][i], curve['B_raw'][i],
                                 curve.get('B_reference', [''] * len(curve['H']))[i]])
        return stream.getvalue().encode('utf-8-sig')

    def download(self, artifact, key, name):
        detail, paths = self._detail(artifact, key)
        if name == 'curves.csv':
            return io.BytesIO(self._csv(detail))
        if name not in paths:
            raise ValueError('未知材料包文件')
        data = paths[name].read_bytes()
        if sha(data) != detail['files'][name]['sha256']:
            raise ValueError('材料源文件在读取期间变化')
        return io.BytesIO(data)

    def bundle(self, artifact, key):
        detail, paths = self._detail(artifact, key)
        data = {n: p.read_bytes() for n, p in paths.items()}
        if any(sha(blob) != detail['files'][n]['sha256'] for n, blob in data.items()):
            raise ValueError('材料源文件在打包期间变化')
        data['curves.csv'] = self._csv(detail)
        detail['bundle_files'] = {n: dict(sha256=sha(b), bytes=len(b)) for n, b in data.items()}
        data['manifest.json'] = json.dumps(detail, ensure_ascii=False, indent=2).encode('utf-8')
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as z:
            for name, blob in data.items():
                z.writestr(name, blob)
        stream.seek(0)
        return stream
