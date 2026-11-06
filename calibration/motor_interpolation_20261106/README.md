# B27R090插值表示控制：未通过，未采用

原26节点RD标量等效、求解器BH平滑及1–100非线性迭代的保存结果，与新的201节点J-PCHIP表示作有限对照。新副本保留26源H/B节点，关闭求解器二次平滑；原张量、几何、工况和31采样点保留，重新生成网格。这是数值表示设置组合，不是单因素因果证明。

大幅正尖峰消失，但新结果最低转矩仍为负，未通过原数值判据。没有删除异常点、修改bank或推广新默认模式，也没有继续扩算其他材料。201点含插值点，不是新增测量或独立样品；与原节点间线性插值最大差约0.09982T，不能称材料精度改善。

两个Torque CSV按实际原单位逐字节保存。source_nodes.csv和execution_nodes.csv分别保存原节点与对照节点；summary.json给出独立CSV算术复核。profile_raw_values.csv只保留原生Final Residual原字段和矩阵调用数，单位/定义未与设置NonlinearSolverResidual等同，不能据数值比较认证收敛或把调用数当迭代次数。原始profile、完整工程/缓存/日志保留私有。

本项490.45秒、2核、1新电机完成，另1只读会话0求解；两专用会话释放，747原生来源文件SHA不变。累计Maxwell58=23电机+35磁静态，MuMax682。对照未进入生产系统、零损耗B-H不输出效率，不作最终材料排名。

插值和平滑背景见Ansys官方说明：https://ansyshelp.ansys.com/public/views/secured/electronics/v242/en/subsystems/maxwell/content/SpecifyingBHCurvesforNonlinearRelativePermeability.htm 。本文不把官方的一般说明当作本案异常原因已被证明。
