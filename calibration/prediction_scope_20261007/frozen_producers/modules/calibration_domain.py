"""Training-parameter support, independent of reference curves and target errors."""
from itertools import combinations
import numpy as np
from modules.material_calibration import features, FEATURE_SCALES

VERSION = 'joint_parameter_support_v1'
TOLERANCE = 1e-8


def assess_parameter_support(samples, params):
    """Measure joint support for the small calibration bank using convex geometry.

    Axis-by-axis ranges can contain parameter combinations never supported by
    the anchors. Enumerating simplex faces gives the exact nearest point for
    these small banks, including collinear and duplicate parameter vectors.
    No B-H value, target error or chemical measurement is used here.
    """
    if not samples:
        raise ValueError('Parameter support requires training anchors')
    rows = sorted(samples, key=lambda s: s['grade'])
    unique = {}
    for row in rows:
        vector = tuple(features(row['params']))
        unique.setdefault(vector, []).append(row['grade'])
    X = np.array(list(unique), dtype=float)
    p = features(params)
    distances = np.linalg.norm(X-p, axis=1)
    centered = X-X[0]
    _, singular, V = np.linalg.svd(centered, full_matrices=False)
    threshold = max(centered.shape)*np.finfo(float).eps*max(float(singular.max()), 1.)
    rank = int(np.sum(singular > threshold))
    span = V[:rank]
    offset = p-X[0]
    affine_distance = float(np.linalg.norm(offset-span.T@(span@offset)))
    result = dict(version=VERSION, feature_scales=dict(FEATURE_SCALES),
        tolerance=TOLERANCE, training_grades=sorted({s['grade'] for s in rows}),
        unique_parameter_count=len(X), affine_rank=rank,
        inside_marginal_parameter_box=bool(np.all(p >= X.min(axis=0)-TOLERANCE)
                                          and np.all(p <= X.max(axis=0)+TOLERANCE)),
        nearest_scaled_parameter_distance=float(distances.min()),
        scaled_distance_to_affine_span=affine_distance,
        matched_anchor_grades=sorted({g for v, grades in unique.items()
                                      if np.linalg.norm(np.array(v)-p) <= TOLERANCE for g in grades}),
        independent_material_accuracy_verified=False,
        composition_or_texture_effects_identified=False,
        scope='Geometry of recorded training parameters; no predictive interval or accuracy guarantee')
    # Explicitly bound the work. Large future banks need a dedicated convex
    # optimizer; absence of this assessment must not be labelled interpolation.
    if len(X) > 12:
        return dict(result, joint_support_evaluated=False, inside_joint_parameter_support=None,
                    reason='Small-bank support assessment is limited to 12 unique anchors')
    best_distance = float('inf'); best_point = None
    for size in range(1, min(len(X), rank+1)+1):
        for indices in combinations(range(len(X)), size):
            simplex = X[list(indices)]
            if size == 1:
                weight = np.array([1.])
            else:
                z = np.linalg.lstsq((simplex[1:]-simplex[0]).T, p-simplex[0], rcond=None)[0]
                weight = np.r_[1-z.sum(), z]
            if np.min(weight) < -1e-10:
                continue
            weight = np.maximum(weight, 0); weight /= weight.sum()
            point = weight@simplex
            distance = float(np.linalg.norm(point-p))
            if distance < best_distance:
                best_distance = distance; best_point = point
    if best_point is None:
        raise ValueError('No feasible convex-support projection')
    return dict(result, joint_support_evaluated=True,
                scaled_distance_to_convex_support=best_distance,
                nearest_supported_parameter_vector=best_point.tolist(),
                inside_joint_parameter_support=best_distance <= TOLERANCE)


def bank_parameter_support(bank, params):
    rows = bank.payload.get('training_samples', bank.payload.get('anchors', []))
    return assess_parameter_support(rows, params)
