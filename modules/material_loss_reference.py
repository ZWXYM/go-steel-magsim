"""Read the existing four-sample catalog/estimate snapshot without fitting or assignment."""
import csv
import hashlib
import io
import json
import math
import zipfile
from pathlib import Path

VERSION = 'four_sample_loss_reference_v1'
GRADES = ('B23R075', 'B27R090', 'B27R095', 'B30P105')


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')


class MaterialLossReference:
    def __init__(self, project):
        self.project = Path(project).resolve()
        self.path = self.project / 'go_steel_data/output/four_sample_loss_reference_v1.json'

    def read(self):
        try:
            if not self.path.resolve().is_relative_to(self.project):
                raise ValueError('损耗参考文件位于程序目录之外')
            raw = self.path.read_bytes()
            data = json.loads(raw)
            if (data['version'] != VERSION or data['efficiency_enabled'] is not False or
                    data['motor_assignment_performed'] is not False or data['new_fits'] != 0 or
                    [r['grade'] for r in data['records']] != list(GRADES)):
                raise ValueError('损耗参考版本、只读身份或四样品牌号不符')
            for row in data['records']:
                if row['reference_kind'] != 'manufacturer_typical' or row['specimen_measurement'] is not False:
                    raise ValueError('厂家典型值不能标为样品实测')
                estimate = row['saved_estimate']
                if estimate['status'] != 'existing_program_estimate' or estimate['not_independent_validation'] is not True:
                    raise ValueError('旧估计的来源身份不符')
                for point in row['points'] + row['alternate_catalog_points']:
                    for key in ('B_T', 'f_Hz', 'P_W_kg'):
                        self._number(point[key], positive=True)
                for key in ('nominal_thickness_mm', 'assumed_density_kg_m3', 'assumed_conductivity_S_m'):
                    self._number(row[key], positive=True)
                for key in ('Kh_fit_mass', 'Kc_classical_mass', 'Ke_fit_mass', 'Kh_fit_volumetric',
                            'Kc_classical_volumetric', 'Ke_fit_volumetric', 'fit_rmse_w_kg', 'fit_max_abs_error_w_kg'):
                    self._number(estimate[key])
                for key, name in [('Kh_fit_mass', 'Kh_fit_volumetric'), ('Kc_classical_mass', 'Kc_classical_volumetric'), ('Ke_fit_mass', 'Ke_fit_volumetric')]:
                    if not math.isclose(estimate[key] * row['assumed_density_kg_m3'], estimate[name], rel_tol=1e-12, abs_tol=1e-12):
                        raise ValueError('损耗质量/体积系数单位换算不符')
                if row['document'] not in data['documents'] or any(p['document'] not in data['documents'] for p in row['alternate_catalog_points']):
                    raise ValueError('损耗参考缺少对应文档')
            # Each response/download is formed from this single saved snapshot.
            data['snapshot_sha256'] = hashlib.sha256(raw).hexdigest()
            return data
        except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
            raise ValueError('损耗参考不可读取：' + str(error)) from error

    @staticmethod
    def _number(value, positive=False):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0 or (positive and value == 0):
            raise ValueError('损耗参考包含无效数值或单位')


def reference_csv(data, kind):
    out = io.StringIO(newline='')
    if kind == 'points':
        fields = ['grade', 'role', 'B_T', 'f_Hz', 'P_W_kg', 'reference_kind', 'specimen_measurement',
                  'document', 'document_sha256', 'pdf_page', 'printed_page', 'snapshot_sha256']
        writer = csv.DictWriter(out, fieldnames=fields); writer.writeheader()
        for row in data['records']:
            for role, points in [('primary_saved_fit_input', row['points']), ('alternate_catalog_not_fitted', row['alternate_catalog_points'])]:
                for point in points:
                    name = point.get('document', row['document']); doc = data['documents'][name]
                    writer.writerow(dict(grade=row['grade'], role=role, **{k: point[k] for k in ('B_T', 'f_Hz', 'P_W_kg')},
                        reference_kind=row['reference_kind'], specimen_measurement=False, document=doc['filename'],
                        document_sha256=doc['sha256'], pdf_page=doc['pdf_page'], printed_page=doc['printed_page'], snapshot_sha256=data['snapshot_sha256']))
    elif kind == 'estimates':
        fields = ['grade', 'nominal_thickness_mm', 'assumed_density_kg_m3', 'assumed_conductivity_S_m',
                  'Kh_fit_mass', 'Kc_classical_mass', 'Ke_fit_mass', 'Kh_fit_volumetric', 'Kc_classical_volumetric', 'Ke_fit_volumetric',
                  'fit_rmse_w_kg', 'fit_max_abs_error_w_kg', 'status', 'warning', 'method',
                  'formula', 'Kh_mass_unit', 'Kc_mass_unit', 'Ke_mass_unit',
                  'Kh_volumetric_unit', 'Kc_volumetric_unit', 'Ke_volumetric_unit', 'snapshot_sha256']
        writer = csv.DictWriter(out, fieldnames=fields); writer.writeheader()
        for row in data['records']:
            value = {k: row[k] if k in row else row['saved_estimate'][k] for k in fields if k != 'snapshot_sha256' and k != 'formula' and not k.endswith('_unit')}
            value['formula'] = data['formula']
            for symbol in ('Kh', 'Kc', 'Ke'):
                value[symbol + '_mass_unit'] = data['mass_units'][symbol]
                value[symbol + '_volumetric_unit'] = data['volumetric_units'][symbol]
            writer.writerow(dict(value, snapshot_sha256=data['snapshot_sha256']))
    else:
        raise ValueError('只支持参考点或旧估计CSV')
    return out.getvalue().encode('utf-8-sig')


def reference_bundle(data):
    files = {'reference.json': json_bytes(data), 'reference_points.csv': reference_csv(data, 'points'),
             'saved_estimates.csv': reference_csv(data, 'estimates'),
             'README.txt': ('厂家典型值与旧程序估计；不是本批样品独立实测。primary为原估计输入，alternate为另版手册值，未合并或重拟合。\n'
                '质量系数公式/质量与体积单位/假定密度与电导率见reference.json。体积系数=质量系数×假定密度。\n'
                '未写入Maxwell材料或更改已有AMAT，未开放校准材料损耗、效率及最终排名。原PDF保持私有，包内只含对应事实点及文档哈希/页码。\n').encode('utf8')}
    files['manifest.json'] = json_bytes(dict(version=VERSION, snapshot_sha256=data['snapshot_sha256'],
        files={name: hashlib.sha256(raw).hexdigest() for name, raw in files.items()}))
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, raw in files.items():
            archive.writestr(name, raw)
    out.seek(0)
    return out
