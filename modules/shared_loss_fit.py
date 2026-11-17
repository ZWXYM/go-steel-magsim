"""Fit one nonnegative loss law across measured B peaks and training frequencies."""
import math

import numpy as np
from scipy.optimize import nnls


VERSION = 'frequency_loss_validation_v4_shared_B_fit'
COEFFICIENTS = ('Kh', 'Kc', 'Ke')
COEFFICIENT_UNITS = {'Kh':'W kg^-1 Hz^-1 T^-2', 'Kc':'W kg^-1 Hz^-2 T^-2', 'Ke':'W kg^-1 Hz^-1.5 T^-1.5'}


def _physical_peak(row):
    return row.get('observed_peak_T', row['peak_T'])


def _design(rows, frequency_scale, peak_scale):
    f = np.asarray([row['frequency_Hz'] for row in rows])/frequency_scale
    b = np.asarray([_physical_peak(row) for row in rows])/peak_scale
    return np.column_stack((f*b*b, (f*b)**2, (f*b)**1.5))


def fit_shared_loss(table, holdout, fixed_kc=None):
    if table['amplitude'] != 'B' or table['loss_unit'] != 'W/kg':
        raise ValueError('共用系数拟合需要 B 峰值和质量损耗（W/kg 或每周期 J/kg）')
    train = [row for row in table['rows'] if row['frequency_Hz'] != holdout]
    test = [row for row in table['rows'] if row['frequency_Hz'] == holdout]
    peaks = sorted({row['peak_T'] for row in train})
    if len(peaks) < 2 or {row['peak_T'] for row in test} != set(peaks):
        raise ValueError('至少需要两个磁密峰值，每个峰值须有该留出频率')
    if fixed_kc is not None and (not math.isfinite(fixed_kc) or fixed_kc < 0):
        raise ValueError('固定 Kc 须为非负有限数值')
    f_scale = max(row['frequency_Hz'] for row in train)
    fitted_peaks = [_physical_peak(row) for row in train]
    b_scale = max(fitted_peaks)
    observed_train = np.asarray([row['loss'] for row in train])
    observed_test = np.asarray([row['loss'] for row in test])
    p_scale = float(np.min(observed_train))
    try:
        with np.errstate(over='raise', invalid='raise', divide='raise'):
            factors = np.asarray([f_scale*b_scale*b_scale, (f_scale*b_scale)**2, (f_scale*b_scale)**1.5])
            design = _design(train, f_scale, b_scale)*(p_scale/observed_train)[:,None]
            validation = _design(test, f_scale, b_scale)
            variants = [('nonnegative',None)]
            if fixed_kc is not None:
                variants.append(('fixed_classical',fixed_kc))
            fits = {key: _variant(design, validation, observed_test, test, factors, p_scale, kc) for key,kc in variants}
    except (FloatingPointError, OverflowError, RuntimeError, np.linalg.LinAlgError) as error:
        raise ValueError('共用系数拟合超出可解范围，请核对单位及训练点跨度') from error
    return dict(formula='Kh*f*B^2 + Kc*f^2*B^2 + Ke*(f*B)^1.5', basis_amplitude='B', units='W/kg',
                coefficient_units=COEFFICIENT_UNITS, weighting='inverse_observed_training_loss',
                training_source_rows=[r['source_row'] for r in train], training_B_range_T=[min(fitted_peaks),b_scale],
                training_frequencies_Hz=sorted({r['frequency_Hz'] for r in train}),
                holdout_source_rows=[r['source_row'] for r in test], fits=fits)


def _variant(design, validation, losses, test, factors, p_scale, fixed_kc):
    columns = [0,1,2] if fixed_kc is None else [0,2]
    target = np.ones(len(design))
    coeff = np.zeros(3)
    if fixed_kc is not None:
        coeff[1] = fixed_kc * factors[1] / p_scale
        target -= design[:,1]*coeff[1]
    chosen = design[:,columns]
    scale = np.linalg.norm(chosen,axis=0)
    if np.any(scale<=0):
        raise ValueError('训练点无法区分所选损耗项')
    normalized = chosen/scale
    if np.linalg.matrix_rank(normalized) < len(columns):
        raise ValueError('训练点无法区分所选损耗项')
    scaled, _ = nnls(normalized,target)
    coeff[columns] = scaled/scale
    by_mass = p_scale*coeff/factors
    if fixed_kc is not None:
        by_mass[1]=fixed_kc
    prediction = p_scale*(validation@coeff)
    relative = 100*(prediction/losses-1)
    if not all(np.all(np.isfinite(values)) for values in (by_mass,prediction,relative)):
        raise ValueError('共用系数或留出误差超出有限数值范围')
    points = [dict(source_row=row['source_row'],peak_B_T=row['peak_T'],used_B_T=_physical_peak(row),frequency_Hz=row['frequency_Hz'],
                   observed_W_kg=row['loss'],predicted_W_kg=float(p),relative_error_pct=float(error))
              for row,p,error in zip(test,prediction,relative)]
    return dict(fixed_Kc=fixed_kc, coefficients_mass=dict(zip(COEFFICIENTS,map(float,by_mass))),
                holdout_points=points, mean_absolute_relative_pct=math.fsum(abs(p['relative_error_pct'])/len(points) for p in points))
