"""Versioned Goss scatter + Haar SO(3) background, with stable iid prefixes.

This is a distribution prior, not an EBSD measurement. The Goss angle is
half-normal and clipped at 3*sigma (including an atom at the cap), retaining
the historical radial scatter rule. It is not a Gaussian density w.r.t. Haar
measure and sigma is not a measured ODF half-width at half maximum.
"""
import hashlib
import json

import numpy as np
import scipy
from scipy.spatial.transform import Rotation

LEGACY_SAMPLING_VERSION = 'legacy_multi_peak_importance_v1'
SAMPLING_VERSION = 'goss_haar_iid_prefix_v2'


def sample_texture(f_Goss, theta_0_deg, n_grains, halfwidth_deg, seed):
    parameters = np.array([f_Goss, theta_0_deg, halfwidth_deg], dtype=float)
    if not np.all(np.isfinite(parameters)) or not 0 <= f_Goss <= 1:
        raise ValueError('Finite texture parameters and f_Goss in [0, 1] required')
    if not 0 <= halfwidth_deg <= 60:
        raise ValueError('Scatter sigma must lie in [0, 60] degrees')
    if not isinstance(n_grains, (int, np.integer)) or isinstance(n_grains, bool) or n_grains < 1:
        raise ValueError('Positive integer grain count required')
    if not isinstance(seed, (int, np.integer)) or isinstance(seed, bool) or seed < 0:
        raise ValueError('Nonnegative integer seed required')
    # Separate streams always draw ALL n rows, even for unused components.
    # Thus component counts, vectorization size and global RNG cannot alter
    # the first k grains of any larger ensemble with identical parameters.
    def stream(channel):
        return np.random.Generator(np.random.PCG64(np.random.SeedSequence([int(seed), channel])))

    choices = stream(0).random(n_grains)
    is_goss = choices < f_Goss
    axes = stream(1).standard_normal((n_grains, 3))
    axes /= np.linalg.norm(axes, axis=1, keepdims=True)
    angles = np.minimum(np.abs(stream(2).standard_normal(n_grains)), 3) * np.deg2rad(halfwidth_deg)
    scatter = Rotation.from_rotvec(axes * angles[:, None])
    base = Rotation.from_euler('ZXZ', [0, 45, 0], degrees=True)
    goss = scatter * base
    # Normalized four-dimensional isotropic Gaussian has uniform measure on
    # S^3; q and -q represent the same rotation, giving Haar measure on SO(3).
    quaternion = stream(3).standard_normal((n_grains, 4))
    quaternion /= np.linalg.norm(quaternion, axis=1, keepdims=True)
    mixed = goss.as_quat()
    mixed[~is_goss] = quaternion[~is_goss]
    # Active crystal -> sample convention matches mx3_generator's axes.
    # Rotate the complete realization about sample ND, keeping the analytic
    # background uniform and the common-random-number geometry consistent.
    nd = Rotation.from_euler('z', theta_0_deg, degrees=True)
    rotations = nd * Rotation.from_quat(mixed)
    eulers = rotations.as_euler('ZXZ', degrees=True)
    eulers[:, [0, 2]] %= 360
    quaternions = rotations.as_quat()  # xyzw
    rows = [{'grain_id': i + 1, 'component': 'goss' if is_goss[i] else 'haar_background',
             'phi1_deg': float(e[0]), 'Phi_deg': float(e[1]), 'phi2_deg': float(e[2]),
             'qx': float(q[0]), 'qy': float(q[1]), 'qz': float(q[2]), 'qw': float(q[3]),
             'goss_scatter_angle_deg': float(np.rad2deg(angles[i])) if is_goss[i] else None}
            for i, (e, q) in enumerate(zip(eulers, quaternions))]
    distribution = {'texture_sampling_version': SAMPLING_VERSION,
        'f_Goss_prior': float(f_Goss), 'theta_0_deg': float(theta_0_deg),
        'goss_center_euler_deg': [0, 45, 0],
        'goss_scatter_sigma_deg_prior': float(halfwidth_deg),
        'goss_scatter': 'uniform_S2_axis_half_normal_angle_clipped_at_3sigma',
        'background': 'Haar_uniform_SO3_normalized_isotropic_4D_Gaussian',
        'mixture': 'iid_Bernoulli_equal_volume_grains',
        'orientation_convention': 'active_crystal_to_sample_intrinsic_ZXZ_degrees'}
    digest = hashlib.sha256(json.dumps(distribution, sort_keys=True,
                            separators=(',', ':')).encode()).hexdigest()
    metadata = {'distribution': distribution, 'distribution_sha256': digest,
        'seed': int(seed), 'n_grains': int(n_grains), 'rng': 'PCG64_SeedSequence_seed_channel',
        'numpy_version': np.__version__, 'scipy_version': scipy.__version__,
        'goss_count': int(is_goss.sum()), 'background_count': int((~is_goss).sum()),
        'prefix_policy': 'same_parameters_and_seed_same_first_k_grains',
        'ODF_status': 'parametric_prior_not_independent_EBSD'}
    return eulers, rows, metadata
