# GO-Steel MagSim — 取向硅钢微磁仿真与材料校准

2026-10-05 电机复核定位插片铁损覆盖 0/48、标量 B-H 与 RD/TD 本构未接通；新副本的 M6/23ZH90 原生预检已确认损耗覆盖 48/48，未求解。数值通过与最终比较资格分开，旧结果保留。5001 原进程因自动审批拦截重启而保持；新版在独立 5002 CPU 预览核验。详见 [电机复核与下一步](docs/motor_review_20261005.md)。

ODF 取向采样 → MuMax3 单晶仿真 → RD/TD 曲线聚合 → 校准与独立材料验证 → 代理模型/Maxwell 材料接口。Python 3.10+，Flask；原生试验使用 MuMax3 3.11.1。

## 当前阶段（2026-10-04）

2026-10-05 继续新增 [停止判据与电机周期诊断](docs/torque_and_motor_phase_20261005.md)：八项固定大偏移粒对照证明 dM 停止与 torque 门限需分开检查；H0 偏移降至约 1e-7 T，torque 均未达标，更紧设置不保证分支改善。两案官方电流时间/相位正常重复，转矩/铁损内部差仍未通过原门限；全部 91 点保留，未制造效率排名。116 项主测试通过，现行 bank/预测保持；两个新证据目录与 CPU 独立重算入口已提供。

2026-10-05 用户恢复 GPU 授权后，完成 [首批四项状态对照](docs/initial_state_gpu_20261005.md)：B30P105 固定第 1 粒、RD/TD、严格大回线与双横向初态。严格 TD 反演误差由 1.059 T 降至 0.000108 T，但四项 residual torque 均未达冻结门限，初态影响仍明显；全部 raw/来源保留，未替换 bank 或宣称精度提高。107 项测试通过；本机工作台已恢复普通模式，旧失败扫描及已准备任务未自动提交。公开 CPU 独立重算入口在说明中。

2026-10-05 新增 [跨材料泛化修正试验](docs/generalization_20261005.md)：固定八个候选、内层材料选参/外层完整牌号留出，平均 RMSE 从 0.06581 T 降至 0.04404 T、最大 B800 误差从 0.15906 T 降至 0.10616 T，约下降三分之一，仍未全部达到 0.05 T。76 项测试通过，四个留出 bank/材料文件和完整来源独立保存，原协议可对照。仅参考对照仍更好，新增批次及原生仿真额外预测价值尚未验证；不升级旧代理或损耗模型。工作台可直接选择新方法、查看指标及下载。

最新用户优先级已落到 `/workbench`：四个已确认样品的原生仿真/参数/参考 B-H 校准、留出检验、其他材料修正及批量导出；V8_2 模板上的网页牌号选择、隔离扫描队列、重启恢复及含验收原因的完整官方报告包。支持固定三周期、第二/第三周期一致性检验以及单周期对照，不混合排名。启动 `python tools/start_workbench.py --port 5001`，访问 `http://127.0.0.1:5001/workbench`，见 [使用与验收](docs/workbench_20261004.md)。68 项测试通过；中断项保留，只有未尝试牌号可生成新任务继续。另有 12 次成功数值诊断及 2 次超时留档，累计成功研究求解 668；v3 仅准备未运行，未升级旧 bank。

第三轮完成三个 seed 的 TD/N128 阶段，新增 256 次真实求解并核验复用前缀；44 项测试通过。H800 seed 极差 0.059629 T，筛查仍失败，并发现较大的 guard 介入；见 [当前扩大采样与来源审计](docs/expanded_sampling_20261004.md)。当前研究实际求解累计 656 次，未升级 bank/代理。

第二轮推进新增 `goss_haar_iid_prefix_v2`，使用固定分布的均匀 SO(3) 背景、逐粒来源及可复现嵌套前缀；bank/训练/预测/材料 metadata 检查采样版本。B30P105 的两个 seed、N64 双方向试验与收敛筛查见 [固定分布采样记录](docs/haar_prefix_sampling_20261004.md)。旧采样器、完整 n8 bank 与既有代理仍保留。

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

