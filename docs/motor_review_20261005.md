# 电机仿真复核（2026-10-05）

本轮复核 17 份保存工程：V8_2 基础模板、五个旧扫描的十份副本、六个历史材料工程；重算 9 组可取得的同版本官方 CSV 指标。150 个原项目/记录/CSV 文件前后 SHA 一致。另用两个全新副本完成 AEDT 原生模型预检，没有启动 `analyze_setup`，新增电机求解为 0、累计仍为 4。用户正在使用的原目录 AEDT 进程保持运行。

新增 11 项电机合同检查，全项目 127 项测试、6 个修改/新增 Python AST 及网页 JavaScript 语法通过。九组 CSV 重算与审计保存值一致，17 份工程核验可重复生成；公开环境跳过依赖完整私有电机归档的一项检查。

## 影响当前结论的发现

1. **插片铁损遗漏**：17 份旧工程均只把 `Stator`、`Rotor_1`（ID 6、735）列为 CoreLoss 对象；48 个 GOES 插片的覆盖为 **0/48**。旧 CoreLoss 数值是这两个启用对象的结果，不能解释为包含全部插片的整机铁损。材料替换可间接改变它们的场和损耗，但没有计算插片自身损耗。ANSYS 的 [SetCoreLoss 说明](https://ansyshelp.ansys.com/public/Views/Secured/Electronics/v251/en/Subsystems/Maxwell/Subsystems/Maxwell%20Scripting/Content/SetCoreLoss.htm) 明确要求把需要计算损耗的对象列入清单。
2. **坐标系与本构分离**：48 个物体均绑定对应 `GOES_V3_*_CS`，也全部在运动对象集合中；所查材料却都是单条 `nonlinear` B-H 曲线，没有 RD/TD 的独立张量分量。当前工程支持标量材料替换诊断，尚未接入校准工作台的双方向 `.amat`。ANSYS 2025 R1 的 [各向异性定义](https://ansyshelp.ansys.com/public/Views/Secured/Electronics/v251/en/Subsystems/Maxwell/Content/Materials/DefiningAnisotropicTensors.htm) 说明方向作用于材料张量；[非线性曲线说明](https://ansyshelp.ansys.com/public/Views/Secured/Electronics/v251/en/Subsystems/Maxwell/Content/Maxwell/SpecifyingBHCurvesforNonlinearRelativePermeability.htm) 区分单条曲线与各向异性分量。
3. **重复性仍失败**：M6 / 23ZH90 第 2、3 周期最大转矩差为 0.245182 / 0.239221 N·m，超过冻结 0.1 N·m 门限。相位诊断保留全部点；输入重复一致，不能靠删点、平移或改门限认证。
4. **历史证据缺口**：23ZDKH75 的 `final_resolved_official_report.json` 存在，所声明的 final CSV 目录缺失；resolved/clean_resolved2 的其他 CSV 仍保存，但没有足够来源绑定证明可以替代 final 版本。其余五组 final CSV 与保存摘要在原舍入公差内一致；四组新扫描的指标与原记录一致。原始来源未覆盖。

固定工况为 3000 rpm、4 极、100 Hz 电频、90 mm 有效轴长、每电周期 30 个时间步（333.333 µs），负方向旋转采用 `-Moving1.Torque`。模型固定步长、未开启自动稳态判定、非线性残差设为 1e-4、B-H 平滑关闭。没有时间步/网格收敛证据；预检通过不是求解稳定性的证明。材料库损耗附件主要为 50/60 Hz 数据，不能当作 100 Hz 及谐波/旋转损耗已独立校准的证据。插片材料没有显式 stacking_type；其二维叠片方向及等效参数还需单独核验，不擅自填写。

## 已实施的修正及边界

- 新任务采用 `V8_2_object_CS_loss_scope_v2` / `core_loss_scope_v2_all_magnetic_inserts`，在独立 AEDT 副本中保留原定/转子损耗对象，同时启用全部 48 个插片铁损。损耗作用于场的开关保持 false，避免把后处理覆盖修复混为耦合物理升级。
- 求解前保存实际材料属性、物体/运动/CS 绑定、损耗对象和工况的核验；实际材料未加载、缺少有效损耗定义或工况不符时停止，不编造系数。
- 两个独立预检副本均通过 AEDT `validate_simple=1`，损耗对象保存为原 2 个 + 48 个插片，覆盖 **48/48**。仅执行模型设置和验证；没有新转矩/损耗预测，不能据此公布新效率或提升可信度。
- 保留旧数值门限和旧 `accepted` 身份，另加最终比较资格。单周期通过、缺少插片铁损、RD/TD/损耗/网格证据的结果均不可用于最终优化排名。网页、CSV、Markdown 与 ZIP 明确展示边界，当前最佳转矩/最低铁损排名为空。
- 严格核验转矩符号及重导出功率/效率；SolidLoss 明确列出，并声明旧效率公式不含此项、机械和杂散损耗。九组现有 SolidLoss 均为 0。完整周期的梯形均值仅另作端点权重诊断，不替换历史算术均值。

## 证据与复现

主工作区：`calibration/diagnostics/motor_review_20261005`，含 17 份物理核验、9 组 CSV、原指标差、比较边界及 150 个来源哈希；公开副本映射到 `calibration/motor_review_20261005`。原生预检：`calibration/diagnostics/motor_review_preflight_20261005`，固定两材料、串行、总超时 240 秒、不重试；完整 .aedt 与日志保留私有，公开仅提供预检摘要、来源和物理核验。

```powershell
# CPU 审计，output 必须为全新目录，不能写入旧算例。
.runtime/magsim/Scripts/python.exe -X utf8 magsim/tools/review_motor_models.py --root D:/Project/go-steel-thesis --output D:/Project/go-steel-thesis/work/new_motor_review
```

新 worker 的 `--preflight-only --job-dir <全新任务目录>` 只验证模型，目录转为 `preflight_completed_not_solved` 后不得再下发。后续求解另建新任务，旧失败任务/旧 prepared 均不自动重提。

## 下一步

先核验校准 `.amat` 的 RD→局部 x、TD→局部 y 的实际导入，以及旋转 90°/各向同性退化的小模型对照；当前正式校准包的损耗系数是零占位，不能直接替换材料用于铁损/效率比较。再以新的完整损耗范围为基线，分别固定预算比较时间步 30/60/120 点、网格及非线性精度；所有条件串行、另存、只改变指定因素。只有工况/方向/损耗和数值收敛均成立，才扩大多牌号优化。

服务重启请求被自动审批以 “blocked by policy” 拦截，5001 原进程保持。新代码在独立 **5002 CPU 预览服务**核验，网页完整汇总 ZIP 下载与服务器字节一致。该预览的 CPU 开关仅限制预览提交，不改变用户已恢复的全局 GPU 授权。5001 在人工重启加载代码前仍为旧进程，不能把新版接口验证记成 5001 已生效。
