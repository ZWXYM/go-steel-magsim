"""Collect an explicitly chosen saved study without re-running its scorer."""
from modules.motor_constraint_studies import MotorConstraintStudies
from modules.system_data import json_bytes, sha256


class SavedConstraintExports:
    def __init__(self, optimization, storage):
        self.studies = MotorConstraintStudies(optimization, storage)

    def read(self, artifact):
        report, files = self.studies.content(artifact)
        binding = sha256(json_bytes({name: sha256(blob) for name, blob in files.items()}))
        return report, files, binding

    def catalog(self):
        result = []
        for record in self.studies.records():
            try:
                report, _, binding = self.read(record['id'])
                result.append(dict(record, source_binding_sha256=binding))
            except (OSError, ValueError, KeyError, TypeError) as error:
                result.append(dict(id=record['id'], readable=False, error=str(error)))
        return result

    def collect(self, selection):
        if not isinstance(selection, dict) or set(selection) != {'id', 'source_binding_sha256'}:
            raise ValueError('请明确选择已有约束研究及其来源版本')
        report, files, binding = self.read(selection['id'])
        if binding != selection['source_binding_sha256']:
            raise ValueError('所选约束研究文件已变化，请刷新后重新选择')
        _, again, after = self.read(selection['id'])
        if binding != after or files != again:
            raise ValueError('约束研究在打包期间变化')
        index = {key: report[key] for key in ('id', 'dataset', 'model_version', 'measurement_protocol',
            'reference', 'source_hashes', 'configuration_count', 'source_case_count',
            'configurations_with_feasible_case', 'original_default_candidate_count')}
        index.update(source_binding_sha256=binding, new_analyses=0, new_native_solves=0,
            new_independent_materials=0, final_physical_optimization_certified=False)
        return index, files
