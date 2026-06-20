# generate_individual_scripts.py
import numpy as np
import os
import re
from pathlib import Path
from datetime import datetime


# ========================================
# 配置类
# ========================================
class SimulationConfig:
    """仿真配置管理"""
    SIMULATION_TYPE = 'single'  # 'single' 或 'complex'
    DEFAULT_ANGLES = [0, 30, 45, 60, 90, 120, 135, 150, 180]
    INPUT_DIR = 'input'
    OUTPUT_DIR = 'grain_scripts'

    # ========================================
    # 仿真参数配置
    # ========================================
    # 网格设置
    GRID_SIZE_X = 4
    GRID_SIZE_Y = 4
    GRID_SIZE_Z = 1
    CELL_SIZE = 4.0e-9  # 10 nm

    # 材料参数
    SI_CONTENT = 3.0  # Si含量 (%)
    MSAT = 1.56e6  # 饱和磁化强度 (A/m)  文献值 Fe-3%Si: 1.56e6 A/m (μ₀Msat≈1.96T)
    AEX = 2.1e-11  # 有效交换常数 (J/m)
    ALPHA = 0.01  # Gilbert阻尼系数
    KU1_BASE = 4.8e4  # 立方各向异性基准值 (J/m³)，用于 get_Ku1() 公式参考

    # 外场设置
    # H_k = 2*Ku1/(μ₀*Msat) ≈ 36728 A/m for Fe-3%Si
    # H_MAX 必须 > H_k 才能完成完整磁滞回线，建议 1.4× H_k ≈ 50000 A/m
    H_MAX = 50000.0  # 最大外场 (A/m)
    N_STEPS = 150  # 每段步数 (150步×4段=600次minimize，约85s/晶粒 RTX2060)

    @classmethod
    def get_Ku1(cls):
        """计算立方各向异性常数 K1 (J/m³)
        公式来源: Moses 2012 综述, Fe-Si: K1(Si%) = (KU1_BASE - 0.4*Si%*1e4)
        KU1_BASE = 4.8e4 → @ 3%Si: K1 = 48000 - 12000 = 36000 J/m³
        注意: H_k = 2*K1/(μ₀*Msat) = 36728 A/m (不是 2*K1/Msat，单位为 T)
        """
        return cls.KU1_BASE - 0.4 * cls.SI_CONTENT * 1e4

    @classmethod
    def get_rve_size(cls):
        """获取RVE尺寸（单位：米）"""
        return (
            cls.GRID_SIZE_X * cls.CELL_SIZE,
            cls.GRID_SIZE_Y * cls.CELL_SIZE,
            cls.GRID_SIZE_Z * cls.CELL_SIZE
        )

    @classmethod
    def get_rve_size_um(cls):
        """获取RVE尺寸（单位：微米）"""
        size_m = cls.get_rve_size()
        return tuple(s * 1e6 for s in size_m)


# ========================================
# 工具函数
# ========================================
def euler_to_easy_axis(phi1, Phi, phi2):
    """Convert Euler angles to <100> easy axis direction"""
    phi1_rad = np.radians(phi1)
    Phi_rad = np.radians(Phi)
    phi2_rad = np.radians(phi2)

    c1, s1 = np.cos(phi1_rad), np.sin(phi1_rad)
    c, s = np.cos(Phi_rad), np.sin(Phi_rad)
    c2, s2 = np.cos(phi2_rad), np.sin(phi2_rad)

    g = np.array([
        [c1 * c2 - s1 * s2 * c, -c1 * s2 - s1 * c2 * c, s1 * s],
        [s1 * c2 + c1 * s2 * c, -s1 * s2 + c1 * c2 * c, -c1 * s],
        [s2 * s, c2 * s, c]
    ])

    easy_axis = g @ np.array([1, 0, 0])
    return easy_axis / np.linalg.norm(easy_axis)


def euler_to_crystal_axes(phi1, Phi, phi2):
    """
    从欧拉角获取立方晶体坐标系的三个轴
    返回: (axis_100, axis_010, axis_001) 在样品坐标系中的表示
    """
    phi1_rad = np.radians(phi1)
    Phi_rad = np.radians(Phi)
    phi2_rad = np.radians(phi2)

    c1, s1 = np.cos(phi1_rad), np.sin(phi1_rad)
    c, s = np.cos(Phi_rad), np.sin(Phi_rad)
    c2, s2 = np.cos(phi2_rad), np.sin(phi2_rad)

    # Bunge convention rotation matrix (crystal -> sample)
    g = np.array([
        [c1 * c2 - s1 * s2 * c, -c1 * s2 - s1 * c2 * c, s1 * s],
        [s1 * c2 + c1 * s2 * c, -s1 * s2 + c1 * c2 * c, -c1 * s],
        [s2 * s, c2 * s, c]
    ])

    # 提取晶体坐标系的三个轴
    axis_100 = g @ np.array([1, 0, 0])  # <100> direction
    axis_010 = g @ np.array([0, 1, 0])  # <010> direction
    axis_001 = g @ np.array([0, 0, 1])  # <001> direction

    # 归一化（理论上已归一化，这里保险起见）
    axis_100 = axis_100 / np.linalg.norm(axis_100)
    axis_010 = axis_010 / np.linalg.norm(axis_010)
    axis_001 = axis_001 / np.linalg.norm(axis_001)

    return axis_100, axis_010, axis_001


