"""Explicitly bound CSV for byte-identical saved reports, in a separate dataset.

No prefix search, source-file renaming, final-archive changes or solver calls.
"""
import copy
import json
import math
import re
import tempfile
from pathlib import Path

from modules.material_calibration import file_hash
from modules.motor_workbench import collect_metrics
from modules.motor_model_audit import audit_project, comparison_status

DATASET_ID = 'v8_2_bound_archive'
VERSION = 'V8_2_bound_report_CSV_v1'
TOLERANCES = dict(T_avg_Nm=.00006, K_T_ripple_pct=.006, P_Fe_W=.0006, P_Cu_W=.0006)


class BoundMotorArchive:
    def __init__(self, root, motor, contract_path=None):
        self.root, self.motor = Path(root).resolve(), motor
        self.contract_path = Path(contract_path or Path(__file__).resolve().parents[1]/'go_steel_data/output/motor_archive_bindings_v1.json')

    def contract(self):
        data = json.loads(self.contract_path.read_text(encoding='utf8'))
        if (not isinstance(data,dict) or data.get('schema') != 'explicit_identical_report_archive_bindings_v1'
                or data.get('version') != VERSION or not isinstance(data.get('aliases'), list)):
            raise ValueError('归档来源绑定合同不符')
        seen = set()
        for alias in data['aliases']:
            if not isinstance(alias,dict):raise ValueError('绑定条目不是对象')
            case = alias.get('case')
            if (not isinstance(case,str) or not re.fullmatch(r'[a-z0-9_]{1,100}',case) or case in seen
                    or alias.get('source_prefix') != 'clean_resolved2'
                    or alias.get('final_report') != 'final_resolved_official_report.json'
                    or alias.get('source_report') != 'clean_resolved2_official_report.json'
                    or alias.get('metric_tolerances') != TOLERANCES
                    or not isinstance(alias.get('project_name'),str)
                    or not re.fullmatch(r'[A-Za-z0-9_-]+\.aedt',alias['project_name'])):
                raise ValueError('归档来源绑定的算例/前缀/固定容差不符')
            seen.add(case)
            names={'final_resolved_official_report.json','clean_resolved2_official_report.json',
                'clean_resolved2_reports/Torque Plots.csv','clean_resolved2_reports/Drive Current Plots.csv',
                *('clean_resolved2_maxwell_reports/'+name+'.csv' for name in ('CoreLoss','SolidLoss','StrandedLoss','input_voltage'))}
            if set(alias.get('files',{})) != names:
                raise ValueError('绑定合同没有完整的指定原报告/CSV')
            for item in alias['files'].values():
                if (not isinstance(item,dict) or not isinstance(item.get('sha256'),str)
                        or not re.fullmatch(r'[a-f0-9]{64}',item['sha256']) or type(item.get('bytes')) is not int or item['bytes']<0):
                    raise ValueError('绑定来源哈希不符')
        return data

    def available(self):
        if not self.contract_path.is_file():return False
        try:
            return any((self.root/'motor/v8_2/cases'/row['case']).is_dir() for row in self.contract()['aliases'])
        except (OSError,ValueError,KeyError,TypeError):
            # Keep the optional dataset visible with its own read error; do not
            # prevent the unchanged original datasets from being listed.
            return any((self.root/'motor/v8_2/cases').glob('*/final_resolved_official_report.json'))

    def _sources(self, alias):
        case=(self.root/'motor/v8_2/cases'/alias['case']).resolve()
        base=(self.root/'motor/v8_2/cases').resolve()
        if case.parent!=base:
            raise ValueError('绑定算例不属于归档目录')
        paths={name:(case/name).resolve() for name in alias['files']}
        for name,path in paths.items():
            expected=alias['files'][name]
            if not path.is_relative_to(case) or not path.is_file() or path.stat().st_size!=expected['bytes'] or file_hash(path)!=expected['sha256']:
                raise ValueError('明确绑定的原文件已改变或缺失：'+name)
        final=paths[alias['final_report']].read_bytes();source=paths[alias['source_report']].read_bytes()
        if final!=source:
            raise ValueError('绑定的两份报告不是相同字节，未借用其他前缀')
        report=json.loads(source)
        if (Path(report['project']).resolve()!=(case/alias['project_name']).resolve()
                or report.get('ok') is not True or report.get('solve',{}).get('ok') is not True):
            raise ValueError('绑定报告不属于该工程或未记录求解完成')
        return paths,report

    def records(self):
        contract_sha256=file_hash(self.contract_path)
        contract=self.contract();aliases={row['case']:row for row in contract['aliases']}
        rows=copy.deepcopy(self.motor.archived_results())
        for row in rows:
            row['collection_version']=VERSION
            row['actual_csv_prefix']='final_resolved'
            if row['case'] not in aliases:
                continue
            alias=aliases[row['case']]
            row.update(csv_verified=False,metric_source='bound_source_unverified',actual_csv_prefix=alias['source_prefix'])
            row.pop('metrics',None)
            try:
                sources,report=self._sources(alias)
                # The existing CSV parser and original numerical criteria are reused.
                with tempfile.TemporaryDirectory(prefix='bound motor CSV ') as temporary:
                    folder=Path(temporary)
                    for name,path in sources.items():
                        if not name.endswith('.csv'):continue
                        target=folder/('reports' if name.startswith('clean_resolved2_reports/') else 'maxwell_reports')/path.name
                        target.parent.mkdir(exist_ok=True);target.write_bytes(path.read_bytes())
                    metrics=collect_metrics(folder)
                expected=report['thesis_metrics']
                for key,tolerance in TOLERANCES.items():
                    if (not isinstance(expected[key],(int,float)) or not math.isfinite(expected[key])
                            or not isinstance(metrics[key],(int,float)) or not math.isfinite(metrics[key])
                            or abs(metrics[key]-expected[key])>tolerance):
                        raise ValueError('绑定CSV与同报告指标不符：'+key)
                # Verify source bytes again after parsing. No result is written back.
                self._sources(alias)
                if file_hash(self.contract_path)!=contract_sha256:
                    raise ValueError('绑定合同在读取期间变化')
                project=self.root/'motor/v8_2/cases'/row['case']/alias['project_name']
                if project.is_file():metrics['model_physics_audit']=audit_project(project)
                metrics['comparison_eligibility']=comparison_status(metrics)
                sources['_binding_contract.json']=self.contract_path
                row.update(csv_verified=True,metrics=metrics,display_metrics=metrics,metric_source='official_CSV_verified',reason=None,
                    files={name:dict(sha256=file_hash(path),bytes=path.stat().st_size) for name,path in sources.items()},
                    report_binding=dict(version=VERSION,case=row['case'],actual_source_version='V8_2/clean_resolved2',
                        final_report_sha256=alias['files'][alias['final_report']]['sha256'],
                        source_report_sha256=alias['files'][alias['source_report']]['sha256'],
                        reports_byte_identical=True,contract_sha256=contract_sha256,
                        original_final_dataset_unchanged=True,new_native_solves=0,
                        scope='Same saved report identity and CSV arithmetic; not new physical or steady-state certification'))
            except (OSError,ValueError,KeyError,TypeError) as error:
                row['reason']='明确来源绑定未通过：'+str(error)
        return rows

    def source_file(self, case_id, name):
        aliases={row['case']:row for row in self.contract()['aliases']}
        if case_id not in aliases:
            return self.motor.archive_file(case_id,name)
        paths,_=self._sources(aliases[case_id])
        paths['_binding_contract.json']=self.contract_path
        if name not in paths:
            raise ValueError('文件不属于明确绑定的原报告与CSV')
        return paths[name]
