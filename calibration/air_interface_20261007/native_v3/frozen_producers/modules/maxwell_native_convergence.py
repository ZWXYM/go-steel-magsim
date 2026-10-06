"""Read official Maxwell adaptive convergence; API success alone is insufficient."""
import math
import re
from pathlib import Path


def read_convergence(path, expected_target):
    text = Path(path).read_text(encoding='utf-8-sig')
    def number(label):
        match = re.search(r'^'+label+r'\s*:\s*(\d+)\s*$',text,re.M)
        if not match:
            raise ValueError('Missing native convergence '+label)
        return int(match[1])
    completed,maximum,minimum = (number(k) for k in ('Completed','Maximum','Minimum'))
    if not minimum <= completed <= maximum:
        raise ValueError('Invalid native adaptive pass count')
    def pair(label):
        match = re.search(r'^'+label+r'\s*:\s*\(([^,]+),\s*([^\)]+)\)',text,re.M)
        if not match:
            raise ValueError('Missing native convergence '+label)
        values = [float(v) for v in match.groups()]
        if not all(math.isfinite(v) and v>=0 for v in values):
            raise ValueError('Invalid convergence criteria')
        return values
    target,current = pair('Target'),pair('Current')
    if target != [float(expected_target)]*2:
        raise ValueError('Native convergence target differs from frozen budget')
    passes = []
    for line in text.splitlines():
        if not re.match(r'^\s*\d+\|',line):
            continue
        fields = line.split('|')
        if len(fields)!=6 or fields[-1].strip():
            raise ValueError('Malformed official convergence row')
        p,n = int(fields[0]),int(fields[1])
        energy,error = float(fields[2]),float(fields[3])
        delta = None if fields[4].strip()=='N/A' else float(fields[4])
        if p!=len(passes)+1 or n<=0 or not all(math.isfinite(v) and v>=0 for v in (energy,error)) or (delta is not None and (not math.isfinite(delta) or delta<0)):
            raise ValueError('Invalid official adaptive sequence')
        passes.append(dict(pass_number=p,triangles=n,energy_J=energy,energy_error_percent=error,delta_energy_percent=delta))
    if len(passes)!=completed or passes[-1]['delta_energy_percent'] is None:
        raise ValueError('Incomplete native convergence history')
    last = passes[-1]
    if [last['energy_error_percent'],last['delta_energy_percent']]!=current:
        raise ValueError('Native convergence summary and final pass disagree')
    return dict(completed_passes=completed,maximum_passes=maximum,minimum_passes=minimum,
        targets_percent=target,final_percent=current,passes=passes,
        adaptive_converged=all(c<=t for c,t in zip(current,target)),
        reached_maximum_passes=completed==maximum)
