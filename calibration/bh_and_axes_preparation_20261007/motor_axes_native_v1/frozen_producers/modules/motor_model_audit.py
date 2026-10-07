"""Read-only, conservative audit of saved Maxwell text projects.

This checks serialized assignments and settings, not geometric validity,
mesh convergence, measured loss calibration, or a true directional law.
Unknown formats remain unknown. No AEDT or native solver is imported.
"""
from __future__ import annotations

import hashlib
import math
import re
from pathlib import Path

VERSION = 'motor_saved_model_audit_v1'
LOSS_POLICY = 'core_loss_scope_v2_all_magnetic_inserts'
TOKEN = re.compile(r"(?m)^\s*\$(begin|end) '([^\r\n]+)'\s*$")
GOES = re.compile(r'GOES_V3_\d{2}(?:_\d+)?\Z')


def blocks(text, name):
    """Balanced blocks; never use an unbalanced nested material regex."""
    stack = []
    found = []
    for match in TOKEN.finditer(text):
        kind, label = match.groups()
        if kind == 'begin':
            stack.append((label, match.end()))
        else:
            if not stack or stack[-1][0] != label:
                raise ValueError('AEDT 块结构无法核验: ' + label)
            label, start = stack.pop()
            if label == name:
                found.append(text[start:match.start()])
    if stack:
        raise ValueError('AEDT 块不完整')
    return found


def value(text, key):
    match = re.search(r"(?m)^\s*'?" + re.escape(key) + r"'?=([^\r\n]+)$", text)
    if not match:
        return None
    return match.group(1).strip().strip("'")


def one(text, name):
    values = blocks(text, name)
    if len(values) != 1:
        raise ValueError(f'AEDT {name} 需唯一块，实际 {len(values)}')
    return values[0]


def _ids(text, key):
    match = re.search(r'(?m)^\s*' + key + r'(?:\[\d+:([^\]]*)\]|\(([^)]*)\))\s*$', text)
    if not match:
        return None
    raw = next(s for s in match.groups() if s is not None)
    return [int(s.strip()) for s in raw.split(',') if s.strip()]


def _variable(text, name):
    match = re.search(r"VariableProp\('" + re.escape(name) + r"', '[^']*', '[^']*', '([^']*)'", text)
    return match.group(1) if match else None


def material_audit(text, name):
    materials = blocks(text, 'Materials')
    candidates = blocks(materials[0], name) if len(materials) == 1 else []
    if len(candidates) != 1:
        return dict(name=name, embedded=False, permeability_type='unknown',
                    directional_constitutive_verified=False, loss_definition_present=False)
    body = candidates[0]
    mu = blocks(body, 'permeability')
    ptype = value(mu[0], 'property_type') if len(mu) == 1 else 'simple_or_unknown'
    curves = blocks(mu[0], 'BHCoordinates') if len(mu) == 1 else []
    tensor = ptype in ('AnisoProperty', 'TensorProperty')
    loss = blocks(body, 'core_loss_type')
    loss_type = value(loss[0], 'Choice') if len(loss) == 1 else None
    # A declared tensor alone is not proof of independently sourced RD/TD,
    # their axis mapping, or successful import of the calibrated pair.
    coefficients={k:value(body,k) for k in ('core_loss_cm','core_loss_x','core_loss_y','core_loss_kh','core_loss_kc','core_loss_ke')}
    def positive(key):
        try:
            number=float(coefficients[key])
            return math.isfinite(number) and number>0
        except (ValueError,TypeError):
            return False
    loss_defined=(loss_type=='Power Ferrite' and all(positive(k) for k in ('core_loss_cm','core_loss_x','core_loss_y'))) or (loss_type=='Electrical Steel' and any(positive(k) for k in ('core_loss_kh','core_loss_kc','core_loss_ke')))
    result = dict(name=name, embedded=True, material_block_sha256=hashlib.sha256(body.encode()).hexdigest(),
        permeability_type=ptype, scalar_BH_curve_count=len(curves) if not tensor else 0,
        tensor_declared=tensor, directional_constitutive_verified=False,
        core_loss_type=loss_type, loss_definition_present=loss_defined,
        conductivity=value(body, 'conductivity'), mass_density=value(body, 'mass_density'),
        stacking_type=value(blocks(body, 'stacking_type')[0], 'Choice') if blocks(body, 'stacking_type') else None,
        loss_coefficients=coefficients,
        loss_calibration_verified=False, calibrated_RD_TD_import_verified=False)
    result['loss_curve_frequencies_Hz'] = [float(x) for x in re.findall(r"Frequency='([0-9.]+)Hz'", body)]
    if len(curves) == 1:
        match = re.search(r'Points\[\d+:([^]]+)\]', curves[0])
        if match:
            data = [float(s.strip()) for s in match.group(1).split(',')]
            result.update(BH_points=len(data)//2, H_unit=value(mu[0],'HUnit'), B_unit=value(mu[0],'BUnit'),
                          H_last_A_per_m=data[-2], B_last_T=data[-1])
    return result


