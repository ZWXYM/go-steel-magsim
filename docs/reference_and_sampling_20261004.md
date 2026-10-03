# 参考上界与取向采样诊断（2026-10-04 第一轮自动推进）

新增 80 次原生 MuMax3 求解，全部 exit=0；四牌号旧 n8 的原始/派生文件均保留。新研究只完成 B30P105：seed=20261003、N=32、RD/TD 各 32 次；另以 seed=20261004、N=8、RD/TD 各 8 次作采样敏感性检查。累计当前研究原生成功任务为 144 次（旧基线 64 + 本轮 80），不是 320 次。

## 采样波动是当前实质问题

以下是 **未经参考修正、已施加既定 J 约束的大回线中点代理**在 H=800 A/m 的均值；不是实际材料 B800，也不是新版校准精度。

| B30P105 取向集合 | RD B800（T） | TD B800（T） |
|---|---:|---:|
| 旧 n8，seed 20261003 | 0.125734 | 0.127641 |
| 新 n32，seed 20261003 | 0.047008 | 0.060852 |
| 新 n8，seed 20261004 | 0.002776 | 0.005889 |

相同 N=8 的两 seed 均值分别相差 RD=0.122958 T、TD=0.121752 T。新 n32 的晶粒标准差在 H800 为 RD=0.185351 T、TD=0.224389 T，晶粒标准差不等于实验材料置信区间。

同一新 n32 取向集合中的前缀 N8/N16/N32 也不稳定：TD 的 H800 均值依次为 0.007034、0.118997、0.060852 T；N16 与 N32 的差为 0.058145 T。全曲线最大差为 RD N16→N32=0.311097 T、TD=0.069143 T。n32 只是有限比较基准，零“自身差”不能证明已收敛。旧 n8 与新 n32 不是嵌套样本，即使 seed 相同也不保证取向相同。

代码证据：`odf_texture.py::generate_mixed_texture()` 将“随机背景”实现为五个宽高斯峰，其中心 Euler 角随 seed 随机生成；`sample_importance()` 再按多项式分配晶粒。因此跨 seed 同时改变了近似 ODF 和有限取向抽样，不能把上述差异都归为同一分布下的 Monte Carlo 误差。增加晶粒数可以检查某个已固定近似 ODF 的有限采样，但无法保证不同 seed 的模型分布一致。

下一步应使用固定、版本化的背景分布，或实现真正均匀的 SO(3) 背景，再保存每粒取向/组分和随机种子。orix 的官方 [随机取向/四元数采样说明](https://orix.readthedocs.io/en/stable/reference/generated/orix.quaternion.Symmetry.random.html)和 [SO(3) 均匀取样教程](https://orix.readthedocs.io/en/stable/tutorials/uniform_sampling_of_orientation_space.html)提供依据。本轮没有改变旧采样器，也没有用这些诊断材料选择新的修正系数或升级 bank。

## Msat 与参考曲线不完全一致

保持当前先验 Msat=1.56e6 A/m，J 上界为 μ0Msat=1.960354 T。在全部冻结参考节点计算 J=B−μ0H，得到：

| 冻结参考方向 | 超出 J 上界节点数 | 最大超出（T） |
|---|---:|---:|
| B23R075 RD | 47 | 0.037132 |
| B27R090 RD | 32 | 0.032125 |
| B27R095 RD | 27 | 0.025055 |
| B30P105 RD | 23 | 0.017075 |

TD 四曲线未超出上界，但所有八曲线均存在 J 下降区间；例如 B23R075 TD 的既定单调 J 约束最大改动为 0.022428 T。故“同牌号拟合不是零误差”包含先验/处理参考不一致的贡献，不能通过放宽边界来掩盖来源缺口。完整结果与每条参考 SHA256 见 `calibration/convergence_20261004/reference_bounds.json`。参考数据均未改写，Msat 未按留出曲线拟合。

已核对 [Nippon Steel 原始 D008en 手册](https://www.nipponsteel.com/product/catalog_download/pdf/D008en.pdf) Appendix III，PDF 第 14 页、印刷第 24–25 页：HI-B 与 C.G.O. 的通用表列 Saturation Induction=2.03 T。该表没有指定此数值的测试 H，也不是四个 B 牌号同批次的成分证明。不能直接把它改名为 J_s 后覆盖模拟 Msat。PDF、提取页、图像和文件 SHA256 仅存本地证据目录，公开仓库记录来源链接和文件哈希。

现有 `go_steel_data/generate_bh_data.py` 的 CP/checkpoint 值与 `metadata.json` 明确含人为单调修复；原曲线页码、测量定义和同批次成分仍未绑定。本次没有填充未知成分/EBSD字段，也没有把资料估计升级为实测值。

## 可复现入口与完成范围

公开目录 `calibration/convergence_20261004/` 保存两个完整规划 manifest、冻结取向/脚本/参考，以及实际完成的 B30P105 原生 table。n32 manifest 的 256 个计划任务只完成 64 个，seed2 n8 manifest 的 64 个计划任务只完成 16 个；未完成的其余牌号保持待运行。本轮没有调用这两套未完整研究的全材料 `--analyze`，没有新的材料级 bank 或代理模型结论。

```powershell
# 在公开仓库根目录；本地论文目录从 magsim/ 用 ../calibration/runs/...。
python tools/run_calibration_pilot.py --run-dir calibration/convergence_20261004/n32_seed20261003 --n-grains 32 --seed 20261003 --run --only-grade B30P105 --only-direction TD --mumax <可执行文件>
python tools/analyze_sampling_convergence.py calibration/convergence_20261004/n32_seed20261003 --grade B30P105 --direction TD --output-dir tmp/convergence_check --baseline calibration/pilot_20261003_n8
python tools/audit_reference_bounds.py calibration/pilot_20261003_n8 --output tmp/reference_bounds_check.json
```

按牌号/方向筛选只改变待执行任务，不改变 manifest 或其余任务；已完成内容哈希漂移、原生 MuMax3 二进制变化或不完整目标取向集合都会被拒绝。新准备的 manifest 还记录采样器身份和代码哈希，已有冻结 manifest 不事后改写。

本轮新增六个必要合同测试，加上已有测试共 23/23 通过。新增测试验证 B/J 区分、不修复输入、原始哈希漂移、目标集合完整性、筛选续跑及二进制漂移拒绝。原有 0.06581 T 留出 RMSE / 0.15906 T 最大 B800 误差仍属于旧 n8 bank；本轮没有替换其值。

下一轮优先修复并版本化背景取向采样，先核验固定分布/前缀一致性和理想 Goss 轴，再建立新协议下 n8/n32/n64、多 seed 比较。成分原文件及对应 RD/TD 性能来源路径已经向用户提出补充请求；来源缺口期间仍继续可独立完成的程序和采样工作。
