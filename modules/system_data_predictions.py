"""Collect saved prediction materials without running the predictor again."""
import io,json,re,zipfile
from modules.system_data import json_bytes,sha256


class SavedPredictionExports:
    def __init__(self,materials):self.materials=materials

    def _detail(self,calibration,artifact):
        if (not isinstance(calibration,str) or not re.fullmatch(r'cal_[a-f0-9]{12}',calibration)
                or not isinstance(artifact,str) or not re.fullmatch(r'pred_[a-f0-9]{12}',artifact)):
            raise ValueError('保存预测编号不符')
        detail=self.materials.detail(calibration,'prediction:'+artifact)
        if detail['calibration_id']!=calibration or detail['key']!='prediction:'+artifact or detail['kind']!='prediction':
            raise ValueError('保存预测身份不符')
        return detail,sha256(json_bytes(detail['files']))

    def catalog(self):
        if self.materials is None:return []
        rows=[]
        for folder in sorted(self.materials.storage.glob('cal_*/pred_*')):
            if not folder.is_dir():continue
            row=dict(calibration_id=folder.parent.name,id=folder.name,readable=False)
            try:
                detail,binding=self._detail(folder.parent.name,folder.name)
                row.update(readable=True,material_name=detail['material_name'],source_binding_sha256=binding,
                    effective_bank_sha256=detail['effective_bank_sha256'],excluded_grades=detail['excluded_grades'],
                    training_grades=detail['training_grades'],loss_status=detail['loss_status'])
            except (OSError,ValueError,KeyError,TypeError) as error:row['error']=str(error)
            rows.append(row)
        return rows

    def collect(self,selection):
        if not isinstance(selection,dict) or set(selection)!={'calibration_id','id','source_binding_sha256'}:
            raise ValueError('保存预测选择不符')
        detail,before=self._detail(selection['calibration_id'],selection['id'])
        if before!=selection['source_binding_sha256']:
            raise ValueError('所选保存预测来源已变化，请刷新后重新选择')
        blob=self.materials.bundle(selection['calibration_id'],'prediction:'+selection['id']).getvalue()
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            manifest=json.loads(archive.read('manifest.json'))
            if manifest['files']!=detail['files'] or manifest['effective_bank_sha256']!=detail['effective_bank_sha256']:
                raise ValueError('保存预测在打包期间变化')
        _,after=self._detail(selection['calibration_id'],selection['id'])
        if after!=before:raise ValueError('保存预测在打包期间变化')
        index={key:detail[key] for key in ('material_name','parent_bank_sha256','effective_bank_sha256',
            'training_grades','excluded_grades','prediction_scope','H_axis','H_scale','loss_status')}
        index.update(id=selection['id'],calibration_id=selection['calibration_id'],source_binding_sha256=before,
            bundle_sha256=sha256(blob),new_predictions=0,new_native_solves=0,efficiency_enabled=False)
        return index,blob
