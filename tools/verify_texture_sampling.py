"""Audit active axes, iid prefix invariance and Haar background moments."""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation
from scipy.stats import kstest
from orix.quaternion import Orientation

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from modules.texture_sampling import sample_texture, SAMPLING_VERSION
from modules.mx3_generator import euler_to_crystal_axes
from modules.material_calibration import file_hash


def verify(n_background=100000):
    small, small_rows, sm = sample_texture(.79, 6, 8, 8, 20261007)
    large, large_rows, lm = sample_texture(.79, 6, 64, 8, 20261007)
    _, _, alternate = sample_texture(.79, 6, 64, 8, 20261008)
    ideal, _, _ = sample_texture(1, 0, 4, 0, 1)
    ideal_matrix = np.column_stack(euler_to_crystal_axes(*ideal[0]))
    # Cubic-equivalent Goss {011}<100>: [100] lies in RD and ND is [011].
    ideal_error = max(np.max(np.abs(ideal_matrix[:, 0] - [1, 0, 0])),
                      np.max(np.abs(ideal_matrix.T @ [0, 0, 1] - np.array([0, 1, 1])/np.sqrt(2))))
    native = np.array([np.column_stack(euler_to_crystal_axes(*e)) for e in large])
    passive = Orientation.from_euler(np.deg2rad(large)).to_matrix()
    orix_error = float(np.max(np.abs(native - passive.transpose(0, 2, 1))))
    quaternion = np.array([[r[k] for k in ('qx', 'qy', 'qz', 'qw')] for r in large_rows])
    quaternion_error = float(np.max(np.abs(native - Rotation.from_quat(quaternion).as_matrix())))
    background, _, bg_metadata = sample_texture(0, 0, n_background, 8, 20261004)
    matrices = Rotation.from_euler('ZXZ', background, degrees=True).as_matrix()
    mean_error = float(np.max(np.abs(matrices.mean(axis=0))))
    moment_error = float(np.max(np.abs((matrices**2).mean(axis=0) - 1/3)))
    phi_ks = float(kstest(np.cos(np.deg2rad(background[:, 1])), 'uniform', args=(-1, 2)).statistic)
    checks = {'prefix_eulers_exact': np.array_equal(small, large[:8]),
              'prefix_provenance_exact': small_rows == large_rows[:8],
              'distribution_seed_and_N_independent': sm['distribution_sha256'] == lm['distribution_sha256'] == alternate['distribution_sha256'],
              'ideal_Goss_axes': bool(ideal_error < 1e-12),
              'orix_passive_to_native_active_axes': orix_error < 1e-12,
              'stored_quaternion_to_native_axes': quaternion_error < 1e-12,
              'Haar_axis_mean_check': mean_error < .006,
              'Haar_axis_second_moment_check': moment_error < .006,
              'cos_Phi_uniform_check': phi_ks < .006}
    return {'status': 'passed' if all(checks.values()) else 'failed',
            'texture_sampling_version': SAMPLING_VERSION,
            'sampler_source_sha256': file_hash(PROJECT / 'modules/texture_sampling.py'),
            'verification_source_sha256': file_hash(Path(__file__)), 'checks': checks,
            'background_sample_count': n_background, 'ideal_Goss_axis_max_error': float(ideal_error),
            'orix_axis_max_error': orix_error, 'quaternion_axis_max_error': quaternion_error,
            'background_axis_mean_max_abs': mean_error,
            'background_axis_second_moment_max_error': moment_error, 'cos_Phi_KS_statistic': phi_ks,
            'mixture_metadata': lm, 'background_metadata': bg_metadata,
            'limitations': ['Algorithm and geometric convention checks only, not measured ODF validation',
                            'Moment and marginal checks supplement the Haar construction; they are not a proof from finite samples',
                            'Goss radial scatter remains an explicit prior, not measured ODF HWHM']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = verify()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result['status'] == 'passed' else 1)
