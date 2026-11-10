# 接入时的原生字段补充

实际保存B27R095材料：sigma=2e6 S/m、rho=7650 kg/m³，与已有估计的假定值一致；名义片厚0.27mm与保存Equiv.CutDepth=0.27mm分别记录，数值相同不表示两者物理含义相同。原零损耗分支不修改。

[Ansys官方切边说明](https://ansyshelp.ansys.com/public/Views/Secured/Electronics/v251/en/Subsystems/Maxwell/Content/Maxwell/CutEdgeEffectsonCoreLossforLaminationSteels.htm)要求Edge Cut Based网格、Lamination组合和正的Equiv.CutDepth三项同时存在才计入切边损耗；仅看到该字段非零，不能断言切边模型已经生效。新工程损耗分支先核对源模板实际设置，保持片厚、体积系数与切边参数各自身份，另核对EddyEffect和汇总范围，避免重复计入。当前未新增原生会话或损耗求解。

100Hz表只是在B=1.5/1.7T的比损耗外推，不是当前电机总铁损；不能用转矩峰值代替磁通密度，也不能从该表直接生成效率。
