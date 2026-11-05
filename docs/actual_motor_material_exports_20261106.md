# 下载实际求解材料与筛选后的比较

校准方向等效电机结果页现在可下载 `actual_material.amat`、`actual_material_BH.csv` 和 `actual_material.metadata.json`。这三个文件也进入单次执行包、批量结果包及完整比较包。原始输入的 RD/TD 材料包、官方转矩 CSV、旧结果和失败记录均保留。

实际 AMAT 取自完成后保存的原生工程定义，不重新预测或重建曲线。CSV 使用该定义中实际保存的 H/B 节点，H 为 A/m、B 为 T。metadata 绑定执行、分析计划、原材料文件、有效校准库、训练/排除材料、保存工程、官方转矩 CSV、分析协议和模型版本。导出前核对这些已保存的材料与文件；损坏或身份不符时不提供替代材料。

RD/TD 等效模型把一个方向的原曲线作为各向同性标量 B-H 使用。导出单方向等效 AMAT 不表示已经验证插片的局部 RD/TD 响应。损耗系数仍是未校准的零占位，不用于效率计算。metadata 保留该次转矩判据的原值：异常算例同样可以下载材料，下载成功不等于转矩合格。

字段 `native_equivalent_cut_edge_depth_m` 表示 AEDT 的等效切边退化深度；它不是叠片厚度。仅当源 metadata 明确给出 `thickness_mm` 时，另以 `declared_sheet_thickness_mm` 保留该声明，缺失则留空。此前输入文件的数值不因此重写。[Ansys 对该字段的定义](https://ansyshelp.ansys.com/public/Views/Secured/Electronics/v252/en/Subsystems/Maxwell/Content/Maxwell/CoreLossModelforaMaxwellMaterial.htm)。

与指定基准的页面比较仅显示双方均通过原转矩判据、平均转矩为正、保存工况和校准来源一致的工程差值。若只有基准自身合格，页面明确告知尚不能形成材料性能比较。该筛选是工程使用条件，不等于稳态、损耗或材料因果效应认证。

完整比较 ZIP 另含 `engineering_comparison.md` 和 `engineering_comparison.csv`，只列筛选后的条目；`comparison.json` / `comparison.csv` 保留全部诊断原值、原差值和异常标记，并新增 `qualified_delta_*` 字段。数值异常的高转矩不能解释为性能提升。所有原始数据仍在 `saved_batch_report/` 中，条目下可找到实际材料三文件。保持用户选择顺序，不自动排名，不执行新的求解。

2026-11-05 的四材料平滑/迭代协议实际完成 4 项（其中 1 项复用），B27R095 通过原判据，其余 3 项异常。该协议名称中的 stable 不表示对四材料普遍有效。作者原有材料库扫描与优化程序及其结果继续作为工程分析入口使用。
