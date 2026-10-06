# 电机材料方向与磁静态响应检查（2026-10-06）

两个解析常磁导率对照通过，证实本机 Maxwell 2025 R1 在独立小模型中按物体局部 CS 旋转材料轴。冻结 B23R075 校准曲线的六项对照虽有一致的方向趋势，但均未通过场分布和自适应收敛判据，不能据此接通正式电机优化或宣布真实曲线的场响应已经验证。

## 模型与冻结预算

全新 20 mm × 20 mm 材料方片，XY 磁静态，不含电机、空气间隙或损耗求解。两个相对边赋 A_z=0 与 ±B_target×0.02 Wb/m，另外两边为 H 法向的偶对称边界；理想解为均匀轴向 B。9 个内部点导出 Global CS 的完整 B/H 矢量，使用 SI 单位。边界接口依据 [原生矢量磁位说明](https://ansyshelp.ansys.com/public/Views/Secured/Electronics/v242/en/Subsystems/Maxwell/Content/AssigningaVectorPotentialfora2DMagnetostaticSolver.htm) 和本机 PyAEDT 1.0.0 源码；实际 B 值另行核验。

校准曲线协议 `uniform_flux_directional_controls_v1` 在求解前冻结六个算例：原曲线/Global/y、原曲线/CS90/y、交换 RD/TD/Global/y、面内 RD 等向退化/Global/y、相同退化/CS90/y、原曲线/Global/x。后两个面内分量相同的诊断仍保留 ND=1000 先验，不称三维完全各向同性。所有变换是接口诊断，不是新增实测材料。

每项独立 Desktop 会话、100 秒总墙钟预算、2 CPU 核、0 GPU、最多 6 次自适应，不重试；网格长度限制 2 mm，Energy Error/Delta Energy 各 0.1%，非线性残差 1e-6，线性残差 1e-8，SmoothBHCurve=false。B 轴误差和横向场均须 ≤1e-4 T，H 横向/空间极差须 ≤主分量的 0.1%，等价 RD 对照须 ≤1%，RD 源节点 H 须 ≤1%。门限未在看到结果后放宽。

## 校准曲线结果：拒绝通过

使用 `cal_65e506f6e207` 的原始 B23R075 RD/TD 26 点，原文件和 bank 不改。B_target=1.51547615418 T，是 RD 的 H=20 A/m 输入节点。TD 的预期 H 只作 200–500 A/m 原节点区间检查，线性反插值为诊断值，不作为独立材料误差。

| 对照 | 平均主轴 H（A/m） | 最终能量误差 % | 最终能量变化 % |
|---|---:|---:|---:|
| TD / Global / y | 403.744010 | 12.833 | 2960.2 |
| RD / CS90 / y | 26.175324 | 1.0395 | 8.8268 |
| 交换 RD/TD / Global / y | 26.204276 | 0.090031 | 2.2486 |
| 面内 RD 等向 / Global / y | 26.296413 | 3.2914 | 1.171 |
| 面内 RD 等向 / CS90 / y | 26.296413 | 3.2914 | 1.171 |
| RD / Global / x | 26.288930 | 1.256 | 10.072 |

五项 RD 等价关系均满足预设 1% 门限，TD/RD 主轴 H 比约 15.42；等向材料旋转前后的 H 完全一致。但 RD 响应偏离输入 20 A/m 节点、局部 B/H 不够均匀，六项最后自适应记录均未满足两个 0.1% 条件。求解调用成功、缓存存在、方向趋势正确均不能替代完整数值通过。官方原始每次 adaptive 记录、全部 9 点及失败判据均保留。

## 独立解析对照与程序改进

另立 `linear_sanity_v2` 新预算，仅两项：解析 RD μ_r=1000、TD μ_r=100 的常磁导率曲线（由公式生成，不是实测曲线），相同几何、边界、网格和门限。B_target=0.0251327412287 T，Global/y 得 H=200 A/m、CS90/y 得 H=20 A/m，比值恰为 10；所有采样及两项原生 adaptive 条件通过，误差约 1e-13%。这证明本接口的边界单位和 CS90 旋转在解析模型上有效；工作磁密、曲线形状与校准试验不同，仍不能认定 B23R075 非线性问题的唯一原因。

新 `maxwell_native_convergence.py` 严格读取官方 pass 数、目标、最后能量误差/变化和逐 pass 记录，拒绝缺项、非有限值、改目标和摘要/最后行不一致。v2 必须同时通过场判据和原生收敛才能给出接口通过；解析对照通过也始终保持 `calibrated_material_field_verified=false`、`motor_geometry_direction_verified=false`、损耗/电机排名 false。

v1 是改进收敛检查前的冻结 producer；保留其原始 summary 和全部 producer SHA。新增 CPU 审核用当前检查器独立重算每点及收敛，写 `reviewed_summary.json`；不回改 v1 的原记录。公开证据同时保存 native producer 和 audit producer，明确区分版本。

## 保存、复现与下一步

私有完整 `.aedt`、`.aedtresults`、日志位于 `calibration/diagnostics/directional_field_20261006/{attempt_v1,linear_sanity_v2}/private_native`。公开仅发布 `public_evidence` 的源材料、协议、冻结程序、原生场/收敛、保存定义片段和审核摘要。CPU 审核核对原材料定义及已有 217 个来源文件，未关闭用户应用或 5001/5002。

主工作区的新目录入口：

```powershell
.runtime/magsim/Scripts/python.exe -X utf8 magsim/tools/run_directional_field_controls.py --root D:/Project/go-steel-thesis --study-dir D:/Project/go-steel-thesis/work/new_directional --prepare
.runtime/magsim/Scripts/python.exe -X utf8 magsim/tools/run_directional_field_controls.py --root D:/Project/go-steel-thesis --study-dir D:/Project/go-steel-thesis/work/new_directional --execute
```

`--prepare --linear-sanity` 仅准备两个解析常磁导率对照。CPU 审核使用 `tools/audit_directional_controls.py --root ... --study-dir ... --output-dir ...`，输出必须新建。复现不能重提本轮原目录。公开重算可直接对 `cases/*/{B,H}.fld` 与 `convergence.txt` 调用三个纯 CPU 模块，审核来源 SHA 绑定到相应 frozen producer。

本轮新增磁静态完成 8 项，其中只有 2 项解析对照数值通过；新增电机瞬态 0、MuMax 0。统一工作区累计 Maxwell 调用完成 12（旧电机 4 + 新磁静态 8），不能把 12 当作通过验收数量；MuMax 累计 680 保持。152 项主/公开测试通过（公开跳过 1 项私有归档检查）。525 原 pilot、26 冻结校准文件、217 项当前来源及旧公开证据树保持。

下一步先在新小预算中核查非线性插值/自适应行为、工作磁密与网格对照，并独立读取插片实际几何主轴及局部 CS；不能扩大正式牌号求解以替代这些检查。[ANSYS 2025 R1 曲线说明](https://ansyshelp.ansys.com/public/Views/Secured/Electronics/v251/en/Subsystems/Maxwell/Content/Maxwell/SpecifyingBHCurvesforNonlinearRelativePermeability.htm) 提醒未平滑曲线可能造成非线性收敛问题，平滑时工作点可偏离输入；这是检查线索，尚未证明为本试验的根因。所有损耗仍需独立证据，零损耗占位材料禁止效率求解，最终排名仍不可用。
