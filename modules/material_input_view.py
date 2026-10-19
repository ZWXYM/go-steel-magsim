"""Read-only names and input parameters over verified, unchanged material packages."""
import copy
import hashlib
import json
import math

PARAMETERS = ('f_Goss', 'theta_0_deg', 'halfwidth_deg', 'Si_content')


class MaterialInputView:
    def __init__(self, library):
        self.library = library

    @staticmethod
    def _read(detail, sources, name):
        data = sources[name].read_bytes()
        if hashlib.sha256(data).hexdigest() != detail['files'][name]['sha256']:
            raise ValueError('材料输入在读取期间变化，请刷新')
        return json.loads(data)

    def detail(self, artifact, key):
        # The library verifies bank, package, raw input and curve identity first.
        detail, sources = self.library._detail(artifact, key)
        detail = copy.deepcopy(detail)
        if detail['kind'] == 'prediction':
            raw = self._read(detail, sources, 'input_raw_pair.json')
            prediction = self._read(detail, sources, 'prediction.json')
            material_id, params = raw['material_id'], raw['params']
            if not isinstance(material_id, str) or not material_id.strip():
                raise ValueError('保存的输入材料代号无效')
            if prediction.get('material_id') != material_id or prediction.get('params') != params:
                raise ValueError('原生输入与保存预测的材料代号或参数不符')
            label = material_id + ' · ' + key.split(':', 1)[1]
            parameter_source = '本次原生输入保存的建模参数'
        else:
            material_id = key.split(':', 1)[1]
            bank = self._read(detail, sources, 'parent_bank.json')
            rows = bank.get('training_samples', bank.get('anchors', []))
            matches = [row['params'] for row in rows if row.get('grade') == material_id]
            if not matches or any(p != matches[0] for p in matches):
                raise ValueError('父库保存的材料参数缺失或不一致')
            params, label = matches[0], material_id
            parameter_source = '父校准库保存的建模参数'
        if not isinstance(material_id, str) or not material_id.strip():
            raise ValueError('保存的输入材料代号无效')
        if not isinstance(params, dict) or any(
                isinstance(params.get(k), bool) or not isinstance(params.get(k), (int, float))
                or not math.isfinite(params[k]) for k in PARAMETERS):
            raise ValueError('保存的输入参数缺失或不是有限数值')
        detail.update(display_name=label, source_material_id=material_id,
                      input_params={k: params[k] for k in PARAMETERS},
                      input_parameter_source=parameter_source,
                      input_identity_scope='saved_model_parameters_not_measurement_certification')
        return detail

    def catalog(self):
        catalog = self.library.catalog()
        for record in catalog['records']:
            for entry in record['entries']:
                if not entry['available']:
                    continue
                try:
                    detail = self.detail(record['id'], entry['key'])
                    entry.update(label=detail['display_name'], source_material_id=detail['source_material_id'],
                                 input_params=detail['input_params'])
                except (ValueError, KeyError, TypeError, OSError) as error:
                    entry.update(available=False, reason='材料输入身份不可核对：' + str(error))
        return catalog
