"""Freeze saved BH packages into motor preparation copies; no native dispatch."""
import io
import json
import re
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from modules.material_library import sha
from modules.motor_workbench import inspect_template, write_json
from modules.maxwell_material_transport import load_contract


class CalibratedMotorPreparation:
    def __init__(self, library, motor, storage):
        self.library, self.motor = library, motor
        self.storage = Path(storage).resolve()
        self.storage.mkdir(parents=True, exist_ok=True)

    def path(self, artifact):
        if not isinstance(artifact, str) or not re.fullmatch(r'calmotor_[0-9a-f]{12}', artifact):
            raise ValueError('无效校准材料电机副本')
        path = (self.storage / artifact).resolve()
        if not path.is_relative_to(self.storage):
            raise ValueError('副本超出准备目录')
        return path

    def prepare(self, data):
        if not isinstance(data, dict) or set(data) - {'calibration', 'key', 'cores'}:
            raise ValueError('请选择保存材料和核数')
        cores = data.get('cores', 2)
        if type(cores) is not int or not 1 <= cores <= 32:
            raise ValueError('核数需为1–32整数')
        # The bundle verifies source curves, both banks, exclusions and file hashes.
        bundle = self.library.bundle(data.get('calibration'), data.get('key')).getvalue()
        with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
            source = json.loads(archive.read('manifest.json'))
            files = {n: archive.read(n) for n in archive.namelist()}
        template_bytes = self.motor.template.read_bytes()
        text = template_bytes.decode('utf-8')
        patched, quality = inspect_template(text, source['material_name'])
        artifact = 'calmotor_' + uuid.uuid4().hex[:12]
        folder = self.path(artifact)
        folder.mkdir()
        # Keep separate from scan_* queues; no worker can discover this preparation.
        (folder / 'package.zip').write_bytes(bundle)
        package = folder / 'package'
        package.mkdir()
        for name, blob in files.items():
            if Path(name).name != name or name not in set(source['bundle_files']) | {'manifest.json'}:
                raise ValueError('材料包文件名无效')
            (package / name).write_bytes(blob)
        (package / 'material.metadata.json').write_bytes(files['metadata.json'])
        contract = load_contract(package / 'material.amat')
        (folder / 'motor.aedt').write_bytes(patched.encode('utf-8'))
        manifest = dict(id=artifact, created_utc=datetime.now(timezone.utc).isoformat(),
            status='awaiting_native_material_import', calibration_id=source['calibration_id'],
            material_key=source['key'], material_name=source['material_name'], cores=cores,
            parent_bank_sha256=source['parent_bank_sha256'], effective_bank_sha256=source['effective_bank_sha256'],
            training_grades=source['training_grades'], excluded_grades=source['excluded_grades'],
            source_package_sha256=sha(bundle), H_axis=source['H_axis'], H_scale=source['H_scale'],
            material_contract=contract, template_sha256=sha(template_bytes),
            geometry_quality=quality, material_assignment_scope='48 insert references patched; saved native material definition not imported yet',
            native_import_completed=False, native_submission_allowed=False, efficiency_enabled=False,
            optimization_ranking_enabled=False, original_rotor_stator_loss_objects_preserved=True,
            new_native_solves=0, result_reuse=False,
            files={str(p.relative_to(folder)).replace('\\','/'):sha(p.read_bytes()) for p in folder.rglob('*') if p.is_file()})
        write_json(folder / 'manifest.json', manifest)
        return self.get(artifact)

    def get(self, artifact):
        folder = self.path(artifact)
        manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
        if (manifest.get('id') != artifact or manifest.get('status') != 'awaiting_native_material_import' or
                any(manifest.get(k) is not False for k in ('native_submission_allowed', 'native_import_completed',
                                                          'efficiency_enabled', 'optimization_ranking_enabled'))):
            raise ValueError('准备记录身份或执行范围不符')
        for name, digest in manifest['files'].items():
            p = (folder / name).resolve()
            if not p.is_relative_to(folder) or not p.is_file() or sha(p.read_bytes()) != digest:
                raise ValueError('准备副本文件缺失或改变：' + name)
        contract = load_contract(folder / 'package/material.amat')
        if contract != manifest['material_contract']:
            raise ValueError('保存的材料合同不符')
        return manifest

    def records(self):
        rows = []
        for folder in sorted(self.storage.glob('calmotor_*'), reverse=True):
            try:
                rows.append(self.get(folder.name))
            except (ValueError, KeyError, OSError) as error:
                rows.append(dict(id=folder.name, status='invalid', error=str(error)))
        return sorted(rows, key=lambda r:r.get('created_utc',''), reverse=True)

    def download(self, artifact, name):
        if name not in ('motor.aedt', 'package.zip', 'manifest.json'):
            raise ValueError('未知准备文件')
        self.get(artifact)
        return self.path(artifact) / name
