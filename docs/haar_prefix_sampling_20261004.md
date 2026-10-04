# 固定分布与可复现前缀采样（2026-10-04 第二轮推进）

本轮新增 `goss_haar_iid_prefix_v2`，保留旧 `legacy_multi_peak_importance_v1` 和全部历史试验。新背景直接在 SO(3) 上均匀取样，不再随 seed 生成五个近似背景峰；seed 仅决定固定分布中的数值实现。参数仍是报告先验，没有新增成分证明或独立 EBSD 测量。

## 分布与坐标约定

用独立、固定编号的 PCG64 流生成每粒的 Bernoulli 组分选择、均匀球面旋转轴、Goss 散布角和背景四元数。归一化各向同性四维高斯四元数给出 Haar SO(3) 背景。SciPy 的 [均匀旋转说明](https://docs.scipy.org/doc/scipy/reference/generated/scipy.spatial.transform.Rotation.random.html)明确其 Haar 定义；实现选择独立 RNG 流以固定前缀，不依赖全局 `np.random.seed()`。

Goss 保留明确的径向散布先验：旋转角 `min(abs(N(0,1)),3)*sigma`，轴在 S² 上均匀；3σ 截顶有点质量。这不是相对于 Haar 测度的高斯 ODF 密度，`halfwidth_deg` 在本协议中是散布 σ，不能作为经 EBSD 测得的 HWHM。全体取向随后绕样品 ND 转过 theta。

Euler 使用 active crystal→sample 的 intrinsic ZXZ，与 `.mx3` 的样品坐标轴一致。中心 `(0°,45°,0°)` 对应立方对称等价的 `{011}<100>` Goss：晶体 [100] 沿样品 RD，样品 ND 在晶体中为 [011]/√2。不是把 [100] 任意改称字面 [001]；立方对称使其与通常的 `{110}<001>` 等价。orix 使用相反的 passive 矩阵方向，已用当前安装的 0.13.3 核对转置关系，并参考其 [Euler 取向定义](https://orix.readthedocs.io/en/stable/reference/generated/orix.quaternion.Orientation.from_euler.html)。

每粒记录组分、Euler、四元数和 Goss 散布角；背景粒的 Goss 角为空。分布 JSON/SHA256 不包含 seed/N，实际 RNG seed、N、库版本、实现 SHA256 和有限组分计数另行保存。相同参数及 seed 下 N8/N32 的行必须与 N64 前缀逐值一致。有限 Goss 比例允许偏离先验 f_Goss；不事后重排晶粒以美化收敛。

## 代码核验

10 万背景样本的轴均值最大绝对值为 0.003445，轴二阶矩相对 1/3 的最大偏差为 0.000932；cos(Phi) 的均匀边际 KS 统计量为 0.001983。Euler/native 轴与 orix passive 矩阵转置的最大差约 1e-15；存储四元数/native 轴最大差约 8.6e-16。N8→N64 前缀、每粒来源和跨 seed 分布指纹均一致。该有限数值检查补充算法构造依据，不是实测织构认证。

bank、原始成对曲线、训练合同、预测和 `.amat` metadata 都携带采样版本。缺省的历史 bank/原始曲线按旧协议兼容处理；新版 bank 必须明确声明锚点协议，未声明或不一致即拒绝。两训练器拒绝混合采样版本或新版与缺失版本混用。校准入口在新目录默认 v2，冻结旧目录按其旧版本续跑；显式要求不同版本会停止。每个 run 增加独占求解锁，防止两个进程覆盖同一个状态表。

## 原生试验与筛查口径

预先记录两个 manifest seed=20261004/20261005；B30P105 的实际材料 seed=20261007/20261008。每 seed、每方向 N64，共 256 个目标任务；两个全材料 manifest 各准备 512 个任务，其余牌号未运行。N8/N16/N32 直接使用每个 N64 的嵌套前缀，不重新抽样，也不与旧近似背景的曲线合并。

采样筛查阈值在求解前冻结：每 seed/方向 N32→N64 的 |H800 均值差|≤0.02 T、全曲线最大差≤0.05 T，两 seed N64 的 |H800 均值差|≤0.05 T。它们是工程采样筛查，不能替代 0.05 T 的独立材料校准目标；即使全部通过，也只能适用于本材料、两个 seed 和既定先验。

曲线是未参考校准、已施加既定 J 约束的大回线中点工程代理。工具同时保存约束前集合均值和逐粒约束改动，晶粒标准差不是实验置信区间。

256 次目标求解全部 exit=0，累计研究成功任务为 64+80+256=400 次。每个新 manifest 完成 128/512，只有 B30P105 已完成双方向；总计 1,024 个新规划脚本不能计作已完成试验。

| 实际材料 seed | 方向 | N64 H800 代理均值（T） | N32−N64 H800 差（T） | 全曲线 N32/N64 最大差（T） |
|---|---|---:|---:|---:|
| 20261007 | RD | 0.037376 | −0.003982 | 0.136707 |
| 20261007 | TD | 0.089341 | 0.019841 | 0.098397 |
| 20261008 | RD | 0.020010 | 0.015604 | 0.077284 |
| 20261008 | TD | 0.010658 | −0.003455 | 0.124016 |

四组的 H800 前缀差都通过 0.02 T 筛查，但全曲线差全部超过 0.05 T。两个 N64 seed 的 H800 差为 RD=−0.017365 T、TD=−0.078683 T；全曲线最大差为 RD=0.299424 T、TD=0.176620 T。因此整体筛查为 `sampling_screen_failed_needs_more_grains_seeds`，不能只挑 RD 或 H800 作收敛结论。

两 seed 的 Goss 实现比例分别为 44/64=0.6875 与 54/64=0.84375，先验为 0.79。固定解析分布已消除背景峰模型随 seed 变化的混杂，有限混合比例和组分内取向波动仍需收敛。H800 的约束前后集合均值在四组中一致；个别 TD 晶粒的全曲线 J 约束改动可达 0.158199 T，已单列记录，不隐去约束影响。

新增科学合同后主工作区 35/35 unittest 通过；旧平台 11 个烟雾函数在新建公开布局隔离副本中通过，未覆盖旧测试输出。103 个 Python 文件 AST 预检通过。四组源数据、完整筛查与图件在 `calibration/haar_prefix_20261004/analysis`。图例使用实际材料 seed，全部 256 个晶粒纳入聚合；N4/N8/N16/N32/N64 均显示，H=0 仅从对数轴显示中排除并保留在源 CSV。SVG/PDF 的文字可编辑，600 dpi TIFF 和 PNG 预览一起保存。

本轮没有更新完整 n8 bank、拟合/留出误差、代理、损耗或电机结果。下一轮先分解组分计数和组分内方差，再预先冻结更大 N、至少三个 seed 的试验预算；相同参数/seed 下应逐文件核验并复用 N64 的原生前缀，记录复用来源与实际新求解数。若比较固定权重的分层估计，必须另设聚合协议版本与独立筛查，不能悄悄替换现有等体积均值。

## 公开复现

```powershell
python tools/verify_texture_sampling.py --output tmp/sampler_verification.json
python tools/run_calibration_pilot.py --run-dir calibration/haar_prefix_20261004/n64_seed20261004 --n-grains 64 --seed 20261004 --run --only-grade B30P105 --texture-sampling-version goss_haar_iid_prefix_v2 --mumax <mumax3可执行文件>
python tools/summarize_sampling_study.py --run-dir calibration/haar_prefix_20261004/n64_seed20261004 --run-dir calibration/haar_prefix_20261004/n64_seed20261005 --protocol calibration/haar_prefix_20261004/analysis/study_protocol.json --output-dir tmp/haar_screen
python tools/plot_sampling_study.py --analysis-dir tmp/haar_screen --output-dir tmp/haar_figures
```

不要对未完成的全材料 manifest 调用全材料校准 `--analyze`，也不要将这些单材料收敛诊断提升为新 bank 或正式代理训练集。后续仍须完成全部材料的多 seed 收敛、来源绑定、独立校准、Hc/Br/多频损耗、电机和优化验收。
