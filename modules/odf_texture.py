"""
高级晶体织构取向分布生成工具 - 使用orix库
改进版:实现多峰高斯型ODF模型和ODF采样
新增:ODF 3D等高线图和φ2 sections可视化
批量生成:科学采样方法(LHS/Sobol/Grid/Random)
"""

import numpy as np
import matplotlib.pyplot as plt
from matplotlib import cm
from mpl_toolkits.mplot3d import Axes3D
import warnings
from typing import Tuple, List, Dict
from dataclasses import dataclass
import platform
import matplotlib

matplotlib.rcParams['axes.unicode_minus'] = False  # 解决负号显示问题
# 添加支持 Unicode 下标字符 (₀₁₂) 的字体；SimSun 不含这些字形
matplotlib.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'SimSun', 'DejaVu Sans']


# 根据操作系统选择合适的中文字体
def configure_fonts():
    """配置全局图表字体设置"""
    # 检测操作系统类型
    system = platform.system()

    # 配置中文字体
    if system == 'Windows':
        chinese_font = 'SimSun'  # Windows系统宋体
    elif system == 'Darwin':
        chinese_font = 'Songti SC'  # macOS系统宋体
    else:
        chinese_font = 'SimSun'  # Linux系统尝试使用宋体

    # 配置英文字体
    english_font = 'Times New Roman'

    # 设置字体
    font_list = [chinese_font, english_font, 'DejaVu Sans']

    # 设置字体大小
    chinese_size = 12
    english_size = 10

    # 配置matplotlib字体
    plt.rcParams['font.family'] = 'sans-serif'
    plt.rcParams['font.sans-serif'] = font_list
    plt.rcParams['axes.unicode_minus'] = False  # 正确显示负号

    # 设置不同元素的字体
    matplotlib.rcParams['font.size'] = english_size  # 默认英文字体大小
    matplotlib.rcParams['axes.titlesize'] = chinese_size  # 标题字体大小
    matplotlib.rcParams['axes.labelsize'] = english_size  # 轴标签字体大小
    matplotlib.rcParams['xtick.labelsize'] = english_size  # x轴刻度标签字体大小
    matplotlib.rcParams['ytick.labelsize'] = english_size  # y轴刻度标签字体大小
    matplotlib.rcParams['legend.fontsize'] = english_size  # 图例字体大小

    # 设置DPI和图表大小
    matplotlib.rcParams['figure.dpi'] = 100
    matplotlib.rcParams['savefig.dpi'] = 300

    # 返回字体配置,以便在特定函数中使用
    return {
        'chinese_font': chinese_font,
        'english_font': english_font,
        'chinese_size': chinese_size,
        'english_size': english_size
    }


try:
    # 导入orix库的核心组件
    from orix import plot
    from orix.quaternion import Orientation, Symmetry, Rotation
    from orix.vector import Miller, Vector3d
    from orix.plot import IPFColorKeyTSL
    from orix.crystal_map import Phase
    import orix

    ORIX_AVAILABLE = True
except ImportError:
    ORIX_AVAILABLE = False
    raise ImportError(
        "需要安装orix库才能使用高级版本。\n"
        "安装方法:pip install orix"
    )


@dataclass
class TextureComponent:
    """织构组分定义"""
    name: str  # 组分名称
    euler_deg: Tuple[float, float, float]  # Euler角(度,Bunge约定)
    weight: float  # 权重(归一化后)
    sigma_deg: float  # 高斯分布标准差(度)

    def __post_init__(self):
        """验证参数"""
        if self.weight < 0:
            raise ValueError(f"权重必须非负,当前值:{self.weight}")
        if self.sigma_deg <= 0:
            raise ValueError(f"标准差必须为正,当前值:{self.sigma_deg}")


@dataclass
class ParameterRange:
    """参数范围定义"""
    f_Goss_min: float = 0.4
    f_Goss_max: float = 0.9
    theta_0_min: float = 0.0
    theta_0_max: float = 45.0
    halfwidth_min: float = 8.0
    halfwidth_max: float = 15.0
    N_grains: int = 1000

    def __post_init__(self):
        """验证参数范围"""
        if not (0 <= self.f_Goss_min <= self.f_Goss_max <= 1):
            raise ValueError(f"f_Goss范围无效: [{self.f_Goss_min}, {self.f_Goss_max}]")
        if not (0 <= self.theta_0_min <= self.theta_0_max <= 90):
            raise ValueError(f"theta_0范围无效: [{self.theta_0_min}, {self.theta_0_max}]")
        if not (0 < self.halfwidth_min <= self.halfwidth_max):
            raise ValueError(f"halfwidth范围无效: [{self.halfwidth_min}, {self.halfwidth_max}]")


