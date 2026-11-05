"""Saved system data inventory and immutable, locally registered workpacks.

Registration copies a verified package; read endpoints never dispatch work or
rebuild scientific results. Manifest integrity is not physical validation.
"""
import hashlib
import io
import json
import re
import stat
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from flask import Blueprint, jsonify, render_template, send_file
from modules.motor_dataset_export import dataset_usage

MAX_PACKAGE_BYTES = 100 * 1024 * 1024
MAX_CONTENT_BYTES = 256 * 1024 * 1024
PACK_ID = re.compile(r'pack_[a-f0-9]{16}')
SHA256 = re.compile(r'[a-f0-9]{64}')


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False).encode('utf8')


def sha256(blob):
    return hashlib.sha256(blob).hexdigest()


def verify_package(blob):
    """Verify a bounded archive without extracting or altering any member."""
    if len(blob) > MAX_PACKAGE_BYTES:
        raise ValueError('数据包超过本地注册大小限制')
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            entries = archive.infolist()
            if not entries or sum(item.file_size for item in entries) > MAX_CONTENT_BYTES:
                raise ValueError('数据包内容为空或超出大小限制')
            names = set()
            for item in entries:
                name = item.filename
                path = PurePosixPath(name)
                if (not name or path.is_absolute() or '..' in path.parts or '\\' in name
                        or ':' in name or path.as_posix() != name or item.is_dir()
                        or name.casefold() in names or stat.S_ISLNK(item.external_attr >> 16)):
                    raise ValueError('数据包成员路径无效或重复')
                names.add(name.casefold())
            manifest = json.loads(archive.read('manifest.json'))
            if (not isinstance(manifest, dict) or manifest.get('version') not in ('system_data_workpack_v2', 'system_data_workpack_v3')
                    or manifest.get('private_only') is not True or not isinstance(manifest.get('files'), dict)):
                raise ValueError('数据包清单版本或私有标记不符')
            files = manifest['files']
            if set(archive.namelist()) - {'manifest.json'} != set(files):
                raise ValueError('数据包清单未完整覆盖成员')
            for name, expected in files.items():
                if not isinstance(expected, str) or not SHA256.fullmatch(expected) or sha256(archive.read(name)) != expected:
                    raise ValueError('数据包成员哈希不符：' + name)
            return dict(package_version=manifest['version'], member_count=len(entries),
                        content_bytes=sum(item.file_size for item in entries),
                        manifest_sha256=sha256(archive.read('manifest.json')),
                        integrity_verified=True, content_validation='manifest_integrity_only', private_only=True)
    except (zipfile.BadZipFile, KeyError, json.JSONDecodeError, UnicodeError, RuntimeError) as error:
        raise ValueError('数据包无法完整核验：' + str(error)) from error


