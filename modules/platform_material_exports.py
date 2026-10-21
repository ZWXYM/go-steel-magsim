"""Read existing platform exports and original source records; never infer or rewrite."""
import csv
import hashlib
import io
import json
import re
import zipfile
from pathlib import Path

def encoded(value):
    return (json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n').encode('utf8')


class PlatformMaterialExports:
    def __init__(self,directory): self.directory=directory

    @staticmethod
    def _read(path,base):
        path=path.resolve()
        if not path.is_relative_to(base) or not path.is_file(): raise ValueError('文件缺失或超出当前导出目录')
        return path.read_bytes()

    def snapshots(self):
        base=Path(self.directory() if callable(self.directory) else self.directory).resolve()
        records=[]
        if not base.exists(): return base,records
        try:
            materials=sorted(base.rglob('*.amat'));paired={p.with_suffix('.metadata.json') for p in materials}
            paths=[(p,p.with_suffix('.metadata.json')) for p in materials]
            paths += [(None,p) for p in sorted(base.rglob('*.metadata.json')) if p not in paired]
            for material,meta in paths:
                name=(material or meta).relative_to(base).as_posix();contents={};files={};errors=[];kind='paired' if meta.exists() else 'missing'
                if material and not meta.exists() and (material.parent/'augmented_metadata.json').is_file():meta=material.parent/'augmented_metadata.json';kind='directory_record'
                for key,path in [('material.amat',material),('metadata.json',meta)]:
                    if path is None or not path.exists():continue
                    try:
                        raw=self._read(path,base);contents[key]=raw;files[key]=dict(name=path.relative_to(base).as_posix(),bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest())
                    except (OSError,ValueError) as error:errors.append(key+'：'+str(error))
                metadata=None;augmentation=None
                if 'metadata.json' in contents:
                    try:
                        def invalid(value):raise ValueError('非有限数值：'+value)
                        metadata=json.loads(contents['metadata.json'].decode('utf-8-sig'),parse_constant=invalid)
                        if kind=='directory_record':
                            matches=[v for v in metadata if isinstance(v,dict) and v.get('aug_name')==material.stem] if isinstance(metadata,list) else []
                            augmentation=matches[0] if len(matches)==1 else None
                        elif not isinstance(metadata,dict):raise ValueError('配对元数据不是对象')
                    except (UnicodeError,ValueError) as error:kind='unreadable';errors.append('元数据：'+str(error));metadata=None
                elif kind!='missing':kind='unreadable'
                fields=metadata if kind=='paired' and isinstance(metadata,dict) else {}
                material_name=fields.get('material_name') if isinstance(fields.get('material_name'),str) else (material.stem if material else meta.name)
                identity=encoded(dict(folder=str(base),file=name,files=files));artifact='export_'+hashlib.sha256(identity).hexdigest()[:24]
                prefix='/api/workbench/platform-material-exports/'+artifact
                row=dict(id=artifact,relative_file=name,material_name=material_name,available='material.amat' in contents,
                    metadata_kind=kind,model_id=fields.get('source_model_id') if isinstance(fields.get('source_model_id'),str) else None,
                    prediction_params=fields.get('prediction_params') if isinstance(fields.get('prediction_params'),dict) else None,
                    H_axis=fields.get('H_axis') if isinstance(fields.get('H_axis'),str) else None,
                    augmentation_source=augmentation.get('source') if augmentation else None,files=files,error='；'.join(errors) or None,
                    urls={key:prefix+'/files/'+key for key in files},bundle_url=prefix+'/bundle',detail_url=prefix)
                records.append((row,contents,augmentation if kind=='directory_record' else metadata))
        except OSError as error:raise ValueError('导出目录不可读取：'+str(error)) from error
        return base,records

    def catalog(self):
        base,records=self.snapshots()
        snapshot=hashlib.sha256(encoded(dict(folder=str(base),ids=[r[0]['id'] for r in records]))).hexdigest()
        return dict(folder=str(base),records=[r[0] for r in records],snapshot_sha256=snapshot,new_predictions=0,new_native_solves=0,source_files_changed=False)

    def find(self,artifact):
        if not isinstance(artifact,str) or not re.fullmatch(r'export_[a-f0-9]{24}',artifact):raise ValueError('无效导出记录')
        base,records=self.snapshots()
        for row,contents,metadata in records:
            if row['id']==artifact:return dict(row,folder=str(base),metadata=metadata),contents
        raise ValueError('导出不存在、工作目录或原文件已变化；请刷新目录，不自动改选其他材料')

    def download(self,artifact,name):
        if name not in ('material.amat','metadata.json'):raise ValueError('不支持的导出文件')
        detail,contents=self.find(artifact)
        if name not in contents:raise ValueError('此记录没有可读取的文件：'+name)
        return contents[name],Path(detail['files'][name]['name']).name

    def bundle(self,artifact):
        detail,contents=self.find(artifact);files=dict(contents)
        files['entry.json']=encoded(detail)
        files['README.txt']=('原平台保存的文件与来源记录，按原字节保留；没有重新预测、导出或求解。\n'
            'metadata_kind=missing/unreadable表示来源缺失/不可读；directory_record为同目录原增强清单，只按aug_name精确匹配，不能当独立实测。\n').encode('utf8')
        files['manifest.json']=encoded(dict(id=artifact,files={k:hashlib.sha256(v).hexdigest() for k,v in files.items()}))
        out=io.BytesIO()
        with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as archive:
            for name,raw in files.items():archive.writestr(name,raw)
        out.seek(0);return out

    def csv(self,expected=None):
        data=self.catalog()
        if expected is not None and expected!=data['snapshot_sha256']:raise ValueError('导出目录或文件已变化，请刷新后再下载清单')
        out=io.StringIO(newline='');fields=['id','relative_file','material_name','available','metadata_kind','model_id','prediction_params','H_axis','augmentation_source','AMAT_sha256','metadata_sha256','folder','error'];writer=csv.DictWriter(out,fieldnames=fields);writer.writeheader()
        for row in data['records']:
            value={key:row.get(key) for key in fields};value.update(prediction_params=json.dumps(row['prediction_params'],ensure_ascii=False) if row['prediction_params'] is not None else '',AMAT_sha256=row['files'].get('material.amat',{}).get('sha256'),metadata_sha256=row['files'].get('metadata.json',{}).get('sha256'),folder=data['folder']);writer.writerow(value)
        return out.getvalue().encode('utf-8-sig')