def parse_filename(filename):
    """
    解析文件名提取参数（兼容有/无序号、有/无 hw 参数的文件名）

    支持的文件名格式：
    1. 新格式（含序号+hw）: grain_orientations_ODF_142_fGoss0.71_theta0_hw13_N100.txt
    2. 新格式（无序号+hw）: grain_orientations_ODF_fGoss0.70_theta40_hw10_N100.txt
    3. 旧格式（无序号无hw）: grain_orientations_ODF_fGoss0.70_theta40_N100.txt

    返回: {
        'f_goss': 0.70,
        'theta_0': 40,
        'halfwidth': 10,  # 如果存在
        'n_grains': 100,
        'short_name': 'F70_T40_N100'  # 保持现有格式，不含hw和序号
    }
    """
    # 尝试匹配新格式（含序号+hw参数）
    pattern_with_index_hw = r'grain_orientations_ODF_(\d+)_fGoss([\d.]+)_theta(\d+)_hw([\d.]+)_N(\d+)\.txt'
    match = re.search(pattern_with_index_hw, filename)

    if match:
        # 新格式：含序号 + hw 参数
        index = int(match.group(1))
        f_goss = float(match.group(2))
        theta_0 = int(match.group(3))
        halfwidth = float(match.group(4))
        n_grains = int(match.group(5))
    else:
        # 尝试匹配新格式（无序号+hw参数）
        pattern_no_index_hw = r'grain_orientations_ODF_fGoss([\d.]+)_theta(\d+)_hw([\d.]+)_N(\d+)\.txt'
        match = re.search(pattern_no_index_hw, filename)

        if match:
            # 新格式：无序号 + hw 参数
            index = None
            f_goss = float(match.group(1))
            theta_0 = int(match.group(2))
            halfwidth = float(match.group(3))
            n_grains = int(match.group(4))
        else:
            # 尝试匹配旧格式（无序号无hw参数）
            pattern_old = r'grain_orientations_ODF_fGoss([\d.]+)_theta(\d+)_N(\d+)\.txt'
            match = re.search(pattern_old, filename)

            if not match:
                raise ValueError(f"无法解析文件名: {filename}")

            # 旧格式：无序号 + 无 hw 参数
            index = None
            f_goss = float(match.group(1))
            theta_0 = int(match.group(2))
            halfwidth = None  # 旧文件无此参数
            n_grains = int(match.group(3))

    # 生成简称（保持现有格式，不包含 hw 和序号）
    f_goss_short = f"F{int(f_goss * 100):02d}"  # 0.70 -> F70
    theta_short = f"T{theta_0}"  # 40 -> T40
    n_grains_short = f"N{n_grains}"  # 100 -> N100
    short_name = f"{f_goss_short}_{theta_short}_{n_grains_short}"
    if index is not None:
        short_name = f"S{index:03d}_{short_name}"

    result = {
        'f_goss': f_goss,
        'theta_0': theta_0,
        'n_grains': n_grains,
        'short_name': short_name,
        'filename': filename
    }

    # 如果存在 hw 参数，添加到返回字典中
    if halfwidth is not None:
        result['halfwidth'] = halfwidth

    # 如果存在序号，添加到返回字典中
    if index is not None:
        result['index'] = index

    return result


