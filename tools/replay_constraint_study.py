"""Replay the fixed original-rule study from one verified saved dataset ZIP."""
import argparse
import copy
import io
import json
import sys
import zipfile
from pathlib import Path,PurePosixPath
from types import SimpleNamespace

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from modules.motor_constraint_studies import MotorConstraintStudies
from modules.system_data import sha256


def load_dataset(path):
    blob=Path(path).read_bytes()
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        names=archive.namelist()
        if len({name.casefold() for name in names})!=len(names):raise ValueError('数据包成员重复')
        if any(PurePosixPath(name).is_absolute() or '..' in PurePosixPath(name).parts or '\\' in name or ':' in name for name in names):
            raise ValueError('数据包路径不符')
        manifest=json.loads(archive.read('manifest.json'))
        if manifest.get('version')!='motor_engineering_dataset_bundle_v2' or set(manifest['files'])!=set(names)-{'manifest.json'}:
            raise ValueError('请选择单份原数据集包，不是整个系统工作包')
        for name,digest in manifest['files'].items():
            if sha256(archive.read(name))!=digest:raise ValueError('数据包成员哈希不符：'+name)
        data=json.loads(archive.read('dataset.json'));snapshot=json.loads(archive.read('source_snapshot.json'))
        if (snapshot['dataset']!=data['id'] or snapshot['model_version']!=data['version']
                or snapshot['measurement_protocol']!=data['measurement_protocol']):raise ValueError('数据集来源身份不符')
        sources={row['source']:row for row in snapshot['sources']}
        if set(sources)!=set(data['source_hashes']):raise ValueError('来源记录不完整')
        for name,digest in data['source_hashes'].items():
            row=sources[name]
            if row['sha256']!=digest:raise ValueError('来源哈希不符：'+name)
            if row['included'] and sha256(archive.read(row['member']))!=digest:raise ValueError('原文件字节不符：'+name)
    return data,sha256(blob)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset-bundle',type=Path,required=True)
    parser.add_argument('--reference',required=True)
    parser.add_argument('--storage',type=Path,required=True,help='Directory for new replay records; existing records remain')
    args=parser.parse_args()
    try:
        data,bundle_sha=load_dataset(args.dataset_bundle)
        def dataset(identifier):
            if identifier!=data['id']:raise ValueError('所选数据集不符')
            return copy.deepcopy(data)
        manager=SimpleNamespace(dataset=dataset)
        studies=MotorConstraintStudies(manager,args.storage)
        result=studies.run(dict(dataset=data['id'],reference=args.reference,source_hashes_sha256=studies.inputs(data['id'])['source_hashes_sha256']))
        print(json.dumps(dict(study=result['id'],folder=str(studies.path(result['id'])),dataset=data['id'],
            source_bundle_sha256=bundle_sha,configurations=result['configuration_count'],source_cases=result['source_case_count'],
            configurations_with_feasible_case=result['configurations_with_feasible_case'],
            original_default_candidate_count=result['original_default_candidate_count'],new_native_solves=0),ensure_ascii=False,indent=2))
    except (ValueError,KeyError,OSError,zipfile.BadZipFile) as error:parser.error(str(error))


if __name__=='__main__':main()
