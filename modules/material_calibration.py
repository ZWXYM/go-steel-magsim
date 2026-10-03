"""Versioned, fixed-physical-H calibration, separate from legacy SW caches.

The loop midpoint is an engineering proxy, not a measured normal/virgin curve.
No Hc, remanence or loss targets are inferred by this protocol.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

MU0 = 4 * np.pi * 1e-7
VERSION = 'fixed_h_delta_v1'
PHYSICS_VERSION = 'cubic_sample_frame_v2'
FEATURE_SCALES = {'f_Goss': 0.5, 'theta_0_deg': 10.0,
                  'halfwidth_deg': 10.0, 'Si_content': 0.2}


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def validated_curve(H, B):
    h, b = np.asarray(H, dtype=float), np.asarray(B, dtype=float)
    if h.ndim != 1 or b.shape != h.shape or len(h) < 2:
        raise ValueError('Curve must contain equal length, one-dimensional H/B arrays')
    if not np.all(np.isfinite(h)) or not np.all(np.isfinite(b)):
        raise ValueError('Non-finite curve values')
    if h[0] < 0 or np.any(np.diff(h) <= 0):
        raise ValueError('Physical H must be nonnegative and strictly increasing')
    return h, b


def features(params):
    result = np.array([params[k] / s for k, s in FEATURE_SCALES.items()], dtype=float)
    if not np.all(np.isfinite(result)):
        raise ValueError('Missing/non-finite material parameters')
    return result


def guard_B(H, B, Msat=1.56e6):
    """Stable normal-curve prior: nonnegative, monotone, bounded polarization.

    Enforcing monotonic J = B - mu0*H also retains the vacuum slope at high
    field. Every imposed change is recorded; this is a model prior, not data.
    """
    h, b = validated_curve(H, B)
    polarization = np.clip(b - MU0 * h, 0, MU0 * Msat)
    polarization = np.maximum.accumulate(polarization)
    result = MU0 * h + polarization
    if h[0] == 0:
        result[0] = 0.0
    return result, {'max_guard_change_T': float(np.max(np.abs(result - b))),
                    'points_changed': int(np.sum(np.abs(result - b) > 1e-12)),
                    'guard_policy': 'nonnegative_monotone_J_bounded_by_Msat',
                    'Msat_A_per_m_prior': Msat}


def extract_loop_midpoint(table_path, H_grid, *, angle_deg, Msat=1.56e6):
    """Extract both explicitly labelled major-loop branches on the SAME H axis."""
    path = Path(table_path)
    header = path.read_text(encoding='utf-8').splitlines()[0].split('\t')
    branch_columns = [i for i, s in enumerate(header) if s.strip().startswith('branch ')]
    if len(branch_columns) != 1:
        raise ValueError(f'{path}: expected one explicit branch column')
    a = np.loadtxt(path, skiprows=1, ndmin=2)
    direction = np.array([np.cos(np.deg2rad(angle_deg)),
                          np.sin(np.deg2rad(angle_deg)), 0.0])
    H = a[:, 4:7] @ direction / MU0
    B = MU0 * (H + Msat * (a[:, 1:4] @ direction))
    grid = np.asarray(H_grid, dtype=float)
    branches = []
    for label in (-1, 1):
        mask = a[:, branch_columns[0]] == label
        if mask.sum() < 3:
            raise ValueError(f'{path}: incomplete branch {label}')
        hs, bs = H[mask], B[mask]
        order = np.argsort(hs, kind='stable')
        hs, bs = hs[order], bs[order]
        hs, index = np.unique(hs, return_index=True)
        bs = bs[index]
        # MuMax table uses float32 for B_ext; tolerate 0.01 A/m rounding.
        if grid[0] < hs[0] - .01 or grid[-1] > hs[-1] + .01:
            raise ValueError(f'{path}: requested H outside completed loop')
        branches.append(np.interp(grid, hs, bs))
    midpoint = (branches[0] + branches[1]) / 2
    if grid[0] == 0:
        midpoint[0] = 0.0  # odd engineering proxy convention; not measured Br
    guarded, report = guard_B(grid, midpoint, Msat)
    return {'H': grid.tolist(), 'B': guarded.tolist(),
            'B_midpoint_before_guard': midpoint.tolist(),
            'B_descending': branches[0].tolist(), 'B_ascending': branches[1].tolist(),
            'report': report, 'table_sha256': file_hash(path),
            'curve_definition': 'major_loop_midpoint_proxy'}


class CalibrationBank:
    def __init__(self, payload):
        if payload.get('calibration_version') != VERSION:
            raise ValueError('Unsupported calibration schema/version')
        if payload.get('physics_version') != PHYSICS_VERSION:
            raise ValueError('Incompatible simulation physics version')
        if payload.get('H_axis') != 'physical_A_per_m':
            raise ValueError('Calibration requires physical H in A/m')
        self.payload = payload
        self.anchors = payload['anchors']
        if not self.anchors:
            raise ValueError('Empty calibration bank')
        for anchor in self.anchors:
            features(anchor['params'])
            validated_curve(anchor['H'], anchor['delta_B'])
            if anchor['direction'] not in ('RD', 'TD'):
                raise ValueError('Invalid direction')

    @classmethod
    def load(cls, path):
        # Deliberately reload from disk: replacing a bank must affect the next call.
        return cls(json.loads(Path(path).read_text(encoding='utf-8')))

    @property
    def bank_sha256(self):
        return hashlib.sha256(json.dumps(self.payload, sort_keys=True,
                              separators=(',', ':')).encode()).hexdigest()

    def correct(self, H, B_sim, params, *, direction, exclude_grades=(),
                weight_cap=1.0, physics_version=PHYSICS_VERSION):
        if physics_version != self.payload['physics_version']:
            raise ValueError('Cannot apply cubic calibration to legacy simulation labels')
        if not 0 <= weight_cap <= 1:
            raise ValueError('weight_cap must be in [0, 1]')
        h, b = validated_curve(H, B_sim)
        p = features(params)
        eligible = [a for a in self.anchors if a['direction'] == direction
                    and a['grade'] not in set(exclude_grades)]
        if not eligible:
            raise ValueError(f'No eligible {direction} anchors after exclusion')
        if len({a['grade'] for a in eligible}) != len(eligible):
            raise ValueError('Duplicate grade/direction anchor')
        X = np.array([features(a['params']) for a in eligible])
        distances = np.linalg.norm(X - p, axis=1)
        exact = distances < 1e-12
        weights = exact / exact.sum() if np.any(exact) else 1 / np.maximum(distances, 1e-12)
        weights = weights / weights.sum()
        lower = max(a['H'][0] for a in eligible)
        upper = min(a['H'][-1] for a in eligible)
        if h[0] < lower or h[-1] > upper:
            raise ValueError(f'H outside bank support [{lower}, {upper}] A/m')
        delta = sum(w * np.interp(h, a['H'], a['delta_B']) for w, a in zip(weights, eligible))
        corrected, guard = guard_B(h, b + weight_cap * delta)
        if weight_cap == 0:
            corrected = b.copy()  # disabling correction is an exact identity
        return {'H': h.tolist(), 'B': corrected.tolist(), 'B_raw': b.tolist(),
                'delta_B': delta.tolist(), 'calibration_version': VERSION,
                'physics_version': physics_version, 'calibration_sha256': self.bank_sha256,
                'H_axis': 'physical_A_per_m', 'H_scale': 1.0,
                'anchor_weights': {a['grade']: float(w) for a, w in zip(eligible, weights)},
                'excluded_grades': sorted(set(exclude_grades)),
                'outside_feature_box': bool(np.any(p < X.min(axis=0) - 1e-12)
                                             or np.any(p > X.max(axis=0) + 1e-12)),
                'guard': guard, 'status': self.payload.get('status', 'experimental')}
