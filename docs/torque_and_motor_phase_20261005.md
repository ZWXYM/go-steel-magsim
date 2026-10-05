# 停止判据与电机周期诊断（2026-10-05）

本轮对四个先前已定位的约 0.977 T 零场偏移粒做固定八项原生对照，同时只读分解两份电机三周期官方报告；两条链都不更换现行校准 bank、代理、材料指标或排名。

## MuMax3 判据和八项对照

本机 MuMax3 3.11.1 的构建标记为 `ee077035`。对应 [minimizer.go](https://github.com/mumax/3/blob/ee077035/engine/minimizer.go) 用最近若干磁化变化量 dM 决定 `MinimizerStop` 停止条件；[torque.go](https://github.com/mumax/3/blob/ee077035/engine/torque.go) 的 `maxTorque` 从当前全网格 torque 重新计算，不能将 dM 条件解释为 torque 达标，也不能将表中 torque 当成旧缓存。[relax.go](https://github.com/mumax/3/blob/ee077035/engine/relax.go) 另有 torque 停止机制，但本轮没有新增 relax 求解或放宽既定 1e-5 T gate。

`large_H0_stop_probe_v1` 事前固定 B23R075 RD g8、B27R090 TD g2、B30P105 RD g7/TD g5，先四项 dM=1e-7，再四项 dM=1e-8；MinimizerSamples=20，原几何、取向、Si 先验、外场方向及 Hmax=50000 A/m 保持，增加 LastErr 遥测。每项限时 60 秒、串行，失败后停止剩余预算，目录禁止重试或覆盖。八项均成功退出并保存 102 点完整 signed schedule，新 MuMax 成功 8、累计 680；Maxwell 仍 4。

| 晶粒 | dM 档位 | 最大 residual torque / T | 反演误差 / T | 未约束 H0 中点 / T | 原生中点 B800 / T |
|---|---|---:|---:|---:|---:|
| B23R075 RD g8 | 1e-7 | 3.08632e-5 | 9.21366e-7 | −9.80177e-8 | 0.00132939 |
| B27R090 TD g2 | 1e-7 | 3.54568e-5 | 1.39185e-6 | 0 | 0.00197808 |
| B30P105 RD g7 | 1e-7 | 3.03077e-5 | 6.07710e-6 | 0 | 0.00130936 |
| B30P105 TD g5 | 1e-7 | 2.80279e-5 | 1.05859e-5 | 4.90088e-8 | 0.00126447 |
| B23R075 RD g8 | 1e-8 | 3.08556e-5 | 2.94053e-7 | −1.47027e-7 | 0.00132920 |
| B27R090 TD g2 | 1e-8 | 3.54237e-5 | **0.18955837** | 4.90088e-8 | 0.00197804 |
| B30P105 RD g7 | 1e-8 | 3.02777e-5 | 2.74450e-7 | 4.90088e-8 | 0.00130922 |
| B30P105 TD g5 | 1e-8 | 2.80028e-5 | 7.13569e-6 | 4.90088e-8 | 0.00126448 |

八项记录的 dM 都达到本档停止条件、端点均通过 0.95 门限，零场偏移降到约 1e-7 T；八项 torque 均失败。更紧设置没有实质改善最大 torque，且 B27R090 分支反演不稳定：不能推断收紧 dM 会普遍改善回线。原生中点 B800 从约 0.978 T 降到约 0.001–0.002 T，说明原偏移高度依赖数值路径；不证明新的中点就是材料真实正常曲线。所有旧粒保留，未把八条选取的数值探针替换进 n8 均值或正式训练，更未用 reference 选粒/选参。下一步宜另立小预算直接检查 torque 控制/离散化和路径稳定性，再决定统一全粒协议。

主证据 `calibration/diagnostics/torque_stop_probe_20261005`，公开对应 `calibration/torque_stop_probe_20261005`；CPU 独立重算：

```powershell
python tools/run_torque_stop_probe.py --run-dir calibration/torque_stop_probe_20261005 --analyze
```

## 电机三周期官方 CSV 分解

`motor_cycle_phase_diagnostic_v1` 只读 `scan_092904b5932d` 的 M6/23ZH90 两案：复制同算例官方转矩、三相电流、CoreLoss/StrandedLoss/SolidLoss CSV 和 result；核对源 SHA，全部 91 点保留、单位转为 s/Nm/A/W。比较 10–20 ms 与 20–30 ms 的 31 点，原 gate 与接受状态不变。DFT 仅用 30 个不重复相位点并做 Parseval 核验，频谱/循环位移用于诊断，不过滤波形或修改正式判定。

| 算例 | 原转矩最大相位差 / Nm | 转矩相位差 RMS / Nm | 最差相位 / ms | 铁损最大相位差 / W | 现行接受 |
|---|---:|---:|---:|---:|---|
| M6 | 0.245182 | 0.092297 | 5.333333 | 25.021706 | false |
| 23ZH90 | 0.239221 | 0.073752 | 1.000000 | 11.303778 | false |

三相输入电流同相位差均 ≤1.151e-13 A，铜损差 ≤1.990e-13 W；时间网格一致。转矩最大差位于周期内部，去除边界的诊断最大值仍为原值，因此不能用丢弃共享端点修复 gate。两案转矩差中第 6–15 谐波分别占诊断功率约 42.35%/36.98%，但低谐波也有差异；不是仅靠单一相位偏移可解释。`TorqueDQ []` 原 CSV 未声明物理单位，单独保留为未确认量，不能替代机械转矩或用于通过判定。

这些结果支持下一步优先做同 V8_2 object-CS 模板的时间步/网格/数值精度对照，而非直接扩大牌号扫描或放宽 0.1 Nm gate；尚不能从这批数据唯一识别原因。没有 Maxwell 新求解、损耗校准或可靠效率排名，原项目及所有原 CSV/结果保留。主证据 `calibration/diagnostics/motor_phase_20261005`，公开对应 `calibration/motor_phase_20261005`。

公开独立 CPU 重算（使用新输出目录）：

```powershell
python tools/audit_motor_phase.py --source-job calibration/motor_phase_20261005/source_job --output-dir calibration/new_motor_phase_audit
```

公开 `source_job` 仅含两案官方 CSV/result/manifest/state，完整 .aedt/.aedtresults 及私有论文不发布。主工作区用 `.runtime/magsim/Scripts/python.exe -X utf8` 和 `magsim/tools/` 入口；重复输出须用新目录。
