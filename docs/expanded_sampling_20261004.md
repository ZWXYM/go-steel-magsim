# 三 seed、更大 N 与原生证据复用（2026-10-04）

本轮采用冻结的 `goss_haar_iid_prefix_v2`。完整当前阶段为 B30P105、TD、N128、三个预先选定的 manifest seed：20261004/20261005/20261006；实际材料 seed 为 20261007/20261008/20261009。RD 和其余三个材料尚不属于已完成阶段。先扩 TD 的依据是上轮该方向失败，不能据此宣称材料各方向收敛。

## 有限样本误差分解

以 q 表示有限样本 Goss 比例、p=0.79 表示既定先验，g/b 分别为 Goss/背景曲线均值。原聚合 M=qg+(1-q)b；独立版本的诊断对照 F=pg+(1-p)b，严格有 M−F=(q−p)(g−b)。总体方差用 ddof=0，等于组内 Goss、组内背景与组间三项之和。标准误采用样本方差的 iid plug-in，仅作诊断，无同步置信带或材料验证意义。

两个 seed 的对称分解为 ΔM=(q₂−q₁)(g₁+g₂−b₁−b₂)/2+q̄Δg+(1−q̄)Δb。这是带符号的精确恒等式，不能把各项解释成因果百分比。N64 的 TD H800 总差 −0.078683 T，计数项 +0.004193 T，Goss 组内项 −0.090659 T，背景组内项 +0.007783 T。固定先验加权不能消除本例差异；该对照未作为新标签或校准输出。

## 预先冻结的当前阶段

`analysis/study_protocol.json` 在新增求解前保存阈值：每 seed 的 N64−N128 H800 差绝对值≤0.02 T，全曲线最大差≤0.05 T，三 seed N128 的 H800 极差≤0.05 T。任一失败均不能升级 bank。后续计划为 N256、三个 seed、RD/TD，并补齐所有材料和来源；N256 数值尚未作为本轮实测结果。

当前三目录各规划四牌号、双方向 N128，共 3,072 份脚本。前三个牌号仅准备。前两个目录各复用 128 份已验证 N64 原生证据，覆盖 RD/TD；第三个不复用。当前 TD 阶段需要 384 份有效证据，其中 128 份为已有 TD 前缀，本轮预算新增 256 次真正执行的 TD 求解。RD 只有前两个目录的各 64 份前缀。

## 复用合同

`tools/reuse_native_prefix.py` 要求独立的更大-N目录，固定 seed、解析分布、采样器 SHA、物理 H、物理/修正版本、材料参数/参考与逐粒 Euler/组分完全一致。逐一检查源 table、实际执行脚本及目标脚本哈希；源目录有锁时拒绝，目标使用与 native runner 相同的独占锁。重复调用不更改已有成功证据。

成功记录的 `execution_kind=imported_native_prefix`、`elapsed_seconds=0`，并保存最近父来源与 `root_native_origin`。新执行记录为 `native`。源 ID 采用主归档名称，公开副本目录映射为 `calibration/haar_prefix_20261004/n64_seed...` 与 `calibration/expanded_sampling_20261004/n128_seed...`；源 manifest/table 哈希不会因目录映射改变。复用不是新增模拟，不能把 256 份复用证据再加进累计新求解数。真实 N64→N128→隔离 N256 链已逐文件核验，隔离 N256 没有执行新 native 求解。

## 材料来源审计

