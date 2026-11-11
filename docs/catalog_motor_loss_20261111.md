# 校准材料的目录铁损工程估计

材料库中选择四个已确认牌号的拟合条目，再选择 **RD等效 · 101点厂家目录铁损估计**。点击准备或接续，打开分析计划后可以开始单案并下载结果。批量页同样可以选择此协议，勾选拟合条目、准备计划，再开始串行执行。已完成且来源一致的记录直接复用。

目录协议使用现有 RD 等效 Intrinsic 曲线，保留 26 个物理 B–H 节点，沿用 3000 r/min、4 极、0.1 ms 步长和 0–10 ms 的 101 个官方点。每个新正式算例的预算为 2 核和 1800 秒。模型和执行身份与默认 B–H 转矩协议各自保存。

材料系数来自[四样品目录数据](material_loss_reference_20261021.md)的保存估计。按同牌号、片厚、假定密度和电导率核对，使用体积系数 Kh、Kc、Ke，固定磁滞指数 Y=2、Kdc=0。厂家点为 50 Hz 或 50/60 Hz 的典型值，当前电气频率 100 Hz，属于工程外推。AEDT 保存后的物理节点与系数均有读回。公式和系数量纲见 [Ansys Electrical Steel 模型](https://ansyshelp.ansys.com/public/Views/Secured/Electronics/v242/en/Subsystems/Maxwell/Content/CoreLossModelforaMaxwellMaterial.htm)及 [BP 系数单位](https://ansyshelp.ansys.com/public/Views/Secured/Electronics/v252/en/Subsystems/Maxwell/Content/Maxwell/CalculatingPropertiesforCoreLossBPCurve.htm)。

独立副本保留 48 个插片、定子及转子共 50 个铁损对象，关闭铁损场反馈。插片使用实体材料，等效切边深度字段随源材料保存，切边修正未启用。详情见 [Ansys 切边模型的启用条件](https://ansyshelp.ansys.com/public/Views/Secured/Electronics/v251/en/Subsystems/Maxwell/Content/Maxwell/CutEdgeEffectsonCoreLossforLaminationSteels.htm)。

分析页提供官方 `CoreLoss`、`StrandedLoss`、`SolidLoss` 的原始 CSV 及 0–10 ms 的时间加权均值。逐对象报告可用时，逐点核对其合计和总铁损，再展示 48 插片小计。批量包附 `catalog_loss_summary.csv`、每案的原始报告、材料定义及曲线。与指定基准比较时，铁损差值采用同目录快照、同模型和同校准来源，保留原转矩判据及预算状态。数据中心可保存整批与原 B–H 批量的组合包。

适用范围为单周期仿真和同工况工程比较。原始材料源、已有零损耗分支和作者优化结果可独立重开。转矩换算的是电磁机械功率，损耗报告和同工况差值独立提供。

## 首案与重启恢复

B27R095 的 `bhexec_735cbf500f0a` 已在预算内完成。101 个官方时间点覆盖 0–10 ms，平均转矩 1.864497 N·m，折算电磁机械功率 585.749 W。总铁损、绞线损耗、实体损耗的时间加权均值分别为 42.482235、49.175064、0 W。48 插片铁损为 0.887848 W，全部 50 个对象的逐点合计与官方总铁损最大差为 9.10×10⁻¹³ W。单周期原始序列和来源见 `calibration/catalog_motor_loss_initial_20261111/`。

批量已实测复用 B27R095 后仅下发 B30P105。操作系统计划重启时 B30P105 尚未完成，保存记录保留该中断。控制器已退出且网页显示“归档已中断任务”时，可以归档本批并释放占用。系统核对记录的控制器、工作进程、守护进程和专用 AEDT 会话均已退出，再保存原状态和归属文件。已完成项可继续下载，中断副本、原生锁文件和部分输出原样保留。需要补算时，在相应分析页准备独立执行任务，再用新批量复用已有结果。
