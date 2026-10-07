# B-H 表示与 48 插片方向准备（2026-10-07）

本轮完成两个 B-H 表示对照的程序、48 插片显式轴候选计划及 CPU 核验。原生启动在进入求解前失败，因此没有新的磁场或电机结果：Maxwell 累计保持 39（电机 4、磁静态 35），MuMax 保持 680。主工作区 197 项测试通过。

## 解析表示对照

`modules/maxwell_bh_representation.py` 从 B23R075 的 RD/TD B20/20 割线分别构造固定工作段磁导率，用 Normal B-H 的标量和张量表示。两种表示都保留源 H 的 26 个节点，只在诊断副本改 B：H≤50 A/m 为恒定斜率，H>50 为 μ0 尾段。50 A/m 膝点是人工诊断设定，不是实测饱和信息。源文件、现行 bank、真实参考曲线及留出误差没有改动。

冻结预算为最多 2 项、每项 120 秒、2 CPU / 0 GPU、6 pass，不重试。20 mm 方形 coupon、1 m 默认深度、全边解析磁矢势、Global SI 九点 B/H，与已完成的 high_mu_linear_v7 割线基线只读比较。保留 B 1e-4 T、H 1%、均匀性 0.001、自适应 0.1% 门限；追加解析工作段的本构积分能量相对误差 1e-4。任何采样离开恒定磁导率段，不允许使用该解析能量作为通过证据。

[官方曲线说明](https://ansyshelp.ansys.com/public/views/secured/electronics/v261/en/subsystems/Maxwell/Content/Maxwell/SpecifyingBHCurvesforNonlinearRelativePermeability.htm)规定 Normal 曲线斜率不小于 μ0，末段应接近 μ0，非平滑曲线按段线性处理。本程序将这些性质与 H=A/m、B=T 的保存传输核验分开。保存校验的末位数值容差不改变场、能量或自适应门限。

实际仅下发第一项：新 AEDT 会话成功启动，日志最后停留在 project 创建，Maxwell2d 初始化超过 120 秒。没有材料导入、预检、`solve_started` 或 `solve_completed` 标记。第二项未下发。不能从该超时推断非线性本构失败或材料误差改善。

## 48 插片候选

`modules/motor_explicit_axes.py` 将前轮实际 Global 顶点与 48 个 object-CS 记录一一绑定，冻结原绝对端点框架对应的显式 X/Y 单位方向候选，记录其与几何主轴的角差。主轴来自真实顶点的 SVD，不能冒充轧制方向测量。候选最大角差沿用此前约 3.85° 的来源结果。

`tools/prepare_motor_explicit_axes.py` 复制已有损耗覆盖 48/48 的 23ZH90 独立模板，计划新建 48 个唯一 `ExplicitCandidate` CS，保留旧 CS，回读实际保存的有单位 X / 无量纲归一化 Y，核对逐插片绑定、真实顶点、运动对象、原定转子损耗对象、材料名称和工况。仅允许 1 专用原生准备会话、240 秒、0 求解。

实际会话能打开复制项目，但设计激活返回 None，PyAEDT 随后报 `GetDesignType` 的 NoneType 错误；原生末条错误为 `Script macro error: Access to the requested resource is not permitted.`。失败发生在实际顶点读取、CS 创建和预检之前，**本轮实际保存的候选为 0/48**。不能将 CPU 计划称为 48 插片物理认证。原生拒绝原因尚未知；不把 TCP 许可端口连通当成真实许可证或设计访问成功。

## 保留、复核与下一步

两个专用会话均已核对归属并结束；没有关闭用户应用。完整失败副本、原生日志和 producer 快照保留私有。CPU 审核在 `tools/audit_bh_and_axes_preparation.py`，薄证据位于 `calibration/bh_and_axes_preparation_20261007`，原始状态与独立审核分开，原失败目录不重提。

197 项测试包括 6 个解析曲线/能量/单位测试及 3 个真实几何绑定测试；这些测试通过只证明 CPU 程序契约。未执行的保存核验代码仍需原生验证。原五扫描状态、525 pilot、26 冻结校准、旧原生及公开证据保持。四样品留出均值 RMSE 仍为 0.0440426473 T，最坏 B800 误差仍约 0.106157 T，没有宣称泛化精度改善。

下一轮先只读核查设计访问/许可和原生错误证据，提出新的初始化最小检查预算，避免下发物理求解直到设计访问确实可用；不得通过重复提交失败目录或换工具绕过既有进程终止拒绝。CPU 侧继续来源、留出校准和导出契约核验。真实非线性、完整 H 方向接口、48 插片响应、独立损耗、稳态电机与最终排名仍未验收；零损耗校准材料仍禁止效率求解。


后续只读检查：现有环境已配置的许可服务器 TCP 连接失败，ANSYS 的两个许可相关本地服务处于 Stopped；没有改配置或启动服务。这不是原生拒绝原因的唯一确认。新增 `maxwell_native_readiness` 下发前/worker 入口检查：不可连通时在创建 AEDT 前停止，两个 CPU 测试核对未知/失败状态拦截及连通不等于 checkout/设计认证。原失败 producer 原封保留，当前改进程序与当次冻结程序版本分开。197 项全套测试用于当前版本。5002 当前不可访问，没有终止或重启任何旧/用户服务。