class MultiPeakODF:
    """
    多峰高斯型ODF模型

    数学形式:f(g) = Σ w_i * exp(-ω²(g, g_i) / (2σ_i²))

    其中:
    - g: 取向
    - g_i: 第i个理想织构组分的取向
    - w_i: 权重(归一化)
    - ω(g, g_i): 取向空间中的角度距离(弧度)
    - σ_i: 织构锐度(标准差,弧度)
    """

    def __init__(self, symmetry: Symmetry):
        """
        初始化多峰ODF

        参数:
            symmetry: 晶体对称性
        """
        self.symmetry = symmetry
        self.components: List[Tuple[Orientation, float, float]] = []
        self.is_normalized = False

    def add_component(self,
                      center_orientation: Orientation,
                      weight: float,
                      sigma_deg: float):
        """
        添加一个织构组分

        参数:
            center_orientation: 中心取向
            weight: 权重(未归一化)
            sigma_deg: 高斯分布标准差(度)
        """
        if weight < 0:
            raise ValueError(f"权重必须非负:{weight}")
        if sigma_deg <= 0:
            raise ValueError(f"标准差必须为正:{sigma_deg}")

        sigma_rad = np.radians(sigma_deg)
        self.components.append((center_orientation, weight, sigma_rad))
        self.is_normalized = False
        print(f"已添加织构组分:权重={weight:.3f}, σ={sigma_deg:.1f}°")

    def normalize_weights(self):
        """归一化权重,使其和为1"""
        if not self.components:
            raise ValueError("没有织构组分")

        total_weight = sum(w for _, w, _ in self.components)
        if total_weight == 0:
            raise ValueError("总权重为0")

        self.components = [
            (ori, w / total_weight, sigma)
            for ori, w, sigma in self.components
        ]
        self.is_normalized = True
        print(f"权重已归一化,组分数:{len(self.components)}")

    def evaluate(self, orientation: Orientation) -> np.ndarray:
        """
        计算ODF在给定取向处的值

        参数:
            orientation: 待评估的取向(可以是单个或数组)

        返回:
            ODF值(标量或数组)
        """
        if not self.is_normalized:
            self.normalize_weights()

        # 初始化ODF值
        if orientation.size == 1:
            f_total = 0.0
        else:
            f_total = np.zeros(orientation.size)

        # 累加所有组分的贡献
        for center_ori, weight, sigma in self.components:
            # 计算角度距离(考虑晶体对称性)
            # angle_with返回最小对称等价角度(弧度)
            angle_rad = orientation.angle_with(center_ori)

            # 高斯函数
            gaussian = np.exp(-(angle_rad ** 2) / (2 * sigma ** 2))
            f_total += weight * gaussian

        return f_total

    def sample_rejection(self,
                         n_samples: int,
                         max_attempts: int = None) -> Orientation:
        """
        使用拒绝采样从ODF中采样取向

        参数:
            n_samples: 需要的样本数
            max_attempts: 最大尝试次数(默认为n_samples的100倍)

        返回:
            采样得到的Orientation数组
        """
        if not self.is_normalized:
            self.normalize_weights()

        if max_attempts is None:
            max_attempts = n_samples * 100

        print(f"\n使用拒绝采样从ODF中采样 {n_samples} 个取向...")

        # 估算ODF最大值(通过评估所有组分中心点)
        f_max = 0.0
        for center_ori, weight, sigma in self.components:
            # 在中心点,角度距离为0,高斯函数值为1
            f_center = weight * 1.0
            f_max = max(f_max, f_center)

        # 添加安全系数
        f_max *= 1.2

        print(f"估算的ODF最大值:{f_max:.4f}")

        # 拒绝采样
        accepted_orientations = []
        attempts = 0

        while len(accepted_orientations) < n_samples and attempts < max_attempts:
            # 从均匀分布生成候选取向
            candidate = Orientation.random(symmetry=self.symmetry)

            # 计算ODF值
            f_candidate = self.evaluate(candidate)

            # 生成随机数
            u = np.random.uniform(0, f_max)

            # 接受判断
            if u <= f_candidate:
                accepted_orientations.append(candidate)

            attempts += 1

            # 进度显示
            if attempts % 1000 == 0 and len(accepted_orientations) > 0:
                acceptance_rate = len(accepted_orientations) / attempts * 100
                print(f"  进度:{len(accepted_orientations)}/{n_samples} "
                      f"(接受率 {acceptance_rate:.1f}%)")

        if len(accepted_orientations) < n_samples:
            warnings.warn(
                f"拒绝采样未完成:只获得 {len(accepted_orientations)}/{n_samples} 个样本"
            )

        # 合并所有取向
        if accepted_orientations:
            # 提取所有四元数数据并堆叠
            all_data = np.vstack([ori.data for ori in accepted_orientations])
            result = Orientation(all_data, symmetry=self.symmetry)

            acceptance_rate = len(accepted_orientations) / attempts * 100
            print(f"拒绝采样完成:{len(accepted_orientations)} 个样本,"
                  f"接受率 {acceptance_rate:.1f}%")
            return result
        else:
            raise RuntimeError("拒绝采样失败:未获得任何样本")

    def sample_importance(self, n_samples: int) -> Orientation:
        """
        使用重要性采样从ODF中采样取向

        策略:根据权重从各组分分别采样,然后合并

        参数:
            n_samples: 需要的样本数

        返回:
            采样得到的Orientation数组
        """
        if not self.is_normalized:
            self.normalize_weights()

        print(f"\n使用重要性采样从ODF中采样 {n_samples} 个取向...")

        # 根据权重分配样本数。旧实现逐组分 round(weight * n)；
        # 在小 N_grains 下会丢样本，导致文件名 N 与实际晶粒数不一致。
        all_orientations = []
        weights = np.array([weight for _, weight, _ in self.components], dtype=float)
        weights = weights / weights.sum()
        component_counts = np.random.multinomial(n_samples, weights)

        for i, ((center_ori, weight, sigma), n_component) in enumerate(zip(self.components, component_counts)):
            if n_component == 0:
                continue

            print(f"  组分 {i + 1}: 权重={weight:.3f}, σ={np.degrees(sigma):.1f}°, "
                  f"采样数={n_component}")

            # 从该组分的高斯分布中采样
            component_samples = self._sample_from_gaussian_component(
                center_ori, sigma, n_component
            )
            all_orientations.append(component_samples)

        # 合并所有组分
        if all_orientations:
            all_data = np.vstack([ori.data for ori in all_orientations])
            result = Orientation(all_data, symmetry=self.symmetry)

            # 随机打乱
            indices = np.random.permutation(result.size)
            result = result[indices]

            print(f"重要性采样完成:{result.size} 个样本")
            return result
        else:
            raise RuntimeError("重要性采样失败:没有生成任何样本")

    def _sample_from_gaussian_component(self,
                                        center_ori: Orientation,
                                        sigma: float,
                                        n_samples: int) -> Orientation:
        """
        从单个高斯组分中采样

        参数:
            center_ori: 中心取向
            sigma: 标准差(弧度)
            n_samples: 样本数

        返回:
            采样得到的Orientation数组
        """
        # 生成随机旋转轴
        axes = Vector3d.random(n_samples)

        # 从高斯分布生成旋转角度
        # 使用截断的正态分布(3σ截断)
        angles = np.abs(np.random.normal(0, sigma, n_samples))
        angles = np.clip(angles, 0, 3 * sigma)

        # 创建旋转
        rotations = Rotation.from_axes_angles(axes, angles)

        # 应用到中心取向
        dispersed_ori = rotations * center_ori

        return dispersed_ori

    def get_texture_info(self) -> Dict:
        """获取ODF的统计信息"""
        if not self.is_normalized:
            self.normalize_weights()

        info = {
            'n_components': len(self.components),
            'components': []
        }

        for i, (center_ori, weight, sigma) in enumerate(self.components):
            euler_deg = np.degrees(center_ori.to_euler())[0]
            info['components'].append({
                'index': i + 1,
                'weight': weight,
                'sigma_deg': np.degrees(sigma),
                'euler_deg': euler_deg.tolist()
            })

        return info


