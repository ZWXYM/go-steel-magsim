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
> It integrates ODF texture generation → MuMax3 micromagnetic simulation → polycrystalline B-H aggregation → ML surrogate model training → ANSYS Maxwell `.amat` export and BH curve analysis report, all from a single Flask web UI.

---

## 目录 / Contents

- [研究背景](#研究背景)
- [系统架构](#系统架构)
- [主要功能](#主要功能)
- [快速开始](#快速开始)
- [模块说明](#模块说明)
- [数据说明](#数据说明)
- [API 接口](#api-接口)
- [参考材料数据库](#参考材料数据库)
- [文件结构](#文件结构)
- [License](#license)

---

## 研究背景

取向硅钢（Grain-Oriented Silicon Steel, GO Steel）是变压器铁芯的核心磁性材料，其磁各向异性由 **Goss 织构 {110}⟨001⟩** 主导。传统磁性能表征依赖物理实验，成本高、周期长，难以覆盖连续织构参数空间。

本平台构建了一条**从 ODF 织构参数到角度相关 B-H 曲线的完整数值仿真 + 机器学习代理模型工作流**，将一次 B-H 曲线预测的时间从小时级（MuMax3）压缩至**毫秒级**（ML 推断），同时保留对 ANSYS Maxwell 的直接导出能力。

---

## 系统架构

```mermaid
graph LR
    A["ODF 参数\n(φ, σ, f_Goss…)"] -->|odf_texture.py| B["晶粒取向集合\nN=200~2000 grain"]
    B -->|mx3_generator.py| C["MuMax3 脚本\n(.mx3)"]
    C -->|MuMax3 GPU| D["磁化场 OVF\n时序输出"]
    D -->|bh_extractor.py| E["单晶粒 BH 点"]
    E -->|pipeline_runner.py| F["多晶聚合 BH 曲线\nRD / TD / θ°"]
    F -->|physics_calibrator.py| G["物理校准\nB₀ 归一化 · 单调性"]
    G -->|dataset_builder.py| H["特征数据集\n(CSV)"]
    H -->|ml_trainer.py| I["代理模型\nXGBoost / ExtraTrees"]
    I -->|Web UI 预测| J["B-H 预测曲线\n⚡ ms级"]
    J -->|maxwell_exporter.py| K[".amat (AEDT 格式)"]
    J -->|bh_curve_analyzer.py| L["BH 分析报告\n3张对比图 + 指标"]
    K --> M["ANSYS Maxwell\n电磁仿真"]
```

---

## 主要功能

| 模块 | 功能 |
|------|------|
| **ODF 织构生成** | 基于 Goss 峰 + 均匀背景生成 N 个随机晶粒取向，支持 σ（散布角）参数控制 |
| **MuMax3 批量调度** | 自动生成 `.mx3` 脚本，支持批量 GPU 任务调度，断点续跑 |
| **多晶 BH 聚合** | 从 OVF 文件提取单晶粒 B-H 点，加权平均到材料级别，支持 0°~180° |
| **物理约束校准** | 消除数值伪差（初始非单调、B₀ 漂移），对齐工程坐标系 |
| **ML 代理模型** | XGBoost + ExtraTrees 双模型，5折交叉验证，ms 级推断 |
| **快速预测 Web UI** | 浏览器端实时调参 → 即时 B-H 曲线 → 与历史结果对比 |
| **Maxwell 导出** | 输出 AEDT `$begin` 格式 `.amat`，含各向异性 μr（RD/TD/ND）+ 铁损系数 |
| **BH 分析报告** | 3张对比图（全等级 BH、RD 分析、TD 各向异性）+ Bertotti 铁损参数 |
| **材料代表性分析** | 多角度仿真结果的分组统计、ODF 传递矩阵可视化 |
| **自选对比材料** | 可选取 go_steel_data 参考等级 + 历史仿真导出，定制对比图 |

---

## 快速开始

### 环境要求

- Python 3.10+
- MuMax3 3.10（仅仿真调度模块需要，预测模块不需要）
- CUDA 兼容 GPU（MuMax3 仿真，可选）

### 安装依赖

```bash
git clone https://github.com/ZWXYM/go-steel-magsim.git
cd go-steel-magsim
pip install -r requirements.txt
```

`requirements.txt` 主要依赖：

```
flask
numpy
scipy
matplotlib
scikit-learn
xgboost
joblib
```

### 启动 Web UI

```bash
python app.py
```

浏览器访问 `http://127.0.0.1:5000`

### 初始化数据目录

首次运行时，系统自动创建以下目录（如不存在）：

```
data/
  exports/      ← .amat 导出文件
  datasets/     ← ML 训练数据集（gitignored）
  models/       ← 训练好的模型（gitignored）
```

---

## 模块说明

```
modules/
├── odf_texture.py          ODF 织构采样（Goss + 均匀背景，von Mises / Bunge 参数化）
├── mx3_generator.py        MuMax3 .mx3 脚本生成器（PBC 边界、交换耦合、各向异性 Ku）
├── bh_extractor.py         OVF → B-H 点提取器（FFT 降噪 + 磁化投影）
├── pipeline_runner.py      批量仿真调度 + 多晶 BH 聚合器
├── physics_calibrator.py   物理约束校准（单调化、B₀ 归一化）
├── dataset_builder.py      特征工程 + 训练集 CSV 生成
├── ml_trainer.py           XGBoost / ExtraTrees 训练、评估、保存
├── maxwell_exporter.py     AEDT .amat 生成（各向异性 + Bertotti 系数）
├── bh_curve_analyzer.py    BH 曲线对比分析（PCHIP + NNLS，3 张 matplotlib 图）
├── batch_scheduler.py      GPU 任务队列调度器
└── anisotropy_interpolator.py  角度相关 μr 插值
```

---

## 数据说明

### go_steel_data/output/

包含 **10 个宝钢 / IEC 标准 GO 硅钢等级**的参考 `.amat` 文件（B-H 曲线 + 铁损数据），用于与仿真结果对比：

| 等级 | B₈₀₀ RD (T) | P₁₅/₅₀ (W/kg) | P₁₇/₅₀ (W/kg) |
|------|------------|--------------|--------------|
| GO_Steel_23QG090 | ~1.88 | 0.90 | — |
| GO_Steel_27QG095 | ~1.87 | 0.95 | — |
| GO_Steel_27QG100 | ~1.86 | 1.00 | — |
| GO_Steel_30QG105 | ~1.85 | 1.05 | — |
| GO_Steel_30QG120 | ~1.83 | 1.20 | — |
| GO_Steel_35QG155 | ~1.80 | 1.55 | — |
| GO_Steel_IEC_M080_23P | ~1.90 | 0.80 | — |
| GO_Steel_IEC_M089_27P | ~1.88 | 0.89 | — |
| GO_Steel_IEC_M111_30P | ~1.86 | 1.11 | — |
| GO_Steel_IEC_M140_35P | ~1.83 | 1.40 | — |

> 铁损数据用于 Bertotti 三分量模型（Kh/Kc/Ke）的 NNLS 拟合。

### .amat 格式（AEDT $begin 格式）

```
$begin 'GO_Sim_20250101'
    'CoordinateSystemType'='Cartesian'
    $begin 'permeability'
        'property_type'='AnisoProperty'
        $begin 'component1'        # RD 方向 μr
            $begin 'BHCoordinates'
                Points[N: 0,0, H1,B1, H2,B2, ...]
            $end 'BHCoordinates'
        $end 'component1'
        $begin 'component2'        # TD 方向 μr
            ...
        $end 'component2'
        'component3'='1000'        # ND 方向 μr（各向同性近似）
    $end 'permeability'
    'core_loss_kh'='8.500000e-04'
    'core_loss_kc'='2.467146e-05'
    'core_loss_ke'='0.000000e+00'
    'core_loss_equiv_cut_depth'='0.00035meter'
    'mass_density'='7650'
$end 'GO_Sim_20250101'
```

---

## API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/status` | 系统状态（模型加载、数据集统计） |
| POST | `/api/predict` | 快速 B-H 预测（输入 ODF 参数） |
| POST | `/api/analyze/material` | 材料代表性分析（多角度仿真聚合） |
| POST | `/api/analyze/export-bh-analysis` | 导出 `.amat` + 生成 BH 对比分析报告 |
| GET | `/api/bh-analysis/reference-list` | 获取可选对比材料列表（参考等级 + 历史仿真） |
| GET | `/api/exports/list` | 历史导出 `.amat` 列表 |
| POST | `/api/train` | 触发 ML 模型训练 |
| GET | `/data/exports/<filename>` | 下载导出文件 |

---

## 参考材料数据库

`bh_curve_analyzer.py` 内置 Bertotti 损耗参数数据（`LOSS_POINTS`），来源为宝钢产品手册与 IEC 60404-8-7。计算方法：

- **Kc（古典涡流）**：`π²·σ·d²/(6·ρm)`，d=板厚，σ=电导率，ρm=质量密度
- **Kh、Ke（磁滞 + 超量）**：NNLS 拟合 P₁₅/₅₀ 和 P₁₇/₅₀ 两个测量点

> ⚠️ 正常 B-H 曲线（单调磁化）无法确定剩磁 Br 和矫顽力 Hc，后者需要完整磁滞回线。

---

## 文件结构

```
.
├── app.py                          Flask 主程序
├── requirements.txt
├── templates/
│   └── index.html                  单页 Web UI（Chart.js + 原生 JS）
├── modules/                        核心功能模块
│   ├── odf_texture.py
│   ├── mx3_generator.py
│   ├── bh_extractor.py
│   ├── pipeline_runner.py
│   ├── physics_calibrator.py
│   ├── dataset_builder.py
│   ├── ml_trainer.py
│   ├── maxwell_exporter.py
│   ├── bh_curve_analyzer.py
│   ├── anisotropy_interpolator.py
│   └── batch_scheduler.py
├── go_steel_data/
│   ├── output/                     宝钢/IEC 参考等级 .amat（10 个等级）
│   └── *.py                        数据生成脚本
├── docs/                           设计文档
│   ├── anisotropic_interpolation_workflow.md
│   ├── paper_grade_training_protocol.md
│   └── physics_constraint_calibration.md
├── data/                           运行时数据（gitignored）
│   ├── exports/                    .amat 导出
│   ├── datasets/                   ML 训练集
│   └── models/                     训练好的模型
└── scripts/                        批处理脚本占位
```

---

## License

MIT License — 学术研究与非商业用途。引用请注明出处。

```
@misc{go-steel-magsim-2025,
  title  = {GO-Steel MagSim: A Micromagnetic Simulation and ML Surrogate Platform for GO Silicon Steel},
  author = {YUCE},
  year   = {2025},
  url    = {https://github.com/ZWXYM/go-steel-magsim}
}
```
