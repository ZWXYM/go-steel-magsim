"""Strict CPU contract for transporting experimental RD/TD curves to AEDT.

Serialized curve transport is separate from directional field validation,
loss calibration and material independence. No solver is imported here.
"""
import hashlib
import json
import math
import re
from pathlib import Path

import numpy as np
from modules.motor_model_audit import blocks, one, value

VERSION = 'aedt_directional_transport_v1'


def cut_depth_meters(text):
    match = re.fullmatch(r'([0-9.eE+-]+)(meter|mm|um|m)', text or '')
    if not match:
        raise ValueError('Unsupported cut depth unit')
    result = float(match[1])*{'meter':1., 'm':1., 'mm':1e-3, 'um':1e-6}[match[2]]
    if not math.isfinite(result) or result <= 0:
        raise ValueError('Cut depth must be explicit, finite and positive')
    return result


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def directional_curves(text, material_name):
    body = one(text, material_name)
    if value(body, 'CoordinateSystemType') != 'Cartesian':
        raise ValueError('Only explicit Cartesian RD/TD material transport is supported')
    mu = one(body, 'permeability')
    if value(mu, 'property_type') != 'AnisoProperty':
        raise ValueError('RD/TD require distinct nonlinear anisotropic components')
    result = {}
    for axis, direction in ((1, 'RD'), (2, 'TD')):
        component = one(mu, 'component'+str(axis))
        if (value(component, 'property_type') != 'nonlinear'
                or (value(component, 'BTypeForSingleCurve') or '').casefold() != 'normal'
                or value(component, 'HUnit') != 'A_per_meter'
                or value(component, 'BUnit') != 'tesla'
                or value(component, 'IsTemperatureDependent') != 'false'):
            raise ValueError('Need normal, non-temperature-dependent B/H in T and A/m')
        coordinates = one(component, 'BHCoordinates')
        match = re.search(r'Points\[(\d+):\s*([^\]]+)\]', coordinates)
        if not match:
            raise ValueError('Missing native Points array')
        data = [float(s.strip()) for s in match[2].split(',')]
        if int(match[1]) != len(data) or len(data) < 6 or len(data) % 2:
            raise ValueError('Native Points count is invalid')
        pairs = np.asarray(data).reshape(-1, 2)
        if (not np.isfinite(pairs).all() or pairs[0].tolist() != [0., 0.]
                or np.any(np.diff(pairs[:, 0]) <= 0)
                or np.any(np.diff(pairs[:, 1]) < -1e-12) or np.any(pairs < 0)):
            raise ValueError('Invalid physical H / monotone normal B curve')
        result[direction] = dict(H=pairs[:, 0].tolist(), B=pairs[:, 1].tolist())
    nd = float(value(mu, 'component3'))
    if not math.isfinite(nd) or nd <= 0 or blocks(mu, 'component3'):
        raise ValueError('Need the explicitly recorded scalar ND prior')
    result['ND_mu_r'] = nd
    for field in ('core_loss_kh', 'core_loss_kc', 'core_loss_ke', 'core_loss_kdc'):
        if float(value(body, field)) != 0:
            raise ValueError('This BH-only transport protocol cannot import loss estimates')
    result['zero_loss_placeholders'] = True
    result['core_loss_equiv_cut_depth'] = value(body, 'core_loss_equiv_cut_depth')
    result['core_loss_equiv_cut_depth_m'] = cut_depth_meters(result['core_loss_equiv_cut_depth'])
    result['conductivity_S_per_m'] = float(value(body, 'conductivity'))
    result['mass_density_kg_per_m3'] = float(value(body, 'mass_density'))
    if not all(math.isfinite(result[k]) and result[k] > 0 for k in
               ('conductivity_S_per_m', 'mass_density_kg_per_m3')):
        raise ValueError('Missing explicit positive physical material properties')
    return result