def read_texture_metadata(texture_file: str) -> dict:
    """Read '#   key: value' metadata written by odf_texture.py."""
    meta = {}
    try:
        with open(texture_file, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                if not line.startswith('#'):
                    continue
                m = re.match(r'#\s+([A-Za-z0-9_]+)\s*:\s*(.+?)\s*$', line)
                if m:
                    meta[m.group(1)] = m.group(2)
    except Exception:
        pass
    return meta


def scan_input_files(input_dir=SimulationConfig.INPUT_DIR):
    """
    扫描input目录下的所有grain_orientations_ODF文件及批量子文件夹。

    返回: 条目列表，每个条目为以下之一：
      - 单一文件: {'item_type':'single', 'filename':..., 'full_path':..., ...params}
      - 批量文件夹: {'item_type':'batch', 'folder_name':..., 'folder_path':...,
                     'files':[...], 'n_total':N, 'short_name':...}
    """
    input_path = Path(input_dir)
    if not input_path.exists():
        print(f"警告: '{input_dir}' 目录不存在，将在当前目录查找文件")
        input_path = Path('.')

    items = []
    pattern = 'grain_orientations_ODF_*.txt'

    # 1. 直接在 input/ 下的单一 txt 文件
    for file_path in sorted(input_path.glob(pattern)):
        try:
            info = parse_filename(file_path.name)
            info['full_path'] = str(file_path)
            info['item_type'] = 'single'
            items.append(info)
        except ValueError as e:
            print(f"跳过文件: {file_path.name} ({e})")

    # 2. 子目录（批量批次文件夹）
    for subdir in sorted(input_path.iterdir()):
        if not subdir.is_dir():
            continue
        batch_files = sorted(subdir.glob(pattern))
        if not batch_files:
            continue
        batch_file_infos = []
        for fp in batch_files:
            try:
                info = parse_filename(fp.name)
                info['full_path'] = str(fp)
                info['item_type'] = 'single'
                batch_file_infos.append(info)
            except ValueError as e:
                print(f"  跳过批量文件: {fp.name} ({e})")
        if batch_file_infos:
            items.append({
                'item_type': 'batch',
                'folder_name': subdir.name,
                'folder_path': str(subdir),
                'files': batch_file_infos,
                'n_total': len(batch_file_infos),
                'short_name': subdir.name
            })

    if not items:
        raise FileNotFoundError(f"未在 '{input_dir}' 目录找到匹配的文件或批量文件夹")

    return items


def read_orientations(file_path):
    """读取晶粒取向文件"""
    orientations = []
    with open(file_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#'):
                parts = line.split()
                if len(parts) == 3:
                    orientations.append([float(x) for x in parts])
    return orientations


def get_angle_folder_name(angle):
    """将角度转换为文件夹名称: 30 -> angle_030"""
    return f"angle_{int(angle):03d}"


# ========================================
# MX3脚本生成函数
# ========================================
def generate_single_mode_script(grain_id, phi1, Phi, phi2, angle):
    """
    生成单角度single模式的mx3脚本(修正为预饱和+4段完整回线)

    Args:
        grain_id: 晶粒编号
        phi1, Phi, phi2: 欧拉角(Bunge约定)
        angle: 外场角度(度,样品坐标系中)
    """
    # 获取晶体坐标系的三个轴
    axis_100, axis_010, axis_001 = euler_to_crystal_axes(phi1, Phi, phi2)

    # 构建旋转矩阵:从样品坐标系到晶体坐标系
    rotation_matrix = np.column_stack([axis_100, axis_010, axis_001])
    R_s2c = rotation_matrix.T  # 样品 -> 晶体

    # 外场方向(样品坐标系)
    angle_rad = np.radians(angle)
    H_sample = np.array([np.cos(angle_rad), np.sin(angle_rad), 0])

    # 转换到晶体坐标系
    H_crystal = R_s2c @ H_sample
    H_crystal = H_crystal / np.linalg.norm(H_crystal)  # 归一化

    # 从配置类获取参数
    cfg = SimulationConfig
    Ku1_value = cfg.get_Ku1()
    Msat_value = cfg.MSAT
    Aex_value = cfg.AEX
    alpha_value = cfg.ALPHA
    H_max_value = cfg.H_MAX
    n_steps_value = cfg.N_STEPS
    si_percent = cfg.SI_CONTENT

    # 网格参数
    grid_x, grid_y, grid_z = cfg.GRID_SIZE_X, cfg.GRID_SIZE_Y, cfg.GRID_SIZE_Z
    cell_size = cfg.CELL_SIZE
    rve_x, rve_y, rve_z = cfg.get_rve_size_um()

    script_content = f"""// Grain {grain_id} simulation - {angle} degree direction
// Euler angles: phi1={phi1:.2f}, Phi={Phi:.2f}, phi2={phi2:.2f}
// Material: Fe-{si_percent}%Si
// Applied field angle: {angle} degrees (in sample frame)

// ============================================
// Grid settings - Single grain RVE
// ============================================
// RVE size: {rve_x:.3f} um x {rve_y:.3f} um x {rve_z:.3f} um
SetGridSize({grid_x}, {grid_y}, {grid_z})
SetCellSize({cell_size:.2e}, {cell_size:.2e}, {cell_size/4:.2e})

// ============================================
// Fe-Si material parameters
// ============================================
Msat = {Msat_value:.2e}     // Saturation magnetization (A/m)
Aex = {Aex_value:.2e}        // Exchange constant (J/m)
alpha = {alpha_value:.3f}    // Gilbert damping

// ============================================
// Cubic anisotropy (Fe-Si crystal)
// ============================================
// Energy: E = Ku1 * (α1²α2² + α2²α3² + α3²α1²) + Ku2 * (α1²α2²α3²)
// The simulation xyz axes correspond to crystal <100>, <010>, <001> directions

Ku1 = {Ku1_value:.2e}        // First cubic anisotropy constant (J/m³)
Ku2 = 0                      // Second cubic anisotropy (negligible for Fe-Si)

// ============================================
// Crystal orientation information
// ============================================
// Crystal axes in sample frame:
// <100>: ({axis_100[0]:.6f}, {axis_100[1]:.6f}, {axis_100[2]:.6f})
// <010>: ({axis_010[0]:.6f}, {axis_010[1]:.6f}, {axis_010[2]:.6f})
// <001>: ({axis_001[0]:.6f}, {axis_001[1]:.6f}, {axis_001[2]:.6f})

// ============================================
// Field direction in CRYSTAL frame
// ============================================
// Sample frame: angle = {angle}° → ({H_sample[0]:.6f}, {H_sample[1]:.6f}, {H_sample[2]:.6f})
// Crystal frame: ({H_crystal[0]:.6f}, {H_crystal[1]:.6f}, {H_crystal[2]:.6f})

H_max := {H_max_value:.1f}   // Maximum field magnitude (A/m)
n_steps := {n_steps_value}   // Steps per segment

// Field direction unit vector in crystal coordinates
Hx_dir := {H_crystal[0]:.10f}
Hy_dir := {H_crystal[1]:.10f}
Hz_dir := {H_crystal[2]:.10f}

// ============================================
// Pre-saturation: Start from saturated state
// ============================================
print("Pre-saturation: magnetizing to +H_max")
B_ext = vector(H_max*mu0*Hx_dir, H_max*mu0*Hy_dir, H_max*mu0*Hz_dir)
m = uniform(Hx_dir, Hy_dir, Hz_dir)
minimize()
print("Initial saturation completed")

// ============================================
// Output setup
// ============================================
tableadd(B_ext)              // Applied field (T)
tableadd(m)                  // Average magnetization (normalized)
tableadd(E_total)            // Total energy (J)
tableautosave(10e-12)        // Auto-save every 10 ps

// ============================================
// Hysteresis loop: +H_max → 0 → -H_max → 0 → +H_max
// ============================================

// Segment 1: Descending from saturation (+H_max → 0)
print("Segment 1: +H_max -> 0")
for i := 0; i <= n_steps; i++ {{
    H := H_max - i*H_max/n_steps
    B_ext = vector(H*mu0*Hx_dir, H*mu0*Hy_dir, H*mu0*Hz_dir)
    minimize()
    tablesave()
}}

// Segment 2: Reverse magnetization (0 → -H_max)
print("Segment 2: 0 -> -H_max")
for i := 0; i <= n_steps; i++ {{
    H := -i*H_max/n_steps
    B_ext = vector(H*mu0*Hx_dir, H*mu0*Hy_dir, H*mu0*Hz_dir)
    minimize()
    tablesave()
}}

// Segment 3: Return to zero field (-H_max → 0)
print("Segment 3: -H_max -> 0")
for i := 0; i <= n_steps; i++ {{
    H := -H_max + i*H_max/n_steps
    B_ext = vector(H*mu0*Hx_dir, H*mu0*Hy_dir, H*mu0*Hz_dir)
    minimize()
    tablesave()
}}

// Segment 4: Return to saturation (0 → +H_max)
print("Segment 4: 0 -> +H_max (closing loop)")
for i := 0; i <= n_steps; i++ {{
    H := i*H_max/n_steps
    B_ext = vector(H*mu0*Hx_dir, H*mu0*Hy_dir, H*mu0*Hz_dir)
    minimize()
    tablesave()
}}

print("========================================")
print("Hysteresis loop simulation completed!")
print("Total data points: {4 * (n_steps_value + 1)}")
print("Loop type: Symmetric major loop")
print("Suitable for: FEMM B-H curve import")
print("========================================")
"""
    return script_content


def generate_complex_mode_script(grain_id, phi1, Phi, phi2, angles):
    """生成多角度complex模式的mx3脚本(修正为预饱和+4段完整回线)"""

    # 获取晶体坐标系
    axis_100, axis_010, axis_001 = euler_to_crystal_axes(phi1, Phi, phi2)
    rotation_matrix = np.column_stack([axis_100, axis_010, axis_001])
    R_s2c = rotation_matrix.T

    # 为每个角度计算晶体坐标系中的场方向
    field_directions = []
    for angle in angles:
        angle_rad = np.radians(angle)
        H_sample = np.array([np.cos(angle_rad), np.sin(angle_rad), 0])
        H_crystal = R_s2c @ H_sample
        H_crystal = H_crystal / np.linalg.norm(H_crystal)
        field_directions.append(H_crystal)

    # 从配置类获取参数
    cfg = SimulationConfig
    Ku1_value = cfg.get_Ku1()
    Msat_value = cfg.MSAT
    Aex_value = cfg.AEX
    alpha_value = cfg.ALPHA
    H_max_value = cfg.H_MAX
    n_steps_value = cfg.N_STEPS
    si_percent = cfg.SI_CONTENT

    grid_x, grid_y, grid_z = cfg.GRID_SIZE_X, cfg.GRID_SIZE_Y, cfg.GRID_SIZE_Z
    cell_size = cfg.CELL_SIZE
    rve_x, rve_y, rve_z = cfg.get_rve_size_um()

    # 构建角度和方向数组
    angles_str = ", ".join([str(float(a)) for a in angles])

    # 构建方向数组字符串
    field_dirs_str = ""
    for i, (angle, H_crys) in enumerate(zip(angles, field_directions)):
        field_dirs_str += f"    // {angle}°: ({H_crys[0]:.10f}, {H_crys[1]:.10f}, {H_crys[2]:.10f})\n"

    script_content = f"""// Grain {grain_id} multi-angle simulation
// Euler angles: phi1={phi1:.2f}, Phi={Phi:.2f}, phi2={phi2:.2f}
// Material: Fe-{si_percent}%Si
// Angles: {angles_str} degrees

// ============================================
// Grid settings
// ============================================
SetGridSize({grid_x}, {grid_y}, {grid_z})
SetCellSize({cell_size:.2e}, {cell_size:.2e}, {cell_size/4:.2e})

// ============================================
// Material parameters
// ============================================
Msat = {Msat_value:.2e}
Aex = {Aex_value:.2e}
alpha = {alpha_value:.3f}

// ============================================
// Cubic anisotropy
// ============================================
Ku1 = {Ku1_value:.2e}
Ku2 = 0

// ============================================
// Field directions in crystal frame
// ============================================
{field_dirs_str}
H_max := {H_max_value:.1f}
n_steps := {n_steps_value}

// Angle array
angles := []float64{{{angles_str}}}

// Field direction arrays (in crystal frame)
Hx_dirs := []float64{{{", ".join([f"{h[0]:.10f}" for h in field_directions])}}}
Hy_dirs := []float64{{{", ".join([f"{h[1]:.10f}" for h in field_directions])}}}
Hz_dirs := []float64{{{", ".join([f"{h[2]:.10f}" for h in field_directions])}}}

// ============================================
// Loop over all angles
// ============================================
for angle_idx, angle := range angles {{
    Hx_dir := Hx_dirs[angle_idx]
    Hy_dir := Hy_dirs[angle_idx]
    Hz_dir := Hz_dirs[angle_idx]

    print(sprint("\\n========================================"))
    print(sprint("Processing angle: ", angle, " degrees"))
    print(sprint("Field direction: (", Hx_dir, ", ", Hy_dir, ", ", Hz_dir, ")"))
    print(sprint("========================================\\n"))

    // Pre-saturation for each angle
    print("Pre-saturation: magnetizing to +H_max")
    B_ext = vector(H_max*mu0*Hx_dir, H_max*mu0*Hy_dir, H_max*mu0*Hz_dir)
    m = uniform(Hx_dir, Hy_dir, Hz_dir)
    minimize()
    print("Initial saturation completed")

    tableadd(B_ext)
    tableadd(m)
    tableadd(E_total)

    // Segment 1: Descending from saturation (+H_max → 0)
    print("Segment 1: +H_max -> 0")
    for i := 0; i <= n_steps; i++ {{
        H := H_max - i*H_max/n_steps
        B_ext = vector(H*mu0*Hx_dir, H*mu0*Hy_dir, H*mu0*Hz_dir)
        minimize()
        tablesave()
    }}

    // Segment 2: Reverse magnetization (0 → -H_max)
    print("Segment 2: 0 -> -H_max")
    for i := 0; i <= n_steps; i++ {{
        H := -i*H_max/n_steps
        B_ext = vector(H*mu0*Hx_dir, H*mu0*Hy_dir, H*mu0*Hz_dir)
        minimize()
        tablesave()
    }}

    // Segment 3: Return to zero (-H_max → 0)
    print("Segment 3: -H_max -> 0")
    for i := 0; i <= n_steps; i++ {{
        H := -H_max + i*H_max/n_steps
        B_ext = vector(H*mu0*Hx_dir, H*mu0*Hy_dir, H*mu0*Hz_dir)
        minimize()
        tablesave()
    }}

    // Segment 4: Return to saturation (0 → +H_max)
    print("Segment 4: 0 -> +H_max (closing loop)")
    for i := 0; i <= n_steps; i++ {{
        H := i*H_max/n_steps
        B_ext = vector(H*mu0*Hx_dir, H*mu0*Hy_dir, H*mu0*Hz_dir)
        minimize()
        tablesave()
    }}

    print(sprint("Angle ", angle, " completed (204 data points)\\n"))
}}

print("\\nAll angles completed!")
"""
    return script_content


def generate_simulation_parameters(config_info, angles, output_path):
    """
    生成simulation_parameters.txt文件（参数自动从配置读取）

    Args:
        config_info: 配置信息字典
        angles: 角度列表
        output_path: 输出文件路径
    """
    cfg = SimulationConfig

    # 计算参数
    Ku1_value = cfg.get_Ku1()
    Msat_value = cfg.MSAT
    Aex_value = cfg.AEX
    alpha_value = cfg.ALPHA
    H_max_value = cfg.H_MAX
    n_steps_value = cfg.N_STEPS
    si_percent = cfg.SI_CONTENT

    # RVE尺寸
    rve_x, rve_y, rve_z = cfg.get_rve_size_um()
    grid_x, grid_y, grid_z = cfg.GRID_SIZE_X, cfg.GRID_SIZE_Y, cfg.GRID_SIZE_Z
    cell_size_nm = cfg.CELL_SIZE * 1e9

    # 计算交换长度
    mu0 = 4 * np.pi * 1e-7
    lambda_ex = np.sqrt(2 * Aex_value / (mu0 * Msat_value ** 2)) * 1e9  # 转换为nm

    # 计算每段数据点
    total_points = 4 * (n_steps_value + 1)  # 每段 n_steps+1 个点

    # 角度字符串
    angles_str = ", ".join([f"{a}°" for a in angles])

    # H_max转换为其他单位
    H_max_mT = H_max_value * mu0 * 1000  # mT
    H_max_Oe = H_max_value / 79.5775  # Oe

    # 处理 halfwidth 参数：如果存在则显示值，否则显示 "-"
    halfwidth_display = f"{config_info['halfwidth']}°" if 'halfwidth' in config_info else "-"
    halfwidth_policy = config_info.get('halfwidth_policy', 'sampled_or_unspecified')

    content = f"""Fe-Si Polycrystalline Micromagnetic Simulation Parameters
{"=" * 60}

Configuration Information:
{"-" * 60}
Configuration name:                  {config_info['short_name']}
Source file:                         {config_info['filename']}
Goss texture fraction (f_Goss):      {config_info['f_goss']}
Texture rotation angle (theta_0):    {config_info['theta_0']} degrees
Texture halfwidth (sigma):           {halfwidth_display}
Halfwidth policy:                    {halfwidth_policy}
Number of grains:                    {config_info['n_grains']}
Simulated angles:                    {angles_str}

Material Parameters:
{"-" * 60}
1. Goss texture fraction (f_Goss):     {config_info['f_goss']}
2. Texture rotation angle (theta_0):   {config_info['theta_0']} degrees
3. Texture halfwidth (sigma):          {halfwidth_display}
   Note: Represents the Gaussian standard deviation used during ODF
         texture generation. Smaller values → sharper texture peak;
         Larger values → more dispersed orientations around ideal Goss.
         "-" indicates parameter not available in source file.
   Halfwidth policy: {halfwidth_policy}
4. Average grain size (d_grain):       Not explicitly modeled (implied by RVE)
5. Plate thickness (t):                0.35 mm (reference for post-processing)
6. Edge damage depth (delta_stress):   0 um (no edge effect modeled)
7. Si content (Si%):                   {si_percent}%
8. Working frequency (f):              Quasi-static

Micromagnetic Parameters (MESO-SCALE ADJUSTED):
{"-" * 60}
Saturation magnetization (Msat):       {Msat_value:.2e} A/m ({Msat_value * mu0:.3f} T)
Exchange constant (Aex):               {Aex_value:.2e} J/m
  Note: Reduced from bulk value (2.1e-11) to account for grain boundary
        weakening effects at meso-scale
Exchange length (λ_ex):                {lambda_ex:.2f} nm
Gilbert damping (alpha):               {alpha_value:.3f}
  Note: Increased from 0.01 to enhance convergence and approximate
        macroscopic quasi-static processes
Cubic anisotropy (Ku1):                {Ku1_value:.2e} J/m³
  Calculated from: (4.8 - 0.4 * {si_percent}) × 1e4 J/m³  [Moses 2012 Si-dependence]

Simulation Settings:
{"-" * 60}
Grid size:                             {grid_x} x {grid_y} x {grid_z}
Cell size:                             {cell_size_nm:.1f} nm x {cell_size_nm:.1f} nm x {cell_size_nm/4:.1f} nm
Total RVE volume:                      {rve_x:.2f} μm x {rve_y:.2f} μm x {rve_z:.3f} μm
Cell-to-exchange ratio:                {cell_size_nm / lambda_ex:.2f} (cell_size / λ_ex)
  Note: Ratio > 1 indicates coarse-grained simulation suitable for
        meso-scale modeling. Each cell represents averaged behavior.

Maximum applied field (H_max):         {H_max_value:.0f} A/m ({H_max_mT:.1f} mT, ~{H_max_Oe:.0f} Oe)
  Note: H_k = 2*Ku1/(mu0*Msat) ≈ 36728 A/m; H_max={H_max_value:.0f} > H_k ensures full magnetization reversal
Steps per segment:                     {n_steps_value}
Total data points per grain:           {total_points} (4 segments of hysteresis loop)
Number of grains:                      {config_info['n_grains']}

Scaling Rationale:
{"-" * 60}
The simulation uses a meso-scale approach (RVE ~ 2-3 μm) instead of
nano-scale (< 100 nm) to:

1. Reduce exchange energy dominance:
   - At nano-scale: Strong exchange coupling suppresses domain wall
     formation, leading to artificially high coercivity
   - At meso-scale: Weaker relative exchange allows more realistic
     domain structures

2. Approximate polycrystalline behavior:
   - Each RVE represents an effective single-crystal volume
   - Grain boundary effects incorporated through reduced Aex
   - Statistical averaging over {config_info['n_grains']} grains captures texture effects

3. Computational efficiency:
   - Coarser mesh ({cell_size_nm:.0f} nm vs 10 nm) reduces computation by 125x per grain
   - Higher damping ({alpha_value} vs 0.01) accelerates convergence

4. Limitations and considerations:
   - Absolute values (Hc, μi) still higher than bulk steel due to
     mesoscale approximation
   - Results suitable for relative comparison (texture parameter effects)
   - For quantitative motor design, use extracted trends to calibrate
     macroscopic constitutive models in FEMM/Maxwell

Expected Performance Improvements vs Nano-scale:
{"-" * 60}
Parameter              Nano-scale        Meso-scale        Real Steel
                       (10nm cells)      ({cell_size_nm:.0f}nm cells)      (bulk)
Coercivity Hc (A/m)    30,000-90,000     500-5,000         10-100
Initial perm. μi       2-10              500-5,000         5,000-50,000
Br/Bs ratio            0.7-0.95          0.3-0.6           0.2-0.5

CRITICAL: B_ext Unit Conversion
{"-" * 60}
All generated .mx3 scripts correctly calculate B_ext as:
    B_ext = vector(H*mu0*cos_angle, H*mu0*sin_angle, 0)
where mu0 = 4π×10⁻⁷ H/m is the permeability of free space.

This converts H (A/m) to B (Tesla) before assignment to B_ext.

Generation Information:
{"-" * 60}
Generated on:                          {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
Simulation mode:                       {SimulationConfig.SIMULATION_TYPE}
Parameter version:                     Meso-scale v3.0
Script generator:                      generate_individual_scripts.py
"""

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(content)

    print(f"参数文件已保存: {output_path}")


# ========================================
# 主生成函数
# ========================================
def generate_single_mode(config_info, angles, orientations):
    """
    生成single模式的仿真脚本（每个角度独立文件）

    Args:
        config_info: 配置信息字典
        angles: 角度列表
        orientations: 晶粒取向列表
    """
    short_name = config_info['short_name']
    base_dir = Path(SimulationConfig.OUTPUT_DIR) / short_name
    base_dir.mkdir(parents=True, exist_ok=True)

    # 从配置读取参数
    cfg = SimulationConfig
    rve_x, rve_y, rve_z = cfg.get_rve_size_um()

    print(f"\n正在生成 single 模式脚本...")
    print(f"输出目录: {base_dir}")
    print(f"角度: {angles}")
    print(f"晶粒数: {len(orientations)}")
    print(f"RVE尺寸: {rve_x:.2f} × {rve_y:.2f} × {rve_z:.3f} μm")
    print(f"Msat: {cfg.MSAT:.2e} A/m")
    print(f"Aex: {cfg.AEX:.2e} J/m")
    print(f"H_max: {cfg.H_MAX:.0f} A/m")
    print(f"alpha: {cfg.ALPHA}")
    print(f"网格: {cfg.GRID_SIZE_X}×{cfg.GRID_SIZE_Y}×{cfg.GRID_SIZE_Z}")
    print(f"网格尺寸: {cfg.CELL_SIZE * 1e9:.1f} nm\n")

    # 为每个角度创建目录并生成脚本
    for angle in angles:
        angle_dir = base_dir / get_angle_folder_name(angle)
        angle_dir.mkdir(exist_ok=True)

        print(f"生成角度 {angle}° 的脚本...")

        for i, (phi1, Phi, phi2) in enumerate(orientations):
            grain_id = i + 1

            script_content = generate_single_mode_script(
                grain_id, phi1, Phi, phi2, angle
            )

            script_file = angle_dir / f"grain_{grain_id:03d}.mx3"
            with open(script_file, 'w', encoding='utf-8') as f:
                f.write(script_content)

            if grain_id % 20 == 0:
                print(f"  已生成 grain_{grain_id:03d}.mx3")

        print(f"  完成! 共 {len(orientations)} 个文件\n")

    # 生成参数文件模板
    param_file = base_dir / "simulation_parameters_template.txt"
    generate_simulation_parameters(config_info, angles, param_file)

    print(f"\n✓ Single模式脚本生成完成!")
    print(f"  总计: {len(angles)} 个角度 × {len(orientations)} 个晶粒 = {len(angles) * len(orientations)} 个文件")


def generate_complex_mode(config_info, angles, orientations):
    """
    生成complex模式的仿真脚本（单个文件包含所有角度）

    Args:
        config_info: 配置信息字典
        angles: 角度列表
        orientations: 晶粒取向列表
    """
    short_name = config_info['short_name']
    base_dir = Path(SimulationConfig.OUTPUT_DIR) / short_name / "all_angles_combined"
    base_dir.mkdir(parents=True, exist_ok=True)

    # 从配置读取参数
    cfg = SimulationConfig
    rve_x, rve_y, rve_z = cfg.get_rve_size_um()

    print(f"\n正在生成 complex 模式脚本...")
    print(f"输出目录: {base_dir}")
    print(f"角度: {angles}")
    print(f"晶粒数: {len(orientations)}")
    print(f"RVE尺寸: {rve_x:.2f} × {rve_y:.2f} × {rve_z:.3f} μm")
    print(f"Msat: {cfg.MSAT:.2e} A/m")
    print(f"Aex: {cfg.AEX:.2e} J/m")
    print(f"H_max: {cfg.H_MAX:.0f} A/m")
    print(f"alpha: {cfg.ALPHA}")
    print(f"网格: {cfg.GRID_SIZE_X}×{cfg.GRID_SIZE_Y}×{cfg.GRID_SIZE_Z}")
    print(f"网格尺寸: {cfg.CELL_SIZE * 1e9:.1f} nm\n")

    for i, (phi1, Phi, phi2) in enumerate(orientations):
        grain_id = i + 1

        script_content = generate_complex_mode_script(
            grain_id, phi1, Phi, phi2, angles
        )

        script_file = base_dir / f"grain_{grain_id:03d}_multiangle.mx3"
        with open(script_file, 'w', encoding='utf-8') as f:
            f.write(script_content)

        if grain_id % 20 == 0:
            print(f"已生成 grain_{grain_id:03d}_multiangle.mx3")

    # 生成参数文件
    param_file = base_dir / "simulation_parameters.txt"
    generate_simulation_parameters(config_info, angles, param_file)

    print(f"\n✓ Complex模式脚本生成完成!")
    print(f"  总计: {len(orientations)} 个文件（每个包含 {len(angles)} 个角度）")
    print(f"\n⚠ 注意: Complex模式下需要手动整理MuMax3的输出文件到对应角度目录")


# ========================================
# 交互界面
# ========================================
def select_files(file_infos):
    """交互式选择要处理的文件"""
    print("\n" + "=" * 60)
    print("检测到以下织构文件:")
    print("=" * 60)

    for idx, info in enumerate(file_infos, 1):
        print(f"[{idx}] {info['filename']}")
        print(f"    简称: {info['short_name']}")
        print(f"    参数: f_Goss={info['f_goss']}, theta={info['theta_0']}°, N={info['n_grains']}")
        print()

    while True:
        choice = input("请选择要处理的文件（输入序号如'1,3'，或'all'处理全部，'q'退出）: ").strip()

        if choice.lower() == 'q':
            return []
        elif choice.lower() == 'all':
            return file_infos
        else:
            try:
                indices = [int(x.strip()) for x in choice.split(',')]
                selected = [file_infos[i - 1] for i in indices if 1 <= i <= len(file_infos)]
                if selected:
                    return selected
                else:
                    print("无效的选择，请重新输入")
            except (ValueError, IndexError):
                print("输入格式错误，请重新输入")


def select_simulation_mode():
    """选择仿真模式"""
    print("\n" + "=" * 60)
    print("选择仿真模式:")
    print("=" * 60)
    print("  [1] single  - 每个角度生成独立mx3文件（推荐，支持并行）")
    print("  [2] complex - 单个mx3文件包含所有角度（串行仿真）")
    print()

    while True:
        choice = input("您的选择 [1]: ").strip() or '1'
        if choice == '1':
            return 'single'
        elif choice == '2':
            return 'complex'
        else:
            print("无效选择，请输入 1 或 2")


def select_angles():
    """选择仿真角度"""
    print("\n选择仿真角度:")
    print(f"  - 直接按回车使用默认角度: {SimulationConfig.DEFAULT_ANGLES}")
    print("  - 输入自定义角度（逗号分隔）: 例如 15, 45, 75")
    print()

    while True:
        choice = input("您的输入: ").strip()

        if not choice:
            return SimulationConfig.DEFAULT_ANGLES

        try:
            angles = [float(x.strip()) for x in choice.split(',')]
            if all(0 <= a <= 180 for a in angles):
                return sorted(angles)
            else:
                print("角度必须在0-180度之间")
        except ValueError:
            print("输入格式错误，请使用逗号分隔的数字")


def confirm_generation(config_info, mode, angles):
    """确认生成配置"""
    print("\n" + "=" * 60)
    print("生成配置确认")
    print("=" * 60)
    print(f"文件: {config_info['filename']}")
    print(f"简称: {config_info['short_name']}")
    print(f"晶粒数: {config_info['n_grains']}")
    print(f"模式: {mode}")
    print(f"角度: {', '.join([f'{a}°' for a in angles])}")
    print(f"输出: {SimulationConfig.OUTPUT_DIR}/{config_info['short_name']}/")
    print()

    choice = input("继续生成？[Y/n]: ").strip().lower()
    return choice != 'n'


# ========================================
# 主程序
# ========================================
def main():
    print("=" * 60)
    print("Fe-Si多晶微磁学仿真脚本生成器")
    print("=" * 60)
    print(f"当前默认模式: {SimulationConfig.SIMULATION_TYPE}")
    print("=" * 60)

    try:
        # 1. 扫描并选择文件
        file_infos = scan_input_files()
        selected_files = select_files(file_infos)
        if not selected_files:
            print("未选择文件，退出")
            return

        # 2. 判断是否启用批量统一配置
        use_batch_config = False
        if len(selected_files) > 1:
            print("\n检测到多个文件。请选择处理方式：")
            print("[1] 为每个文件单独配置（逐个设置模式和角度）")
            print("[2] 对所有文件使用统一配置（一键批量生成）")
            while True:
                choice = input("请输入选项 (1 或 2): ").strip()
                if choice == '1':
                    use_batch_config = False
                    break
                elif choice == '2':
                    use_batch_config = True
                    break
                else:
                    print("无效输入，请输入 1 或 2")

        # 3. 如果启用统一配置，则提前获取全局参数
        global_mode = None
        global_angles = None
        if use_batch_config:
            print("\n【统一配置】请设置所有文件共用的仿真参数：")
            global_mode = select_simulation_mode()
            global_angles = select_angles()

        # 4. 处理每个选中的文件
        for config_info in selected_files:
            print("\n" + "=" * 60)
            print(f"处理文件: {config_info['filename']}")
            print("=" * 60)

            # 确定当前文件使用的 mode 和 angles
            if use_batch_config:
                mode = global_mode
                angles = global_angles
                print(f"使用统一配置: 模式={mode}, 角度={[f'{a}°' for a in angles]}")
            else:
                # 单独配置（兼容单文件或多文件但选了逐个配置）
                mode = SimulationConfig.SIMULATION_TYPE
                if mode == 'single':
                    mode = select_simulation_mode()
                else:
                    print(f"\n使用配置的模式: {mode}")
                angles = select_angles()

                if not confirm_generation(config_info, mode, angles):
                    print("跳过此文件")
                    continue

            # 读取取向数据
            print("\n读取晶粒取向数据...")
            orientations = read_orientations(config_info['full_path'])
            if len(orientations) != config_info['n_grains']:
                print(f"警告: 文件名标注{config_info['n_grains']}个晶粒，"
                      f"但实际读取到{len(orientations)}个")
                config_info['n_grains'] = len(orientations)
            print(f"成功读取 {len(orientations)} 个晶粒取向")

            # 生成脚本
            if mode == 'single':
                generate_single_mode(config_info, angles, orientations)
            else:
                generate_complex_mode(config_info, angles, orientations)

        print("\n" + "=" * 60)
        print("所有文件处理完成!")
        print("=" * 60)

    except Exception as e:
        print(f"\n错误: {e}")
        import traceback
        traceback.print_exc()


# ── 角度模式常量（新增，不修改 DEFAULT_ANGLES）────────────────────
MOTOR_OPTIMIZATION_ANGLES = [0, 45, 90]   # RD / 45° 交叉 / TD，与 Maxwell FEA 对齐


def get_available_modes() -> dict:
    """返回所有可用角度模式的描述。"""
    return {
        'full': {
            'name':        '全极图模式',
            'angles':      SimulationConfig.DEFAULT_ANGLES,
            'description': '9 个离散角度，用于完整各向异性极图可视化和学术分析',
        },
        'motor': {
            'name':        '电机优化模式',
            'angles':      MOTOR_OPTIMIZATION_ANGLES,
            'description': '3 个角度 (RD=0°/45°/TD=90°)，直接对应 ANSYS Maxwell 各向异性材料定义，仿真时间约为全极图的 1/3',
        },
    }


def generate_scripts_for_config(texture_file: str,
                                  angles: list = None,
                                  output_dir: str = None) -> str:
    """
    供 pipeline_runner.py 调用：从单个织构文件生成 MX3 脚本。
    返回输出目录路径。
    """
    angles = angles if angles is not None else SimulationConfig.DEFAULT_ANGLES

    config_info = parse_filename(os.path.basename(texture_file))
    config_info['full_path'] = texture_file
    metadata = read_texture_metadata(texture_file)
    if 'halfwidth_policy' in metadata:
        config_info['halfwidth_policy'] = metadata['halfwidth_policy']
    if 'halfwidth_deg' in metadata and 'halfwidth' not in config_info:
        try:
            config_info['halfwidth'] = float(metadata['halfwidth_deg'])
        except ValueError:
            pass

    if output_dir is not None:
        orig_out = SimulationConfig.OUTPUT_DIR
        SimulationConfig.OUTPUT_DIR = output_dir

    orientations = read_orientations(texture_file)
    config_info['n_grains'] = len(orientations)

    generate_single_mode(config_info, angles, orientations)

    if output_dir is not None:
        out_path = str(Path(output_dir) / config_info['short_name'])
        SimulationConfig.OUTPUT_DIR = orig_out
    else:
        out_path = str(Path(SimulationConfig.OUTPUT_DIR) / config_info['short_name'])

    return out_path


def get_configs_in_dir(scripts_dir: str) -> list:
    """
    扫描 scripts_dir 下的配置，返回适合传递给
    batch_scheduler.generate_multi_config_*_script 的 selected_configs 列表。
    """
    result = []
    base = Path(scripts_dir)
    grain_root = Path('grain_scripts').resolve()
    for config_dir in sorted(p for p in base.iterdir() if p.is_dir()):
        angle_data = {}
        for angle_dir in sorted(config_dir.glob('angle_*')):
            angle_m = re.match(r'angle_(\d+)', angle_dir.name)
            if not angle_m:
                continue
            mx3_files = list(angle_dir.glob('grain_*.mx3'))
            if not mx3_files:
                continue
            angle_code = f'{int(angle_m.group(1)):03d}'
            angle_data[angle_code] = {
                'count': len(mx3_files),
                'path': str(angle_dir),
                'angle_value': int(angle_code),
            }
        if angle_data:
            try:
                config_name = str(config_dir.resolve().relative_to(grain_root))
            except ValueError:
                config_name = config_dir.name
            config_name = config_name.replace('/', '\\')
            n_grains = next(iter(angle_data.values()))['count']
            result.append((config_name, {
                'n_grains': n_grains,
                'angles': angle_data,
            }, sorted(angle_data.keys())))
    return result


if __name__ == "__main__":
    main()
