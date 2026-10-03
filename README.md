# GO-Steel MagSim — 取向硅钢微磁仿真与材料校准

ODF 取向采样 → MuMax3 单晶仿真 → RD/TD 曲线聚合 → 校准与独立材料验证 → 代理模型/Maxwell 材料接口。Python 3.10+，Flask；原生试验使用 MuMax3 3.11.1。

## 当前阶段（2026-10-04）

首轮自动推进新增 B30P105 的 n32 双方向及第二 seed 的 n8 双方向，共 80 次原生仿真，合同测试扩展为 23 项。发现显著采样敏感性及“随机背景”随 seed 改变近似 ODF 的问题，见 [参考与采样诊断](docs/reference_and_sampling_20261004.md)。这些数据保留为诊断，尚未生成新的全材料校准 bank。

已修复立方各向异性参数和样品坐标系，新增 `cubic_sample_frame_v2` / `fixed_h_delta_v1` 协议，并保存四牌号 × RD/TD × 8 晶粒的 64 次 GPU 仿真证据。校准、预测 JSON 和 `.amat` 使用同一物理 H（A/m），无需 H 轴缩放。旧平台和历史模型仍走历史修正链；新版校准的独立入口是下方 CLI。

| 验证口径 | 平均 RMSE（T） | B800 最大绝对误差（T） |
|---|---:|---:|
| 原始小晶体代理 | 1.46051 | 见逐材料误差表 |
| 同牌号拟合 | 0.01074 | 不能用作泛化指标 |
| 逐牌号完整留出 | 0.06581 | 0.15906 |
| 相同权重的参考曲线内插对照 | 0.02925 | 0.07450 |

RMSE 是 H≥100 A/m 的规定非均匀网格节点等权统计。**留出 B800 误差并未全部达到 0.05 T 目标，当前 bank 为工程试验版。** 参考内插对照不是微磁预测，其表现优于差分校准，须继续检查残差转移和 ODF 映射。

Si 为报告标称值，ODF 为估计；尚未绑定同批次完整成分证书和 EBSD。小晶体大回线中点是工程代理，不能当作标准初始/正常磁化曲线。铁损系数、Hc、Br 及电机回接未完成验证。导出的 pilot 材料明确标记 `experimental_BH_only`，损耗系数为零占位。

详见 [校准记录](docs/calibration_progress_20261003.md)、[试验数据与协议](calibration/README.md)、[发布差异与后续工作](docs/publication_status.md)。原有功能文档保存在 [历史 README](docs/legacy_README_20260625.md)，历史精度/物理表述不能覆盖当前验证结论。

## 安装与复现

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v
.venv\Scripts\python.exe tests/smoke_v2_workflow.py
```

无需 GPU 即可从已提交的原始 table 重新分析校准与材料留出：

```powershell
python tools/run_calibration_pilot.py --analyze
python tools/predict_calibrated_material.py --bank calibration/pilot_20261003_n8/bank_holdout_B23R075.json --raw-pair calibration/pilot_20261003_n8/B23R075/raw_pair.json --exclude-grade B23R075 --output-dir tmp/calibration_check --name B23R075_independent
```

若重新运行原生求解，另行安装 MuMax3 和兼容 NVIDIA/CUDA 环境。可使用 `--mumax`、`MUMAX3_EXE` 或 PATH 指定可执行文件；校准脚本也支持论文整理工作区的 `.runtime/mumax3/`。

```powershell
python tools/run_calibration_pilot.py --run-dir calibration/convergence_20261004/n32_seed20261003 --n-grains 32 --seed 20261003 --run --only-grade B30P105 --only-direction TD --mumax "D:\mumax3.11.1_windows_cuda12.6\mumax3.exe" --max-jobs 16
```

每次复用同一目录/参数可以继续未完成任务；已完成脚本和 table 的 SHA256 不一致时停止。完整完成后增加 `--analyze`。扩大 N 或更换 seed 使用新目录，保留 n8 基线。重分析会更新派生文件及分析代码哈希，原始 table 保持不变。

原 Flask 平台：`python app.py`。浏览器入口及历史 API 详见历史 README；旧模型不能被标为新版校准模型。合成增强脚本使用显式源材料，例如 `python scripts/augment_bh_curves.py --source go_steel_data/output/GO_Steel_B23R075.amat --n 1 --out tmp/augmentation_check`；它的样本必须跟随源材料划入同一训练/测试组。

## 目录

| 目录 | 内容 |
|---|---|
| `modules/` | ODF、脚本、提取/聚合、历史修正、新版 bank、训练和材料导出 |
| `tools/` | 校准试验的准备/续跑/分析、显式 bank 预测、绘图和历史工具 |
| `calibration/` | 来源登记、冻结输入、64 次基线与 80 次新增采样诊断 table、拟合/留出 bank、误差表和试验材料 |
| `go_steel_data/` | 既有处理参考曲线和材料库，保持其参考/估计身份 |
| `tests/` | 科学合同、便携运行路径与平台烟雾验证 |
| `docs/` | 当前验证记录、研究边界和历史说明 |

公开仓库只保存软件与可复现试验。完整论文、原始 AEDT/Motor-CAD 工程、本机运行环境和来源快照保留在本地论文工作区，原目录未移动或删除。
