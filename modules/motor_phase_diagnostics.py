"""CPU phase diagnostics; preserve every official sample and existing acceptance gates."""
from pathlib import Path
import re

import numpy as np

from modules.motor_workbench import numeric_csv, collect_metrics

VERSION = 'motor_cycle_phase_diagnostic_v1'


def phase_pair(time_s, values, *, cycles=(2,3)):
    time_s,values = np.asarray(time_s,dtype=float),np.asarray(values,dtype=float)
    if (time_s.shape!=(91,) or values.shape!=time_s.shape or not np.all(np.isfinite(values))
            or not np.allclose(time_s,np.linspace(0,.03,91),atol=1e-10,rtol=1e-8)
            or cycles not in ((1,2),(2,3))):
        raise ValueError('Expected complete 91-point, three-cycle physical time grid')
    first,second = [values[(c-1)*30:c*30+1] for c in cycles]
    delta=second-first
    worst=int(np.argmax(np.abs(delta)))
    unique=delta[:30]  # FFT diagnostics avoid counting the shared endpoint twice.
    amplitudes=np.abs(np.fft.rfft(unique))/30
    weights=np.full(len(amplitudes),2.)
    weights[[0,-1]]=1.
    power=weights*amplitudes**2
    total=float(power.sum())
    lag_rms=np.array([np.sqrt(np.mean((np.roll(second[:30],shift)-first[:30])**2)) for shift in range(30)])
    best=int(lag_rms.argmin())
    signed=best if best<=15 else best-30
    return dict(cycles=list(cycles),phase_time_ms=(np.arange(31)/3).tolist(),
        first_values=first.tolist(),second_values=second.tolist(),delta_values=delta.tolist(),
        max_abs_phase_delta=float(abs(delta[worst])),rms_phase_delta=float(np.sqrt(np.mean(delta**2))),
        mean_delta=float(delta.mean()),max_abs_interior_phase_delta=float(np.abs(delta[1:-1]).max()),
        worst_phase_index=worst,worst_phase_ms=worst/3,
        first_worst_time_ms=((cycles[0]-1)*10+worst/3),second_worst_time_ms=((cycles[1]-1)*10+worst/3),
        unique_30point_mean_square_delta=float(np.mean(unique**2)),
        frequency_power=power.tolist(),parseval_absolute_error=float(abs(total-np.mean(unique**2))),
        high_harmonic_6_to_15_power_fraction=float(power[6:].sum()/total) if total else 0.,
        best_cyclic_shift_samples=signed,best_shift_rms=float(lag_rms[best]),
        unshifted_unique_rms=float(lag_rms[0]),
        shifted_result_is_not_acceptance=True,no_filtering_or_sample_exclusion=True)


def read_channels(directory):
    directory=Path(directory)
    channels={}
    expected_time=None
    specs=[('reports/Torque Plots.csv',{'NewtonMeter':1,'Nm':1,'mNewtonMeter':1e-3,'':1}),
        ('reports/Drive Current Plots.csv',{'A':1,'mA':1e-3}),
        *[(f'maxwell_reports/{name}.csv',{'W':1,'mW':1e-3,'kW':1e3})
            for name in ('CoreLoss','StrandedLoss','SolidLoss')]]
    for relative,scales in specs:
        headers,values=numeric_csv(directory/relative)
        times=[h for h in headers if h.startswith('Time ')]
        if len(times)!=1:
            raise ValueError('Expected unique official Time column')
        def declared(header):
            match=re.search(r'\[([^]]*)\]',header)
            if match is None:
                raise ValueError('Missing unit declaration')
            return match.group(1)
        tc=times[0]
        time_unit=declared(tc)
        if time_unit not in ('s','ms','us','ns'):
            raise ValueError('Unknown time unit')
        ts=values(tc)*{'s':1,'ms':1e-3,'us':1e-6,'ns':1e-9}[time_unit]
        if expected_time is not None and (ts.shape!=expected_time.shape or not np.allclose(ts,expected_time,atol=1e-10,rtol=1e-8)):
            raise ValueError('Official channels differ in time grid')
        expected_time=ts
        for header in headers:
            if header==tc:
                continue
            unit=declared(header)
            if unit not in scales or (unit=='' and not header.startswith('TorqueDQ ')):
                raise ValueError('Unknown physical channel unit')
            key=header.split(' [')[0]
            if key in channels:
                raise ValueError('Duplicate official channel')
            channels[key]=dict(source=relative,declared_unit=unit,
                unit_is_physically_confirmed=unit!='',
                values=values(header)*scales[unit],
                converted_unit=('A' if 'Current' in key else 'W' if 'Loss' in key else 'Nm') if unit else 'unconfirmed')
    return expected_time,channels


def audit_case(directory):
    directory=Path(directory)
    time,channels=read_channels(directory)
    existing=collect_metrics(directory,'periodic_cycle3_v1')
    output={key:{k:v for k,v in channel.items() if k!='values'} for key,channel in channels.items()}
    for key,channel in channels.items():
        output[key]['cycle1_vs_2']=phase_pair(time,channel['values'],cycles=(1,2))
        output[key]['cycle2_vs_3']=phase_pair(time,channel['values'])
    mechanical=output['-Moving1.Torque']['cycle2_vs_3']
    if abs(mechanical['max_abs_phase_delta']-existing['periodic_stability']['max_phase_torque_delta_Nm'])>1e-12:
        raise ValueError('Phase diagnostics differ from frozen official metric')
    currents=[channel['values'] for key,channel in channels.items() if key.startswith('InputCurrent(')]
    if len(currents)!=3:
        raise ValueError('Expected three official drive currents')
    return dict(protocol=VERSION,channels=output,
        max_abs_three_phase_current_sum_A=float(np.abs(np.sum(currents,axis=0)).max()),
        existing_periodic_stability=existing['periodic_stability'],existing_acceptance=existing['accepted'],
        existing_T_avg_Nm=existing['T_avg_Nm'],existing_P_Fe_W=existing['P_Fe_W'],
        all_91_points_preserved=True,gates_unchanged=True,
        lag_or_spectrum_diagnostics_do_not_change_acceptance=True,
        unknown_TorqueDQ_unit_is_not_mechanical_torque_validation=True)