class WorkpackStore:
    def __init__(self, storage):
        self.storage = Path(storage).resolve()

    def folder(self, artifact):
        if not isinstance(artifact, str) or not PACK_ID.fullmatch(artifact):
            raise ValueError('数据包编号不符')
        folder = (self.storage / artifact).resolve()
        if not folder.is_relative_to(self.storage):
            raise ValueError('数据包不属于当前存储')
        return folder

    def register(self, source, title, description=''):
        source = Path(source).resolve()
        with source.open('rb') as stream:
            blob = stream.read(MAX_PACKAGE_BYTES + 1)
        return self.register_bytes(blob, title, description, source_filename=source.name)

    def register_bytes(self, blob, title, description='', *, source_filename='generated_system_data.zip'):
        if not isinstance(title, str) or not title.strip() or len(title) > 200:
            raise ValueError('请填写不超过200字的数据包名称')
        if not isinstance(description, str) or len(description) > 4000:
            raise ValueError('数据包说明过长')
        verified = verify_package(blob)
        checksum = sha256(blob)
        artifact = 'pack_' + checksum[:16]
        folder = self.folder(artifact)
        if folder.exists():
            record, saved = self.read(artifact)
            if record['sha256'] != checksum or saved != blob:
                raise ValueError('同编号数据包内容冲突')
            return record
        record = dict(schema='system_data_registered_package_v1', id=artifact,
                      title=title.strip(), description=description, created_utc=datetime.now(timezone.utc).isoformat(),
                      source_filename=source_filename, download_filename=artifact+'.zip', sha256=checksum,
                      bytes=len(blob), **verified)
        self.storage.mkdir(parents=True, exist_ok=True)
        # A complete new folder becomes visible together. Existing artifacts are never replaced.
        with tempfile.TemporaryDirectory(prefix='_register_', dir=self.storage) as scratch:
            staged = Path(scratch) / artifact
            staged.mkdir()
            (staged / 'package.zip').write_bytes(blob)
            (staged / 'record.json').write_bytes(json_bytes(record))
            try:
                staged.rename(folder)
            except FileExistsError:
                existing, saved = self.read(artifact)
                if saved != blob:
                    raise ValueError('同编号数据包内容冲突')
                return existing
        return record

    def read(self, artifact):
        folder = self.folder(artifact)
        if any(not (folder / name).resolve().is_relative_to(folder) for name in ('record.json', 'package.zip')):
            raise ValueError('数据包文件不属于登记目录')
        record = json.loads((folder / 'record.json').read_text(encoding='utf8'))
        if (not isinstance(record, dict) or record.get('schema') != 'system_data_registered_package_v1'
                or record.get('id') != artifact or not isinstance(record.get('sha256'), str)
                or not SHA256.fullmatch(record['sha256']) or artifact != 'pack_'+record['sha256'][:16]
                or record.get('download_filename') != artifact+'.zip'
                or not isinstance(record.get('title'), str) or not record['title'].strip()
                or not isinstance(record.get('description'), str) or not isinstance(record.get('created_utc'), str)):
            raise ValueError('数据包登记身份不符')
        with (folder / 'package.zip').open('rb') as stream:
            blob = stream.read(MAX_PACKAGE_BYTES + 1)
        if sha256(blob) != record['sha256'] or len(blob) != record.get('bytes'):
            raise ValueError('已登记数据包字节已改变')
        verified = verify_package(blob)
        if any(record.get(key) != value for key, value in verified.items()):
            raise ValueError('数据包登记与实际内容不符')
        return record, blob

    def records(self):
        rows = []
        for folder in sorted(self.storage.glob('pack_*')):
            try:
                record, _ = self.read(folder.name)
                rows.append(dict(record, readable=True))
            except (OSError, ValueError, TypeError) as error:
                rows.append(dict(id=folder.name, readable=False, error=str(error)))
        return sorted(rows, key=lambda row: (row.get('created_utc', ''), row['id']), reverse=True)


