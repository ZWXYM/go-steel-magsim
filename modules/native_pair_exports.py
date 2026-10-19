"""Saved CPU exports selected from local frozen calibration runs."""
import hashlib
import io
import json
import re
import uuid
import zipfile
from datetime import datetime,timezone
from pathlib import Path
from modules.native_pair_export import export_pair
from modules.material_calibration import PHYSICS_VERSION,file_hash


class NativePairExports:
    def __init__(self,root,storage):
        self.root=Path(root).resolve();self.sources=(self.root/'calibration').resolve()
        self.storage=Path(storage).resolve()

    def catalog(self):
        rows=[];issues=[]
        prepared_root=self.storage.parent/'native_material_runs'
        candidates=[(p,self.sources,'calibration') for p in self.sources.rglob('manifest.json')]
        candidates += [(p,prepared_root,'prepared') for p in prepared_root.glob('nativerun_*/run/manifest.json')]
        for path,source_root,kind in sorted(candidates,key=lambda r:str(r[0])):
            if not path.resolve().is_relative_to(source_root):continue
            try:
                m=json.loads(path.read_text(encoding='utf-8-sig'))
                if not isinstance(m,dict) or not {'materials','jobs','H_grid','n_grains'}<=m.keys():continue
                if m.get('physics_version')!=PHYSICS_VERSION or m.get('H_axis')!='physical_A_per_m':continue
                relative=path.relative_to(source_root).as_posix()
                grades=[v['grade'] for v in m['materials']]
                if any(not isinstance(g,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',g) for g in grades):raise ValueError('来源材料ID格式不支持')
                if not grades or len(grades)!=len(set(grades)):raise ValueError('来源材料缺失或重复')
                status=path.parent/'run_status.json'
                state=json.loads(status.read_text(encoding='utf8')) if status.is_file() else {}
                if not isinstance(state,dict) or not isinstance(state.get('jobs',[]),list):raise ValueError('来源执行状态格式不正确')
                key=relative if kind=='calibration' else 'prepared/'+relative
                row=dict(id='nativesrc_'+hashlib.sha256(key.encode()).hexdigest()[:16],
                    source_kind=kind,source_relative=path.parent.relative_to(source_root).as_posix(),
                    title=path.parent.relative_to(source_root).as_posix() if kind=='calibration' else '新材料/'+path.parent.parent.name,grades=grades,
                    manifest_sha256=file_hash(path),n_grains=m['n_grains'],H_grid_points=len(m['H_grid']),
                    texture_sampling_version=m.get('texture_sampling_version','legacy_multi_peak_importance_v1'),
                    declared_jobs=len(m['jobs']),recorded_successes=sum(j.get('exit_code')==0 for j in state.get('jobs',[])),
                    readiness='导出时核对原脚本和table；当前仅盘点来源')
                if kind=='prepared':row['native_run_id']=path.parent.parent.name
                rows.append(row)
            except (ValueError,KeyError,TypeError,AttributeError,OSError) as exc:
                issues.append(dict(source=path.parent.relative_to(source_root).as_posix(),error=str(exc)))
        return dict(sources=rows,issues=issues,new_native_solves=0)

    def prepare(self,data):
        if not isinstance(data,dict) or set(data)!={'source_id','manifest_sha256','grade'}:
            raise ValueError('请选择冻结来源和材料，不接受任意本机路径')
        source=next((r for r in self.catalog()['sources'] if r['id']==data['source_id']),None)
        if not source or data['grade'] not in source['grades']:raise ValueError('所选来源或材料不存在，请刷新')
        if data['manifest_sha256']!=source['manifest_sha256']:raise ValueError('来源已变化，请刷新后重新选择')
        source_root=self.sources if source['source_kind']=='calibration' else self.storage.parent/'native_material_runs'
        base=(source_root/source['source_relative']).resolve()
        if not base.is_relative_to(source_root):raise ValueError('来源超出允许目录')
        self.storage.mkdir(parents=True,exist_ok=True)
        artifact='nativepair_'+uuid.uuid4().hex[:12];folder=self.storage/artifact
        export_pair(base,data['grade'],folder)
        record=dict(id=artifact,source_id=source['id'],source_title=source['title'],material_id=data['grade'],
            source_manifest_sha256=source['manifest_sha256'],created_utc=datetime.now(timezone.utc).isoformat(),
            files={name:file_hash(folder/name) for name in ('raw_pair.json','source_manifest.json')},
            new_native_solves=0,new_predictions=0,quality_status='unassessed')
        (folder/'manifest.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf8')
        return self.get(artifact)

    def path(self,artifact):
        if not isinstance(artifact,str) or not re.fullmatch(r'nativepair_[0-9a-f]{12}',artifact):raise ValueError('无效原生对导出ID')
        folder=(self.storage/artifact).resolve()
        if not folder.is_relative_to(self.storage):raise ValueError('导出目录越界')
        return folder

    def get(self,artifact):
        folder=self.path(artifact);record=json.loads((folder/'manifest.json').read_text(encoding='utf8'))
        if not isinstance(record,dict) or record.get('id')!=artifact or set(record.get('files',{}))!={'raw_pair.json','source_manifest.json'}:raise ValueError('导出记录不完整')
        for name,digest in record['files'].items():
            if not (folder/name).resolve().is_relative_to(folder) or file_hash(folder/name)!=digest:raise ValueError('保存导出文件发生变化，未载入预测')
        record['raw_pair']=json.loads((folder/'raw_pair.json').read_text(encoding='utf8'))
        record['bundle_url']='/api/workbench/native-pairs/'+artifact+'/bundle'
        return record

    def records(self):
        rows=[]
        for path in sorted(self.storage.glob('nativepair_*/manifest.json'),key=lambda p:p.stat().st_mtime,reverse=True):
            try:
                record=self.get(path.parent.name);record.pop('raw_pair');rows.append(record)
            except (ValueError,KeyError,OSError) as exc:rows.append(dict(id=path.parent.name,error=str(exc)))
        return rows

    def download(self,artifact,name):
        self.get(artifact)
        if name not in ('raw_pair.json','source_manifest.json','manifest.json'):raise ValueError('不允许下载此文件')
        return self.path(artifact)/name

    def bundle(self,artifact):
        self.get(artifact);out=io.BytesIO()
        with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
            for name in ('raw_pair.json','source_manifest.json','manifest.json'):z.write(self.path(artifact)/name,name)
        out.seek(0);return out
