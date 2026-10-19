# 求解协议、运行版本与预算控制

材料库和校准批量页新增可选“1周期·B-H平滑对照（诊断）”，保持原材料节点、几何、激励及1周期31点，仅请求SmoothBHCurve=true，使用独立协议和模型版本。默认两个历史协议不改。平滑会影响内部曲线处理，工作点可能偏离原节点，见[Ansys 2025R1说明](https://ansyshelp.ansys.com/public/Views/Secured/Electronics/v251/en/Subsystems/Maxwell/Content/Maxwell/SpecifyingBHCurvesforNonlinearRelativePermeability.htm)。

本机唯一B23R075对照通过原生保存设置和预检，复用已导入材料。1200秒仍未完成，核对本次专用PID、创建时间、唯一工程及本案路径后请求停止，原生返回stopped；停止客户端没有新建会话，原worker随后释放专用会话。没有完整转矩结果，批量仅经CPU收尾为completed_with_failures，未重试，未证明负点或材料精度改善。所有原始、失败和缓存保留。累计完成仍Maxwell44/MuMax680，中断尝试不计为第45项。

新B-H执行任务带独立CPU预算观察进程。求解前先等就绪回执；到1200秒核对专用身份、队列、工程和路径后请求停止，未知身份或其他工程不操作，返回未确认不说已停止。原worker负责保存和释放，已请求停止的数据不作为完整结果，不自动重试。API参照[PyAEDT停止接口](https://aedt.docs.pyansys.com/version/stable/API/_autosummary/ansys.aedt.core.maxwell.Maxwell2d.stop_simulations.html)。

失败页显示预算原因并保留原始RPC错误；本地执行包附停止上下文。旧两份真实转矩结果和4076旧文件保持。完整原生、profile、日志和截图留私有。

主413项软件测试通过，含12项预算安全、24项执行、6项版本保护及真实CPU子进程CLI演练。实际原生停止API有上述单案证据；新内置观察程序没有追加原生端到端试验，CPU检查不算新的物理验证。

主页核对启动快照与磁盘源码。版本不同或源码不可读时，阻止新求解、训练及恢复请求并返回409；旧结果、CPU准备和阶段暂停仍可用。重新运行启动器即可打开匹配版本，已有进程和活动任务不会被自动关闭；旧服务不会因磁盘修改自动加载新保护。该版本问题不代表全局GPU暂停。

系统验收完成前论文写作暂停。
