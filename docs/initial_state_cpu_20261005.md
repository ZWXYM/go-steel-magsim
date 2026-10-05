# 原生零场状态与分支合同：CPU 检查点（2026-10-05）

用户要求暂不进行 GPU 密集操作。本轮没有启动 MuMax、GPU 训练或其他原生求解，只从已存在的 64 份 n8 table 做 CPU 审计，并准备 16 个状态/路径对照脚本。每 5 小时任务仍 ACTIVE，已加入“用户明确允许恢复前禁止 GPU；空闲不代表授权”。主工作区 AGENTS 和 continuation_plan 同样保存限制。

## 实际新增结果

新增 `directional_native_loop_audit_v1`，支持样品坐标系 RD/TD 和指定角度的场/磁化投影、B=μ0(H+M)、完整有符号 H 序列、原生 H0、四端点、分支反演、能量密度及 torque 状态。与现有中点提取逐粒一致；TD 历史数值诊断保持。旧 parser、table、bank 和预测曲线不更改。

64 个完整回线中，5 粒未通过既有四端点投影筛查（≥0.95），18 粒未通过分支反演筛查（≤0.02 T）。这两种失败可重叠，不能相加作独立失败数。64 粒均未记录 torque，残余 torque 保持未知，不能将“无该列”判作通过。筛查阈值只定位需要检查的路径/数值问题，不认证真实材料性能。

| 材料 | 方向 | 端点筛查失败 / 8 | 反演筛查失败 / 8 | 最大未约束零场中点 / T |
|---|---|---:|---:|---:|
| B23R075 | RD | 2 | 2 | 0.977168 |
| B23R075 | TD | 0 | 3 | 0.000009 |
| B27R090 | RD | 0 | 0 | 0.000037 |
| B27R090 | TD | 1 | 3 | 0.975966 |
| B27R095 | RD | 0 | 1 | 0.000018 |
| B27R095 | TD | 0 | 4 | 0.000070 |
| B30P105 | RD | 1 | 1 | 0.977304 |
| B30P105 | TD | 1 | 4 | 0.977794 |

## 四个较大零场偏移

| 回线 | 晶粒 | 原生未约束 H0 中点 / T | 原生中点 B800 / T | 最小带符号端点投影 | 反演差 / T |
|---|---:|---:|---:|---:|---:|
| B23R075 RD | 8 | 0.977168 | 0.978213 | −0.006282 | 1.971609 |
| B27R090 TD | 2 | 0.975966 | 0.977265 | −0.006230 | 1.971975 |
| B30P105 RD | 7 | 0.977304 | 0.978340 | −0.006280 | 1.971629 |
| B30P105 TD | 5 | 0.977794 | 0.978810 | −0.006310 | 1.971778 |

旧中点代理在 H0 强制设零，但这些回线在 H>0 的偏移仍保留。四组 n8 的未约束 H0 均值约 0.1220–0.1222 T，原生 B800 均值约 0.1235–0.1276 T。作为纯诊断把每粒 B800 与其未约束 H0 作差，均值为 0.00135/0.00173/0.00357/0.00541 T；同四组逐粒 B800 标准差约 0.344–0.345 T，差值标准差为 0.00054–0.00773 T。具体分解在 `zero_state_decomposition.json`。

上述恒等拆分说明这些回线零场偏移与 B800 波动密切相关，是上一轮敏感性的重要待查来源；没有证明减去偏移就是正确物理修正。没有删粒、减偏移、换参考、重选模型或把 H0 数值称为测量 Br。即使小偏移粒也可能存在分支反演失败或未知 torque；不能用低 guard/低偏移认定求解健康。现行留出 RMSE 仍为 0.04404265 T，未获得新的独立材料验证。

## 准备项与当前限制

四材料 RD/TD 均固定取 grain_id=1，每个两种条件，共 16 个脚本。这是事先按编号取样的状态对照，**不是上表四个大偏移粒的复跑**。后续针对表中四粒的对照须另立预算/记录，不能悄悄替换本批固定案例。

- `strict_major_loop`：原几何、晶体轴、材料先验和 50000 A/m 上界；MinimizerStop=1e-7、20 samples，连续完整降/升支，102 个保存点。
- `zero_transverse_pair`：匹配同一 solver，每次 H=0 从两个相反的面内横向均匀磁化重新开始，再各自扫描正场 26 点，52 个保存点。使用 ±2 状态标签，区别于 ±1 大回线分支。

横向相反初态不等于经过认证的去磁态/初始磁化曲线，严格 minimize 也不自动证明 torque 收敛。此批 `prepared_not_executed`，原生执行数量为 0；没有 `run_status` 或新原生表。工具只有准备/CPU 审计能力，没有运行 MuMax 的入口。GPU 限制解除后仍要单独核对静态脚本、建立可控执行预算及新状态解析合同；不得混入旧 bank/代理、材料出口、损耗或效率排名。

## 复现与证据

公开证据 `calibration/initial_state_cpu_20261005`；主工作区对应 `calibration/diagnostics/initial_state_cpu_20261005`。8 份原脚本与 8 份原 table 是复制的旧来源，不计新增求解；另 16 个脚本仅规划。

```powershell
python tools/prepare_initial_state_audit.py --root . --output-dir calibration/new_initial_state_cpu_audit
```

主工作区从根目录执行 `.runtime/magsim/Scripts/python.exe -X utf8 magsim/tools/prepare_initial_state_audit.py --root . --output-dir calibration/diagnostics/new_initial_state_cpu_audit`。输出须为新目录且在 pilot 之外，旧证据不覆盖。

`report.json` 保存全部 64 粒、来源、质量标志、8 组汇总及 16 项计划；`native_state_metrics.csv` 是逐粒摘要；`study_protocol.json` 在读取结果前冻结；`source_integrity.json` 核对旧 525 文件前后 SHA；`frozen_producers/` 与原始来源副本绑定字节。`zero_state_decomposition.json` 是随后只读分解，明确不作修正。

分解可独立复现：`python tools/decompose_zero_state_audit.py --report calibration/initial_state_cpu_20261005/report.json --output calibration/new_zero_state_decomposition.json`。使用逐粒 `B800-H0` 均值和样本标准差（ddof=1），输出必须是新文件，原报告不写回；独立 helper 字节随证据保存。

新增七项测试覆盖 RD/TD/斜角投影、真实 H0 与单位、旧 TD 数值一致性、缺 torque/重复列、方向/完整序列/标签检查、匹配 solver/保留物理前缀、禁止 subprocess.run 的真实 CPU 准备检查及不写回原报告的恒等分解。主工作区共 90 项 unittest、129 个 Python AST 检查通过。旧四个电机扫描结果不重提，原生累计保持 MuMax 668、Maxwell 4。

下一步在 GPU 限制内继续完善质量标志接入工作台、校准/训练来源合同和电机官方 CSV 的 CPU 分析。恢复原生对照必须来自用户的新明确授权，不能靠定时器或 GPU 空闲推断。
