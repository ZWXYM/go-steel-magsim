# 厂家典型损耗工程模式的接入准备

已存在四牌号14个厂家典型损耗点和旧程序非负Bertotti估计。未改数据或系数；原RMSE及质量×密度=体积量纲复算通过。B23仅50Hz，其他50/60Hz；100Hz表是外推估计，不是新实测、当前电机总铁损或效率。

Ansys 2024R1起Electrical Steel磁滞项支持Y指数，新分支应明确Y=2以匹配旧公式。[官方定义](https://ansyshelp.ansys.com/public/Views/Secured/Electronics/v242/en/Subsystems/Maxwell/Content/CoreLossModelforaMaxwellMaterial.htm)。系数使用体积SI约定，[官方BP系数说明](https://ansyshelp.ansys.com/public/Views/Secured/Electronics/v252/en/Subsystems/Maxwell/Content/Maxwell/CalculatingPropertiesforCoreLossBPCurve.htm)。Equiv.CutDepth是制造影响参数，不能当名义片厚替换，[PyAEDT接口](https://aedt.docs.pyansys.com/version/stable/API/_autosummary/ansys.aedt.core.modules.material.Material.set_electrical_steel_coreloss.html)。

下一阶段新建独立catalog_loss_estimate材料/模型分支，保留原零损耗合同，不静默替换。先做0求解独立保存预检，核对非零Kh/Kc/Ke、Y=2、原26物理B节点和原损耗选择；原生传输与完整案例验证后，再接工程损耗比较。保留厂家典型、旧估计及频率外推身份，不要求新增实测才开发软件。当前没有创建损耗材料、原生分配、新求解或效率出口。
