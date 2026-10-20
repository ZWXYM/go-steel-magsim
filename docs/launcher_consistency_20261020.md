# 两个启动入口保持同一任务存储

此前在完整工作区双击根目录start_system.cmd会进入已保存结果的共享存储，而双击magsim子目录同名入口会默认进入magsim/data/system_workbench。后者可能显示空记录；原任务没有被删除，但重开行为不一致。

现在子目录入口检测到完整工作区的AGENTS、冻结校准manifest和根启动器后，转交根入口，使用同一资源根目录与原任务存储。根入口继续传递用户参数，--root、--storage、--port等显式选择优先；公开独立副本保持自己的.venv/系统Python启动逻辑和data/system_workbench默认值，不包含本机私有存储路径。

开始前可以只检查配置。在PowerShell中执行：

```powershell
.\start_system.cmd --check-config
```

输出JSON包含project_path、root_path、storage_path、CPU模式、自动队列恢复开关、请求端口和首页选择。该选项在选择监听端口、导入Flask应用和打开浏览器之前返回，不创建任务或启动求解；它核对路径选项，不认证许可checkout、材料数据或模型质量。直接Python入口也支持--check-config；直接入口未指定--storage时仍使用程序目录的data/system_workbench，请按操作说明显式指定原存储。

三个专项检查实际执行了CLI、Windows批处理和含空格路径下的独立公开式目录：验证主/子入口输出相同、参数可覆盖、公开式目录独立运行、只检查不创建任务。已有应用、旧服务、源目录和4983份工作台文件均保留；本轮不新增MuMax、Maxwell、训练或材料预测。
