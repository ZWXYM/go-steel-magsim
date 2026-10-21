"""Describe explicit windows of existing calibrated-motor CSV points, without solving."""
import copy
import csv
import hashlib
import io
import json
import math
import re
import zipfile
from datetime import datetime,timezone
from pathlib import Path
from flask import Blueprint,jsonify,render_template,request,send_file
from modules.material_batch_reports import verified_operating_point,torque_findings

VERSION='calibrated_batch_torque_windows_v1'
TIME_TOL_MS=1e-7  # Same physical tolerance as the existing 1e-10-second CSV check.


def digest(data):return hashlib.sha256(data).hexdigest()
LOADED_PRODUCER_SHA=digest(Path(__file__).read_bytes())
def json_bytes(data):return (json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False)+'\n').encode('utf8')
def finite(value):return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value)


def window_statistics(times,torques,window,speed_rpm):
    if (not isinstance(times,list) or not isinstance(torques,list) or len(times)!=len(torques) or len(times)<2
            or not all(finite(value) for value in times+torques) or any(a>=b for a,b in zip(times,times[1:]))):
        raise ValueError('原始转矩时间或数据不完整、无序或不是有限数值')
    if (not isinstance(window,(list,tuple)) or len(window)!=2 or not all(finite(value) for value in window)
            or window[0]>=window[1] or not finite(speed_rpm) or speed_rpm<=0):
        raise ValueError('请选择递增的有效时间区间和已保存转速')
    lo,hi=map(float,window)
    if lo<times[0]-TIME_TOL_MS or hi>times[-1]+TIME_TOL_MS:raise ValueError('所选区间超出已有原始波形')
    indices=[i for i,time in enumerate(times) if lo-TIME_TOL_MS<=time<=hi+TIME_TOL_MS]
    if len(indices)<2:raise ValueError('所选区间至少需要两个已有采样点；不会插值补点')
    values=[torques[i] for i in indices];mean=math.fsum(value/len(values) for value in values);minimum=min(values);maximum=max(values)
    ratio=(maximum-minimum)/abs(mean)*100 if mean!=0 else None
    power=mean*2*math.pi*speed_rpm/60
    if not all(finite(v) for v in (mean,minimum,maximum,power)) or ratio is not None and not finite(ratio):raise ValueError('区间统计超出有限数值范围')
    return dict(requested_window_ms=[lo,hi],sampled_window_ms=[times[indices[0]],times[indices[-1]]],sample_indices=indices,
        n_samples=len(indices),sample_mean_torque_Nm=mean,min_torque_Nm=minimum,max_torque_Nm=maximum,
        peak_to_peak_over_abs_mean_pct=ratio,nonpositive_samples=sum(value<=0 for value in values),
        speed_rpm=speed_rpm,electromagnetic_mechanical_power_W=power,
        scope='Selected original samples; arithmetic mean; no resampling, new acceptance test or material ranking')