def load_contract(amat_path):
    path = Path(amat_path)
    metadata_path = path.with_suffix('.metadata.json')
    metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
    if (metadata.get('status') != 'experimental_BH_only'
            or metadata.get('H_axis') != 'physical_A_per_m'
            or metadata.get('H_scale') != 1.0
            or metadata.get('core_loss_status') != 'uncalibrated_zero_placeholders'
            or metadata.get('exported_directions') != ['RD', 'TD']
            or metadata.get('ND_status') != 'scalar_prior_1000_not_measured'):
        raise ValueError('Material metadata is not compatible with fixed-H BH-only transport')
    name = metadata['material_name']
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', name):
        raise ValueError('Unsupported material name')
    curves = directional_curves(path.read_text(encoding='utf-8-sig'), name)
    if curves['ND_mu_r'] != 1000:
        raise ValueError('Scalar ND prior differs from its recorded metadata')
    return dict(transport_version=VERSION, material_name=name, curves=curves,
        amat_sha256=digest(path), metadata_sha256=digest(metadata_path),
        calibration_sha256=metadata['calibration_sha256'],
        reference_correction_version=metadata['reference_correction_version'],
        axis_mapping={'RD': 'component1/local_x', 'TD': 'component2/local_y', 'ND': 'component3/local_z'},
        mapping_scope='serialized components only; physical field response not yet verified',
        directional_field_verified=False, motor_ranking_eligible=False, loss_calibration_verified=False)


def native_arguments(contract):
    """Use documented NAME:Point arrays, avoiding PyAEDT NAME:Points loss.

    Native success is still established only by reading the saved project;
    AddMaterial returning without an exception is insufficient evidence.
    """
    curves = contract['curves']
    permeability = ['NAME:permeability', 'property_type:=', 'AnisoProperty', 'unit:=', '']
    for axis, direction in ((1, 'RD'), (2, 'TD')):
        curve = curves[direction]
        coordinates = ['NAME:BHCoordinates', ['NAME:DimUnits', '', '']]
        coordinates.extend(['NAME:Point', h, b] for h, b in zip(curve['H'], curve['B']))
        permeability.append(['NAME:component'+str(axis), 'property_type:=', 'nonlinear',
            'BTypeForSingleCurve:=', 'Normal', 'HUnit:=', 'A_per_meter', 'BUnit:=', 'tesla',
            'IsTemperatureDependent:=', False, coordinates, ['NAME:Temperatures']])
    permeability.extend(['component3:=', str(curves['ND_mu_r'])])
    return ['NAME:'+contract['material_name'], 'CoordinateSystemType:=', 'Cartesian',
        'BulkOrSurfaceType:=', 1, ['NAME:PhysicsTypes', 'set:=', ['Electromagnetic']],
        'permittivity:=', '1', permeability,
        'conductivity:=', str(curves['conductivity_S_per_m']),
        'mass_density:=', str(curves['mass_density_kg_per_m3']),
        ['NAME:core_loss_type', 'property_type:=', 'ChoiceProperty', 'Choice:=', 'Electrical Steel'],
        'core_loss_kh:=', '0', 'core_loss_kc:=', '0', 'core_loss_ke:=', '0', 'core_loss_kdc:=', '0',
        'core_loss_equiv_cut_depth:=', curves['core_loss_equiv_cut_depth']]


