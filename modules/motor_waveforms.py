"""Read and compare official V8_2 waveforms without running or changing a solve."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import numpy as np

from modules.material_calibration import file_hash
from modules.motor_workbench import collect_metrics, numeric_csv, unit

VERSION = 'official_motor_waveforms_v1'
LEGACY_PROTOCOL = 'v8_2_torque_all_core_drop_first_copper_all_v1'
FILES = {'torque': 'reports/Torque Plots.csv',
         'core_loss': 'maxwell_reports/CoreLoss.csv',
         'copper_loss': 'maxwell_reports/StrandedLoss.csv',
         'solid_loss': 'maxwell_reports/SolidLoss.csv'}


class MotorWaveforms:
    def __init__(self, motor):
        self.motor = motor

    def catalog(self):
        datasets = []
        archived = self.motor.archived_results()
        if archived:
            datasets.append(dict(id='v8_2_archive', label='V8_2 final 原官方结果',
                version='V8_2/final_resolved', measurement_protocol='v8_2_cycle1',
                cases=[dict(id=r['case'], material=r['case'], available=r['csv_verified'],
                            accepted=(r.get('metrics') or {}).get('accepted'),
                            reason=r.get('reason'), source='official_CSV' if r['csv_verified'] else 'saved_summary_only')
                       for r in archived]))
        errors = []
        for job in self.motor.jobs():
            if job['status'] not in ('completed', 'completed_with_failures'):
                continue
            try:
                folder = self.motor.path(job['id'])
                manifest = json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
                cases = []
                for c in manifest['cases']:
                    if not re.fullmatch(r'case_\d{2}', c['id']):
                        raise ValueError('无效算例 ID')
                    path = folder/c['id']/'result.json'
                    row = dict(id=c['id'], material=c['material'], available=False, accepted=None)
                    if path.is_file():
                        result = json.loads(path.read_text(encoding='utf-8'))
                        row.update(available=True, accepted=result.get('accepted'), source='official_CSV')
                    else:
                        row['reason'] = '该案未生成完整官方结果；失败现场保留'
                    cases.append(row)
                datasets.append(dict(id=job['id'], label=job['id'], version=manifest['model_version'],
                    measurement_protocol=manifest.get('measurement_protocol', 'v8_2_cycle1'), cases=cases))
            except (OSError, ValueError, KeyError, TypeError) as error:
                errors.append(dict(dataset=job['id'], error=str(error)))
        return dict(version=VERSION, datasets=datasets, errors=errors, new_native_solves=0)

    def _paths(self, dataset, case):
        if not isinstance(case, str):
            raise ValueError('无效算例 ID')
        if dataset == 'v8_2_archive':
            source = self.motor.archive_sources(case)
            paths = {}
            for key, relative in FILES.items():
                name = 'final_resolved_'+relative
                if key == 'solid_loss' and name not in source:
                    continue
                paths[key] = self.motor.archive_file(case, name)
            rows = {r['case']: r for r in self.motor.archived_results()}
            record = rows[case]
            if not record['csv_verified']:
                raise ValueError(record.get('reason') or '该 final 版本没有完整官方 CSV')
            links = {k: '/api/workbench/motor/archive/'+quote(case, safe='')+'/files/'+
                     quote(p.relative_to(p.parents[1]).as_posix(), safe='/') for k, p in paths.items()}
            return paths, dict(dataset=dataset, case=case, material=case,
                model_version='V8_2/final_resolved', measurement_protocol='v8_2_cycle1',
                metrics=record['metrics'], files=links, legacy_protocol_alias=None)
        if not isinstance(dataset, str) or not re.fullmatch(r'scan_[0-9a-f]{12}', dataset):
            raise ValueError('请选择当前目录内的官方 V8_2 数据集')
        if not re.fullmatch(r'case_\d{2}', case):
            raise ValueError('无效算例 ID')
        if self.motor.state(dataset)['status'] not in ('completed', 'completed_with_failures'):
            raise ValueError('扫描结束后才能比较官方波形')
        job = self.motor.path(dataset)
        manifest = json.loads((job/'manifest.json').read_text(encoding='utf-8'))
        matches = [c for c in manifest['cases'] if c['id'] == case]
        if len(matches) != 1:
            raise ValueError('该算例不属于所选任务')
        folder = (job/case).resolve()
        if not folder.is_relative_to(job.resolve()):
            raise ValueError('算例路径不属于所选任务')
        if not (folder/'result.json').is_file():
            raise ValueError('该案没有完整官方结果；不能用其他算例替代')
        saved = json.loads((folder/'result.json').read_text(encoding='utf-8'))
        protocol = manifest.get('measurement_protocol', 'v8_2_cycle1')
        alias = 'measurement_protocol' not in manifest and saved['metric_protocol'] == LEGACY_PROTOCOL
        if saved['metric_protocol'] != protocol and not (alias and protocol == 'v8_2_cycle1'):
            raise ValueError('结果与任务测量协议不同')
        for name, digest in saved['csv_hashes'].items():
            source = (folder/name).resolve()
            if not source.is_relative_to(folder) or file_hash(source) != digest:
                raise ValueError('官方 CSV 路径或哈希改变，停止比较')
        paths = {k: folder/v for k, v in FILES.items() if k != 'solid_loss' or (folder/v).is_file()}
        for path in paths.values():
            if str(path.relative_to(folder)) not in saved['csv_hashes']:
                raise ValueError('波形 CSV 未绑定到保存结果')
        computed = collect_metrics(folder, protocol)
        for key in ('T_avg_Nm', 'K_T_ripple_pct', 'P_Fe_W', 'P_Cu_W', 'eta_estimate_pct'):
            a, b = saved[key], computed[key]
            if a != b and (a is None or b is None or abs(a-b) > 1e-10):
                raise ValueError('官方波形与保存指标不符: '+key)
        if saved['accepted'] != computed['accepted']:
            raise ValueError('保存数值筛查与官方波形不符')
        return paths, dict(dataset=dataset, case=case, material=matches[0]['material'],
            model_version=manifest['model_version'], measurement_protocol=protocol, metrics=computed,
            files={k: '/api/workbench/files/motor/'+dataset+'/'+case+'/'+quote(FILES[k], safe='/') for k in paths},
            legacy_protocol_alias=LEGACY_PROTOCOL if alias else None)

    def _read(self, dataset, case):
        paths, record = self._paths(dataset, case)
        series, time, hashes = {}, None, {}
        for name, path in paths.items():
            before = file_hash(path)
            if before != record['metrics']['csv_hashes'].get(str(Path(FILES[name]))):
                raise ValueError('官方波形与已核对指标的源哈希不符')
            headers, values = numeric_csv(path)
            tc = next((c for c in headers if 'Time' in c), None)
            match = '-Moving1.Torque' if name == 'torque' else {'core_loss':'CoreLoss', 'copper_loss':'StrandedLoss', 'solid_loss':'SolidLoss'}[name]
            yc = next((c for c in headers if c.split(' [', 1)[0] == match), None)
            if tc is None or yc is None:
                raise ValueError('缺少已知的官方时间或波形字段')
            ts = values(tc)*unit(tc, {'s':1000, 'ms':1, 'us':.001, 'ns':1e-6})
            if time is None:
                time = ts
            elif len(time) != len(ts) or not np.allclose(time, ts, atol=1e-7, rtol=1e-8):
                raise ValueError('所选案转矩与损耗时间网格不同')
            scale = unit(yc, {'NewtonMeter':1, 'Nm':1, 'mNewtonMeter':.001} if name == 'torque' else {'W':1, 'mW':.001, 'kW':1000})
            series[name] = (values(yc)*scale).tolist()
            if file_hash(path) != before:
                raise ValueError('官方波形在读取期间改变')
            hashes[name] = before
        record.update(time_ms=time.tolist(), series=series, csv_sha256=hashes,
                      selected_window_ms=record['metrics']['selected_window_ms'],
                      accepted=record['metrics']['accepted'])
        record['metrics'] = {k: v for k, v in record['metrics'].items()
                             if k not in ('model_physics_audit', 'comparison_eligibility')}
        return record, paths

    def compare(self, data):
        if not isinstance(data, dict) or set(data) != {'selection'}:
            raise ValueError('请明确选择官方算例')
        selection = data['selection']
        if not isinstance(selection, list) or not 1 <= len(selection) <= 6:
            raise ValueError('每次选择1至6个官方算例')
        seen, records, sources = set(), [], []
        for item in selection:
            if not isinstance(item, dict) or set(item) != {'dataset', 'case'} or not all(isinstance(v, str) for v in item.values()):
                raise ValueError('无效的波形选择')
            identity = (item['dataset'], item['case'])
            if identity in seen:
                raise ValueError('不能重复选择同一算例')
            seen.add(identity)
            record, paths = self._read(*identity)
            records.append(record)
            sources.append(paths)
        versions = {(r['model_version'], r['measurement_protocol']) for r in records}
        if len(versions) != 1:
            raise ValueError('同图比较需要同一模型版本和测量协议；请分别查看不同版本')
        base = records[0]['time_ms']
        if any(len(r['time_ms']) != len(base) or not np.allclose(r['time_ms'], base, atol=1e-7, rtol=1e-8) for r in records):
            raise ValueError('不同时间网格不能叠加比较；请分别查看')
        payload = dict(version=VERSION, created_utc=datetime.now(timezone.utc).isoformat(), records=records,
            model_version=records[0]['model_version'], measurement_protocol=records[0]['measurement_protocol'],
            scope='Official raw CSV, no smoothing or resampling; same model/protocol/time grid',
            new_native_solves=0, raw_results_changed=False)
        return payload, sources

    def export(self, data):
        payload, sources = self.compare(data)
        stream = io.StringIO(newline='')
        writer = csv.writer(stream)
        writer.writerow(['dataset','case','material','time_ms','torque_Nm','core_loss_W','copper_loss_W','solid_loss_W','in_metric_window'])
        for r in payload['records']:
            for i, t in enumerate(r['time_ms']):
                lo, hi = r['selected_window_ms']
                writer.writerow([r['dataset'],r['case'],r['material'],t,r['series']['torque'][i],
                    r['series']['core_loss'][i],r['series']['copper_loss'][i],
                    r['series'].get('solid_loss', ['']*len(r['time_ms']))[i],lo-1e-7 <= t <= hi+1e-7])
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as bundle:
            bundle.writestr('comparison.json', json.dumps(payload,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
            bundle.writestr('comparison.csv', '\ufeff'+stream.getvalue())
            for r, paths in zip(payload['records'], sources):
                for name, path in paths.items():
                    raw = path.read_bytes()
                    if hashlib.sha256(raw).hexdigest() != r['csv_sha256'][name]:
                        raise ValueError('官方文件在下载准备期间改变')
                    bundle.writestr(r['dataset']+'/'+r['case']+'/'+path.name, raw)
        output.seek(0)
        return output
