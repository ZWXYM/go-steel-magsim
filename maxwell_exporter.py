"""
maxwell_exporter.py
将 B-H 曲线导出为 ANSYS Maxwell .amat 材料文件（XML 格式）。
格式参考：go_steel_data/output/GO_Steel_B23R075.amat
"""
import os
from pathlib import Path
from datetime import datetime

# .amat XML 模板（与现有 go_steel_data/generate_pyaedt_import.py 中的格式完全一致）
_HEADER = '''<?xml version="1.0" encoding="UTF-8"?>
<Material xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <MatProperty name="Name" fullname="name" type="string">{mat_name}</MatProperty>
  <MatProperty name="CoordinateSystemType" fullname="coordinate system type" type="choice">Cartesian</MatProperty>
  <MatProperty name="BulkOrSurface" fullname="bulk or surface" type="choice">Bulk</MatProperty>
  <MatProperty name="MassDensity" fullname="mass density" type="float" unit="kg_per_m3">{density:.0f}</MatProperty>

'''

_FOOTER = '''  <MatProperty name="CoreLossModel" fullname="core loss model" type="choice">Electrical Steel</MatProperty>
  <MatProperty name="Thickness" fullname="thickness" type="float" unit="mm">{thickness}</MatProperty>
  <MatProperty name="Conductivity" fullname="conductivity" type="float" unit="siemens_per_m">2000000</MatProperty>
</Material>
'''


def _preprocess_bh(H: list, B: list) -> tuple[list, list]:
    """
    预处理 B-H 数据：去掉 H<=0，排序，去重，确保 B 单调不减。
    返回 (H_clean, B_clean)。
    """
    import numpy as np
    H_arr = np.array(H, dtype=float)
    B_arr = np.array(B, dtype=float)

    # 仅保留 H > 0
    mask = H_arr > 0
    H_arr = H_arr[mask]
    B_arr = B_arr[mask]

    if len(H_arr) == 0:
        return [0.1], [0.0]

    # 排序
    idx = np.argsort(H_arr)
    H_arr = H_arr[idx]
    B_arr = B_arr[idx]

    # 去重（保留第一个）
    _, uid = np.unique(H_arr, return_index=True)
    H_arr = H_arr[uid]
    B_arr = B_arr[uid]

    return H_arr.tolist(), B_arr.tolist()


def generate_amat_content(mat_name: str,
                           rd_H: list, rd_B: list,
                           td_H: list = None, td_B: list = None,
                           nd_mu_r: float = 1000.0,
                           density_kg_m3: float = 7650.0,
                           thickness_mm: float = 0.35) -> str:
    """
    生成完整 .amat 文件内容字符串。
    td_H/td_B 为 None 时，TD 与 RD 相同（各向同性退化）。
    """
    if td_H is None or td_B is None:
        td_H, td_B = rd_H, rd_B

    rd_H_c, rd_B_c = _preprocess_bh(rd_H, rd_B)
    td_H_c, td_B_c = _preprocess_bh(td_H, td_B)

    lines = [_HEADER.format(mat_name=mat_name, density=density_kg_m3)]

    # RD (X)
    lines.append('  <!-- Rolling Direction (X) - Nonlinear BH -->\n')
    lines.append('  <MatProperty name="BH_Data_X" fullname="BH data X" type="dataset">\n')
    for h, b in zip(rd_H_c, rd_B_c):
        lines.append(f'    <DataPoint X="{h:.6f}" Y="{b:.6f}"/>\n')
    lines.append('  </MatProperty>\n\n')

    # TD (Y)
    lines.append('  <!-- Transverse Direction (Y) - Nonlinear BH -->\n')
    lines.append('  <MatProperty name="BH_Data_Y" fullname="BH data Y" type="dataset">\n')
    for h, b in zip(td_H_c, td_B_c):
        lines.append(f'    <DataPoint X="{h:.6f}" Y="{b:.6f}"/>\n')
    lines.append('  </MatProperty>\n\n')

    # ND (Z) - 线性标量
    lines.append('  <!-- Normal Direction (Z) - Linear permeability -->\n')
    lines.append(f'  <MatProperty name="Permeability_Z" fullname="relative permeability Z" '
                 f'type="float">{nd_mu_r:.1f}</MatProperty>\n\n')

    lines.append(_FOOTER.format(thickness=thickness_mm))
    return ''.join(lines)


def save_amat_file(content: str, mat_name: str,
                   export_dir: str = 'data/exports') -> str:
    """保存到 data/exports/<mat_name>.amat，返回文件路径。"""
    Path(export_dir).mkdir(parents=True, exist_ok=True)
    path = Path(export_dir) / f'{mat_name}.amat'
    path.write_text(content, encoding='utf-8')
    return str(path)


def export_from_bh_curves(rd_H: list, rd_B: list,
                           td_H: list = None, td_B: list = None,
                           mat_name: str = None,
                           thickness_mm: float = 0.35,
                           export_dir: str = 'data/exports') -> str:
    """
    从手动 B-H 数据生成并保存 .amat 文件，返回文件路径。
    mat_name 为 None 时自动生成时间戳命名。
    """
    if mat_name is None:
        mat_name = f'GO_Sim_{datetime.now().strftime("%Y%m%d_%H%M%S")}'
    content = generate_amat_content(mat_name, rd_H, rd_B, td_H, td_B,
                                    thickness_mm=thickness_mm)
    return save_amat_file(content, mat_name, export_dir)


def export_from_prediction(prediction_result: dict,
                            mat_name: str,
                            thickness_mm: float = 0.35,
                            export_dir: str = 'data/exports') -> str:
    """
    从 BHPredictor.predict_bh() 的返回值直接生成 .amat。
    prediction_result 格式：{'RD': {'H':[...], 'B':[...]}, 'TD': {...}, ...}
    """
    rd = prediction_result.get('RD', {})
    td = prediction_result.get('TD', {})
    content = generate_amat_content(
        mat_name,
        rd.get('H', []), rd.get('B', []),
        td.get('H', []), td.get('B', []),
        thickness_mm=thickness_mm
    )
    return save_amat_file(content, mat_name, export_dir)


def list_exports(export_dir: str = 'data/exports') -> list[dict]:
    """列出 data/exports/ 下所有 .amat 文件的元信息。"""
    result = []
    d = Path(export_dir)
    if not d.exists():
        return result
    for f in sorted(d.glob('*.amat'), reverse=True):
        result.append({
            'name':    f.name,
            'path':    str(f),
            'size_kb': round(f.stat().st_size / 1024, 1),
            'created': datetime.fromtimestamp(f.stat().st_mtime).strftime('%Y-%m-%d %H:%M:%S'),
        })
    return result