def compatible_import_copy(amat_path, output_directory):
    """Copy an old experimental export, normalizing two parser-only fields.

    Preserve source files and all curve/scalar values. Never overwrite a
    destination or hide this conversion as the original source byte identity.
    """
    path = Path(amat_path)
    contract = load_contract(path)
    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    destination = output/path.name
    metadata_destination = destination.with_suffix('.metadata.json')
    if destination.exists() or metadata_destination.exists():
        raise ValueError('Transport destinations must be new')
    text = path.read_text(encoding='utf-8-sig')
    text = text.replace("'set'('Electromagnetic')", "set('Electromagnetic')")
    text = text.replace("'DimUnits'('')", "DimUnits('', '')")
    for body in blocks(text, 'ModifierData'):
        if "'modifier_data'='no_modifier'" not in body or 'all_thermal_modifiers' in body:
            raise ValueError('Nontrivial modifiers cannot be silently removed')
        text = re.sub(r"(?ms)^\s*\$begin 'ModifierData'.*?\$end 'ModifierData'\s*\n", '', text, count=1)
    # PyAEDT 1.0.0 treats an internal blank line as the empty property name.
    text = '\n'.join(line for line in text.splitlines() if line.strip())+'\n'
    assert directional_curves(text, contract['material_name']) == contract['curves']
    destination.write_text(text, encoding='utf-8', newline='\n')
    metadata = json.loads(path.with_suffix('.metadata.json').read_text(encoding='utf-8'))
    metadata.update(amat_path=str(destination), aedt_transport_version=VERSION,
        original_amat_sha256=contract['amat_sha256'], original_metadata_sha256=contract['metadata_sha256'],
        import_normalization='unquoted PhysicsTypes set and two DimUnits; omitted empty no_modifier block; H/B unchanged')
    metadata_destination.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
    return destination, contract


def verify_saved_material(saved_text, contract):
    actual = directional_curves(saved_text, contract['material_name'])
    for direction in ('RD', 'TD'):
        for coordinate in ('H', 'B'):
            expected = np.asarray(contract['curves'][direction][coordinate])
            found = np.asarray(actual[direction][coordinate])
            if found.shape != expected.shape or not np.allclose(found, expected, rtol=1e-10, atol=2e-7):
                raise ValueError('Saved material changed '+direction+' '+coordinate)
    for key in ('ND_mu_r', 'zero_loss_placeholders', 'conductivity_S_per_m', 'mass_density_kg_per_m3', 'core_loss_equiv_cut_depth_m'):
        if actual[key] != contract['curves'][key]:
            raise ValueError('Saved material changed '+key)
    return dict(material_name=contract['material_name'], serialized_transport_verified=True,
        curve_points={d:len(actual[d]['H']) for d in ('RD', 'TD')},
        axis_mapping=contract['axis_mapping'], directional_field_verified=False,
        loss_calibration_verified=False, motor_ranking_eligible=False)


def equivalent_material_name(contract,direction,curve_representation='Normal'):
    if direction not in ('RD','TD'):raise ValueError('方向等效材料必须选择RD或TD')
    if curve_representation not in ('Normal','Intrinsic'):raise ValueError('未知方向等效曲线表示')
    return contract['material_name']+'_'+direction+('_INTRINSIC' if curve_representation=='Intrinsic' else '')+'_EQ_v1'


def equivalent_curve(contract,direction,curve_representation='Normal'):
    """Keep physical H/B nodes; Intrinsic stores Bi=B-mu0H, never normal B."""
    equivalent_material_name(contract,direction,curve_representation)
    source=contract['curves'][direction];h=np.asarray(source['H'],dtype=float);b=np.asarray(source['B'],dtype=float)
    if curve_representation=='Normal':return h,b
    if h.ndim!=1 or b.shape!=h.shape or len(h)<2 or not np.all(np.isfinite(h)) or not np.all(np.isfinite(b)) or h[0]!=0 or b[0]!=0 or np.any(np.diff(h)<=0):
        raise ValueError('Intrinsic需要从原点开始的完整物理H/B节点')
    bi=np.maximum.accumulate(b-4*np.pi*1e-7*h)
    if np.max(abs(bi+4*np.pi*1e-7*h-b))>1e-10:
        raise ValueError('原曲线不能在1e-10T舍入范围内转换为单调Intrinsic；不修改物理曲线')
    return h,bi