新增 [质量工作台与 CPU 模式](docs/workbench_quality_20261005.md)：逐方向原生质量表、冻结来源下载、预测/材料 metadata 继承及正式训练标签筛查。使用 `python tools/start_workbench.py --port 5001 --cpu-only` 可校准、准备和整理任务，原生求解/训练提交暂停，启动不恢复队列；本轮未改变 bank 或曲线，100 项测试通过。

新增 [CPU 原生零场/分支审计](docs/initial_state_cpu_20261005.md)：64 份旧回线中发现四个约 0.977 T 的未约束零场中点，同时存在端点/反演筛查失败；保留全部粒，不把扣除偏移当作校准。16 个状态对照只准备未运行，工具 `tools/prepare_initial_state_audit.py` 没有原生求解入口；未知 torque、同牌号标定和独立验证分开标识。

新增 [原生残差敏感性诊断](docs/native_transfer_sensitivity_20261005.md)：直接从冻结的四材料外层 bank 和 64 份 table 生成晶粒/分支/约束报告，不调参、不新增求解。固定相同权重的仅参考诊断优于现行模型；最终约束会遮蔽部分波动，bank 仍为试验版。入口为 `python tools/audit_calibration_native_sensitivity.py --root . --artifact calibration/generalization_20261005/cal_65e506f6e207 --output-dir calibration/new_native_sensitivity`，使用新目录。

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v
.venv\Scripts\python.exe tests/smoke_v2_workflow.py
```

无需 GPU 即可从已提交的原始 table 重新分析校准与材料留出：

```powershell
New-Item -ItemType Directory -Path tmp -Force | Out-Null
Copy-Item -LiteralPath calibration/pilot_20261003_n8 -Destination tmp/n8_reanalysis -Recurse
python tools/run_calibration_pilot.py --run-dir tmp/n8_reanalysis --analyze
python tools/predict_calibrated_material.py --bank calibration/pilot_20261003_n8/bank_holdout_B23R075.json --raw-pair calibration/pilot_20261003_n8/B23R075/raw_pair.json --exclude-grade B23R075 --output-dir tmp/calibration_check --name B23R075_independent
```

若重新运行原生求解，另行安装 MuMax3 和兼容 NVIDIA/CUDA 环境。可使用 `--mumax`、`MUMAX3_EXE` 或 PATH 指定可执行文件；校准脚本也支持论文整理工作区的 `.runtime/mumax3/`。

```powershell
python tools/run_calibration_pilot.py --run-dir calibration/convergence_20261004/n32_seed20261003 --n-grains 32 --seed 20261003 --run --only-grade B30P105 --only-direction TD --mumax "D:\mumax3.11.1_windows_cuda12.6\mumax3.exe" --max-jobs 16
```

每次复用同一目录/参数可以继续未完成任务；已完成脚本和 table 的 SHA256 不一致时停止。完整完成后增加 `--analyze`。扩大 N 或更换 seed 使用新目录，保留 n8 基线。重分析会更新派生文件及分析代码哈希，因此先复制到新目录。新目录默认使用 Haar/prefix v2，冻结目录保留其采样版本；新旧版本不能合并校准或训练。

原 Flask 平台：`python app.py`。浏览器入口及历史 API 详见历史 README；旧模型不能被标为新版校准模型。合成增强脚本使用显式源材料，例如 `python scripts/augment_bh_curves.py --source go_steel_data/output/GO_Steel_B23R075.amat --n 1 --out tmp/augmentation_check`；它的样本必须跟随源材料划入同一训练/测试组。

## 目录

| 目录 | 内容 |
|---|---|
| `modules/` | ODF、脚本、提取/聚合、历史修正、新版 bank、训练和材料导出 |
| `tools/` | 校准试验的准备/续跑/分析、显式 bank 预测、绘图和历史工具 |
| `calibration/` | 来源登记、冻结输入、64 次基线与累计 604 次新增研究求解的原生证据（复用不重复计数）、拟合/留出 bank、工作台限定官方 CSV、误差表和试验材料 |
| `go_steel_data/` | 既有处理参考曲线和材料库，保持其参考/估计身份 |
| `tests/` | 科学合同、便携运行路径与平台烟雾验证 |
| `docs/` | 当前验证记录、研究边界和历史说明 |

公开仓库只保存软件与可复现试验。完整论文、原始 AEDT/Motor-CAD 工程、本机运行环境和来源快照保留在本地论文工作区，原目录未移动或删除。