class TorqueWindows:
    def __init__(self,batches):self.batches=batches

    def catalog(self):
        rows=[]
        for batch in self.batches.records():
            items=[]
            for item in batch.get('items',[]):
                metrics=item.get('metrics') or {};available=item.get('result_available') is True
                items.append(dict(id=item['id'],material_name=item['source']['material_name'],selection=item['selection'],
                    available=available,original_acceptance=metrics.get('torque_numerical_acceptance') if available else None,
                    original_window_ms=metrics.get('selected_window_ms') if available else None,error=item.get('error'),
                    time_range_ms=[metrics['time_ms'][0],metrics['time_ms'][-1]] if available else None))
            rows.append(dict(id=batch['id'],status=batch['status'],created_utc=batch.get('created_utc'),items=items,error=batch.get('error')))
        return dict(version=VERSION,batches=rows,new_native_solves=0,raw_results_changed=False)

    def _snapshot(self,artifact,data):
        if not isinstance(artifact,str) or not re.fullmatch(r'bhbatch_[0-9a-f]{12}',artifact):raise ValueError('无效校准材料批量')
        if not isinstance(data,dict) or set(data)-{'items','window_ms'}:raise ValueError('请明确选择批量项和时间区间')
        selected=data.get('items')
        if (not isinstance(selected,list) or not 1<=len(selected)<=6 or any(not isinstance(name,str) or not re.fullmatch(r'item_\d{2}',name) for name in selected)
                or len(selected)!=len(set(selected))):raise ValueError('每次选择1至6个不同批量项')
        batch=self.batches.get(artifact);lookup={item['id']:item for item in batch['items']};records=[];raw_sources={};frames=set();base_times=None;window=data.get('window_ms')
        for name in selected:
            if name not in lookup:raise ValueError('所选项目不属于本批量')
            item=lookup[name]
            if item.get('result_available') is not True:raise ValueError('所选项目没有完整官方转矩结果：'+name)
            execution=self.batches.chain.execution.get(item['execution_id']);operating=verified_operating_point(item,execution);metrics=item['metrics']
            if execution['result']['metrics']!=metrics:raise ValueError('批量原指标与已核验执行不符')
            template=item['source'].get('template_sha256')
            if not isinstance(template,str) or not re.fullmatch(r'[0-9a-f]{64}',template):raise ValueError('保存的模板身份缺失')
            frames.add((template,item['model_version'],item['measurement_protocol'],operating['speed_rpm']))
            times=metrics.get('time_ms');torques=metrics.get('torque_Nm')
            if window is None:window=metrics.get('selected_window_ms')
            stats=window_statistics(times,torques,window,operating['speed_rpm'])
            if base_times is not None and (len(times)!=len(base_times) or any(not math.isclose(a,b,abs_tol=TIME_TOL_MS,rel_tol=1e-8) for a,b in zip(times,base_times))):raise ValueError('所选项目原始时间网格不同，请分别查看')
            base_times=times
            raw=self.batches.chain.execution.download(execution['id'],'case/reports/Torque Plots.csv').read_bytes()
            if digest(raw)!=metrics['torque_csv_sha256']:raise ValueError('官方转矩CSV与保存来源哈希不同')
            raw_sources[name]=raw
            records.append(dict(item=name,execution_id=execution['id'],analysis_plan_id=execution['analysis_plan_id'],analysis_url=item['analysis_url'],
                material_name=item['source']['material_name'],selection=copy.deepcopy(item['selection']),source=copy.deepcopy(item['source']),
                model_version=item['model_version'],measurement_protocol=item['measurement_protocol'],time_ms=list(times),torque_Nm=list(torques),
                original_metrics=copy.deepcopy({key:value for key,value in metrics.items() if key not in ('time_ms','torque_Nm')}),
                original_findings=torque_findings(metrics),window_statistics=stats,torque_csv_sha256=metrics['torque_csv_sha256'],
                raw_csv_url='/api/workbench/calibrated-motor/executions/'+execution['id']+'/files/case/reports/Torque%20Plots.csv'))
        if len(frames)!=1:raise ValueError('同图需要相同模板、模型版本、周期协议和转速；请分别查看')
        identity=dict(version=VERSION,producer_sha256=LOADED_PRODUCER_SHA,batch=artifact,requested_window_ms=records[0]['window_statistics']['requested_window_ms'],
            records=[{key:record[key] for key in ('item','execution_id','analysis_plan_id','selection','source','model_version','measurement_protocol','torque_csv_sha256')} for record in records])
        snapshot=digest(json.dumps(identity,sort_keys=True,separators=(',',':'),allow_nan=False).encode())
        result=dict(version=VERSION,producer_sha256=LOADED_PRODUCER_SHA,batch=artifact,created_utc=datetime.now(timezone.utc).isoformat(),snapshot_sha256=snapshot,
            requested_window_ms=identity['requested_window_ms'],records=records,new_native_solves=0,raw_results_changed=False,
            original_acceptance_changed=False,efficiency_enabled=False,optimization_ranking_enabled=False,
            scope='Descriptive windows of original official torque samples; original metrics/acceptance retained; derived power is not rated shaft power')
        return result,raw_sources

    def analyze(self,artifact,data):return self._snapshot(artifact,data)[0]

    def export(self,artifact,data):
        if not isinstance(data,dict) or set(data)-{'items','window_ms','snapshot_sha256'} or not re.fullmatch(r'[0-9a-f]{64}',str(data.get('snapshot_sha256',''))):raise ValueError('请先统计并按该结果快照下载')
        payload,raw_sources=self._snapshot(artifact,{key:value for key,value in data.items() if key!='snapshot_sha256'})
        if payload['snapshot_sha256']!=data['snapshot_sha256']:raise ValueError('选择、时间区间或来源已改变，请重新统计后下载')
        summary=io.StringIO(newline='');writer=csv.writer(summary);writer.writerow(['item','material','execution','requested_start_ms','requested_stop_ms','sampled_start_ms','sampled_stop_ms','n_samples','sample_mean_torque_Nm','min_torque_Nm','max_torque_Nm','peak_to_peak_over_abs_mean_pct','nonpositive_samples','speed_rpm','electromagnetic_mechanical_power_W','original_T_avg_Nm','original_numerical_acceptance','torque_csv_sha256'])
        points=io.StringIO(newline='');point_writer=csv.writer(points);point_writer.writerow(['item','execution','index','time_ms','torque_Nm','in_selected_window'])
        for record in payload['records']:
            stats=record['window_statistics'];writer.writerow([record['item'],record['material_name'],record['execution_id'],*stats['requested_window_ms'],*stats['sampled_window_ms'],*[stats[key] for key in ('n_samples','sample_mean_torque_Nm','min_torque_Nm','max_torque_Nm','peak_to_peak_over_abs_mean_pct','nonpositive_samples','speed_rpm','electromagnetic_mechanical_power_W')],record['original_metrics']['T_avg_Nm'],record['original_metrics']['torque_numerical_acceptance'],record['torque_csv_sha256']])
            chosen=set(stats['sample_indices'])
            for index,(time,value) in enumerate(zip(record['time_ms'],record['torque_Nm'])):point_writer.writerow([record['item'],record['execution_id'],index,time,value,index in chosen])
        files={'analysis.json':json_bytes(payload),'summary.csv':summary.getvalue().encode('utf-8-sig'),'all_original_points.csv':points.getvalue().encode('utf-8-sig')}
        for name,raw in raw_sources.items():files['raw/'+name+'/Torque Plots.csv']=raw
        files['README.txt']='区间统计不替换原指标或原判据。all_original_points.csv保留全部原始点并标明是否入选；raw为未改官方CSV。均值为选中点的算术均值，功率为电磁转矩乘已保存转速折算，不是铭牌额定轴功率。没有新求解、效率计算或最终材料排名。\n'.encode('utf8')
        files['manifest.json']=json_bytes(dict(version=VERSION,batch=artifact,snapshot_sha256=payload['snapshot_sha256'],files={name:digest(raw) for name,raw in files.items()},new_native_solves=0,original_acceptance_changed=False))
        out=io.BytesIO()
        with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as archive:
            for name,raw in files.items():archive.writestr(name,raw)
        out.seek(0);return out