class SystemData:
    def __init__(self, storage, optimization, batches=None, builder=None):
        self.storage, self.optimization = Path(storage).resolve(), optimization
        self.packages = WorkpackStore(self.storage / 'system_data')
        self.batches, self.builder = batches, builder

    def snapshot(self):
        datasets, calibrations, batches = [], [], []
        try:
            catalog = self.optimization.datasets()
        except (OSError, ValueError, KeyError, TypeError) as error:
            catalog = []
            datasets.append(dict(id='catalog', readable=False, error=str(error)))
        for item in catalog:
            try:
                data = self.optimization.dataset(item['id'])
                if data['id'] != item['id']:
                    raise ValueError('数据集身份不符')
                usage = dataset_usage(data)
                datasets.append(dict(id=data['id'], title=item['title'], readable=True,
                    version=data['version'], measurement_protocol=data['measurement_protocol'],
                    source_hashes_sha256=sha256(json_bytes(data['source_hashes'])),
                    row_count=len(data['rows']), original_accepted_count=sum(row.get('input_accepted') is True for row in data['rows']),
                    data_usage=usage, scope=data['scope']))
            except (OSError, ValueError, KeyError, TypeError) as error:
                datasets.append(dict(id=item['id'], title=item.get('title', item['id']), readable=False, error=str(error)))
        for path in sorted((self.storage / 'calibration').glob('cal_*/report.json'), reverse=True):
            try:
                blob = path.read_bytes()
                report = json.loads(blob)
                if not re.fullmatch(r'cal_[a-f0-9]{12}', path.parent.name) or report['id'] != path.parent.name:
                    raise ValueError('校准记录身份不符')
                calibrations.append(dict(readable=True, report_sha256=sha256(blob), **{key: report[key] for key in (
                    'id', 'created_utc', 'selected_grades', 'bank_sha256', 'calibration_version', 'H_axis',
                    'calibration_fit', 'material_holdout', 'evidence_status', 'independently_validated')}))
            except (OSError, ValueError, KeyError, TypeError) as error:
                calibrations.append(dict(id=path.parent.name, readable=False, error=str(error)))
        if self.batches:
            from modules.system_data_builder import batch_binding
            for row in self.batches.records():
                try:
                    available = row['status'] in ('completed', 'completed_with_failures') and any(item.get('result_available') for item in row['items'])
                    batches.append(dict(id=row['id'], status=row['status'], available=available,
                        source_binding_sha256=batch_binding(self.batches, row['id']),
                        result_count=sum(item.get('result_available') is True for item in row['items']),
                        item_count=len(row['items']), measurement_protocol=row['measurement_protocol']))
                except (OSError, ValueError, KeyError, TypeError) as error:
                    batches.append(dict(id=row['id'], available=False, error=str(error)))
        readable = [row for row in datasets if row['readable']]
        return dict(schema='go_steel_system_data_v1', created_utc=datetime.now(timezone.utc).isoformat(),
            datasets=datasets, calibrations=calibrations, calibrated_batches=batches, packages=self.packages.records(),
            optimizations=self.builder.optimization_exports.catalog() if self.builder is not None else [],
            package_build_available=self.builder is not None,
            counts=dict(dataset_count=len(readable), row_count=sum(row['row_count'] for row in readable),
                original_accepted_count=sum(row['original_accepted_count'] for row in readable),
                native_csv_accepted_count=sum(row['data_usage']['native_csv_accepted_count'] for row in readable),
                complete=len(readable)==len(datasets)),
            scope='保存记录清点；不同版本分别下载，重复记录/曲线节点不计作独立材料。完整包是注册时快照，不随当前任务变化。',
            new_native_solves=0, new_predictions=0, new_analyses=0)


def create_system_data_routes(data):
    bp = Blueprint('system_data', __name__)

    @bp.get('/system-data')
    def page():
        return render_template('system_data.html')

    @bp.get('/api/workbench/system-data')
    def snapshot():
        response = jsonify(data.snapshot())
        response.cache_control.no_store = True
        return response

    @bp.get('/api/workbench/system-data/index.json')
    def download_index():
        response = send_file(io.BytesIO(json_bytes(data.snapshot())), as_attachment=True,
            download_name='system_data_index.json', mimetype='application/json')
        response.cache_control.no_store = True
        return response

    @bp.get('/api/workbench/system-data/packages/<artifact>/download')
    def download_package(artifact):
        record, blob = data.packages.read(artifact)
        response = send_file(io.BytesIO(blob), as_attachment=True, download_name=record['download_filename'],
                             mimetype='application/zip', etag=record['sha256'])
        response.cache_control.no_store = True
        return response

    @bp.post('/api/workbench/system-data/packages')
    def build_package():
        from flask import request
        if data.builder is None:
            raise ValueError('当前实例未配置数据包生成器')
        record = data.builder.build(request.get_json())
        response = jsonify(record)
        response.cache_control.no_store = True
        return response, 201

    return bp
