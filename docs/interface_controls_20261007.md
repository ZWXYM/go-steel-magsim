# Maxwell 坐标系、单位与能量交叉核查（2026-10-07）

本轮完成 11 项新的磁静态小模型、3 项求解失败；没有新电机或 MuMax3 求解。累计 Maxwell 完成 28＝4 电机＋24 磁静态，MuMax3 680。已释放全部 14 个求解专用会话及 3 个只读检查会话；用户 AEDT 应用未关闭。真实校准曲线、方向传输与最终优化排名仍未通过验收。

## 已定位的问题

`full_boundary_v1` 使用四边固定解析向量势，简单线性张量 μr=(1000,100,1000)，2 CPU 核、0 GPU，每项 120 秒、最多 6 自适应 pass。全局原点控制通过；三个平移控制均原生预检返回 1，但消息明确为 `TAU: Surface Mesh Generation Failed.`。两个条件非线性任务未执行。此前平移 object-CS 的失败不能归因于其材料方向。

`millimeter_v2` 保留相同物理几何、材料、边界与网格长度，仅把原生几何单位改为 mm；场采样文件显式声明 `Unit=meter`，输出为 SI/Global。四个线性控制均完成并通过原来的 B/H 与 adaptive 检查。新增能量核对后，object-CS 控制被否决；其原生能量 0.00010210 J 与导出 H 推算的约 0.000100531 J 不一致，其余三项通过。因此这里既有可复现的单位/网格敏感性，也有独立的方向/后处理问题，不能把所有原门限通过项称为方向认证。

相同源 B-H 点、H=20 A/m 对应 B=1.515476154 T 的两个 mm 非线性控制完成，但仍未通过原场/自适应门限：标量 RD 平均 H=(26.123367,-0.051196,0)，张量 RD 为 (26.329796,0.179213,0) A/m。最后 energy error/delta energy 分别为 3.737%/56.755% 和 0.070558%/3.1786%，既定门限均为 0.1%。不能以正常退出或预检 1 代替收敛。

## 斜向控制揭示的矛盾

`oblique_mm_v3` 取旧电机 `GOES_V3_02` 的原生 CS 参数，绝对端点候选方向约 47.3326°，在新的均匀磁通 coupon 分别施加 Bx、By，并加 relative-CS 与各向同性控制。四项全部完成、adaptive 通过；原 producer 的 B/H 判据有三项通过，但这是候选匹配，不是材料方向认证。

|控制|导出 H，A/m|最后原生能量，J|能量匹配候选|
|---|---|---|---|
|object-CS / Bx|(20,0,0)|0.00023873|原始轴值作为向量、X 优先|
|relative-CS / Bx|(20,0,0)|0.00058972|预定 47.3326° 框架|
|object-CS / By|(0,200,0)|0.00086711|原始轴值作为向量、X 优先|
|object-CS / 面内等向 Bx|(20,0,0)|0.00010053|各向同性|

例如 relative-CS 的解析预测 H=(117.320050,-89.701822,0) A/m，能量预测 0.00058971489 J 与原生总能量吻合，H 导出却未吻合。object-CS 两个能量均吻合 `direction_vector_x_primary_legacy`，不是绝对端点候选，也不是忽略方向的全局张量。这里是实测接口行为的候选判别，尚不能推广为所有对象或版本的通用语义。

三个完成结果的独立副本仅做字段与 profile 读取，**新增求解 0**：H、H_Vector、Energy、coEnergy、0.5 B·H 均保留。H 重导出与原文件逐字节一致；更换 H_Vector 没有解决矛盾。字段 Energy 与导出 H 的关系一致，却与原生自适应总能量不一致。现阶段不能选择某一套输出就宣称正确，更不能用解析重建 H 冒充原生验证。

只读 scalar-RD profile 显示每 pass 20 次矩阵调用，最后为 `Adaptive Passes did not converge`，而总体退出状态为 `Normal Completion`。本版本保存的 `UseNonLinearIterNum=false`；20 次调用不是已经独立确认的最大非线性迭代数。下一步应读取本机原生迭代属性及残差记录，按明确假设冻结新的对照预算。

## 程序与证据

`modules/maxwell_interface_controls.py` 冻结解析张量、全局四边向量势、单位与候选响应。`tools/run_maxwell_interface_controls.py` 仅接受新目录，冻结 producer/输入，串行运行，保存包括失败路径的原生消息，禁止重复提交已执行任务。

`tools/audit_maxwell_interface_controls.py` 在 CPU 上重现原 producer 的结果，再独立添加线性恒定磁通模型的能量核对：W=0.5 B·H × 面积 × 单位深度。原生 convergence 表的有限有效位数使用固定 1e-4 相对门限；不适用于非线性材料，其能量必须积分本构曲线。原 producer、summary 和门限保持原样，审核报告另存。11 完成项中原门限通过 8 项；追加线性能量核对通过 5 项，其中无斜向各向异性方向认证。连同前轮两个解析项，不代表真实材料通过。

`tools/inspect_maxwell_interface_results.py` 只打开完整结果副本，不调用 analyze，不修改源结果。公开仅包含字段、保存定义摘录、冻结代码、协议与审核报告；完整 AEDT、结果缓存、完整 profile 和日志留主工作区。

现有 763 旧来源、525 pilot 文件、26 文件冻结校准包和旧五扫描状态保持。冻结 bank 的材料留出平均 RMSE=0.04404264729 T、最差 B800 误差=0.10615668758 T，未变。本轮没有替换真实曲线、训练模型或正式电机材料库，也没有开放效率或最终排名。

## 下一步

1. 在新对照中显式提供目标方向的单位向量，检验 object-CS 原生总能量，同时保留旧轴参数作为独立对照。先确定实际方向，再审查 48 插片主轴；不得直接覆盖旧 CS。
2. 用第二种原生可观察量或磁场响应核对方向，定位 H/能量字段与求解总能量的矛盾。必要时用包含空气界面的响应，避免全边界固定磁通掩盖张量方向。
3. 查明本机非线性迭代属性后再预算新控制；保留全部原始 B-H 点和原门限。真实材料非线性通过之前，冻结正式电机与排名入口。

依据：[各向异性张量与对象 Orientation](https://ansyshelp.ansys.com/public/views/secured/electronics/v261/en/subsystems/Maxwell/Content/Materials/DefiningAnisotropicTensors.htm)、[磁静态能量定义](https://ansyshelp.ansys.com/public/views/secured/electronics/v251/en/subsystems/maxwell/content/Maxwell/MagneticFieldEnergyforaMagnetostaticFieldSolution.htm)、[标准字段量](https://ansyshelp.ansys.com/public/views/secured/electronics/v261/en/subsystems/Maxwell/Content/Maxwell/PlottingStandardFieldQuantities.htm)、[求解设置](https://ansyshelp.ansys.com/public/Views/Secured/Electronics/v251/en/Subsystems/Maxwell/Content/Maxwell/MagnetostaticSolverSettings.htm)。上述版本资料支持核查方法；具体接口矛盾来自本机冻结证据，尚未判定软件内部原因。
