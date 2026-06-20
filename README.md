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
go_steel_data/           参考锚点修正         physics_calibrator
实测参考曲线      →  B_corr = B_sim + δ(H)  →  物理约束校准
(B23R075…B30P105)        多锚点 ODF 加权                ↓
                                               ML 训练数据集
                                                      ↓
                                         XGBoost / ExtraTrees 代理模型
                                                      ↓
                          预测 B-H 曲线 (ms 级)  +  参考修正注入
                                                      ↓
                         ANSYS Maxwell .amat      BH 分析报告
```

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
| **B800 / B1000 工程指标** | ⚠️ 系统偏低 0.08–0.28T | 多机制叠加 | 经参考锚点修正可改善（目标 < 0.05T） |
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
4. **绝对值校正**: 通过参考锚点修正将仿真 B-H 向实测曲线校准

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

MuMax3 单磁畴聚合模型（SW 模型）对 RD 方向 B800 系统性偏低 0.08–0.28T，根因是非 Goss 晶粒被"锁定"在各自易轴方向（m_RD ≈ cos θ），而真实材料中畴壁运动使所有晶粒在 H > 10 A/m 时即基本沿 H 方向饱和。

### 核心公式（`modules/reference_corrector.py`）

```
B_corrected(H; ODF) = B_sim(H; ODF) + Δ(H; ODF)

