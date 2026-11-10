"""Collect explicitly selected saved results into a private, reproducible workpack."""
import csv
import io
import json
import math
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from modules.calibration_transfer import load_bank
from modules.material_batch_reports import report_bundle
from modules.motor_dataset_export import dataset_bundle,DATASET_ID_PATTERN
from modules.system_data import WorkpackStore, MAX_CONTENT_BYTES, json_bytes, sha256
from modules.system_data_relations import batch_calibration_relation

VERSION = 'system_data_workpack_v4'
PRODUCER_SHA256 = sha256(Path(__file__).read_bytes())


def csv_bytes(rows, fields):
    out = io.StringIO(newline='')
    writer = csv.DictWriter(out, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue().encode('utf-8-sig')


def batch_binding(batches, artifact):
    folder = batches.path(artifact)
    return sha256(json_bytes({name: sha256((folder/name).read_bytes()) for name in ('manifest.json', 'state.json')}))


class SystemDataBuilder:
    def __init__(self, storage, calibration, optimization, materials, batches, plans=None):
        self.store = WorkpackStore(Path(storage)/'system_data')
        self.calibration, self.optimization, self.materials, self.batches = calibration, optimization, materials, batches
        from modules.system_data_optimization import SavedOptimizationExports
        self.optimization_exports=SavedOptimizationExports(optimization,plans)
        from modules.system_data_studies import SavedConstraintExports
        self.constraint_exports=SavedConstraintExports(optimization,Path(storage)/'optimization_sensitivity')
        from modules.system_data_predictions import SavedPredictionExports
        self.prediction_exports=SavedPredictionExports(materials)

    def build(self, selection):
        if (not isinstance(selection, dict) or not {'calibration', 'datasets', 'batch'} <= set(selection)
                or set(selection)-{'calibration', 'datasets', 'batch', 'optimization', 'constraint_study','predictions'}
                or not isinstance(selection['datasets'], list) or len(selection['datasets']) > 16):
            raise ValueError('请明确选择校准、至多16份电机数据集及可选批量')
        predictions=selection.get('predictions',[])
        if not isinstance(predictions,list) or len(predictions)>16:
            raise ValueError('每包至多16份保存预测')
        if not selection['calibration'] and not selection['datasets'] and not selection['batch'] and not selection.get('optimization') and not selection.get('constraint_study') and not predictions:
            raise ValueError('请至少选择一项已有数据')
        files, source_files = {}, {}
        index = dict(version=VERSION, created_utc=datetime.now(timezone.utc).isoformat(),
                     calibration=None, datasets=[], calibrated_batch=None, optimization=None, constraint_study=None,saved_predictions=[], producer_sha256=PRODUCER_SHA256,
                     private_only=True, new_native_solves=0, new_predictions=0, new_analyses=0)

        def put(name, blob):
            if name in files:
                raise ValueError('数据包成员冲突')
            if len(blob) + sum(len(value) for value in files.values()) > MAX_CONTENT_BYTES:
                raise ValueError('所选数据超出单包大小限制，请分开选择')
            files[name] = blob

        def bound_file(path):
            blob = Path(path).read_bytes()
            source_files[Path(path)] = sha256(blob)
            return blob

        cal = selection['calibration']
        if cal is not None:
            if (not isinstance(cal, dict) or set(cal) != {'id', 'report_sha256'}
                    or not isinstance(cal['id'], str) or not re.fullmatch(r'cal_[a-f0-9]{12}', cal['id'])):
                raise ValueError('校准选择不符')
            folder = self.calibration.get(cal['id']).resolve()
            report_blob, bank_blob = bound_file(folder/'report.json'), bound_file(folder/'bank.json')
            report, bank = json.loads(report_blob), load_bank(folder/'bank.json')
            if (sha256(report_blob) != cal['report_sha256'] or report['id'] != cal['id']
                    or report['H_axis'] != 'physical_A_per_m' or report['bank_sha256'] != bank.bank_sha256):
                raise ValueError('所选校准或库已变化，请刷新后重新选择')
            put('calibration/report.json', report_blob)
            put('calibration/bank.json', bank_blob)
            columns = {'raw_B_T':'raw_B', 'reference_B_T':'reference_B', 'calibration_fit_B_T':'corrected_B',
                       'material_holdout_B_T':'holdout_B', 'legacy_holdout_B_T':'legacy_holdout_B', 'reference_only_B_T':'reference_only_B'}
            if any('original_nested_holdout_B' in pair for pair in report['comparisons']):
                columns['original_protocol_holdout_B_T']='original_nested_holdout_B'
            rows, identities = [], set()
            for pair in report['comparisons']:
                key = (pair['grade'], pair['direction'])
                h = pair['H']
                if key in identities or key[0] not in report['selected_grades'] or key[1] not in ('RD', 'TD'):
                    raise ValueError('校准曲线材料或方向重复/不符')
                identities.add(key)
                arrays = [h]+[pair[name] for name in columns.values() if name in pair]
                if (len(h)<2 or any(len(a)!=len(h) for a in arrays)
                        or any(not isinstance(v,(int,float)) or isinstance(v,bool) or not math.isfinite(v) for a in arrays for v in a)
                        or any(a>=b for a,b in zip(h,h[1:])) or h[0]<0):
                    raise ValueError('保存曲线的物理H或数据点不完整')
                for i, field in enumerate(h):
                    rows.append(dict(grade=key[0], direction=key[1], H_A_per_m=field,
                        **{name:pair[source][i] if source in pair else None for name,source in columns.items()}))
            if identities != {(grade,direction) for grade in report['selected_grades'] for direction in ('RD','TD')}:
                raise ValueError('校准RD/TD曲线缺失')
            put('calibration/curves.csv',csv_bytes(rows,['grade','direction','H_A_per_m',*columns]))
            metrics=report['metrics']
            put('calibration/metrics.csv',csv_bytes(metrics,list(dict.fromkeys(key for row in metrics for key in row))))
            # Use the existing material reader, including its bank/metadata/hash checks.
            for kind, field in (('fit','known_material_exports'),('holdout','holdout_material_exports')):
                if set(report[field]) != set(report['selected_grades']):
                    raise ValueError('保存材料出口与所选校准不完整')
                for grade in report['selected_grades']:
                    put('calibration/materials/'+kind+'_'+grade+'.zip',self.materials.bundle(cal['id'],kind+':'+grade).getvalue())
            index['calibration']=dict(id=cal['id'], bank_sha256=bank.bank_sha256, report_sha256=sha256(report_blob),
                calibration_version=report.get('calibration_version',bank.payload.get('calibration_version')),
                display_label=report.get('display_label'),default_eligible=report.get('default_eligible',True),
                selected_grades=report['selected_grades'], curve_pairs=len(identities), curve_rows=len(rows),
                metrics_rows=len(metrics), calibration_fit=report['calibration_fit'], material_holdout=report['material_holdout'],
                distinct_material_count=len(set(report['selected_grades'])),
                evidence_status=report['evidence_status'], H_axis=report['H_axis'])

        seen_predictions=set()
        for selected in predictions:
            if not isinstance(selected,dict) or not isinstance(selected.get('id'),str) or not isinstance(selected.get('calibration_id'),str):
                raise ValueError('保存预测选择不符')
            identity=(selected['calibration_id'],selected['id'])
            if identity in seen_predictions:raise ValueError('保存预测选择重复')
            seen_predictions.add(identity)
            saved,blob=self.prediction_exports.collect(selected)
            put('predictions/'+saved['calibration_id']+'/'+saved['id']+'.zip',blob)
            index['saved_predictions'].append(saved)

        seen=set()
        for selected in selection['datasets']:
            if (not isinstance(selected, dict) or set(selected) != {'id','source_hashes_sha256'}
                    or not isinstance(selected['id'], str) or not re.fullmatch(DATASET_ID_PATTERN,selected['id'])
                    or selected['id'] in seen):
                raise ValueError('电机数据集选择无效或重复')
            seen.add(selected['id'])
            blob=dataset_bundle(self.optimization,selected['id']).getvalue()
            with zipfile.ZipFile(io.BytesIO(blob)) as archive:
                dataset=json.loads(archive.read('dataset.json'))
                snapshot=json.loads(archive.read('source_snapshot.json'))
            if sha256(json_bytes(dataset['source_hashes'])) != selected['source_hashes_sha256']:
                raise ValueError('所选电机数据来源已变化，请刷新后重新选择')
            put('motor_datasets/'+selected['id']+'.zip',blob)
            index['datasets'].append(dict(id=dataset['id'], version=dataset['version'],
                measurement_protocol=dataset['measurement_protocol'], row_count=snapshot['row_count'],
                original_accepted_count=snapshot['saved_accepted_count'], native_csv_accepted_count=snapshot['native_csv_accepted_count'],
                data_usage=snapshot['data_usage'], source_hashes=dataset['source_hashes'], bundle_sha256=sha256(blob)))

        batch=selection['batch']
        if batch is not None:
            if (not isinstance(batch,dict) or set(batch)!={'id','source_binding_sha256'}
                    or not isinstance(batch['id'],str) or not re.fullmatch(r'bhbatch_[a-f0-9]{12}',batch['id'])):
                raise ValueError('校准电机批量选择不符')
            before=batch_binding(self.batches,batch['id'])
            if before!=batch['source_binding_sha256']:
                raise ValueError('所选批量状态已变化，请刷新后重新选择')
            blob=report_bundle(self.batches,batch['id']).getvalue()
            with zipfile.ZipFile(io.BytesIO(blob)) as archive:
                record=json.loads(archive.read('batch.json'))
            if record['id']!=batch['id'] or record['status'] not in ('completed','completed_with_failures'):
                raise ValueError('批量结束后再收集当前结果，不从活动任务取快照')
            if batch_binding(self.batches,batch['id'])!=before:
                raise ValueError('批量在打包期间发生变化，请刷新后重新选择')
            put('calibrated_motor/'+batch['id']+'.zip',blob)
            index['calibrated_batch']=dict(id=batch['id'], source_binding_sha256=before, bundle_sha256=sha256(blob),
                result_count=sum(item.get('result_available') is True for item in record['items']), item_count=len(record['items']),
                calibration_relation=batch_calibration_relation(record,index['calibration']),
                efficiency_enabled=False, optimization_ranking_enabled=False)

        if selection.get('optimization') is not None:
            optimization,content=self.optimization_exports.collect(selection['optimization'])
            optimization['current_dataset_bundle_included']=any(row['id']==optimization['dataset'] for row in index['datasets'])
            for row in index['datasets']:
                if row['id']==optimization['dataset'] and row['source_hashes']!=optimization['dataset_source_hashes']:
                    raise ValueError('所选原筛选与当前同名数据集的来源不同，请分开打包')
            for name,blob in content.items():put('optimization/'+name,blob)
            index['optimization']=optimization
        if selection.get('constraint_study') is not None:
            study,content=self.constraint_exports.collect(selection['constraint_study'])
            for row in index['datasets']:
                if row['id']==study['dataset'] and row['source_hashes']!=study['source_hashes']:
                    raise ValueError('所选约束研究与当前同名数据集的来源不同，请分开打包')
            for name,blob in content.items():put('constraint_studies/'+study['id']+'/'+name,blob)
            index['constraint_study']=study
        if any(sha256(path.read_bytes())!=checksum for path,checksum in source_files.items()):
            raise ValueError('校准来源在打包期间变化，未登记新包')
        put('selection.json',json_bytes(selection))
        put('data_index.json',json_bytes(index))
        put('README.md',('''# 所选系统数据工作包

本包在网页显式选择后，从已保存数据生成；不含论文正文，不新增仿真、预测、训练或优化筛选。范围见selection.json和data_index.json。

calibration/保存所选校准原报告、bank、曲线/误差CSV及既有标定/留出材料包。拟合和整材料留出分别报告；原成分、织构与参考状态保留在bank和材料来源中，未知项不补为实测。

predictions/仅含明确勾选的已有材料预测包，保留原输入、父bank、有效子bank、修正曲线及材料出口；不重新预测。整材料排除与训练成员分别保留，不与同材料拟合混称独立验证。零损耗出口不能用于效率排名。

motor_datasets/每包仅含一个原模型/协议的数据集、原文件及哈希。原判据通过与官方CSV来源且通过原判据分别列出；V8_1历史处理/估算保留，不能替代当前原生损耗或稳态改善证据。重复材料、工况及曲线节点不增加独立样品数。不同版本不得混拼排名。

calibrated_motor/仅在选择批量时包含；保留实际材料、官方转矩CSV、原接受标记和异常。标量方向等效不视为局部RD/TD验证；零损耗B-H分支不输出效率或最终优化排名。
所选校准与既有电机批量可能来自不同版本；实际记录、父/有效子库和逐条关系见data_index.json的calibrated_batch.calibration_relation。分别归档不表示已用所选校准重算该批量。

optimization/仅在明确选择已有筛选时包含；原analysis/ranking/说明/ZIP逐字节保留，附其关联原计划（如有）与当前使用范围说明。不重新评分、不改基准、权重、约束或“无改善候选”的结果；历史估算不改称原生损耗改善。

constraint_studies/仅在明确选择已有约束研究时包含；原六个文件逐字节保存，不重新评分。配置组数不是新增材料，原默认候选数和约束范围保持；同名数据集来源不同需分开打包。

CSV单位以原文件列头为准；ns/mW等原列保持。数据集内指标按各自原公式和时间窗口生成。原效率估算未覆盖全部机械/杂散损耗，转矩折算功率不作为铭牌额定功率。参阅原报告/来源metadata，不把历史估算标为实测。

manifest.json核对文件完整性；本包是私有冻结快照，不随新任务更新，不自动公开到GitHub。完整原生工程、日志和缓存仍保留在原任务目录。
''').encode('utf8'))
        put('manifest.json',json_bytes(dict(version=VERSION,private_only=True,producer_sha256=PRODUCER_SHA256,
            files={name:sha256(blob) for name,blob in files.items()},new_native_solves=0,new_predictions=0,new_analyses=0)))
        out=io.BytesIO()
        with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as archive:
            for name,blob in files.items(): archive.writestr(name,blob)
        relation=(index['calibrated_batch'] or {}).get('calibration_relation') or {}
        return self.store.register_bytes(out.getvalue(),'所选系统数据 '+index['created_utc'],
            '网页冻结快照：'+str(len(index['datasets']))+'份版本独立电机数据集；校准'+('已包含' if cal else '未选择')+
            '，校准电机批量'+('已包含' if batch else '未选择')+'，已有原筛选'+('已包含' if index['optimization'] else '未选择')+
            '，已有约束研究'+('已包含' if index['constraint_study'] else '未选择')+
            '，保存预测'+str(len(index['saved_predictions']))+'份。原始异常和使用范围保持。'+
            (' '+relation['note'] if relation.get('relationship') in ('unknown','different_or_mixed_sources') else ''))
