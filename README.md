# GO-Steel MagSim — 取向硅钢微磁仿真与机器学习代理模型平台

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10%2B-blue?logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/Flask-3.x-lightgrey?logo=flask" alt="Flask">
  <img src="https://img.shields.io/badge/MuMax3-3.10-orange" alt="MuMax3">
  <img src="https://img.shields.io/badge/XGBoost%20%2F%20ExtraTrees-ML-green?logo=scikit-learn&logoColor=white" alt="ML">
  <img src="https://img.shields.io/badge/ANSYS%20Maxwell-.amat-red" alt="ANSYS">
  <img src="https://img.shields.io/badge/license-MIT-blue" alt="License">
</p>

> **English abstract:** A full-stack research platform for grain-oriented (GO) silicon steel.  
> Integrates ODF texture generation → MuMax3 micromagnetic simulation → polycrystalline B-H aggregation → reference-anchored correction → ML surrogate model training → ANSYS Maxwell `.amat` export, all from a single Flask web UI.

---

## 目录 / Contents

- [研究背景](#研究背景)
- [系统架构](#系统架构)
- [物理建模说明](#物理建模说明特征晶体代理模型)
- [Hc 参考数据库](#hc-参考数据库)
- [基准映射修正机制](#基准映射修正机制)
- [主要功能](#主要功能)
- [快速开始](#快速开始)
- [模块说明](#模块说明)
- [数据说明](#数据说明)
- [API 接口](#api-接口)
- [文件结构](#文件结构)
- [License](#license)

---

## 研究背景

取向硅钢（Grain-Oriented Silicon Steel, GO Steel）是变压器铁芯的核心磁性材料，其磁各向异性由 **Goss 织构 {110}⟨001⟩** 主导。传统磁性能表征依赖物理实验，成本高、周期长，难以覆盖连续织构参数空间。

本平台构建了一条**从 ODF 织构参数到角度相关 B-H 曲线的完整数值仿真 + 机器学习代理模型工作流**，将一次 B-H 曲线预测的时间从小时级（MuMax3）压缩至**毫秒级**（ML 推断），同时保留对 ANSYS Maxwell 的直接导出能力。

---

## 系统架构

```
ODF 参数                 MuMax3                  多晶聚合
(f_Goss, θ₀, σ)  →  单晶粒 B-H 曲线  →  加权平均 B-H(RD/TD/θ°)
                                                      ↓
go_steel_data/        ┌── RD: δ(H) smoothstep 修正  ┐
实测参考曲线      →   └── TD: Hk 轴缩放参考插值修正  ┘
(B23R075…B30P105)         多锚点 ODF 加权 IDW
                                    ↓
                          修正后 B-H 训练数据集
                          (dataset_builder.py)
                                    ↓
                      XGBoost / ExtraTrees 代理模型
                          ↓            ↓
                      物理守恒检查   ANSYS Maxwell
                      (单调化/饱和)   .amat 导出
                          ↓
                      预测 B-H 曲线 (ms 级)
                          ↓
                      BH 分析报告 (3 图 + CSV)
```

**关键设计原则**：参考曲线修正在**训练数据生成阶段**一次性完成（RD + TD 均修正），XGBoost 学习的目标值已是修正后的物理正确 B 值，预测时无需再做参考修正，只保留物理守恒检查（单调性、饱和上限裁剪）。

---

## 物理建模说明（"特征晶体"代理模型）

### 仿真的实际物理含义

本平台的 MuMax3 仿真采用**单磁畴静态能量最小化**方法（`minimize()` 准静态求解器）。

**每一次 MuMax3 计算代表的是**：具有特定晶体学取向的**理想单晶参考体**（"特征晶体"）在准静态磁场下的能量最小化磁化响应。这不是对真实取向硅钢薄片微观结构的直接仿真，而是对**单一晶粒晶体学取向贡献**的理论计算。

```
仿真对象:  理想单晶特征晶体 (无畴壁, 无缺陷, 相干旋转)
物理过程:  Stoner-Wohlfarth 能量最小化
物理意义:  该取向的"非滞后磁化曲线" (可逆磁化分量)
仿真参数:  Msat=1.56e6 A/m, Ku1=3.6e4 J/m³, H_MAX=50,000 A/m (1.36×Hk)
```

**多晶聚合**：ODF 织构参数定义的取向概率分布，通过对 N 个特征晶体的加权平均，得到织构贡献的宏观 B-H 曲线（Taylor-Sachs 晶粒平均）。

### 模型适用范围与已知局限

| 物理量 | 建模质量 | 实际物理机制 | 说明 |
|--------|---------|------------|------|
| **饱和磁感应强度 Bsat** | ✅ 较准确 | μ₀Msat 决定 | Fe-3%Si: ≈ 2.0T |
| **高场 B-H 各向异性 (H > 2000 A/m)** | ✅ 定性正确 | 织构各向异性控制 | RD/TD 差异规律可靠 |
| **B800 / B1000 工程指标（修正后）** | ✅ 修正后误差 < 0.05T | 参考锚点 δ(H) 修正 | RD 已通过 smoothstep 修正 |
| **TD B-H 曲线形状（修正后）** | ✅ 修正后与参考一致 | Hk 缩放 + 参考插值 | 消除 SW 硬轴过估导致的曲线失真 |
| **初始磁导率 μ_init** | ⚠️ 失真 | 畴壁可逆弯曲决定 | 不在本模型物理范围内 |
| **矫顽力 Hc** | ❌ 严重失真 | 实测 < 10 A/m | 仿真 ≈ **H_k ≈ 34,000–36,000 A/m**（~6000× 误差）|
| **磁滞回线形状** | ❌ 定性错误 | 畴壁钉扎-去钉扎决定 | 不可用 |
| **铁损 W/kg** | ❌ 不可直接预测 | 高频涡流 + 畴壁损耗 | 通过 Bertotti 参数估算，非仿真直接输出 |

### 矫顽力差异的根本原因

```
实际机制 (Hc < 10 A/m):
  磁化反转 = 180° 畴壁在毫米尺度 Goss 晶粒内移动
  能垒 ≈ 钉扎能 (晶格缺陷、夹杂物) → 极小
  Hc ∝ 钉扎场 << 各向异性场 H_k

本仿真 (Hc ≈ H_k ≈ 34,000–36,000 A/m):
  磁化反转 = 所有磁矩相干旋转 (Stoner-Wohlfarth)
  能垒 = 各向异性能 K1 → 较大
  Hc ≈ H_k = 2K1/(μ₀Msat) = 2×36000/(4π×10⁻⁷×1.56e6) ≈ 36,728 A/m
```

已通过 MuMax3 外部精度测试（`mumax3_precision_test/v7`）实验验证：
- 实测仿真 Hc = 34,203 A/m ≈ H_k（符合 Stoner-Wohlfarth 理论预测）
- 真实 GO 钢 Hc < 10 A/m（来自 `go_steel_reference.py` 参考数据库）

**为什么无法在合理时间内修正**：

```
畴壁宽度: δ_w = π√(A/K1) = π√(2.1×10⁻¹¹ / 3.6×10⁴) ≈ 75 nm
可信仿真最小晶粒尺寸 ≥ 5×δ_w ≈ 375 nm (94×94×94 cells @ 4nm)
RTX 2060 估算: > 8 小时/晶粒
当前网格 (4×4×1 = 16nm): 比畴壁宽度小 4.7×，物理上无法支持畴壁
```

### 正确使用方式

1. **可用于预测**: 高场 B-H（H > 2000 A/m）、各向异性比（B_RD/B_TD）、不同 ODF 参数间的相对差异趋势
2. **不可用**: 矫顽力、剩磁、磁滞回线、B-H 膝部精确值
3. **Hc 来源**: `go_steel_reference.py` 参考数据库（< 10 A/m 实测值）
4. **绝对值校正**: 通过参考锚点修正将仿真 B-H 向实测曲线校准（RD: δ(H) smoothstep；TD: Hk 缩放）

---

## Hc 参考数据库

`modules/go_steel_reference.py` 提供基于 IEC 60404-8-7 和宝钢/JFE 产品手册的参考矫顽力值，替换仿真中严重偏高的 Hc：

| 牌号 | Hc (A/m) | 厚度 | 说明 |
|------|---------|------|------|
| B23R075 | **4.0** | 0.23mm | Hi-B 激光细化，最高品质 |
| B27R090 | **5.5** | 0.27mm | Hi-B 标准 |
| B27R095 | **7.0** | 0.27mm | 常规 GO |
| B30P105 | **8.5** | 0.30mm | 常规 GO，较厚 |
| B35P135 | **9.5** | 0.35mm | 标准常规 GO |
| 默认 (Si=3.0%) | **6.0** | — | 无牌号时按 Si% 插值 |

**影响链路**：
- `dataset_builder.py` → 训练集 Hc 字段使用参考值
- `ml_trainer.predict_bh()` → 预测输出 `Hc_reference_Am` 字段覆盖
- `maxwell_exporter.py` → `kh = μ₀×Hc/(π×Bmax)` 使用参考 Hc，物理正确的磁滞损耗系数

---

## 基准映射修正机制

### 问题与动机

MuMax3 单磁畴聚合模型（SW 模型）对不同方向 B-H 曲线存在系统性失真，但根因不同：

| 方向 | 失真原因 | 失真特征 |
|------|---------|---------|
| **RD** | 非 Goss 晶粒"锁定"在各自易轴（m_RD≈cosθ），畴壁运动缺失 | B800 系统偏低 0.08–0.28T；Hc_sim >> Hc_ref |
| **TD** | 硬轴相干旋转模型夸大磁化困难度 | H 轴位置失真（Hk_sim/Hk_ref ≈ 10×），曲线形状完全失效 |

### ODF 加权（多锚点 IDW）

```
d_k = sqrt( (Δf_Goss)²×4.0 + (Δθ₀)²×0.01 + (Δσ)²×0.01 )
w_k = (1/d_k) / Σ (1/d_j)   ← 逆 ODF 距离权重（IDW）
```

**4 个锚点**：

| 锚点牌号 | f_Goss | θ₀ (°) | σ (°) | RD B800 误差（修正前）|
|---------|--------|--------|-------|---------------------|
| B23R075 | 0.92 | 3.0 | 6.0 | +0.083 T |
| B27R090 | 0.82 | 6.0 | 8.0 | +0.173 T |
| B27R095 | 0.70 | 9.0 | 10.0 | +0.263 T |
| B30P105 | 0.65 | 11.0 | 11.0 | +0.280 T |

### RD 修正：对数域 Smoothstep δ(H) 法

**物理根据**：RD 磁化由 180° 畴壁运动主导，Hc_sim >> Hc_ref（约 6000×），但曲线形状在对数 H 轴上与参考曲线可对齐。

```python
# H 轴重映射（缩放至真实 H 空间）
H_lo   = 0.5 × Hc_ref          # 过渡区下限
H_hi   = 10  × Hc_ref          # 过渡区上限
scale  = Hc_ref / Hc_sim        # ≈ 0.005

# 平滑混合权重（cubic smoothstep，log H 域）
t       = clamp((log(H_real) - log(H_lo)) / (log(H_hi) - log(H_lo)), 0, 1)
alpha   = t² × (3 - 2t)         # smoothstep

# 最终修正
B_corr  = (1 - alpha) × B_sim + alpha × Σ w_k × B_ref_k(H_real)
```

**效果**：膝部转折位置正确（H≈10 A/m 而非 ≈600 A/m），B800 误差 < 0.05T。

### TD 修正：Hk 缩放参考加权法

**物理根据**：TD 磁化由相干旋转主导，但 SW 模型高估各向异性场：
- Hk_sim（仿真硬轴饱和场，= B=50%Bmax 处 H）≈ 7000 A/m
- Hk_ref（参考曲线对应值）≈ 100–200 A/m
- scale_TD = Hk_ref / Hk_sim ≈ **0.088–0.15**（比 RD 的 scale_RD ≈ 0.005 大约 20–30×）

```python
# H 轴重映射
H_real_TD  = H_sim × scale_TD      # scale_TD = Hk_ref / Hk_sim

# IDW 加权参考曲线插值（直接用参考 B 值，无 δ 修正）
B_corr_TD  = Σ w_k × B_ref_k_TD(H_real_TD)

# 高 H 延伸（H > H_sim_max × scale_TD ≈ 662 A/m）
# 额外查询参考 TD 在 [700, 800, 1000, ..., 50000] A/m 处的值
# 存为 entry['H_td_full'] / entry['B_td_full']（17 点，H 到 50000 A/m）
```

**典型数值**（F92_T0 配置，scale_TD=0.088）：

| H_real (A/m) | 50 | 200 | 500 | 1000 | 2000 | 5000 |
|-------------|-----|-----|-----|------|------|------|
| B_corr (T)  | 0.29 | 1.15 | 1.58 | 1.71 | 1.80 | 1.89 |

各向异性比 B_TD/B_RD 在 H=5000 A/m 处达到 **0.96**（参考材料 0.91–0.96），消除旧版本的 0.84 平台化问题。

### 修正在流水线中的位置

```
仿真原始 B-H
    ↓ dataset_builder.py（训练数据生成时，一次性完成）
    ├── angle=0°  → RD smoothstep δ(H) 修正  → B_train_RD（已修正）
    └── angle=90° → TD Hk 缩放参考插值修正  → B_train_TD（已修正）
                                                      ↓
                                              XGBoost 训练目标
                                                      ↓
                                              XGBoost 预测输出
                                                      ↓
                                         physics_calibrator（仅物理检查）
                                         单调性 / 饱和上限 / RD≥TD 一致性
```

Web UI 中的 **δ(H) 修正开关**（`/api/analyze/apply-delta-correction`）是独立的可视化工具，用于交互对比修正前后曲线差异，不影响训练数据生成。

---

## 主要功能

| 模块 | 功能 |
|------|------|
| **ODF 织构生成** | 基于 Goss 峰 + 均匀背景生成 N 个随机晶粒取向，支持 σ（散布角）参数控制 |
| **MuMax3 批量调度** | 自动生成 `.mx3` 脚本，支持批量 GPU 任务调度，断点续跑 |
| **多晶 BH 聚合** | 从 OVF 文件提取单晶粒 B-H 点，加权平均到材料级别，支持 0°~180° |
| **Hc 参考数据库** | `go_steel_reference.py`：按牌号/Si% 查表，替换仿真 Hc（~6000× 误差）|
| **RD δ(H) 修正** | `reference_corrector.py`：对数域 smoothstep 混合，4锚点 IDW 加权 |
| **TD Hk 缩放修正** | `reference_corrector.py`：Hk_ref/Hk_sim 轴缩放 + IDW 参考插值，延伸至 50000 A/m |
| **物理约束校准** | `physics_calibrator.py`：单调化、B₀ 归一化、RD/TD 一致性检查 |
| **ML 代理模型** | XGBoost + ExtraTrees 双模型，5折交叉验证，ms 级推断（训练数据已含参考修正） |
| **δ(H) 可视化开关** | Web UI 实时对比修正前/后 RD + TD 曲线（橙色/红色虚线叠加层）|
| **Maxwell 导出** | 输出 AEDT `$begin` 格式 `.amat`，含各向异性 μr（RD/TD/ND）+ 正确 kh |
| **BH 分析报告** | 3张对比图（全等级 BH、RD 分析、TD 各向异性热图）+ CSV + Bertotti 铁损参数 |

---

## 快速开始

### 环境要求

- Python 3.10+
- `scipy`（参考修正三次样条需要）
- MuMax3 3.10（仅仿真调度模块需要，预测模块不需要）
- CUDA 兼容 GPU（MuMax3 仿真，可选）

### 安装依赖

```bash
git clone https://github.com/ZWXYM/go-steel-magsim.git
cd go-steel-magsim
pip install -r requirements.txt
```

主要依赖：`flask numpy scipy pandas matplotlib scikit-learn xgboost joblib`

### 启动 Web UI

```bash
python app.py
```

浏览器访问 `http://127.0.0.1:5000`

### 典型使用流程

```
1. 织构生成  →  输入 f_Goss/θ₀/σ，生成 N 粒晶粒取向文件
2. MuMax3 仿真  →  批量生成 .mx3 脚本，提交 GPU 队列
3. 聚合分析  →  选择 output 目录，查看多角度 B-H 曲线
4. 导出 .amat  →  点击"导出 .amat + BH对比报告"，自动应用 TD/RD 修正
5. ML 训练  →  扫描数据集（已含参考修正），一键训练 XGBoost
6. 快速预测  →  输入 ODF 参数，毫秒级返回 B-H 曲线
```

### 参考修正自动初始化

首次调用相关功能时，若 `data/reference_anchors/` 中无校准文件，系统会自动运行 SW 物理估算（约 5–10 秒）并缓存结果，无需手动操作。

---

## 模块说明

```
modules/
├── odf_texture.py             ODF 织构采样（Goss 峰 + 均匀背景，支持 σ 控制散布角）
├── mx3_generator.py           MuMax3 .mx3 脚本生成（H_MAX=50000, Msat=1.56e6, Ku1=3.6e4）
├── bh_extractor.py            OVF → B-H 点提取（FFT 降噪 + 磁化投影）
├── pipeline_runner.py         批量仿真调度 + 多晶 BH 聚合
├── physics_calibrator.py    ★ 物理约束校准（单调化/饱和裁剪/RD-TD 一致性检查）
│                               calibrate_bh_curve() / calibrate_material_pair()
├── reference_corrector.py   ★ 基准映射修正引擎
│                               RD: smoothstep δ(H) 对数域混合（4锚点 IDW 加权）
│                               TD: Hk_ref/Hk_sim 轴缩放 + IDW 参考插值
│                               apply_reference_correction() / get_td_scale()
├── go_steel_reference.py    ★ Hc 参考数据库（IEC 60404-8-7，< 10 A/m）
├── dataset_builder.py       ★ 训练数据集生成
│                               angle=0°: RD δ(H) smoothstep 修正后存入训练特征
│                               angle=90°: TD Hk 修正后存入训练特征
│                               同时存储 H_td_full/B_td_full（17点，到50000 A/m）供导出
├── ml_trainer.py            ★ XGBoost / ExtraTrees 训练与预测
│                               训练数据已含参考修正，预测后仅运行物理守恒检查
│                               predict_bh() 返回 {RD, TD, scalars, calibration_report}
├── paper_surrogate_trainer.py 论文级代理模型训练（精细化超参数，交叉验证）
├── maxwell_exporter.py        AEDT .amat 生成（各向异性 μr + 参考 Hc → kh）
├── bh_curve_analyzer.py       BH 曲线对比分析（3 张图：全等级/RD 膝部/TD 各向异性热图）
├── anisotropy_interpolator.py 角度相关 μr 插值（0°/90°/θ° 插值到全角度）
└── batch_scheduler.py         GPU 任务队列调度器（断点续跑，多卡支持）
```

---

## 数据说明

### go_steel_data/output/

包含多个 GO 硅钢等级的实测参考 B-H 曲线（RD/TD CSV），用于：
1. 参考修正锚点（`B_ref_k(H)` in `reference_corrector.py`）
2. 与仿真/预测曲线对比显示（BH 分析报告三图）
3. Maxwell 直接使用参考材料参数

**4 个主锚点等级（IEC/宝钢实测数据）**：

| 等级 | B800_RD (T) | B800_TD (T) | Hk_TD (A/m) | Hc_RD (A/m) | 说明 |
|------|------------|------------|------------|------------|------|
| B23R075 | 1.965 | 1.70 | ~80 | 4.0 | Hi-B 激光细化 |
| B27R090 | 1.950 | 1.68 | ~105 | 5.5 | Hi-B 标准（主锚点）|
| B27R095 | 1.925 | 1.65 | ~130 | 7.0 | 常规 GO |
| B30P105 | 1.901 | 1.60 | ~160 | 8.5 | 常规 GO |

### data/reference_anchors/

预计算的 RD 锚点修正量 δ(H)，每次修正调用时通过 IDW 加权使用：

```
B23R075_RD_delta.json   B27R090_RD_delta.json   ← 主锚点
B27R095_RD_delta.json   B30P105_RD_delta.json
```

`source` 字段说明当前精度：
- `physics_estimate_SW_model` — SW 解析估算（当前，B800 误差 0.02–0.06T）
- `real_pipeline_simulation` — 真实流水线仿真（运行 `tools/recalibrate_anchor.py` 后）

### material_representative_summary.json（运行时生成）

每个仿真配置目录下生成，包含关键字段：

```json
{
  "scale_td": 0.088308,
  "angles": {
    "0":  { "B_at_std_H": [...], "hc_sim_median": 34203.0, ... },
    "90": {
      "B_corrected_td": [0.022, 0.058, 0.247, 0.628, 1.154, 1.38, 1.55, 1.645],
      "scale_td": 0.088308,
      "H_td_full": [8.8, 17.7, ..., 662.3, 700, 800, ..., 50000],
      "B_td_full": [0.022, 0.058, ..., 1.645, 1.653, 1.674, ..., 1.966]
    }
  }
}
```

- `scale_td`：Hk_ref_TD / Hk_sim_TD，TD H 轴缩放因子
- `B_corrected_td`：8 个 STANDARD_H_POINTS 处的修正 B 值（H_real 最大 ≈ 662 A/m）
- `H_td_full` / `B_td_full`：17 点完整 TD 参考曲线（H_real 延伸至 50000 A/m），导出 .amat 时使用

---

## API 接口

### 材料分析

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/analysis/scan-output` | 扫描 output 目录，返回可用配置列表 |
| POST | `/api/analysis/analyze-path` | 分析指定配置路径，返回多角度 B-H 汇总 |
| GET | `/api/analysis/material-representative` | 获取代表性材料摘要（含 scale_td、B_corrected_td、H_td_full）|
| GET | `/api/analysis/full-direction` | 全方向（0°~90°）B-H 曲线数据 |

### 修正与导出

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/analyze/apply-delta-correction` | 实时对指定 H/B 数据应用参考修正（方向=RD/TD，返回修正曲线 + scale_td）|
| POST | `/api/analyze/export-maxwell` | 导出仿真结果为 ANSYS Maxwell `.amat` |
| POST | `/api/analyze/export-bh-analysis` | 导出 `.amat` + 生成 3 张 BH 对比分析图 + CSV |
| GET | `/api/analyze/exports` | 历史导出文件列表 |
| GET | `/api/bh-analysis/reference-list` | 获取可用参考材料列表 |
| GET | `/data/exports/<filename>` | 下载导出文件 |

### 机器学习

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/ml/train` | 触发 ML 模型训练（XGBoost / ExtraTrees）|
| POST | `/api/ml/paper-train` | 论文级精细化训练（含超参数搜索）|
| GET | `/api/ml/models` | 已训练模型列表 |
| POST | `/api/ml/predict` | 快速 B-H 预测（输入 ODF 参数，预测输出已在修正空间内）|

### 织构与仿真调度

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/texture/generate-single` | 生成单个 ODF 织构文件 |
| POST | `/api/texture/generate-batch` | 批量生成织构配置 |
| POST | `/api/mx3/generate` | 生成 MuMax3 `.mx3` 脚本 |
| POST | `/api/batch/generate` | 批量生成仿真任务脚本 |
| POST | `/api/pipeline/start` | 启动完整流水线（ODF→仿真→聚合）|
| GET | `/api/pipeline/<pid>/state` | 查询流水线任务状态 |

---

## 文件结构

```
.
├── app.py                              Flask 主程序（所有 API 路由）
├── requirements.txt                    Python 依赖（flask/numpy/scipy/pandas/xgboost等）
├── templates/
│   └── index.html                      单页 Web UI（Chart.js + 原生 JS）
│                                       含 δ(H) 修正开关（RD橙色+TD红色虚线叠加）
├── modules/                            核心功能模块
│   ├── reference_corrector.py     ★   基准映射修正引擎（RD smoothstep + TD Hk 缩放）
│   ├── go_steel_reference.py      ★   Hc 参考数据库（IEC 60404-8-7）
│   ├── physics_calibrator.py      ★   物理守恒检查（单调化/饱和裁剪/RD≥TD）
│   ├── dataset_builder.py         ★   训练集生成（RD+TD均已修正，存H_td_full）
│   ├── ml_trainer.py              ★   XGBoost 训练/预测（仅物理检查，无二次修正）
│   ├── odf_texture.py                  ODF 织构采样
│   ├── mx3_generator.py               MuMax3 脚本生成（参数已校准）
│   ├── bh_extractor.py                OVF → B-H 点提取
│   ├── pipeline_runner.py             批量仿真调度 + 多晶聚合
│   ├── paper_surrogate_trainer.py     论文级代理模型训练
│   ├── maxwell_exporter.py            AEDT .amat 生成
│   ├── bh_curve_analyzer.py           BH 对比分析（3图：全等级/RD膝部/TD热图）
│   ├── anisotropy_interpolator.py     角度相关 μr 插值
│   └── batch_scheduler.py             GPU 任务队列调度器
├── go_steel_data/
│   ├── output/                         实测参考 B-H 曲线（CSV + .amat）
│   │   ├── metadata.json              含 Hk_TD_Am、Hc_RD_Am 等字段
│   │   ├── B23R075_RD.csv / _TD.csv
│   │   ├── B27R090_RD.csv / _TD.csv
│   │   ├── B27R095_RD.csv / _TD.csv
│   │   └── B30P105_RD.csv / _TD.csv
│   └── compare_curves.py              参考曲线对比脚本（BH 分析基线）
├── data/
│   ├── reference_anchors/         ★   RD 锚点 δ(H) 校准文件（4个JSON，首次自动生成）
│   ├── exports/                        .amat 导出文件（运行时生成）
│   ├── datasets/                       ML 训练集（gitignored）
│   ├── models/                         训练好的模型（gitignored）
│   └── sim_bh/                         仿真 B-H 中间数据缓存
├── output/                             MuMax3 仿真输出（OVF 文件、角度子目录）
├── input/                              待仿真的晶粒取向文件
├── preinput/                           织构生成中间文件
├── grain_scripts/                      生成的 .mx3 仿真脚本
├── scripts/                            批量任务调度脚本
├── tools/
│   └── recalibrate_anchor.py      ★   用真实仿真数据更新锚点校准
├── configs/
│   └── paper_grade_pipeline_recommended.json  推荐流水线配置
├── tests/                              单元测试
└── docs/
    ├── material_mapping_strategy.md    映射策略设计文档
    ├── anisotropic_interpolation_workflow.md
    ├── paper_grade_training_protocol.md
    └── physics_constraint_calibration.md
```

---

## 精度现状

| 指标 | 修正前 | 修正后（当前） | 说明 |
|------|--------|--------------|------|
| RD B800 误差 | 0.10–0.28 T | **< 0.05 T** | smoothstep δ(H) 修正 |
| TD B-H 形状 | 完全失效（1.42T平台）| **与参考曲线一致** | Hk 缩放修正 |
| TD 各向异性比 @H=5000 | 0.84（均匀平台）| **0.92–0.96** | H 延伸到 50000 A/m |
| Hc（RD）| 34,203 A/m（SW理论值）| **< 10 A/m** | go_steel_reference 替换 |

---

## License

MIT License — 学术研究与非商业用途。引用请注明出处。

```
@misc{go-steel-magsim-2026,
  title  = {GO-Steel MagSim: A Micromagnetic Simulation and ML Surrogate Platform for GO Silicon Steel},
  author = {YUCE},
  year   = {2026},
  url    = {https://github.com/ZWXYM/go-steel-magsim}
}
```
