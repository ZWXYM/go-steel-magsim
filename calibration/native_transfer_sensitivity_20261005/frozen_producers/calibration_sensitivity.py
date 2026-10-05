"""Conditional grain sensitivity of FROZEN material-holdout predictors.

This diagnostic never selects candidates, trains a new bank, or exports a
replacement material. Quantile ranges describe resampling the observed grains;
they are not confidence intervals for physical materials or new seeds.
"""
from __future__ import annotations

import copy
import numpy as np

from modules.calibration_transfer import TransferBank, digest, error_metrics, weights
from modules.material_calibration import guard_B, validated_curve

VERSION = 'fixed_h_native_sensitivity_v1'
DIRECTIONS = ('RD', 'TD')
MODES = ('all_materials', 'query_only', 'training_only')


def protocol():
    return dict(diagnostic_version=VERSION, resamples=512, random_seed=20261005,
        rng='numpy Generator(PCG64)', quantiles=[.025, .5, .975],
        quantile_method='linear',
        model='read frozen outer banks; no candidate selection or tuning',
        sampling='equal-volume empirical grains, with replacement within material',
        pairing='one grain-index draw shared by RD/TD; independent draws across materials',
        perturbations=list(MODES),
        decomposition='reference interpolation + beta*(query native - weighted training native)',
        controls=['same frozen weights with beta=0 (diagnostic only)',
            'same frozen weights/beta with pre-grain-guard midpoint (diagnostic only)'],
        guard='existing per-grain guard before mean; existing final guard after prediction; record both final-guarded and pre-final-guard sensitivity',
        scoring='outer reference used only after predictions; unchanged H>=100/B800 metrics',
        H_axis='physical_A_per_m', conditional_on_observed_grains=True,
        calibrated_material_confidence_interval=False, candidate_selection_uncertainty=False,
        prospective_external_validation=False, new_native_solves=0,
        limitations=['n8 single-seed legacy distribution, not fixed-ODF multi-seed evidence',
            'processed reference uncertainty and material-parameter error not propagated',
            '512 computational draws are not 512 independent material experiments',
            'fixed-weights controls are retrospective diagnostics, not deployable alternatives'])


def checked_ensembles(ensembles):
    """Require explicit identical physical H and paired Euler grain identities."""
    result = copy.deepcopy(ensembles)
    common_h = None
    for grade in sorted(result):
        item = result[grade]
        h, _ = validated_curve(item['H'], np.zeros(len(item['H'])))
        if common_h is None:
            common_h = h
        if not np.array_equal(h, common_h):
            raise ValueError('Ensembles must share the exact physical H grid')
        identity = None
        for direction in DIRECTIONS:
            c = item[direction]
            ids = c['grain_ids']
            if len(ids) < 2 or ids != list(range(1, len(ids) + 1)):
                raise ValueError('Complete ordered grain IDs are required')
            current = (ids, c['orientations_sha256'])
            if identity is not None and current != identity:
                raise ValueError('RD/TD must have the same grain IDs and Euler provenance')
            identity = current
            for key in ('guarded_B', 'midpoint_B'):
                a = np.asarray(c[key], dtype=float)
                if a.shape != (len(ids), len(h)) or not np.all(np.isfinite(a)):
                    raise ValueError('Incomplete/non-finite per-grain curves')
                c[key] = a
    if not result:
        raise ValueError('No native ensembles')
    return result


def paired_draws(ensembles, count=None, seed=None):
    ensembles = checked_ensembles(ensembles)
    p = protocol()
    count = p['resamples'] if count is None else count
    seed = p['random_seed'] if seed is None else seed
    if isinstance(count, bool) or not isinstance(count, int) or count < 2:
        raise ValueError('At least two integer resampling draws are required')
    rng = np.random.Generator(np.random.PCG64(seed))
    return {grade: rng.integers(0, len(ensembles[grade]['RD']['grain_ids']),
            size=(count, len(ensembles[grade]['RD']['grain_ids'])))
        for grade in sorted(ensembles)}


def _guard_rows(h, a):
    return np.array([guard_B(h, row)[0] for row in a])


def _range(a, axis=0):
    q = np.quantile(a, protocol()['quantiles'], axis=axis, method='linear')
    return dict(p025=q[0].tolist(), median=q[1].tolist(), p975=q[2].tolist())


