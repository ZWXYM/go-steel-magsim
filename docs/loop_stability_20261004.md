# 原生回线数值稳定性诊断（2026-10-04）

N128 的 guard 审计发现单粒修改达 1.382096 T，因而先核对原生回线，再决定是否扩大晶粒数。所有原始粒、脚本、旧模型与修正曲线保留。这里的结果是小网格数值诊断，不能作为材料独立校准、实测正常磁化曲线、Hc/Br 或多频铁损。

## 来源与停止判据

核对的是 [MuMax 3.11.1 minimizer 源码](https://github.com/mumax/3/blob/v3.11.1/engine/minimizer.go)、[relax 源码](https://github.com/mumax/3/blob/v3.11.1/engine/relax.go) 和[官方 API](https://mumax.github.io/api.html)。该版本 Minimize 默认检查最近 10 次最大磁化变化、阈值 1e-6；它不是残余 torque 或能量稳定性的直接验收。Relax 默认 -1 依据能量/torque 变化停止，正阈值采用最大 torque 判据。来源 URL、版本、提交和 SHA256 随试验保存；版本标签核对不能替代该 Windows 可执行文件的独立源码重建证明。

另存 `maxTorque`（torque/γ0，T）、原生三维磁化、物理 H 与能量密度。原脚本同时默认输出和 tableadd(m)，故 mx/my/mz 列重复；只有两组数据完全相同时分析器接受它们。若重复列冲突则报错。原始 table 的字段与字节不改写。

## 先声明，再试验

沿用上一轮明确列出的 9 个案例：manifest seed 20261004 的 grain 69/115/82/53，20261005 的 90/102/22，20261006 的 71/62。最后三个是低 guard 改动对照，未假设其求解健康。物理模型、Msat/Kc、网格和晶体轴完全保留；六种条件为原字节复跑、默认 minimize 加 telemetry、MinimizerStop=1e-7/20 samples、默认 relax 连续扫描、默认 relax 负端重置、默认 relax 正负 200000 A/m 与负端重置。较高场条件增加 75000/100000/150000/200000 A/m，比较仍用原共同 H 网格。

v1 预声明 54 次，实际前三次成功，第四次 `RelaxTorqueThreshold=1e-5` 在预饱和阶段 120 秒超时，50 次未尝试。超时进程为本工具所建，旧结果与用户应用未受影响。v1 全部输入、成功表、状态、失败输出、日志及冻结代码在主工作区保留；公开副本保留来源和状态，不上传本机日志。首次 Git 暂存命令目录错误，纠正后的 `a07b145a` 在第一项求解启动后建立；manifest、条件及脚本哈希已在所有求解前冻结，此时间差另有记录，不能声称该 Git 提交先于全部 v1 求解。

v2 新建目录，保持同一 9 个案例及独立报告阈值，用 upstream 默认 RelaxTorqueThreshold=-1；修订根据、v1 状态/manifest 哈希写入 v2 manifest。v2 的全部输入和代码在 `2128939b` 提交后才执行。v1/v2 结果分别统计，复制的原始表不计新增求解，失败和未尝试不计成功求解。

预声明的数值筛查要求残余 torque≤1e-5 T、分支反演差≤0.02 T、四个带符号端点磁化投影≥0.95。它们是诊断阈值，不能替代实际材料性能验收。反演差检查完整有符号回线 `B_desc(H)+B_asc(-H)`，保留真实 H=0 值；guard 工程中点仍单独遵守原 H0=0 约定。

## 旧 N128 完整只读审计

三组 TD/N128 共 384 粒全部读取、逐一核验源脚本/执行表/manifest 与原生来源，不剔除异常。12 粒未通过四端点投影筛查，182 粒分支反演差超过 0.02 T；其中 11 粒端点筛查失败、174 粒反演筛查失败，却具有≤0.01 T 的 guard 改动。旧表没有 torque 列，其残余 torque 保持未知。端点阈值本身不是“不稳定”的充分证明，需结合原始 mz、能量、复跑和 solver 对照。

| manifest seed | 端点筛查失败 | 反演筛查失败 | 低 guard 且端点失败 | 低 guard 且反演失败 |
|---|---:|---:|---:|---:|
| 20261004 | 4 | 55 | 4 | 51 |
| 20261005 | 1 | 64 | 0 | 62 |
| 20261006 | 7 | 63 | 7 | 61 |

这说明小幅 guard 介入不代表原生回线可靠，也不能把旧跨 seed 差异都归为采样噪声。现有 bank/代理不升级，旧 N64/N128 记录仍作为其原协议下的历史诊断保留。

## 复现入口

公开仓库已有冻结诊断，在新输出目录重分析，无需再次求解：

```powershell
python tools/audit_raw_loop_states.py --run-dir calibration/expanded_sampling_20261004/n128_seed20261004 --run-dir calibration/expanded_sampling_20261004/n128_seed20261005 --run-dir calibration/expanded_sampling_20261004/n128_seed20261006 --output-dir tmp/raw_loop_new_audit
python -m unittest discover -s tests -p "test_*.py" -v
```

主工作区试验路径为 `calibration/runs/loop_stability*_20261004`，分析为 `calibration/diagnostics/loop_stability_20261004`。原生执行只能在冻结输入、同一二进制、未修改工具、独占 lock 且无未核验输出时进行。120 秒超时由工具记录并保留，已失败目录不原地重试。后续采用新协议必须重新建立完整收敛/材料校准证据，不能静默复用旧 solver 曲线作为新标签。

## 用户调整后的检查点

v1 成功 3、超时 1；v2 成功 9、超时 1，另 44 项未尝试。默认 relax 的第二个案例在接近扫描末尾到达 120 秒预算，保留 99 行不完整表，不能计为完整回线。v3 另声明 9 案例×5 条件（45 项），用严格 minimize 加超 torque 时的默认 relax 回退，180 秒预算；只准备、未执行，未通过原生验证。最新成功实际计数 668（此前 656+12），超时 2 不计成功。逐项状态/完整结果见 analysis/partial_probe_checkpoint.json。

用户已明确更紧急的任务是四材料标定程序与电机扫描易用界面，故暂不再扩展诊断求解；这不是数值收敛已验收。grain 69/115 的严格 minimize 反演差从约 2.96/2.92 T 降至 6.8e-6/1.5e-5 T，但最大 residual torque 仍约 1.2e-4/1.4e-4 T。grain 69 默认 relax 完整通过该粒数值筛查，其 B800 中点约 0.00556 T。单粒成功不外推到全材料。
