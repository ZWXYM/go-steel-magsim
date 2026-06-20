# 材料基准映射策略

## 背景与核心问题

当前预测管道存在**绝对误差大**的问题：仿真使用 Stoner-Wohlfarth 单磁畴模型，
缺乏畴壁运动物理机制，导致预测 B-H 曲线在 H≈800 A/m 处急剧饱和，
与实测 GO 钢材料相比膝部过于尖锐，B800 偏低约 0.05-0.15T。

然而，**仿真对于不同 ODF 参数之间的相对差异是可靠的**——
改变 f_Goss、θ₀、σ、Si% 时，模拟曲线的变化趋势与物理预期一致。

## 核心思路：差分修正 + 锚点标定

不要求仿真给出绝对准确的 B-H 曲线，
而是用仿真描述**材料之间的差异**，再用一个已知参考材料标定绝对尺度。

```
B_predicted(params) = B_sim(params) + Δ(H, params)
```

其中 Δ(H, params) 是基于参考锚点的修正函数。

---

## 实施步骤

### Step 1：建立 ODF 参数 → 材料牌号等价表

根据各 GO 钢牌号的典型织构强度，建立参数映射估计：

| 材料牌号 | f_Goss 估计 | θ₀ (°) | σ_hw (°) | B800 RD (T) | 数据来源 |
|---------|------------|--------|----------|------------|--------|
| B23R075 (宝钢) | 0.88-0.95 | 2-4    | 5-7      | ≥ 1.88     | 宝钢产品手册 |
| B27R090 (宝钢) | 0.78-0.85 | 5-8    | 7-9      | ≥ 1.85     | 宝钢产品手册 |
| B30P105        | 0.65-0.75 | 8-12   | 9-11     | ≥ 1.82     | IEC 60404-8-7 |
| IEC M089-27P   | 0.70-0.78 | 6-10   | 7-10     | ≥ 1.83     | IEC 60404-8-7 |
| IEC M111-30P   | 0.55-0.65 | 10-15  | 10-13    | ≥ 1.79     | IEC 60404-8-7 |
| IEC M120-35S   | 0.45-0.60 | 12-18  | 12-15    | ≥ 1.77     | IEC 60404-8-7 |

> 注：以上 ODF 参数为基于文献的估计，未经直接 EBSD 测量验证。
> 建议后续用项目采集的仿真数据反拟合确定。

### Step 2：选择锚点材料并运行校准仿真

**推荐锚点：B27R090**（原因：居中偏高，RD 磁性能数据完整，
项目已有数字化曲线数据，且与仿真最佳配置较接近）

运行仿真参数：
- f_Goss = 0.82, θ₀ = 6°, σ = 8°
- N_grains = 32（标准精度），Si_content = 3.0%
- 使用修正后的仿真参数（H_max=20000, Msat=1.56e6 等）

得到仿真曲线 B_sim(H; f=0.82, θ=6°, σ=8°)

### Step 3：计算修正函数 δ(H)

```python
# 已有实测数据：go_steel_data/ 目录下的 B27R090 数字化曲线
# 已有仿真结果：锚点参数的聚合 B-H 曲线

import numpy as np
H_common = np.array([10, 20, 50, 100, 200, 300, 500, 800, 1000, 2000, 5000, 10000, 20000])

B_real  = np.interp(H_common, H_ref, B_ref)   # 插值实测曲线到公共 H 网格
B_sim   = np.interp(H_common, H_sim, B_sim_arr)  # 插值仿真曲线

delta_H = B_real - B_sim  # 逐点修正量 [T]
```

### Step 4：参数化修正函数（插值 + 外推）

δ(H) 本身随 H 变化（低场区修正量大，高场区小），且随 ODF 参数变化：

```python
from scipy.interpolate import CubicSpline

# 单锚点：固定修正
cs = CubicSpline(H_common, delta_H, extrapolate=True)

def correct_bh(H_pred, B_sim, params=None, weight=1.0):
    """
    对仿真 B-H 曲线应用修正。
    weight: 1.0 = 完全使用锚点修正（ODF 与锚点相同）
            0.0 = 不修正（ODF 与锚点差异极大时退化）
    """
    correction = cs(H_pred) * weight
    return np.clip(B_sim + correction, 0, 2.5)
```

### Step 5：根据 ODF 距离计算权重（多锚点扩展版）

若有多个锚点（B23R075, B27R090, B30P105），权重基于 ODF 参数空间距离：

```python
ANCHORS = {
    'B23R075': {'f_Goss': 0.92, 'theta': 3.0, 'sigma': 6.0, 'delta': delta_B23},
    'B27R090': {'f_Goss': 0.82, 'theta': 6.0, 'sigma': 8.0, 'delta': delta_B27},
    'B30P105': {'f_Goss': 0.70, 'theta': 10.0, 'sigma': 10.0, 'delta': delta_B30},
}

def odf_distance(p1, p2):
    """ODF 参数空间的加权欧氏距离。"""
    df = (p1['f_Goss'] - p2['f_Goss']) ** 2 * 4.0   # f_Goss 权重 ×4
    dt = (p1['theta']  - p2['theta'])  ** 2 * 0.01  # 角度权重 ×0.01
    ds = (p1['sigma']  - p2['sigma'])  ** 2 * 0.01
    return np.sqrt(df + dt + ds)

def weighted_correction(H_pred, B_sim, params):
    dists = {k: odf_distance(params, v) for k, v in ANCHORS.items()}
    weights = {k: 1.0 / (d + 1e-6) for k, d in dists.items()}
    total_w = sum(weights.values())
    correction = sum(
        (w / total_w) * CubicSpline(H_common, ANCHORS[k]['delta'])(H_pred)
        for k, w in weights.items()
    )
    return np.clip(B_sim + correction, 0, 2.5)
```

---

## 工程实现位置

修正函数建议集成到：
- **`modules/physics_calibrator.py`** — 新增 `apply_reference_correction()` 函数
- **`modules/bh_curve_analyzer.py`** — 在导出前调用修正
- **流水线训练数据**：在 `dataset_builder.py` 的聚合步骤应用修正，
  让 ML 模型直接学习修正后的数据，无需在推理时再次修正

## 前提条件（需先完成）

1. ✅ 完成仿真脚本参数修正验证（见 `mumax3_precision_test/` 桌面目录）
2. ⬜ 运行锚点参数的标准流水线（B27R090 等价参数，64+ 样本）
3. ⬜ 数字化参考材料 B-H 曲线至 H=20000 A/m
4. ⬜ 实现并测试 `apply_reference_correction()` 函数
5. ⬜ 使用修正后数据重新训练代理模型，评估预测误差改善量

## 预期收益评估

| 阶段 | 改动 | B800 预期误差 |
|------|------|------------|
| 当前（仿真参数未修正） | — | ~0.10-0.20T |
| 仿真参数修正后 | H_max、Msat、Ku1 | ~0.06-0.12T |
| + 单锚点修正 | δ(H) 修正 | ~0.02-0.05T |
| + 多锚点加权修正 | 3锚点插值 | ~0.01-0.03T |

单锚点修正目标：B800 误差 < 0.05T（可接受工程精度）。

---

*此文档由 Claude Sonnet 4.6 生成，2026-06-20*
*状态：待实施（仿真验证阶段未完成）*