def audit_fold(target, bank, ensembles, draws):
    """Hold weights and beta fixed; target reference affects ONLY error scores."""
    if not isinstance(bank, TransferBank):
        raise ValueError('A frozen v2 transfer bank is required')
    grade = target['grade']
    training_grades = [s['grade'] for s in bank.samples]
    if grade in training_grades:
        raise ValueError('Held-out target occurs in the frozen training bank')
    expected = set(training_grades + [grade])
    if not expected <= set(ensembles) or not expected <= set(draws):
        raise ValueError('Missing target/training native ensemble or draws')
    count = None
    for g in sorted(expected):
        indices = np.asarray(draws[g])
        n = len(ensembles[g]['RD']['grain_ids'])
        if (indices.ndim != 2 or indices.shape[1] != n or indices.shape[0] < 2
                or not np.issubdtype(indices.dtype, np.integer)
                or np.any(indices < 0) or np.any(indices >= n)):
            raise ValueError('Invalid paired resampling indices')
        if count is not None and count != len(indices):
            raise ValueError('Material draw counts differ')
        count = len(indices)
    w, domain = weights(bank.samples, target['params'], bank.candidate)
    beta = bank.candidate['native_beta']
    output = []
    for direction in DIRECTIONS:
        c = target['curves'][direction]
        h, raw = validated_curve(c['H'], c['raw_B'])
        required_samples = {s['grade']: s for s in bank.samples} | {grade: target}
        for g, sample in required_samples.items():
            if not np.array_equal(ensembles[g]['H'], h) or sample['curves'][direction]['H'] != c['H']:
                raise ValueError('Frozen bank/target/ensembles differ in physical H')
            if not np.allclose(ensembles[g][direction]['guarded_B'].mean(axis=0),
                    sample['curves'][direction]['raw_B'], atol=1e-12, rtol=0):
                raise ValueError('Per-grain aggregate differs from frozen native curve')
        reference_part = sum(v * np.asarray(s['curves'][direction]['reference_B'])
            for v, s in zip(w, bank.samples))
        train_mean = sum(v * ensembles[s['grade']][direction]['guarded_B'].mean(axis=0)
            for v, s in zip(w, bank.samples))
        native_part = beta * (raw - train_mean)
        proposed = reference_part + native_part
        base, imposed = guard_B(h, proposed)
        # Verify the decomposition against the actual deployed frozen-bank path.
        actual = bank.correct(h, raw, target['params'], direction=direction,
            texture_sampling_version=bank.texture_sampling_version)
        if not np.allclose(base, actual['B'], atol=1e-12, rtol=0):
            raise ValueError('Fixed-weights decomposition differs from frozen predictor')
        control, _ = guard_B(h, reference_part)
        midpoint_part = beta * (ensembles[grade][direction]['midpoint_B'].mean(axis=0)
            - sum(v * ensembles[s['grade']][direction]['midpoint_B'].mean(axis=0)
                for v, s in zip(w, bank.samples)))
        midpoint_control, _ = guard_B(h, reference_part + midpoint_part)
        resampled = {g: ensembles[g][direction]['guarded_B'][draws[g]].mean(axis=1)
            for g in expected}
        resampled_training = sum(v * resampled[s['grade']] for v, s in zip(w, bank.samples))
        modes = {}
        for mode in MODES:
            q = resampled[grade] if mode != 'training_only' else np.broadcast_to(raw, (count, len(h)))
            t = resampled_training if mode != 'query_only' else train_mean
            proposed_draws = reference_part + beta * (q - t)
            b = _guard_rows(h, proposed_draws)
            b800 = np.array([np.interp(800, h, row) for row in b])
            unguarded_b800 = np.array([np.interp(800, h, row) for row in proposed_draws])
            ranges = _range(b)
            modes[mode] = dict(conditional_B_T=ranges, conditional_B800_T=_range(b800),
                B800_span_T=float(np.quantile(b800, .975) - np.quantile(b800, .025)),
                max_curve_span_T=float(np.max(np.asarray(ranges['p975']) - ranges['p025'])),
                conditional_B800_std_T=float(np.std(b800, ddof=1)),
                pre_final_guard_B_T=_range(proposed_draws), pre_final_guard_B800_T=_range(unguarded_b800),
                pre_final_guard_B800_span_T=float(np.quantile(unguarded_b800, .975) - np.quantile(unguarded_b800, .025)),
                max_imposed_final_guard_change_T=float(np.max(np.abs(b-proposed_draws))))
        output.append(dict(grade=grade, direction=direction, H=c['H'],
            frozen_candidate=bank.candidate['id'], frozen_bank_sha256=bank.bank_sha256,
            training_grades=training_grades, native_beta=beta,
            anchor_weights={s['grade']: float(v) for v, s in zip(w, bank.samples)}, domain=domain,
            reference_interpolation_B_T=reference_part.tolist(), native_component_B_T=native_part.tolist(),
            native_component_B800_T=float(np.interp(800, h, native_part)),
            frozen_prediction_B_T=base.tolist(), fixed_weights_reference_only_B_T=control.tolist(),
            pre_grain_guard_control_B_T=midpoint_control.tolist(), final_guard=imposed,
            native_guard_effect_on_B800_T=float(np.interp(800, h, base-midpoint_control)),
            native_guard_effect_max_curve_T=float(np.max(np.abs(base-midpoint_control))),
            sensitivity=modes,
            scores={name: error_metrics(h, b, c['reference_B']) for name, b in (
                ('frozen_native_v2', base), ('fixed_weights_reference_only', control),
                ('pre_grain_guard_diagnostic', midpoint_control))}))
    return output


def summarize(rows):
    methods = ('frozen_native_v2', 'fixed_weights_reference_only', 'pre_grain_guard_diagnostic')
    scores = {name: dict(mean_rmse_T=float(np.mean([r['scores'][name]['rmse_T'] for r in rows])),
        max_abs_B800_error_T=max(abs(r['scores'][name]['B800_error_T']) for r in rows))
        for name in methods}
    return dict(scores=scores, direction_cases=len(rows),
        native_improves_rmse_cases=sum(r['scores']['frozen_native_v2']['rmse_T'] <
            r['scores']['fixed_weights_reference_only']['rmse_T'] for r in rows),
        native_improves_abs_B800_cases=sum(abs(r['scores']['frozen_native_v2']['B800_error_T']) <
            abs(r['scores']['fixed_weights_reference_only']['B800_error_T']) for r in rows),
        largest_conditional_B800_span_T=max(r['sensitivity']['all_materials']['B800_span_T'] for r in rows),
        largest_native_guard_effect_on_B800_T=max(abs(r['native_guard_effect_on_B800_T']) for r in rows),
        empirical_resampling_only=True, model_changed=False, new_native_solves=0,
        prospective_external_validation=False)
