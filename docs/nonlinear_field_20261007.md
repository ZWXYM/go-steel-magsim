# 非线性插值与插片实际几何核查（2026-10-07）

固定预算已执行结束：5 个磁静态求解完成但完整质量门限全部未通过；1 个 object-CS 解析对照原生预检通过，求解器返回失败，无 B/H 结果。读取了独立电机副本全部 48 插片的原生顶点及实际 Orientation 绑定。未求解电机、未运行 MuMax、未修改校准 bank 或旧结果。

## 冻结设计与实测输出

私有算例 `calibration/diagnostics/nonlinear_field_20261007/ready_v2`，公开薄证据 `calibration/nonlinear_field_20261007`。B23R075 的来源始终是 `cal_65e506f6e207`；四样品参考曲线/成分/织构证据状态未升级。源曲线 RD：H=20 A/m 对应 B=1.51547615418 T，H=10 A/m 对应 B=0.650142276271 T。标量对照只使用同一 RD 点集，不代替 RD/TD 材料。

20 mm 正方形，2 CPU 核/0 GPU，6 次最大求解尝试、7 个独立会话（其中 1 个只读几何），场对照每项 120 秒、只读几何 100 秒；最多 8 adaptive pass、至少 2 pass，能量误差及变化均要求 ≤0.1%，非线性残差 1e-6，矩阵残差 1e-8。9 个内部全局 B/H 点；场均匀性、方向和源曲线节点门限沿用前轮固定协议，没有调宽。

| 对照 | 平均 H / A·m⁻¹ | 最终能量误差 / 变化 % | 完整门限 |
|---|---:|---:|---|
| scalar_RD_discrete | 26.385083 | 14.185 / 43.726 | 未通过 |
| scalar_RD_smooth | 27.210588 | 22.328 / 35.719 | 未通过 |
| tensor_RD_smooth | 27.022626 | 1.0862 / 30.88 | 未通过 |
| tensor_RD_smooth_fine | 27.112878 | 1.1986 / 59.458 | 未通过 |
| tensor_RD_lower_flux | 10.057082 | 2.395 / 47.478 | 未通过 |
| translated_object_CS_linear | 无结果 | 无结果 | 求解失败 |

未平滑标量仍偏离 20 A/m，说明偏差并不只出现在张量或旋转接口。平滑后的标量、张量及 1 mm 网格均未消除高磁密偏差。低磁密平均 H 接近 10 A/m，但自适应能量没有稳定，不能只凭平均 H 宣称通过。所有 5 项均到达最大 8 pass，官方消息包含未达到指定收敛条件的警告。当前对照没有证明唯一根因；不能归因于错误的参考数据，也不能以增加网格或开启平滑就宣布修复。

## 插片几何与坐标系的证据边界

从独立 AEDT 电机副本在 Global 工作坐标系下读取 48 插片全部顶点，核对原生 Orientation 与保存 CS 名称。24 个实体为旋转复制体（DuplicateBodyAroundAxis），读取程序已支持其历史结构；没有把母体多边形当成复制体当前全局顶点。顶点 SVD 主轴只表示几何主轴，不等于实测轧向。

按轴参数为绝对端点的假设，RD 与主轴夹角 0.00117°–3.84580°，其中 24/48 小于 1°。另一组 x 轴优先 Gram–Schmidt 的方向向量假设为 2.38447°–82.30408°；它只是诊断候选，尚未通过原生响应验证，亦没有处理 Y 驱动参数的另一种候选。因此不能据此判定原坐标系必错或认证全部方向正确。

平移 object-CS 解析对照保留同一插片坐标参数，预检 validation=1，但求解器报错，没有完成标记、场或收敛文件；累计计数不把它算作完成。原生会话已释放，失败副本保持。旧 runner 未在异常路径采集完整原生消息，根因仍未确定；现行 runner 已改为在释放会话前始终尝试保存消息，这个改动通过 CPU fixture 测试，尚未执行新的原生失败对照。原始冻结 runner 保留，其 SHA 与现行 runner 分开，未重写旧 producer。

官方说明支持用 Orientation 指定张量的坐标系，但这不能替代本机原生验证：[材料属性类型](https://ansyshelp.ansys.com/public/Views/Secured/Electronics/v261/en/Subsystems/Maxwell/Content/Materials/AssigningMaterialPropertyTypes.htm)、[CreateObjectCS](https://ansyshelp.ansys.com/public/Views/Secured/Electronics/v251/en/Subsystems/Maxwell/Subsystems/Maxwell%20Scripting/Content/CreateObjectCS.htm)、[磁静态求解设置](https://ansyshelp.ansys.com/public/Views/Secured/Electronics/v251/en/Subsystems/Maxwell/Content/Maxwell/MagnetostaticSolverSettings.htm)。

## 复算、保存与后续

CPU 审核独立重读 B/H、官方逐 pass 收敛和实际保存的材料节点，逐项复现原生结果；未完成的第六项单独列为 failure_status，完整研究标志仍 false。763 个旧来源 SHA 保持，7 个专用会话均释放；四样品 bank、留出 RMSE=0.04404264729 T、525 pilot、26 冻结校准文件保持。首次 CPU 准备失败 `paired_v1` 和首版 CPU 审核也保留。公开只含输入、冻结 producer、字段/收敛、保存定义片段和顶点 JSON；完整电机/原生结果/缓存/日志留私有。

新增 Maxwell 完成 5（电机 0、MuMax 0）；累计 Maxwell 完成 17=4 电机+13 磁静态，和质量通过数量分开。只有前轮 2 项解析模型通过其门限；本轮 0 项。5001/5002 在本轮检查时已不存在，未由本轮结束；五旧队列原状态保持、不重提。没有宣称现行网页加载新代码。

下一轮先冻结独立的小预算：补足异常路径原生消息，核对 DrivenByXAxis、绝对方向参数与实际矩阵，并用真正简单常磁导率/全局 CS 控制分离材料定义、平移和 object-CS；先准备并核对再执行，不能重提本轮失败目录。非线性方面先核验完整曲线的微分斜率及初始化/非线性迭代记录，再设计有明确假设的配对对照。保持源节点及门限，不把单个均值或预检作为物理通过。方向与非线性验收、独立损耗及电机数值验证完成前，零损耗校准材料禁止效率求解，最终排名仍不可用。
