# 一致的本机启动入口

Windows双击 `start_system.cmd`。在统一工作区，从根目录或magsim子目录启动都会使用同一批保存记录；在独立公开副本中，则使用该副本的默认任务目录。命令行参数可以明确覆盖资源、存储、端口与页面。

本机已验证的默认AEDT连接为COM，普通启动允许GPU和原生任务。用户显式设置 `MAGSIM_AEDT_TRANSPORT=auto|grpc|com` 时保留其选择。连接方式只作用于启动进程，不改许可配置，也不关闭用户应用。是否可以实际求解仍由现有原生入口检查。

统一私有工作区还保留两个旧入口，现已对齐：

- 根目录 `start_workbench.cmd` 转交公共启动器并转发参数，默认打开工作台。
- `tools/start_workbench.ps1` 默认使用工作区资源及同一份 `work/system_entry_20261008_005642/storage`，不会因为换入口而进入另一份空存储。支持 `-Root`、`-Storage`、`-Port`、`-Page system|workbench`、`-OpenBrowser`、`-CheckConfig`、`-CpuOnly`、`-ResumeQueue`、`-NoFallback`；COM默认设置在脚本结束后恢复原环境。

上述两个旧入口属于统一私有工作区，不是公开独立副本的必备文件。公开副本直接使用其根目录 `start_system.cmd`。

无需打开网页或下发求解即可核对设置：

```powershell
.\start_system.cmd --check-config
```

输出包括资源目录、保存记录目录、连接方式及资源模式。该选项不启动Flask、浏览器或求解器，不创建任务文件。普通启动不自动恢复旧队列；需要显式请求才恢复。
