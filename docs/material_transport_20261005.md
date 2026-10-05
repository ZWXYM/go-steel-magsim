# 四样品 RD/TD 材料传输（2026-10-05/06）

本轮把冻结校准包 `cal_65e506f6e207` 的 B23R075、B27R090、B27R095、B30P105 两方向曲线接入独立 AEDT 材料定义。曲线来自已知样品的现有校准拟合；这是接口验证，没有新增独立材料实验或改善留出误差的证据。

## 导入问题与处理

现有 AMAT 的带引号 `set` / `DimUnits` 与空 `no_modifier` 块不符合本机 PyAEDT 1.0.0 解析/材料构造的合同。新导出修正这些字段；旧文件以新副本作格式规范化，所有 H/B、标量 ND、密度、电导率、厚度及零损耗系数保留。开发时规范化脚本引入空行，曾生成无名 `NAME:` 字段；失败保留，修正并加入回归检查。

即使导入函数返回“已添加”，通用 PyAEDT 的 `NAME:Points` 参数也未把非线性磁导率保存到本机 AEDT 工程。新传输用 ANSYS 2025 R1 [AddMaterial 原生接口](https://ansyshelp.ansys.com/public/Views/Secured/Electronics/v251/en/Subsystems/Maxwell/Subsystems/Maxwell%20Scripting/Content/AddMaterialMaxwell.htm) 的逐个 `NAME:Point` 列表，成功条件是实际保存工程的两组件 H/B 与冻结输入逐点一致，而不是函数返回值。原生会把 `Normal` 存为 `normal`、把 0.00030meter 存为 0.0003meter；验证器按曲线类型和 SI 数值比较，仍拒绝 Intrinsic、错误单位、改值、错序、交换方向和零损耗以外的系数。

## 验证范围

最终协议 `four_material_directional_transport_preflight_v4` 为一个全新原生会话、最多 240 秒、不重试、不调用求解。四个材料各有 RD/TD 26 点，H 为 A/m、B 为 T；ND=1000 是保留的先验，不能改称测量值。B23R075 赋给独立模板的 48 个插片，局部 CS 与运动绑定保持。实际完成状态、原生 validation 和保存工程哈希见 `native_summary.json`，来源及前后检查见 `validation.json`。

四个早期失败试验及首个未执行准备目录保留在私有 `calibration/diagnostics/material_transport_20261005`，没有重提旧失败目录；最终证据为 `ready_v4`。失败涉及格式转换、通用导入未存曲线、Normal 大小写及厚度字符串规范化；不是材料求解结果。第一次清理调用的参数不匹配当前 Desktop API，随后按创建时间/PID 确认归属，仅释放了该试验的独立会话。未关闭用户应用或 5001/5002 服务。

公开仅提供 `ready_v4/public_evidence` 的四材料原输入/规范化副本、实际保存材料块、摘要、失败摘要及冻结 producer；完整电机工程、结果缓存和日志保留私有。

## 使用与下一步

主工作区命令（必须新目录）：

```powershell
.runtime/magsim/Scripts/python.exe -X utf8 magsim/tools/preflight_calibrated_materials.py --root D:/Project/go-steel-thesis --study-dir D:/Project/go-steel-thesis/work/new_transport --prepare
.runtime/magsim/Scripts/python.exe -X utf8 magsim/tools/preflight_calibrated_materials.py --root D:/Project/go-steel-thesis --study-dir D:/Project/go-steel-thesis/work/new_transport --execute
```

这是 B-H 专用导入预检。材料损耗为零占位，本试验保留旧模板损耗范围，只核验材料传输；不得提交为效率/铁损求解，也不能替代已补齐 48/48 损耗的新库材料预检。现有扫描 worker 对缺有效损耗定义的材料仍拒绝求解，最终优化资格仍为 false。

尚未验证真实磁场中 RD→局部 x、TD→局部 y 的响应，也未完成局部 CS 与插片几何主轴的方向对照。下一轮在新小模型中预算旋转 90°、RD/TD 交换、各向同性退化的磁场响应对照；之后再接通正式电机材料入口，单独校准损耗并检查时间步/网格收敛。曲线传输成功不能直接写成电机优化可信度提升。

5001 旧服务曾被自动审批拒绝重启（blocked by policy）；本轮没有再次尝试终止该服务，也没有把当前运行服务声明为已加载新版材料接口。
