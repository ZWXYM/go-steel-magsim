# 多频损耗留出复核

在系统首页进入“多频损耗复核”。每次选同一试样、同一方向和同一统计版本的 UTF-8 CSV。填原表来源、试样标识及预先选定的留出频率。每种峰值须有至少五档频率，且留出频率夹在训练频率之间。已知的试片配置可逐行记录；有配置变化时，误差适用于原表的组合条件。

| 项目 | 表头 |
| --- | --- |
| 频率 | `frequency_Hz` |
| 峰值，二选一 | `peak_B_T` 或 `peak_J_T` |
| 损耗，二选一 | `specific_loss_W_per_kg` 或 `volume_loss_W_per_m3` |
| 标准不确定度，可选 | `standard_u_W_per_kg` 或 `standard_u_W_per_m3`，对应原损耗单位 |
| 逐点试片配置，可选 | `configuration` |
| 原表身份，可选 | `sample_label`、`direction`（`RD`、`TD`、`other` 或 `unspecified`）、`table_variant` |

B 与 J、质量与体积单位分别处理；表内不作换算。可直接读取附带的 HEFMAG 第 20 页 CSV，识别 `peak_polarization_J_T`、`specific_power_loss_xref_W_per_kg`、`reference_u_W_per_kg` 和 `nominal_strip_configuration`。同一峰值和频率不能重复，混合试样、方向或统计版本须拆表。留出频率上的损耗值和不确定度只用于对照。

对每个峰值分别拟合非负频率基函数 `f`、`f^1.5`、`f^2`，数值计算以该峰值的最高训练频率缩放。训练点都有标准不确定度时，最小二乘残差除以各点的不确定度；缺少时用等权。另列最低频损耗的正比换算，以及留出点两侧最近训练点的每周期损耗线性插值。误差是预测减原值，相对误差除以留出原值。原标准不确定度随表提供，不充当拟合预测的不确定度。

结果可在数据中心重开或下载。工作包包括逐字节保留的原表、每点来源与训练/留出角色、系数与误差表。多频结果不写回材料库或电机损耗系数。

已用 Michal Ulvr 的 [HEFMAG 报告](https://doi.org/10.5281/zenodo.10032756) 第 20 页原统计、四个 J 峰值及五档频率核验，复算文件见 `calibration/frequency_loss_workbench_20261112/`。配置随频率变化，留出结果是该试样的表内对照；报告第 22 页替代统计未并入，本例也不绑定项目的四个牌号。
