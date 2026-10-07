"""Apply an explicit calibration bank to a versioned raw simulation pair."""
import argparse
import json
import re
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / 'modules'))

from modules.material_calibration import CalibrationBank, PHYSICS_VERSION, file_hash
from modules.calibration_transfer import load_bank
from modules.maxwell_exporter import export_calibrated_pair
from modules.texture_sampling import LEGACY_SAMPLING_VERSION
from modules.calibration_prediction import predict_pair


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bank', type=Path, required=True)
    parser.add_argument('--raw-pair', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--exclude-grade', action='append', default=[])
    parser.add_argument('--name', default='Calibrated_BH_Pilot')
    args = parser.parse_args()
    if args.output_dir.exists():
        raise ValueError('Output directory must be new; existing predictions are preserved')
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*',args.name):
        raise ValueError('Prediction name must be a safe material identifier')
    raw = json.loads(args.raw_pair.read_text(encoding='utf-8'))
    result = predict_pair(raw, load_bank(args.bank), args.exclude_grade,include_bank=True)
    effective_bank=result.pop('_effective_bank_payload')
    result['raw_pair_sha256'] = file_hash(args.raw_pair)
    result['bank_file_sha256'] = file_hash(args.bank)
    args.output_dir.mkdir(parents=True, exist_ok=False)
    (args.output_dir/'effective_bank.json').write_text(json.dumps(effective_bank,ensure_ascii=False,indent=2),encoding='utf-8')
    json_path = args.output_dir / f'{args.name}.json'
    amat = export_calibrated_pair(result['RD'], result['TD'], args.name,
        thickness_mm=raw['thickness_mm'], export_dir=str(args.output_dir))
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'prediction': str(json_path), 'amat': amat,
                       'status': 'experimental_BH_only', 'H_axis': 'physical_A_per_m'}, indent=2))


if __name__ == '__main__':
    main()