class AdvancedTextureGenerator:
    """高级晶体织构生成器(多峰ODF模型)"""

    def __init__(self):
        """初始化生成器"""
        # 定义立方晶系对称性(m-3m点群,Fe-Si合金)
        self.phase = Phase(point_group="m-3m")
        self.symmetry = self.phase.point_group

        print(f"晶体对称性:{self.symmetry.name}")
        print(f"对称操作数量:{self.symmetry.size}")

    def create_standard_orientation(self,
                                    name: str,
                                    theta_0: float = 0.0) -> Orientation:
        """
        创建标准织构取向

        参数:
            name: 织构名称('Goss', 'Cube', 'Brass', 'Copper', 'S')
            theta_0: 绕样品法向(ND)的旋转角度(度)

        返回:
            Orientation对象
        """
        # 标准织构的Euler角(Bunge约定,度)
        standard_textures = {
            'Goss': (0, 45, 0),  # {110}<001>
            'Cube': (0, 0, 0),  # {100}<001>
            'Brass': (35, 45, 0),  # {110}<112>
            'Copper': (90, 35, 45),  # {112}<111>
            'S': (59, 37, 63),  # {123}<634>
        }

        if name not in standard_textures:
            raise ValueError(f"未知的织构类型:{name}。"
                             f"支持的类型:{list(standard_textures.keys())}")

        phi1, Phi, phi2 = standard_textures[name]

        # 创建基础取向
        ori = Orientation.from_euler(
            [[np.radians(phi1), np.radians(Phi), np.radians(phi2)]],
            symmetry=self.symmetry
        )

        # 应用额外的旋转
        if theta_0 != 0:
            rotation_z = Rotation.from_axes_angles(
                Vector3d.zvector(), np.radians(theta_0)
            )
            ori = rotation_z * ori
            print(f"已创建 {name} 织构,旋转角度 {theta_0:.1f}°")
        else:
            print(f"已创建 {name} 织构")

        return ori

    def create_multi_peak_odf(self,
                              components: List[TextureComponent]) -> MultiPeakODF:
        """
        创建多峰ODF模型

        参数:
            components: 织构组分列表

        返回:
            MultiPeakODF对象
        """
        odf = MultiPeakODF(self.symmetry)

        print("\n构建多峰ODF模型...")
        for comp in components:
            # 创建中心取向
            center_ori = Orientation.from_euler(
                [np.radians(comp.euler_deg)],
                symmetry=self.symmetry
            )

            # 添加到ODF
            odf.add_component(center_ori, comp.weight, comp.sigma_deg)
            print(f"  - {comp.name}: Euler=({comp.euler_deg[0]:.0f}°, "
                  f"{comp.euler_deg[1]:.0f}°, {comp.euler_deg[2]:.0f}°), "
                  f"权重={comp.weight:.3f}, σ={comp.sigma_deg:.1f}°")

        return odf

    def generate_from_odf(self,
                          odf: MultiPeakODF,
                          N_grains: int,
                          method: str = 'importance') -> Orientation:
        """
        从ODF中生成取向

        参数:
            odf: MultiPeakODF对象
            N_grains: 晶粒数量
            method: 采样方法('importance' 或 'rejection')

        返回:
            Orientation数组
        """
        print(f"\n开始生成 {N_grains} 个晶粒取向...")
        print(f"采样方法:{method}")

        if method == 'importance':
            orientations = odf.sample_importance(N_grains)
        elif method == 'rejection':
            orientations = odf.sample_rejection(N_grains)
        else:
            raise ValueError(f"未知的采样方法:{method}")

        print(f"✓ 成功生成 {orientations.size} 个取向")
        return orientations

    def generate_mixed_texture(self,
                               f_Goss: float,
                               theta_0: float,
                               N_grains: int,
                               halfwidth: float = 10.0) -> Orientation:
        """
        生成混合织构(Goss + 随机)- 使用多峰高斯型ODF模型

        符合技术路线第4.1节描述:
        - Goss织构组分:理想取向{110}<001>,权重f_Goss
        - 随机织构组分:均匀分布,权重f_random = 1 - f_Goss
        - 旋转角theta_0通过旋转矩阵Rz施加到整个ODF

        参数:
            f_Goss: Goss织构体积分数
            theta_0: Goss织构旋转角度(度)
            N_grains: 总晶粒数
            halfwidth: Goss织构半宽(度),即高斯分布的σ

        返回:
            混合Orientation对象数组
        """
        print(f"\n生成混合织构(多峰高斯型ODF模型):")
        print(f"  - Goss组分:{f_Goss * 100:.1f}% (σ={halfwidth}°)")
        print(f"  - 随机组分:{(1 - f_Goss) * 100:.1f}%")
        print(f"  - 旋转角度:{theta_0}°")

        # 定义织构组分
        components = []

        # Goss组分:{110}<001>对应Euler角(0°, 45°, 0°)
        # 应用旋转:将theta_0加到第一个Euler角(绕ND轴旋转)
        goss_euler = (theta_0, 45, 0)
        components.append(TextureComponent(
            name='Goss',
            euler_deg=goss_euler,
            weight=f_Goss,
            sigma_deg=halfwidth
        ))

        # 随机组分:用均匀分布的多个弱高斯峰模拟
        # 参考技术路线:随机织构用均匀分布表示
        f_random = 1 - f_Goss
        if f_random > 0:
            # 在取向空间中均匀分布5-10个宽峰来近似均匀分布
            n_random_peaks = max(5, int(f_random * 10))
            random_weight_each = f_random / n_random_peaks
            random_sigma = 30.0  # 较大的σ模拟随机性

            for i in range(n_random_peaks):
                # 在Euler角空间均匀采样
                random_euler = (
                    np.random.uniform(0, 360),
                    np.random.uniform(0, 180),
                    np.random.uniform(0, 360)
                )
                components.append(TextureComponent(
                    name=f'Random_{i + 1}',
                    euler_deg=random_euler,
                    weight=random_weight_each,
                    sigma_deg=random_sigma
                ))

        # 创建多峰高斯型ODF
        odf = self.create_multi_peak_odf(components)

        # 使用Monte Carlo方法(重要性采样)从ODF中采样
        orientations = self.generate_from_odf(odf, N_grains, method='importance')

        return orientations

    def orientations_to_euler(self, orientations: Orientation) -> np.ndarray:
        """
        将Orientation对象转换为Euler角(度)

        参数:
            orientations: Orientation对象

        返回:
            Euler角数组 (N, 3) 度
        """
        euler_rad = orientations.to_euler()
        euler_deg = np.degrees(euler_rad)

        # 确保在正确范围内
        euler_deg[:, 0] = euler_deg[:, 0] % 360  # φ1: [0, 360]
        euler_deg[:, 1] = np.clip(euler_deg[:, 1], 0, 180)  # Φ: [0, 180]
        euler_deg[:, 2] = euler_deg[:, 2] % 360  # φ2: [0, 360]

        return euler_deg

    def save_orientations(self,
                          euler_degrees: np.ndarray,
                          filename: str = None,
                          metadata: Dict = None) -> str:
        """
        保存Euler角数据

        参数:
            euler_degrees: Euler角数组
            filename: 输出文件名(可选)
            metadata: 元数据字典(可选)
        """
        if filename is None:
            filename = f'grain_orientations_N{len(euler_degrees)}.txt'

        with open(filename, 'w') as f:
            f.write('# Grain orientations generated by Multi-Peak ODF Model\n')
            f.write(f'# Generator: AdvancedTextureGenerator with orix {orix.__version__}\n')

            if metadata:
                f.write('# Metadata:\n')
                for key, value in metadata.items():
                    f.write(f'#   {key}: {value}\n')

            f.write('# Columns: phi1 (deg), Phi (deg), phi2 (deg) [Bunge convention]\n')
            f.write(f'# Crystal system: Cubic (m-3m)\n')
            f.write(f'# Number of grains: {len(euler_degrees)}\n')
            f.write('#\n')

            for euler in euler_degrees:
                f.write(f'{euler[0]:.6f}\t{euler[1]:.6f}\t{euler[2]:.6f}\n')

        print(f'\n✓ Euler角已保存到文件:{filename}')
        return filename

    def plot_pole_figures_orix(self,
                               orientations: Orientation,
                               title_suffix: str = "",
                               save_dir: str = None):
        """
        使用orix绘制极图(修复版:解决边界投影问题)

        参数:
            orientations: Orientation对象
            title_suffix: 标题后缀
        """
        from orix.vector import Miller

        # 设置要绘制的晶面
        hkl_list = [[1, 1, 0], [1, 0, 0], [1, 1, 1]]

        # 创建图形
        fig, axes = plt.subplots(1, 3, figsize=(15, 5))

        # 为每个晶面绘制极图
        for ax, h in zip(axes, hkl_list):
            miller = Miller(hkl=[h], phase=self.phase)
            poles = orientations * miller

            # 【修复1】严格上半球,排除赤道附近(避免数值误差)
            epsilon = 0.01  # 小阈值,排除z≈0的点
            upper = poles.z > epsilon
            x = poles.x[upper]
            y = poles.y[upper]

            # 【修复2】裁剪到单位圆内(处理残留的边界误差)
            r = np.sqrt(x ** 2 + y ** 2)
            inside_circle = r <= 1.0
            x = x[inside_circle]
            y = y[inside_circle]

            # 【修复3】对于r非常接近1的点,微调归一化(可选)
            # 这一步可以进一步确保所有点都在圆内
            r_filtered = np.sqrt(x ** 2 + y ** 2)
            scale = np.where(r_filtered > 0.995, 0.995 / r_filtered, 1.0)
            x = x * scale
            y = y * scale

            # 绘制散点图
            ax.scatter(x, y, s=1, alpha=0.3, c='blue')

            # 绘制圆形边界
            circle = plt.Circle((0, 0), 1, fill=False, color='black', linewidth=2)
            ax.add_patch(circle)

            # 设置坐标轴
            ax.set_xlim(-1.1, 1.1)
            ax.set_ylim(-1.1, 1.1)
            ax.set_aspect('equal')
            ax.set_title(f'{{{h[0]}{h[1]}{h[2]}}} Pole Figure', fontsize=12)
            ax.axis('off')

        plt.suptitle(f'Pole Figures {title_suffix}', fontsize=14, y=1.02)
        plt.tight_layout()

        # 保存图像
        filename = f'pole_figures_ODF{title_suffix.replace(" ", "_")}.png'
        if save_dir:
            import os as _os
            _os.makedirs(save_dir, exist_ok=True)
            filename = _os.path.join(save_dir, filename)
        plt.savefig(filename, dpi=150, bbox_inches='tight')
        print(f'✓ 极图已保存到文件:{filename}')
        plt.close('all')

        return filename

    def plot_odf_3d(self,
                    odf: MultiPeakODF,
                    title_suffix: str = "",
                    resolution: int = 30):
        """
        绘制ODF的3D等高线图(在Euler空间中)

        参数:
            odf: MultiPeakODF对象
            title_suffix: 标题后缀
            resolution: 每个轴的采样点数
        """
        print(f"\n正在生成ODF 3D等高线图 (分辨率: {resolution}³)...")

        # 创建Euler角网格 (Bunge约定)
        # φ1: [0, 360°], Φ: [0, 180°], φ2: [0, 360°]
        phi1_range = np.linspace(0, 360, resolution)
        Phi_range = np.linspace(0, 180, resolution)
        phi2_range = np.linspace(0, 360, resolution)

        # 选择固定的φ2切面进行3D可视化
        phi2_fixed = 0  # 可以改为45, 90等

        # 创建φ1-Φ网格
        phi1_grid, Phi_grid = np.meshgrid(phi1_range, Phi_range)

        # 计算该切面的ODF值
        odf_values = np.zeros_like(phi1_grid)

        for i in range(resolution):
            for j in range(resolution):
                euler_deg = [phi1_grid[i, j], Phi_grid[i, j], phi2_fixed]
                euler_rad = np.radians(euler_deg)
                ori = Orientation.from_euler([euler_rad], symmetry=self.symmetry)
                odf_values[i, j] = odf.evaluate(ori)

        # 创建3D图形
        fig = plt.figure(figsize=(14, 10))

        # 3D表面图
        ax1 = fig.add_subplot(221, projection='3d')
        surf = ax1.plot_surface(phi1_grid, Phi_grid, odf_values,
                                cmap='viridis', alpha=0.8,
                                linewidth=0, antialiased=True)
        ax1.set_xlabel('φ₁ (°)', fontsize=10)
        ax1.set_ylabel('Φ (°)', fontsize=10)
        ax1.set_zlabel('ODF强度', fontsize=10)
        ax1.set_title(f'ODF 3D表面图 (φ₂={phi2_fixed}°)', fontsize=11)
        fig.colorbar(surf, ax=ax1, shrink=0.5, aspect=5)

        # 3D等高线图
        ax2 = fig.add_subplot(222, projection='3d')
        levels = np.linspace(odf_values.min(), odf_values.max(), 15)
        ax2.contour3D(phi1_grid, Phi_grid, odf_values,
                      levels=levels, cmap='viridis', linewidths=1.5)
        ax2.set_xlabel('φ₁ (°)', fontsize=10)
        ax2.set_ylabel('Φ (°)', fontsize=10)
        ax2.set_zlabel('ODF强度', fontsize=10)
        ax2.set_title(f'ODF 3D等高线 (φ₂={phi2_fixed}°)', fontsize=11)

        # 2D投影等高线
        ax3 = fig.add_subplot(223)
        contour = ax3.contourf(phi1_grid, Phi_grid, odf_values,
                               levels=20, cmap='viridis')
        ax3.contour(phi1_grid, Phi_grid, odf_values,
                    levels=10, colors='black', linewidths=0.5, alpha=0.3)
        ax3.set_xlabel('φ₁ (°)', fontsize=10)
        ax3.set_ylabel('Φ (°)', fontsize=10)
        ax3.set_title(f'ODF 2D等高线图 (φ₂={phi2_fixed}°)', fontsize=11)
        fig.colorbar(contour, ax=ax3)

        # 2D热图
        ax4 = fig.add_subplot(224)
        im = ax4.imshow(odf_values, extent=[0, 360, 0, 180],
                        origin='lower', aspect='auto', cmap='viridis')
        ax4.set_xlabel('φ₁ (°)', fontsize=10)
        ax4.set_ylabel('Φ (°)', fontsize=10)
        ax4.set_title(f'ODF热图 (φ₂={phi2_fixed}°)', fontsize=11)
        fig.colorbar(im, ax=ax4)

        plt.suptitle(f'ODF 3D等高线可视化 {title_suffix}',
                     fontsize=14, y=0.98)
        plt.tight_layout()

        # 保存
        filename = f'odf_3d{title_suffix.replace(" ", "_")}.png'
        plt.savefig(filename, dpi=150, bbox_inches='tight')
        print(f'✓ ODF 3D图已保存到文件:{filename}')
        plt.close('all')

        return filename

    def plot_phi2_sections(self,
                           odf: MultiPeakODF,
                           phi2_sections: List[float] = None,
                           title_suffix: str = "",
                           resolution: int = 50):
        """
        绘制ODF的φ2截面图(φ sections)

        这是织构分析中常用的可视化方法,在固定φ2角度下,
        在φ1-Φ平面上显示ODF强度分布

        参数:
            odf: MultiPeakODF对象
            phi2_sections: φ2角度列表(度),默认[0, 45, 65, 90]
            title_suffix: 标题后缀
            resolution: 每个轴的采样点数
        """
        if phi2_sections is None:
            phi2_sections = [0, 45, 65, 90]

        print(f"\n正在生成φ2截面图 (sections: {phi2_sections}°)...")

        n_sections = len(phi2_sections)
        fig, axes = plt.subplots(2, (n_sections + 1) // 2,
                                 figsize=(5 * ((n_sections + 1) // 2), 10))
        axes = axes.flatten() if n_sections > 1 else [axes]

        # 创建φ1-Φ网格
        phi1_range = np.linspace(0, 360, resolution)
        Phi_range = np.linspace(0, 180, resolution)
        phi1_grid, Phi_grid = np.meshgrid(phi1_range, Phi_range)

        # 为每个φ2截面绘图
        for idx, phi2 in enumerate(phi2_sections):
            if idx >= len(axes):
                break

            ax = axes[idx]

            # 计算该截面的ODF值
            odf_values = np.zeros_like(phi1_grid)

            for i in range(resolution):
                for j in range(resolution):
                    euler_deg = [phi1_grid[i, j], Phi_grid[i, j], phi2]
                    euler_rad = np.radians(euler_deg)
                    ori = Orientation.from_euler([euler_rad], symmetry=self.symmetry)
                    odf_values[i, j] = odf.evaluate(ori)

            # 绘制等高线填充图
            levels = np.linspace(odf_values.min(), odf_values.max(), 20)
            contourf = ax.contourf(phi1_grid, Phi_grid, odf_values,
                                   levels=levels, cmap='jet')

            # 添加等高线
            contour_lines = ax.contour(phi1_grid, Phi_grid, odf_values,
                                       levels=10, colors='black',
                                       linewidths=0.5, alpha=0.4)
            ax.clabel(contour_lines, inline=True, fontsize=8, fmt='%.2f')

            # 设置坐标轴
            ax.set_xlabel('φ₁ (°)', fontsize=10)
            ax.set_ylabel('Φ (°)', fontsize=10)
            ax.set_title(f'φ₂ = {phi2}°', fontsize=12, fontweight='bold')
            ax.set_xlim(0, 360)
            ax.set_ylim(0, 180)
            ax.grid(True, alpha=0.3)

            # 添加 colorbar（必须用 fig.colorbar 而非 plt.colorbar，避免跨图错误）
            cbar = fig.colorbar(contourf, ax=ax)
            cbar.set_label('ODF强度', fontsize=9)

        # 隐藏多余的子图
        for idx in range(len(phi2_sections), len(axes)):
            axes[idx].axis('off')

        plt.suptitle(f'ODF φ₂截面图 (φ sections) {title_suffix}',
                     fontsize=14, y=0.995)
        plt.tight_layout()

        # 保存
        filename = f'odf_phi2_sections{title_suffix.replace(" ", "_")}.png'
        plt.savefig(filename, dpi=150, bbox_inches='tight')
        print(f'✓ φ2截面图已保存到文件:{filename}')
        plt.close('all')

        return filename

    def calculate_texture_index(self, orientations: Orientation) -> float:
        """
        计算织构指数 J = ∫(f(g))²dg

        简化方法:基于取向的分散程度
        """
        if orientations.size > 1000:
            sample_idx = np.random.choice(orientations.size, 1000, replace=False)
            ori_sample = orientations[sample_idx]
        else:
            ori_sample = orientations

        mean_ori = ori_sample.mean()
        angles = ori_sample.angle_with(mean_ori, degrees=True)
        mean_angle = np.mean(angles)

        if mean_angle < 30:
            texture_index = 3.0 - (mean_angle / 15)
        elif mean_angle > 60:
            texture_index = 1.0 + (90 - mean_angle) / 30
        else:
            texture_index = 1.5 + (60 - mean_angle) / 30

        return max(1.0, texture_index)

    def print_statistics(self,
                         orientations: Orientation,
                         euler_degrees: np.ndarray):
        """打印详细统计信息"""
        print('\n' + '=' * 60)
        print('织构统计信息')
        print('=' * 60)

        print('\nEuler角范围(Bunge约定):')
        print(f'  φ1: [{np.min(euler_degrees[:, 0]):.1f}, '
              f'{np.max(euler_degrees[:, 0]):.1f}]°')
        print(f'  Φ:  [{np.min(euler_degrees[:, 1]):.1f}, '
              f'{np.max(euler_degrees[:, 1]):.1f}]°')
        print(f'  φ2: [{np.min(euler_degrees[:, 2]):.1f}, '
              f'{np.max(euler_degrees[:, 2]):.1f}]°')

        texture_index = self.calculate_texture_index(orientations)
        print(f'\n织构指数: {texture_index:.3f}')
        print('  (1.0 = 完全随机, >1.0 = 择优取向, >2.0 = 强织构)')


class BatchTextureGenerator:
    """批量织构生成器 - 使用科学采样方法"""

    def __init__(self, param_range: ParameterRange):
        """
        初始化批量生成器

        参数:
            param_range: 参数范围定义
        """
        self.param_range = param_range
        self.generator = AdvancedTextureGenerator()
        self.results = []

    def latin_hypercube_sampling(self, n_samples: int, n_dims: int) -> np.ndarray:
        """
        拉丁超立方采样 (Latin Hypercube Sampling)

        优点:
        - 保证每个维度的均匀覆盖
        - 避免参数聚集
        - 样本点在参数空间中分布更均匀

        参数:
            n_samples: 样本数量
            n_dims: 维度数量

        返回:
            归一化的样本点 (n_samples, n_dims) ∈ [0, 1]
        """
        # 为每个维度创建均匀分层
        samples = np.zeros((n_samples, n_dims))

        for dim in range(n_dims):
            # 将[0,1]区间分成n_samples个等间隔的层
            intervals = np.arange(n_samples) / n_samples
            # 在每层内随机采样
            samples[:, dim] = intervals + np.random.uniform(0, 1 / n_samples, n_samples)
            # 随机打乱顺序
            np.random.shuffle(samples[:, dim])

        return samples

    def sobol_sampling(self, n_samples: int, n_dims: int) -> np.ndarray:
        """
        Sobol序列采样(需要scipy)

        参数:
            n_samples: 样本数量
            n_dims: 维度数量

        返回:
            归一化的样本点 (n_samples, n_dims) ∈ [0, 1]
        """
        try:
            from scipy.stats import qmc
            sampler = qmc.Sobol(d=n_dims, scramble=True)
            samples = sampler.random(n_samples)
            return samples
        except ImportError:
            print("警告: scipy未安装,使用拉丁超立方采样替代")
            return self.latin_hypercube_sampling(n_samples, n_dims)

    def uniform_grid_sampling(self, n_samples: int, n_dims: int) -> np.ndarray:
        """
        均匀网格采样

        参数:
            n_samples: 总样本数量(会调整为最接近的完全平方数)
            n_dims: 维度数量

        返回:
            归一化的样本点 ∈ [0, 1]
        """
        # 计算每个维度的采样点数
        n_per_dim = int(np.ceil(n_samples ** (1 / n_dims)))

        # 生成网格
        axes = [np.linspace(0, 1, n_per_dim) for _ in range(n_dims)]
        grids = np.meshgrid(*axes, indexing='ij')
        samples = np.column_stack([grid.ravel() for grid in grids])

        # 如果样本数量超过需求,随机选择
        if len(samples) > n_samples:
            indices = np.random.choice(len(samples), n_samples, replace=False)
            samples = samples[indices]

        return samples

    def generate_parameter_combinations(self,
                                        n_samples: int,
                                        method: str = 'lhs') -> List[Dict]:
        """
        生成参数组合

        参数:
            n_samples: 需要生成的样本数量
            method: 采样方法
                - 'lhs': 拉丁超立方采样(推荐)
                - 'sobol': Sobol序列
                - 'grid': 均匀网格
                - 'random': 随机采样

        返回:
            参数组合列表
        """
        print(f"\n{'=' * 60}")
        print(f"生成参数组合: {n_samples}个样本")
        print(f"采样方法: {method.upper()}")
        print(f"{'=' * 60}")

        # 参数维度
        n_dims = 3  # f_Goss, theta_0, halfwidth

        # 根据方法生成归一化样本
        if method == 'lhs':
            normalized_samples = self.latin_hypercube_sampling(n_samples, n_dims)
        elif method == 'sobol':
            normalized_samples = self.sobol_sampling(n_samples, n_dims)
        elif method == 'grid':
            normalized_samples = self.uniform_grid_sampling(n_samples, n_dims)
        elif method == 'random':
            normalized_samples = np.random.uniform(0, 1, (n_samples, n_dims))
        else:
            raise ValueError(f"未知的采样方法: {method}")

        # 将归一化样本映射到实际参数范围
        param_combinations = []

        for i, sample in enumerate(normalized_samples):
            f_Goss = self.param_range.f_Goss_min + \
                     sample[0] * (self.param_range.f_Goss_max - self.param_range.f_Goss_min)
            theta_0 = self.param_range.theta_0_min + \
                      sample[1] * (self.param_range.theta_0_max - self.param_range.theta_0_min)
            halfwidth = self.param_range.halfwidth_min + \
                        sample[2] * (self.param_range.halfwidth_max - self.param_range.halfwidth_min)

            param_combinations.append({
                'index': i + 1,
                'f_Goss': f_Goss,
                'theta_0': theta_0,
                'halfwidth': halfwidth,
                'N_grains': self.param_range.N_grains
            })

        # 打印参数范围摘要
        print(f"\n参数范围:")
        print(f"  f_Goss:    [{self.param_range.f_Goss_min:.2f}, {self.param_range.f_Goss_max:.2f}]")
        print(f"  theta_0:   [{self.param_range.theta_0_min:.1f}°, {self.param_range.theta_0_max:.1f}°]")
        print(f"  halfwidth: [{self.param_range.halfwidth_min:.1f}°, {self.param_range.halfwidth_max:.1f}°]")
        print(f"  N_grains:  {self.param_range.N_grains}")

        # 打印前几个样本
        print(f"\n前5个参数组合:")
        for params in param_combinations[:5]:
            print(f"  #{params['index']}: f_Goss={params['f_Goss']:.3f}, "
                  f"θ₀={params['theta_0']:.1f}°, σ={params['halfwidth']:.1f}°")

        if n_samples > 5:
            print(f"  ... (共{n_samples}组)")

        return param_combinations

    def batch_generate(self,
                       n_samples: int,
                       sampling_method: str = 'lhs',
                       output_dir: str = 'preinput',
                       save_plots: bool = True,
                       save_orientations: bool = True) -> List[Dict]:
        """
        批量生成织构

        参数:
            n_samples: 生成的样本数量
            sampling_method: 参数采样方法 ('lhs', 'sobol', 'grid', 'random')
            output_dir: 输出目录
            save_plots: 是否保存极图
            save_orientations: 是否保存取向数据

        返回:
            结果列表,每个元素包含参数和生成的取向
        """
        import os
        from datetime import datetime

        # 创建输出目录
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        os.makedirs(output_dir, exist_ok=True)
        batch_folder = f"batch_{sampling_method}_{timestamp}"
        output_path = os.path.join(output_dir, batch_folder)
        os.makedirs(output_path, exist_ok=True)

        print(f"\n{'=' * 60}")
        print(f"批量织构生成")
        print(f"输出目录: {output_path}")
        print(f"{'=' * 60}")

        # 生成参数组合
        param_combinations = self.generate_parameter_combinations(
            n_samples, sampling_method
        )

        # 批量生成
        results = []
        total = len(param_combinations)

        print(f"\n开始批量生成 {total} 个织构样本...")
        print(f"{'=' * 60}\n")

        for params in param_combinations:
            idx = params['index']
            print(f"[{idx}/{total}] 生成织构样本...")
            print(f"  参数: f_Goss={params['f_Goss']:.3f}, "
                  f"θ₀={params['theta_0']:.1f}°, σ={params['halfwidth']:.1f}°")

            try:
                # 生成织构
                orientations = self.generator.generate_mixed_texture(
                    f_Goss=params['f_Goss'],
                    theta_0=params['theta_0'],
                    N_grains=params['N_grains'],
                    halfwidth=params['halfwidth']
                )

                # 转换为Euler角
                euler_degrees = self.generator.orientations_to_euler(orientations)

                # 保存取向数据
                if save_orientations:
                    filename = (f"grain_orientations_ODF_{idx:03d}_fGoss{params['f_Goss']:.2f}_"
                               f"theta{params['theta_0']:.0f}_hw{params['halfwidth']:.0f}_N{params['N_grains']}.txt")
                    filepath = os.path.join(output_path, filename)

                    metadata = {
                        'sample_index': idx,
                        'f_Goss': params['f_Goss'],
                        'theta_0_deg': params['theta_0'],
                        'halfwidth_deg': params['halfwidth'],
                        'N_grains': params['N_grains'],
                        'sampling_method': sampling_method
                    }

                    self.generator.save_orientations(
                        euler_degrees, filepath, metadata
                    )

                # 保存极图(可选)
                if save_plots:
                    import matplotlib.pyplot as plt
                    plt.ioff()  # 关闭交互模式

                    # 使用完整参数信息作为后缀
                    title_suffix = (f"_{idx:03d}_fGoss{params['f_Goss']:.2f}_"
                                  f"theta{params['theta_0']:.0f}_hw{params['halfwidth']:.0f}_N{params['N_grains']}")

                    fig_dir = os.path.join(output_path, 'odf_figures')
                    os.makedirs(fig_dir, exist_ok=True)
                    self.generator.plot_pole_figures_orix(orientations, title_suffix,
                                                          save_dir=fig_dir)
                    plt.close('all')

                # 计算织构指数
                texture_index = self.generator.calculate_texture_index(orientations)

                # 保存结果
                result = {
                    'index': idx,
                    'parameters': params,
                    'euler_degrees': euler_degrees,
                    'orientations': orientations,
                    'texture_index': texture_index,
                    'status': 'success'
                }
                results.append(result)

                print(f"  ✓ 完成 (织构指数: {texture_index:.3f})\n")

            except Exception as e:
                print(f"  ✗ 失败: {str(e)}\n")
                results.append({
                    'index': idx,
                    'parameters': params,
                    'status': 'failed',
                    'error': str(e)
                })

        # 保存批量结果摘要
        self._save_batch_summary(results, output_path, sampling_method)

        # 生成参数分布可视化
        self._plot_parameter_distribution(param_combinations, output_path)

        print(f"\n{'=' * 60}")
        print(f"批量生成完成!")
        print(f"成功: {sum(1 for r in results if r['status'] == 'success')}/{total}")
        print(f"输出目录: {output_path}")
        print(f"{'=' * 60}\n")

        self.results = results
        return results

    def _save_batch_summary(self, results: List[Dict],
                            output_path: str,
                            sampling_method: str):
        """保存批量结果摘要"""
        import os
        from datetime import datetime

        summary_file = os.path.join(output_path, 'batch_summary.txt')

        with open(summary_file, 'w', encoding='utf-8') as f:
            f.write('=' * 70 + '\n')
            f.write('批量织构生成摘要\n')
            f.write('=' * 70 + '\n')
            f.write(f'生成时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}\n')
            f.write(f'采样方法: {sampling_method.upper()}\n')
            f.write(f'样本总数: {len(results)}\n')
            f.write(f'成功样本: {sum(1 for r in results if r["status"] == "success")}\n')
            f.write(f'失败样本: {sum(1 for r in results if r["status"] == "failed")}\n')
            f.write('\n参数范围:\n')
            f.write(f'  f_Goss:    [{self.param_range.f_Goss_min:.2f}, {self.param_range.f_Goss_max:.2f}]\n')
            f.write(f'  theta_0:   [{self.param_range.theta_0_min:.1f}°, {self.param_range.theta_0_max:.1f}°]\n')
            f.write(f'  halfwidth: [{self.param_range.halfwidth_min:.1f}°, {self.param_range.halfwidth_max:.1f}°]\n')
            f.write(f'  N_grains:  {self.param_range.N_grains}\n')
            f.write('\n' + '=' * 70 + '\n')
            f.write('详细结果:\n')
            f.write('=' * 70 + '\n')
            f.write(f'{"Index":<6} {"f_Goss":<8} {"theta_0":<8} {"halfwidth":<10} {"纹理指数":<10} {"状态":<8}\n')
            f.write('-' * 70 + '\n')

            for result in results:
                idx = result['index']
                params = result['parameters']
                status = result['status']

                if status == 'success':
                    texture_idx = result.get('texture_index', 0)
                    f.write(f'{idx:<6} {params["f_Goss"]:<8.3f} {params["theta_0"]:<8.1f} '
                            f'{params["halfwidth"]:<10.1f} {texture_idx:<10.3f} {status:<8}\n')
                else:
                    f.write(f'{idx:<6} {params["f_Goss"]:<8.3f} {params["theta_0"]:<8.1f} '
                            f'{params["halfwidth"]:<10.1f} {"N/A":<10} {status:<8}\n')

        print(f"✓ 批量摘要已保存: {summary_file}")

    def _plot_parameter_distribution(self, param_combinations: List[Dict],
                                     output_path: str):
        """绘制参数分布可视化"""
        import os

        fig = plt.figure(figsize=(15, 5))

        # 提取参数
        f_Goss = [p['f_Goss'] for p in param_combinations]
        theta_0 = [p['theta_0'] for p in param_combinations]
        halfwidth = [p['halfwidth'] for p in param_combinations]

        # 1. f_Goss vs theta_0
        ax1 = fig.add_subplot(131)
        scatter1 = ax1.scatter(f_Goss, theta_0, c=halfwidth,
                               cmap='viridis', s=50, alpha=0.6, edgecolors='black')
        ax1.set_xlabel('f_Goss', fontsize=11)
        ax1.set_ylabel('theta_0 (°)', fontsize=11)
        ax1.set_title('参数空间分布: f_Goss vs theta_0', fontsize=12)
        ax1.grid(True, alpha=0.3)
        plt.colorbar(scatter1, ax=ax1, label='halfwidth (°)')

        # 2. f_Goss vs halfwidth
        ax2 = fig.add_subplot(132)
        scatter2 = ax2.scatter(f_Goss, halfwidth, c=theta_0,
                               cmap='plasma', s=50, alpha=0.6, edgecolors='black')
        ax2.set_xlabel('f_Goss', fontsize=11)
        ax2.set_ylabel('halfwidth (°)', fontsize=11)
        ax2.set_title('参数空间分布: f_Goss vs halfwidth', fontsize=12)
        ax2.grid(True, alpha=0.3)
        plt.colorbar(scatter2, ax=ax2, label='theta_0 (°)')

        # 3. theta_0 vs halfwidth
        ax3 = fig.add_subplot(133)
        scatter3 = ax3.scatter(theta_0, halfwidth, c=f_Goss,
                               cmap='coolwarm', s=50, alpha=0.6, edgecolors='black')
        ax3.set_xlabel('theta_0 (°)', fontsize=11)
        ax3.set_ylabel('halfwidth (°)', fontsize=11)
        ax3.set_title('参数空间分布: theta_0 vs halfwidth', fontsize=12)
        ax3.grid(True, alpha=0.3)
        plt.colorbar(scatter3, ax=ax3, label='f_Goss')

        plt.tight_layout()

        # 保存
        filename = os.path.join(output_path, 'parameter_distribution.png')
        plt.savefig(filename, dpi=150, bbox_inches='tight')
        plt.close()

        print(f"✓ 参数分布图已保存: {filename}")


def generate_texture_with_odf(f_Goss: float = 0.7,
                              theta_0: float = 15.0,
                              N_grains: int = 1000,
                              halfwidth: float = 10.0,
                              sampling_method: str = 'importance',
                              plot_odf: bool = True,
                              output_dir: str = 'preinput'):
    """
    使用多峰高斯型ODF模型生成织构 - 完全符合技术路线第4.1节

    数学模型:
        f(g) = Σ w_i·exp(-ω²(g, g_i) / (2σ_i²))

    其中:
        - Goss组分:{110}<001>,权重f_Goss,σ=halfwidth
        - 随机组分:均匀分布近似,权重(1-f_Goss)
        - 旋转角θ_0:通过旋转矩阵Rz施加到整个ODF

    采样方法:
        - Monte Carlo采样从ODF中抽取N_grains个离散取向
        - 输出Euler角(Bunge约定)文本文件

    参数:
        f_Goss: Goss织构体积分数 (0.0-1.0,推荐0.4-0.8)
        theta_0: 主织构旋转角度(度,0-45)
        N_grains: 晶粒数量(推荐>=500)
        halfwidth: Goss织构分散半宽,即高斯分布σ(度,典型值10)
        sampling_method: 采样方法('importance'快速 或 'rejection'严格)
        plot_odf: 是否绘制ODF可视化图

    返回:
        euler_degrees: Euler角数组(度,Bunge约定)

    示例:
        # 标准Goss织构
        euler = generate_texture_with_odf(f_Goss=0.7, theta_0=0, N_grains=1000)

        # 旋转Goss织构
        euler = generate_texture_with_odf(f_Goss=0.7, theta_0=15, N_grains=1000)

        # 强Goss织构(窄分布)
        euler = generate_texture_with_odf(f_Goss=0.8, theta_0=0,
                                          N_grains=1000, halfwidth=8)
    """
    print(f'\n{"=" * 60}')
    print(f'多峰高斯型ODF织构生成 (技术路线第4.1节)')
    print(f'基于orix {orix.__version__}')
    print(f'{"=" * 60}')
    print(f'输入参数:')
    print(f'  - Goss织构占比 f_Goss: {f_Goss:.2f}')
    print(f'  - 主织构旋转角 θ₀: {theta_0:.1f}°')
    print(f'  - 晶粒数量 N_grains: {N_grains}')
    print(f'  - 织构半宽 σ: {halfwidth:.1f}°')
    print(f'  - 采样方法: {sampling_method}')
    print(f'数学模型:f(g) = Σ w_i·exp(-ω²/(2σ²))')

    # 参数验证
    if not ORIX_AVAILABLE:
        raise ImportError("需要安装orix库")

    if f_Goss < 0 or f_Goss > 1:
        raise ValueError(f"f_Goss必须在[0, 1]范围内,当前值:{f_Goss}")

    if N_grains < 100:
        warnings.warn(f"晶粒数量{N_grains}可能过小,建议至少500个")

    # 创建生成器
    generator = AdvancedTextureGenerator()

    # 生成混合织构(使用多峰ODF)
    orientations = generator.generate_mixed_texture(
        f_Goss, theta_0, N_grains, halfwidth
    )

    # 转换为Euler角
    euler_degrees = generator.orientations_to_euler(orientations)

    # 准备元数据
    metadata = {
        'f_Goss': f_Goss,
        'theta_0_deg': theta_0,
        'N_grains': N_grains,
        'halfwidth_deg': halfwidth,
        'sampling_method': sampling_method,
        'model': 'Multi-Peak Gaussian ODF'
    }

    # 保存数据
    import os as _os
    _os.makedirs(output_dir, exist_ok=True)
    base_fname = f'grain_orientations_ODF_fGoss{f_Goss:.2f}_theta{theta_0:.0f}_hw{halfwidth:.0f}_N{N_grains}.txt'
    filename = _os.path.join(output_dir, base_fname)
    generator.save_orientations(euler_degrees, filename, metadata)

    # 打印统计信息
    generator.print_statistics(orientations, euler_degrees)

    # 绘制极图和ODF可视化(仅当请求时)
    if plot_odf:
        print('\n正在绘制极图...')
        title = f"(f_Goss={f_Goss:.2f}, θ₀={theta_0:.0f}°)"
        generator.plot_pole_figures_orix(orientations, title)

    # 绘制ODF可视化(如果请求)
    if plot_odf:
        # 重新创建ODF用于可视化
        components = []
        goss_euler = (theta_0, 45, 0)
        components.append(TextureComponent(
            name='Goss',
            euler_deg=goss_euler,
            weight=f_Goss,
            sigma_deg=halfwidth
        ))

        # 添加随机组分
        f_random = 1 - f_Goss
        if f_random > 0:
            n_random_peaks = max(5, int(f_random * 10))
            random_weight_each = f_random / n_random_peaks
            random_sigma = 30.0

            for i in range(n_random_peaks):
                random_euler = (
                    np.random.uniform(0, 360),
                    np.random.uniform(0, 180),
                    np.random.uniform(0, 360)
                )
                components.append(TextureComponent(
                    name=f'Random_{i + 1}',
                    euler_deg=random_euler,
                    weight=random_weight_each,
                    sigma_deg=random_sigma
                ))

        odf = generator.create_multi_peak_odf(components)

        # 绘制ODF 3D等高线图
        generator.plot_odf_3d(odf, title, resolution=30)

        # 绘制φ2截面图
        generator.plot_phi2_sections(odf, phi2_sections=[0, 45, 65, 90],
                                     title_suffix=title, resolution=50)

    print(f'\n{"=" * 60}')
    print('✓ 织构生成完成!')
    print(f'{"=" * 60}\n')

    return euler_degrees


def batch_generate_textures(n_samples: int = 10,
                            f_Goss_range: Tuple[float, float] = (0.4, 0.9),
                            theta_0_range: Tuple[float, float] = (0, 45),
                            halfwidth_range: Tuple[float, float] = (8, 15),
                            N_grains: int = 1000,
                            sampling_method: str = 'lhs',
                            output_dir: str = 'batch_results',
                            save_plots: bool = True):
    """
    批量生成织构 - 便捷接口

    参数:
        n_samples: 生成的样本数量
        f_Goss_range: Goss织构体积分数范围 (min, max)
        theta_0_range: 旋转角度范围 (min, max) 单位:度
        halfwidth_range: 织构半宽范围 (min, max) 单位:度
        N_grains: 每个样本的晶粒数量
        sampling_method: 采样方法
            - 'lhs': 拉丁超立方采样(推荐,均匀覆盖)
            - 'sobol': Sobol序列(低差异序列)
            - 'grid': 均匀网格(规则排列)
            - 'random': 随机采样(对照组)
        output_dir: 输出目录前缀
        save_plots: 是否保存极图

    返回:
        结果列表

    示例:
        # 生成20个样本,使用拉丁超立方采样
        results = batch_generate_textures(
            n_samples=20,
            f_Goss_range=(0.5, 0.8),
            theta_0_range=(0, 30),
            halfwidth_range=(8, 12),
            sampling_method='lhs'
        )
    """
    # 创建参数范围
    param_range = ParameterRange(
        f_Goss_min=f_Goss_range[0],
        f_Goss_max=f_Goss_range[1],
        theta_0_min=theta_0_range[0],
        theta_0_max=theta_0_range[1],
        halfwidth_min=halfwidth_range[0],
        halfwidth_max=halfwidth_range[1],
        N_grains=N_grains
    )

    # 创建批量生成器
    batch_gen = BatchTextureGenerator(param_range)

    # 批量生成
    results = batch_gen.batch_generate(
        n_samples=n_samples,
        sampling_method=sampling_method,
        output_dir=output_dir,
        save_plots=save_plots,
        save_orientations=True
    )

    return results


def generate_batch_lhs(n_samples: int = 10,
                       f_Goss_range: Tuple[float, float] = (0.4, 0.9),
                       theta_0_range: Tuple[float, float] = (0, 45),
                       halfwidth_range: Tuple[float, float] = (8, 15),
                       N_grains_range: Tuple[int, int] = (50, 150),
                       Si_content: float = 3.0,
                       output_dir: str = 'preinput/pipeline',
                       save_plots: bool = False) -> str:
    """
    Compatibility wrapper used by the Flask pipeline.

    The current texture generator samples f_Goss/theta_0/halfwidth and uses a
    fixed N_grains value per batch. The pipeline passes an N_grains range, so
    this wrapper records that range and uses its midpoint.
    """
    n_min, n_max = N_grains_range
    n_grains = int(round((float(n_min) + float(n_max)) / 2.0))
    fixed_halfwidth = abs(float(halfwidth_range[0]) - float(halfwidth_range[1])) < 1e-12

    import contextlib as _contextlib
    import io as _io

    from pathlib import Path as _Path
    out = _Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    before_batches = {p.resolve() for p in out.glob('batch_lhs_*') if p.is_dir()}

    with _contextlib.redirect_stdout(_io.StringIO()):
        batch_generate_textures(
            n_samples=n_samples,
            f_Goss_range=tuple(f_Goss_range),
            theta_0_range=tuple(theta_0_range),
            halfwidth_range=tuple(halfwidth_range),
            N_grains=n_grains,
            sampling_method='lhs',
            output_dir=output_dir,
            save_plots=save_plots,
        )

    import json as _json
    after_batches = [p for p in out.glob('batch_lhs_*') if p.is_dir()]
    new_batches = [p for p in after_batches if p.resolve() not in before_batches]
    batch_path = max(new_batches or after_batches, key=lambda p: p.stat().st_mtime)
    metadata = {
        'n_samples': n_samples,
        'f_Goss_range': list(f_Goss_range),
        'theta_0_range': list(theta_0_range),
        'N_grains_range_requested': list(N_grains_range),
        'N_grains': n_grains,
        'Si_content': Si_content,
        'sampling_method': 'lhs',
        'halfwidth_deg': float(halfwidth_range[0]) if fixed_halfwidth else None,
        'halfwidth_policy': 'fixed_for_pipeline' if fixed_halfwidth else 'sampled',
    }
    metadata_text = _json.dumps(metadata, ensure_ascii=False, indent=2)
    (out / 'batch_metadata.json').write_text(metadata_text, encoding='utf-8')
    (batch_path / 'batch_metadata.json').write_text(metadata_text, encoding='utf-8')

    if fixed_halfwidth:
        for txt in batch_path.glob('grain_orientations_ODF_*.txt'):
            with open(txt, 'a', encoding='utf-8') as f:
                f.write('#   halfwidth_policy: fixed_for_pipeline\n')
                f.write(f'#   halfwidth_deg: {float(halfwidth_range[0])}\n')
                f.write(f'#   Si_content: {Si_content}\n')
    return str(batch_path)


def run_single():
    print("\n" + "=" * 70)
    print("单个织构生成")
    print("=" * 70)
    euler1 = generate_texture_with_odf(
        f_Goss=0.7,
        theta_0=15,
        N_grains=10,
        halfwidth=10,
        sampling_method='importance',
        plot_odf=False  # 启用ODF可视化
    )


def run_batch():
    print("\n" + "=" * 70)
    print("批量织构生成")
    print("=" * 70)
    results_lhs = batch_generate_textures(
        n_samples=500,  # 生成200个样本
        f_Goss_range=(0.4, 0.9),  # Goss含量范围
        theta_0_range=(0, 30),  # 旋转角度范围
        halfwidth_range=(5, 15),  # 半宽范围
        N_grains=100,  # 每个样本晶粒数量
        sampling_method='lhs',  # 采样方法
        save_plots=False  # 不保存极图以加快速度
    )
    '''
    # 示例: 比较不同采样方法,小批量测试不同方法
    for method in ['lhs', 'sobol', 'grid', 'random']:
        print(f"\n测试采样方法: {method.upper()}")
        results = batch_generate_textures(
            n_samples=8,
            f_Goss_range=(0.5, 0.8),
            theta_0_range=(0, 30),
            halfwidth_range=(8, 12),
            N_grains=300,
            sampling_method=method,
            output_dir=f'batch_comparison',
            save_plots=False
        )
    '''


if __name__ == "__main__":
    configure_fonts()
    try:
        run_single()
        #run_batch()
        print("\n" + "=" * 70)
        print("所有示例完成!")
        print("=" * 70)

    except ImportError as e:
        print(f"\n错误:{e}")
        print("请先安装orix库:pip install orix")
    except Exception as e:
        print(f"\n发生错误:{e}")
        import traceback

        traceback.print_exc()
