# 首批恢复 GPU 的状态对照（2026-10-05）

用户明确告知 GPU 密集任务结束并允许使用 GPU，先前临时限制已解除，五小时自动任务仍 ACTIVE。历史 CPU 准备证据保持原样；本轮只串行执行事先按编号选定的 B30P105 grain 1、RD/TD、两个条件共四次 MuMax3，不重提旧电机任务。

## 预算和来源

`native_state_controls_v1` 冻结四个脚本、原始单粒脚本/table、producer、物理 H 和 MuMax3 二进制 SHA；每次最多 120 秒，执行/schema 失败后停止剩余预算，已尝试目录不能重试或覆盖。四次均正常退出并通过完整 schedule/原生来源核验，共约 49.872 秒；不是材料级精度验证。102 点严格大回线和 52 点双横向初态脚本与先前 CPU 准备文件逐字节相同。

主证据在 `calibration/diagnostics/initial_state_gpu_20261005`，公开证据在 `calibration/initial_state_gpu_20261005`。旧 CPU report 内 16 项仍保留 `prepared_not_executed` 历史状态，新 manifest/run_status 登记实际执行的四项；其他三个牌号的 12 个脚本仍未执行。B23R075 RD g8、B27R090 TD g2、B30P105 RD g7/TD g5 四个大 H0 偏移案例不属于本轮选取，不将 grain 1 的结论替代它们。

## 实际结果

| 条件 | 方向 | 最大 residual torque / T | 分支反演误差 / T | 未约束 H0 中点 / T | 原生中点 B800 / T |
|---|---|---:|---:|---:|---:|
| 严格大回线 | RD | 3.57459e-5 | 9.21366e-7 | 0 | 0.00165147 |
| 严格大回线 | TD | 6.32374e-5 | 1.07917e-4 | −9.80177e-8 | 0.00284985 |
| 双横向初态 | RD | 6.22565e-5 | 不适用 | 不作中点材料代理 | 不作中点材料代理 |
| 双横向初态 | TD | 2.51496e-5 | 不适用 | 不作中点材料代理 | 不作中点材料代理 |

严格 TD 反演误差由原固定粒 1.05898946 T 降至 0.00010792 T；RD 原误差 0.00010323 T，本次约 0.00000092 T。两条严格回线均通过端点 ≥0.95 与反演 ≤0.02 T 筛查，但四次最大 torque 均超过预设 1e-5 T。门限保持，严格两条回线均未获数值完整通过，更未进入校准或正式训练。

双横向初态 B800 分别为 RD 0.38005705 / −0.17836355 T，TD 0.26278886 / −0.16504105 T；同粒初态差为 0.55842060 / 0.42782991 T。严格回线两支的 B800 分离约 3.89702 / 3.88139 T，中点接近零并不证明真实正常磁化响应。保留负值、原生 H0 和两条初态曲线，不平移、删粒或将横向初态认证为去磁曲线。

旧 525 份 pilot、45 份 CPU 准备证据、26 份冻结校准文件 SHA 保持。新增七项测试覆盖物理投影/单位、state-phase 序列、缺 torque、失败 torque 不被其他筛查掩盖、CPU-only 准备、超时保留/停止/禁止重试及原生 table 哈希复核；全部 107 项主工作区测试通过。MuMax 成功累计 672，Maxwell 仍 4。现行 bank、曲线、材料留出 RMSE 0.04404265 T 均未改，无新增独立材料/成分/损耗验证。

## 入口和后续

CPU 独立重算已有证据：

```powershell
python tools/run_initial_state_controls.py --run-dir calibration/initial_state_gpu_20261005 --analyze
```

主工作区对应 `.runtime/magsim/Scripts/python.exe -X utf8 magsim/tools/run_initial_state_controls.py --run-dir calibration/diagnostics/initial_state_gpu_20261005 --analyze`。创建新的对照需要 `--prepared-dir`、新 `--run-dir`，真实执行另加 `--execute` 和有效的 `--mumax`；不要对当前已完成目录追加执行。该工具固定四项，不会自动扩大晶粒或继续其它材料。

5001 工作台恢复普通模式，网页可提交隔离求解；原四个失败任务及上一轮新准备的双材料任务均未自动提交。下一步核查 solver 停止判据与记录 torque 的关系，并为四个大偏移粒另立固定对照预算；不能仅靠放宽门限或减去 H0 推进正式训练。电机先分析既有三周期官方 CSV 相位差，再决定时间步/周期预算，保留原门限。
