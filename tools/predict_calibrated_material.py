"""Apply an explicit calibration bank to a versioned raw simulation pair."""
import argparse
import json
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / 'modules'))

from modules.material_calibration import CalibrationBank, PHYSICS_VERSION, file_hash
from modules.maxwell_exporter import export_calibrated_pair
from modules.texture_sampling import LEGACY_SAMPLING_VERSION


def predict_pair(raw_pair, bank, exclude_grades=()):
    version = raw_pair['simulation_physics_version']
    sampling = raw_pair.get('texture_sampling_version', LEGACY_SAMPLING_VERSION)
    if version != PHYSICS_VERSION:
        raise ValueError('Raw simulation has an incompatible physics version')
    result = {direction: bank.correct(raw_pair[direction]['H'], raw_pair[direction]['B'],
        raw_pair['params'], direction=direction, exclude_grades=exclude_grades,
        physics_version=version, texture_sampling_version=sampling) for direction in ('RD', 'TD')}
    result.update({'material_id': raw_pair['material_id'], 'params': raw_pair['params'],
                   'simulation_physics_version': version, 'texture_sampling_version': sampling,
                   'calibration_sha256': bank.bank_sha256})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bank', type=Path, required=True)
    parser.add_argument('--raw-pair', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--exclude-grade', action='append', default=[])
    parser.add_argument('--name', default='Calibrated_BH_Pilot')
    args = parser.parse_args()
    raw = json.loads(args.raw_pair.read_text(encoding='utf-8'))
    result = predict_pair(raw, CalibrationBank.load(args.bank), args.exclude_grade)
    result['raw_pair_sha256'] = file_hash(args.raw_pair)
    result['bank_file_sha256'] = file_hash(args.bank)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.output_dir / f'{args.name}.json'
    amat = export_calibrated_pair(result['RD'], result['TD'], args.name,
        thickness_mm=raw['thickness_mm'], export_dir=str(args.output_dir))
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'prediction': str(json_path), 'amat': amat,
                       'status': 'experimental_BH_only', 'H_axis': 'physical_A_per_m'}, indent=2))


if __name__ == '__main__':
    main()