def create_torque_window_routes(service):
    bp=Blueprint('torque_windows',__name__)
    @bp.errorhandler(ValueError)
    @bp.errorhandler(KeyError)
    @bp.errorhandler(OSError)
    def invalid(error):return jsonify(error=str(error)),400
    def query(export=False):
        allowed={'items','start_ms','stop_ms'}|({'snapshot'} if export else set())
        if set(request.args)-allowed or any(len(request.args.getlist(key))!=1 for key in request.args):raise ValueError('区间查询参数不完整或有重复')
        data=dict(items=(request.args.get('items') or '').split(','))
        if 'start_ms' in request.args or 'stop_ms' in request.args:
            if not {'start_ms','stop_ms'}<=set(request.args):raise ValueError('请同时填写开始和结束时间')
            data['window_ms']=[float(request.args['start_ms']),float(request.args['stop_ms'])]
        if export:data['snapshot_sha256']=request.args.get('snapshot')
        return data
    @bp.get('/calibrated-motor/torque-windows')
    def page():return render_template('torque_windows.html')
    @bp.get('/api/workbench/torque-windows')
    def catalog():
        response=jsonify(service.catalog());response.headers['Cache-Control']='no-store';return response
    @bp.get('/api/workbench/torque-windows/<artifact>/analyze')
    def analyze(artifact):
        response=jsonify(service.analyze(artifact,query()));response.headers['Cache-Control']='no-store';return response
    @bp.get('/api/workbench/torque-windows/<artifact>/export')
    def export(artifact):
        response=send_file(service.export(artifact,query(export=True)),as_attachment=True,download_name=artifact+'_torque_window.zip',mimetype='application/zip');response.headers['Cache-Control']='no-store';return response
    return bp
