# GO-Steel MagSim — 取向硅钢微磁仿真与材料校准

2026-10-09 转矩执行流程：分析计划新增独立任务准备、手动执行、状态重开、失败/超时管理及官方转矩 CSV 自动整理，复用作者报表入口。主304项软件测试通过，实际网页已核验；执行器测试使用模拟原生接口。本轮1055不可连接，实际新导入/求解0，2454个旧存储文件保持。实际材料导入与端到端分析仍待完成，效率和最终排名禁用。[说明](docs/torque_execution_20261009.md)。

2026-10-09 转矩分析入口：材料库可保存1/3周期分析计划、重开和下载冻结包；新页面显示既有算例的官方转矩及原CSV，四个转矩指标与原扫描一致。主286测试通过，原2448存储文件保持。1055仍不可连接，本次无原生导入或新求解；实际校准材料分析执行待接通，效率和最终排名保持禁用。[说明](docs/bh_only_analysis_20261009.md)。

2026-10-09 新增校准材料原生导入入口：网页准备独立子副本、手动下发、状态重开和保存预检 worker。最终主278测试通过；旧准备若程序变化会显示需新副本。本次1055不可连接，实操保留 ready，未启动 AEDT/求解；下一步实际导入仍待完成。[说明](docs/calibrated_motor_native_20261009.md)。


2026-10-08 材料库新增“准备隔离电机副本”：冻结原材料包和实际bank，生成48插片引用工程，保存记录重开可用。主269测试通过；当前待原生材料导入，没有求解提交或效率排名，不改变既有牌号扫描。[说明](docs/calibrated_motor_preparation_20261008.md)。

2026-10-08 材料库与系统V1验收：新增 `/material-library`，直接重开标定、整牌号留出和保存预测；AMAT、metadata、原输入、父库与有效库、当前材料CSV统一下载。两份校准19条目读回，网页下载7个原文件字节一致；主265测试通过。已有库牌号扫描、官方波形、作者评分、异常与重开流程的功能验收已汇总；校准材料包直接加入电机任务尚待接通，BH-only材料仍禁止效率求解，论文继续暂停。详见 [材料库](docs/material_library_20261008.md) 与 [整套系统V1功能验收](docs/system_delivery_v1_20261008.md)。

2026-10-08 统一系统入口：运行 `python tools/start_workbench.py --open-browser` 或 Windows `start_system.cmd`，打开 `/system`。材料、校准、扫描和电机优化统一导航；原基线、16牌号保存榜单及三目标评分支持配置、独立导出和自动扫描计划，14个完整原案与原函数一致。新M6和23ZDKH75完成官方CSV/汇总，48插片损耗完整；23ZH90原生矩阵分解失败已留档、分类和下载，未重试。244项软件测试通过。端口占用保留旧服务并顺延，首页显示实际版本/资源/存储，默认不接续旧队列。详见 [启动、配置与验收](docs/system_entry_20261008.md)。

2026-10-08 系统功能更新：旧 V8_2 六案结果完整展示并提供原文件下载，一键导出指标与证据包；缺少 final CSV 的原摘要仍可查看，异常案保留。工作台各模块独立加载，单一目录故障不会阻断校准、旧结果和队列；页面自动加载保存的校准。主/公开212项软件测试通过（公开1项私有模型测试跳过），另有网页启动故障隔离检查通过，无新增求解。详见 [结果浏览与使用流程](docs/system_results_20261007.md)。

2026-10-07 最新：网页与命令行接通完整牌号排除预测，修复顶层结果误标父校准库的问题，实际子库及 RD/TD/材料 metadata 一致，可下载 effective_bank.json。四组既有留出曲线与误差不变；主全套 205 测试通过。用户启用 1055 后，许可 TCP 恢复，一个新独立会话成功读取 Maxwell 瞬态设计并释放，实际新求解 0、48 新轴保存 0/48。累计 Maxwell39/MuMax680，真实非线性、独立损耗与最终排名仍未通过。详见 [预测范围与访问检查](docs/prediction_scope_20261007.md)。

2026-10-07：新增解析 B-H 表示对照、48 插片显式方向候选程序与许可连接下发前检查。主/公开目标 197 项测试；本轮原生初始化失败，实际新求解 0、原生保存候选 0/48，Maxwell39/MuMax680 保持。现有许可端口不可连接，原生设计访问被拒绝，原因尚未唯一确认。四样品 bank、留出误差、五旧扫描与旧结果不变；完整本构/损耗/稳态排名仍未通过。详见 [准备与失败证据](docs/bh_and_axes_preparation_20261007.md)。

2026-10-07 空气界面三项新控制完成：显式object-CS/relative-CS的独立B及原生能量通过，忽略方向负对照完成收敛并被排除。常磁导率新coupon方向影响真实求解已有证据；H字段、真实非线性和48旧CS未认证。累计Maxwell39、MuMax680，主186测试，五旧扫描/校准bank不变。详见 [独立方向响应](docs/air_interface_validation_20261007.md)。

2026-10-07 新线性基线：两项高磁导率对照通过 B/H、自适应及原生能量门限；八条冻结源曲线完成逐段 CPU 审计，空气界面计划已准备但尚未原生执行。累计 Maxwell36、MuMax680，真实非线性/方向/损耗和电机排名仍未认证。详见 [线性基线与源段审计](docs/linear_baseline_20261007.md)。

2026-10-07 后续检查：显式方向向量的四项原生能量匹配目标框架，H 字段矛盾仍在；显式 100 次迭代的两个非线性对照仍未收敛。其他材料预测新增联合参数覆盖范围检查，bank 与留出误差保持。累计 Maxwell 34、MuMax 680，最终排名未开放。详见 [方向与泛化范围](docs/axis_and_support_20261007.md)。

2026-10-07 最新接口检查：毫米单位解除了三个平移控制的 TAU 网格失败；斜向对照发现 H 字段与原生总能量不一致，追加能量门限否决了表面通过的方向控制。本轮新增 11 个磁静态完成项、3 个失败，累计 Maxwell 28、MuMax 680；真实材料及最终电机排名仍未验证。详见 [接口核查](docs/interface_controls_20261007.md)。

2026-10-07 最新电机接口核查：完成 5 项非线性小模型对照，平滑/细网格/降低磁密均未通过完整收敛；读取 48 插片原生顶点。object-CS 解析对照预检通过但求解失败，方向语义仍待认证；累计 Maxwell 完成 17，MuMax 680，最终排名未开放。详见 [核查记录](docs/nonlinear_field_20261007.md)。

2026-10-06 新增 6 项校准曲线方向小模型和 2 项解析对照：解析 CS90 响应通过，校准曲线方向趋势一致但场均匀性/原生自适应收敛均未达门限，正式电机/排名保持未验证。新增官方收敛检查及 CPU 审核，旧 bank/源结果保留。详见 [方向场响应记录](docs/directional_field_20261006.md)。

2026-10-06 四样品 RD/TD 在 AEDT 实际保存工程中逐点传输核验通过，B23R075 在独立副本的 48 插片赋值及原生预检通过；损耗仍零占位，旋转/各向同性场响应、损耗与数值收敛未验证，本轮无新求解。详见 [材料传输记录](docs/material_transport_20261005.md)。

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

2026-10-08 官方波形页面已接通：从系统首页或扫描/优化页进入 [波形对比](docs/motor_waveforms_20261008.md)，查看同版本转矩与损耗原采样点，下载对比和原CSV。重开记录与两个CPU进程的未尝试案续接已验收，本轮新原生求解0。
