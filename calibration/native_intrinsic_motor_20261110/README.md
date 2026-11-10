# 原生Intrinsic表示的四材料工程控制

本目录保存四材料RD标量控制的全部31点官方转矩及原判据，包含未通过项。正常源B-H节点转换为Bi=B−μ0H后，以实际原生Intrinsic类型保存；物理B节点保存误差<=1e-9T，原26个H不改、不拟合转矩。单周期10ms/3000rpm、2核、平滑、最多100非线性迭代；原正均值/正最小/峰<10Nm/脉动<100%判据不变。B27R090是先行独立控制，其余三项为随后网页批量。不是四种新实测样品、独立实验或留出验证。

每案与summary.csv保留原值、是否通过和明确版本。负平均转矩时原公式的脉动百分比可为负，不能解释为性能改善。先行一案通过不代表其余材料都稳定，未采用为默认模式。源曲线与校准bank不变；单方向曲线各向同性使用，不模拟插片局部RD/TD，零损耗材料不计算效率，不做最终优化排名。

AMAT的纵坐标为Bi；actual_material_BH.csv同时保存物理B_T=Bi+μ0H与Bi_T，metadata区分定义。B27先行控制用source_and_native_nodes.csv显示源B/原生Bi/重建B。完整原生工程、缓存、profile和日志留私有，本目录仅薄证据。源参考是项目已有处理参考，非新增批次测量。源节点等价也不认证内部平滑/插值或场解等价。

文档依据：[2025 R1曲线定义](https://ansyshelp.ansys.com/public/Views/Secured/Electronics/v251/en/Subsystems/Maxwell/Content/Maxwell/SpecifyingBHCurvesforNonlinearRelativePermeability.htm)，[原生AddMaterial表示类型](https://ansyshelp.ansys.com/public/Views/Secured/Electronics/v251/en/Subsystems/Maxwell/Subsystems/Maxwell%20Scripting/Content/AddMaterialMaxwell.htm)。