def inspect_model(text):
    objects = []
    for body in blocks(text, 'GeometryPart'):
        attrs = blocks(body, 'Attributes')
        attrs = attrs[0] if attrs else body
        name = value(attrs, 'Name')
        if not name:
            continue
        parent = value(body, 'ParentPartID')
        objects.append(dict(name=name, object_id=int(parent) if parent is not None else None,
            coordinate_system_id=int(value(attrs,'PartCoordinateSystem') or 0),
            material=(value(attrs,'MaterialValue') or '').strip('"'), solve_inside=value(attrs,'SolveInside')))
    inserts = [o for o in objects if GOES.fullmatch(o['name'])]
    cs_names = {}
    cs_blocks = blocks(text, 'CoordinateSystems')
    if len(cs_blocks) == 1:
        for body in blocks(cs_blocks[0], 'Operation'):
            if value(body,'OperationType') == 'CreateObjectCoordinateSystem':
                cs_names[int(value(body,'ID'))] = value(body,'Name')
    for obj in inserts:
        obj['coordinate_system_name'] = cs_names.get(obj['coordinate_system_id'])
        obj['object_CS_binding_verified'] = obj['coordinate_system_name'] == obj['name'] + '_CS'
    bound = blocks(text, 'GlobalBoundData')
    enabled = _ids(bound[0], 'CoreLossObjectIDs') if len(bound) == 1 else None
    ids_to_names = {o['object_id']:o['name'] for o in objects if o['object_id'] is not None}
    settings = blocks(text, 'Setup1')
    setup = settings[0] if len(settings) == 1 else ''
    moving = blocks(text,'Moving1')
    moving_ids = _ids(moving[0],'Objects') if len(moving) == 1 else None
    rpm_raw = _variable(text,'MachineRPM')
    rpm = float(rpm_raw[:-3]) if rpm_raw and re.fullmatch(r'[0-9.]+rpm',rpm_raw) else None
    poles = _variable(text,'NumPoles')
    poles = float(poles) if poles and re.fullmatch(r'[0-9.]+',poles) else None
    covered = [o['name'] for o in inserts if enabled is not None and o['object_id'] in enabled]
    result = dict(version=VERSION, scope='serialized saved model; no solve or mesh/geometry validation',
        insert_count=len(inserts), inserts=inserts,
        local_CS_verified=len(inserts)==48 and all(o['object_CS_binding_verified'] for o in inserts),
        moving_insert_count=sum(o['object_id'] in moving_ids for o in inserts) if moving_ids is not None else None,
        materials=[material_audit(text,n) for n in sorted({o['material'] for o in inserts})],
        core_loss=dict(enabled_object_ids=enabled,
            enabled_object_names=[ids_to_names.get(i,f'unknown_ID_{i}') for i in enabled] if enabled is not None else None,
            insert_enabled_count=len(covered), complete_insert_coverage=len(inserts)==48 and len(covered)==48,
            missing_inserts=[o['name'] for o in inserts if o['name'] not in covered],
            effect_on_field=value(bound[0],'ConsiderCoreLossEffectOnField') if len(bound)==1 else None),
        operating_point=dict(speed_rpm=rpm,poles=poles,electrical_frequency_Hz=rpm*poles/120 if rpm and poles else None,
            cycles=_variable(text,'NumTorqueCycles'),points_per_cycle=_variable(text,'NumTorquePointsPerCycle'),
            angular_velocity=value(text,'Angular Velocity'),model_depth=value(text,'ModelDepth'),
            magnetic_axial_length=_variable(text,'Magnetic_Axial_Length')),
        setup={k:value(setup,k) for k in ('StopTime','TimeStep','NonlinearSolverResidual','SmoothBHCurve',
            'UseAdaptiveTimeStep','AutoDetectSteadyState','FastReachSteadyState','OutputError','OutputPerObjectCoreLoss')},
        directional_constitutive_verified=False,loss_calibration_verified=False,mesh_convergence_verified=False)
    return result


def audit_project(project):
    project = Path(project)
    data = project.read_bytes()
    result = inspect_model(data.decode('utf-8-sig'))
    result['project_sha256'] = hashlib.sha256(data).hexdigest()
    return result


def comparison_status(result):
    """Keep numerical acceptance unchanged; no final ranking without physics."""
    reasons = []
    if not result.get('accepted'):
        reasons.append('未通过数值/周期门限')
    if result.get('metric_protocol') != 'periodic_cycle3_v1' or not (result.get('periodic_stability') or {}).get('passed'):
        reasons.append('稳态未验证')
    audit = result.get('model_physics_audit') or {}
    if not audit.get('core_loss',{}).get('complete_insert_coverage'):
        reasons.append('48 插片铁损覆盖未验证')
    if not audit.get('directional_constitutive_verified'):
        reasons.append('RD/TD 本构与方向映射未验证')
    if not audit.get('loss_calibration_verified'):
        reasons.append('损耗校准未验证')
    if not audit.get('mesh_convergence_verified'):
        reasons.append('网格收敛未验证')
    return dict(eligible=not reasons, reasons=reasons, scope='final thesis material/efficiency optimization ranking')
