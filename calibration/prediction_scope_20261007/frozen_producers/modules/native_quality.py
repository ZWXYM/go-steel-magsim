"""Source-verified CPU numerical screens; no solve, fitting or grain removal."""
import hashlib
import json
from pathlib import Path

import numpy as np

from modules.material_calibration import PHYSICS_VERSION, file_hash
from modules.native_loop_diagnostics import audit_table
from tools.analyze_sampling_convergence import read_ensemble
from tools.run_calibration_pilot import H_GRID

VERSION = 'native_quality_contract_v1'
GATES = dict(min_signed_endpoint_m_projection=.95, max_branch_inversion_error_T=.02,
             max_residual_torque_T=1e-5)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def seal(payload):
    return {**payload, 'quality_sha256': digest(payload)}


def unassessed_quality(material_id='unspecified'):
    return seal(dict(contract_version=VERSION, material_id=material_id,
        status='unassessed', verified_native_source=False, strict_training_eligible=False,
        reason='No matching source-verified native RD/TD tables; uploaded quality claims are not certification',
        independently_validated=False, screening_is_not_material_validation=True))


def validate_quality(quality):
    if not isinstance(quality, dict) or quality.get('contract_version') != VERSION:
        raise ValueError('原生质量合同版本不符')
    payload = {k:v for k,v in quality.items() if k != 'quality_sha256'}
    if quality.get('quality_sha256') != digest(payload):
        raise ValueError('原生质量记录哈希不符')
    if quality['status'] not in ('unassessed', 'numerical_screen_failed',
                                  'convergence_unassessed', 'numerical_screen_passed'):
        raise ValueError('未知原生质量状态')
    if quality.get('strict_training_eligible') != (quality['status'] == 'numerical_screen_passed'):
        raise ValueError('原生质量状态与训练资格不符')
    if quality['status'] == 'numerical_screen_passed' and not quality.get('verified_native_source'):
        raise ValueError('未核实原生来源不能通过数值筛查')
    return quality


def audit_pilot(pilot, grades):
    """Re-read actual source bytes; source claims or cached UI labels are insufficient."""
    pilot = Path(pilot)
    manifest_path = pilot/'manifest.json'
    before = file_hash(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if manifest['physics_version'] != PHYSICS_VERSION:
        raise ValueError('原生审计物理版本不符')
    project = Path(__file__).resolve().parents[1]
    producers = ('modules/native_quality.py', 'modules/native_loop_diagnostics.py',
        'modules/material_calibration.py', 'tools/analyze_sampling_convergence.py', 'tools/run_calibration_pilot.py')
    producer_sha256 = {name:file_hash(project/name) for name in producers}
    result = {}
    for grade in grades:
        if grade not in {m['grade'] for m in manifest['materials']}:
            raise ValueError('原生审计材料不在 manifest 中')
        directions = {}
        sources = {}
        for direction in ('RD', 'TD'):
            ensemble, source = read_ensemble(pilot, grade, direction)
            jobs = sorted((j for j in manifest['jobs'] if j['grade'] == grade and j['direction'] == direction),
                          key=lambda j:j['grain_id'])
            if len(jobs) != len(ensemble):
                raise ValueError('审计要求完整原生晶粒集合')
            grains = []
            for i, job in enumerate(jobs):
                metric = audit_table(pilot/job['output']/'table.txt', H_GRID, angle_deg=job['angle'])
                if not np.allclose(ensemble[i], metric['guarded_midpoint_B_T'], atol=1e-12, rtol=0):
                    raise ValueError('审计与原生聚合合同不符')
                grains.append(dict(grain_id=job['grain_id'], table_sha256=metric['table_sha256'],
                    min_signed_endpoint_m_projection=metric['min_signed_endpoint_m_projection'],
                    max_branch_inversion_error_T=metric['max_branch_inversion_error_T'],
                    native_unforced_midpoint_H0_T=metric['native_unforced_midpoint_H0_T'],
                    max_residual_torque_T=metric['max_residual_torque_T']))
            directions[direction] = dict(grains=len(grains), grain_details=grains, guarded_mean_B_T=ensemble.mean(axis=0).tolist(),
                endpoint_failed_grain_ids=[g['grain_id'] for g in grains if g['min_signed_endpoint_m_projection'] < GATES['min_signed_endpoint_m_projection']],
                inversion_failed_grain_ids=[g['grain_id'] for g in grains if g['max_branch_inversion_error_T'] > GATES['max_branch_inversion_error_T']],
                torque_unknown_grain_ids=[g['grain_id'] for g in grains if g['max_residual_torque_T'] is None],
                torque_failed_grain_ids=[g['grain_id'] for g in grains if g['max_residual_torque_T'] is not None and g['max_residual_torque_T'] > GATES['max_residual_torque_T']],
                max_abs_unforced_midpoint_H0_T=max(abs(g['native_unforced_midpoint_H0_T']) for g in grains))
            sources[direction] = source
        failed = any(d['endpoint_failed_grain_ids'] or d['inversion_failed_grain_ids'] or d['torque_failed_grain_ids'] for d in directions.values())
        unknown = any(d['torque_unknown_grain_ids'] for d in directions.values())
        status = 'numerical_screen_failed' if failed else ('convergence_unassessed' if unknown else 'numerical_screen_passed')
        result[grade] = seal(dict(contract_version=VERSION, material_id=grade, status=status,
            verified_native_source=True, strict_training_eligible=not failed and not unknown,
            source_manifest_sha256=before, producer_sha256=producer_sha256,
            physics_version=manifest['physics_version'],
            texture_sampling_version=manifest.get('texture_sampling_version', 'legacy_multi_peak_importance_v1'),
            H_axis='physical_A_per_m', gates=GATES, directions=directions, source_native_evidence=sources,
            independently_validated=False, screening_is_not_material_validation=True,
            native_H0_is_not_measured_Br=True, no_grain_removed=True, no_offset_correction_applied=True))
    if file_hash(manifest_path) != before:
        raise ValueError('审计期间 manifest 改变')
    return result