def equivalent_native_arguments(contract,direction,curve_representation='Normal'):
    """Use one unchanged directional curve as an explicitly isotropic BH law."""
    name=equivalent_material_name(contract,direction,curve_representation)
    args=native_arguments(contract);args[0]='NAME:'+name
    axis=1 if direction=='RD' else 2
    for index,item in enumerate(args):
        if isinstance(item,list) and item[0]=='NAME:permeability':
            component=next(p for p in item if isinstance(p,list) and p[0]=='NAME:component'+str(axis))
            if curve_representation=='Intrinsic':
                h,bi=equivalent_curve(contract,direction,curve_representation)
                component[component.index('BTypeForSingleCurve:=')+1]='Intrinsic'
                coords=next(v for v in component if isinstance(v,list) and v[0]=='NAME:BHCoordinates')
                coords[:]=['NAME:BHCoordinates',['NAME:DimUnits','',''],*(['NAME:Point',float(x),float(y)] for x,y in zip(h,bi))]
            component[0]='NAME:permeability';args[index]=component
            return args
    raise ValueError('原材料缺少方向B-H曲线')


def verify_saved_equivalent_material(saved_text,contract,direction,curve_representation='Normal'):
    """Keep the original tensor and check the scalar copy against its source nodes."""
    verify_saved_material(saved_text,contract)
    name=equivalent_material_name(contract,direction,curve_representation);body=one(saved_text,name);mu=one(body,'permeability')
    if (value(mu,'property_type')!='nonlinear' or (value(mu,'BTypeForSingleCurve') or '').casefold()!=curve_representation.casefold()
            or value(mu,'HUnit')!='A_per_meter' or value(mu,'BUnit')!='tesla'
            or value(mu,'IsTemperatureDependent')!='false'):
        raise ValueError('方向等效材料不是显式物理H/正常B的标量曲线')
    match=re.search(r'Points\[(\d+):\s*([^\]]+)\]',one(mu,'BHCoordinates'))
    if not match:raise ValueError('方向等效材料未保存B-H节点')
    raw=[float(x.strip()) for x in match[2].split(',')];source=contract['curves'][direction]
    expected=np.column_stack(equivalent_curve(contract,direction,curve_representation))
    if (int(match[1])!=len(raw) or len(raw)!=expected.size
            or not np.allclose(np.asarray(raw).reshape(-1,2),expected,rtol=1e-10,atol=2e-7)):
        raise ValueError('方向等效材料改变了原H/B节点')
    physical_error=None
    if curve_representation=='Intrinsic':
        points=np.asarray(raw).reshape(-1,2);physical_error=float(np.max(abs(points[:,1]+4*np.pi*1e-7*points[:,0]-np.asarray(source['B']))))
        if np.any(np.diff(points[:,1])<0) or not np.allclose(points[:,0],source['H'],rtol=1e-10,atol=1e-12) or physical_error>1e-9:
            raise ValueError('实际Intrinsic节点不能重建原物理B曲线')
    for field in ('core_loss_kh','core_loss_kc','core_loss_ke','core_loss_kdc'):
        if float(value(body,field))!=0:raise ValueError('方向等效B-H材料不得填造损耗系数')
    for field,key in (('conductivity','conductivity_S_per_m'),('mass_density','mass_density_kg_per_m3')):
        if float(value(body,field))!=contract['curves'][key]:raise ValueError('方向等效材料改变了'+field)
    if cut_depth_meters(value(body,'core_loss_equiv_cut_depth'))!=contract['curves']['core_loss_equiv_cut_depth_m']:
        raise ValueError('方向等效材料改变了等效切边深度')
    result=dict(material_name=name,source_material_name=contract['material_name'],equivalent_direction=direction,
        constitutive_model='scalar_direction_equivalent_v1',curve_points=len(source['H']),source_points_unchanged=True,
        serialized_transport_verified=True,directional_field_verified=False,loss_calibration_verified=False,
        scope='One directional normal B-H curve used isotropically for engineering comparison; no local RD/TD response or efficiency')
    if curve_representation=='Intrinsic':
        result.update(curve_representation='Intrinsic',native_B_definition='Bi=B-mu0H',source_points_unchanged=False,
            source_physical_B_preserved_at_nodes=True,physical_B_reconstruction_max_error_T=physical_error,
            physical_B_reconstruction_tolerance_T=1e-9,scope='One directional curve stored as native Intrinsic Bi-H and used isotropically; physical B is preserved at source H nodes within 1e-9T. Interpolation, local anisotropy and losses are not certified.')
    return result