已按官方目录的 PDF 第 5 页（印刷页 04）核对 J800、P15/50 与 P17/50；B30P105 为高磁极化强度 P 系列，B23R075/B27R090 为磁畴细化 R 系列。J800 换算采用 B800=J800+μ₀×800。[宝钢 EVI 目录](https://ecommerce.ibaosteel.com/portal/download/manual/ElectricalSteel.pdf)

保存在 `catalog_audit/` 的 9 个观测点仅供范围检查，表中没有同批次成分/EBSD，也没有明确方向条件。旧 44 页目录使用 B8，另含 B27R095 的 R 系列分类；与新的 J800 目录分别登记版本/页码。旧 metadata 的 B30P105、B27R095“普通取向”标签与目录不符。原始 metadata、参考曲线和材料登记表保留原样；全部资料还不能作为严格校准的已认证材料记录。

## 公开布局复现

用已安装依赖的 Python，从仓库根目录运行。所有新诊断写到新的 tmp 子目录，保留发布的证据。

```powershell
python -m unittest discover -s tests -p "test_*.py" -v
python tools/analyze_component_variation.py --run-dir calibration/haar_prefix_20261004/n64_seed20261004 --run-dir calibration/haar_prefix_20261004/n64_seed20261005 --output-dir tmp/component_check_v3
python tools/screen_expanded_sampling.py --run-dir calibration/expanded_sampling_20261004/n128_seed20261004 --run-dir calibration/expanded_sampling_20261004/n128_seed20261005 --run-dir calibration/expanded_sampling_20261004/n128_seed20261006 --protocol calibration/expanded_sampling_20261004/analysis/study_protocol.json --output-dir tmp/n128_screen_check_v3
```

若要在新 N256 目录复用第一个 N128 的 TD，先 `run_calibration_pilot.py --prepare --n-grains 256 --seed 20261004 --run-dir <新目录>`，再 `reuse_native_prefix.py --source-run calibration/expanded_sampling_20261004/n128_seed20261004 --target-run <新目录> --grade B30P105 --direction TD`。当前 N128 RD 未完成，不能把默认双方向的源当作已完成来源。

## 已完成的当前阶段结果

新增 256 次实际 TD GPU 求解，全部 exit=0；连同 128 份复用 TD 前缀，三个 TD/N128 ensemble 均完整。新三目录总共有 512 份成功证据（256 复用+256 新执行），其中 RD 的 128 份全是复用前缀。累计当前研究实际求解 656 次；不是 3,072 份规划脚本已经完成。44 项 unittest 通过。

| 材料 seed | N128 TD H800（约束后，T） | N64−N128 H800（T） | 全曲线前缀最大差（T） | H800 约束均值改动（T） |
|---|---:|---:|---:|---:|
| 20261007 | 0.04672568 | 0.04261553 | 0.08879342 | 0.01321690 |
| 20261008 | 0.00826167 | 0.00239634 | 0.03953213 | 0.00000000 |
| 20261009 | 0.06789073 | 0.01504569 | 0.15323285 | 0.00000055 |

三 seed 的 H800 极差为 0.05962906 T，全曲线 seed 极差最大 0.15452660 T。整体状态 `stage_sampling_screen_failed`，未更新 bank/正式代理。第二个 seed 单独通过不能代替完整阶段验收。

`analysis/guard_audit/` 保留 384 份逐粒摘要、9,984 行原始分支/约束前后曲线，未剔除晶粒。第一个 seed 的 grain 69/115 在 H800 的中点为 −1.238959/−0.434394 T，被非负 J 先验改为真空场项；grain 115 的全曲线最大改动 1.382096 T。该异常是待核查证据，尚未证明是求解器、分支路径还是代理曲线定义问题。后验 guard 审计不改预先阈值，也不按异常结果筛掉晶粒。

下一步先对上述晶粒及小改动对照，在新诊断目录检查分支推进、初始状态、收敛设置与最大场，再决定是否按已冻结的 N256 预算推进。guard 公式和旧 table 保持原样；未解决该问题前不能用“约束后单调”证明原始响应正确。

只读 guard 审计入口：`python tools/audit_loop_guards.py --run-dir calibration/expanded_sampling_20261004/n128_seed20261004 --run-dir calibration/expanded_sampling_20261004/n128_seed20261005 --run-dir calibration/expanded_sampling_20261004/n128_seed20261006 --output-dir tmp/guard_check_v3`。哈希绑定的采样器/分析工具已设置 Git `-text`，防止 Windows 默认换行转换破坏证据复用合同。