Δ(H; ODF) = Σ_k  w_k(ODF) × δ_k(H)
δ_k(H)    = B_ref_k(H) - B_sim_anchor_k(H)   ← 锚点修正量
w_k(ODF)  = 1/(d_k + ε) / Σ 1/(d_j + ε)      ← 逆 ODF 距离权重
```

**ODF 距离**（加权欧氏距离）：

```python
d = sqrt( (Δf_Goss)²×4.0 + (Δθ₀)²×0.01 + (Δσ)²×0.01 )
```

### 4 个锚点（多锚点扩展版，已实现）

| 锚点牌号 | f_Goss | θ₀ (°) | σ (°) | RD δ(800 A/m) |
|---------|--------|--------|-------|--------------|
| B23R075 | 0.92 | 3.0 | 6.0 | +0.083 T |
| B27R090 | 0.82 | 6.0 | 8.0 | +0.173 T |
| B27R095 | 0.70 | 9.0 | 10.0 | +0.263 T |
| B30P105 | 0.65 | 11.0 | 11.0 | +0.280 T |

修正量随织构强度单调变化（强织构 → 小修正，弱织构 → 大修正），符合物理预期。

### 两种修正模式

| 方向 | 方法 | 原因 |
|------|------|------|
| **RD** | delta 修正法：`B_corr = B_sim + Σ w_k δ_k(H)` | SW 模型能反映相对 ODF 差异，δ(H) 修正绝对偏差 |
| **TD** | 参考数据加权插值：`B_corr = Σ w_k B_ref_k_TD(H)` | SW 硬轴模型完全失效（B_sim_TD(100) ≈ 0.04T vs 实测 0.79T），δ_TD 高达 1.3T 导致越界 |

### 锚点校准数据来源（重要说明）

**当前状态**：`data/reference_anchors/*.json` 中的 `source = "physics_estimate_SW_model"`，即 **尚未运行真实锚点流水线仿真**。

δ(H) 由以下方式估算：
1. `B_ref(H)` — 来自 `go_steel_data/output/<grade>_RD.csv`（实测曲线数字化，真实）
2. `B_sim_anchor(H)` — 用 SW 方程 Newton 迭代解析估算（按 ODF 分布采样 300 晶粒聚合）

当有真实流水线仿真数据时，可替换为精确值：

```bash
python tools/recalibrate_anchor.py \
    --sim-csv data/pipeline_run/B27R090_RD_aggregate.csv \
    --grade B27R090 --dir RD
```

**预期修正精度**：

| 阶段 | 改动 | B800 误差目标 |
|------|------|------------|
| 基线（参数未修正） | — | 0.10–0.20 T |
| 仿真参数修正 | H_MAX=50000, Msat=1.56e6, Ku1=3.6e4 | 0.06–0.12 T |
| + 物理估算锚点修正（当前） | SW 解析估算 δ(H) | 0.02–0.06 T |
| + 真实仿真锚点修正（未来） | 实际流水线数据 δ(H) | < 0.02 T |

---

## 主要功能

| 模块 | 功能 |
|------|------|
| **ODF 织构生成** | 基于 Goss 峰 + 均匀背景生成 N 个随机晶粒取向，支持 σ（散布角）参数控制 |
| **MuMax3 批量调度** | 自动生成 `.mx3` 脚本，支持批量 GPU 任务调度，断点续跑 |
| **多晶 BH 聚合** | 从 OVF 文件提取单晶粒 B-H 点，加权平均到材料级别，支持 0°~180° |
| **Hc 参考数据库** | `go_steel_reference.py`：按牌号/Si% 查表，替换仿真 Hc（~6000× 误差）|
| **基准映射修正** | `reference_corrector.py`：4锚点多向量 ODF 加权 δ(H) 修正，首次调用自动初始化 |
| **物理约束校准** | `physics_calibrator.py`：单调化、B₀ 归一化、参考修正接口 |
| **ML 代理模型** | XGBoost + ExtraTrees 双模型，5折交叉验证，ms 级推断 |
| **快速预测 Web UI** | 浏览器端实时调参 → 即时 B-H 曲线 + 参考修正自动注入 |
| **Maxwell 导出** | 输出 AEDT `$begin` 格式 `.amat`，含各向异性 μr（RD/TD/ND）+ 正确 kh |
| **BH 分析报告** | 3张对比图（全等级 BH、RD 分析、TD 各向异性）+ Bertotti 铁损参数 |

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

主要依赖：`flask numpy scipy matplotlib scikit-learn xgboost joblib`

### 启动 Web UI

```bash
python app.py
```

浏览器访问 `http://127.0.0.1:5000`

### 参考修正自动初始化

首次调用 `predict_bh()` 时，若 `data/reference_anchors/` 中无校准文件，系统会自动运行 SW 物理估算（约 5–10 秒）并缓存结果，无需手动操作。

---

## 模块说明

```
modules/
├── odf_texture.py           ODF 织构采样（Goss + 均匀背景）
├── mx3_generator.py         MuMax3 .mx3 脚本生成（H_MAX=50000, Msat=1.56e6, Ku1=3.6e4）
├── bh_extractor.py          OVF → B-H 点提取（FFT 降噪 + 磁化投影）
├── pipeline_runner.py       批量仿真调度 + 多晶 BH 聚合
├── physics_calibrator.py    物理约束校准 + correct_bh_with_reference() 接口
├── reference_corrector.py   ★ 基准映射修正（4锚点 SW 估算 δ(H)，多锚点 ODF 加权）
├── go_steel_reference.py    ★ Hc 参考数据库（IEC 60404-8-7，< 10 A/m）
├── dataset_builder.py       特征工程 + 训练集生成（含参考 Hc 注入）
├── ml_trainer.py            XGBoost / ExtraTrees 训练、预测（预测时注入参考修正）
├── maxwell_exporter.py      AEDT .amat 生成（参考 Hc → 正确 kh 系数）
├── bh_curve_analyzer.py     BH 曲线对比分析（3 张 matplotlib 图）
├── batch_scheduler.py       GPU 任务队列调度器
└── anisotropy_interpolator.py  角度相关 μr 插值
```

---

## 数据说明

### go_steel_data/output/

包含 **4 个宝钢 GO 硅钢等级**的实测参考 B-H 曲线（RD/TD/combined CSV + .amat），用于：
1. 参考修正锚点（`B_ref_k(H)` in `reference_corrector.py`）
2. 与预测曲线对比显示
3. Maxwell 直接使用参考材料

| 等级 | B800_RD (T) | B800_TD (T) | Hc_RD (A/m) | 说明 |
|------|------------|------------|------------|------|
| B23R075 | 1.965 | 1.70 | 4.0 | Hi-B 激光细化 |
| B27R090 | 1.950 | 1.68 | 5.5 | Hi-B 标准（推荐锚点）|
| B27R095 | 1.925 | 1.65 | 7.0 | 常规 GO |
| B30P105 | 1.901 | 1.60 | 8.5 | 常规 GO |

### data/reference_anchors/

预计算的锚点修正量 δ(H)，每次 `predict_bh()` 调用时使用：

```
B23R075_RD_delta.json   B23R075_TD_delta.json
B27R090_RD_delta.json   B27R090_TD_delta.json   ← 主锚点
B27R095_RD_delta.json   B27R095_TD_delta.json
B30P105_RD_delta.json   B30P105_TD_delta.json
```

`source` 字段说明当前精度：
- `physics_estimate_SW_model` — SW 解析估算（当前，B800 误差 0.02–0.06T）
- `real_pipeline_simulation` — 真实流水线仿真（运行 `tools/recalibrate_anchor.py` 后）

---

## API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/status` | 系统状态（模型加载、数据集统计） |
| POST | `/api/predict` | 快速 B-H 预测（输入 ODF 参数，自动注入参考修正）|
| POST | `/api/analyze/material` | 材料代表性分析（多角度仿真聚合）|
| POST | `/api/analyze/export-bh-analysis` | 导出 `.amat` + 生成 BH 对比分析报告 |
| GET | `/api/bh-analysis/reference-list` | 获取可选对比材料列表 |
| GET | `/api/exports/list` | 历史导出 `.amat` 列表 |
| POST | `/api/train` | 触发 ML 模型训练 |
| GET | `/data/exports/<filename>` | 下载导出文件 |

---

## 文件结构

```
.
├── app.py                              Flask 主程序
├── requirements.txt
├── templates/
│   └── index.html                      单页 Web UI（Chart.js + 原生 JS）
├── modules/                            核心功能模块
│   ├── reference_corrector.py     ★   基准映射修正引擎（新增）
│   ├── go_steel_reference.py      ★   Hc 参考数据库（新增）
│   ├── odf_texture.py
│   ├── mx3_generator.py               仿真参数已修正（H_MAX/Msat/Ku1）
│   ├── bh_extractor.py
│   ├── pipeline_runner.py
│   ├── physics_calibrator.py          新增 correct_bh_with_reference() 接口
│   ├── dataset_builder.py             新增参考 Hc 注入
│   ├── ml_trainer.py                  新增预测时参考修正注入
│   ├── maxwell_exporter.py            新增参考 Hc → kh 修正
│   ├── bh_curve_analyzer.py
│   ├── anisotropy_interpolator.py
│   └── batch_scheduler.py
├── go_steel_data/
│   ├── output/                         实测参考 B-H 曲线（4个牌号 RD/TD CSV）
│   │   ├── metadata.json              含 Hc_RD_Am 字段（新增）
│   │   ├── B23R075_RD.csv / _TD.csv
│   │   ├── B27R090_RD.csv / _TD.csv
│   │   ├── B27R095_RD.csv / _TD.csv
│   │   └── B30P105_RD.csv / _TD.csv
│   └── *.py                            数据生成脚本
├── data/
│   ├── reference_anchors/         ★   锚点 δ(H) 校准文件（新增，8个 JSON）
│   ├── exports/                        .amat 导出（运行时生成）
│   ├── datasets/                       ML 训练集（gitignored）
│   └── models/                         训练好的模型（gitignored）
├── tools/
│   └── recalibrate_anchor.py      ★   用真实仿真数据更新锚点校准（新增）
├── configs/
│   └── paper_grade_pipeline_recommended.json  推荐流水线配置
└── docs/
    ├── material_mapping_strategy.md    映射策略设计文档
    ├── anisotropic_interpolation_workflow.md
    ├── paper_grade_training_protocol.md
    └── physics_constraint_calibration.md
```

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
